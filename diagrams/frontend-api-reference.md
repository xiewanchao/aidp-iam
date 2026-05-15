# IAM 系统接口文档（前端对接版）

**版本**: v2.5 | **日期**: 2026-05-14

本文档基于当前代码实现，列出前端对接所需的全部接口。所有路径遵循统一格式：

```
/AccessManager/Tenants/{tenant_id}/{resource_type}/{resource_id}
```

`{tenant_id}` 为租户 ID（例如 `aidp`）。应用资源路径遵循：

```
/{app_namespace}/Tenants/{tenant_id}/{resource_type}/{resource_id}
```

---

## 用户管理

| 接口名称 | Method | 路径 | 说明 | 请求体/参数 | 响应 |
|---|---|---|---|---|---|
| 用户列表 | GET | `/AccessManager/Tenants/{tenant_id}/Users` | 支持搜索/分页/按组过滤 | `?search=&group_id=&first=0&max=50` | `UserListPageResponse` |
| 查询当前用户信息 | GET | `/AccessManager/Tenants/{tenant_id}/Users/Me` | 返回当前登录用户的基本信息（含邮箱），用于重置密码场景展示"邮件将发送至 xxx@example.com"；无需传 user_id，由服务端从请求头自动识别 | — | `UserMeResponse` |
| 用户详情 | GET | `/AccessManager/Tenants/{tenant_id}/Users/{user_id}/Details` | 用户信息 + 所属组 | — | `UserDetailResponse` |
| 创建用户 | PUT | `/AccessManager/Tenants/{tenant_id}/Users` | 创建内部用户，可选绑组 | `{"username","password","email","nickname","groups":[gid],"temporary_password":true}` | `UserListResponse` (201) |
| 修改用户 | PATCH | `/AccessManager/Tenants/{tenant_id}/Users/{user_id}` | 修改启用状态、昵称、邮箱或所属组 | `{"enabled":bool,"nickname":"...","email":"...","groups":["gid"]}` | `UserListResponse` |
| 删除用户 | DELETE | `/AccessManager/Tenants/{tenant_id}/Users/{user_id}` | 删除单个用户 | — | 204 |
| 批量删除 | POST | `/AccessManager/Tenants/{tenant_id}/Users/BatchDelete` | 批量删除 | `{"user_ids":["uuid1"]}` | `BatchOperationResponse` |
| 重置密码 | PUT | `/AccessManager/Tenants/{tenant_id}/Users/{user_id}/Password` | 重置密码，联邦用户返回 400；重置后 temporary=true，用户下次登录必须修改密码（固定行为） | `{"password":"..."}` | 204 |
| 查询密码状态 | GET | `/AccessManager/Tenants/{tenant_id}/Users/{user_id}/PasswordStatus` | 查询密码创建时间、是否临时密码、距过期剩余天数（依赖 Realm 密码策略中的 forceExpiredPasswordChange） | — | `PasswordStatusResponse` |
| 添加用户到组 | PUT | `/AccessManager/Tenants/{tenant_id}/Users/{user_id}/Groups/{group_id}` | 将用户加入指定组 | — | 204 |
| 移除用户出组 | DELETE | `/AccessManager/Tenants/{tenant_id}/Users/{user_id}/Groups/{group_id}` | 将用户从组中移除 | — | 204 |
| 用户可选组 | GET | `/AccessManager/Tenants/{tenant_id}/Users/{user_id}/AvailableGroups` | 所有组 + joined 标记 | — | `[{"id","name","joined":bool}]` |
| 批量创建用户 | POST | `/AccessManager/Tenants/{tenant_id}/Users/BatchCreate` | 前端解析 CSV 后批量创建，JSON body，best-effort（部分失败不影响其余行），单次上限 100 条 | `{"users":[{"username","password","email","nickname","groups":[gid],"temporary_password":true},...]}` | `BatchOperationResponse` |
| CSV 导入模板 | GET | `/AccessManager/Tenants/{tenant_id}/Users/ImportTemplate` | 下载 CSV 模板 | — | text/csv |
| 批量导入 | POST | `/AccessManager/Tenants/{tenant_id}/Users/BatchImport` | 上传 CSV 文件批量创建用户（服务端解析 CSV） | multipart/form-data | `BatchOperationResponse` |

**UserListPageResponse 示例**

```json
{
  "users": [
    {
      "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "username": "alice",
      "email": "alice@example.com",
      "enabled": true,
      "account_type": "internal",
      "nickname": "Alice",
      "groups": [{"id": "g1", "name": "all-users"}],
      "created_at": "2026-01-01T00:00:00Z"
    }
  ],
  "total": 42
}
```

**查询当前登录用户邮箱（用于重置密码场景）**

调用 `GET /AccessManager/Tenants/{tenant_id}/Users/Me`，无需传 user_id，服务端自动从请求头识别当前用户。

**UserMeResponse 示例**

```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "username": "alice",
  "email": "alice@example.com",
  "enabled": true,
  "account_type": "internal"
}
```

> 联邦用户（`account_type=federated`）的邮箱由外部 IdP 提供，重置密码接口会返回 400，前端应据此隐藏重置密码入口。

**PasswordStatusResponse 示例**

```json
{
  "user_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "credential_created_at": "2026-04-01T10:00:00Z",
  "is_temporary": false,
  "expiry_days": 90,
  "days_remaining": 54,
  "is_expired": false
}
```

字段说明：
- `credential_created_at`：密码最后一次设置的时间（来自 Keycloak credentials[].createdDate）
- `is_temporary`：是否为临时密码（用户尚未完成首次修改）
- `expiry_days`：Realm 密码策略中配置的有效天数；`null` 表示未配置过期策略
- `days_remaining`：距过期剩余天数；`null` 表示无过期策略或无法计算
- `is_expired`：密码是否已过期（`days_remaining <= 0`）

---

## 密码策略管理

Realm 级别的密码策略，控制密码复杂度和有效期。配置后对该租户下所有内部用户生效。

| 接口名称 | Method | 路径 | 说明 | 请求体/参数 | 响应 |
|---|---|---|---|---|---|
| 查询密码策略 | GET | `/AccessManager/Tenants/{tenant_id}/PasswordPolicy` | 获取当前 Realm 的密码策略配置 | — | `PasswordPolicyResponse` |
| 更新密码策略 | PUT | `/AccessManager/Tenants/{tenant_id}/PasswordPolicy` | 更新密码策略，字段均可选，仅传入需要修改的项 | `PasswordPolicyRequest` | `PasswordPolicyResponse` |

**PasswordPolicyRequest / PasswordPolicyResponse 示例**

```json
{
  "expire_days": 90,
  "min_length": 8,
  "require_uppercase": true,
  "require_lowercase": true,
  "require_digits": true,
  "require_special": false,
  "history_count": 5
}
```

字段说明：

