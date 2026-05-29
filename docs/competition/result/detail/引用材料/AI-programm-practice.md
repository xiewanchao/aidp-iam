## 实践过程说明书

### 1、项目说明

| 使用工具&模型 | Claude Code | AI Agent 全链路辅助开发 |

#### 1.1 背景

本项目为 AIDP 公共服务组件鉴权认证项目，实现了基于 Kubernetes 的完整 IAM（身份认证与授权）和 Gateway（网关）解决方案栈。项目通过 Envoy Gateway、Keycloak、OPA 等核心技术，构建了一个支持动态权限控制、策略同步、证书管理的云原生网关系统。

项目采用 AI 原生开发流程，通过上下文工程（Context Engineering）、白+黑协作验收等实践，实现了从需求到部署的全流程 AI 辅助开发，为企业级云原生应用提供统一的认证授权基础设施。

#### 1.2 业务背景

随着微服务架构和云原生技术的普及，企业面临以下挑战：

- 多个微服务需要统一的身份认证和授权管理，各系统重复建设鉴权逻辑，标准不统一
- 传统网关难以满足动态权限控制需求，权限策略分散在各个服务中，管理困难
- 资源实例级权限需业务自行实现，侵入业务代码，运维成本高
- 缺乏零信任安全理念的实际落地方案

本项目通过构建统一的 IAM 网关栈，解决上述问题，提供：

- 集中化的用户身份管理（基于 Keycloak，支持 SAML/OIDC 联邦登录）
- 两级细粒度动态权限控制：路径级（OPA）+ 资源实例级（resource_acl 前缀匹配）
- 统一的 API 网关入口（基于 Envoy Gateway），业务零侵入
- ACL 自动同步（ext_proc 拦截资源创建/删除响应，自动维护权限记录）
- Policy 即代码的权限管理方式，策略热更新无需重启服务

#### 1.3 技术选型

| 组件 | 选型 | 备选方案 | 选型理由 |
| ---- | ---- | -------- | -------- |
| API 网关 | Envoy Gateway | Kong、Nginx | 原生 K8s CRD、ext_authz/ext_proc 支持完善 |
| 策略引擎 | OPA | Casbin、自研 | 声明式 Rego、热更新、OPAL 生态 |
| 身份提供商 | Keycloak | Auth0、自研 | 开源、多租户 realm、离线可用 |
| 资源同步 | ext_proc 拦截 | 事件总线、轮询 | 零侵入业务应用，响应拦截天然保证时序 |
| 数据库 | PostgreSQL | MySQL | JSONB 支持、行级锁、与 Keycloak 共用 |

### 2、需求阶段

#### 2.1 原始需求输入

用户提出的初始需求是业务语言，不是技术语言：

> "我们需要一个多租户权限系统，支持用户登录、API 鉴权、资源级别的权限控制"

AI 在这个阶段的价值是**需求澄清和边界识别**，而不是直接给方案。AI 会追问：

- 租户隔离的粒度？（数据库级 / schema 级 / 行级）
- 鉴权发生在哪一层？（网关 / 应用内 / 两者都有）
- 资源权限是静态配置还是动态授权？
- 是否需要支持外部 IdP（LDAP、OAuth2 等）？

#### 2.2 需求结构化

AI 将模糊需求扩展为结构化功能点：

| 原始需求 | 扩展后的功能点 |
| -------- | -------------- |
| 用户登录 | Keycloak 多租户 realm、JWT 签发、外部 IdP 联邦 |
| API 鉴权 | 网关层 ext_authz、OPA 路径级策略、API Key 支持 |
| 资源权限 | resource_acl 表、前缀匹配、owner/contributor/viewer 三级角色 |
| 多租户 | tenant_id 隔离、realm 隔离、跨租户访问拦截 |

### 3、架构设计阶段

#### 3.1 AI 生成初版架构

基于需求，AI 提出分层架构草案：

