# 统一鉴权系统设计文档

版本：v3.0  
日期：2026-04-30  
状态：草案

---

## 1. 背景与目标

当前各下层应用（DataAgent、MemoryStore、DataBase 等）各自维护独立的鉴权逻辑，导致：

- 权限配置分散，无法统一管理；
- 新应用接入成本高，需重复实现鉴权逻辑；
- 权限语义不一致，难以审计。

**目标**：设计一套统一鉴权系统，所有应用遵循统一的 URL 规范和 ACL 三元组模型，由 AccessManager 集中管理权限，支持应用自定义角色和回调扩展。

---

## 2. 核心概念

### 2.1 Namespace

每个应用对应一个 Namespace，是 URL 路径的第一段，也是 Gateway 路由的依据：

| Namespace | 应用 |
|---|---|
| `AccessManager` | IAM 鉴权系统自身 |
| `DataAgent` | 智能问数 |
| `MemoryStore` | 记忆库 |
| `DataBase` | 数据库服务 |

### 2.2 统一 URL 规范

参照 Microsoft Azure REST API 规范，所有应用 URL 遵循以下结构：

```
https://<FQDN>/<Namespace>/Tenants/<TenantID>/<TypeA>/<IDA>[/<TypeB>/<IDB>[/...]][/<Action>]
```

路径本质是**树结构，树高不固定**，支持任意深度的父子嵌套。

**标准动词映射：**

| 操作 | HTTP 方法 | URL 示例 | 说明 |
|---|---|---|---|
| List | `GET` | `GET /MemoryStore/Tenants/t-001/MemoryStores` | 返回用户有权限的资源列表 |
| Get | `GET` | `GET /MemoryStore/Tenants/t-001/MemoryStores/ms-001` | 读取单个资源 |
| Create | `PUT` | `PUT /MemoryStore/Tenants/t-001/MemoryStores` | **URL 末尾无 ID**，服务端生成 ID 并在响应体返回；ext_proc 拦截 201 响应体提取 ID 写入 ACL |
| Update | `PATCH` | `PATCH /MemoryStore/Tenants/t-001/MemoryStores/ms-001` | 更新资源，URL 含具体 ID |
| Delete | `DELETE` | `DELETE /MemoryStore/Tenants/t-001/MemoryStores/ms-001` | 删除资源，ext_proc 拦截 2xx 后级联清理 ACL |
| Action | `POST` | `POST /MemoryStore/Tenants/t-001/MemoryStores/ms-001/Backup` | 非标准动词，路径末尾为 Action 名称 |

**Create 与 Update 的 URL 区别：**

```
Create（服务端生成 ID）：
  PUT /MemoryStore/Tenants/t-001/MemoryStores          ← collection 路径，无 ID
  → 后端返回 201 + {"id": "ms-generated-001", ...}
  → ext_proc 从响应体提取 "id"，拼接为完整 object_path 写入 ACL

Update（修改已有资源）：
  PATCH /MemoryStore/Tenants/t-001/MemoryStores/ms-001  ← instance 路径，含 ID
  → 不写 ACL
```

**多级子资源示例：**

```
/MemoryStore/Tenants/t-001/MemoryStores/ms-001
/MemoryStore/Tenants/t-001/MemoryStores/ms-001/Memories/mem-001
/MemoryStore/Tenants/t-001/MemoryStores/ms-001/Memories/mem-001/Chunks/chunk-001
/DataAgent/Tenants/t-001/DataAgentDBs/db-001/Tables/tbl-001
```

### 2.3 ACL 三元组

`resource_acl` 表存储 `(User, Object, Role)` 三元组，所有字段均为完整路径字符串：

| 字段 | 含义 | 示例 |
|---|---|---|
| `user_path` | 用户或组的完整路径 | `AccessManager/Tenants/t-001/Groups/dev-team` |
| `object_path` | 资源对象的完整路径 | `MemoryStore/Tenants/t-001/MemoryStores/ms-001` |
| `role_path` | 角色的完整路径（可为空） | `AccessManager/Tenants/System/Roles/Owner` |

**用户/组路径（统一由 AccessManager 管理）：**

```
AccessManager/Tenants/<TenantID>/Users/<UserID>
AccessManager/Tenants/<TenantID>/Groups/<GroupName>
```

**对象路径（支持任意深度）：**

```
# 类型级（用于 List/Create 权限）
MemoryStore/Tenants/t-001/MemoryStores

# 实例级
MemoryStore/Tenants/t-001/MemoryStores/ms-001

# 子资源实例级
MemoryStore/Tenants/t-001/MemoryStores/ms-001/Memories/mem-001/Chunks/chunk-001
```

### 2.4 角色定义

#### 系统预置角色（Default Role，由 AccessManager 定义）

| 角色路径 | 允许操作 |
|---|---|
| `AccessManager/Tenants/System/Roles/Owner` | 全部操作（GET/LIST/PUT/PATCH/DELETE/POST Action） |
| `AccessManager/Tenants/System/Roles/Contributor` | 除 DELETE 外的所有操作，**包括创建本租户顶级资源** |
| `AccessManager/Tenants/System/Roles/Viewer` | 仅 GET / LIST（只返回有权限的资源） |

**权限矩阵：**

| HTTP 方法 | 操作语义 | Owner | Contributor | Viewer |
|---|---|:---:|:---:|:---:|
| GET（单个） | 读取资源 | ✓ | ✓ | ✓ |
| GET（列表） | 列举（仅返回有 GET 权限的资源，分页） | ✓ | ✓ | ✓ |
| PUT（顶级资源） | 在本租户内创建顶级资源 | ✓ | ✓ | ✗ |
| PUT（子资源） | 在有权限的父资源下创建子资源 | ✓ | ✓ | ✗ |
| PATCH | 更新资源 | ✓ | ✓ | ✗ |
| DELETE | 删除资源 | ✓ | ✗ | ✗ |
| POST（Action） | 执行动作 | ✓ | ✓ | ✗ |