| 字段 | 类型 | 说明 | Keycloak 策略名 |
|---|---|---|---|
| `expire_days` | int \| null | 密码有效天数；`null` 表示不过期 | `forceExpiredPasswordChange` |
| `min_length` | int \| null | 最小密码长度 | `length` |
| `require_uppercase` | bool \| null | 是否要求大写字母 | `upperCase` |
| `require_lowercase` | bool \| null | 是否要求小写字母 | `lowerCase` |
| `require_digits` | bool \| null | 是否要求数字 | `digits` |
| `require_special` | bool \| null | 是否要求特殊字符 | `specialChars` |
| `history_count` | int \| null | 禁止重复使用最近 N 个历史密码；`null` 表示不限制 | `passwordHistory` |

> **注意**：密码策略仅对内部用户（非联邦用户）生效。`expire_days` 设置后，`GET .../PasswordStatus` 的 `days_remaining` 字段才有意义。前端可在用户详情页或登录后首页展示密码剩余有效期提醒。

---

## 邮件服务设置

邮件功能分两个接口：**SMTP 服务器配置**（发件通道）和**邮件功能开关**（业务行为）。前端可将两者合并在同一个"邮件服务设置"页面中展示。

> **设计约束**：邮箱仅用于验证（密码重置、邮箱验证），不作为登录凭据。`login_with_email_allowed` 由服务端强制锁定为 `false`，前端展示时应将该字段置灰。

### SMTP 服务器配置

| 接口名称 | Method | 路径 | 说明 | 请求体/参数 | 响应 |
|---|---|---|---|---|---|
| 查询 SMTP 配置 | GET | `/AccessManager/Tenants/{tenant_id}/SmtpSettings` | 获取当前 SMTP 服务器配置，密码不返回 | — | `SmtpSettingsResponse` |
| 更新 SMTP 配置 | PUT | `/AccessManager/Tenants/{tenant_id}/SmtpSettings` | 局部更新，仅传入需修改的字段 | `SmtpSettingsRequest` | `SmtpSettingsResponse` |

**SmtpSettingsRequest / SmtpSettingsResponse 字段说明**

| 字段 | 类型 | 说明 | 仅写 |
|---|---|---|---|
| `host` | string \| null | SMTP 服务器地址 | — |
| `port` | int \| null | 端口（25 / 465 / 587） | — |
| `from_address` | string \| null | 发件人邮箱地址（From:） | — |
| `from_display_name` | string \| null | 发件人显示名称 | — |
| `reply_to` | string \| null | Reply-To 邮箱地址 | — |
| `reply_to_display_name` | string \| null | Reply-To 显示名称 | — |
| `envelope_from` | string \| null | 信封发件人（MAIL FROM），留空则与 `from_address` 一致 | — |
| `ssl` | bool \| null | 使用隐式 SSL/TLS（通常配合端口 465） | — |
| `starttls` | bool \| null | 使用 STARTTLS 升级（通常配合端口 587） | — |
| `auth` | bool \| null | 启用 SMTP 认证 | — |
| `user` | string \| null | SMTP 用户名 | — |
| `password` | string \| null | SMTP 密码 | ✓（GET 不返回） |

**示例：PUT 写入 SMTP 配置**

```json
{
  "host": "smtp.example.com",
  "port": 587,
  "from_address": "no-reply@example.com",
  "from_display_name": "IAM System",
  "starttls": true,
  "ssl": false,
  "auth": true,
  "user": "smtp_user",
  "password": "smtp_pass_123"
}
```

**示例：GET 响应（密码不返回）**

```json
{
  "host": "smtp.example.com",
  "port": 587,
  "from_address": "no-reply@example.com",
  "from_display_name": "IAM System",
  "reply_to": null,
  "reply_to_display_name": null,
  "envelope_from": null,
  "ssl": false,
  "starttls": true,
  "auth": true,
  "user": "smtp_user"
}
```

### 邮件功能开关

| 接口名称 | Method | 路径 | 说明 | 请求体/参数 | 响应 |
|---|---|---|---|---|---|
| 查询邮件功能开关 | GET | `/AccessManager/Tenants/{tenant_id}/EmailSettings` | 获取当前邮件功能开关状态 | — | `EmailSettingsResponse` |
| 更新邮件功能开关 | PUT | `/AccessManager/Tenants/{tenant_id}/EmailSettings` | 局部更新，仅传入需修改的字段 | `EmailSettingsRequest` | `EmailSettingsResponse` |

**EmailSettingsRequest 字段说明**

| 字段 | 类型 | 说明 |
|---|---|---|
| `reset_password_allowed` | bool \| null | 允许用户通过邮件重置密码（"忘记密码"入口），依赖 SMTP 配置 |
| `verify_email` | bool \| null | 新用户首次登录前须点击邮件验证链接，依赖 SMTP 配置 |

**EmailSettingsResponse 字段说明**

| 字段 | 类型 | 说明 |
|---|---|---|
| `reset_password_allowed` | bool \| null | 同上 |
| `verify_email` | bool \| null | 同上 |
| `login_with_email_allowed` | bool | 始终为 `false`，邮箱不作为登录凭据，前端展示时置灰 |

**示例：GET / PUT 响应**

```json
{
  "reset_password_allowed": true,
  "verify_email": false,
  "login_with_email_allowed": false
}
```

> **前端建议**：
> - 两个接口建议合并在同一页面，先保存 SMTP 配置，再开启功能开关，避免开关已开但邮件发不出去的情况。
> - `login_with_email_allowed` 字段只读，前端渲染时直接置灰并标注"不支持邮箱登录"。
> - `verify_email` 开启后，Keycloak 在用户首次登录时自动拦截并发送验证邮件，无需前端额外处理。

---

**BatchOperationResponse 示例**

```json
{
  "succeeded": 8,
  "failed": 2,
  "errors": [
    { "index": 2, "username": "alice", "error": "User exists with same username" },
    { "index": 5, "username": "bob",   "error": "username and password are required" }
  ]
}
```

`index` 对应请求数组中的位置（0-based），前端可据此高亮 CSV 中对应行。`succeeded + failed` 等于提交总数。

> **超过 100 条时分批调用**：单次请求上限为 100 条，总量不限。需要导入更多用户时，前端将列表按 100 条切片，顺序调用多次 `BatchCreate`，汇总各批的 `succeeded`/`failed` 计数即可。每批独立处理，某批中的失败不影响其他批次。

---

## 用户组管理

