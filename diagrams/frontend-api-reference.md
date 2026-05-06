# IAM 系统接口文档（前端对接版）

**版本**: v2.1 | **日期**: 2026-05-06

本文档基于当前代码实现，列出前端对接所需的全部接口。列名与 api-spec-v2.xlsx 保持一致。所有接口同时支持新路径 `/AccessManager/Tenants/{tid}/...` 和旧路径 `/api/v1/...` 别名（见附录兼容对照表）。`{realm}` / `{tid}` 为租户 ID，例如 `aidp`。

---

## 用户管理

| 接口名称 | Method | 路径（新格式） | 调用方 | 说明 | 请求体/参数 | 响应 | 备注 |
|---|---|---|---|---|---|---|---|
| 用户列表 | GET | `/AccessManager/Tenants/{tid}/Users` | 前端 | 支持搜索/分页/按组过滤，返回 account_type + groups | `?search=&group_id=&first=0&max=50` | `List[UserListResponse]` | account_type: internal/federated |
| 用户详情 | GET | `/AccessManager/Tenants/{tid}/Users/{user_id}/details` | 前端 | 用户信息+所属组+权限（通过所在组绑定的 permission_groups 聚合展开） | — | `UserDetailResponse` | permissions 按应用分组展示 |
| 创建用户 | POST | `/AccessManager/Tenants/{tid}/Users` | 前端 | 创建内部用户，可选绑组+是否临时密码 | `{"username","password","groups":[gid],"temporary_password":true}` | `UserListResponse` (201) | temporary_password=false 用于服务/测试账号 |
| 修改用户 | PUT | `/AccessManager/Tenants/{tid}/Users/{user_id}` | 前端 | 修改启用状态 | `{"enabled":bool}` | `UserListResponse` | — |
| 删除用户 | DELETE | `/AccessManager/Tenants/{tid}/Users/{user_id}` | 前端 | 删除单个用户 | — | 204 | 联邦用户删后 IdP 再登会重建 |
| 批量删除 | POST | `/AccessManager/Tenants/{tid}/Users/batch-delete` | 前端 | 批量删除，返回成功/失败统计 | `{"user_ids":["uuid1","uuid2"]}` | `BatchOperationResponse` | — |
| 重置密码 | PUT | `/AccessManager/Tenants/{tid}/Users/{user_id}/password` | 前端 | 重置密码(temporary=true)，联邦用户返回 400 | `{"password":"..."}` | 204 | 联邦用户无法重置 |
| 添加用户到组 | PUT | `/AccessManager/Tenants/{tid}/Users/{user_id}/groups/{group_id}` | 前端 | 将用户加入指定组 | — | 204 | — |
| 移除用户出组 | DELETE | `/AccessManager/Tenants/{tid}/Users/{user_id}/groups/{group_id}` | 前端 | 将用户从组中移除 | — | 204 | — |
| 用户可选组 | GET | `/AccessManager/Tenants/{tid}/Users/{user_id}/available-groups` | 前端 | 所有组+joined 标记，供前端勾选 | — | `[{"id","name","joined":bool,"source"}]` | — |
| CSV 导入模板 | GET | `/AccessManager/Tenants/{tid}/Users/import-template` | 前端 | 下载 CSV 模板 | — | text/csv attachment | 列：username,password,groups |
| 批量导入 | POST | `/AccessManager/Tenants/{tid}/Users/batch-import` | 前端 | 上传 CSV 批量创建用户 | multipart/form-data file | `BatchOperationResponse` | UTF-8/BOM 均支持 |

**UserListResponse 示例**

```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "username": "alice",
  "email": "alice@example.com",
  "enabled": true,
  "account_type": "internal",
  "groups": [
    {"id": "g1", "name": "all-users", "source": "preset"}
  ],
  "created_at": "2026-01-01T00:00:00Z"
}
```

**UserDetailResponse 示例**

