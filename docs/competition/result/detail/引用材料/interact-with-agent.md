# 与 Agent 交互记录

本文档记录了 AIDP 公共服务组件——鉴权认证模块项目开发过程中与 AI Agent（Claude Code）的关键交互内容。项目是一套部署在 Kubernetes 上的 IAM 与 API 网关鉴权栈，核心组件包括 IAM 管理 API、pep-proxy（Envoy ext_authz 鉴权服务）、OPA 策略引擎和 Keycloak。

文档按开发时间线组织：从项目启动时的架构设计问答，到第一版代码完成后的待办事项，再到应用接入规范、前后端联调、联调中发现的错误，最后是测试用例生成、代码重构，以及对整个 AI 辅助开发过程的实践总结。

---

## 一、设计问答

> 完整问答原文见 `docs/architecture/question-and-answer.md`。

### Q1：统一鉴权系统的初始设计

**背景**：项目启动时，需要针对不同下层应用（DataAgent、MemoryStore、DataBase 等）设计一套统一的鉴权方案。

**问题**：
- resource_acl 三元组结构如何设计？
- 各应用需要提供哪些信息才能接入？
- Manifest 文件的作用是什么？
- 权限判定流程如何设计？

**关键决策**（详细表结构见 `docs/architecture/data-storage.md`，完整鉴权设计见 `docs/architecture/unified-authz-design.md`）：

| 决策点 | 结论 | 原因 |
|---|---|---|
| ACL 存储结构 | 三元组（user_path, object_path, role_path），无 effect 字段 | 只存"允许"，无显式拒绝，简化查询逻辑 |
| 路径匹配方式 | 前缀匹配，写入时存完整路径，查询时构造所有祖先前缀候选 | 子资源自动继承父资源权限，无需为每个子路径单独写 ACL |
| 权限管理 API | `GET/PUT/DELETE /AccessManager/Tenants/{id}/ACLs` | 统一入口，支持批量操作 |
| 用户/组来源 | 统一由 AccessManager 管理，其他应用不维护用户 | 单一用户源，避免跨系统用户同步问题 |
| 回调机制 | 自定义角色触发 `Action/Authorize` 回调，由应用二次判断 | 静态 ACL + Role 无法表达复杂业务权限时的扩展点 |

**Manifest 的核心作用**（供鉴权系统和网关使用，非最终用户直接操作）：

1. 自动生成权限配置界面——前端从 manifest 读取资源树和角色列表，动态渲染权限配置表单
2. 校验请求合法性——防止应用请求检查不存在的 object 路径
3. 服务发现——pep-proxy 根据 `base_url` + Namespace 构造自定义角色回调地址
4. default_acl 同步——新租户创建时，自动为各组写入类型级默认权限

---

### Q2：资源路径树结构与前缀匹配

**问题**：路径可能很长，树高不确定，如 `/MemoryStore/Tenants/123/MemoryStores/456/Memories/789/Chunks/101`，这算多级子资源吗？前缀匹配如何实现？

**结论**：

路径本质是树结构，树高不固定。鉴权系统不预设深度，只做前缀匹配。查询某个资源的权限时，将其路径拆解为所有祖先前缀候选，取最长匹配的 ACL 记录生效：

```sql
SELECT * FROM resource_acl
WHERE tenant_id = '123'
  AND user_path IN (用户路径, 所属组路径...)
  AND object_path IN (
    '/MemoryStore/Tenants/123/MemoryStores/456/Memories/789/Chunks/101',
    '/MemoryStore/Tenants/123/MemoryStores/456/Memories/789',
    '/MemoryStore/Tenants/123/MemoryStores/456',
    '/MemoryStore/Tenants/123',
    '/MemoryStore'
  )
ORDER BY LENGTH(object_path) DESC
LIMIT 1;
```

写入 ACL 时存完整路径，不支持 `/*` 通配符，前缀匹配语义自然覆盖所有子资源。

---

### Q3：三类管理员的职责边界

**问题**：master-admins 中的 admin 绕过了哪些鉴权？tenant-admin 能管理 manifest 吗？

**结论**（各组件职责详见 `docs/architecture/component-responsibility.md`）：