| 接口名称 | Method | 路径 | 说明 | 请求体/参数 | 响应 |
|---|---|---|---|---|---|
| 用户组列表 | GET | `/AccessManager/Tenants/{tenant_id}/Groups` | 支持搜索/分页，含 member_count | `?search=&first=0&max=50` | `GroupListPageResponse` |
| 创建用户组 | PUT | `/AccessManager/Tenants/{tenant_id}/Groups` | 创建组，可选绑用户，可传描述 | `{"name","description","users":[uid]}` | `GroupResponse` (201) |
| 用户组详情 | GET | `/AccessManager/Tenants/{tenant_id}/Groups/{group_id}` | 成员列表（分页，含邮箱、创建时间）+ 权限列表 | `?first=0&max=20` | `GroupDetailResponse` |
| 修改用户组 | PATCH | `/AccessManager/Tenants/{tenant_id}/Groups/{group_id}` | 修改名称/描述 + 全量同步成员 | `{"name","description","users":[uid]}` | 204 |
| 删除用户组 | DELETE | `/AccessManager/Tenants/{tenant_id}/Groups/{group_id}` | 删除自定义组，预置组返回 400 | — | 204 |
| 批量添加成员 | POST | `/AccessManager/Tenants/{tenant_id}/Groups/{group_id}/Members/BatchAdd` | 批量将用户添加到组 | `{"user_ids":["uid1"]}` | `BatchOperationResponse` |
| 批量移除成员 | POST | `/AccessManager/Tenants/{tenant_id}/Groups/{group_id}/Members/BatchRemove` | 批量从组中移除用户 | `{"user_ids":["uid1"]}` | `BatchOperationResponse` |

**GroupListPageResponse 示例**

```json
{
  "groups": [
    {
      "id": "g1",
      "name": "dev-team",
      "description": "开发团队",
      "source": "custom",
      "member_count": 5
    }
  ],
  "total": 12
}
```

> 注意：Keycloak 不提供用户组的创建时间，该字段暂不支持。

**GroupDetailResponse 示例**

```json
{
  "id": "g1",
  "name": "dev-team",
  "source": "custom",
  "member_total": 42,
  "members": [
    {
      "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "username": "alice",
      "email": "alice@example.com",
      "enabled": true,
      "account_type": "internal",
      "nickname": "Alice",
      "groups": [{"id": "g1", "name": "dev-team"}],
      "created_at": "2026-01-01T00:00:00Z"
    }
  ],
  "permissions": [
    {
      "app_name": "KnowledgeBase",
      "app_display_name": "Knowledge Base",
      "path_prefix": "/KnowledgeBase/",
      "method": null,
      "required_group": "dev-team"
    }
  ]
}
```

`members` 字段与用户列表接口（`GET /Users`）返回的 `UserListResponse` 结构完全一致，包含 `email`、`nickname`、`created_at`、`groups` 等完整字段。`member_total` 为组内成员总数，`members` 为当页数据，通过 `?first=0&max=20` 分页控制。

---

## 应用管理

应用通过 Manifest 注册，`apps` 表仅保存 `enabled` 状态供 OPA app_disabled 检查。

| 接口名称 | Method | 路径 | 说明 | 请求体/参数 | 响应 |
|---|---|---|---|---|---|
| 应用列表 | GET | `/AccessManager/Tenants/System/Apps` | 所有注册的应用（enabled 状态） | — | `List[AppResponse]` |
| 应用详情 | GET | `/AccessManager/Tenants/System/Apps/{app_name}` | 单个应用详情 | — | `AppResponse` |
| 修改应用 | PUT | `/AccessManager/Tenants/System/Apps/{app_name}` | 修改 enabled 状态（License 开关） | `{"enabled":bool}` | `AppResponse` |

**AppResponse 示例**

```json
{
  "app_name": "KnowledgeBase",
  "path_prefix": "/KnowledgeBase/",
  "display_name": "Knowledge Base",
  "enabled": true
}
```

---

## 应用 Manifest 管理

Manifest 是应用接入 IAM 的注册表，定义资源类型、路径模式、默认 ACL。PUT 时自动：
1. 写入 `app_manifests` 表
2. 同步 `resource_patterns` 表（供 resource-sync 识别资源 ID）
3. 展开 `default_acl` 模板写入所有租户的 `resource_acl`

| 接口名称 | Method | 路径 | 说明 | 请求体/参数 | 响应 |
|---|---|---|---|---|---|
| 注册/更新 Manifest | PUT | `/AccessManager/Tenants/System/AppManifests/{namespace}` | 注册或更新应用 manifest | manifest JSON（见下方格式） | `{"status","namespace","acls_synced","patterns_synced"}` |
| Manifest 列表 | GET | `/AccessManager/Tenants/System/AppManifests` | 列出所有已注册的应用 manifest | — | `{"manifests":[...],"count"}` |
| Manifest 详情 | GET | `/AccessManager/Tenants/System/AppManifests/{namespace}` | 获取单个应用 manifest | — | manifest JSON |
| 删除 Manifest | DELETE | `/AccessManager/Tenants/System/AppManifests/{namespace}` | 删除应用 manifest | — | `{"status":"deleted","namespace"}` |

**Manifest 格式**

```json
{
  "namespace": "KnowledgeBase",
  "display_name": "Knowledge Base",
  "base_url": "http://mock-kb.mock-kb.svc.cluster.local:8080",
  "resources": [
    {
      "type": "KnowledgeBases",
      "path_pattern": "/KnowledgeBase/Tenants/{tenant_id}/KnowledgeBases/{kb_id}",
      "methods": ["GET", "POST", "PUT", "DELETE"],
      "actions": [
        {
          "name": "Stop",
          "path_suffix": "/Stop",
          "http_method": "POST",
          "required_role": "AccessManager/Tenants/System/Roles/Owner"
        }
      ],
      "default_acl": [
        {
          "user_template": "AccessManager/Tenants/{tenant_id}/Groups/all-users",
          "object_template": "KnowledgeBase/Tenants/{tenant_id}/KnowledgeBases",
          "role_path": "AccessManager/Tenants/System/Roles/Contributor"
        }
      ],
      "children": [
        {
          "type": "Files",
          "path_pattern": "/KnowledgeBase/Tenants/{tenant_id}/KnowledgeBases/{kb_id}/Files/{file_id}",
          "methods": ["GET", "POST", "DELETE"],
          "actions": [],
          "default_acl": [],
          "children": []
        }
      ]
    }
  ]
}
```

**path_rules 派生规则**

bundle-server 从 manifest 的 `resources[]` 派生 OPA path_rules：
- `path_pattern` 中第一个 `/{param}` 之前的部分作为 `path_prefix`
- 每个 `method` 生成一条 path_rule，`required_groups` 从 `default_acl[].user_template` 的最后一段提取 group 名（如 `all-users`）；`default_acl` 为空时默认 `["all-users"]`
- `actions[].path_suffix` 也生成对应条目，继承同一资源的 `required_groups`
- `children[]` 递归处理

---

## 组权限管理（tenant-admin 用户组权限配置页）

tenant-admin 在用户组管理界面为某个组配置各应用资源的访问权限，需要以下三个接口配合使用：

**第一步：获取可配置的应用和资源对象列表**

| 接口名称 | Method | 路径 | 说明 |
|---|---|---|---|
| 获取应用 Object 列表 | GET | `/AccessManager/Tenants/{tenant_id}/AppObjects` | 返回 enabled=true 的应用及其顶级资源类型，object_path 已替换为实际租户 ID |

响应示例：