**租户隔离规则：**
- 所有操作强制绑定 `tenant_id`，查询时作为分区键；
- 不允许跨租户访问任何资源，ACL 三元组中的 user、object 必须属于同一租户；
- 跨租户共享不在支持范围内。

**Contributor 创建顶级资源的前提：**
- 对象路径为类型级（无具体 ID），如 `MemoryStore/Tenants/t-001/MemoryStores`；
- 该类型级 ACL 由 tenant-admins 在用户入驻时预写，或由应用在 manifest 中声明"所有租户成员默认有 Contributor"。

#### 应用自定义角色（No Default Role）

应用可在自己的 Namespace 下定义角色，如：

```
DataAgent/Tenants/System/Roles/Backuper
MemoryStore/Tenants/System/Roles/Executor
```

自定义角色无内置语义，鉴权系统无法本地判断，需触发**回调接口**由应用自行决定。

---

## 3. 数据库表结构

### 3.1 resource_acl 表

```sql
CREATE TABLE resource_acl (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   TEXT NOT NULL,
    user_path   TEXT NOT NULL,
    object_path TEXT NOT NULL,
    role_path   TEXT NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
    created_by  TEXT,
    UNIQUE (tenant_id, user_path, object_path)
);

-- 前缀查询索引（支持最长前缀匹配）
CREATE INDEX idx_acl_object_prefix ON resource_acl (object_path text_pattern_ops);
CREATE INDEX idx_acl_user ON resource_acl (tenant_id, user_path);
```

**设计要点：**
- 无 `effect` 字段：只存授予的权限，无显式拒绝；
- 完整路径存储：写入时存完整路径，查询时做前缀匹配；
- 同一 (tenant, user, object) 只能有一个角色，修改需先删后增。

### 3.2 role_definitions 表（自定义角色注册）

```sql
CREATE TABLE role_definitions (
    role_path        TEXT PRIMARY KEY,
    namespace        TEXT NOT NULL,
    allowed_methods  TEXT[],
    allowed_actions  TEXT[],
    description      TEXT,
    created_at       TIMESTAMP NOT NULL DEFAULT NOW()
);
```

### 3.3 app_manifests 表（应用注册）

```sql
CREATE TABLE app_manifests (
    namespace       TEXT PRIMARY KEY,
    base_url        TEXT NOT NULL,
    callback_url    TEXT,
    manifest_json   JSONB NOT NULL,
    registered_at   TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

## 4. 权限查询逻辑（含时序图）

### 4.1 前缀匹配查询

查询某用户对某资源的权限时，将资源路径逐级拆解为所有前缀，取最长匹配：

```sql
-- 查询 user alice 对 /MemoryStore/Tenants/t-001/MemoryStores/ms-001/Memories/mem-001/Chunks/chunk-001 的权限
SELECT role_path, object_path
FROM resource_acl
WHERE tenant_id = 't-001'
  AND user_path IN (
    'AccessManager/Tenants/t-001/Users/alice',
    'AccessManager/Tenants/t-001/Groups/dev-team'   -- alice 所属的所有组
  )
  AND object_path IN (
    'MemoryStore/Tenants/t-001/MemoryStores/ms-001/Memories/mem-001/Chunks/chunk-001',
    'MemoryStore/Tenants/t-001/MemoryStores/ms-001/Memories/mem-001',
    'MemoryStore/Tenants/t-001/MemoryStores/ms-001',
    'MemoryStore/Tenants/t-001/MemoryStores',
    'MemoryStore/Tenants/t-001',
    'MemoryStore'
  )
ORDER BY LENGTH(object_path) DESC
LIMIT 1;
```

**匹配规则：**
1. 将请求路径逐级截断，生成所有前缀候选；
2. 查询 user_path 匹配（用户本人 + 所属所有组）；
3. 取 object_path 最长的那条记录（最精确匹配优先）；
4. 未命中 → 默认拒绝。

### 4.2 Python 实现逻辑

```python
def build_object_prefixes(object_path: str) -> list[str]:
    """将完整路径拆解为所有前缀候选，从最长到最短"""
    parts = object_path.split("/")
    prefixes = []
    for i in range(len(parts), 0, -1):
        prefixes.append("/".join(parts[:i]))
    return prefixes

async def query_permission(
    tenant_id: str,
    user_path: str,
    group_paths: list[str],
    object_path: str,
) -> str | None:
    subjects = [user_path] + group_paths
    candidates = build_object_prefixes(object_path)
    row = await db.fetchrow(
        """
        SELECT role_path, object_path FROM resource_acl
        WHERE tenant_id = $1
          AND user_path = ANY($2)
          AND object_path = ANY($3)
        ORDER BY LENGTH(object_path) DESC
        LIMIT 1
        """,
        tenant_id, subjects, candidates,
    )
    return row["role_path"] if row else None