| 用户组 | 鉴权 bypass 范围 | 能否管理 manifest |
|---|---|---|
| `master-admins` | 不 bypass 租户资源级鉴权；可访问 `Tenants/System` 路径下的系统级资源 | 是（manifest 属于系统级配置，路径为 `/AccessManager/Tenants/System/AppManifests/`） |
| `tenant-admins` | bypass pep-proxy 资源级 ACL 检查；对于标准应用拥有所有资源的 Owner 权限，对于委托授权模式的应用（如 DataAgent）则走路径级鉴权，不自动拥有实例级 Owner 权限 | 否（manifest 是 System 级路径，tenant-admins 无权访问） |
| `{namespace}-admins` | 不 bypass，需要在 manifest default_acl 中配置 Owner 权限 | 否 |

**系统级配置清单**（仅 master-admins 可管理，路径均在 `Tenants/System` 下）：

| 资源 | 说明 |
|---|---|
| `AppManifests` | 应用接入配置 |
| `Roles` | 系统预置角色定义 |
| `PathRules` | OPA 路径级鉴权规则 |
| `LogCollect` | 日志采集配置（`GET/POST /Tenants/System/LogCollect`） |

---

### Q4：多层级授权场景下的鉴权架构设计

**问题**：在 DataAgent 应用中，tenant-admins 需要将不同数据库实例的管理权限分配给不同的用户组，普通用户只能操作自己有权限的实例，而类型级权限（如"可以创建数据库"）和实例级权限（如"可以操作某个具体数据库"）的控制粒度不同。针对这种多层级授权场景，鉴权系统应该如何设计？

**结论**：采用路径级鉴权（OPA）与资源级鉴权（resource_acl）双模式，分别处理不同粒度的访问控制需求（详见 `docs/architecture/unified-authz-design.md`）：

| 模式 | 引擎 | 控制粒度 | 典型场景 |
|---|---|---|---|
| 路径级鉴权 | OPA + path_rules | URL 路径模式，与具体资源实例无关 | 限制某类用户只能访问特定接口；tenant-admins bypass 资源级检查 |
| 资源级鉴权 | resource_acl 前缀匹配 | 具体资源实例（含子资源继承） | 控制用户对某个具体数据库/知识库实例的读写权限 |

**为什么不能只用资源级鉴权**：资源级鉴权依赖 resource_acl 表中已有的记录，但"谁有权创建新资源"本身无法用实例级 ACL 表达——资源还不存在，ACL 也不存在。类型级 ACL（如对 `DataAgent/Tenants/t-001/DataAgentDBs` 整个集合授予 Contributor）可以解决创建权限，但 tenant-admins 的 bypass 逻辑（跳过所有资源级检查）无法用 ACL 记录表达，必须在路径级规则中处理。

**为什么不能只用路径级鉴权**：路径级规则是静态配置，无法表达"用户 A 只能访问自己创建的数据库实例 db-001，而不能访问 db-002"这类动态的实例级隔离需求。实例归属关系在运行时才产生，必须由 resource_acl 动态记录。

**双模式协同的授权流程**（以 DataAgent 为例）：

第一层——tenant-admins 通过路径级 bypass + 类型级 ACL 向用户组授权：

```
tenant-admins 写入类型级 ACL：
  user:   AccessManager/Tenants/t-001/Groups/team-a
  object: DataAgent/Tenants/t-001/DataAgentDBs        ← 类型级，非实例级
  role:   AccessManager/Tenants/System/Roles/Contributor
```

team-a 成员获得在该类型下创建新实例的权限。tenant-admins 自身的操作能通过，依赖路径级规则中的 bypass 逻辑，而非 resource_acl 中的记录。

第二层——用户创建实例时 ext_proc 自动写入实例级 Owner ACL：

```
ext_proc 拦截 201 响应，自动写入：
  user:   AccessManager/Tenants/t-001/Users/alice
  object: DataAgent/Tenants/t-001/DataAgentDBs/db-001  ← 实例级
  role:   AccessManager/Tenants/System/Roles/Owner
```

Owner 可以进一步将实例权限授予其他用户或组，形成多级委托，完全由 resource_acl 动态管理，无需修改任何路径级规则。

架构设计确定后，第一版代码随即展开。代码完成后沉淀了一批已知的优化点和待确认项，记录如下。

---

## 二、第一版代码生成后待办事项记录

> 完整待办列表见 `docs/architecture/todo.md`。

### 高优先级

