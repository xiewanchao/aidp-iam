# 应用接入信息收集表

版本：v1.1  
日期：2026-05-06  
用途：各应用团队填写后，用于生成 manifest.json 并完成接入配置。

---

## 第零部分：统一 API 规范（必读）

接入前请确认应用能遵循以下规范，这是鉴权系统正常工作的前提。

### 0.1 标准 URL 格式

所有接口 URL 必须遵循以下层级结构（参照 Microsoft Azure REST API 规范）：

```
https://{Fqdn}/{Namespace}/Tenants/{TenantId}/{TypeA}/{IdA}[/{TypeB}/{IdB}[/...]][/{Action}]
```

**六类标准操作示例**（以 Users 资源为例）：

| 操作 | HTTP 方法 | URL 示例 |
|---|---|---|
| List（列举） | `GET` | `GET https://{Fqdn}/{Namespace}/Tenants/{TenantId}/Users` |
| Get（获取单个） | `GET` | `GET https://{Fqdn}/{Namespace}/Tenants/{TenantId}/Users/{UserId}` |
| Create（创建） | `PUT` | `PUT https://{Fqdn}/{Namespace}/Tenants/{TenantId}/Users` |
| Update（更新） | `PATCH` | `PATCH https://{Fqdn}/{Namespace}/Tenants/{TenantId}/Users/{UserId}` |
| Delete（删除） | `DELETE` | `DELETE https://{Fqdn}/{Namespace}/Tenants/{TenantId}/Users/{UserId}` |
| Action（自定义动作） | `POST` | `POST https://{Fqdn}/{Namespace}/Tenants/{TenantId}/Users/{UserId}/{Action}` |

> **Create 与 Update 的关键区别**：Create 的 URL **末尾无资源 ID**（指向资源类型集合），服务端生成 ID 并在响应体中返回；Update 的 URL **含具体资源 ID**。鉴权系统通过这个区别判断是否需要写入 ACL。

**多级子资源示例**：

```
# MemoryStore 下的记忆库 → 记忆 → 片段
GET    /MemoryStore/Tenants/t-001/MemoryStores                          ← List
PUT    /MemoryStore/Tenants/t-001/MemoryStores                          ← Create（无 ID）
GET    /MemoryStore/Tenants/t-001/MemoryStores/ms-001                   ← Get
PATCH  /MemoryStore/Tenants/t-001/MemoryStores/ms-001                   ← Update
DELETE /MemoryStore/Tenants/t-001/MemoryStores/ms-001                   ← Delete
PUT    /MemoryStore/Tenants/t-001/MemoryStores/ms-001/Memories          ← Create 子资源
POST   /MemoryStore/Tenants/t-001/MemoryStores/ms-001/Backup            ← Action

# DataAgent 下的数据库 → 数据表
PUT    /DataAgent/Tenants/t-001/DataAgentDBs                            ← Create
PUT    /DataAgent/Tenants/t-001/DataAgentDBs/db-001/Tables              ← Create 子资源
POST   /DataAgent/Tenants/t-001/DataAgentDBs/db-001/Query               ← Action
```

### 0.2 命名格式规范

本项目 URL 路径格式参照 **Microsoft Azure REST API 规范**，字段和参数使用 **`snake_case`**。

| 位置 | 规范 | 示例 |
|---|---|---|
| URL 路径段 — Namespace | `PascalCase` | `DataAgent`、`MemoryStore`、`AccessManager` |
| URL 路径段 — 固定关键字 | `PascalCase` | `Tenants`、`System`、`Action` |
| URL 路径段 — 资源类型（集合名） | `PascalCase` | `DataAgentDBs`、`MemoryStores`、`Users` |
| URL 路径段 — 资源实例 ID（占位符） | `PascalCase`（文档/模板中） | `{TenantId}`、`{DbId}`、`{UserId}` |
| URL 路径段 — 资源实例 ID（实际值） | 小写 + 连字符 | `t-001`、`db-001`、`ms-abc123` |
| URL 路径段 — Action 名称 | `PascalCase` | `Query`、`Backup`、`Authorize` |
| 查询参数 | `snake_case` | `?page_size=50&order_by=created_at` |
| 请求体字段 | `snake_case` | `{"user_id": "...", "created_at": "..."}` |
| 响应体字段 | `snake_case` | `{"tenant_id": "...", "total_count": 42}` |
| HTTP Header | `kebab-case` | `X-Allowed-Ids`、`X-Auth-User-Id` |