```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "username": "alice",
  "email": "alice@example.com",
  "enabled": true,
  "account_type": "internal",
  "groups": [
    {"id": "g1", "name": "all-users", "source": "preset"}
  ],
  "permissions": [
    {
      "app_name": "kb",
      "app_display_name": "知识库",
      "permission_groups": [
        {"id": 1, "name": "kb-viewer", "description": "只读访问知识库"}
      ]
    }
  ],
  "created_at": "2026-01-01T00:00:00Z"
}
```

---

## 用户组管理

| 接口名称 | Method | 路径（新格式） | 调用方 | 说明 | 请求体/参数 | 响应 | 备注 |
|---|---|---|---|---|---|---|---|
| 用户组列表 | GET | `/AccessManager/Tenants/{tid}/Groups` | 前端 | 支持搜索/分页，含 source/member_count | `?search=&first=0&max=50` | `List[GroupListResponse]` | source: preset/app-preset/custom |
| 创建用户组 | POST | `/AccessManager/Tenants/{tid}/Groups` | 前端 | 创建组，可选绑用户 | `{"name","users":[uid]}` | `GroupResponse` (201) | — |
| 用户组详情 | GET | `/AccessManager/Tenants/{tid}/Groups/{group_id}` | 前端 | 成员+权限（通过绑定的 permission_groups 展开） | — | `GroupDetailResponse` | permissions 按应用分组展示 |
| 修改用户组 | PUT | `/AccessManager/Tenants/{tid}/Groups/{group_id}` | 前端 | 修改名称+全量同步成员 | `{"name","users":[uid]}` | 204 | users 全量覆盖 |
| 删除用户组 | DELETE | `/AccessManager/Tenants/{tid}/Groups/{group_id}` | 前端 | 删除自定义组，预置组返回 400 | — | 204 | preset 组不可删 |
| 批量添加成员 | POST | `/AccessManager/Tenants/{tid}/Groups/{group_id}/members/batch-add` | 前端 | 批量将用户添加到组 | `{"user_ids":["uid1","uid2"]}` | `BatchOperationResponse` | — |
| 批量移除成员 | POST | `/AccessManager/Tenants/{tid}/Groups/{group_id}/members/batch-remove` | 前端 | 批量从组中移除用户 | `{"user_ids":["uid1"]}` | `BatchOperationResponse` | — |
| 设置组权限 | PUT | `/AccessManager/Tenants/{tid}/Groups/{group_id}/permissions` | 前端 | 全量替换组绑定的 permission_groups | `{"permission_group_ids":[1,3,5]}` | `{"group_id","group_name","permission_groups":[...]}` | 写入 permission_group_bindings 表 |

---

## 权限管理

| 接口名称 | Method | 路径（新格式） | 调用方 | 说明 | 请求体/参数 | 响应 | 备注 |
|---|---|---|---|---|---|---|---|
| 权限列表（按应用分组） | GET | `/AccessManager/Tenants/{tid}/permissions` | 前端 | 所有 permission_groups 按 app 聚合，含路径和绑定的 Keycloak 组 | — | `[{"app_name","app_display_name","permission_groups":[...]}]` | 供前端权限矩阵/勾选使用 |

**响应示例**

```json
[
  {
    "app_name": "kb",
    "app_display_name": "知识库",
    "permission_groups": [
      {
        "id": 1,
        "name": "kb-viewer",
        "description": "只读访问知识库",
        "paths": [
          {"id": 10, "path_prefix": "/kb/v1/", "method": "GET"}
        ],
        "bindings": ["all-users"]
      }
    ]
  }
]
```

---

## 权限点 CRUD（permission_groups）

