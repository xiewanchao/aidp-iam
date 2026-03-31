# IAM 资源实例级鉴权设计文档

> 版本：v1.0 | 日期：2026-03-31

---

## 1 背景与动机

### 1.1 现有架构

当前系统采用经典 RBAC 模型，鉴权链条为：

```
User → Group → Role → Policy → Resource（URL 路径前缀）
```

OPA 三层授权逻辑：

| 层级 | 判断 | 效果 |
|------|------|------|
| 第 1 层 | super-admin（master realm token） | 跨租户全放行 |
| 第 2 层 | tenant-admin + 本租户 | 本租户全放行 |
| 第 3 层 | 普通用户 → role UUID → role_binding → policy.rules → 路径匹配 | 资源**类型**级放行/拒绝 |

### 1.2 问题

1. **资源管理是静态的** — policy.rules 中的 resource 是手写的 URL 路径字符串，没有资源注册机制，IAM 不知道系统里实际存在哪些资源。
2. **粒度不够** — 第 3 层只能控制"能不能访问知识库"，不能控制"能不能访问这个具体的知识库"。
3. **各应用需求差异大**：
   - **记忆库**：简单场景，template 由 tenant-admin 管理，memory 用户私有，不需要分享。
   - **知识库**：协作场景，用户创建后成为 owner，可授权他人读写，类似 Google Drive。
4. **Policy + Role Binding 模型过重** — 对于资源实例级权限，每个资源实例都要创建 policy 再绑定 role，链条太长。

### 1.3 目标

- 统一为 **ACL（访问控制列表）模型**，干掉 policy 和 role_binding 表
- 支持资源**动态注册**（应用创建资源时通知 IAM）
- 支持资源**实例级权限**（owner/writer/reader/creator/denied）
- 兼容粗粒度和细粒度场景，一套模型覆盖所有应用
- 绑定主体支持 user 和 group

---

## 2 整体架构

### 2.1 改动前 vs 改动后

```
改动前（RBAC）:
  User → Group → Role → Policy(rules: [{resource, effect}]) → URL 路径匹配
                          ↑                    ↑
                    role_bindings 表        policies 表

改动后（ACL）:
  User → Group ──┐
  User ──────────┼→ Resource Instance → Role(owner/writer/reader/creator/denied)
                  │       ↑
                  │  resources 表 + resource_acls 表
                  │
                  └→ Resource Type(*) → Role (粗粒度，等价于以前的 RBAC)
```

### 2.2 请求鉴权流

```
客户请求
  │
  ▼
AgentGateway (统一入口)
  │
  ▼ gRPC ext-authz
pep-proxy
  │ 1. 解析 JWT → 提取 user_id, tenant_id, system_roles, group_ids
  │ 2. 调用 OPA
  ▼
OPA Rego 统一判断
  │
  ├─ super-admin?          → ✅ 放行
  ├─ tenant-admin + 本租户? → ✅ 放行
  └─ 普通用户?
       │
       ├─ 检查 denied        → ❌ 有 denied 直接拒绝
       ├─ 收集实例级 ACL 角色  → resource_type + resource_id
       ├─ 收集类型级 ACL 角色  → resource_type + "*"
       ├─ 取最高权限角色
       └─ 角色是否允许该 action → ✅ 放行 / ❌ 拒绝
```

### 2.3 组件职责变更

| 组件 | 改动前 | 改动后 |
|------|--------|--------|
| **Keycloak** | 管理 user/group/role | 不变，继续管理 user/group/系统角色 |
| **keycloak-proxy** | tenant/user/group/role CRUD + IDP 管理 | 不变 + 新增资源注册 API、ACL 管理 API |
| **bundle-server** | 从 PostgreSQL 读 policies + role_bindings，推送到 OPA | 从 PostgreSQL 读 resources + resource_acls + group_members，推送到 OPA |
| **pep-proxy** | ext-authz + policy CRUD API | ext-authz（Rego 逻辑替换） + 资源/ACL CRUD API |
| **OPA** | Rego 三层判断（admin/admin/policy匹配） | Rego 统一 ACL 判断 |
| **OPAL** | 同步 policy 数据变更 | 同步 ACL 数据变更 |