```
前端/客户端
    ↓
Envoy Gateway（路由 + SecurityPolicy ext_authz + EnvoyExtensionPolicy ext_proc）
    ↓                              ↓
pep-proxy（JWT验证 + OPA路径鉴权 + resource_acl资源鉴权）
    ↓                              ↓
业务应用                    resource-sync（监听响应，写/删 resource_acl）
                                   ↓
                            pending_acl（失败重试队列）
```

#### 3.2 关键设计决策迭代

这个阶段是来回最多的地方，本项目经历了几个典型的设计调整：

**决策 1：组模型 vs 角色模型**

初版用角色（roles），后来改为组（groups）。原因：Keycloak 的 groups 天然支持层级、JWT 里 groups 声明更标准、`master-admins / tenant-admins / all-users` 三层结构更清晰。

**决策 2：path_rules 的来源**

初版是纯静态配置，后来演化为两个来源合并：
- `permission_group_paths` 表（系统路径，如 `/AccessManager/`）
- `app_manifests`（应用注册时动态生成，bundle-server 派生）

这个设计让业务应用接入时不需要手动配置 OPA 规则。

**决策 3：资源 ID 提取的灵活性**

初版只支持从 path 提取资源 ID，后来扩展为 `id_source`（path/query/body）+ `id_field`（支持嵌套如 `data.kb_id`），解决了非 RESTful API 的接入问题。

#### 3.3 数据库 schema 设计

AI 生成初版 schema，用户审查后调整：

```sql
-- 系统级（无 tenant_id）
apps, resource_patterns, resource_actions, path_rules

-- 租户级（有 tenant_id 隔离）
resource_acl, api_keys, pending_acl
```

这个分层是本项目的核心设计之一，AI 识别出哪些表需要租户隔离、哪些是全局配置。

### 4、代码生成阶段

#### 4.1 分模块生成策略

AI 按模块顺序生成，每个模块生成后立即验证：

1. **DB schema** → 验证建表 SQL 可执行
2. **pep-proxy 核心鉴权流程** → 验证 JWT 解析、OPA 调用、resource_acl 查询
3. **bundle-server** → 验证 Rego 策略正确性、OPA 数据推送
4. **resource-sync ext_proc** → 验证响应拦截、ACL 写入
5. **iam-api 管理 API** → 验证租户/用户/组 CRUD
6. **Helm charts + K8s 配置** → 验证部署可用

#### 4.2 典型代码生成示例

以 `query_acl()` 为例，AI 生成的关键代码（`apps/pep-proxy/app/db.py`）：

```python
# 构造所有祖先前缀候选，从最长到最短
parts = object_path.split("/")
candidates = ["/".join(parts[:i]) for i in range(len(parts), 0, -1)]

# 一次 SQL 查询，取最长匹配
SELECT role_path, object_path FROM resource_acl
WHERE tenant_id = $1
  AND user_path = ANY($2)
  AND object_path = ANY($3)
ORDER BY LENGTH(object_path) DESC
LIMIT 1
```

这个设计的价值：子资源（`/Details`、`/Password`）自动继承父资源权限，不需要为每个子路径单独写 ACL 记录。

#### 4.3 生成过程中的典型问题

AI 生成代码时会犯的错误，本项目实际出现过的：

- **init SQL 用 `WHERE NOT EXISTS` 而不是 `ON CONFLICT DO UPDATE`**：导致 configmap 更新后 DB 数据不跟着更新
- **path_prefix 用反斜杠**：Windows 开发环境下 AI 可能混淆路径分隔符
- **N+1 查询**：group 详情页 `member_total` 每个成员都查一次（已修复为 COUNT 聚合）

### 5、测试与验证阶段

#### 5.1 测试策略分层

本项目的测试体系（`deploy/scripts/test.sh`）：

```
集成测试（主体）
├── SR01-SR08：基础鉴权流程（JWT、API Key、路径级、资源级）
├── SR09：外部 IdP 场景
├── SR10-SR22：各业务应用接入（KnowledgeBase、MemoryStore）
├── SR23：aidp-web 客户端 mapper 验证
└── SR24：DataAgent 多级授权流程
```

