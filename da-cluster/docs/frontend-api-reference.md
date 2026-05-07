# IAM 系统接口文档（前端对接版）

**版本**: v2.1 | **日期**: 2026-05-07

本文档基于当前代码实现，列出前端对接所需的全部接口。所有路径遵循统一格式：

```
/AccessManager/Tenants/{tid}/{ResourceType}/{resourceId}
```

`{tid}` 为租户 ID（例如 `aidp`）。应用资源路径遵循：

```
/{AppNamespace}/Tenants/{tid}/{ResourceType}/{resourceId}
```

---

## 用户管理

| 接口名称 | Method | 路径 | 说明 | 请求体/参数 | 响应 |
|---|---|---|---|---|---|
| 用户列表 | GET | `/AccessManager/Tenants/{tid}/Users` | 支持搜索/分页/按组过滤 | `?search=&group_id=&first=0&max=50` | `List[UserResponse]` |
| 用户详情 | GET | `/AccessManager/Tenants/{tid}/Users/{userId}/details` | 用户信息 + 所属组 | — | `UserDetailResponse` |
| 创建用户 | POST | `/AccessManager/Tenants/{tid}/Users` | 创建内部用户，可选绑组 | `{"username","password","groups":[gid],"temporary_password":true}` | `UserResponse` (201) |
| 修改用户 | PUT | `/AccessManager/Tenants/{tid}/Users/{userId}` | 修改启用状态 | `{"enabled":bool}` | `UserResponse` |
| 删除用户 | DELETE | `/AccessManager/Tenants/{tid}/Users/{userId}` | 删除单个用户 | — | 204 |
| 批量删除 | POST | `/AccessManager/Tenants/{tid}/Users/BatchDelete` | 批量删除 | `{"user_ids":["uuid1"]}` | `BatchOperationResponse` |
| 重置密码 | PUT | `/AccessManager/Tenants/{tid}/Users/{userId}/Password` | 重置密码，联邦用户返回 400 | `{"password":"..."}` | 204 |
| 添加用户到组 | PUT | `/AccessManager/Tenants/{tid}/Users/{userId}/Groups/{groupId}` | 将用户加入指定组 | — | 204 |
| 移除用户出组 | DELETE | `/AccessManager/Tenants/{tid}/Users/{userId}/Groups/{groupId}` | 将用户从组中移除 | — | 204 |
| 用户可选组 | GET | `/AccessManager/Tenants/{tid}/Users/{userId}/AvailableGroups` | 所有组 + joined 标记 | — | `[{"id","name","joined":bool}]` |
| CSV 导入模板 | GET | `/AccessManager/Tenants/{tid}/Users/ImportTemplate` | 下载 CSV 模板 | — | text/csv |
| 批量导入 | POST | `/AccessManager/Tenants/{tid}/Users/BatchImport` | 上传 CSV 批量创建用户 | multipart/form-data | `BatchOperationResponse` |

**UserResponse 示例**

```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "username": "alice",
  "email": "alice@example.com",
  "enabled": true,
  "account_type": "internal",
  "groups": [{"id": "g1", "name": "all-users"}],
  "created_at": "2026-01-01T00:00:00Z"
}
```

---

## 用户组管理

| 接口名称 | Method | 路径 | 说明 | 请求体/参数 | 响应 |
|---|---|---|---|---|---|
| 用户组列表 | GET | `/AccessManager/Tenants/{tid}/Groups` | 支持搜索/分页，含 member_count | `?search=&first=0&max=50` | `List[GroupResponse]` |
| 创建用户组 | POST | `/AccessManager/Tenants/{tid}/Groups` | 创建组，可选绑用户 | `{"name","users":[uid]}` | `GroupResponse` (201) |
| 用户组详情 | GET | `/AccessManager/Tenants/{tid}/Groups/{groupId}` | 成员列表 | — | `GroupDetailResponse` |
| 修改用户组 | PUT | `/AccessManager/Tenants/{tid}/Groups/{groupId}` | 修改名称 + 全量同步成员 | `{"name","users":[uid]}` | 204 |
| 删除用户组 | DELETE | `/AccessManager/Tenants/{tid}/Groups/{groupId}` | 删除自定义组，预置组返回 400 | — | 204 |
| 批量添加成员 | POST | `/AccessManager/Tenants/{tid}/Groups/{groupId}/Members/BatchAdd` | 批量将用户添加到组 | `{"user_ids":["uid1"]}` | `BatchOperationResponse` |
| 批量移除成员 | POST | `/AccessManager/Tenants/{tid}/Groups/{groupId}/Members/BatchRemove` | 批量从组中移除用户 | `{"user_ids":["uid1"]}` | `BatchOperationResponse` |