```

---

## 5. 鉴权流程与时序图

### 5.1 场景一：Default Role 鉴权（最常见）

用户请求访问某资源，角色为系统预置的 Owner/Contributor/Viewer，pep-proxy 本地判断，无需回调。

```
Client         Gateway(Envoy)      pep-proxy         resource_acl DB
  │                  │                  │                    │
  │  DELETE /MemoryStore/Tenants/t-001/ │                    │
  │    MemoryStores/ms-001 ────────────►│                    │
  │                  │  ext_authz ─────►│                    │
  │                  │                  │                    │
  │                  │         [1] 解析 JWT                  │
  │                  │             user_path, groups         │
  │                  │                  │                    │
  │                  │         [2] 解析 URL                  │
  │                  │             namespace=MemoryStore     │
  │                  │             tenant_id=t-001           │
  │                  │             object=MemoryStore/       │
  │                  │               Tenants/t-001/          │
  │                  │               MemoryStores/ms-001     │
  │                  │             action=DELETE             │
  │                  │                  │                    │
  │                  │         [3] 构造前缀候选列表           │
  │                  │                  │──query(tenant,    │
  │                  │                  │   subjects,       │
  │                  │                  │   prefixes)──────►│
  │                  │                  │◄──role_path───────│
  │                  │                  │  =AccessManager/  │
  │                  │                  │   .../Roles/Owner │
  │                  │                  │                    │
  │                  │         [4] role.namespace            │
  │                  │             = "AccessManager"        │
  │                  │             → Default Role           │
  │                  │             → 本地矩阵:              │
  │                  │               Owner+DELETE = ✓       │
  │                  │                  │                    │
  │                  │◄── allow=true ───│                    │
  │◄── 转发后端 ─────│                  │                    │
```

---

### 5.2 场景二：List 请求的资源过滤

List 请求不能返回全量资源，只能返回用户有 GET 权限的资源，且需要分页。有两种处理模式，应用可自行选择。

#### 模式 A：Gateway 注入 X-Allowed-Ids（默认）

pep-proxy 在鉴权通过后，查询 resource_acl 做**反向查询**（给定 user，找出所有有权限的 object_id），将结果注入 `X-Allowed-Ids` 请求头，后端应用按此列表过滤返回结果。

**Gateway 注入的 X-Allowed-Ids 不设条数上限**，由 ext_proc 一次性查出所有有权限的 ID 并注入。应用收到后自行决定是否使用该列表过滤，或忽略它改用模式 B。

```
Client        Gateway(ext_proc)   pep-proxy        resource_acl DB   Backend App
  │                  │                 │                   │               │
  │  GET /MemoryStore/Tenants/         │                   │               │
  │   t-001/MemoryStores?page=1 ──────►│                   │               │
  │                  │  ext_authz ────►│                   │               │
  │                  │                 │                   │               │
  │                  │        [1] 鉴权通过（类型级 ACL）    │               │
  │                  │                 │                   │               │
  │                  │        [2] 反向查询:                 │               │
  │                  │            SELECT object_path        │               │
  │                  │            WHERE user IN subjects    │               │
  │                  │              AND object LIKE         │               │
  │                  │              'MemoryStore/Tenants/   │               │
  │                  │               t-001/MemoryStores/%'  │               │
  │                  │              AND page/limit ────────►│               │
  │                  │◄─── [ms-001, ms-003] ───────────────│               │
  │                  │                 │                   │               │
  │                  │  注入 X-Allowed-Ids: ms-001,ms-003  │               │
  │                  │────────────────────────────────────────────────────►│
  │                  │                 │                   │               │
  │                  │◄──── 只返回 ms-001, ms-003 的数据 ──────────────────│
  │◄── 过滤后的列表 ─│                 │                   │               │
```

**反向查询 SQL：**

```sql
SELECT DISTINCT
    split_part(object_path, '/', 6) AS resource_id  -- 取路径第6段作为 ID
FROM resource_acl
WHERE tenant_id = $1
  AND user_path = ANY($2)           -- 用户本人 + 所属组
  AND object_path LIKE $3           -- 'MemoryStore/Tenants/t-001/MemoryStores/%'
  AND LENGTH(object_path) - LENGTH(REPLACE(object_path, '/', '')) = 5  -- 只取顶层，不含子资源
ORDER BY resource_id;               -- 不分页，全量返回
```

---

#### 模式 B：应用自行过滤（回调模式）

当应用有更复杂的过滤逻辑，或希望自行控制分页时，可**忽略** `X-Allowed-Ids`，主动调用鉴权接口批量查询。

```
Backend App                    AccessManager API       resource_acl DB
  │                                   │                      │
  │  收到请求，忽略 X-Allowed-Ids      │                      │
  │                                   │                      │
  │  POST /AccessManager/Tenants/     │                      │
  │   t-001/Action/QueryACLs ────────►│                      │
  │  Body: [{user, object_type,       │                      │
  │          page, page_size}]        │                      │
  │                                   │ 反向查询（分页）─────►│
  │◄── [{resource_id, role}, ...] ────│                      │
  │                                   │                      │
  │  应用自行决定返回哪些资源           │                      │
```

**应用在 manifest 中声明使用哪种模式：**

```json
{
  "list_filter_mode": "gateway_inject"  // 或 "app_callback"
}
```

> `gateway_inject`（默认）：ext_proc 注入 X-Allowed-Ids，无条数限制，应用自行决定是否使用。
> `app_callback`：应用忽略 X-Allowed-Ids，主动调用 QueryACLs 接口。

---

### 5.3 场景三：自定义角色 + 回调鉴权

用户持有应用自定义角色（如 `DataAgent/Tenants/System/Roles/Backuper`），pep-proxy 无法本地判断，触发回调。

```
Client        Gateway       pep-proxy      resource_acl DB   DataAgent App
  │               │               │               │                │
  │  POST /DataAgent/Tenants/     │               │                │
  │   t-001/DataAgentDBs/         │               │                │
  │   db-001/Backup ─────────────►│               │                │
  │               │  ext_authz ──►│               │                │
  │               │               │               │                │
  │               │      [1] 解析 JWT + URL        │                │
  │               │               │──query()─────►│                │
  │               │               │◄──role_path───│                │
  │               │               │  =DataAgent/  │                │
  │               │               │   .../Backuper│                │
  │               │               │               │                │
  │               │      [2] role.namespace        │                │
  │               │          = "DataAgent"         │                │
  │               │          ≠ "AccessManager"     │                │
  │               │          → 触发回调            │                │
  │               │               │                                 │
  │               │               │──POST /DataAgent/Tenants/       │
  │               │               │   t-001/Action/Authorize───────►│
  │               │               │   {user, object, role, action}  │
  │               │               │◄────────────────{allowed:true}──│
  │               │               │               │                │
  │               │◄──allow=true──│               │                │
  │◄──转发后端────│               │               │                │