AI 在测试阶段的作用：
- 生成测试用例骨架
- 识别边界条件（跨租户访问、token 过期、OPA 不可用时的 fail-close）
- 分析测试失败的根因

#### 5.2 验证循环

```
代码修改 → rebuild.sh <component> → test.sh（或单条 curl）→ 分析日志 → 再修改
```

AI 在这个循环里的价值是**快速定位根因**，不是盲目改代码。典型示例：

- 现象：`GET /AccessManager/Tenants/{id}/Users/{id}/Details` 返回 403
- 错误信息：`No path_rule matches groups ['all-users'...]`
- 排查路径：OPA Rego 逻辑 → path_rules 数据 → DB init SQL → `WHERE NOT EXISTS` 语义
- 根因：DB 里的旧数据没有被新的 init SQL 覆盖，`permission_group_paths` 存的是旧格式的路径前缀

#### 5.3 DT 测试用例生成

本项目使用 AI 自动生成结构化测试用例，规范定义在 `docs/architecture/case-test.md`，输出为 CSV 格式供测试人员直接使用。

**生成流程：**

```
业务功能描述 + 相关代码 → AI 理解业务逻辑 → 生成标准化测试用例 → 输出 CSV
```

**核心约束：**

1. 必须覆盖：正常流程、异常操作、依赖服务故障三大场景
2. 每个功能点标配：1 条正向用例 + 至少 1 条异常/故障场景，异常用例不超过 5 条
3. 优先级：用户高频操作 > 核心业务链路 > 高危边界场景
4. 黑盒视角：站在不看代码的用户角度写，不暴露 API 名称和参数
5. 步骤与结果一一对应：`用例_测试步骤` 和 `用例_预期结果` 数量必须相同
6. 故障用例命名规范：`xxx服务在XXX时故障，导致xxx功能失败`

**本项目的典型用例示例（用户管理模块）：**

| 用例名称 | 场景类型 | 覆盖点 |
| -------- | -------- | ------ |
| 普通用户查看自己信息成功 | 正常流 | JWT 鉴权 + resource_acl 前缀匹配 |
| 普通用户查看他人信息失败 | 异常 | 跨用户资源访问拦截 |
| Keycloak 在用户登录时故障，导致 Token 获取失败 | 依赖服务故障 | ext_authz fail-close 行为 |
| 批量创建用户超过 100 条失败 | 异常 | payload 大小限制 |
| 数据库在写入 ACL 时故障，导致新用户无法访问自己信息 | 依赖服务故障 | resource-sync pending_acl 重试机制 |

#### 5.4 性能基准测试

性能测试使用 `deploy/scripts/test.sh` 执行（1000 并发 / 100 租户）。

**关键基准数据（v2.0，Kind 单节点）：**

| 场景 | 吞吐量 | P99 延迟 |
| ---- | ------ | -------- |
| 业务路由（JWT + ext_authz + ext_proc） | 40 req/s | 150ms |
| 管理 API（JWT + ext_authz） | 38 req/s | 250ms |
| OPA 路径鉴权（单独） | 72 req/s | — |
| client_credentials 认证 | 113 req/s | — |
| password grant（Argon2id） | 17 req/s | — |
| ACL 查询（300,000 行，索引） | — | 0.14ms |

AI 分析出 Argon2id 是 password grant 的唯一瓶颈（50 并发下失败率 62%），而 OPA 和 ext_proc 不是瓶颈，给出调优建议：降低 Argon2 memory 参数、扩 Keycloak 副本、服务间调用改用 client_credentials。

### 6、迭代与演进阶段

#### 6.1 需求变更的处理

本项目经历的典型演进：

- v1.0：基于角色的静态路径鉴权
- v2.0：引入 resource_acl、动态 path_rules、ext_proc ACL 同步
- v2.1：groups 模型替换 roles、manifest 动态注册、DataAgent 多级授权

