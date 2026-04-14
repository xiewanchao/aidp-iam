# AIDP IAM 管理台 UI 设计（单租户版）

> 版本：v2.0（单租户简化）
> 日期：2026-04-14

---

## 1 设计前提

本版假设：

- **单租户**：所有用户、应用、资源都在固定的 `aidp` realm 下
- **角色合并**：原 `master-admin` + `tenant-admin` 合并为统一的 **`admins`** 组
- **不再有租户切换、租户管理、跨租户视角**
- **realm 固化**为 `aidp`，无创建新 realm 的能力

## 2 角色与组模型

| 组 | 来源 | 作用 |
|---|---|---|
| **admins** | 系统预置 | 管理员：用户/组/应用/路径规则/IdP/API Key |
| **{app}-admins** | 注册应用时自动创建 | 应用管理员：本应用资源治理 |
| **all-users** | 系统预置（默认组） | 普通用户：业务 App 使用者 |
| **自定义组** | admin 创建 | 部门组、业务组（如 dev-team / kb-creators） |

JWT 输出 `groups` 示例：

```json
{
  "sub": "uuid-xxx",
  "iss": "https://gateway.aidp.com/realms/aidp",
  "groups": ["admins", "all-users", "knowledgebase-admins"],
  "email": "zhangsan@aidp.com"
}
```

## 3 顶层架构：1 主台 + 应用控制台 + 个人中心

```
┌────────────────────────────────────────────────────┐
│  登录后根据 JWT.groups 判定入口                     │
└────────────────────────────────────────────────────┘
        │
        ├── admins                    → 管理控制台（Admin Console）
        ├── {app}-admins (但非 admins)  → 直接进对应应用控制台
        └── 仅 all-users               → 个人中心 / 业务 App UI
```

**导航大幅简化**：原来"平台台 + 租户台"两层合并为一个管理控制台。

## 4 管理控制台（admins 使用）

**一句话定位**：管理员管理一切的地方。原来的"平台台" + "租户台"全部并入这里。

```
管理控制台
├── 概览
│   ├── 用户/用户组/应用/API Key 数量
│   ├── 今日 QPS / 错误率
│   └── 最近活动流
│
├── 用户管理 (Users)
│   ├── 用户列表 (搜索/按组过滤/按状态过滤)
│   ├── 邀请用户 (邮件激活链接，不预置密码)
│   ├── 用户详情
│   │   ├── 基本信息
│   │   ├── 所属组 (勾选立即生效)
│   │   ├── 权限预览 (TA 当前能访问哪些 path)
│   │   ├── 登录历史
│   │   └── 重置密码 / 禁用 / 删除
│   └── 批量导入 CSV
│
├── 用户组管理 (Groups)
│   ├── 系统预置组
│   │   ├── admins (管理员，谨慎管理成员)
│   │   ├── all-users (全员默认组)
│   │   └── {app}-admins (每个应用一个，自动创建)
│   ├── 自定义组
│   │   ├── 部门组 (dev-team, finance-team)
│   │   └── 业务组 (kb-creators, beta-testers)
│   └── 组详情
│       ├── 成员列表（增删）
│       ├── 关联的 path_rules (该组授予哪些路径权限)
│       └── 关联的 resource_acl (该组持有哪些资源权限)
│
├── 应用管理 (Apps)
│   ├── 应用列表 (apps 表)
│   ├── 注册应用
│   │   ├── 基本信息: app_name, path_prefix, display_name
│   │   ├── 资源模式 (resource_patterns)
│   │   │   含 id_source / id_field / id_query_param
│   │   │   带"测试 ID 提取"按钮
│   │   ├── 资源动作 (resource_actions, 非标准 API 可选)
│   │   └── 路径规则预览 (实时生成 Rego)
│   ├── 应用详情
│   │   ├── License 开关 (enabled/disabled)
│   │   ├── 资源模式
│   │   ├── 资源动作
│   │   └── 路径规则
│   ├── 进入应用控制台 (跳转到 {app}-console)
│   └── 下线应用
│
├── 路径规则 (Path Rules)
│   ├── 规则列表 (按应用分组显示)
│   │   /api/v1/**             → admins      (系统预置)
│   │   /knowledgebase/admin/* → kb-admins   (应用预置)
│   │   /knowledgebase/v1/kb   → kb-creators (自定义)
│   ├── 新建规则 (path_prefix + required_group)
│   └── 编辑/删除
│
├── 身份认证 (Identity Providers)
│   ├── IdP 列表
│   ├── 新建 IdP (SAML/OIDC, 含 Metadata 自动导入)
│   ├── IdP 详情
│   │   ├── 基本配置 (Endpoint / 证书 / 过期告警)
│   │   ├── IdP Mapper (外部属性 → Keycloak 属性/组)
│   │   └── 测试登录
│   └── Client Mapper (JWT 输出哪些 claim)
│
├── API Key (API Keys)
│   ├── Key 列表 (前缀 + 关联身份 + 最后使用)
│   ├── 创建 Key
│   │   ├── Name / 关联用户或服务账号 / 作用域 / 有效期 / 限流
│   │   └── 一次性明文展示 (30 秒后跳转)
│   ├── 轮换 Key (subject_id 不变, ACL 保留)
│   └── 禁用 / 删除
│
├── 审计日志
│   ├── 登录日志
│   ├── 权限变更日志 (谁加/移谁的组、改了哪个 path_rule)
│   ├── 资源 ACL 变更
│   └── API Key 使用记录
│
└── 系统设置
    ├── 密码策略
    ├── 登录安全 (MFA、锁定策略)
    ├── 邮件/SMTP 配置 (用于邀请激活)
    └── 品牌定制 (Logo / 主题色)
```