---

## 3 数据模型

### 3.1 数据库表设计（PostgreSQL）

#### 3.1.1 resources — 资源注册表

应用创建资源时动态注册到 IAM，IAM 知道系统中存在哪些资源。

```sql
CREATE TABLE resources (
    tenant_id      VARCHAR(128)  NOT NULL,
    resource_type  VARCHAR(128)  NOT NULL,   -- 资源类型：'kb', 'memory', 'template' 等
    resource_id    VARCHAR(256)  NOT NULL,   -- 资源实例 ID，由应用生成
    display_name   VARCHAR(512),             -- 显示名称
    created_by     VARCHAR(128)  NOT NULL,   -- 创建者 user_id
    created_at     TIMESTAMP     NOT NULL DEFAULT NOW(),

    PRIMARY KEY (tenant_id, resource_type, resource_id)
);

CREATE INDEX idx_resources_tenant ON resources (tenant_id);
CREATE INDEX idx_resources_type   ON resources (tenant_id, resource_type);
CREATE INDEX idx_resources_owner  ON resources (tenant_id, created_by);
```

#### 3.1.2 resource_acls — 访问控制列表

定义谁（user/group）对哪个资源有什么角色。

```sql
CREATE TABLE resource_acls (
    tenant_id      VARCHAR(128)  NOT NULL,
    resource_type  VARCHAR(128)  NOT NULL,
    resource_id    VARCHAR(256)  NOT NULL,   -- '*' 表示该类型下所有实例（类型级通配符）
    subject_type   VARCHAR(16)   NOT NULL,   -- 'user' | 'group'
    subject_id     VARCHAR(128)  NOT NULL,   -- user_id 或 Keycloak group name
    role           VARCHAR(16)   NOT NULL,   -- 'owner' | 'writer' | 'reader' | 'creator' | 'denied'
    granted_by     VARCHAR(128)  NOT NULL,   -- 操作人 user_id
    created_at     TIMESTAMP     NOT NULL DEFAULT NOW(),

    PRIMARY KEY (tenant_id, resource_type, resource_id, subject_type, subject_id),

    CONSTRAINT chk_subject_type CHECK (subject_type IN ('user', 'group')),
    CONSTRAINT chk_role CHECK (role IN ('owner', 'writer', 'reader', 'creator', 'denied'))
);

CREATE INDEX idx_acl_tenant    ON resource_acls (tenant_id);
CREATE INDEX idx_acl_resource  ON resource_acls (tenant_id, resource_type, resource_id);
CREATE INDEX idx_acl_subject   ON resource_acls (tenant_id, subject_type, subject_id);
```

#### 3.1.3 废弃的表

以下表不再使用，可在迁移后删除：

| 废弃表 | 原用途 | 替代方案 |
|--------|--------|---------|
| `policies` | 存储 policy rules（URL 路径 + effect） | resource_acls 的通配符（`*`）条目 |
| `role_policy_bindings` | role UUID → policy ID 的 1:1 绑定 | resource_acls 直接绑定 group/user 到资源 |

### 3.2 角色体系

五种内置角色，权限由高到低：

| 角色 | 权限 | 优先级 | 说明 |
|------|------|--------|------|
| `owner` | create + read + write + delete + manage_acl | 5 | 资源所有者，可管理 ACL |
| `writer` | create + read + write + delete | 4 | 可读写删除 |
| `reader` | read | 3 | 只读 |
| `creator` | create | 2 | 只能创建新资源（创建后自动成为该实例 owner） |
| `denied` | 无 | 最高（否决权） | 显式拒绝，优先级高于一切 |

**权限包含关系：** owner ⊃ writer ⊃ reader，creator 独立。

**denied 的特殊性：** denied 是否决票，只要匹配到任何一条 denied，无论其他条目给了什么角色，一律拒绝。