```

**回调约定（无需在 manifest 中预先声明 callback_url）：**

pep-proxy 遇到非 `AccessManager` 命名空间的角色时，按以下约定路径回调，由应用自行判断：

```
POST <base_url>/<Namespace>/Tenants/<TenantID>/Action/Authorize
```

例如 DataAgent 的 base_url 为 `https://dataagent.example.com`，则回调地址为：

```
POST https://dataagent.example.com/DataAgent/Tenants/t-001/Action/Authorize
```

**回调请求体：**

```json
{
  "user":        "AccessManager/Tenants/t-001/Users/bob",
  "object":      "DataAgent/Tenants/t-001/DataAgentDBs/db-001",
  "role":        "DataAgent/Tenants/System/Roles/Backuper",
  "action":      "POST",
  "action_name": "Backup"
}
```

**回调响应体：**

```json
{ "allowed": true,  "reason": "Backuper role permits Backup action" }
```

**调用 Authorize 回调不需要额外权限校验**（pep-proxy 使用服务账号调用，应用自行验证请求合法性）。

---

### 5.4 场景四：批量 ACL 查询（QueryACLs PUT）

管理员或应用批量查询多个三元组的权限结果。

```
Caller              AccessManager / App
  │                        │
  │  PUT /<Namespace>/Tenants/<ID>/Action/QueryACLs
  │  Body: [{user,object,role}, ...]
  │───────────────────────►│
  │                        │ 逐条查询 resource_acl
  │                        │ + 前缀匹配
  │◄───────────────────────│
  │  [{allowed:true,...},  │
  │   {allowed:false,...}] │
```

**请求体（数组）：**

```json
[
  {
    "properties": {
      "user":   "AccessManager/Tenants/t-001/Groups/dev-team",
      "object": "MemoryStore/Tenants/t-001/MemoryStores/ms-001",
      "role":   "AccessManager/Tenants/System/Roles/Contributor"
    }
  },
  {
    "properties": {
      "user":   "AccessManager/Tenants/t-001/Users/alice",
      "object": "DataAgent/Tenants/t-001/DataAgentDBs/db-001/Tables/tbl-001",
      "role":   "AccessManager/Tenants/System/Roles/Owner"
    }
  }
]
```

**响应体：**

```json
[
  { "allowed": true,  "matched_object": "MemoryStore/Tenants/t-001/MemoryStores/ms-001" },
  { "allowed": false, "reason": "No ACL entry found" }
]
```

---

### 5.5 场景五：资源创建后自动写入 ACL

用户创建新资源，resource-sync 通过 ext_proc 监听 2xx 响应，自动为创建者写入 Owner ACL。

```
Client      Gateway(ext_proc)   Backend App   resource-sync   resource_acl DB
  │                │                 │               │               │
  │  PUT /MemoryStore/Tenants/       │               │               │
  │   t-001/MemoryStores/ms-new ────►│               │               │
  │                │──转发请求───────►│               │               │
  │                │◄──201 Created───│               │               │
  │                │  {id:"ms-new"}  │               │               │
  │                │                 │               │               │
  │                │ ext_proc 拦截响应│               │               │
  │                │ 提取 creator=alice, id=ms-new   │               │
  │                │─────────────────────────────────►               │
  │                │                 │               │               │
  │                │                 │               │──INSERT ACL──►│
  │                │                 │               │  user=alice   │
  │                │                 │               │  obj=ms-new   │
  │                │                 │               │  role=Owner   │
  │                │                 │               │               │
  │◄──201 Created──│                 │               │               │
```

**自动写入的 ACL 记录：**

```
user_path:   AccessManager/Tenants/t-001/Users/alice
object_path: MemoryStore/Tenants/t-001/MemoryStores/ms-new
role_path:   AccessManager/Tenants/System/Roles/Owner
```

资源删除成功后，级联清理所有以该 object_path 为前缀的 ACL 记录。

---

### 5.6 场景六：ACL 写入（权限创建）

ACL 写入有三种来源，校验规则不同。

#### 来源 A：资源创建时 ext_proc 自动写入（无需人工操作）

```
Creator       Gateway(ext_proc)   Backend App   resource-sync   resource_acl DB
  │                  │                 │               │               │
  │  PUT /MemoryStore/Tenants/         │               │               │
  │   t-001/MemoryStores/ms-new ──────►│               │               │
  │                  │──转发──────────►│               │               │
  │                  │◄──201 Created───│               │               │
  │                  │                 │               │               │
  │                  │ ext_proc 拦截响应，提取 creator + resource_id    │
  │                  │────────────────────────────────►│               │
  │                  │                 │               │──INSERT ACL──►│
  │                  │                 │               │  user=creator │
  │                  │                 │               │  obj=ms-new   │
  │                  │                 │               │  role=Owner   │
  │◄──201 Created────│                 │               │               │
```

创建者自动成为资源 Owner，无需额外操作。

---

#### 来源 B：Owner 手动授权给其他用户/组

资源 Owner 将自己持有的资源授权给其他用户或组，AccessManager 在写入前做四项校验：