### 关键 UI 决策

- **顶部不再有"切换租户"**——单租户没必要
- **"邀请用户"用激活邮件**：admin 不直接看到密码，发激活链接（`UPDATE_PASSWORD` required action），用户自己设密码
- **"注册应用"右侧实时预览生成的 Rego 策略**，让开发者直观感受 path_rules 效果
- **资源模式编辑器**要有"测试"按钮：输入示例 URL，实时显示提取到的 resource_id
- **用户详情的"权限预览"**：自动求解该用户当前能访问哪些 path（基于其所在组 + path_rules）
- **组详情显示"关联规则"**：删除组前能看到会影响哪些权限

### IdP + Mapper 配置可视化

抽象概念太多，UI 上做一个**流程图引导**：

```
[IdP 配置]                配置 SAML/OIDC 连接参数（含证书）
      ↓
[IdP Mapper]              外部 IdP 属性 → Keycloak 用户属性 / 自动加组
      例：Department=研发 → 自动加入 dev-team 组
      ↓
[用户登录]                Keycloak 创建影子用户，按 Mapper 规则填充
      ↓
[Client Mapper]           Keycloak 用户信息 → JWT claims
      例：user.groups → JWT.groups
      ↓
[JWT 给后端]              后端读 X-Auth-Groups header 做鉴权
```

每个节点点开就能配置；配置完显示一个"模拟登录后 JWT 长什么样"的预览框。

## 5 应用控制台（{app}-admins 使用）

**一句话定位**：管具体应用的资源**治理**（不是"我的资源"操作；那个在业务 App 里做）。

按应用独立一套（knowledgebase-admin 只看知识库）：

```
应用控制台: knowledgebase
├── 概览
│   ├── 资源总数 / 活跃用户 / 今日 QPS
│   └── 待处理事项 (孤儿资源/异常分享)
│
├── 资源管理 (治理视角)
│   ├── 资源列表 (跨用户查看本应用所有资源)
│   ├── 资源详情
│   │   ├── Owner / Contributors / Viewers
│   │   ├── 授权历史 (谁分享给谁)
│   │   ├── 强制回收权限
│   │   └── 转移 Owner (原 Owner 离职等场景)
│   └── 异常告警 (孤儿资源、超大共享、长期未访问)
│
├── 路径规则 (只读, 跳转主台编辑)
│
├── 应用组成员
│   └── knowledgebase-admins 成员管理
│
└── 审计
    └── 该应用相关的所有操作
```