| 接口名称 | Method | 路径（新格式） | 调用方 | 说明 | 请求体/参数 | 响应 | 备注 |
|---|---|---|---|---|---|---|---|
| 创建权限点 | POST | `/AccessManager/Tenants/{tid}/PermissionGroups` | 内部（管理员） | 创建 permission_group+嵌套 paths+bindings | `{"app_name","name","description","paths":[{"path_prefix","method"}],"bindings":["all-users"]}` | `PermissionGroupResponse` (201) | app_name='' 表示平台级；(app_name,name) 冲突→409 |
| 权限点列表 | GET | `/AccessManager/Tenants/{tid}/PermissionGroups` | 前端/内部 | 列出所有 permission_group（可按 app_name 过滤） | `?app_name=` | `List[PermissionGroupResponse]` | 扁平列表；UI 聚合视图见 GET /permissions |
| 权限点详情 | GET | `/AccessManager/Tenants/{tid}/PermissionGroups/{group_id}` | 前端/内部 | 单个 permission_group 详情+paths+bindings | — | `PermissionGroupResponse` | — |
| 修改权限点 | PUT | `/AccessManager/Tenants/{tid}/PermissionGroups/{group_id}` | 内部（管理员） | 修改 name/description；paths/bindings 置空数组=清空，null=保持 | `{"name","description","paths":[],"bindings":[]}` | `PermissionGroupResponse` | paths/bindings 是 replace-all |
| 删除权限点 | DELETE | `/AccessManager/Tenants/{tid}/PermissionGroups/{group_id}` | 内部（管理员） | 删除 permission_group（级联删 paths+bindings） | — | 204 | — |
| 新增路径 | POST | `/AccessManager/Tenants/{tid}/PermissionGroups/{group_id}/paths` | 内部（管理员） | 单条新增 path_prefix/method | `{"path_prefix","method"}` | `PermissionGroupPathResponse` (201) | — |
| 删除路径 | DELETE | `/AccessManager/Tenants/{tid}/PermissionGroups/{group_id}/paths/{path_id}` | 内部（管理员） | 按 path id 单条删除 | — | 204 | — |
| 新增绑定 | POST | `/AccessManager/Tenants/{tid}/PermissionGroups/{group_id}/bindings/{kc_group_name}` | 内部（管理员） | 把该权限点开给指定 Keycloak 组（幂等） | — | `{"group_id","kc_group_name"}` | — |
| 删除绑定 | DELETE | `/AccessManager/Tenants/{tid}/PermissionGroups/{group_id}/bindings/{kc_group_name}` | 内部（管理员） | 回收该权限点对指定 Keycloak 组的授权 | — | 204 | — |

---

## 应用管理

| 接口名称 | Method | 路径（新格式） | 调用方 | 说明 | 请求体/参数 | 响应 | 备注 |
|---|---|---|---|---|---|---|---|
| 应用列表 | GET | `/AccessManager/Tenants/{tid}/apps` | 前端/内部 | 所有注册的应用（含 resource_patterns+actions 嵌套） | — | `List[AppResponse]` | — |
| 注册应用 | POST | `/AccessManager/Tenants/{tid}/apps` | 内部（管理员） | 注册新应用+资源模式+自动建 {app}-admins 组 | `{"app_name","path_prefix","display_name","enabled","resource_patterns":[...]}` | `AppResponse` (201) | 通常由 init 脚本预置 |
| 应用详情 | GET | `/AccessManager/Tenants/{tid}/apps/{app_name}` | 前端/内部 | 单个应用详情 | — | `AppResponse` | — |
| 修改应用 | PUT | `/AccessManager/Tenants/{tid}/apps/{app_name}` | 内部（管理员） | 修改应用信息/License 开关 | `{"display_name","enabled"}` | `AppResponse` | — |
| 删除应用 | DELETE | `/AccessManager/Tenants/{tid}/apps/{app_name}` | 内部（管理员） | 下线应用 | — | 204 | 级联删除 resource_patterns+resource_actions |

---

## 资源模式（resource_patterns）

| 接口名称 | Method | 路径（新格式） | 调用方 | 说明 | 请求体/参数 | 响应 | 备注 |
|---|---|---|---|---|---|---|---|
| 新增模式 | POST | `/AccessManager/Tenants/{tid}/apps/{app_name}/resource-patterns` | 内部（管理员） | 给已存在的 app 新加一个 resource_pattern | `{"resource_prefix","method","resource_type","id_source","id_field","share_to_admin_group_on_create","share_to_all_users_on_create","actions":[]}` | `ResourcePatternResponse` (201) | (app,prefix,method) 冲突→409 |
| 修改模式 | PUT | `/AccessManager/Tenants/{tid}/apps/{app_name}/resource-patterns` | 内部（管理员） | 按复合键更新 resource_pattern | `?resource_prefix=&method=` + body | `ResourcePatternResponse` | method='' 表示 fallback 条目 |
| 删除模式 | DELETE | `/AccessManager/Tenants/{tid}/apps/{app_name}/resource-patterns` | 内部（管理员） | 删单条 resource_pattern | `?resource_prefix=&method=&cascade_actions=true` | 204 | cascade_actions=true 一并删同 prefix 的 actions |