### 3.3 通配符 vs 实例 — 一张表两种粒度

| resource_id | 含义 | 等价于 |
|-------------|------|--------|
| `*` | 该 resource_type 下所有实例 | 以前的 RBAC（"data-team 能访问知识库服务"） |
| `kb-001` | 具体资源实例 | ACL（"data-team 能读这个知识库"） |

**规则：实例级条目覆盖类型级条目时取最高权限（denied 除外）。**

### 3.4 OPA 数据结构

bundle-server 将 PostgreSQL 数据转换为以下结构推送到 OPA：

```json
{
  "tenants": {
    "aidp": {
      "acls": {
        "kb": {
          "*": [
            {"subject_type": "group", "subject_id": "data-team",  "role": "creator"},
            {"subject_type": "group", "subject_id": "dev-team",   "role": "creator"}
          ],
          "kb-001": [
            {"subject_type": "user",  "subject_id": "zhangsan",   "role": "owner"},
            {"subject_type": "user",  "subject_id": "lisi",       "role": "writer"},
            {"subject_type": "group", "subject_id": "data-team",  "role": "reader"}
          ]
        },
        "memory": {
          "*": [
            {"subject_type": "group", "subject_id": "all-users",  "role": "creator"}
          ],
          "mem-001": [
            {"subject_type": "user",  "subject_id": "zhangsan",   "role": "owner"}
          ]
        },
        "template": {
          "*": [
            {"subject_type": "group", "subject_id": "all-users",      "role": "reader"},
            {"subject_type": "group", "subject_id": "tenant-admins",  "role": "owner"}
          ]
        }
      },
      "group_members": {
        "data-team":     ["zhangsan", "lisi"],
        "dev-team":      ["wangwu", "zhaoliu"],
        "all-users":     ["zhangsan", "lisi", "wangwu", "zhaoliu"],
        "tenant-admins": ["admin"]
      }
    }
  }
}
```

**group_members 数据来源：** 从 Keycloak Admin API 定期同步（或通过 OPAL 数据源实时同步）。

---

## 4 OPA Rego 策略

### 4.1 统一鉴权策略

```rego
package authz

import future.keywords.in

default allow = false

# ===================================================================
# 系统角色短路
# ===================================================================

# super-admin：跨租户全放行
allow {
    "super-admin" in input.roles
}

# tenant-admin：本租户全放行
allow {
    "tenant-admin" in input.roles
    input.tenant_id == input.token_tenant_id
}

# ===================================================================
# ACL 鉴权（普通用户）
# ===================================================================

allow {
    not is_denied
    role := effective_role
    role_permits(role, input.action)
}

# -------------------------------------------------------------------
# 角色-操作 权限映射
# -------------------------------------------------------------------
role_permits("owner", _)        = true
role_permits("writer", action)  { action in ["create", "read", "write", "delete"] }
role_permits("reader", action)  { action in ["read"] }
role_permits("creator", action) { action in ["create"] }

# -------------------------------------------------------------------
# 用户所属的所有 group（从 group_members 反查）
# -------------------------------------------------------------------
user_groups[g] {
    some g, members in data.tenants[input.tenant_id].group_members
    input.user_id in members
}

# -------------------------------------------------------------------
# 收集匹配的角色
# -------------------------------------------------------------------

# 实例级 ACL：精确匹配 resource_type + resource_id
instance_roles[role] {
    some entry in data.tenants[input.tenant_id].acls[input.resource_type][input.resource_id]
    subject_matches(entry)
    role := entry.role
}

# 类型级 ACL：通配符 resource_type + "*"
wildcard_roles[role] {
    some entry in data.tenants[input.tenant_id].acls[input.resource_type]["*"]
    subject_matches(entry)
    role := entry.role
}

# 所有匹配的角色 = 实例级 ∪ 类型级
all_matched_roles := instance_roles | wildcard_roles

# -------------------------------------------------------------------
# subject 匹配
# -------------------------------------------------------------------
subject_matches(entry) {
    entry.subject_type == "user"
    entry.subject_id == input.user_id
}

subject_matches(entry) {
    entry.subject_type == "group"
    entry.subject_id in user_groups
}

# -------------------------------------------------------------------
# denied 检查（最高优先级）
# -------------------------------------------------------------------
is_denied {
    "denied" in all_matched_roles
}

# -------------------------------------------------------------------
# 有效角色 = 所有非 denied 角色中优先级最高的
# -------------------------------------------------------------------
role_priority := {
    "owner":   5,
    "writer":  4,
    "reader":  3,
    "creator": 2
}

effective_role := role {
    candidates := {r | some r in all_matched_roles; r != "denied"}
    count(candidates) > 0
    role := [r | some r in candidates; role_priority[r] >= role_priority[x]; some x in candidates][0]
}
```