**路径结构示意（占位符 vs 实际值）：**

```
文档/模板中（占位符用 PascalCase）：
  /DataAgent/Tenants/{TenantId}/DataAgentDBs/{DbId}/Tables/{TableId}

实际请求（具体值用小写+连字符）：
  /DataAgent/Tenants/t-001/DataAgentDBs/db-001/Tables/tbl-001
   ─────────  ───────  ─────  ────────────  ──────  ──────  ───────
   Namespace  关键字   实例ID  资源类型      实例ID  资源类型  实例ID
   Pascal     Pascal   小写    Pascal        小写    Pascal    小写
```

> **与 Azure 规范的差异**：Azure 的查询参数和响应体字段用 camelCase（`createdAt`、`nextLink`）。本项目字段格式选择 snake_case 以降低后端实现成本，前端可用 `humps` 库做一次全局转换。

---

### 0.3 统一返回值规范

所有接口的响应体必须遵循以下格式，**不允许额外包裹 `{code, message, data}` 层**。

#### 成功响应

**Get / Create / Update**：直接返回资源对象，HTTP 状态码表达语义。

```json
// GET /MemoryStore/Tenants/t-001/MemoryStores/ms-001
// HTTP 200 OK
{
  "id": "ms-001",
  "name": "我的记忆库",
  "tenant_id": "t-001",
  "created_at": "2026-05-06T10:00:00Z",
  "properties": {
    "description": "用于存储对话记忆"
  }
}
```

**List**：返回 `value` 数组 + 可选分页链接。

```json
// GET /MemoryStore/Tenants/t-001/MemoryStores?page=1&page_size=20
// HTTP 200 OK
{
  "value": [
    { "id": "ms-001", "name": "记忆库A" },
    { "id": "ms-002", "name": "记忆库B" }
  ],
  "next_link": "/MemoryStore/Tenants/t-001/MemoryStores?page=2&page_size=20",
  "total_count": 42
}
```

**Create（服务端生成 ID）**：返回 201 + 资源对象，`id` 字段必须在顶层。

```json
// PUT /MemoryStore/Tenants/t-001/MemoryStores
// HTTP 201 Created
{
  "id": "ms-generated-001",
  "name": "新记忆库",
  "tenant_id": "t-001",
  "created_at": "2026-05-06T10:00:00Z"
}
```

**Delete**：返回 204 No Content，无响应体。

**Action**：返回 200 + 操作结果。

```json
// POST /DataAgent/Tenants/t-001/DataAgentDBs/db-001/Query
// HTTP 200 OK
{
  "result": "查询结果...",
  "execution_time": 120
}
```

#### 错误响应

所有错误统一使用以下格式：

```json
// HTTP 4xx / 5xx
{
  "error": {
    "code": "ResourceNotFound",
    "message": "MemoryStore 'ms-999' not found in tenant 't-001'",
    "details": []
  }
}
```

**常用错误码：**

| HTTP 状态码 | error.code | 含义 |
|---|---|---|
| 400 | `InvalidRequest` | 请求参数不合法 |
| 401 | `Unauthorized` | 未提供或无效的认证凭据 |
| 403 | `Forbidden` | 无权限执行该操作 |
| 404 | `ResourceNotFound` | 资源不存在 |
| 409 | `Conflict` | 资源已存在（创建冲突） |
| 500 | `InternalServerError` | 服务内部错误 |

#### 资源 ID 的位置要求

ext_proc 在拦截创建响应时需要提取资源 ID，**ID 必须在响应体的顶层 `id` 字段**，或在 manifest 中通过 `response_id_field` 声明嵌套路径（如 `data.store_id`）。

```json
// 推荐：顶层 id（snake_case）
{ "id": "ms-001", "name": "..." }

// 可接受：嵌套路径（需在 manifest 中声明 response_id_field）
{ "data": { "store_id": "ms-001", "name": "..." } }
```

### 0.4 查询参数规范

所有查询参数使用 **snake_case** 命名；分页参数使用 `page` / `page_size`。

#### 标准查询参数

| 参数 | 类型 | 说明 | 示例 |
|---|---|---|---|
| `page` | integer | 页码，从 1 开始 | `?page=2` |
| `page_size` | integer | 每页条数，默认 20，建议上限 100 | `?page_size=50` |
| `search` | string | 关键词搜索 | `?search=alice` |
| `order_by` | string | 排序字段，支持 `asc` / `desc` | `?order_by=created_at desc` |
| `filter` | string | 过滤条件（应用自定义） | `?filter=status:active` |