每次演进 AI 都需要：
1. 理解旧设计的约束
2. 评估新需求对现有接口的破坏性
3. 设计迁移路径（DB migration、向后兼容期）

#### 6.2 文档同步

CLAUDE.md 明确要求：代码和 `docs/` 文档必须同步更新。AI 在每次功能变更后会同步更新：
- `docs/architecture/` 下的架构文档
- `docs/architecture/data-storage.md`、`docs/architecture/manifest-template-*.json` 等设计文档

### 7、AI 辅助开发的创新点

#### 7.1 需求→架构的结构化转换

传统开发：需求文档 → 人工拆解 → 架构评审（周级）

AI 辅助：需求对话 → AI 实时扩展和结构化 → 快速收敛到技术方案（小时级）

本项目的体现：`permission_group_paths × permission_group_bindings` 这个多对多设计，是 AI 在理解"功能点可以绑定多个组"这个需求后自动推导出来的，不是人工预先设计的。

#### 7.2 根因分析而非症状修复

AI 不是看到报错就改代码，而是沿着调用链追溯：

```
403 → OPA deny → path_rules 数据 → DB init SQL → WHERE NOT EXISTS 语义
```

这种系统性排查在复杂分布式系统里价值很高，人工排查可能需要几小时，AI 辅助可以在几分钟内定位。

#### 7.3 约束感知的代码生成

AI 生成代码时会主动考虑项目约束：
- Gateway API 必须用 experimental channel
- Windows + Git Bash 环境下路径用正斜杠
- Helm chart 默认命名空间与实际部署命名空间保持一致

这些约束在 CLAUDE.md 里显式记录，AI 每次生成代码都会遵守，避免重复犯同类错误。

#### 7.4 测试驱动的验证闭环

不是生成完代码就结束，而是：生成 → 部署 → 测试 → 分析失败 → 修复 → 再测试。AI 全程参与这个循环，而不只是参与"生成"这一步。

#### 7.5 文档即上下文：AI 的"长期记忆"体系

本项目构建了三层上下文持久化机制，解决了 AI 跨会话"遗忘"的根本问题：

| 层级 | 载体 | 存储内容 | 生命周期 |
| ---- | ---- | -------- | -------- |
| 项目约束 | `CLAUDE.md` | 架构规则、部署命令、开发环境约束 | 随代码库永久存在 |
| 设计知识 | `docs/` 文档体系 | 架构决策、数据流、故障模式、接入规范 | 随代码库永久存在，与代码同步更新 |
| 隐性知识 | memory 系统 | 被否定的方案、非显式的决策依据、用户偏好 | 跨会话持久化 |

三层结合，AI 在任何一次新对话里都能快速恢复完整的项目上下文，不需要用户重复解释背景。

#### 7.6 值得推广的实践

**1. CLAUDE.md 作为 AI 的项目上下文入口**

把架构约束、命令、设计决策写进 CLAUDE.md，让 AI 在每次对话里都能获取项目背景。本项目的 CLAUDE.md 覆盖了：组件职责、架构版本、数据库 schema、部署命令、开发约束。这是成本最低、收益最高的单项实践。

**2. 错误信息驱动排查，而非现象描述**

把完整的 JSON 报错体直接给 AI，比"访问被拒绝了"这类描述高效得多。AI 可以直接定位到代码层面的原因，本项目的 403 排查从报错到根因不超过 5 分钟。

**3. 分模块验证，不一次性生成全部**

每个模块生成后立即验证，问题在小范围内暴露。本项目有 4 个 Python 服务 + 网关 + OPA + Keycloak，如果一次性生成再统一调试，问题会相互掩盖，排查成本指数级上升。

**4. 性能基准文档化**

记录真实的性能数字（OPA 72 req/s、Keycloak 113 req/s）。AI 在做性能相关的设计决策时会参考这些基准，而不是给出脱离实际的理论值。



### 8、IAM and Gateway 设计文档