### 4.2 ext-authz 输入结构

pep-proxy 解析请求后，构造以下输入传给 OPA：

```json
{
  "input": {
    "user_id":          "zhangsan",
    "tenant_id":        "aidp",
    "token_tenant_id":  "aidp",
    "roles":            ["normal-user"],
    "resource_type":    "kb",
    "resource_id":      "kb-001",
    "action":           "read",
    "method":           "GET",
    "path":             "/aidp/kb-service/api/v1/kb/kb-001/docs"
  }
}
```

**resource_type 和 resource_id 的提取方式：**

- 方案 A（推荐）：应用在请求 Header 中传递 `X-Resource-Type` 和 `X-Resource-Id`
- 方案 B：pep-proxy 根据 URL 路径规则解析（需约定路径格式）

**action 的提取方式：**

| HTTP Method | action |
|-------------|--------|
| POST（创建类） | create |
| GET | read |
| PUT / PATCH | write |
| DELETE | delete |

---

## 5 API 设计

### 5.1 资源管理 API

基础路径：`/api/v1/resources`

#### 5.1.1 注册资源

应用在创建资源时调用，IAM 自动为创建者添加 owner ACL。

```
POST /api/v1/resources

Request:
{
    "tenant_id":      "aidp",
    "resource_type":  "kb",
    "resource_id":    "kb-001",
    "display_name":   "机器学习文档",
    "created_by":     "zhangsan"
}

Response: 201
{
    "tenant_id":      "aidp",
    "resource_type":  "kb",
    "resource_id":    "kb-001",
    "display_name":   "机器学习文档",
    "created_by":     "zhangsan",
    "created_at":     "2026-03-31T10:00:00Z",
    "acl": {
        "subject_type": "user",
        "subject_id":   "zhangsan",
        "role":         "owner"
    }
}
```

**自动行为：** 插入 resources 记录 + 插入 resource_acls 记录（created_by → owner）。

#### 5.1.2 查询资源列表

```
GET /api/v1/resources?tenant_id=aidp&resource_type=kb

Response: 200
{
    "items": [
        {
            "resource_type": "kb",
            "resource_id":   "kb-001",
            "display_name":  "机器学习文档",
            "created_by":    "zhangsan",
            "created_at":    "2026-03-31T10:00:00Z"
        }
    ],
    "total": 1
}
```

#### 5.1.3 注销资源

```
DELETE /api/v1/resources/{resource_type}/{resource_id}?tenant_id=aidp

Response: 204
```

**自动行为：** 删除 resources 记录 + 级联删除该资源所有 resource_acls 记录。

### 5.2 ACL 管理 API

基础路径：`/api/v1/resources/{resource_type}/{resource_id}/acl`

**权限要求：** 调用者必须是该资源的 owner 或 tenant-admin。

#### 5.2.1 查看资源 ACL

```
GET /api/v1/resources/kb/kb-001/acl?tenant_id=aidp

Response: 200
{
    "resource_type": "kb",
    "resource_id":   "kb-001",
    "acl": [
        {"subject_type": "user",  "subject_id": "zhangsan",  "role": "owner",  "granted_by": "system"},
        {"subject_type": "user",  "subject_id": "lisi",      "role": "writer", "granted_by": "zhangsan"},
        {"subject_type": "group", "subject_id": "data-team", "role": "reader", "granted_by": "zhangsan"}
    ]
}
```