#### 分页响应格式

```json
// GET /MemoryStore/Tenants/t-001/MemoryStores?page=1&page_size=20
// HTTP 200 OK
{
  "value": [ ... ],
  "next_link": "/MemoryStore/Tenants/t-001/MemoryStores?page=2&page_size=20",
  "total_count": 100
}
```

#### 自定义查询参数

业务相关的自定义参数使用 snake_case：

```
GET /DataAgent/Tenants/t-001/DataAgentDBs?page=1&page_size=10&include_deleted=false
```

### 0.5 其他参数规范

#### 路径参数

- 使用 **PascalCase** 标识资源类型段（如 `KnowledgeBases`、`DataAgentDBs`）
- 使用**原始 ID 字符串**标识资源实例段，不做额外编码（`kb-001`、`t-001`）
- 路径参数均为**必填**，不得有默认值

#### 请求头

| 头字段 | 说明 | 示例 |
|---|---|---|
| `Authorization` | Bearer Token 或 API Key，由网关注入，应用无需处理 | `Bearer eyJ...` |
| `Content-Type` | 请求体格式，JSON 接口必须为 `application/json` | `application/json` |
| `Accept` | 期望的响应格式，默认 `application/json` | `application/json` |
| `X-Request-Id` | 请求追踪 ID，由网关注入，应用应透传到日志 | `550e8400-e29b-41d4` |

> 自定义业务头使用 `X-` 前缀 + kebab-case，如 `X-Tenant-Context`。

#### 请求体

- 格式统一为 **JSON**，`Content-Type: application/json`
- 字段命名使用 **snake_case**（与响应体一致）
- Create（PUT）请求体包含资源属性，**不含 `id`**（由服务端生成）
- Update（PATCH）请求体只包含**需要变更的字段**（部分更新语义）

#### 幂等性

| 方法 | 幂等要求 |
|---|---|
| `GET` / `DELETE` | 天然幂等，重复调用结果相同 |
| `PATCH` | 应设计为幂等（相同请求体多次调用结果一致） |
| `PUT`（Create） | 非幂等，重复调用应返回 `409 Conflict` |
| `POST`（Action） | 由应用自行决定，需在 API 文档中说明 |

---

## 第一部分：应用基本信息

| 项目 | 填写 | 说明 |
|---|---|---|
| 应用 Namespace | | 英文，唯一标识，如 `DataAgent`、`MemoryStore`。作为所有 URL 的第一段，一旦确定不可更改。 |
| 应用显示名称 | | 中文名，用于权限配置界面展示，如"智能问数" |
| 应用服务地址（base_url） | | 如 `https://dataagent.example.com`。pep-proxy 遇到自定义角色时，按 `{BaseUrl}/{Namespace}/Tenants/{TenantId}/Action/Authorize` 构造回调地址。 |
| 负责人 | | 姓名 + 联系方式 |
| API 文档地址 | | Swagger / Postman / 其他 |

---

## 第二部分：资源清单

**什么是"资源"**：用户创建的、有归属的、需要控制"谁能看谁能改"的实体。例如知识库、记忆库、数据库、模板、文档。

**什么不是"资源"**：系统配置、搜索接口、统计接口、登录接口——这些走路径级保护，不需要填此表。

请列出所有需要权限控制的资源类型，并标注父子关系：

| 资源类型标识（英文复数） | 显示名 | 父资源类型（无则填"顶级"） | 说明 |
|---|---|---|---|
| 示例：KnowledgeBases | 知识库 | 顶级 | 用户创建的知识库实例 |
| 示例：Documents | 文档 | KnowledgeBases | 知识库下的文档 |
| 示例：Chunks | 文本块 | Documents | 文档下的切片 |
| | | | |
| | | | |

---

## 第三部分：REST API 规范确认

### 3.1 标准操作 URL 确认

请对照下表，填写应用**实际**的 URL，并确认是否符合规范（符合请填"是"，不符合请填实际 URL 并说明差异）：

#### 资源类型：____________（每种资源填一张表）

| 操作 | 规范 URL 格式 | 应用实际 URL | 符合规范？ |
|---|---|---|---|
| List（列举） | `GET /{Namespace}/Tenants/{TenantId}/{Type}` | | |
| Get（获取单个） | `GET /{Namespace}/Tenants/{TenantId}/{Type}/{Id}` | | |
| Create（创建） | `PUT /{Namespace}/Tenants/{TenantId}/{Type}`（**无 ID**，服务端生成） | | |
| Update（更新） | `PATCH /{Namespace}/Tenants/{TenantId}/{Type}/{Id}` | | |
| Delete（删除） | `DELETE /{Namespace}/Tenants/{TenantId}/{Type}/{Id}` | | |