#### 8.1 需求概述

构建基于 Kubernetes 的完整 IAM 和 Gateway 解决方案栈，提供统一的身份认证、授权管理和网关服务，支持动态权限控制、策略同步、证书管理，为企业级云原生应用提供安全可靠的基础设施。

#### 8.2 功能设计

**IAM 统一认证授权**
- 集成 Keycloak 作为统一身份提供商，支持用户管理、客户端管理、JWT 令牌签发
- 实现 pep-proxy，提供 Envoy ext_authz 授权服务，对流量进行两级细粒度权限验证
- 支持标准 OIDC 协议，与企业现有 SSO 体系无缝对接；同时支持 API Key 认证

**动态 ACL 权限控制**
- 路径级鉴权：OPA 策略引擎，bundle-server 将 `path_rules` 表推送为 OPA bundle，热更新无需重启
- 资源实例级鉴权：`resource_acl` 表前缀匹配，pep-proxy 直接查 PostgreSQL，实时无延迟
- resource-sync 通过 ext_proc 拦截业务响应，在 201 创建后自动写入 ACL，DELETE 后自动清理
- `pending_acl` 队列保障写入可靠性，指数退避重试（最多 10 次，上限 2560s）

**Gateway 统一网关管理**
- 基于 Envoy Gateway 构建统一 API 网关入口，SecurityPolicy 挂载 ext_authz，EnvoyExtensionPolicy 挂载 ext_proc
- gateway-manager 提供证书管理 API 和 OMS 日志回调 API
- 支持多路由配置、负载均衡、流量路由

**OPA 策略包管理**
- bundle-server 定时读取 `apps` + `path_rules` 表，生成 OPA bundle 推送，秒级生效
- PostgreSQL 存储所有策略数据，确保持久化；OPA 只持有内存快照

#### 8.3 技术架构

##### 8.3.1 技术栈

| 类别 | 技术 |
| ---- | ---- |
| 运行时 | Kubernetes, Envoy Gateway, Keycloak |
| 数据库 | PostgreSQL（iam DB + keycloak DB + opal DB） |
| 策略引擎 | OPA (Open Policy Agent) |
| 认证协议 | OIDC, OAuth2, API Key |
| 部署工具 | Helm, Docker, Kind |
| 测试框架 | 自动化黑盒测试链（`deploy/scripts/test.sh`） |

##### 8.3.2 文件结构

```
aidp-iam/
├── apps/
│   ├── iam-api/          # IAM 管理 API 与 Keycloak 代理
│   ├── pep-proxy/        # Envoy ext_authz 授权服务（HTTP + gRPC 双模式）
│   ├── bundle-server/    # OPA Bundle 生成器
│   ├── resource-sync/    # Envoy ext_proc ACL 同步器
│   ├── gateway-manager/  # Gateway 证书 API
│   ├── keycloak-spi/     # Keycloak 定制 SPI
│   └── keycloak-theme/   # Keycloak 定制主题
├── deploy/
│   ├── helm/
│   │   ├── aidp-gateway/ # Gateway Helm Chart
│   │   └── aidp-iam/     # IAM Helm Chart（含 Keycloak、PostgreSQL 子 chart）
│   ├── scripts/          # setup / cleanup / rebuild / test / test-gateway
│   └── kind/             # 本地 Kind 集群配置
├── docs/architecture/    # 架构设计文档（data-storage、manifest-template 等）
├── tests/
│   ├── fixtures/         # Mock 后端镜像源
│   └── manual-cases/     # 手动验证脚本和日志
└── legacy/               # 历史材料，不用于当前部署路径
```

#### 8.4 核心算法

**前缀匹配 ACL 查询**（`apps/pep-proxy/app/db.py:query_acl`）：