```
Alice(Owner)    AccessManager API    resource_acl DB
  │                    │                   │
  │  PUT /AccessManager/Tenants/           │
  │   t-001/ACLs ─────►│                   │
  │  Body:{            │                   │
  │   user:dev-team,   │                   │
  │   object:ms-001,   │                   │
  │   role:Contributor │                   │
  │  }                 │                   │
  │                    │                   │
  │                    │ [1] 查 ACL:       │
  │                    │  alice 对 ms-001  │
  │                    │  是否有 Owner? ──►│
  │                    │◄── 是 ────────────│
  │                    │                   │
  │                    │ [2] 被授予的 role │
  │                    │  ≤ 操作者 role?   │
  │                    │  Contributor≤Owner│
  │                    │  → 通过           │
  │                    │                   │
  │                    │ [3] user_path 格式│
  │                    │  合法? → 通过     │
  │                    │                   │
  │                    │ [4] object_path   │
  │                    │  符合 manifest?   │
  │                    │  → 通过           │
  │                    │                   │
  │                    │ [5] UPSERT ACL ──►│
  │◄── 200 OK ─────────│                   │
```

**四项校验规则：**

| 校验 | 规则 | 失败返回 |
|---|---|---|
| 操作者权限 | 操作者对 object 必须有 Owner 角色（前缀匹配） | 403 |
| 不越权授权 | 被授予的 role 不能高于操作者自身 role | 403 |
| user_path 格式 | 必须是 `AccessManager/Tenants/<tid>/Users\|Groups/...` | 400 |
| object_path 合法性 | 必须符合某个已注册 Namespace 的 manifest 路径规范 | 400 |

---

#### 来源 C：管理员直接写入（跳过 Owner 校验）

`tenant-admins` 或 `master-admins` 成员可对任意资源授权，跳过 Owner 校验：

```
TenantAdmin     AccessManager API    resource_acl DB
  │                    │                   │
  │  PUT /AccessManager/Tenants/           │
  │   t-001/ACLs ─────►│                   │
  │  Body:{任意三元组}  │                   │
  │                    │                   │
  │                    │ [1] 操作者在      │
  │                    │  tenant-admins 或 │
  │                    │  master-admins?   │
  │                    │  → 是，跳过       │
  │                    │    Owner 校验     │
  │                    │                   │
  │                    │ [2] UPSERT ACL ──►│
  │◄── 200 OK ─────────│                   │
```

---

#### ACL 管理 API 汇总

```http
# 授予权限（批量 UPSERT，同一 user+object 已有记录则覆盖 role）
PUT /AccessManager/Tenants/{tid}/ACLs
Body: {
  "acls": [
    {
      "user":   "AccessManager/Tenants/t-001/Groups/dev-team",
      "object": "MemoryStore/Tenants/t-001/MemoryStores/ms-001",
      "role":   "AccessManager/Tenants/System/Roles/Contributor"
    }
  ]
}

# 查询某对象的所有 ACL
GET /AccessManager/Tenants/{tid}/ACLs?object=MemoryStore/Tenants/t-001/MemoryStores/ms-001

# 查询某用户的所有权限
GET /AccessManager/Tenants/{tid}/ACLs?user=AccessManager/Tenants/t-001/Users/alice

# 删除权限（先删后增，不支持 PATCH）
DELETE /AccessManager/Tenants/{tid}/ACLs
Body: { "user": "...", "object": "..." }
```

---

## 6. 完整鉴权流程（代码实现逻辑图）