**创建接口补充信息：**

| 问题 | 填写 | 示例 |
|---|---|---|
| 创建成功的 HTTP 状态码 | | `201` |
| 响应体中资源 ID 的字段名 | | `id`（顶层）或 `data.storeId`（嵌套） |
| 资源 ID 是由调用方提供还是服务端生成？ | | 调用方提供 / 服务端生成 |
| 响应体是否符合 0.2 节规范（直接返回资源对象）？ | | 是 / 否（请说明实际格式） |

---

### 3.1.1 单例子资源

**单例子资源**是指某个父资源下有且只有一份的子资源（如数据库的初始化配置、元数据），不需要 ID 区分，URL 末尾直接是资源类型名。

| 操作 | 规范 URL 格式 | 说明 |
|---|---|---|
| Get | `GET /{Namespace}/Tenants/{TenantId}/{ParentType}/{ParentId}/{SingletonType}` | 末尾无 ID |
| Update | `PATCH /{Namespace}/Tenants/{TenantId}/{ParentType}/{ParentId}/{SingletonType}` | 末尾无 ID |

**示例**（数据库的元数据配置）：

```
GET   /DataAgent/Tenants/t-001/DataAgentDBs/db-001/Metadata        ← 读取元数据
PATCH /DataAgent/Tenants/t-001/DataAgentDBs/db-001/Metadata        ← 更新元数据
GET   /DataAgent/Tenants/t-001/DataAgentDBs/db-001/Metadata/Init   ← 读取元数据下的 Init 配置
PATCH /DataAgent/Tenants/t-001/DataAgentDBs/db-001/Metadata/Init   ← 更新 Init 配置
```

**与多实例子资源的区别：**

| | 多实例子资源（如 Tables） | 单例子资源（如 Metadata） |
|---|---|---|
| URL 末尾 | `/{ParentId}/Tables/{TableId}` | `/{ParentId}/Metadata`（无 ID） |
| 是否有 Create 操作 | 有（PUT，ext_proc 自动写 Owner ACL） | 无（随父资源预置，不触发 ACL 写入） |
| 权限来源 | 自身 ACL + 父资源前缀继承 | 完全依赖父资源前缀继承 |
| manifest default_acl | 可配置 | 留空（`[]`） |

如果应用有单例子资源，请在下表中列出：

| 单例子资源类型 | 父资源类型 | 支持的操作 | 说明 |
|---|---|---|---|
| 示例：Metadata | DataAgentDBs | GET、PATCH | 数据库元数据，随数据库创建预置 |
| 示例：Init | Metadata | GET、PATCH | 元数据下的初始化配置 |
| | | | |

---

### 3.2 自定义 Action（非标准动词）

如果应用有不属于标准 CRUD 的操作（如"查询"、"备份"、"导出"、"执行"），请逐一填写：

| Action 名称 | HTTP 方法 | URL 路径后缀 | 作用于哪种资源 | 所需最低权限 |
|---|---|---|---|---|
| 示例：Query | POST | `/Query` | KnowledgeBases | Contributor |
| 示例：Export | POST | `/Export` | KnowledgeBases | Owner |
| | | | | |

---

## 第四部分：默认权限配置

### 4.0 系统内置用户组说明

系统预置四类用户组，应用在配置默认权限时需要了解各组的职责边界：

| 用户组 | 路径格式 | 职责 | 对资源的默认能力 |
|---|---|---|---|
| `master-admins` | `AccessManager/Tenants/{TenantId}/Groups/master-admins` | 平台级超级管理员，暂不参与业务鉴权 | 暂无（未来用于跨租户管理） |
| `tenant-admins` | `AccessManager/Tenants/{TenantId}/Groups/tenant-admins` | 租户管理员，管理用户/组/权限，**绕过资源级鉴权** | 对所有资源有 Owner 权限 |
| `{Namespace}-admins` | `AccessManager/Tenants/{TenantId}/Groups/{Namespace}-admins` | 应用管理员，管理本应用资源实例的权限 | 对本应用所有资源实例有 Owner 权限 |
| `all-users` | `AccessManager/Tenants/{TenantId}/Groups/all-users` | 租户内所有用户自动加入 | 由 manifest default_acl 决定（通常为 Contributor） |