```python
async def query_acl(tenant_id, user_path, group_paths, object_path):
    subjects = [user_path] + group_paths
    parts = object_path.split("/")
    # 从最长到最短构造所有祖先前缀候选
    candidates = ["/".join(parts[:i]) for i in range(len(parts), 0, -1)]
    row = await pool.fetchrow(
        """
        SELECT role_path, object_path AS matched_path FROM resource_acl
        WHERE tenant_id = $1
          AND user_path = ANY($2)
          AND object_path = ANY($3)
        ORDER BY LENGTH(object_path) DESC
        LIMIT 1
        """,
        tenant_id, subjects, candidates,
    )
    return (row["role_path"], row["matched_path"]) if row else None
```

子资源（`/Details`、`/Password`）自动继承父资源权限，不需要为每个子路径单独写 ACL 记录。

**OPA 路径鉴权（Rego，`data-storage.md` 中的实际逻辑）**：

```rego
allow {
    not app_disabled                          # 应用未被禁用
    some rule in data.path_rules              # 遍历 path_rules
    startswith(input.path, rule.path_prefix)  # 路径前缀匹配
    method_matches(rule)                      # Method 匹配（NULL 则通配）
    some g in rule.required_groups            # 该规则的绑定组
    g in input.groups                         # 用户在其中一个绑定组里
}
```

**pending_acl 指数退避重试**（`apps/resource-sync/app/db.py`）：

```python
# 退避策略：5s → 10s → 20s → ... → 2560s，最多重试 10 次
backoff = min(5 * (2 ** (new_count - 1)), 2560)
next_retry = datetime.utcnow() + timedelta(seconds=backoff)
```

#### 8.5 数据结构

##### 8.5.1 数据库拓扑

```
PostgreSQL 实例
├── keycloak DB — Keycloak 内部存储（不触碰）
├── opal DB    — OPAL Server pub/sub（保留）
└── iam DB     — 所有 IAM 业务表
    ├── 系统级（无 tenant_id）
    │   ├── apps                    # 应用注册表
    │   ├── resource_patterns       # 资源匹配 + ID 提取规则
    │   ├── resource_actions        # 操作识别规则
    │   ├── permission_groups       # 功能点（OPA path_rules 来源之一）
    │   ├── permission_group_paths  # 功能点 → (path_prefix, method) N:M
    │   ├── permission_group_bindings # 功能点 → Keycloak 组 N:M
    │   └── app_manifests           # 应用 manifest JSON 注册表
    └── 租户级（有 tenant_id）
        ├── resource_acl            # 资源实例级 ACL（三元组，前缀匹配）
        ├── api_keys                # API Key 管理
        └── pending_acl             # ACL 写入失败重试队列
```

**关键设计决策**：系统面向单一客户销售，租户代表部门。应用注册、路径规则、资源模式都是系统级配置（无 tenant_id），只有资源 ACL 和重试队列是租户级数据（有 tenant_id）。

##### 8.5.2 核心表结构

**resource_acl — 资源权限表（租户级）**：

```sql
CREATE TABLE IF NOT EXISTS resource_acl (
    id          SERIAL PRIMARY KEY,
    tenant_id   VARCHAR(128) NOT NULL,
    user_path   VARCHAR(512) NOT NULL,  -- AccessManager/Tenants/{tid}/Users/{uid} 或组路径
    object_path VARCHAR(512) NOT NULL,  -- 资源路径前缀，支持层级继承
    role_path   VARCHAR(512) NOT NULL,  -- AccessManager/Tenants/System/Roles/{Owner|Contributor|Viewer}
    created_by  VARCHAR(512),
    created_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, user_path, object_path)
);
CREATE INDEX IF NOT EXISTS idx_acl_object ON resource_acl (tenant_id, object_path);
CREATE INDEX IF NOT EXISTS idx_acl_user   ON resource_acl (tenant_id, user_path);
```

**resource_patterns — 资源匹配 + ID 提取规则（系统级）**：