#### 5.2.2 添加/修改 ACL 条目

```
PUT /api/v1/resources/kb/kb-001/acl?tenant_id=aidp

Request:
{
    "subject_type": "group",
    "subject_id":   "data-team",
    "role":         "reader"
}

Response: 200
```

**语义：** UPSERT — 如果该 subject 已有 ACL 条目，更新角色；否则新增。

#### 5.2.3 移除 ACL 条目

```
DELETE /api/v1/resources/kb/kb-001/acl?tenant_id=aidp&subject_type=group&subject_id=data-team

Response: 204
```

**约束：** 不允许移除最后一个 owner（资源必须至少有一个 owner）。

### 5.3 类型级 ACL 管理 API

类型级 ACL 使用 `resource_id = *`，由 tenant-admin 在管理界面配置。

```
PUT /api/v1/resources/kb/*/acl?tenant_id=aidp

Request:
{
    "subject_type": "group",
    "subject_id":   "data-team",
    "role":         "creator"
}
```

含义：data-team 组的成员可以创建知识库类型的资源。

### 5.4 废弃的 API

以下 API 在迁移完成后废弃：

| 废弃 API | 原用途 | 替代 |
|----------|--------|------|
| `POST /api/v1/policies` | 创建策略 | 类型级 ACL（`PUT .../*/acl`） |
| `PUT /api/v1/policies/{id}` | 更新策略 | 同上 |
| `DELETE /api/v1/policies/{id}` | 删除策略 | 同上 |
| `POST /api/v1/roles/{id}/policy` | 角色绑策略 | 不再需要，ACL 直接绑 group/user |
| `GET /api/v1/policies/templates` | 策略模板 | 不再需要 |

---

## 6 应用场景详解

### 6.1 记忆库（简单型 — 用户私有资源）

**资源类型：** `memory`（用户私有记忆）、`template`（公共模板）

#### 初始配置（tenant-admin 操作）

```sql
-- 所有用户可以创建 memory（创建后自动 owner，只有自己能访问）
INSERT INTO resource_acls VALUES ('aidp', 'memory',   '*', 'group', 'all-users',     'creator', 'admin', NOW());

-- 所有用户可以读 template
INSERT INTO resource_acls VALUES ('aidp', 'template', '*', 'group', 'all-users',     'reader',  'admin', NOW());

-- tenant-admin 组是 template 的 owner（可增删改）
INSERT INTO resource_acls VALUES ('aidp', 'template', '*', 'group', 'tenant-admins', 'owner',   'admin', NOW());
```

#### 用户操作流程

```
张三创建一条 memory：
  1. 记忆库应用调用 POST /api/v1/resources
     {resource_type: "memory", resource_id: "mem-001", created_by: "zhangsan"}
  2. IAM 自动添加 ACL: (memory, mem-001, user, zhangsan, owner)

张三读自己的 memory：
  OPA 判断:
    user_groups(zhangsan) = [data-team, all-users]
    实例级: memory:mem-001 → user:zhangsan → owner ✅
    effective_role = owner
    role_permits(owner, read) → ✅ 放行

李四尝试读张三的 memory：
  OPA 判断:
    实例级: memory:mem-001 → 没有 lisi
    类型级: memory:* → all-users → creator
    effective_role = creator
    role_permits(creator, read) → ❌ 拒绝（creator 只能 create）
```

### 6.2 知识库（协作型 — 资源分享）

**资源类型：** `kb`（知识库）

#### 初始配置（tenant-admin 操作）

```sql
-- data-team 和 dev-team 可以创建知识库
INSERT INTO resource_acls VALUES ('aidp', 'kb', '*', 'group', 'data-team', 'creator', 'admin', NOW());
INSERT INTO resource_acls VALUES ('aidp', 'kb', '*', 'group', 'dev-team',  'creator', 'admin', NOW());
```