| 项目 | 状态 | 说明 |
|---|---|---|
| Manifest 自动注册机制 | 待实现 | 当前需手动调用 API 注册；建议应用启动时自注册，改动最小 |

### 中优先级

| 项目 | 状态 | 说明 |
|---|---|---|
| 服务间调用的身份模型 | 待设计 | 服务账号路径格式、是否需要资源级鉴权待定义 |
| 新租户创建时自动同步所有 manifest 的 default_acl | 待实现 | 当前只处理"manifest 注册 → 遍历租户"方向，缺少反向同步 |
| pep-proxy 回调超时策略 | 待确认 | 当前固定 500ms；是否需要按 namespace 配置不同超时待评估 |

### 低优先级

| 项目 | 状态 | 说明 |
|---|---|---|
| resource_acl 前缀匹配深度限制 | 待实现 | 建议限制最大 10 级，避免极端路径深度导致性能问题 |
| ACL 变更通知机制 | 待评估 | 当前实时查询，待性能测试后决定是否引入缓存及失效通知 |
| role_path 为空的三元组语义明确化 | 待确认 | 是否需要支持空 role（仅记录关联关系，不授予权限）待定 |

### 已完成

- resource_acl 表结构迁移为三元组（user_path, object_path, role_path）
- pep-proxy 资源级鉴权改为统一 URL 前缀匹配
- ext_proc 改为按 object_path 前缀级联删除 ACL
- IAM API 新增 ACL 管理接口和 Manifest 管理接口
- 统一 URL 规范：Create 改为 PUT 到 collection 路径（无 ID），服务端生成 ID
- 统一返回值规范文档化
- manifest 模板添加字段注释
- 应用接入信息收集表补充 API 标准示例
- 修复普通用户访问他人详情的权限放大问题（pep-proxy self-only 检查）

待办事项中优先级最高的是 Manifest 自动注册机制。Manifest 是各应用接入鉴权系统的核心配置文件，其结构和填写规范说明如下。

---

## 三、Manifest 模板说明

Manifest 是应用接入鉴权系统的核心配置文件。各应用负责人按照接入信息收集表（`docs/onboarding/app-onboarding-template.md`）填写后，根据填写的注册表生成 `manifest.json` 并完成接入配置。

### 3.1 Manifest 的作用

| 作用 | 说明 |
|---|---|
| 权限配置界面生成 | 前端从 manifest 读取资源树和角色列表，动态渲染权限配置表单 |
| 请求合法性校验 | 防止应用请求检查不存在的 object 路径 |
| 服务发现 | pep-proxy 根据 `base_url` + Namespace 自动构造自定义角色回调地址 |
| default_acl 同步 | 新租户创建时，自动为各用户组写入类型级默认权限 |

### 3.2 Manifest JSON 结构示例

```json
{
  "namespace": "DataAgent",
  "base_url": "https://dataagent.example.com",
  "resources": [
    {
      "type": "DataAgentDBs",
      "path_pattern": "DataAgent/Tenants/{TenantId}/DataAgentDBs/{DbId}",
      "methods": ["GET", "PUT", "PATCH", "DELETE"],
      "default_acl": [
        {
          "user_template":   "AccessManager/Tenants/{TenantId}/Groups/all-users",
          "object_template": "DataAgent/Tenants/{TenantId}/DataAgentDBs",
          "role_path":       "AccessManager/Tenants/System/Roles/Contributor"
        },
        {
          "user_template":   "AccessManager/Tenants/{TenantId}/Groups/dataagent-admins",
          "object_template": "DataAgent/Tenants/{TenantId}/DataAgentDBs",
          "role_path":       "AccessManager/Tenants/System/Roles/Owner"
        }
      ]
    },
    {
      "type": "Tables",
      "parent": "DataAgentDBs",
      "path_pattern": "DataAgent/Tenants/{TenantId}/DataAgentDBs/{DbId}/Tables/{TableId}",
      "methods": ["GET", "PUT", "PATCH", "DELETE"],
      "default_acl": []
    }
  ],
  "custom_roles": [],
  "actions": [
    {
      "name": "Query",
      "path_suffix": "/Query",
      "http_method": "POST",
      "resource_type": "DataAgentDBs",
      "required_role": "Contributor"
    }
  ]
}
```

### 3.3 关键填写规则

**1. Namespace 一旦确定不可更改。** 它是所有 URL 的第一段，也是 resource_acl 中 object_path 的前缀。修改 Namespace 意味着所有已写入的 ACL 记录全部失效。