```sql
CREATE TABLE IF NOT EXISTS resource_patterns (
    app_name          VARCHAR(128) NOT NULL REFERENCES apps(app_name),
    resource_prefix   VARCHAR(256) NOT NULL,
    method            VARCHAR(10)  NOT NULL DEFAULT '',   -- '' = 通配所有方法
    resource_type     VARCHAR(128) NOT NULL,
    id_source         VARCHAR(16)  NOT NULL DEFAULT 'path',  -- path / query / body
    id_field          VARCHAR(128) NOT NULL DEFAULT 'id',    -- 支持嵌套 'data.kb_id'
    response_id_field VARCHAR(128) DEFAULT NULL,             -- 响应体字段名（与请求字段不同时）
    on_create_acl     JSONB        NOT NULL DEFAULT '[]',    -- 创建时额外写入的 ACL 模板
    allow_create_without_acl BOOLEAN NOT NULL DEFAULT false, -- 允许无 ACL 前置检查直接创建
    admin_bypass      BOOLEAN      NOT NULL DEFAULT true,
    PRIMARY KEY (app_name, resource_prefix, method)
);
```

`id_source` 三种模式：
- `path`（默认）：资源 ID 在 URL 路径段，如 `/v1/kb/kb-001`
- `query`：资源 ID 在 query 参数，如 `/v1/kb?KDSID=kb-001`
- `body`：资源 ID 在请求体 JSON，如 `{"data": {"kb_id": "kb-001"}}`，`id_field` 支持嵌套 `data.kb_id`

**pending_acl — 写入失败重试队列（租户级）**：

```sql
CREATE TABLE IF NOT EXISTS pending_acl (
    id          SERIAL PRIMARY KEY,
    action      VARCHAR(16)  NOT NULL,       -- 'write' | 'delete_prefix'
    tenant_id   VARCHAR(128) NOT NULL,
    user_path   VARCHAR(512) NOT NULL,
    object_path VARCHAR(512) NOT NULL,
    role_path   VARCHAR(512),
    retry_count INTEGER      NOT NULL DEFAULT 0,
    max_retries INTEGER      NOT NULL DEFAULT 10,
    last_error  TEXT,
    created_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
    next_retry  TIMESTAMP    NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pending_retry ON pending_acl (next_retry)
    WHERE retry_count < max_retries;
```

##### 8.5.3 OPA bundle 数据结构

bundle-server 定时读取 `apps` + `permission_group_paths` + `permission_group_bindings` 表，生成如下结构推送给 OPA：

```json
{
  "apps": {
    "KnowledgeBase": { "path_prefix": "/KnowledgeBase/", "enabled": true },
    "MemoryStore":   { "path_prefix": "/MemoryStore/",   "enabled": true }
  },
  "path_rules": [
    {
      "path_prefix":     "/AccessManager/",
      "method":          null,
      "required_groups": ["master-admins", "tenant-admins"]
    },
    {
      "path_prefix":     "/KnowledgeBase/",
      "method":          null,
      "required_groups": ["tenant-admins", "all-users"]
    }
  ]
}
```

**为什么 resource_acl 不推 OPA**：

```
apps + path_rules：几十条数据，适合全量加载到内存（微秒级查询）
resource_acl：约 300 万条数据，推到 OPA 内存会爆

所以：
  路径级鉴权 → OPA 内存（快，微秒级）
  资源级鉴权 → 直接查 PostgreSQL（有索引，< 1ms）
```

##### 8.5.4 app manifest 结构

应用注册时提交 manifest JSON（以 MemoryStore 为例，`docs/architecture/manifest-template-memorystore.json`）：