#### 用户操作流程

```
1. 张三创建知识库 "机器学习文档"：
   POST /api/v1/resources
   {resource_type: "kb", resource_id: "kb-001", display_name: "机器学习文档", created_by: "zhangsan"}
   → 自动 ACL: (kb, kb-001, user, zhangsan, owner)

2. 张三在管理界面邀请协作：
   PUT /api/v1/resources/kb/kb-001/acl  → {subject_type: "user",  subject_id: "lisi",      role: "writer"}
   PUT /api/v1/resources/kb/kb-001/acl  → {subject_type: "group", subject_id: "data-team", role: "reader"}

3. 最终 ACL:
   kb:kb-001
     user:zhangsan  → owner
     user:lisi      → writer
     group:data-team → reader

4. 各用户权限效果：
   张三(owner):  读 ✅  写 ✅  删 ✅  管理ACL ✅
   李四(writer): 读 ✅  写 ✅  删 ✅  管理ACL ❌
   王五(不在任何匹配组): 读 ❌  写 ❌
   陈管理(tenant-admin): 全部 ✅（第 2 层短路放行）
```

### 6.3 公告系统（不需要 owner 的场景）

```sql
-- 所有人可读公告
INSERT INTO resource_acls VALUES ('aidp', 'notice', '*', 'group', 'all-users',     'reader', 'admin', NOW());
-- tenant-admins 组管理公告
INSERT INTO resource_acls VALUES ('aidp', 'notice', '*', 'group', 'tenant-admins', 'owner',  'admin', NOW());
```

不注册具体资源实例，纯用类型级通配符，**退化为传统 RBAC 效果**。

### 6.4 张三离职 — 权限交接

```
1. tenant-admin 把 kb-001 的 owner 转给李四：
   PUT /api/v1/resources/kb/kb-001/acl → {subject_type: "user", subject_id: "lisi", role: "owner"}

2. 移除张三的权限：
   DELETE /api/v1/resources/kb/kb-001/acl?subject_type=user&subject_id=zhangsan

3. （可选）批量操作：查询张三是 owner 的所有资源，批量转让
   GET /api/v1/resources?tenant_id=aidp&created_by=zhangsan
```

---

## 7 数据同步流

### 7.1 ACL 数据同步（bundle-server → OPA）

```
PostgreSQL (resource_acls 表)
    │
    ▼ bundle-server 定期拉取 / OPAL 实时推送
构建 OPA 数据文档
    │
    ▼
OPA 内存缓存
    │
    ▼ pep-proxy gRPC 调用
鉴权决策
```

**bundle-server 构建逻辑：**

```python
def build_tenant_data(tenant_id: str) -> dict:
    """将 PostgreSQL 中的 ACL 数据转换为 OPA 数据文档"""

    # 1. 查询该租户所有 ACL 条目
    acls = db.query("SELECT * FROM resource_acls WHERE tenant_id = %s", tenant_id)

    # 2. 按 resource_type → resource_id 分组
    acl_data = {}
    for acl in acls:
        rt = acl["resource_type"]
        ri = acl["resource_id"]
        acl_data.setdefault(rt, {}).setdefault(ri, []).append({
            "subject_type": acl["subject_type"],
            "subject_id":   acl["subject_id"],
            "role":         acl["role"]
        })

    # 3. 从 Keycloak 同步 group members
    group_members = keycloak.get_all_group_members(tenant_id)

    return {
        "acls": acl_data,
        "group_members": group_members
    }
```

### 7.2 Group Members 同步

group_members 数据来源于 Keycloak，同步方式：

| 方案 | 实现 | 延迟 |
|------|------|------|
| **定期拉取（推荐起步）** | bundle-server 每 30s 调用 Keycloak Admin API 获取各 group 成员列表 | 最大 30s |
| **OPAL 数据源** | 配置 OPAL 的 Keycloak data fetcher | 实时 |
| **Keycloak Event Listener** | 自定义 SPI，用户加入/离开 group 时主动通知 bundle-server | 实时 |