**2. 系统预置角色的 Namespace 是 `AccessManager`，不是应用自己的 Namespace。**

```
✓ AccessManager/Tenants/System/Roles/Owner
✗ DataAgent/Tenants/System/Roles/Owner   ← 这是自定义角色，不是系统预置角色
```

**3. `object_template` 必须是类型级路径，不含具体资源 ID。**

```
✓ "DataAgent/Tenants/{TenantId}/DataAgentDBs"
✗ "DataAgent/Tenants/{TenantId}/DataAgentDBs/db-001"
```

类型级 ACL 表示对该类型下所有资源的默认权限；具体资源实例的 Owner ACL 由 ext_proc 在创建时自动写入。

**4. 子资源通常不需要配置 default_acl。** 权限通过父资源 ACL 的前缀匹配自动继承，`default_acl` 留空即可。

**5. 单例子资源（如 Metadata、Init）不需要 Create 操作。** 它随父资源创建时预置，manifest 中 `default_acl` 留空，权限完全依赖父资源 ACL 的前缀继承。

Manifest 确定后，前后端需要就 API 规范达成一致，以下是联调过程中使用的统一规范。

---

## 四、前后端联调规范

> 完整规范见 `docs/onboarding/app-onboarding-template.md`，前端接口参考见 `docs/api/frontend-api-reference.md`。

### 4.1 标准 URL 格式

所有接口 URL 遵循以下层级结构（参照 Microsoft Azure REST API 规范）：

```
https://{Fqdn}/{Namespace}/Tenants/{TenantId}/{TypeA}/{IdA}[/{TypeB}/{IdB}[/...]][/{Action}]
```

### 4.2 六类标准操作

| 操作 | HTTP 方法 | URL 示例 |
|---|---|---|
| List（列举） | `GET` | `GET /DataAgent/Tenants/{tid}/DataAgentDBs` |
| Get（获取单个） | `GET` | `GET /DataAgent/Tenants/{tid}/DataAgentDBs/{id}` |
| Create（创建） | `PUT` | `PUT /DataAgent/Tenants/{tid}/DataAgentDBs`（**末尾无 ID**） |
| Update（更新） | `PATCH` | `PATCH /DataAgent/Tenants/{tid}/DataAgentDBs/{id}` |
| Delete（删除） | `DELETE` | `DELETE /DataAgent/Tenants/{tid}/DataAgentDBs/{id}` |
| Action（自定义动作） | `POST` | `POST /DataAgent/Tenants/{tid}/DataAgentDBs/{id}/Query` |

### 4.3 命名规范

| 位置 | 规范 | 示例 |
|---|---|---|
| URL 路径段（Namespace、资源类型、Action） | `PascalCase` | `DataAgent`、`DataAgentDBs`、`Query` |
| 查询参数 | `snake_case` | `?page_size=50&order_by=created_at` |
| 请求体 / 响应体字段 | `snake_case` | `{"user_id": "...", "total_count": 42}` |
| HTTP Header | `kebab-case` | `X-Allowed-Ids`、`X-Auth-User-Id` |

### 4.4 统一返回值格式

**List 接口**：

```json
{
  "value": [ ... ],
  "next_link": "/DataAgent/Tenants/t-001/DataAgentDBs?page=2&page_size=20",
  "total_count": 42
}
```

**Create 接口**（HTTP 201）：

```json
{
  "id": "db-generated-001",
  "name": "新数据库",
  "tenant_id": "t-001",
  "created_at": "2026-05-06T10:00:00Z"
}
```

**错误响应**：

```json
{
  "error": {
    "code": "ResourceNotFound",
    "message": "DataAgentDB 'db-999' not found in tenant 't-001'",
    "details": []
  }
}
```

### 4.5 前端需要了解的鉴权机制

#### List 过滤：X-Allowed-Ids 注入

List 接口默认由 IAM 在请求头注入 `X-Allowed-Ids`，后端按此列表过滤返回结果。前端无需感知该机制，但需注意：

- List 接口返回的资源已经是当前用户有权限查看的子集
- `total_count` 是权限过滤后的总数，不是全量数据总数

#### 操作按钮权限：QueryACLs 接口

前端渲染"删除"、"编辑"等操作按钮是否可用时，调用以下接口查询当前用户对指定资源的权限：