> **注意**：`tenant-admins` 绕过 pep-proxy 的资源级鉴权，无需在 resource_acl 中为其配置权限。`{Namespace}-admins` 不绕过鉴权，需要在 manifest 的 `default_acl` 中为其配置 Owner 权限。

### 4.1 租户内用户的默认权限

新租户创建后，系统会根据 manifest 中的 `default_acl` 自动为各组写入类型级默认权限。请确认每种资源类型的默认权限是否合适：

| 资源类型 | all-users 默认角色 | {Namespace}-admins 默认角色 | tenant-admins | 备注 |
|---|---|---|---|---|
| 示例：KnowledgeBases | Contributor（可创建） | Owner | 自动绕过，无需配置 | |
| 示例：Documents | 无（继承父资源） | Owner | 自动绕过，无需配置 | |
| | | | | |

**角色说明：**
- `Owner`：全部操作（含删除）
- `Contributor`：除删除外的所有操作，含创建子资源
- `Viewer`：仅读取和列举
- `无`：不写入默认 ACL，依赖父资源继承

**对应 manifest default_acl 写法示例：**

```json
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
```

### 4.2 是否需要自定义角色

如果系统预置的 Owner / Contributor / Viewer 三个角色无法满足需求，请描述自定义角色：

| 自定义角色名称（英文） | 显示名 | 允许的操作 | 禁止的操作 | 适用场景 |
|---|---|---|---|---|
| 示例：Backuper | 备份员 | POST /Backup | 其他所有操作 | 只允许执行备份，不能读写数据 |
| | | | | |

**如有自定义角色，应用需实现以下回调接口：**

```
POST {BaseUrl}/{Namespace}/Tenants/{TenantId}/Action/Authorize
```

请求体（由 pep-proxy 发送）：

```json
{
  "user":        "AccessManager/Tenants/t-001/Users/bob",
  "object":      "DataAgent/Tenants/t-001/DataAgentDBs/db-001",
  "role":        "DataAgent/Tenants/System/Roles/Backuper",
  "action":      "POST",
  "action_name": "Backup"
}
```

响应体（应用返回）：

```json
{ "allowed": true,  "reason": "Backuper role permits Backup action" }
{ "allowed": false, "reason": "Backup not allowed on this resource" }
```

| 项目 | 填写 |
|---|---|
| 是否需要自定义角色？ | 是 / 否 |
| 如是，回调接口是否已实现？ | 是 / 否（预计完成时间：） |
| 回调接口的鉴权方式 | 如：Bearer Token / mTLS / 无 |

---

## 第五部分：List 过滤模式

List 接口只返回用户有权限查看的资源。有两种过滤模式，请选择：

| 模式 | 说明 | 适用场景 |
|---|---|---|
| **gateway_inject**（默认） | IAM 在请求头注入 `X-Allowed-Ids`，无条数限制，应用按此列表过滤 | 通用场景 |
| **app_callback** | 应用忽略 `X-Allowed-Ids`，在 List 处理器内主动调用 IAM 的 `ListAllowedIds` 接口获取可访问 ID 列表，自行过滤后返回 | 需要复杂过滤逻辑 |

**ListAllowedIds 接口（app_callback 模式使用）：**

```
POST /AccessManager/Tenants/{TenantId}/Action/ListAllowedIds
```

请求体：

```json
{
  "user_path":   "AccessManager/Tenants/t-001/Users/user-uuid",
  "type_prefix": "MemoryStore/Tenants/t-001/Instances",
  "page":        1,
  "page_size":   200
}
```

- `user_path`：从请求头 `X-Auth-User-Id` + `X-Auth-Tenant` 拼装，格式 `AccessManager/Tenants/{tid}/Users/{uid}`
- `type_prefix`：资源类型级路径（不含具体资源 ID），与 manifest 的 `object_template` 格式一致

响应体：

```json
{
  "ids":       ["inst-001", "inst-002"],
  "total":     2,
  "page":      1,
  "page_size": 200
}
```

应用拿到 `ids` 后，在自己的 DB 查询里加 `WHERE id IN (...)` 过滤即可。

> **注意**：`QueryACLs` 接口（`POST /Action/QueryACLs`）用于检查用户对**已知具体资源**的权限，不适用于 List 过滤场景（因为 List 时应用尚不知道有哪些资源 ID）。

---

### 5.2 QueryACLs 接口（应用主动查权限）

当应用需要在**业务逻辑中主动判断**某用户对某个已知资源是否有权限时（如渲染按钮是否可点击、执行前预检），使用此接口。