```
┌──────────────────────────────────────────────────────────────────┐
│  pep-proxy: check_authz(request)                                 │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Step 1: 身份解析                                         │   │
│  │  jwt       = parse_jwt(Authorization header)             │   │
│  │  user_path = jwt["sub"]   # AccessManager/Tenants/...   │   │
│  │  groups    = jwt["groups"]                               │   │
│  │  tenant_id = jwt["tenant_id"]                           │   │
│  └─────────────────────────┬────────────────────────────────┘   │
│                            │                                     │
│  ┌─────────────────────────▼────────────────────────────────┐   │
│  │  Step 2: URL 解析                                         │   │
│  │  namespace, tenant, object_path = parse_url(request)    │   │
│  │  action = derive_action(method, path_suffix)             │   │
│  │    GET        → read / list                              │   │
│  │    PUT        → create                                   │   │
│  │    PATCH      → update                                   │   │
│  │    DELETE     → delete                                   │   │
│  │    POST+suffix→ action_name                              │   │
│  └─────────────────────────┬────────────────────────────────┘   │
│                            │                                     │
│  ┌─────────────────────────▼────────────────────────────────┐   │
│  │  Step 3: 管理员组快速通过                                  │   │
│  │  if master-admins ∈ groups                               │   │
│  │     or tenant-admins ∈ groups:                           │   │
│  │       return ALLOW                                       │   │
│  └─────────────────────────┬────────────────────────────────┘   │
│                            │                                     │
│  ┌─────────────────────────▼────────────────────────────────┐   │
│  │  Step 4: 查询 resource_acl（前缀匹配）                    │   │
│  │  prefixes = build_object_prefixes(object_path)           │   │
│  │  role = query_acl(tenant, user, groups, prefixes)        │   │
│  │  if role is None → return DENY 403                       │   │
│  └──────────────┬──────────────────────────────────────────┘   │
│                 │                                                │
│        ┌────────▼─────────────────────────┐                     │
│        │  role.namespace == "AccessManager"?                    │
│        └────────┬──────────────┬──────────┘                     │
│                 │ YES          │ NO                              │
│  ┌──────────────▼──────────┐  ┌▼────────────────────────────┐  │
│  │  Default Role 本地判断  │  │  Custom Role → 回调          │  │
│  │                         │  │                              │  │
│  │  Owner   → all ✓        │  │  url = base_url + "/"        │  │
│  │  Contrib → no DELETE    │  │    + ns + "/Tenants/" + tid  │  │
│  │  Viewer  → GET/LIST     │  │    + "/Action/Authorize"     │  │
│  │                         │  │  resp = POST url {user,      │  │
│  │  Viewer  → GET/LIST     │  │    object, role, action}     │  │
│  │                         │  │  allowed = resp["allowed"]   │  │
│  │  allowed = matrix       │  │                              │  │
│  │    [role][action]       │  │  timeout=500ms, fail-close   │  │
│  └──────────────┬──────────┘  └──────────────┬──────────────┘  │
│                 └──────────────────────────────┘                 │
│                             │                                    │
│  ┌──────────────────────────▼──────────────────────────────┐    │
│  │  Step 5: 返回结果                                        │    │
│  │  allowed=true  → 200, Gateway 转发请求到后端             │    │
│  │  allowed=false → 403 Forbidden                          │    │
│  └─────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 7. Manifest 规范

每个应用注册时提供 `manifest.json`，描述资源结构、支持的角色和回调地址。

### 7.1 Manifest 作用

| 用途 | 说明 |
|---|---|
| 自动生成权限配置界面 | 前端从 manifest 读取资源树和角色列表，动态渲染授权表单 |
| 校验 ACL 合法性 | 写入 ACL 时验证 object_path 符合该 Namespace 的路径规范 |
| 自定义角色回调约定 | 声明应用支持自定义角色，pep-proxy 遇到非 AccessManager 角色时按约定路径回调 |
| 自定义角色注册 | 声明应用自定义角色及其语义 |
| **初始 ACL 同步模板** | 声明每个资源类型的默认权限，AccessManager 在租户创建或应用注册时自动展开写入 resource_acl |

### 7.2 Manifest 示例（MemoryStore）

```json
{
  "namespace": "MemoryStore",
  "base_url": "https://memorystore.example.com",
  "_comment_callback": "callback_url 不在 manifest 中预先声明。应用若支持自定义角色，pep-proxy 按约定路径 POST <base_url>/<Namespace>/Tenants/<tid>/Action/Authorize 回调，由应用自行判断是否允许。",
  "resources": [
    {
      "type": "MemoryStores",
      "path_pattern": "/MemoryStore/Tenants/{tenantId}/MemoryStores/{storeId}",
      "methods": ["GET", "PUT", "PATCH", "DELETE"],
      "actions": [
        {
          "name": "Backup",
          "path_suffix": "/Backup",
          "http_method": "POST",
          "required_role": "MemoryStore/Tenants/System/Roles/Backuper"
        }
      ],
      "children": [
        {
          "type": "Memories",
          "path_pattern": "/MemoryStore/Tenants/{tenantId}/MemoryStores/{storeId}/Memories/{memoryId}",
          "methods": ["GET", "PUT", "PATCH", "DELETE"],
          "children": [
            {
              "type": "Chunks",
              "path_pattern": "...Memories/{memoryId}/Chunks/{chunkId}",
              "methods": ["GET", "PUT", "DELETE"]
            }
          ]
        }
      ]
    }
  ],
  "supported_roles": [
    "AccessManager/Tenants/System/Roles/Owner",
    "AccessManager/Tenants/System/Roles/Contributor",
    "AccessManager/Tenants/System/Roles/Viewer",
    "MemoryStore/Tenants/System/Roles/Backuper"
  ],
  "custom_roles": [
    {
      "role_path": "MemoryStore/Tenants/System/Roles/Backuper",
      "allowed_methods": ["POST"],
      "allowed_actions": ["Backup"],
      "description": "允许执行备份操作，不能读写数据"
    }
  ]
}
```

### 7.3 Manifest 驱动权限配置界面的生成逻辑

```
manifest.resources（树结构）
         │
         ▼
┌─────────────────────────────────────────┐
│  前端资源树渲染                          │
│                                         │
│  📁 MemoryStore                         │
│    📁 MemoryStores                      │
│      📄 {storeId}  ← 用户填写 ID        │
│        📁 Memories                      │
│          📄 {memoryId}                  │
│            📁 Chunks                   │
│              📄 {chunkId}              │
└─────────────────────────────────────────┘
         │ 用户选择节点 + 填写 ID
         ▼
  构造 object_path:
  MemoryStore/Tenants/{tid}/MemoryStores/{storeId}
         │
         ▼
┌─────────────────────────────────────────┐
│  角色下拉（来自 supported_roles）        │
│  ○ Owner                                │
│  ○ Contributor                          │
│  ○ Viewer                               │
│  ○ Backuper  ← 自定义角色               │
└─────────────────────────────────────────┘
         │ 提交
         ▼
  PUT /AccessManager/Tenants/{tid}/ACLs
  Body: {user, object, role}
```

---

### 7.4 Manifest 中的 default_acl 字段

Manifest 通过 `default_acl` 字段声明每个资源类型的默认权限模板，`{tenantId}` 是占位符，由 AccessManager 在同步时替换为真实租户 ID：

```json
{
  "namespace": "MemoryStore",
  "resources": [
    {
      "type": "MemoryStores",
      "path_pattern": "/MemoryStore/Tenants/{tenantId}/MemoryStores/{storeId}",
      "default_acl": [
        {
          "user_template":   "AccessManager/Tenants/{tenantId}/Groups/all-users",
          "object_template": "MemoryStore/Tenants/{tenantId}/MemoryStores",
          "role_path":       "AccessManager/Tenants/System/Roles/Contributor"
        },
        {
          "user_template":   "AccessManager/Tenants/{tenantId}/Groups/tenant-admins",
          "object_template": "MemoryStore/Tenants/{tenantId}/MemoryStores",
          "role_path":       "AccessManager/Tenants/System/Roles/Owner"
        }
      ]
    }
  ],
  "list_filter_mode": "gateway_inject",
  "max_allowed_ids_per_response": 500
}
```

**字段说明：**

| 字段 | 说明 |
|---|---|
| `user_template` | 主体路径模板，`{tenantId}` 为占位符 |
| `object_template` | 对象路径模板，**无具体资源 ID**（类型级），`{tenantId}` 为占位符 |
| `role_path` | 角色路径，与租户无关，直接使用完整路径 |

---

### 7.5 初始 ACL 同步规则

#### 同步触发时机

有两个互补的触发点，保证任意注册顺序下都不会漏写：

```
触发点 A：新应用注册（manifest 首次写入）
  MemoryStore 注册 manifest
       │
       ▼
  AccessManager 查询所有已存在租户
  [t-001, t-002, t-003, ...]
       │
       ▼
  对每个租户，将 default_acl 模板中的
  {tenantId} 替换为真实 tenant_id
       │
       ▼
  批量 INSERT INTO resource_acl
  （ON CONFLICT DO NOTHING，幂等）