---

## 资源动作（resource_actions）

| 接口名称 | Method | 路径（新格式） | 调用方 | 说明 | 请求体/参数 | 响应 | 备注 |
|---|---|---|---|---|---|---|---|
| 新增动作 | POST | `/AccessManager/Tenants/{tid}/apps/{app_name}/resource-actions` | 内部（管理员） | 新增 resource_action 行 | `?resource_prefix=` + `{"action","method","path_suffix","success_status","min_permission"}` | `ResourceActionResponse` (201) | resource_prefix 必须对应已有 pattern |
| 修改动作 | PUT | `/AccessManager/Tenants/{tid}/apps/{app_name}/resource-actions/{action_id}` | 内部（管理员） | 按 id 更新 resource_action 行 | `{"action","method","path_suffix","success_status","min_permission"}` | `ResourceActionResponse` | — |
| 删除动作 | DELETE | `/AccessManager/Tenants/{tid}/apps/{app_name}/resource-actions/{action_id}` | 内部（管理员） | 按 id 删除 resource_action 行 | — | 204 | — |

---

## API Key 管理

| 接口名称 | Method | 路径（新格式） | 调用方 | 说明 | 请求体/参数 | 响应 | 备注 |
|---|---|---|---|---|---|---|---|
| 创建 Key | POST | `/AccessManager/Tenants/{tid}/api-keys` | 前端 | 创建 API Key，明文只返回一次 | `{"app_name","description","subject_id","allowed_paths":[],"expires_at"}` | `ApiKeyCreateResponse` (201) | 含明文 api_key 字段 |
| Key 列表 | GET | `/AccessManager/Tenants/{tid}/api-keys` | 前端 | 列出所有 Key（只显示前缀，无明文） | — | `List[ApiKeyResponse]` | — |
| Key 详情 | GET | `/AccessManager/Tenants/{tid}/api-keys/{key_id}` | 前端 | 单个 Key 详情 | — | `ApiKeyResponse` | 无明文 |
| 修改 Key | PUT | `/AccessManager/Tenants/{tid}/api-keys/{key_id}` | 前端 | 修改 Key 信息 | `{"description","enabled"}` | `ApiKeyResponse` | — |
| 删除 Key | DELETE | `/AccessManager/Tenants/{tid}/api-keys/{key_id}` | 前端 | 删除 Key | — | 204 | — |
| 轮换 Key | POST | `/AccessManager/Tenants/{tid}/api-keys/{key_id}/rotate` | 前端 | 轮换 Key，subject_id 不变，旧 Key 立即失效 | — | `ApiKeyCreateResponse` | 含新明文 api_key |

**ApiKeyCreateResponse 示例**

```json
{
  "id": "key-uuid-001",
  "app_name": "kb",
  "description": "CI pipeline key",
  "subject_id": "user-uuid",
  "key_prefix": "aidp_",
  "api_key": "aidp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "allowed_paths": ["/kb/v1/"],
  "enabled": true,
  "expires_at": "2027-01-01T00:00:00Z",
  "created_at": "2026-05-06T00:00:00Z"
}
```

---

## 资源 ACL 管理（新）