---

## 应用管理

应用通过 Manifest 注册，`apps` 表仅保存 `enabled` 状态供 OPA app_disabled 检查。

| 接口名称 | Method | 路径 | 说明 | 请求体/参数 | 响应 |
|---|---|---|---|---|---|
| 应用列表 | GET | `/api/v1/apps` | 所有注册的应用（enabled 状态） | — | `List[AppResponse]` |
| 应用详情 | GET | `/api/v1/apps/{appName}` | 单个应用详情 | — | `AppResponse` |
| 修改应用 | PUT | `/api/v1/apps/{appName}` | 修改 enabled 状态（License 开关） | `{"enabled":bool}` | `AppResponse` |

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
      "path_pattern": "/KnowledgeBase/Tenants/{tenantId}/KnowledgeBases/{kbId}",
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
          "user_template": "AccessManager/Tenants/{tenantId}/Groups/all-users",
          "object_template": "KnowledgeBase/Tenants/{tenantId}/KnowledgeBases",
          "role_path": "AccessManager/Tenants/System/Roles/Contributor"
        }
      ],
      "children": [
        {
          "type": "Files",
          "path_pattern": "/KnowledgeBase/Tenants/{tenantId}/KnowledgeBases/{kbId}/Files/{fileId}",
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
- 每个 `method` 生成一条 path_rule，`required_groups = ["all-users"]`
- `actions[].path_suffix` 也生成对应条目
- `children[]` 递归处理

---

## 资源 ACL 管理

所有路径使用全路径格式（`AccessManager/Tenants/{tid}/...`、`{AppNS}/Tenants/{tid}/...`）。

| 接口名称 | Method | 路径 | 说明 | 请求体/参数 | 响应 |
|---|---|---|---|---|---|
| 写入 ACL | PUT | `/AccessManager/Tenants/{tid}/ACLs` | 写入 ACL 三元组，调用者须是 Owner 或管理员 | `{"user_path","object_path","role_path"}` | 200/201 |
| 查询 ACL | GET | `/AccessManager/Tenants/{tid}/ACLs` | 按 object 或 user 查询 ACL 列表 | `?object=...` 或 `?user=...` | `{"acls":[...],"count"}` |
| 删除 ACL | DELETE | `/AccessManager/Tenants/{tid}/ACLs` | 撤销指定 ACL 条目 | `{"user_path","object_path"}` | 200/204 |
| 批量权限检查 | POST | `/AccessManager/Tenants/{tid}/Action/QueryACLs` | 批量检查 (user, object) 的当前角色 | `{"queries":[{"user_path","object_path"}]}` | `{"results":[{"user_path","object_path","role_path","allowed":bool}]}` |

**路径格式说明**

| 字段 | 格式 | 示例 |
|---|---|---|
| `user_path` | `AccessManager/Tenants/{tid}/Users/{userId}` | `AccessManager/Tenants/aidp/Users/3fa85f64` |
| `object_path` | `{AppNS}/Tenants/{tid}/{ResourceType}/{resourceId}` | `KnowledgeBase/Tenants/aidp/KnowledgeBases/kb-001` |
| `role_path` | `AccessManager/Tenants/System/Roles/{role}` | `AccessManager/Tenants/System/Roles/Owner` |

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
| 创建 Key | POST | `/api/v1/{tid}/api-keys` | 创建 API Key，明文只返回一次 | `{"app_name","description","subject_id","allowed_paths":[],"expires_at"}` | `ApiKeyCreateResponse` (201) |
| Key 列表 | GET | `/api/v1/{tid}/api-keys` | 列出所有 Key（只显示前缀，无明文） | — | `List[ApiKeyResponse]` |
| Key 详情 | GET | `/api/v1/{tid}/api-keys/{keyId}` | 单个 Key 详情 | — | `ApiKeyResponse` |
| 修改 Key | PUT | `/api/v1/{tid}/api-keys/{keyId}` | 修改 Key 信息 | `{"description","enabled"}` | `ApiKeyResponse` |
| 删除 Key | DELETE | `/api/v1/{tid}/api-keys/{keyId}` | 删除 Key | — | 204 |
| 轮换 Key | POST | `/api/v1/{tid}/api-keys/{keyId}/rotate` | 轮换 Key，旧 Key 立即失效 | — | `ApiKeyCreateResponse` |

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
| 导入 SAML 元数据 | POST | `/AccessManager/Tenants/{tid}/Idp/Saml/Import` | 上传 SAML 元数据 XML | multipart/form-data | `SAMLImportResponse` |
| 创建 IdP 实例 | POST | `/AccessManager/Tenants/{tid}/Idp/Saml/Instances` | 创建 SAML IdP 实例 | `{"alias","displayName","config":{...}}` | `IdPInstanceResponse` (201) |
| 修改 IdP 实例 | PUT | `/AccessManager/Tenants/{tid}/Idp/Saml/Instances` | 修改 SAML IdP 配置 | `{"config":{...}}` | `IdPInstanceResponse` |
| IdP 实例列表 | GET | `/AccessManager/Tenants/{tid}/Idp/Saml/Instances` | 列出所有 IdP 实例 | — | `List[IdPInstanceResponse]` |
| 删除 IdP 实例 | DELETE | `/AccessManager/Tenants/{tid}/Idp/Saml/Instances/{alias}` | 删除指定 IdP 实例 | — | 204 |
| Mapper 列表 | GET | `/AccessManager/Tenants/{tid}/Idp/Saml/Instances/{alias}/Mappers` | 获取属性映射列表 | — | `List[MapperResponse]` |
| 创建 Mapper | POST | `/AccessManager/Tenants/{tid}/Idp/Saml/Instances/{alias}/Mappers` | 创建属性映射 | `{"name","identityProviderMapper","config":{...}}` | `MapperResponse` (201) |
| 修改 Mapper | PUT | `/AccessManager/Tenants/{tid}/Idp/Saml/Instances/{alias}/Mappers/{mapperId}` | 修改属性映射 | `{"name","config":{...}}` | `MapperResponse` |
| 删除 Mapper | DELETE | `/AccessManager/Tenants/{tid}/Idp/Saml/Instances/{alias}/Mappers/{mapperId}` | 删除属性映射 | — | 204 |
| GroupMapper 列表 | GET | `/AccessManager/Tenants/{tid}/Idp/Saml/Instances/{alias}/GroupMappers` | 获取条件化自动加组规则 | — | `List[GroupMapperResponse]` |
| 创建 GroupMapper | POST | `/AccessManager/Tenants/{tid}/Idp/Saml/Instances/{alias}/GroupMappers` | 创建条件化自动加组规则 | `{"attribute_name","attribute_value","group_id"}` | `GroupMapperResponse` (201) |
| 修改 GroupMapper | PUT | `/AccessManager/Tenants/{tid}/Idp/Saml/Instances/{alias}/GroupMappers/{mapperId}` | 修改条件化加组规则 | `{"attribute_name","attribute_value","group_id"}` | `GroupMapperResponse` |
| 删除 GroupMapper | DELETE | `/AccessManager/Tenants/{tid}/Idp/Saml/Instances/{alias}/GroupMappers/{mapperId}` | 删除条件化加组规则 | — | 204 |

---

## Token

| 接口名称 | Method | 路径 | 说明 | 请求体/参数 | 响应 |
|---|---|---|---|---|---|
| 授权码换 Token | POST | `/AccessManager/Tenants/{tid}/Token/Exchange` | OIDC 授权码换取 access_token | `{"code","redirect_uri","client_id","client_secret"}` | `TokenExchangeResponse` |

---

## 公共接口

| 接口名称 | Method | 路径 | 说明 | 响应 |
|---|---|---|---|---|
| 健康检查 | GET | `/api/v1/common/health` | 服务健康状态 | `{"status":"ok"}` |
| 租户列表 | GET | `/api/v1/tenants` | 列出租户（单租户模式） | `List[TenantResponse]` |

---

## KnowledgeBase 应用接口

KnowledgeBase 应用遵循统一 URL 格式，路径鉴权由 OPA 从 manifest 派生，资源级鉴权由 resource-sync 通过 `resource_acl` 控制。

| 接口名称 | Method | 路径 | 说明 |
|---|---|---|---|
| 知识库列表 | GET | `/KnowledgeBase/Tenants/{tid}/KnowledgeBases` | 支持分页，X-Allowed-Ids 过滤 |
| 创建知识库 | POST | `/KnowledgeBase/Tenants/{tid}/KnowledgeBases` | 创建后 ext_proc 自动写 Owner ACL |
| 知识库详情 | GET | `/KnowledgeBase/Tenants/{tid}/KnowledgeBases/{kbId}` | 需 Viewer 权限 |
| 更新知识库 | PUT | `/KnowledgeBase/Tenants/{tid}/KnowledgeBases/{kbId}` | 需 Contributor 权限 |
| 删除知识库 | DELETE | `/KnowledgeBase/Tenants/{tid}/KnowledgeBases/{kbId}` | 需 Owner 权限 |
| 目录映射列表 | GET | `/KnowledgeBase/Tenants/{tid}/KnowledgeBases/{kbId}/Mappings` | — |
| 创建目录映射 | POST | `/KnowledgeBase/Tenants/{tid}/KnowledgeBases/{kbId}/Mappings` | — |
| 删除目录映射 | DELETE | `/KnowledgeBase/Tenants/{tid}/KnowledgeBases/{kbId}/Mappings/{mappingId}` | — |
| 文件列表 | GET | `/KnowledgeBase/Tenants/{tid}/KnowledgeBases/{kbId}/Files` | — |
| 上传文件 | POST | `/KnowledgeBase/Tenants/{tid}/KnowledgeBases/{kbId}/Files` | multipart/form-data |
| 删除文件 | DELETE | `/KnowledgeBase/Tenants/{tid}/KnowledgeBases/{kbId}/Files/{fileId}` | — |
| 会话列表 | GET | `/KnowledgeBase/Tenants/{tid}/Conversations` | — |
| 创建会话（问答） | POST | `/KnowledgeBase/Tenants/{tid}/Conversations` | — |
| 会话详情 | GET | `/KnowledgeBase/Tenants/{tid}/Conversations/{threadId}` | — |
| 停止会话 | POST | `/KnowledgeBase/Tenants/{tid}/Conversations/{threadId}/Stop` | — |
| 删除会话 | DELETE | `/KnowledgeBase/Tenants/{tid}/Conversations/{threadId}` | — |
| 模型配置列表 | GET | `/KnowledgeBase/Tenants/System/ModelConfigs` | 系统级，需 master-admins |
| 创建模型配置 | POST | `/KnowledgeBase/Tenants/System/ModelConfigs` | — |
| 更新模型配置 | PUT | `/KnowledgeBase/Tenants/System/ModelConfigs/{modelId}` | — |
| 删除模型配置 | DELETE | `/KnowledgeBase/Tenants/System/ModelConfigs/{modelId}` | — |
| 提示词列表 | GET | `/KnowledgeBase/Tenants/System/Prompts` | 系统级 |
| 创建提示词 | POST | `/KnowledgeBase/Tenants/System/Prompts` | — |
| 提示词详情 | GET | `/KnowledgeBase/Tenants/System/Prompts/{promptId}` | — |
| 更新提示词 | PUT | `/KnowledgeBase/Tenants/System/Prompts/{promptId}` | — |
| 删除提示词 | DELETE | `/KnowledgeBase/Tenants/System/Prompts/{promptId}` | — |
| 术语库列表 | GET | `/KnowledgeBase/Tenants/{tid}/JargonLibraries` | — |
| 创建术语库 | POST | `/KnowledgeBase/Tenants/{tid}/JargonLibraries` | — |
| 删除术语库 | DELETE | `/KnowledgeBase/Tenants/{tid}/JargonLibraries/{libName}` | — |
| 术语列表 | GET | `/KnowledgeBase/Tenants/{tid}/JargonLibraries/{libName}/Jargons` | — |
| 创建术语 | POST | `/KnowledgeBase/Tenants/{tid}/JargonLibraries/{libName}/Jargons` | — |
| 更新术语 | PUT | `/KnowledgeBase/Tenants/{tid}/JargonLibraries/{libName}/Jargons/{jargonName}` | — |
| 删除术语 | DELETE | `/KnowledgeBase/Tenants/{tid}/JargonLibraries/{libName}/Jargons/{jargonName}` | — |
| 融合检索 | POST | `/KnowledgeBase/Tenants/{tid}/Action/FusionSearch` | — |

---

## DataAgent 应用接口

DataAgent 通过 manifest 注册，路径鉴权由 OPA 从 manifest 派生。

| 接口名称 | Method | 路径 | 说明 |
|---|---|---|---|
| 数据库列表 | GET | `/DataAgent/Tenants/{tid}/DataBases` | X-Allowed-Ids 过滤 |
| 创建数据库 | POST | `/DataAgent/Tenants/{tid}/DataBases` | ext_proc 自动写 Owner ACL |
| 数据库详情 | GET | `/DataAgent/Tenants/{tid}/DataBases/{databaseId}` | 需 Viewer 权限 |
| 更新数据库 | PUT | `/DataAgent/Tenants/{tid}/DataBases/{databaseId}` | 需 Contributor 权限 |
| 删除数据库 | DELETE | `/DataAgent/Tenants/{tid}/DataBases/{databaseId}` | 需 Owner 权限 |
| 知识库列表 | GET | `/DataAgent/Tenants/{tid}/DataAgentDBs` | — |
| 创建知识库 | POST | `/DataAgent/Tenants/{tid}/DataAgentDBs` | — |
| 知识库详情 | GET | `/DataAgent/Tenants/{tid}/DataAgentDBs/{dbId}` | — |
| 更新知识库 | PUT | `/DataAgent/Tenants/{tid}/DataAgentDBs/{dbId}` | — |
| 删除知识库 | DELETE | `/DataAgent/Tenants/{tid}/DataAgentDBs/{dbId}` | — |
| 查询（NL2SQL） | POST | `/DataAgent/Tenants/{tid}/DataAgentDBs/{dbId}/Query` | 需 Contributor 权限 |
| 数据表列表 | GET | `/DataAgent/Tenants/{tid}/DataAgentDBs/{dbId}/Tables` | — |
| 会话列表 | GET | `/DataAgent/Tenants/{tid}/DataAgentSessions` | — |
| 创建会话 | POST | `/DataAgent/Tenants/{tid}/DataAgentSessions` | — |
| 会话详情 | GET | `/DataAgent/Tenants/{tid}/DataAgentSessions/{sessionId}` | — |
| 删除会话 | DELETE | `/DataAgent/Tenants/{tid}/DataAgentSessions/{sessionId}` | — |
| 对话 | POST | `/DataAgent/Tenants/{tid}/DataAgentSessions/{sessionId}/Chat` | 需 Contributor 权限 |

---

## 附录：路径格式规范

### 统一 URL 格式

所有应用资源路径遵循：

```
/{AppNamespace}/Tenants/{tenantId}/{ResourceType}/{resourceId}
/{AppNamespace}/Tenants/{tenantId}/{ResourceType}/{resourceId}/{ChildType}/{childId}
/{AppNamespace}/Tenants/{tenantId}/Action/{ActionName}   ← 非 CRUD 操作
/{AppNamespace}/Tenants/System/{ResourceType}/{resourceId}  ← 系统级资源
```

**命名规范：**
- `AppNamespace`：PascalCase，例如 `KnowledgeBase`、`DataAgent`、`MemoryStore`
- `ResourceType`：PascalCase 复数，例如 `KnowledgeBases`、`DataAgentDBs`
- `resourceId`：原始 ID，不做转换
- `Action`：PascalCase，例如 `FusionSearch`、`QueryACLs`、`BatchDelete`

### IAM 管理路径

```
/AccessManager/Tenants/{tid}/{ResourceType}/{resourceId}
/AccessManager/Tenants/System/{ResourceType}/{resourceId}  ← 系统级（跨租户）
/api/v1/...  ← 系统管理 API（apps、tenants、health）
```

### ACL 路径格式

```
user_path:   AccessManager/Tenants/{tid}/Users/{userId}
object_path: {AppNS}/Tenants/{tid}/{ResourceType}/{resourceId}
role_path:   AccessManager/Tenants/System/Roles/{Owner|Contributor|Viewer}
```