```json
{
  "apps": [
    {
      "namespace": "KnowledgeBase",
      "display_name": "Knowledge Base",
      "objects": [
        {
          "resource_type": "KnowledgeBases",
          "display_name": "知识库",
          "object_path": "KnowledgeBase/Tenants/t-001/KnowledgeBases",
          "methods": [
            {"method": "GET",    "display_name": "查看"},
            {"method": "PUT",    "display_name": "创建"},
            {"method": "PATCH",  "display_name": "编辑"},
            {"method": "DELETE", "display_name": "删除"}
          ],
          "actions": []
        },
        {
          "resource_type": "Conversations",
          "display_name": "会话",
          "object_path": "KnowledgeBase/Tenants/t-001/Conversations",
          "methods": [
            {"method": "GET",    "display_name": "查看"},
            {"method": "POST",   "display_name": "创建"},
            {"method": "DELETE", "display_name": "删除"}
          ],
          "actions": [
            {
              "name": "Stop",
              "display_name": "Stop",
              "http_method": "POST",
              "required_role": "AccessManager/Tenants/System/Roles/Owner"
            }
          ]
        }
      ]
    }
  ]
}
```

说明：
- 只返回 `apps.enabled=true` 的应用
- 子资源（如 Mappings、Files）不单独列出，权限通过父资源前缀匹配自动继承
- `object_path` 可直接用于 `resource_acl`，无需前端做任何转换
- `methods` 和 `actions` 供前端渲染语义勾选项（"查看知识库"、"删除知识库"等）

**前端映射规则（勾选操作 → role_path，由前端完成，后端接口不感知语义）：**

| 勾选的操作 | 映射到 role_path |
|---|---|
| 仅勾选 GET（查看） | `AccessManager/Tenants/System/Roles/Viewer` |
| 勾选 GET + PUT/PATCH/POST（含创建或编辑，不含删除） | `AccessManager/Tenants/System/Roles/Contributor` |
| 勾选 DELETE（含删除，无论是否勾选其他） | `AccessManager/Tenants/System/Roles/Owner` |
| 勾选某个 action | 使用该 action 的 `required_role` 字段值 |
| 全部取消勾选 | `role_path: null`（撤销该条 ACL） |

前端完成映射后，调用 `PUT .../Groups/{group_name}/ObjectPermissions` 写入 ACL。

**第二步：查询某个组当前对各 Object 的权限（复用现有接口）**

```
GET /AccessManager/Tenants/{tenant_id}/ACLs?user=AccessManager/Tenants/{tenant_id}/Groups/{group_name}
```

返回该组在 `resource_acl` 里的所有条目，前端对照 AppObjects 列表渲染当前勾选状态。

**第三步：保存权限配置**

| 接口名称 | Method | 路径 | 说明 |
|---|---|---|---|
| 批量设置组权限 | PUT | `/AccessManager/Tenants/{tenant_id}/Groups/{group_name}/ObjectPermissions` | 全量替换该组对指定 Object 集合的 ACL，一次原子操作 |

请求体：

```json
{
  "permissions": [
    {
      "object_path": "KnowledgeBase/Tenants/t-001/KnowledgeBases",
      "role_path": "AccessManager/Tenants/System/Roles/Viewer"
    },
    {
      "object_path": "KnowledgeBase/Tenants/t-001/Conversations",
      "role_path": "AccessManager/Tenants/System/Roles/Contributor"
    },
    {
      "object_path": "DataAgent/Tenants/t-001/DataAgentDBs",
      "role_path": null
    }
  ]
}
```

- `role_path` 非 null → upsert ACL（新增或覆盖）
- `role_path: null` → 撤销该条 ACL
- 调用方必须是 `tenant-admins` 或 `master-admins`，否则返回 403
- `user_path` 由服务端从路径参数自动构造，前端不需要传

响应：

```json
{
  "status": "ok",
  "group_path": "AccessManager/Tenants/t-001/Groups/dev-team",
  "upserted": 2,
  "deleted": 1
}
```

---

## 资源 ACL 管理

所有路径使用全路径格式（`AccessManager/Tenants/{tenant_id}/...`、`{app_namespace}/Tenants/{tenant_id}/...`）。

| 接口名称 | Method | 路径 | 说明 | 请求体/参数 | 响应 |
|---|---|---|---|---|---|
| 写入 ACL | PUT | `/AccessManager/Tenants/{tenant_id}/ACLs` | 写入 ACL 三元组，调用者须是 Owner 或管理员 | `{"user_path","object_path","role_path"}` | 200/201 |
| 查询 ACL | GET | `/AccessManager/Tenants/{tenant_id}/ACLs` | 按 object 或 user 查询 ACL 列表 | `?object=...` 或 `?user=...` | `{"acls":[...],"count"}` |
| 删除 ACL | DELETE | `/AccessManager/Tenants/{tenant_id}/ACLs` | 撤销指定 ACL 条目 | `{"user_path","object_path"}` | 200/204 |
| 批量权限检查 | POST | `/AccessManager/Tenants/{tenant_id}/Action/QueryACLs` | 批量检查 (user, object) 的当前角色，适用于已知资源 ID 的场景 | `{"queries":[{"user_path","object_path"}]}` | `[{"user_path","object_path","role_path","allowed":bool}]` |
| List 可访问资源 | POST | `/AccessManager/Tenants/{tenant_id}/Action/ListAllowedIds` | 返回用户在某资源类型下有权限的资源 ID 列表，供 app_callback 模式的 List 接口使用 | `{"user_path","type_prefix","page":1,"page_size":200}` | `{"ids":["id1","id2"],"total":2,"page":1,"page_size":200}` |

**路径格式说明**

| 字段 | 格式 | 示例 |
|---|---|---|
| `user_path` | `AccessManager/Tenants/{tenant_id}/Users/{user_id}` | `AccessManager/Tenants/aidp/Users/3fa85f64` |
| `object_path` | `{app_namespace}/Tenants/{tenant_id}/{resource_type}/{resource_id}` | `KnowledgeBase/Tenants/aidp/KnowledgeBases/kb-001` |
| `role_path` | `AccessManager/Tenants/System/Roles/{role_name}` | `AccessManager/Tenants/System/Roles/Owner` |

**可用角色**

| role_path | 权限 |
|---|---|
| `AccessManager/Tenants/System/Roles/Owner` | 读 + 写 + 删除 |
| `AccessManager/Tenants/System/Roles/Contributor` | 读 + 写 |
| `AccessManager/Tenants/System/Roles/Viewer` | 只读 |

**PUT /ACLs 请求示例**

```json
{
  "user_path": "AccessManager/Tenants/aidp/Users/3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "object_path": "KnowledgeBase/Tenants/aidp/KnowledgeBases/kb-uuid-001",
  "role_path": "AccessManager/Tenants/System/Roles/Contributor"
}
```

**POST /Action/QueryACLs 请求示例**

```json
{
  "queries": [
    {
      "user_path": "AccessManager/Tenants/aidp/Users/3fa85f64",
      "object_path": "KnowledgeBase/Tenants/aidp/KnowledgeBases/kb-uuid-001"
    }
  ]
}
```