| 接口名称 | Method | 路径（新格式） | 调用方 | 说明 | 请求体/参数 | 响应 | 备注 |
|---|---|---|---|---|---|---|---|
| 批量授权 | PUT | `/AccessManager/Tenants/{tid}/ACLs` | 前端/应用 | 批量写入 ACL 三元组，调用者须是 Owner 或管理员 | `{"acls":[{"user","object","role"}]}` | `{"results":[{"user","object","status","reason"}]}` | role 须是合法角色路径 |
| 查询 ACL | GET | `/AccessManager/Tenants/{tid}/ACLs` | 前端/应用 | 按 object 或 user 查询 ACL 列表 | `?object=...` 或 `?user=...` | `{"acls":[...],"count"}` | 二选一必填 |
| 删除权限 | DELETE | `/AccessManager/Tenants/{tid}/ACLs` | 前端/应用 | 撤销指定 ACL 条目，调用者须是 Owner 或管理员 | `{"user","object"}` | `{"status":"deleted","user","object"}` | — |
| 批量权限检查 | POST | `/AccessManager/Tenants/{tid}/Action/QueryACLs` | 前端/应用 | 批量检查(user,object,role)三元组是否满足，支持前缀匹配 | `[{"properties":{"user","object","role"}}]` | `[{"allowed":bool,"matched_object","role","reason"}]` | — |

**PUT /ACLs 请求示例**

```json
{
  "acls": [
    {"user": "user-uuid-alice", "object": "kb/kb-uuid-001", "role": "contributor"},
    {"user": "user-uuid-bob",   "object": "kb/kb-uuid-001", "role": "viewer"}
  ]
}
```

**POST /Action/QueryACLs 请求示例**

```json
[
  {"properties": {"user": "user-uuid-alice", "object": "kb/kb-uuid-001", "role": "viewer"}},
  {"properties": {"user": "user-uuid-bob",   "object": "kb/kb-uuid-002", "role": "owner"}}
]
```

---

## 应用 Manifest 管理（新）

| 接口名称 | Method | 路径（新格式） | 调用方 | 说明 | 请求体/参数 | 响应 | 备注 |
|---|---|---|---|---|---|---|---|
| 注册/更新 Manifest | PUT | `/AccessManager/Tenants/System/AppManifests/{namespace}` | 内部（管理员） | 注册或更新应用 manifest，触发 default_acl 同步写入所有租户 | `{"base_url","manifest_json":{...}}` | `{"status","namespace","acls_synced":N}` | manifest_json 格式见 manifest-template.json |
| Manifest 列表 | GET | `/AccessManager/Tenants/System/AppManifests` | 内部（管理员） | 列出所有已注册的应用 manifest | — | `{"manifests":[...],"count"}` | — |
| Manifest 详情 | GET | `/AccessManager/Tenants/System/AppManifests/{namespace}` | 内部（管理员） | 获取单个应用 manifest | — | `{"namespace","base_url","manifest_json",...}` | — |
| 删除 Manifest | DELETE | `/AccessManager/Tenants/System/AppManifests/{namespace}` | 内部（管理员） | 删除应用 manifest（不级联删除已写入的 ACL） | — | `{"status":"deleted","namespace"}` | — |

---

## IdP / SAML 管理