---

## 8 resource_type 与 action 提取

### 8.1 推荐方案：应用传 Header

应用在发起请求时，通过自定义 Header 告知 IAM 当前操作的资源：

```
GET /aidp/kb-service/api/v1/kb/kb-001/docs
Headers:
  Authorization: Bearer <jwt>
  X-Resource-Type: kb
  X-Resource-Id: kb-001
```

pep-proxy 提取这些 Header 填入 OPA input。

**优点：** 不依赖 URL 格式约定，应用自由定义路径。
**缺点：** 需要应用配合传 Header。

### 8.2 备选方案：URL 路径约定

约定 URL 格式：`/{tenant-id}/{app}/api/v1/{resource_type}/{resource_id}/...`

pep-proxy 通过正则提取。无需应用改动，但路径格式必须统一。

### 8.3 action 映射

```python
ACTION_MAP = {
    "POST":   "create",   # 需要进一步判断：如果 resource_id 已存在则为 write
    "GET":    "read",
    "PUT":    "write",
    "PATCH":  "write",
    "DELETE": "delete",
}
```

---

## 9 迁移方案

### 9.1 分阶段迁移

```
阶段 1：新建表，双写（2 周）
  - 创建 resources 和 resource_acls 表
  - 新增资源注册 API 和 ACL API
  - 保留旧 policies + role_bindings 表，OPA 同时加载两套数据
  - Rego 策略：旧逻辑 OR 新 ACL 逻辑，任一通过即放行

阶段 2：应用接入（2-4 周）
  - 各应用改造：创建资源时调用资源注册 API
  - tenant-admin 在管理界面配置类型级 ACL
  - 将现有 policy rules 迁移为类型级 ACL 条目

阶段 3：切换，废弃旧表（1 周）
  - 确认所有应用已切到新 ACL 模型
  - Rego 策略移除旧逻辑，只保留 ACL 判断
  - 废弃 policies 和 role_bindings API
  - 保留旧表 30 天后删除
```

### 9.2 数据迁移脚本

将现有 policy rules 转换为类型级 ACL：

```python
def migrate_policies_to_acls():
    """将旧 policy rules 迁移为类型级 ACL"""

    policies = db.query("SELECT * FROM policies")
    bindings = db.query("SELECT * FROM role_policy_bindings")

    for binding in bindings:
        policy = find_policy(policies, binding["policy_id"])
        role_name = keycloak.get_role_name(binding["tenant_id"], binding["role_id"])

        for rule in policy["rules"]:
            # 旧: {resource: "/api/v1/documents", effect: "allow"}
            # 新: resource_type 从路径提取，resource_id = '*'
            resource_type = extract_type_from_path(rule["resource"])
            acl_role = "reader" if rule["effect"] == "allow" else "denied"

            db.insert("resource_acls", {
                "tenant_id":     binding["tenant_id"],
                "resource_type": resource_type,
                "resource_id":   "*",
                "subject_type":  "group",           # 需要找到 role 绑定的 group
                "subject_id":    find_group_for_role(role_name),
                "role":          acl_role,
                "granted_by":    "migration",
            })
```

---

## 10 与业界方案对比

| 维度 | 本方案 | Google Zanzibar / SpiceDB | OpenFGA | Casbin |
|------|--------|--------------------------|---------|--------|
| **模型** | ACL + 通配符 RBAC | ReBAC（关系图） | ReBAC（关系图） | PERM 元模型 |
| **存储** | PostgreSQL + OPA 内存 | 专用图数据库 | 专用存储 | 库内嵌 / 数据库 |
| **部署** | 复用现有 OPA/OPAL 栈 | 新增独立服务 | 新增独立服务 | 嵌入应用进程 |
| **适合规模** | 万级 ACL 条目 | 亿级 | 百万级 | 十万级 |
| **权限继承** | 扁平（通配符模拟） | 原生图遍历继承 | 原生图遍历继承 | 匹配器组合 |
| **复杂度** | 低 | 高 | 中 | 中 |
| **迁移成本** | 低（改表 + 改 Rego） | 高（新基础设施） | 中（新服务） | 中（新依赖） |