---

## API Key 管理

| 接口名称 | Method | 路径 | 说明 | 请求体/参数 | 响应 |
|---|---|---|---|---|---|
| 创建 Key | POST | `/AccessManager/Tenants/{tenant_id}/ApiKeys` | 创建 API Key，明文只返回一次 | `{"app_name","description","subject_id","allowed_paths":[],"expires_at"}` | `ApiKeyCreateResponse` (201) |
| Key 列表 | GET | `/AccessManager/Tenants/{tenant_id}/ApiKeys` | 列出所有 Key（只显示前缀，无明文） | — | `List[ApiKeyResponse]` |
| Key 详情 | GET | `/AccessManager/Tenants/{tenant_id}/ApiKeys/{key_id}` | 单个 Key 详情 | — | `ApiKeyResponse` |
| 修改 Key | PUT | `/AccessManager/Tenants/{tenant_id}/ApiKeys/{key_id}` | 修改 Key 信息 | `{"description","enabled"}` | `ApiKeyResponse` |
| 删除 Key | DELETE | `/AccessManager/Tenants/{tenant_id}/ApiKeys/{key_id}` | 删除 Key | — | 204 |
| 轮换 Key | POST | `/AccessManager/Tenants/{tenant_id}/ApiKeys/{key_id}/Rotate` | 轮换 Key，旧 Key 立即失效 | — | `ApiKeyCreateResponse` |

**ApiKeyCreateResponse 示例**

```json
{
  "id": "key-uuid-001",
  "app_name": "KnowledgeBase",
  "description": "CI pipeline key",
  "subject_id": "user-uuid",
  "key_prefix": "ak_",
  "api_key": "ak_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "allowed_paths": ["/KnowledgeBase/"],
  "enabled": true,
  "expires_at": "2027-01-01T00:00:00Z",
  "created_at": "2026-05-07T00:00:00Z"
}
```

---

## IdP / SAML 管理

| 接口名称 | Method | 路径 | 说明 | 请求体/参数 | 响应 |
|---|---|---|---|---|---|
| 导入 SAML 元数据 | POST | `/AccessManager/Tenants/{tenant_id}/Idp/Saml/Import` | 上传 SAML 元数据 XML，自动解析 SSO URL、证书等字段 | multipart/form-data (`file`) | `SAMLMetadataImportResponse` |
| 创建 IdP 实例 | POST | `/AccessManager/Tenants/{tenant_id}/Idp/Saml/Instances` | 创建 SAML IdP 实例，每个租户只能有一个 | `IDPRequest` | `IDPInstanceResponse` (201) |
| 修改 IdP 实例 | PUT | `/AccessManager/Tenants/{tenant_id}/Idp/Saml/Instances` | 修改 SAML IdP 配置 | `IDPRequest` | `IDPInstanceResponse` |
| IdP 实例列表 | GET | `/AccessManager/Tenants/{tenant_id}/Idp/Saml/Instances` | 列出所有 IdP 实例 | — | `List[IDPInstanceResponse]` |
| 删除 IdP 实例 | DELETE | `/AccessManager/Tenants/{tenant_id}/Idp/Saml/Instances/{alias}` | 删除指定 IdP 实例 | — | 204 |
| Mapper 列表 | GET | `/AccessManager/Tenants/{tenant_id}/Idp/Saml/Instances/{alias}/Mappers` | 获取属性映射列表 | — | `List[IdPMapperResponse]` |
| 创建 Mapper | POST | `/AccessManager/Tenants/{tenant_id}/Idp/Saml/Instances/{alias}/Mappers` | 创建属性映射（SAML 属性 → Keycloak 用户属性） | `IdPMapperCreate` | `IdPMapperResponse` (201) |
| 修改 Mapper | PUT | `/AccessManager/Tenants/{tenant_id}/Idp/Saml/Instances/{alias}/Mappers/{mapper_id}` | 修改属性映射 | `IdPMapperUpdate` | 204 |
| 删除 Mapper | DELETE | `/AccessManager/Tenants/{tenant_id}/Idp/Saml/Instances/{alias}/Mappers/{mapper_id}` | 删除属性映射 | — | 204 |
| GroupMapper 列表 | GET | `/AccessManager/Tenants/{tenant_id}/Idp/Saml/Instances/{alias}/GroupMappers` | 获取条件化自动加组规则 | — | `List[IdPGroupMapperResponse]` |
| 创建 GroupMapper | POST | `/AccessManager/Tenants/{tenant_id}/Idp/Saml/Instances/{alias}/GroupMappers` | 创建条件化自动加组规则 | `IdPGroupMapperCreate` | `IdPGroupMapperResponse` (201) |
| 修改 GroupMapper | PUT | `/AccessManager/Tenants/{tenant_id}/Idp/Saml/Instances/{alias}/GroupMappers/{mapper_id}` | 修改条件化加组规则 | `IdPGroupMapperUpdate` | 204 |
| 删除 GroupMapper | DELETE | `/AccessManager/Tenants/{tenant_id}/Idp/Saml/Instances/{alias}/GroupMappers/{mapper_id}` | 删除条件化加组规则 | — | 204 |

**IDPRequest（创建/修改 IdP 实例请求体）**