**重要边界**：
- ❌ **不在这里做**：用户分享自己的 KB（属于业务操作，应在业务 App UI 里）
- ✅ **在这里做**：管理员审计、强制回收、转移 owner、异常治理

## 6 个人中心（all-users 都能看）

```
个人中心
├── 基本信息 (姓名/邮箱/手机/头像)
├── 安全设置
│   ├── 修改密码
│   ├── 双因素认证 (TOTP/WebAuthn)
│   └── 登录设备 (强制下线)
├── 我的 API Key (只看自己创建的)
├── 我创建的资源 (只读概览，跳业务 App 操作)
├── 我收到的分享
└── 登录历史
```

## 7 应用权限配置：必须按应用独立

理由不变：

1. **权限模型本身按 app 隔离**：path_rules 和 resource_actions 都带 app_name
2. **UX 合理**：knowledgebase-admin 不该看到 memory-admin 的资源
3. **扩展性**：新应用接入时直接挂载到控制台，不用改平台代码

**技术实现**：应用控制台可以设计成**可插拔 UI 模块**：

```
admin-console/
├── core/                    # 主台骨架
│   ├── layout/
│   ├── users/
│   ├── groups/
│   ├── apps/
│   ├── path-rules/
│   ├── idp/
│   └── api-keys/
├── app-modules/
│   ├── knowledgebase/       # 每个应用一个模块
│   │   ├── routes.ts
│   │   └── pages/
│   │       ├── ResourceList.vue
│   │       └── ResourceDetail.vue
│   └── memory/
│       └── ...
```

注册新应用时，平台也下发对应的前端模块 URL，动态加载。

## 8 API Key 管理：增删改查的正确做法

### 关键 UX 原则

**明文只展示一次**。这是行业铁律（AWS、GitHub、Stripe 都这样）。

### 流程设计

```
[点击 "新建 API Key"]
     ↓
[填写表单]
  - Name: 用途描述
  - 关联身份: 用户 / 服务账号 / 指定 group
  - 作用域: 哪些路径可用（从 path_rules 选）
  - 有效期: 30 天 / 90 天 / 永不过期
  - 速率限制: 100 req/min
     ↓
[后端生成 + hash 存 DB]
     ↓
[一次性展示页]
  ┌──────────────────────────────────────┐
  │  ⚠ 请立即复制，此密钥只会显示一次       │
  │                                      │
  │  API Key:                            │
  │  aidp_live_xxxxxxxxxxxxxxxxxxxx     │
  │  [复制到剪贴板]  [下载 .env 文件]      │
  │                                      │
  │  30 秒后该页面将关闭                   │
  └──────────────────────────────────────┘
     ↓
[跳转到 Key 列表]
  只显示 key_prefix (aidp_live_xxxx...) + metadata
```

### 列表字段

| 字段 | 显示 |
|---|---|
| Name | 用户填的描述 |
| Prefix | `aidp_live_abcd...`（前 8 位） |
| 关联身份 | 用户名/服务账号 |
| 作用域 | 路径 tags |
| 创建时间 | 相对时间（3 天前） |
| 最后使用 | 相对时间 + IP |
| 状态 | Active / Expired / Revoked |
| 操作 | 轮换 / 禁用 / 删除 |

### 轮换 vs 重建

- **轮换（Rotate）**：subject_id 不变，换一个新 Key。`resource_acl` 里的数据保留，业务系统无感知
- **删除后重建**：subject_id 变了（新用户/服务账号），权限需重新授权

UI 上两个按钮都要有，并做清楚区分。

## 9 技术栈与组件建议

### 框架
- **Vue 3 + TypeScript + Element Plus** 或 **React + TypeScript + Ant Design Pro**
- 路由：Vue Router / React Router
- 状态：Pinia / Redux Toolkit
- OIDC 登录：`oidc-client-ts`（浏览器端直接对接 Keycloak，拿 token）

### 推荐组件
- **表格**：ProTable（Ant Design Pro）或 Element Plus Table + 自定义封装
- **表单**：支持"分步表单 + 条件显示 + 动态字段"
- **树形选择**（组/权限）：Element Tree 或 Ant Tree
- **JSON 编辑器**（resource_patterns、Rego 预览）：Monaco Editor