**选型理由：** 当前阶段资源实例数量在万级以内，不需要复杂的权限继承（如文件夹→子文件夹→文档），复用现有 OPA+OPAL 栈成本最低。如未来 ACL 规模超过十万或需要嵌套继承，可迁移到 SpiceDB/OpenFGA，数据模型兼容（ACL 条目可直接转换为关系元组）。

---

## 附录 A：完整鉴权示例

### A.1 张三读自己的知识库文档

```
请求: GET /aidp/kb-service/api/v1/kb/kb-001/docs/doc-123
Header: X-Resource-Type: kb, X-Resource-Id: kb-001

OPA input:
  user_id: "zhangsan", tenant_id: "aidp", resource_type: "kb",
  resource_id: "kb-001", action: "read", roles: ["normal-user"]

判断:
  1. super-admin? ❌ → 2. tenant-admin? ❌ → 进入 ACL 判断
  3. user_groups("zhangsan") = {"data-team", "all-users"}
  4. denied? → 无
  5. instance_roles: kb:kb-001 → user:zhangsan → owner       → {owner}
  6. wildcard_roles: kb:*     → group:data-team → creator     → {creator}
  7. all_matched_roles = {owner, creator}
  8. effective_role = owner (优先级 5 > 2)
  9. role_permits("owner", "read") → ✅ 放行
```

### A.2 赵六尝试读张三的 memory

```
请求: GET /aidp/memory-service/api/v1/memories/mem-001
Header: X-Resource-Type: memory, X-Resource-Id: mem-001

OPA input:
  user_id: "zhaoliu", resource_type: "memory", resource_id: "mem-001", action: "read"

判断:
  1. super-admin? ❌ → 2. tenant-admin? ❌ → 进入 ACL 判断
  3. user_groups("zhaoliu") = {"dev-team", "all-users"}
  4. denied? → 无
  5. instance_roles: memory:mem-001 → 无 zhaoliu 相关条目     → {}
  6. wildcard_roles: memory:*      → group:all-users → creator → {creator}
  7. all_matched_roles = {creator}
  8. effective_role = creator
  9. role_permits("creator", "read") → ❌ 拒绝
```

### A.3 李四写入张三的知识库

```
请求: POST /aidp/kb-service/api/v1/kb/kb-001/docs
Header: X-Resource-Type: kb, X-Resource-Id: kb-001

OPA input:
  user_id: "lisi", resource_type: "kb", resource_id: "kb-001", action: "write"

判断:
  1. super-admin? ❌ → 2. tenant-admin? ❌ → 进入 ACL 判断
  3. user_groups("lisi") = {"data-team", "all-users"}
  4. denied? → 无
  5. instance_roles: kb:kb-001 → user:lisi → writer            → {writer}
  6. wildcard_roles: kb:*     → group:data-team → creator       → {creator}
  7. all_matched_roles = {writer, creator}
  8. effective_role = writer (优先级 4 > 2)
  9. role_permits("writer", "write") → ✅ 放行
```

### A.4 王五尝试写入张三的知识库

```
请求: POST /aidp/kb-service/api/v1/kb/kb-001/docs
Header: X-Resource-Type: kb, X-Resource-Id: kb-001

OPA input:
  user_id: "wangwu", resource_type: "kb", resource_id: "kb-001", action: "write"

判断:
  1. super-admin? ❌ → 2. tenant-admin? ❌ → 进入 ACL 判断
  3. user_groups("wangwu") = {"dev-team", "all-users"}
  4. denied? → 无
  5. instance_roles: kb:kb-001 → 无 wangwu 相关条目            → {}
  6. wildcard_roles: kb:*     → group:dev-team → creator        → {creator}
  7. all_matched_roles = {creator}
  8. effective_role = creator
  9. role_permits("creator", "write") → ❌ 拒绝
```