```json
{
  "namespace": "MemoryStore",
  "display_name": "统一记忆管理",
  "base_url": "http://127.0.0.1:8040",
  "list_filter_mode": "gateway_inject",
  "resources": [
    {
      "type": "Instances",
      "path_pattern": "/MemoryStore/Tenants/{tenantId}/Instances/{instanceName}",
      "methods": ["GET", "PUT", "DELETE"],
      "actions": [
        {
          "name": "MemoriesQuery",
          "path_suffix": "/Memories/Query",
          "http_method": "POST",
          "required_role": "AccessManager/Tenants/System/Roles/Viewer"
        }
      ],
      "default_acl": [
        {
          "user_template":   "AccessManager/Tenants/{tenantId}/Groups/all-users",
          "object_template": "MemoryStore/Tenants/{tenantId}/Instances",
          "role_path":       "AccessManager/Tenants/System/Roles/Viewer"
        }
      ],
      "children": [
        {
          "type": "Memories",
          "path_pattern": "/MemoryStore/Tenants/{tenantId}/Instances/{instanceName}/Memories/{memoryId}",
          "allow_create_without_acl": true,
          "default_acl": []
        }
      ]
    }
  ],
  "supported_roles": [
    "AccessManager/Tenants/System/Roles/Owner",
    "AccessManager/Tenants/System/Roles/Contributor",
    "AccessManager/Tenants/System/Roles/Viewer"
  ],
  "custom_roles": []
}
```

`list_filter_mode` 两种模式：
- `gateway_inject`（默认）：ext_proc 查询 resource_acl 后将有权限的 ID 注入 `X-Allowed-Ids` 请求头
- `app_callback`：应用主动调用 `POST /AccessManager/Tenants/{tid}/Action/ListAllowedIds` 自行过滤

##### 8.5.5 数据量预估与索引策略

| 表 | 预估数据量 | 说明 |
| -- | ---------- | ---- |
| apps | 几十条 | 系统级，应用数量有限 |
| resource_patterns | 每应用 1-3 条 | 系统级，含 ID 提取规则 |
| resource_actions | 每资源 0-6 条 | 标准 RESTful 无需记录，用代码默认规则 |
| permission_groups | 几十条 | 系统级，功能点数量有限 |
| resource_acl | 约 300 万条 | 200 租户 × 5 应用 × 1000 资源 × 3 ACL |
| pending_acl | 通常为 0 | 仅写入失败时产生，成功重试后删除 |
| api_keys | 几十至几百条 | 租户级，外部应用接入凭证 |

resource_acl 索引策略：

| 索引 | 用途 |
| ---- | ---- |
| `idx_acl_object (tenant_id, object_path)` | pep-proxy 按资源路径查权限 |
| `idx_acl_user (tenant_id, user_path)` | ext_proc 按用户查可访问资源列表 |

#### 8.6 实现计划

| 阶段 | 内容 | 工时 |
| ---- | ---- | ---- |
| 阶段 1：基础框架 | 搭建 K8s 开发环境、配置 Envoy Gateway、部署 Keycloak、验证网络连通性 | 80h |
| 阶段 2：核心功能 | 实现 pep-proxy ext_authz、bundle-server 策略包、resource-sync ACL 同步、gateway-manager 证书管理 | 120h |
| 阶段 3：测试与优化 | 编写自动化测试脚本、黑盒测试验证、性能优化、文档完善 | 40h |

**总计：约 240 小时**

### 核心目录结构

```
apps/
├── iam-api/          # IAM 管理 API 与 Keycloak 代理
├── pep-proxy/        # Envoy ext_authz 授权服务
├── bundle-server/    # OPA Bundle 生成器
├── resource-sync/    # Envoy ext_proc ACL 同步器
├── gateway-manager/  # Gateway 证书 API
├── keycloak-spi/     # Keycloak 定制 SPI
└── keycloak-theme/   # Keycloak 定制主题

deploy/
├── helm/             # Helm Chart（生产与 Mock）
├── scripts/          # 部署脚本
└── kind/             # 本地 Kind 集群配置

docs/architecture/    # 架构设计文档
```

### 关键技术栈

- **运行时**：Kubernetes, Envoy Gateway, Keycloak
- **数据库**：PostgreSQL（iam DB + keycloak DB + opal DB）
- **策略引擎**：OPA (Open Policy Agent)
- **认证协议**：OIDC, OAuth2, API Key
- **部署工具**：Helm, Docker, Kind
- **测试框架**：自动化黑盒测试链