```json
{
  "displayName": "企业 SSO",
  "enabled": true,
  "trustEmail": false,
  "config": {
    "singleSignOnServiceUrl": "https://idp.example.com/sso",
    "singleLogoutServiceUrl": "https://idp.example.com/slo",
    "entityId": "https://idp.example.com/entity",
    "signingCertificate": "<Base64 证书内容>",
    "validateSignature": "true",
    "postBindingAuthnRequest": "true",
    "postBindingResponse": "true",
    "postBindingLogout": "true",
    "nameIDPolicyFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified"
  }
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `displayName` | string | 登录页展示的 IdP 名称，如"企业 SSO" |
| `enabled` | bool | 是否启用该 IdP，默认 `true` |
| `trustEmail` | bool | 是否信任 IdP 传来的邮箱（跳过邮箱验证），默认 `false`，生产环境建议保持 `false` |
| `config.singleSignOnServiceUrl` | string | **必填**。IdP 的 SSO 登录地址 |
| `config.singleLogoutServiceUrl` | string | IdP 的单点登出地址（可选） |
| `config.entityId` | string | IdP 的 Entity ID |
| `config.signingCertificate` | string | IdP 的签名证书（Base64，不含 PEM 头尾） |
| `config.validateSignature` | string | 是否验证 SAML 断言签名，`"true"` / `"false"` |
| `config.postBindingAuthnRequest` | string | 使用 POST Binding 发送认证请求，`"true"` / `"false"` |
| `config.postBindingResponse` | string | 使用 POST Binding 接收响应，`"true"` / `"false"` |
| `config.postBindingLogout` | string | 使用 POST Binding 发送登出请求，`"true"` / `"false"` |
| `config.nameIDPolicyFormat` | string | NameID 格式，通常为 `unspecified` 或 `emailAddress` |

> `alias` 字段传了也无效，服务端固定使用环境变量 `DEFAULT_IDP_ALIAS`（默认 `da-saml-idp`）。通常通过先调用 `POST /Idp/Saml/Import` 上传 XML 元数据，再把解析结果填入 `config` 创建实例。

**IDPInstanceResponse（响应）**

```json
{
  "alias": "da-saml-idp",
  "displayName": "企业 SSO",
  "providerId": "saml",
  "enabled": true,
  "trustEmail": false,
  "firstBrokerLoginFlowAlias": "first broker login",
  "config": {
    "singleSignOnServiceUrl": "https://idp.example.com/sso",
    "singleLogoutServiceUrl": "https://idp.example.com/slo",
    "entityId": "https://idp.example.com/entity",
    "signingCertificate": true,
    "validateSignature": "true",
    "postBindingAuthnRequest": "true",
    "postBindingResponse": "true",
    "postBindingLogout": "true",
    "nameIDPolicyFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified"
  }
}
```

> `config.signingCertificate` 返回 `bool`，`true` 表示已配置证书，内容不返回。

**SAMLMetadataImportResponse（导入元数据响应）**

```json
{
  "singleSignOnServiceUrl": "https://idp.example.com/sso",
  "singleLogoutServiceUrl": "https://idp.example.com/slo",
  "entityId": "https://idp.example.com/entity",
  "signingCertificate": "<Base64>",
  "validateSignature": "true",
  "postBindingAuthnRequest": "true",
  "postBindingResponse": "true",
  "nameIDPolicyFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified"
}
```

> 导入元数据后，将响应字段直接填入 `IDPRequest.config` 即可创建实例。

**IdPMapperCreate / IdPMapperResponse**

```json
{
  "name": "email-mapper",
  "attributeKey": "email",
  "attributeValue": "email",
  "friendlyName": "邮箱"
}
```

| 字段 | 说明 |
|---|---|
| `attributeKey` | SAML 断言中的属性名（Remote Attribute） |
| `attributeValue` | 映射到 Keycloak 的用户属性名（Local Attribute） |
| `friendlyName` | 可选，友好名称 |

**IdPGroupMapperCreate / IdPGroupMapperResponse**

```json
{
  "name": "rd-group-mapper",
  "conditions": [
    {"attribute": "Department", "value": "RD-Infra", "matchType": "exact"},
    {"attribute": "Role", "value": "admin.*", "matchType": "regex"},
    {"attribute": "Team", "value": "platform", "matchType": "contains"}
  ],
  "group": "/rd-admins"
}
```

| 字段 | 说明 |
|---|---|
| `conditions` | 属性条件列表，多个条件为 AND 语义，全部命中才触发加组 |
| `conditions[].attribute` | SAML 断言中的属性名 |
| `conditions[].value` | 期望的属性值 |
| `conditions[].matchType` | 匹配方式：`exact`（全匹配，默认）/ `contains`（包含）/ `regex`（正则） |
| `group` | Keycloak 组路径，必须以 `/` 开头 |

---

## Token

| 接口名称 | Method | 路径 | 说明 | 请求体/参数 | 响应 |
|---|---|---|---|---|---|
| 授权码换 Token | POST | `/AccessManager/Tenants/{tenant_id}/Token/Exchange` | OIDC 授权码换取 access_token | `{"code","redirect_uri","client_id","client_secret"}` | `TokenExchangeResponse` |

---

## 公共接口

| 接口名称 | Method | 路径 | 说明 | 响应 |
|---|---|---|---|---|
| 健康检查 | GET | `/AccessManager/Tenants/Common/Health` | 服务健康状态 | `{"status":"ok"}` |
| 租户列表 | GET | `/AccessManager/Tenants` | 列出租户（单租户模式） | `List[TenantResponse]` |

---

## KnowledgeBase 应用接口

KnowledgeBase 应用遵循统一 URL 格式，路径鉴权由 OPA 从 manifest 派生，资源级鉴权由 resource-sync 通过 `resource_acl` 控制。

| 接口名称 | Method | 路径 | 说明 |
|---|---|---|---|
| 知识库列表 | GET | `/KnowledgeBase/Tenants/{tenant_id}/KnowledgeBases` | 支持分页，X-Allowed-Ids 过滤 |
| 创建知识库 | POST | `/KnowledgeBase/Tenants/{tenant_id}/KnowledgeBases` | 创建后 ext_proc 自动写 Owner ACL |
| 知识库详情 | GET | `/KnowledgeBase/Tenants/{tenant_id}/KnowledgeBases/{kb_id}` | 需 Viewer 权限 |
| 更新知识库 | PUT | `/KnowledgeBase/Tenants/{tenant_id}/KnowledgeBases/{kb_id}` | 需 Contributor 权限 |
| 删除知识库 | DELETE | `/KnowledgeBase/Tenants/{tenant_id}/KnowledgeBases/{kb_id}` | 需 Owner 权限 |
| 目录映射列表 | GET | `/KnowledgeBase/Tenants/{tenant_id}/KnowledgeBases/{kb_id}/Mappings` | — |
| 创建目录映射 | POST | `/KnowledgeBase/Tenants/{tenant_id}/KnowledgeBases/{kb_id}/Mappings` | — |
| 删除目录映射 | DELETE | `/KnowledgeBase/Tenants/{tenant_id}/KnowledgeBases/{kb_id}/Mappings/{mapping_id}` | — |
| 文件列表 | GET | `/KnowledgeBase/Tenants/{tenant_id}/KnowledgeBases/{kb_id}/Files` | — |
| 上传文件 | POST | `/KnowledgeBase/Tenants/{tenant_id}/KnowledgeBases/{kb_id}/Files` | multipart/form-data |
| 删除文件 | DELETE | `/KnowledgeBase/Tenants/{tenant_id}/KnowledgeBases/{kb_id}/Files/{file_id}` | — |
| 会话列表 | GET | `/KnowledgeBase/Tenants/{tenant_id}/Conversations` | — |
| 创建会话（问答） | POST | `/KnowledgeBase/Tenants/{tenant_id}/Conversations` | — |
| 会话详情 | GET | `/KnowledgeBase/Tenants/{tenant_id}/Conversations/{thread_id}` | — |
| 停止会话 | POST | `/KnowledgeBase/Tenants/{tenant_id}/Conversations/{thread_id}/Stop` | — |
| 删除会话 | DELETE | `/KnowledgeBase/Tenants/{tenant_id}/Conversations/{thread_id}` | — |
| 模型配置列表 | GET | `/KnowledgeBase/Tenants/System/ModelConfigs` | 系统级，需 master-admins |
| 创建模型配置 | POST | `/KnowledgeBase/Tenants/System/ModelConfigs` | — |
| 更新模型配置 | PUT | `/KnowledgeBase/Tenants/System/ModelConfigs/{model_id}` | — |
| 删除模型配置 | DELETE | `/KnowledgeBase/Tenants/System/ModelConfigs/{model_id}` | — |
| 提示词列表 | GET | `/KnowledgeBase/Tenants/System/Prompts` | 系统级 |
| 创建提示词 | POST | `/KnowledgeBase/Tenants/System/Prompts` | — |
| 提示词详情 | GET | `/KnowledgeBase/Tenants/System/Prompts/{prompt_id}` | — |
| 更新提示词 | PUT | `/KnowledgeBase/Tenants/System/Prompts/{prompt_id}` | — |
| 删除提示词 | DELETE | `/KnowledgeBase/Tenants/System/Prompts/{prompt_id}` | — |
| 术语库列表 | GET | `/KnowledgeBase/Tenants/{tenant_id}/JargonLibraries` | — |
| 创建术语库 | POST | `/KnowledgeBase/Tenants/{tenant_id}/JargonLibraries` | — |
| 删除术语库 | DELETE | `/KnowledgeBase/Tenants/{tenant_id}/JargonLibraries/{lib_name}` | — |
| 术语列表 | GET | `/KnowledgeBase/Tenants/{tenant_id}/JargonLibraries/{lib_name}/Jargons` | — |
| 创建术语 | POST | `/KnowledgeBase/Tenants/{tenant_id}/JargonLibraries/{lib_name}/Jargons` | — |
| 更新术语 | PUT | `/KnowledgeBase/Tenants/{tenant_id}/JargonLibraries/{lib_name}/Jargons/{jargon_name}` | — |
| 删除术语 | DELETE | `/KnowledgeBase/Tenants/{tenant_id}/JargonLibraries/{lib_name}/Jargons/{jargon_name}` | — |
| 融合检索 | POST | `/KnowledgeBase/Tenants/{tenant_id}/Action/FusionSearch` | — |

---

## DataAgent 应用接口

DataAgent 通过 manifest 注册，路径鉴权由 OPA 从 manifest 派生。

| 接口名称 | Method | 路径 | 说明 |
|---|---|---|---|
| 数据库列表 | GET | `/DataAgent/Tenants/{tenant_id}/DataBases` | X-Allowed-Ids 过滤 |
| 创建数据库 | POST | `/DataAgent/Tenants/{tenant_id}/DataBases` | ext_proc 自动写 Owner ACL |
| 数据库详情 | GET | `/DataAgent/Tenants/{tenant_id}/DataBases/{database_id}` | 需 Viewer 权限 |
| 更新数据库 | PUT | `/DataAgent/Tenants/{tenant_id}/DataBases/{database_id}` | 需 Contributor 权限 |
| 删除数据库 | DELETE | `/DataAgent/Tenants/{tenant_id}/DataBases/{database_id}` | 需 Owner 权限 |
| 知识库列表 | GET | `/DataAgent/Tenants/{tenant_id}/DataAgentDBs` | — |
| 创建知识库 | POST | `/DataAgent/Tenants/{tenant_id}/DataAgentDBs` | — |
| 知识库详情 | GET | `/DataAgent/Tenants/{tenant_id}/DataAgentDBs/{db_id}` | — |
| 更新知识库 | PUT | `/DataAgent/Tenants/{tenant_id}/DataAgentDBs/{db_id}` | — |
| 删除知识库 | DELETE | `/DataAgent/Tenants/{tenant_id}/DataAgentDBs/{db_id}` | — |
| 查询（NL2SQL） | POST | `/DataAgent/Tenants/{tenant_id}/DataAgentDBs/{db_id}/Query` | 需 Contributor 权限 |
| 数据表列表 | GET | `/DataAgent/Tenants/{tenant_id}/DataAgentDBs/{db_id}/Tables` | — |
| 会话列表 | GET | `/DataAgent/Tenants/{tenant_id}/DataAgentSessions` | — |
| 创建会话 | POST | `/DataAgent/Tenants/{tenant_id}/DataAgentSessions` | — |
| 会话详情 | GET | `/DataAgent/Tenants/{tenant_id}/DataAgentSessions/{session_id}` | — |
| 删除会话 | DELETE | `/DataAgent/Tenants/{tenant_id}/DataAgentSessions/{session_id}` | — |
| 对话 | POST | `/DataAgent/Tenants/{tenant_id}/DataAgentSessions/{session_id}/Chat` | 需 Contributor 权限 |

---

## 附录：路径格式规范

### 统一 URL 格式

所有应用资源路径遵循：

```
/{app_namespace}/Tenants/{tenant_id}/{resource_type}/{resource_id}
/{app_namespace}/Tenants/{tenant_id}/{resource_type}/{resource_id}/{child_type}/{child_id}
/{app_namespace}/Tenants/{tenant_id}/Action/{action_name}   ← 非 CRUD 操作
/{app_namespace}/Tenants/System/{resource_type}/{resource_id}  ← 系统级资源
```

**命名规范：**
- `AppNamespace`：PascalCase，例如 `KnowledgeBase`、`DataAgent`、`MemoryStore`
- `ResourceType`：PascalCase 复数，例如 `KnowledgeBases`、`DataAgentDBs`
- `resourceId`：原始 ID，不做转换
- `Action`：PascalCase，例如 `FusionSearch`、`QueryACLs`、`BatchDelete`

### IAM 管理路径

```
/AccessManager/Tenants/{tenant_id}/{resource_type}/{resource_id}
/AccessManager/Tenants/System/{resource_type}/{resource_id}  ← 系统级（跨租户）
/AccessManager/Tenants/Common/{resource_type}               ← 公共接口（health 等）
```

### ACL 路径格式

```
user_path:   AccessManager/Tenants/{tenant_id}/Users/{user_id}
object_path: {app_namespace}/Tenants/{tenant_id}/{resource_type}/{resource_id}
role_path:   AccessManager/Tenants/System/Roles/{Owner|Contributor|Viewer}
```

---

## MemoryStore 应用接口

> **应用标识**：`MemoryStore`（统一记忆管理）
> **URL 前缀**：`/MemoryStore/`
> **权限模型**：资源级三角色（Owner / Contributor / Viewer），继承自父资源 Instances。`all-users` 组默认对 Instances 有 Contributor 权限，`tenant-admins` 有 Owner 权限。

### 接口总览

| 接口名称 | Method | 路径 | 最低权限 | 说明 |
|---|---|---|---|---|
| 实例列表 | GET | `/MemoryStore/Tenants/{tenant_id}/Instances` | Viewer | 返回当前用户有权限的实例列表（网关注入 `X-Allowed-Ids` 过滤） |
| 获取实例 | GET | `/MemoryStore/Tenants/{tenant_id}/Instances/{instanceName}` | Viewer | 获取单个实例详情 |
| 创建/更新实例 | PUT | `/MemoryStore/Tenants/{tenant_id}/Instances/{instanceName}` | Contributor | 不存在则创建（201），已存在则全量更新（200） |
| 删除实例 | DELETE | `/MemoryStore/Tenants/{tenant_id}/Instances/{instanceName}` | Owner | 级联删除该实例下所有 Memories 和 Templates |
| 记忆列表 | GET | `/MemoryStore/Tenants/{tenant_id}/Instances/{instanceName}/Memories` | Viewer | 列出实例下所有记忆 |
| 获取记忆 | GET | `/MemoryStore/Tenants/{tenant_id}/Instances/{instanceName}/Memories/{memoryId}` | Viewer | 获取单条记忆详情 |
| 创建/更新记忆 | PUT | `/MemoryStore/Tenants/{tenant_id}/Instances/{instanceName}/Memories/{memoryId}` | Contributor | 不存在则创建（201），已存在则全量更新（200） |
| 删除记忆 | DELETE | `/MemoryStore/Tenants/{tenant_id}/Instances/{instanceName}/Memories/{memoryId}` | Owner | 删除单条记忆 |
| 查询记忆 | POST | `/MemoryStore/Tenants/{tenant_id}/Instances/{instanceName}/Memories/Query` | Viewer | 按文本检索相关记忆 |
| 规则列表 | GET | `/MemoryStore/Tenants/{tenant_id}/Instances/{instanceName}/Templates` | Viewer | 列出实例下所有记忆规则 |
| 获取规则 | GET | `/MemoryStore/Tenants/{tenant_id}/Instances/{instanceName}/Templates/{templateName}` | Viewer | 获取单条规则详情 |
| 创建/更新规则 | PUT | `/MemoryStore/Tenants/{tenant_id}/Instances/{instanceName}/Templates/{templateName}` | Contributor | 不存在则创建（201），已存在则全量更新（200） |
| 部分更新规则 | PATCH | `/MemoryStore/Tenants/{tenant_id}/Instances/{instanceName}/Templates/{templateName}` | Contributor | 只更新传入的字段，其余字段保持不变 |
| 删除规则 | DELETE | `/MemoryStore/Tenants/{tenant_id}/Instances/{instanceName}/Templates/{templateName}` | Owner | 删除单条规则 |
| 设置规则过滤器 | POST | `/MemoryStore/Tenants/{tenant_id}/Instances/{instanceName}/Templates/{templateName}/Filters` | Contributor | 设置 Agent 黑白名单过滤器 |
| 设置 LLM 提取 | POST | `/MemoryStore/Tenants/{tenant_id}/Instances/{instanceName}/Templates/{templateName}/LLMExtraction` | Contributor | 设置 LLM 提取开关及参数 |
| 系统默认规则 | GET | `/MemoryStore/Templates/Defaults/{templateName}` | 无（公开） | 获取系统预置规则模板，所有租户可读，无权限控制 |

---

### 实例（Instances）

**PUT /MemoryStore/Tenants/{tenant_id}/Instances/{instanceName} 请求体**

```json
{
  "description": "用于客服场景的记忆实例",
  "config": {
    "max_memories": 1000,
    "ttl_days": 30
  }
}
```

**响应（201 创建 / 200 更新）**

```json
{
  "tenant_id": "t-001",
  "name": "customer-service",
  "description": "用于客服场景的记忆实例",
  "config": {
    "max_memories": 1000,
    "ttl_days": 30
  }
}
```

**GET /MemoryStore/Tenants/{tenant_id}/Instances 响应**

```json
{
  "items": [
    {
      "tenant_id": "t-001",
      "name": "customer-service",
      "description": "用于客服场景的记忆实例"
    }
  ],
  "total": 1
}
```

> `items` 已由网关根据当前用户权限过滤（`X-Allowed-Ids`），前端无需二次过滤。

---

### 记忆（Memories）

**PUT /MemoryStore/Tenants/{tenant_id}/Instances/{instanceName}/Memories/{memoryId} 请求体**

```json
{
  "content": "用户偏好：不喜欢推荐价格超过 500 元的商品",
  "tags": ["preference", "price"],
  "source": "conversation",
  "created_at": "2026-05-15T10:00:00Z"
}
```

**响应（201 创建 / 200 更新）**

```json
{
  "tenant_id": "t-001",
  "instance_name": "customer-service",
  "id": "mem-abc123",
  "content": "用户偏好：不喜欢推荐价格超过 500 元的商品",
  "tags": ["preference", "price"],
  "source": "conversation",
  "created_at": "2026-05-15T10:00:00Z"
}
```

**POST /Memories/Query 请求体与响应**

```json
// 请求体
{
  "query": "用户价格偏好"
}