### 鉴权
- 前端用 `oidc-client-ts` 拿 JWT
- 所有 API 请求带 `Authorization: Bearer <token>`
- 前端路由根据 JWT 的 groups 动态渲染菜单（隐藏无权限的页面）
- 关键操作双保险：前端隐藏 + 后端 `path_rules` 强制校验

## 10 分层 MVP 实现路线

```
第 1 层（MVP）: 1-2 周
  - 登录 (Keycloak OIDC)
  - 用户管理 (CRUD + 邀请)
  - 用户组管理 (CRUD + 成员)
  - API Key 管理 (创建/列表/轮换/删除)

第 2 层（完整 IAM）: 1-2 周
  + 应用管理 (注册/路径规则/资源模式)
  + 路径规则编辑
  + 应用控制台 (knowledgebase 一个示例)

第 3 层（企业级）: 2 周
  + IdP + Mapper 配置
  + 审计日志
  + MFA
  + 品牌定制
```

## 11 关键交互细节

### 信息一致性

- **顶部不再显示租户**：单租户上下文，去掉所有"租户名"字样
- **应用切换**：顶部下拉 `[knowledgebase ▾]`，可切到 memory / llm 等已注册应用
- **右上角 avatar 下拉**：个人中心 / 修改密码 / 退出

### 错误提示

不要只报"403 Forbidden"。要翻译成用户能懂的：

```
❌ 你没有权限执行此操作
   原因：该操作需要 admins 组权限
   你当前的组：all-users, knowledgebase-admins
   联系系统管理员获取权限
```

### 危险操作

- 删除应用、下线 IdP、批量删用户都要二次确认
- 显示影响范围："将影响 230 个用户"

## 12 与多租户版的对比

| 维度 | 旧（多租户） | 新（单租户） |
|---|---|---|
| 控制台层级 | 平台台 + 租户台 + 应用台 | 管理台 + 应用台 |
| 一级菜单数 | 平台 5 + 租户 8 = 13 | 管理 9 |
| 角色 | master-admin / tenant-admin / app-admin / user | admin / app-admin / user |
| 租户切换 | 顶部下拉 | 删除 |
| "进入租户视角" | 有红色警示 | 删除 |
| 路径规则 | 系统级 vs 租户级 | 全部全局 |
| 创建租户向导 | 3 步 | 删除 |
| 应用启用申请 | 租户申请 + master 审批 | admin 直接启用 |
| 页面总数 | ~30 | ~20 |

## 13 后端配套修改

代码层面对应改动（非 UI 但需要同步）：

| 改动 | 文件 | 说明 |
|---|---|---|
| `_require_admin` 检查 `admins` 而非 `tenant-admins` | `pep-proxy/main.py` | 角色合并 |
| init-job 创建 `admins` 组（不是 `tenant-admins` / `master-admins`） | `keycloak-init/init-keycloak.py` | 预置组改名 |
| 删除"创建租户" API（`POST /api/v1/tenants`） | `da-idb-proxy/api/v1/tenants.py` | 单租户不需要 |
| Realm 固定为 `aidp`，删除创建 realm 逻辑 | 同上 | 固化 |
| `path_rules` 不加 `tenant_id` 字段 | `iam` schema | 全局规则 |
| `resource_acl.tenant_id` 退化为 audit 字段或删除 | `resource-sync` | 单租户场景 |
| Rego 策略中的"管理员组"统一改 `admins` | `bundle-server` | 同步策略 |

## 14 核心设计原则

1. **单租户化后，删掉所有租户相关概念**（切换、视角、跨租户）
2. **角色仍按"管理员 / 应用管理员 / 普通用户"分层**，应用管理员独立控制台
3. **抽象概念可视化**（IdP→Mapper→JWT 流程图）
4. **密钥一次性展示 + 前缀标识**（API Key 行业规范）
5. **危险操作都有二次确认 + 预览后果**
6. **业务操作（用户分享自己资源）回归业务 App UI，IAM 只做治理**