```
POST /AccessManager/Tenants/{TenantId}/Action/QueryACLs
```

请求体（支持批量查询）：

```json
{
  "queries": [
    {
      "user_path":   "AccessManager/Tenants/t-001/Users/user-uuid",
      "object_path": "DataAgent/Tenants/t-001/Databases/db-001"
    },
    {
      "user_path":   "AccessManager/Tenants/t-001/Users/user-uuid",
      "object_path": "DataAgent/Tenants/t-001/Databases/db-001/Knowledge/item-001"
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
      "object_path":    "DataAgent/Tenants/t-001/Databases/db-001",
      "matched_object": "DataAgent/Tenants/t-001/Databases/db-001",
      "role_path":      "AccessManager/Tenants/System/Roles/Owner"
    },
    {
      "allowed":        true,
      "user_path":      "AccessManager/Tenants/t-001/Users/user-uuid",
      "object_path":    "DataAgent/Tenants/t-001/Databases/db-001/Knowledge/item-001",
      "matched_object": "DataAgent/Tenants/t-001/Databases/db-001",
      "role_path":      "AccessManager/Tenants/System/Roles/Owner"
    }
  ]
}
```

- `matched_object`：实际命中的 ACL 条目路径。子资源没有独立 ACL 时，会命中父资源的 ACL（前缀继承）。
- `allowed: false` 时，`role_path` 和 `matched_object` 字段不存在。
- `user_path` 支持用户路径或用户组路径，组路径格式：`AccessManager/Tenants/{TenantId}/Groups/{GroupName}`。

**与 ListAllowedIds 的区别：**

| | QueryACLs | ListAllowedIds |
|---|---|---|
| 适用场景 | 已知资源 ID，查该用户有无权限 | 不知道有哪些资源，查用户能访问哪些 |
| 典型用途 | 渲染操作按钮、执行前预检 | List 接口过滤（app_callback 模式） |
| 输入 | `(user_path, object_path)` 列表 | `(user_path, type_prefix)` |
| 输出 | 每条查询的 `allowed` + `role_path` | 可访问的资源 ID 列表 |

| 问题 | 填写 |
|---|---|
| 选择哪种模式？ | |
| 分页参数是否使用 `page` / `page_size`（规范要求）？ | |
| 若不符合，实际参数名称是什么？ | |

---

## 第五部分B：应用服务主动写 ACL

某些场景下，应用后端服务需要**主动写入 ACL**，使资源对特定用户或用户组可见。典型场景：

- 创建**企业资源**后，写入 `all-users → Viewer`，使该资源对全员可见
- 管理员操作后，动态调整某个资源的访问范围

### 推荐方案：透传调用方 token

应用收到请求时，调用方的 `Authorization` header 里已有 Bearer token。把这个 token **原样透传**给 IAM 标准 `PUT /ACLs` 接口即可，IAM 验证 token 身份后按正常流程处理。

**适用条件：** 调用方必须是管理员（tenant-admins），因为写 ACL 需要管理员权限。企业资源通常只有管理员才能创建，天然满足此条件。

**KnowledgeBase 后端示例：**

```python
async def create_knowledge_base(request: Request, body: KBCreateRequest, tid: str):
    # 创建知识库
    kb = await db.insert_kb(tid, body)

    # 企业知识库：透传调用方 token 写 all-users → Viewer
    if body.kb_type == "enterprise":
        auth_header = request.headers.get("Authorization")
        async with httpx.AsyncClient() as client:
            await client.put(
                f"http://iam-services.aidp-iam.svc.cluster.local:8090"
                f"/AccessManager/Tenants/{tid}/ACLs",
                headers={
                    "Authorization": auth_header,      # 原样透传，不是应用自己的身份
                    "Content-Type": "application/json",
                },
                json={
                    "user_path":   f"AccessManager/Tenants/{tid}/Groups/all-users",
                    "object_path": f"KnowledgeBase/Tenants/{tid}/KnowledgeBases/{kb.id}",
                    "role_path":   "AccessManager/Tenants/System/Roles/Viewer",
                },
            )
    return kb
```

**IAM 侧：** 无需任何改动，现有的 `PUT /AccessManager/Tenants/{tid}/ACLs` 直接可用。

**优点：**
- 零额外配置，无 API Key，无 K8s Secret
- 调试时 Postman/curl 带 admin token 测试，行为完全一致
- 权限天然正确：只有 admin token 才能写 ACL，不会被普通用户滥用