```
POST /AccessManager/Tenants/{TenantId}/Action/QueryACLs
```

请求体（支持批量查询）：

```json
{
  "queries": [
    {
      "user_path":   "AccessManager/Tenants/t-001/Users/user-uuid",
      "object_path": "DataAgent/Tenants/t-001/DataAgentDBs/db-001"
    }
  ]
}
```

响应体：

```json
{
  "results": [
    {
      "allowed":        true,
      "user_path":      "AccessManager/Tenants/t-001/Users/user-uuid",
      "object_path":    "DataAgent/Tenants/t-001/DataAgentDBs/db-001",
      "matched_object": "DataAgent/Tenants/t-001/DataAgentDBs/db-001",
      "role_path":      "AccessManager/Tenants/System/Roles/Owner"
    }
  ]
}
```

`matched_object` 表示实际命中的 ACL 条目路径。子资源没有独立 ACL 时，会命中父资源的 ACL（前缀继承）。

#### 角色与操作的对应关系

| 角色 | 允许操作 |
|---|---|
| `Owner` | 全部操作（含 DELETE） |
| `Contributor` | 除 DELETE 外的所有操作，含创建子资源 |
| `Viewer` | 仅 GET / List |

联调规范确定后，前后端开始对接。以下是联调过程中发现并跟踪的接口问题。

---

## 五、错误记录

> 原始错误记录见 `docs/architecture/error.md`，涉及接口的完整定义见 `docs/api/frontend-api-reference.md`。

以下为前后端联调过程中发现的接口问题，按模块分类整理。

### E1：HTTP Method 不符合 API 规范

**问题**：前端 API 参考文档中，部分接口的 HTTP Method 与项目 API 规范不一致，例如创建操作使用了 `POST` 而非 `PUT`，更新操作使用了 `PUT` 而非 `PATCH`。

**规范要求**（完整规范见 `docs/onboarding/app-onboarding-template.md` 第零部分）：

| 操作 | 正确 Method | 说明 |
|---|---|---|
| Create | `PUT`（URL 末尾无 ID） | 服务端生成 ID，URL 指向资源类型集合 |
| Update | `PATCH`（URL 含 ID） | 部分更新语义 |
| Action | `POST` | 自定义动作 |

---

### E2：用户管理接口问题

| 接口 | 问题 | 状态 |
|---|---|---|
| `GET /AccessManager/Tenants/{tid}/Users` | 缺少 `created_at` 字段；缺少 `total_count`，分页机制有问题 | 待修复 |
| `GET /AccessManager/Tenants/{tid}/Users/{uid}/Details` | 权限放大导致接口可被任意用户访问（见 E4） | 已修复 |
| `PUT /AccessManager/Tenants/{tid}/Users/BatchCreate` | 超过多少条会报 payload too large 待确认，需前端限制最大条数 | 待确认 |
| `PUT /AccessManager/Tenants/{tid}/Users` | 新增邮箱字段，批量导入和创建接口是否已实现待确认 | 待确认 |

---

### E3：用户组接口问题

| 接口 | 问题 | 状态 |
|---|---|---|
| `GET /AccessManager/Tenants/{tid}/Groups` | 缺少 `total_count`；缺少创建时间字段 | 待修复 |
| `PUT /AccessManager/Tenants/{tid}/Groups` | 传入 `attributes` 字段报错：`input should be a valid dictionary` | 待修复 |
| 用户组详情 | API 文档中用户组管理部分格式错乱；缺少用户加入用户组的时间字段 | 待修复 |
| IdP / SAML 管理 | 所有相关接口返回 404 | 待排查 |

---

### E4：普通用户访问他人详情的权限放大问题

**问题**：`GET /AccessManager/Tenants/{tenant_id}/Users/{user_id}/Details` 接口，普通用户只能访问自己的信息，但测试时发现用户之间可以任意互访。

**根因分析**（完整请求链路见 `docs/architecture/request-flow.md`）：

```
现象：普通用户 A 能访问用户 B 的 /Details，返回 200
  ↓
OPA 未拒绝 → 检查 path_rules 数据
  ↓
DB init SQL 使用 WHERE NOT EXISTS → 旧数据未被新规则覆盖
  ↓
根因：存量数据问题，path_rules 中旧记录未被新的 init SQL 更新
修复：补 UPDATE 语句处理存量数据；在 pep-proxy 中增加 self-only 检查
```