| 接口名称 | Method | 路径（新格式） | 调用方 | 说明 | 请求体/参数 | 响应 | 备注 |
|---|---|---|---|---|---|---|---|
| 导入 SAML 元数据 | POST | `/AccessManager/Tenants/{tid}/idp/saml/import` | 前端 | 上传 SAML 元数据 XML，解析 IdP 配置 | multipart/form-data file | `SAMLImportResponse` | — |
| 创建 IdP 实例 | POST | `/AccessManager/Tenants/{tid}/idp/saml/instances` | 前端 | 创建 SAML IdP 实例（每 realm 仅一个，alias 固定为 da-saml-idp） | `{"alias","displayName","config":{...}}` | `IdPInstanceResponse` (201) | — |
| 修改 IdP 实例 | PUT | `/AccessManager/Tenants/{tid}/idp/saml/instances` | 前端 | 修改 SAML IdP 配置 | `{"config":{...}}` | `IdPInstanceResponse` | — |
| IdP 实例列表 | GET | `/AccessManager/Tenants/{tid}/idp/saml/instances` | 前端 | 列出 realm 下所有 IdP 实例 | — | `List[IdPInstanceResponse]` | — |
| 删除 IdP 实例 | DELETE | `/AccessManager/Tenants/{tid}/idp/saml/instances/{alias}` | 前端 | 删除指定 IdP 实例 | — | 204 | — |
| Mapper 列表 | GET | `/AccessManager/Tenants/{tid}/idp/saml/instances/{alias}/mappers` | 前端 | 获取属性映射列表 | — | `List[MapperResponse]` | — |
| 创建 Mapper | POST | `/AccessManager/Tenants/{tid}/idp/saml/instances/{alias}/mappers` | 前端 | 创建属性映射：SAML 属性 → Keycloak 属性 | `{"name","identityProviderMapper","config":{...}}` | `MapperResponse` (201) | — |
| 修改 Mapper | PUT | `/AccessManager/Tenants/{tid}/idp/saml/instances/{alias}/mappers/{mapper_id}` | 前端 | 修改属性映射 | `{"name","config":{...}}` | `MapperResponse` | — |
| 删除 Mapper | DELETE | `/AccessManager/Tenants/{tid}/idp/saml/instances/{alias}/mappers/{mapper_id}` | 前端 | 删除属性映射 | — | 204 | — |
| GroupMapper 列表 | GET | `/AccessManager/Tenants/{tid}/idp/saml/instances/{alias}/group-mappers` | 前端 | 获取条件化自动加组规则列表 | — | `List[GroupMapperResponse]` | — |
| 创建 GroupMapper | POST | `/AccessManager/Tenants/{tid}/idp/saml/instances/{alias}/group-mappers` | 前端 | 创建条件化自动加组规则（SAML 属性命中时自动加入指定组） | `{"attribute_name","attribute_value","group_id"}` | `GroupMapperResponse` (201) | — |
| 修改 GroupMapper | PUT | `/AccessManager/Tenants/{tid}/idp/saml/instances/{alias}/group-mappers/{mapper_id}` | 前端 | 修改条件化加组规则 | `{"attribute_name","attribute_value","group_id"}` | `GroupMapperResponse` | — |
| 删除 GroupMapper | DELETE | `/AccessManager/Tenants/{tid}/idp/saml/instances/{alias}/group-mappers/{mapper_id}` | 前端 | 删除条件化加组规则 | — | 204 | — |

---

## Token

| 接口名称 | Method | 路径（新格式） | 调用方 | 说明 | 请求体/参数 | 响应 | 备注 |
|---|---|---|---|---|---|---|---|
| 授权码换 Token | POST | `/AccessManager/Tenants/{tid}/token/exchange` | 前端 | OIDC 授权码换取 access_token | `{"code","redirect_uri","client_id","client_secret"}` | `TokenExchangeResponse` | client_secret 可选 |

---

## 公共接口

| 接口名称 | Method | 路径（新格式） | 调用方 | 说明 | 请求体/参数 | 响应 | 备注 |
|---|---|---|---|---|---|---|---|
| 健康检查 | GET | `/AccessManager/Tenants/common/health` | 监控 | 服务健康状态 | — | `{"status":"ok"}` | — |
| 租户列表 | GET | `/AccessManager/Tenants` | 前端 | 列出租户（单租户模式，返回当前配置的 realm） | — | `List[TenantResponse]` | — |

---

## 附录：旧路径兼容对照表