### 备选方案：API Key（仅限异步任务场景）

如果应用有**后台定时任务**需要写 ACL（无用户 token 可用），可以申请应用专属 API Key。申请方式：联系 IAM 管理员，提供应用 namespace，由管理员调用：

```
POST /AccessManager/Tenants/{TenantId}/ApiKeys
{ "app_name": "KnowledgeBase", "subject_type": "app" }
```

返回的 Key 存入 K8s Secret，挂载为环境变量后通过 `X-API-Key` 请求头调用专属回调接口：

```
POST /AccessManager/Tenants/{TenantId}/Apps/{AppName}/ACLs
X-API-Key: <app-api-key>
```

> 大多数场景用透传 token 即可，不需要 API Key。

### API Key 申请

| 问题 | 填写 |
|---|---|
| 应用是否需要主动写 ACL？ | 是 / 否 |
| 如是，写 ACL 的具体场景是什么？ | |
| 是否有无用户 token 的异步写 ACL 场景？ | 是（需申请 API Key） / 否（用透传 token） |

### 接口定义

**写入 ACL（授权）**

```
POST /AccessManager/Tenants/{TenantId}/Apps/{AppName}/ACLs
X-API-Key: <app-api-key>
Content-Type: application/json

{
  "user_path":   "AccessManager/Tenants/{TenantId}/Groups/all-users",
  "object_path": "{AppName}/Tenants/{TenantId}/Resources/{resourceId}",
  "role_path":   "AccessManager/Tenants/System/Roles/Viewer"
}
```

响应（200）：

```json
{
  "status": "ok",
  "user_path": "AccessManager/Tenants/t-001/Groups/all-users",
  "object_path": "KnowledgeBase/Tenants/t-001/KnowledgeBases/kb-001",
  "role_path": "AccessManager/Tenants/System/Roles/Viewer"
}
```

**撤销 ACL（解除授权）**

```
DELETE /AccessManager/Tenants/{TenantId}/Apps/{AppName}/ACLs
X-API-Key: <app-api-key>
Content-Type: application/json

{
  "user_path":   "AccessManager/Tenants/{TenantId}/Groups/all-users",
  "object_path": "{AppName}/Tenants/{TenantId}/Resources/{resourceId}"
}
```

### 权限限制

- URL 中的 `{AppName}` 必须与 API Key 绑定的 `app_name` 一致，否则返回 403
- `object_path` 必须以 `{AppName}/Tenants/{TenantId}/` 开头，不能跨 namespace 写 ACL
- `user_path` 不限制，可以是用户路径或组路径

### API Key 申请

接入时由 IAM 管理员颁发，`app_name` 字段填写应用的 namespace（如 `KnowledgeBase`）。API Key 通过 K8s Secret 注入到应用容器，不得明文写入代码。

| 问题 | 填写 |
|---|---|
| 应用是否需要主动写 ACL？ | 是 / 否 |
| 如是，写 ACL 的具体场景是什么？ | |
| 是否已申请应用 API Key？ | 是 / 否（预计完成时间：） |

---

## 第六部分：其他特殊需求

| 问题 | 填写 |
|---|---|
| 是否有不需要鉴权的公开接口？请列出路径前缀 | |
| 是否有只允许服务间调用（无用户 JWT）的接口？ | |
| 是否有需要跨租户访问的场景？（注：当前设计不支持，如有需求请说明） | |
| 其他特殊鉴权需求 | |

---

## 填写注意事项

### 关于 Namespace

1. **Namespace 一旦确定不可更改**，它是所有 URL 的第一段，也是 resource_acl 中 object_path 的前缀。修改 Namespace 意味着所有已写入的 ACL 记录全部失效。

2. **Namespace 区分大小写**，建议使用 PascalCase（如 `DataAgent`、`MemoryStore`），与 URL 路径段保持一致。

---

### 关于角色路径

3. **系统预置角色的 Namespace 是 `AccessManager`，不是应用自己的 Namespace**：

   ```
   ✓ 正确：AccessManager/Tenants/System/Roles/Owner
   ✓ 正确：AccessManager/Tenants/System/Roles/Contributor
   ✓ 正确：AccessManager/Tenants/System/Roles/Viewer

   ✗ 错误：DataAgent/Tenants/System/Roles/Owner   ← 这是自定义角色，不是系统预置角色
   ```