**修复**：在 `apps/pep-proxy/app/main.py` 中，将 AccessManager 路径的 self-only 检查移至 resource_patterns 查找之前，防止通过 AccessManager bypass 绕过限制。

接口问题修复后，核心路径趋于稳定。此时进入测试用例生成阶段，由 Agent 系统性覆盖正常流程、异常操作和依赖服务故障三大场景。

---

## 六、DT 测试用例生成

> 测试用例规范见 `docs/architecture/case-test.md`。

在代码基本完成后，向 Agent 提供业务功能描述和相关代码，由 Agent 系统性生成覆盖三大场景的标准化测试用例，输出可直接导入测试管理工具的 CSV 格式。

### 6.1 生成规范

| 规则 | 说明 |
|---|---|
| 三大场景必须覆盖 | 正常流程、异常操作、依赖服务故障 |
| 每个功能点标配 | 1 条正向用例 + 至少 1 条异常/故障用例，异常用例不超过 5 条 |
| 优先级排序 | 用户高频操作 > 核心业务链路 > 高危边界场景 |
| 黑盒视角 | 站在不看代码的用户角度写，不暴露 API 名称和内部参数 |
| 步骤与结果对应 | `用例_测试步骤` 和 `用例_预期结果` 数量必须相同 |
| 故障用例命名 | `xxx服务在XXX时故障，导致xxx功能失败` |
| 预置条件通用项 | 所有用例必须包含"A800集群正常，模型服务、数据库、向量库服务集群正常" |

### 6.2 向 Agent 提问的方式

生成测试用例时，向 Agent 提供以下信息效果最好：

1. **业务功能描述**：用自然语言描述这个功能做什么，涉及哪些角色
2. **相关代码**：提供实现代码，Agent 会自动过滤无关逻辑，聚焦业务行为
3. **依赖服务**：明确指出依赖哪些外部服务（Keycloak、OPA、PostgreSQL 等），Agent 会为每个依赖生成故障场景

### 6.3 输出格式

CSV 格式，可直接导入测试管理工具：

```csv
用例_名称,用例_编号,用例_预置条件,用例_测试步骤,用例_预期结果
普通用户查看自己详情成功,TC-001,"1、A800集群正常，模型服务、数据库、向量库服务集群正常；2、已有普通用户账号","1、使用普通用户账号登录系统；2、进入个人信息页面","1、登录成功，跳转至首页；2、正确显示当前用户的个人信息"
普通用户查看他人详情失败,TC-002,"1、A800集群正常，模型服务、数据库、向量库服务集群正常；2、已有两个不同的普通用户账号","1、使用用户A账号登录系统；2、尝试访问用户B的个人信息页面","1、登录成功；2、系统拒绝访问，提示无权限查看他人信息"
Keycloak在用户登录时故障导致Token获取失败,TC-003,"1、A800集群正常，模型服务、数据库、向量库服务集群正常；2、已有普通用户账号；3、Keycloak服务已停止","1、使用普通用户账号尝试登录系统","1、系统提示登录失败，无法获取认证令牌"
```

### 6.4 为什么 Agent 生成比人工更全面

人工设计测试用例有两个系统性盲区：依赖服务故障场景（Keycloak 宕机、OPA 不可用、DB 超时）在正常开发环境不出现，容易遗漏；多租户 × 多角色 × 多资源类型的权限组合，人工穷举成本极高。Agent 从业务逻辑推导测试场景，不从代码路径推导，测的是业务行为而非实现细节。

---

## 七、代码重构规范

> 完整规范见 [`clean-code.md`](clean-code.md)，适用于项目所有 Python 服务。

### 7.1 重构触发条件

遇到以下任一情况，必须重构后才能继续添加功能：

| 触发条件 | 硬限制 | 处理方式 |
|---|---|---|
| 圈复杂度过高 | > 15 | 立即重构，不得合入 |
| 函数过长 | > 50 行 | 拆分为多个单职责函数 |
| 嵌套层次过深 | > 4 层 | 提取内层逻辑或使用 Early Return |
| 参数过多 | > 5 个 | 封装为 Pydantic model 或 dataclass |
| 重复逻辑 | 同一逻辑出现 3 次以上 | 提取为公共函数 |
| 函数名含"and" | — | 拆分为两个函数 |