触发点 B：新租户创建
  tenant t-004 创建
       │
       ▼
  AccessManager 查询所有已注册 manifest
  [MemoryStore, DataAgent, DataBase, ...]
       │
       ▼
  对每个 manifest 的 default_acl，
  将 {tenantId} 替换为 t-004
       │
       ▼
  批量 INSERT INTO resource_acl
  （ON CONFLICT DO NOTHING，幂等）
```

#### 同步时序图

```
场景：先有租户 t-001，后注册 MemoryStore

t-001 已存在    AccessManager    app_manifests    resource_acl DB
     │                │                │                │
     │                │                │                │
     │  MemoryStore 注册 manifest ─────►│                │
     │                │                │                │
     │                │ 查询所有租户    │                │
     │                │◄── [t-001] ────│                │
     │                │                │                │
     │                │ 展开模板:       │                │
     │                │  {tenantId}    │                │
     │                │  → t-001       │                │
     │                │                │                │
     │                │ INSERT ACL ────────────────────►│
     │                │  user=.../t-001/Groups/all-users│
     │                │  obj=.../t-001/MemoryStores     │
     │                │  role=Contributor               │
     │                │                │                │
     │                │  INSERT ACL ───────────────────►│
     │                │  user=.../t-001/Groups/tenant-admins
     │                │  obj=.../t-001/MemoryStores     │
     │                │  role=Owner                     │


场景：MemoryStore 已注册，新建租户 t-002

AccessManager    app_manifests    resource_acl DB
     │                │                │
     │  创建租户 t-002 │                │
     │                │                │
     │ 查询所有 manifest ──────────────►│
     │◄── [MemoryStore, DataAgent, ...] │
     │                │                │
     │ 对每个 manifest 展开 default_acl │
     │  {tenantId} → t-002             │
     │                │                │
     │ 批量 INSERT ACL ────────────────►│
     │  (ON CONFLICT DO NOTHING)       │
```

#### 同步的幂等性保证

```sql
-- 同步写入使用 ON CONFLICT DO NOTHING，重复触发不会产生重复记录
INSERT INTO resource_acl (tenant_id, user_path, object_path, role_path)
VALUES ($1, $2, $3, $4)
ON CONFLICT (tenant_id, user_path, object_path) DO NOTHING;
```

#### 能同步的内容 vs 不能同步的内容

| 内容 | 能否同步 | 说明 |
|---|---|---|
| 类型级默认权限（`all-users → Contributor`） | ✓ | manifest 静态声明，展开时填入 tenant_id |
| 管理员组默认权限（`tenant-admins → Owner`） | ✓ | 同上 |
| 具体资源实例的权限 | ✗ | 实例在注册时不存在，由 ext_proc 在创建时写入 |
| 跨租户权限 | ✗ | 设计上不允许 |
| 子资源类型级权限 | 可选 | 若 manifest 中子资源也声明了 default_acl，同样展开写入 |

---

### 8.1 强隔离原则

租户之间完全隔离，不允许任何形式的跨租户资源访问：

| 规则 | 说明 |
|---|---|
| ACL 三元组同租户 | user_path 和 object_path 中的 TenantID 必须相同 |
| 查询强制分区 | 所有 resource_acl 查询必须带 `tenant_id` 条件 |
| 跨租户共享不支持 | 不提供将 t-001 资源授权给 t-002 用户的机制 |
| JWT 校验 | pep-proxy 校验 JWT 中的 tenant_id 与 URL 中的 TenantID 必须一致 |

### 8.2 租户隔离校验流程

```
请求: GET /MemoryStore/Tenants/t-002/MemoryStores/ms-001
JWT:  tenant_id = t-001

pep-proxy:
  URL.tenant_id (t-002) ≠ JWT.tenant_id (t-001)
  → 403 Forbidden（跨租户访问拒绝）
  → 不进入 ACL 查询
```

### 8.3 Contributor 创建顶级资源

Contributor 可以在**本租户内**创建顶级资源，前提是对该资源类型有类型级 ACL：

```
# 类型级 ACL（由 tenant-admins 在用户入驻时预写）
user_path:   AccessManager/Tenants/t-001/Groups/all-users
object_path: MemoryStore/Tenants/t-001/MemoryStores      ← 无具体 ID
role_path:   AccessManager/Tenants/System/Roles/Contributor