4. **自定义角色的 Namespace 必须是应用自己的 Namespace，不能用 `AccessManager`**：

   ```
   ✓ 正确：DataAgent/Tenants/System/Roles/Backuper    ← DataAgent 应用的自定义角色
   ✓ 正确：MemoryStore/Tenants/System/Roles/Executor  ← MemoryStore 应用的自定义角色

   ✗ 错误：AccessManager/Tenants/System/Roles/Backuper ← 不能用 AccessManager 命名空间定义自定义角色
   ```

5. **自定义角色没有内置语义**，pep-proxy 遇到非 `AccessManager` 命名空间的角色时，会回调应用的 `Action/Authorize` 接口，由应用自行判断是否允许。如果应用没有实现该接口，所有自定义角色的请求都会被拒绝。

---

### 关于 default_acl 模板

6. **`object_template` 必须是类型级路径（无具体资源 ID）**，不能是实例路径：

   ```json
   ✓ 正确："object_template": "DataAgent/Tenants/{TenantId}/DataAgentDBs"
   ✗ 错误："object_template": "DataAgent/Tenants/{TenantId}/DataAgentDBs/db-001"
   ```

   类型级 ACL 表示"对该类型下所有资源的默认权限"，具体资源实例的 Owner ACL 由 ext_proc 在创建时自动写入。

7. **`{TenantId}` 是固定占位符，不要替换成具体值**。AccessManager 在同步时会自动将其替换为每个租户的真实 ID。

8. **子资源通常不需要配置 default_acl**，留空即可，权限通过父资源 ACL 的前缀匹配自动继承：

   ```json
   { "type": "Tables", "default_acl": [] }
   ```

---

### 关于 Create URL

9. **Create 操作的 URL 末尾不含资源 ID**，指向资源类型集合，服务端生成 ID 并在响应体中返回：

   ```
   ✓ 正确：PUT /DataAgent/Tenants/t-001/DataAgentDBs          ← Create（无 ID）
   ✓ 正确：PATCH /DataAgent/Tenants/t-001/DataAgentDBs/db-001 ← Update（有 ID）
   ✗ 错误：PUT /DataAgent/Tenants/t-001/DataAgentDBs/db-001   ← 这是 Update，不是 Create
   ```

10. **Create 响应体中的资源 ID 必须在顶层 `id` 字段**，或在 manifest 中通过 `response_id_field` 声明嵌套路径。ext_proc 依赖这个字段写入 ACL，如果提取不到 ID，Owner ACL 将不会被写入。

---

### 关于单例子资源

11. **单例子资源的 URL 末尾不含资源 ID**，`PATCH` 也不例外——父资源的 ID 已经唯一确定了这份单例，不需要再加 ID：

    ```
    ✓ 正确：PATCH /DataAgent/Tenants/t-001/DataAgentDBs/db-001/Metadata   ← 单例，无 ID
    ✓ 正确：PATCH /DataAgent/Tenants/t-001/DataAgentDBs/db-001/Tables/tbl-001 ← 多实例，有 ID
    ✗ 错误：PATCH /DataAgent/Tenants/t-001/DataAgentDBs/db-001/Metadata/meta-001 ← 单例不需要 ID
    ```

12. **单例子资源不需要 Create 操作**，它随父资源创建时预置，不触发 ext_proc 写 ACL。manifest 中 `default_acl` 留空，权限完全依赖父资源 ACL 的前缀匹配继承。

13. **单例子资源可以多级嵌套**，每一级都不带 ID：

    ```
    GET   /DataAgent/Tenants/t-001/DataAgentDBs/db-001/Metadata          ← 单例
    PATCH /DataAgent/Tenants/t-001/DataAgentDBs/db-001/Metadata/Init     ← 单例的单例子属性
    ```

---

### 关于回调接口

14. **应用侧只需要实现一个回调接口**：`Action/Authorize`，且只有使用自定义角色的应用才需要实现。路径格式固定：

    ```
    POST {BaseUrl}/{Namespace}/Tenants/{TenantId}/Action/Authorize
    ```

    不需要在 manifest 中声明 `callback_url`，pep-proxy 会根据 `base_url` 和 Namespace 自动构造。

15. **回调响应必须在 500ms 内返回**，超时视为拒绝（fail-close）。如果应用的鉴权逻辑较复杂，建议提前缓存判断结果。

---

## 填写完成后

请将本文件连同 API 文档一起发送给 IAM ，IAM 将根据填写内容生成 `manifest.json` 并与各应用确认后完成接入。

参考文档：
- Manifest 模板：`diagrams/manifest-template.json`