| 新路径（v2.1） | 旧路径（/api/v1/ 别名） |
|---|---|
| `/AccessManager/Tenants/{tid}/Users` | `/api/v1/{realm}/users` |
| `/AccessManager/Tenants/{tid}/Users/{user_id}/details` | `/api/v1/{realm}/users/{user_id}/details` |
| `/AccessManager/Tenants/{tid}/Users/{user_id}/password` | `/api/v1/{realm}/users/{user_id}/password` |
| `/AccessManager/Tenants/{tid}/Users/{user_id}/groups/{group_id}` | `/api/v1/{realm}/users/{user_id}/groups/{group_id}` |
| `/AccessManager/Tenants/{tid}/Users/{user_id}/available-groups` | `/api/v1/{realm}/users/{user_id}/available-groups` |
| `/AccessManager/Tenants/{tid}/Users/batch-delete` | `/api/v1/{realm}/users/batch-delete` |
| `/AccessManager/Tenants/{tid}/Users/batch-import` | `/api/v1/{realm}/users/batch-import` |
| `/AccessManager/Tenants/{tid}/Users/import-template` | `/api/v1/{realm}/users/import-template` |
| `/AccessManager/Tenants/{tid}/Groups` | `/api/v1/{realm}/groups` |
| `/AccessManager/Tenants/{tid}/Groups/{group_id}` | `/api/v1/{realm}/groups/{group_id}` |
| `/AccessManager/Tenants/{tid}/Groups/{group_id}/members/batch-add` | `/api/v1/{realm}/groups/{group_id}/members/batch-add` |
| `/AccessManager/Tenants/{tid}/Groups/{group_id}/members/batch-remove` | `/api/v1/{realm}/groups/{group_id}/members/batch-remove` |
| `/AccessManager/Tenants/{tid}/Groups/{group_id}/permissions` | `/api/v1/{realm}/groups/{group_id}/permissions` |
| `/AccessManager/Tenants/{tid}/permissions` | `/api/v1/{realm}/permissions` |
| `/AccessManager/Tenants/{tid}/PermissionGroups` | `/api/v1/{realm}/permission-groups` |
| `/AccessManager/Tenants/{tid}/PermissionGroups/{group_id}` | `/api/v1/{realm}/permission-groups/{group_id}` |
| `/AccessManager/Tenants/{tid}/PermissionGroups/{group_id}/paths` | `/api/v1/{realm}/permission-groups/{group_id}/paths` |
| `/AccessManager/Tenants/{tid}/PermissionGroups/{group_id}/paths/{path_id}` | `/api/v1/{realm}/permission-groups/{group_id}/paths/{path_id}` |
| `/AccessManager/Tenants/{tid}/PermissionGroups/{group_id}/bindings/{kc_group_name}` | `/api/v1/{realm}/permission-groups/{group_id}/bindings/{kc_group_name}` |
| `/AccessManager/Tenants/{tid}/apps` | `/api/v1/apps` |
| `/AccessManager/Tenants/{tid}/apps/{app_name}` | `/api/v1/apps/{app_name}` |
| `/AccessManager/Tenants/{tid}/apps/{app_name}/resource-patterns` | `/api/v1/apps/{app_name}/resource-patterns` |
| `/AccessManager/Tenants/{tid}/apps/{app_name}/resource-actions` | `/api/v1/apps/{app_name}/resource-actions` |
| `/AccessManager/Tenants/{tid}/apps/{app_name}/resource-actions/{action_id}` | `/api/v1/apps/{app_name}/resource-actions/{action_id}` |
| `/AccessManager/Tenants/{tid}/api-keys` | `/api/v1/{realm}/api-keys` |
| `/AccessManager/Tenants/{tid}/api-keys/{key_id}` | `/api/v1/{realm}/api-keys/{key_id}` |
| `/AccessManager/Tenants/{tid}/api-keys/{key_id}/rotate` | `/api/v1/{realm}/api-keys/{key_id}/rotate` |
| `/AccessManager/Tenants/{tid}/ACLs` | `/acl/v1/resources/acls` |
| `/AccessManager/Tenants/{tid}/Action/QueryACLs` | `/acl/v1/resources/query-acls` |
| `/AccessManager/Tenants/{tid}/idp/saml/instances` | `/api/v1/{realm}/idp/saml/instances` |
| `/AccessManager/Tenants/{tid}/idp/saml/instances/{alias}/mappers` | `/api/v1/{realm}/idp/saml/instances/{alias}/mappers` |
| `/AccessManager/Tenants/{tid}/idp/saml/instances/{alias}/group-mappers` | `/api/v1/{realm}/idp/saml/instances/{alias}/group-mappers` |
| `/AccessManager/Tenants/{tid}/token/exchange` | `/api/v1/{realm}/token/exchange` |
| `/AccessManager/Tenants/common/health` | `/api/v1/health` |
| `/AccessManager/Tenants` | `/api/v1/tenants` |