# 此后 t-001 租户内任意用户均可：
PUT /MemoryStore/Tenants/t-001/MemoryStores/ms-new       ← 创建顶级资源 ✓
DELETE /MemoryStore/Tenants/t-001/MemoryStores/ms-new    ← 删除 ✗（需 Owner）
```

**类型级 ACL 的写入时机：**
1. 应用在 manifest 中声明 `default_type_acl`，AccessManager 在租户创建时自动写入；
2. 或由 tenant-admins 手动调用 ACL 管理接口写入。

---

## 8. 默认用户组与职责边界

系统内置四类用户组，职责严格分层，不可越权。

### 8.1 master-admins

- **来源**：Keycloak master realm 下的账户
- **当前职责**：持有账号，暂不参与业务鉴权，不执行任何资源操作
- **绕过规则**：不绕过 pep-proxy 的资源级鉴权（与 tenant-admins 不同）
- **用途**：未来用于跨租户管理或平台级运维操作

### 8.2 tenant-admins

- **来源**：每个租户的管理员组，由 master-admins 创建
- **职责**：
  - 创建/管理本租户内的用户和用户组
  - 管理 permission_groups（path 级路径鉴权规则）
  - 写入/撤销 resource_acl 中的权限关系（对任意 object 有 Owner 权限）
  - 在新用户入驻时写入类型级默认 ACL
- **绕过规则**：绕过 pep-proxy 资源级鉴权（直接通过，不查 resource_acl）
- **不能做**：不能跨租户操作，不能修改 master realm 配置

### 8.3 app-admins（应用管理员）

- **来源**：由 tenant-admins 创建，绑定到具体应用（如 `dataagent-admins`）
- **职责**：
  - 管理本应用下具体资源实例的权限（写入/撤销 resource_acl）
  - 对本应用所有资源实例有 Owner 角色
- **绕过规则**：不绕过 pep-proxy 资源级鉴权，但因持有 Owner 角色，所有操作均通过
- **不能做**：不能管理用户/组，不能修改 path 级路径规则

### 8.4 all-users

- **来源**：租户内所有用户自动加入
- **职责**：持有类型级 Contributor ACL（由 manifest default_acl 同步写入），可创建顶级资源
- **绕过规则**：不绕过任何鉴权
- **不能做**：不能删除他人资源，不能管理权限

### 8.5 职责对比

| 能力 | master-admins | tenant-admins | app-admins | all-users |
|---|:---:|:---:|:---:|:---:|
| 跨租户操作 | 未来支持 | ✗ | ✗ | ✗ |
| 管理用户/组 | ✗ | ✓ | ✗ | ✗ |
| 管理 path 级权限组 | ✗ | ✓ | ✗ | ✗ |
| 写入任意资源 ACL | ✗ | ✓ | 本应用 | ✗ |
| 绕过资源级鉴权 | ✗ | ✓ | ✗ | ✗ |
| 创建顶级资源 | ✗ | ✓ | ✓ | ✓（有类型级 ACL） |
| 删除资源 | ✗ | ✓ | ✓（Owner） | ✗ |

---

## 9. 租户隔离与跨租户规则

### 9.1 强隔离原则

租户之间完全隔离，不允许任何形式的跨租户资源访问：

| 规则 | 说明 |
|---|---|
| ACL 三元组同租户 | user_path 和 object_path 中的 TenantID 必须相同 |
| 查询强制分区 | 所有 resource_acl 查询必须带 `tenant_id` 条件 |
| 跨租户共享不支持 | 不提供将 t-001 资源授权给 t-002 用户的机制 |
| JWT 校验 | pep-proxy 校验 JWT 中的 tenant_id 与 URL 中的 TenantID 必须一致 |

### 9.2 租户隔离校验流程

```
请求: GET /MemoryStore/Tenants/t-002/MemoryStores/ms-001
JWT:  tenant_id = t-001

pep-proxy:
  URL.tenant_id (t-002) ≠ JWT.tenant_id (t-001)
  → 403 Forbidden（跨租户访问拒绝）
  → 不进入 ACL 查询
```

### 9.3 Contributor 创建顶级资源

Contributor 可以在**本租户内**创建顶级资源，前提是对该资源类型有类型级 ACL：

```
# 类型级 ACL（由 tenant-admins 在用户入驻时预写）
user_path:   AccessManager/Tenants/t-001/Groups/all-users
object_path: MemoryStore/Tenants/t-001/MemoryStores      ← 无具体 ID
role_path:   AccessManager/Tenants/System/Roles/Contributor

# 此后 t-001 租户内任意用户均可：
PUT /MemoryStore/Tenants/t-001/MemoryStores/ms-new       ← 创建顶级资源 ✓
DELETE /MemoryStore/Tenants/t-001/MemoryStores/ms-new    ← 删除 ✗（需 Owner）
```

**类型级 ACL 的写入时机：**
1. 应用在 manifest 中声明 `default_acl`，AccessManager 在租户创建时自动写入；
2. 或由 tenant-admins 手动调用 ACL 管理接口写入。

---

## 10. 应用接入清单

新应用接入统一鉴权系统需提供以下信息：

| 项目 | 说明 | 必须/可选 |
|---|---|---|
| Namespace | 应用唯一标识 | 必须 |
| base_url | 应用服务地址（用于构造 Authorize 回调地址） | 必须 |
| 资源路径层级 | 各资源类型及父子关系（树结构，深度不限） | 必须 |
| 支持的 HTTP 方法 | 每个资源类型支持哪些操作 | 必须 |
| 自定义 Action | Action 名称、路径后缀、所需角色 | 可选 |
| 自定义角色定义 | 角色路径、允许的方法/Action | 可选 |
| Authorize 回调实现 | 应用实现 `POST <base_url>/<NS>/Tenants/<tid>/Action/Authorize` 接口 | 有自定义角色时必须 |
| manifest.json | 上述信息的结构化描述文件 | 必须 |

---

## 11. 待确认事项

| 编号 | 问题 | 建议 |
|---|---|---|
| P1 | resource_acl 字段类型（TEXT vs VARCHAR） | 改为 TEXT，路径长度不可预测 |
| P2 | 前缀匹配候选列表最大深度 | 建议限制 10 级，超出截断 |
| P3 | 回调超时策略 | 500ms 超时 + fail-close（拒绝） |
| P4 | ACL 变更是否需要通知应用缓存失效 | pep-proxy 不缓存，每次实时查询 |
| P5 | 跨 Namespace 的 user_path 引用 | 统一使用 AccessManager Namespace，其他 Namespace 不允许定义用户 |
| P6 | role_path 为空的三元组语义 | 空 role = 仅记录关联关系，不授予任何操作权限 |