// 响应
{
  "query": "用户价格偏好",
  "results": [
    {
      "id": "mem-abc123",
      "content": "用户偏好：不喜欢推荐价格超过 500 元的商品",
      "tags": ["preference", "price"]
    }
  ],
  "total": 1
}
```

---

### 记忆规则（Templates）

**PUT /Templates/{templateName} 请求体**

```json
{
  "description": "过滤非客服 Agent 的记忆写入",
  "enabled": true,
  "filters": {
    "allow_agents": ["agent-cs-001", "agent-cs-002"],
    "deny_agents": []
  },
  "llm_extraction": {
    "enabled": false,
    "model": "gpt-4o-mini"
  }
}
```

**PATCH /Templates/{templateName} 请求体**（只传需要修改的字段）

```json
{
  "enabled": false
}
```

**POST /Templates/{templateName}/Filters 请求体**

```json
{
  "allow_agents": ["agent-cs-001"],
  "deny_agents": ["agent-test-999"]
}
```

**响应**

```json
{
  "status": "ok",
  "filters": {
    "allow_agents": ["agent-cs-001"],
    "deny_agents": ["agent-test-999"]
  }
}
```

**POST /Templates/{templateName}/LLMExtraction 请求体**

```json
{
  "enabled": true,
  "model": "gpt-4o-mini",
  "prompt_template": "从以下对话中提取用户偏好信息：{conversation}"
}
```

**响应**

```json
{
  "status": "ok",
  "llm_extraction": {
    "enabled": true,
    "model": "gpt-4o-mini",
    "prompt_template": "从以下对话中提取用户偏好信息：{conversation}"
  }
}
```

---

### 权限说明

| 操作 | 所需角色 | 备注 |
|---|---|---|
| 读取实例/记忆/规则（GET） | Viewer | 包括列表和详情 |
| 查询记忆（POST /Query） | Viewer | 文本检索 |
| 创建/更新实例、记忆、规则（PUT/PATCH） | Contributor | `all-users` 组默认具备 |
| 设置过滤器/LLM 提取（POST /Filters、/LLMExtraction） | Contributor | |
| 删除实例/记忆/规则（DELETE） | Owner | `tenant-admins` 组默认具备 |

> 权限继承：Memories 和 Templates 的权限从父资源 Instances 继承。用户对某个 Instance 有 Contributor 权限，则对该 Instance 下的所有 Memories 和 Templates 也有 Contributor 权限。

> `tenant-admins` 组绕过资源级鉴权，对租户内所有资源拥有完整权限。