### 7.2 向 Agent 提问的方式

重构时，向 Agent 提供以下信息：

1. **现有代码**：直接粘贴需要重构的函数或模块
2. **重构目标**：说明是降低圈复杂度、拆分职责还是消除重复
3. **约束**：明确接口签名不能变更（重构不改行为，只改结构）

Agent 会按以下顺序执行：读取现有代码理解设计意图 → 识别重构边界 → 生成重构后代码 → 逐行说明每处改动的原因 → 确认行为完全不变。

**重构与新功能严格分离**：重构不引入新功能，新功能不顺带重构。两者混在一起提交是 code review 最难发现问题的场景。

### 7.3 本项目的典型重构案例

**案例 1：`_enrich_user` 多职责导致 N+1 超时**

`_enrich_user` 同时处理 account_type、groups、nickname、email、created_at，是典型的多职责函数。在 all-users 组详情页场景下引发 N+1 查询超时。重构方向：拆分为 `_get_user_groups`、`_get_user_attributes` 等独立函数，并将 N+1 查询合并为批量查询。

**案例 2：`BatchImportRequest` 缺少长度约束**

`users: List[UserCreateRequest]` 无上限，超大 payload 被 Envoy 的 `maxRequestBytes=8192` 拦截报错。应在 schema 层加 `max_length=100` 约束，在业务层提前拒绝，而不是依赖网关报错。这也是错误记录 E2 中 BatchCreate 接口待确认项的根因。

---

## 八、AI 辅助开发的实践总结

本节总结在 AIDP 公共服务组件 —— 鉴权认证模块 项目中，与 Agent 协作效率最高的几种交互方式，供后续项目参考。

### 8.1 需求澄清：先追问隐含约束，再给方案

在项目启动阶段，直接描述需求往往包含大量隐含约束。向 Agent 提出需求时，Agent 不会直接给出方案，而是先追问五个维度：隔离粒度（数据库级/行级/无）、鉴权位置（网关层/应用层/两者）、数据性质（静态配置/动态授权）、外部依赖（IdP 类型、第三方 API 的 SLA）、性能基线（并发量、P99 延迟）。

本项目 Q1 的初始设计即通过这种方式展开：从"需要一套统一鉴权方案"出发，逐步澄清出 ACL 三元组结构、前缀匹配语义、manifest 的作用边界等核心决策，避免了在代码阶段才发现隐含约束的高成本返工。

### 8.2 根因分析：提供完整报错上下文

在排查鉴权问题时，将完整的 HTTP 响应体（包含状态码、错误字段、请求路径）直接提供给 Agent，比描述性语言定位效率高出数倍。Agent 会沿调用链逐层追溯——从 HTTP 响应到 OPA 策略，再到数据库初始化数据——而不是停在表面现象。

本项目中 E4 的权限放大问题即通过这种方式定位：表面是 OPA 未拒绝，根因是 DB init SQL 的 `WHERE NOT EXISTS` 语义导致存量数据未被更新，最终修复点在数据层而非策略层。

### 8.3 代码生成：先注入上下文，再描述任务

生成代码前，先明确告知 Agent 需要读取哪些现有文件（项目约束、数据结构、相关模块），再描述具体任务。这样生成的代码能自动匹配项目的命名风格、接口规范和架构约束，无需事后逐条纠正。

### 8.4 问题日志驱动的夜间修复

白天测试时只记录问题，不临时打补丁。问题按优先级（P0 阻断 / P1 影响体验 / P2 细节）分类，每条记录包含操作步骤、实际结果、预期结果和复现率。夜间由 Agent 批量读取日志，逐条根因分析并修复，次日输出修复摘要供人工验证。

这种分工避免了临时补丁掩盖真实问题，也让 Agent 能在无干扰的环境下做跨文件的一致性修复。

### 8.5 跨会话知识持久化

项目将关键设计决策、被否定的方案和调试陷阱分层持久化：

- 项目约束（API 规范、部署命令、环境限制）写入 `CLAUDE.md`，每次会话自动加载
- 架构决策和数据流写入 `docs/architecture/`，进入代码阶段时按需加载
- 被否定的方案、调试陷阱和用户偏好写入 `memory/`，遇到技术选型时加载

三层结合，Agent 在任何新会话中都能在短时间内恢复完整的项目上下文，不需要用户重复解释背景，也不会重新提出已被否定的方案。
