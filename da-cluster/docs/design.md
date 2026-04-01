# IAM 统一认证与路径级鉴权设计文档

> 版本：v2.0 | 日期：2026-04-01

---

## 1 设计原则

**IAM 只做两件事：**

1. **认证** — 你是谁（JWT 签发、用户/组管理）
2. **路径级鉴权** — 你能不能访问这个 URL 路径（Gateway + OPA）

**细粒度权限由应用自己负责：**

- 记忆库自己按 `user_id` 隔离数据
- 知识库自己维护分享表做协作权限
- IAM 不关心应用内部的资源实例

---

## 2 整体架构

### 2.1 请求鉴权流

```
客户端
  │ Authorization: Bearer <JWT>
  ▼
AgentGateway（统一入口）
  │
  ├─ 路由匹配: /memory/** → memory-service
  ├─ 路由匹配: /knowledgebase/** → kb-service
  │
  ▼ gRPC ext-authz
pep-proxy
  │ 1. 验证 JWT → 提取 user_id, tenant_id, groups
  │ 2. 调用 OPA 判断路径权限
  │ 3. 通过 → 注入 X-Auth-* Header 转发给后端
  │    拒绝 → 返回 403
  ▼
OPA Rego
  │
  ├─ super-admins?           → 全放行
  ├─ tenant-admins + 本租户?  → 全放行
  ├─ 命中路径保护规则?         → 检查是否在指定 group 中
  └─ 未命中任何保护规则?       → all-users 即可放行
  ▼
后端应用（读 X-Auth-* Header 做业务逻辑）
```

### 2.2 注入的 Header

Gateway 鉴权通过后，pep-proxy 向后端注入以下 Header：

| Header | 值 | 说明 |
|--------|----|------|
| `X-Auth-User-Id` | `zhangsan` | 用户 ID |
| `X-Auth-Tenant` | `aidp` | 租户 ID |
| `X-Auth-Groups` | `data-team,all-users` | 用户所属组（逗号分隔） |
| `X-Auth-Group-Ids` | `uuid-1,uuid-2` | 组 UUID（逗号分隔，与 Groups 一一对应） |

应用只需读这些 Header，不需要调 IAM 任何接口。

### 2.3 组件职责

| 组件 | 职责 |
|------|------|
| **Keycloak** | 用户/组管理、JWT 签发、OIDC/SAML 联邦登录 |
| **keycloak-proxy** | 租户/用户/组/应用/路径规则 CRUD API |
| **AgentGateway** | 统一入口、路由转发、URL Rewrite |
| **pep-proxy** | JWT 验证、OPA 调用、Header 注入 |
| **OPA** | 路径级鉴权策略执行 |
| **bundle-server** | 从 PostgreSQL 读路径规则，推送到 OPA |
| **init-job** | Helm 部署时写入默认应用和路径规则 |

---

## 3 数据模型

### 3.1 apps — 应用注册表

应用接入时注册一次，前端管理界面从此表读取可选应用列表。

```sql
CREATE TABLE apps (
    tenant_id    VARCHAR(128) NOT NULL,
    app_name     VARCHAR(128) NOT NULL,      -- 'memory', 'knowledgebase'
    path_prefix  VARCHAR(256) NOT NULL,      -- '/memory/', '/knowledgebase/'
    display_name VARCHAR(256),               -- '记忆库', '知识库'
    description  VARCHAR(512),
    created_at   TIMESTAMP    NOT NULL DEFAULT NOW(),

    PRIMARY KEY (tenant_id, app_name)
);
```

### 3.2 path_rules — 路径保护规则表

定义哪些路径需要特定组权限才能访问。未命中任何规则的路径，`all-users` 即可访问。

```sql
CREATE TABLE path_rules (
    id              SERIAL PRIMARY KEY,
    tenant_id       VARCHAR(128) NOT NULL,
    path_prefix     VARCHAR(256) NOT NULL,    -- '/memory/v1/admin/'
    required_group  VARCHAR(128) NOT NULL,    -- 'memory-admins'
    description     VARCHAR(512),
    created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),

    UNIQUE (tenant_id, path_prefix)
);

CREATE INDEX idx_path_rules_tenant ON path_rules (tenant_id);
```

### 3.3 Keycloak 中的组结构

| 组 | 说明 | 来源 |
|----|------|------|
| `super-admins` | 超级管理员，仅 master realm | 系统初始化 |
| `tenant-admins` | 租户管理员 | 创建租户时自动创建 |
| `all-users` | 默认组，新用户自动加入 | 创建租户时自动创建 |
| `{app}-admins` | 应用管理员（如 `memory-admins`） | 租户管理员按需创建 |
| 业务组 | 如 `data-team`、`dev-team` | 租户管理员按需创建 |

### 3.4 OPA 数据结构

bundle-server 将 path_rules 表转换为以下结构推送到 OPA：

```json
{
  "tenants": {
    "aidp": {
      "path_rules": [
        {
          "path_prefix": "/memory/v1/admin/",
          "required_group": "memory-admins"
        }
      ]
    }
  }
}
```

---

## 4 OPA Rego 策略

```rego
package authz

import future.keywords.in

default allow = false

# ===================================================================
# 系统角色短路
# ===================================================================

# super-admins：跨租户全放行
allow {
    "super-admins" in input.groups
}

# tenant-admins：本租户全放行
allow {
    "tenant-admins" in input.groups
    input.tenant_id == input.token_tenant_id
}

# ===================================================================
# 路径级鉴权（普通用户）
# ===================================================================

# 命中保护规则 → 必须在指定 group 中
allow {
    some rule in data.tenants[input.tenant_id].path_rules
    startswith(input.path, rule.path_prefix)
    rule.required_group in input.groups
}

# 未命中任何保护规则 → all-users 即可
allow {
    not path_is_protected
    "all-users" in input.groups
}

# -------------------------------------------------------------------
# 辅助：判断当前路径是否命中保护规则
# -------------------------------------------------------------------
path_is_protected {
    some rule in data.tenants[input.tenant_id].path_rules
    startswith(input.path, rule.path_prefix)
}
```

### 4.1 ext-authz 输入结构

pep-proxy 解析请求后，构造以下输入传给 OPA：

```json
{
  "input": {
    "user_id":         "zhangsan",
    "tenant_id":       "aidp",
    "token_tenant_id": "aidp",
    "groups":          ["data-team", "all-users"],
    "path":            "/memory/v1/memories",
    "method":          "GET"
  }
}
```

---

## 5 API 设计

### 5.1 应用管理 API

super-admin 和 tenant-admin 可操作。

```
GET    /api/v1/apps                    查看已注册应用列表
POST   /api/v1/apps                    注册新应用
PUT    /api/v1/apps/{app_name}         修改应用信息
DELETE /api/v1/apps/{app_name}         删除应用
```

**注册应用示例：**

```
POST /api/v1/apps

Request:
{
    "app_name": "memory",
    "path_prefix": "/memory/",
    "display_name": "记忆库"
}

Response: 201
{
    "tenant_id": "aidp",
    "app_name": "memory",
    "path_prefix": "/memory/",
    "display_name": "记忆库",
    "created_at": "2026-04-01T10:00:00Z"
}
```

### 5.2 路径保护规则 API

super-admin 和 tenant-admin 可操作。

```
GET    /api/v1/path-rules              查看所有规则
POST   /api/v1/path-rules              添加规则
PUT    /api/v1/path-rules/{id}         修改规则
DELETE /api/v1/path-rules/{id}         删除规则
```

**添加规则示例：**

```
POST /api/v1/path-rules

Request:
{
    "path_prefix": "/memory/v1/admin/",
    "required_group": "memory-admins",
    "description": "记忆库模板管理接口"
}

Response: 201
{
    "id": 1,
    "tenant_id": "aidp",
    "path_prefix": "/memory/v1/admin/",
    "required_group": "memory-admins",
    "description": "记忆库模板管理接口",
    "created_at": "2026-04-01T10:00:00Z"
}
```

### 5.3 现有 API（不变）

以下 API 保持不变：

```
# 租户管理
POST   /api/v1/tenants                 创建租户

# 用户管理
GET    /api/v1/{realm}/users           查看用户列表
POST   /api/v1/{realm}/users           创建用户
DELETE /api/v1/{realm}/users/{id}      删除用户

# 组管理
GET    /api/v1/{realm}/groups                      查看组列表
POST   /api/v1/{realm}/groups                      创建组
DELETE /api/v1/{realm}/groups/{id}                 删除组
PUT    /api/v1/{realm}/groups/{id}/members         添加成员
DELETE /api/v1/{realm}/groups/{id}/members/{uid}   移除成员
```

---

## 6 初始化与默认数据

### 6.1 init-job

Helm 部署时，init-job 自动写入默认的应用和路径规则：

```python
DEFAULT_APPS = [
    {
        "tenant_id": "aidp",
        "app_name": "memory",
        "path_prefix": "/memory/",
        "display_name": "记忆库"
    },
    {
        "tenant_id": "aidp",
        "app_name": "knowledgebase",
        "path_prefix": "/knowledgebase/",
        "display_name": "知识库"
    }
]

DEFAULT_PATH_RULES = [
    {
        "tenant_id": "aidp",
        "path_prefix": "/memory/v1/admin/",
        "required_group": "memory-admins",
        "description": "记忆库模板管理接口"
    }
]

# UPSERT：已存在则跳过，不覆盖管理员后续修改
for app in DEFAULT_APPS:
    db.execute("""
        INSERT INTO apps (tenant_id, app_name, path_prefix, display_name)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (tenant_id, app_name) DO NOTHING
    """, app)

for rule in DEFAULT_PATH_RULES:
    db.execute("""
        INSERT INTO path_rules (tenant_id, path_prefix, required_group, description)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (tenant_id, path_prefix) DO NOTHING
    """, rule)
```

### 6.2 Keycloak 初始化（创建租户时自动完成）

```
Realm: aidp
│
├── Client: data-agent (OIDC 登录)
│     └── Protocol Mapper: group-mapper (把 groups 写入 JWT)
│
├── Default Group: all-users (新用户自动加入)
│
├── Groups:
│   ├── tenant-admins
│   └── all-users
│
└── Users:
    └── chen-admin → groups: [tenant-admins, all-users]
```

### 6.3 完整生命周期

```
第一次部署（Helm install）:
  init-job 自动写入:
    apps: [记忆库, 知识库]
    path_rules: [/memory/v1/admin/ → memory-admins]
    Keycloak groups: [tenant-admins, all-users, memory-admins]
  ↓
  系统可用，默认规则生效

日常运维（管理员操作）:
  新应用接入    → POST /api/v1/apps 注册
  新保护规则    → POST /api/v1/path-rules 添加
  调整规则      → PUT /api/v1/path-rules/{id}
  创建业务组    → POST /api/v1/{realm}/groups
  分配应用管理员 → PUT /api/v1/{realm}/groups/{id}/members

重新部署（Helm upgrade）:
  init-job 再跑 → ON CONFLICT DO NOTHING → 不影响已有配置
  新增默认应用  → 自动插入
```

---

## 7 Gateway 路由配置

每个接入应用对应一条 HTTPRoute 规则，在 Helm chart 中配置：

```yaml
# 记忆库路由
- matches:
    - path:
        type: PathPrefix
        value: /memory/
  filters:
    - type: URLRewrite
      urlRewrite:
        path:
          type: ReplacePrefixMatch
          replacePrefixMatch: /
  backendRefs:
    - name: memory-service

# 知识库路由
- matches:
    - path:
        type: PathPrefix
        value: /knowledgebase/
  filters:
    - type: URLRewrite
      urlRewrite:
        path:
          type: ReplacePrefixMatch
          replacePrefixMatch: /
  backendRefs:
    - name: kb-service
```

**URL Rewrite 说明：** 外部路径 `/memory/v1/admin/templates` 经 Gateway 转发后，后端收到 `/v1/admin/templates`。应用原有的接口路径无需修改，Gateway 层负责改写。

---

## 8 完整例子：从零到应用接入

### 8.1 第一部分：租户系统初始化

#### 8.1.1 超级管理员创建租户

```
POST /api/v1/tenants
{
  "realm": "aidp",
  "admin_username": "chen-admin",
  "admin_password": "ChenAdmin@123"
}
```

Keycloak 自动创建：

```
Realm: aidp
│
├── Client: data-agent (OIDC 登录)
│     └── Protocol Mapper: group-mapper (把 groups 写入 JWT)
│
├── Default Group: all-users (新用户自动加入)
│
├── Groups:
│   ├── super-admins    ← 仅 master realm 有
│   ├── tenant-admins   ← chen-admin 在这里
│   └── all-users       ← 默认组
│
└── Users:
    └── chen-admin → groups: [tenant-admins, all-users]
```

#### 8.1.2 租户管理员导入用户

陈管理可配置 SAML SSO 对接企业 AD，或手动创建用户：

```
POST /api/v1/aidp/users  → 张三(zhangsan)  → 自动加入 all-users
POST /api/v1/aidp/users  → 李四(lisi)      → 自动加入 all-users
POST /api/v1/aidp/users  → 王五(wangwu)    → 自动加入 all-users
POST /api/v1/aidp/users  → 小王(xiaowang)  → 自动加入 all-users
```

#### 8.1.3 创建业务组

```
POST /api/v1/aidp/groups → data-team
POST /api/v1/aidp/groups → dev-team

把人拉进组:
  data-team: [张三, 李四]
  dev-team:  [王五]
```

#### 8.1.4 此时的状态

```
Realm: aidp
│
├── Groups:
│   ├── tenant-admins  → [chen-admin]
│   ├── all-users      → [chen-admin, zhangsan, lisi, wangwu, xiaowang]
│   ├── data-team      → [zhangsan, lisi]
│   └── dev-team       → [wangwu]
│
├── Users:
│   ├── chen-admin  → groups: [tenant-admins, all-users]
│   ├── zhangsan    → groups: [data-team, all-users]
│   ├── lisi        → groups: [data-team, all-users]
│   ├── wangwu      → groups: [dev-team, all-users]
│   └── xiaowang    → groups: [all-users]
│
└── 没有任何应用接入，没有任何路径保护规则
```

张三登录拿到的 JWT：

```json
{
  "sub": "zhangsan",
  "iss": "http://keycloak:8080/realms/aidp",
  "groups": ["data-team", "all-users"],
  "group_ids": ["550e8400-e29b-41d4-a716-446655440000", "661f9511-a3bc-42e1-8822-771234560000"]
}
```

---

### 8.2 第二部分：应用接入

#### 8.2.1 记忆库接入

**第 1 步：约定 URL 前缀** — `"我们的应用叫 memory，URL 前缀是 /memory/"`

**第 2 步：约定哪些接口需要 IAM 保护**

普通接口（所有用户可访问）:

```
GET    /memory/v1/memories            查我的记忆列表
POST   /memory/v1/memories            创建记忆
GET    /memory/v1/memories/{id}       查某条记忆
DELETE /memory/v1/memories/{id}       删除某条记忆
```

管理接口（需要 memory-admins 权限）:

```
GET    /memory/v1/admin/templates     查模板列表
POST   /memory/v1/admin/templates     创建模板
PUT    /memory/v1/admin/templates/{id} 修改模板
DELETE /memory/v1/admin/templates/{id} 删除模板
```

> 注：记忆库原来的管理接口可能是 `/api/v1/templates`，Gateway 会将 `/memory/v1/admin/templates` 改写为 `/api/v1/templates` 转发，应用不需要改代码。

**第 3 步：应用内部实现隔离逻辑**

记忆库读 Header 做数据隔离，不需要调 IAM 任何接口：

```python
@app.get("/v1/memories")
def list_memories(request):
    user_id = request.headers["X-Auth-User-Id"]
    tenant_id = request.headers["X-Auth-Tenant"]
    # 只返回自己的记忆，天然隔离
    return db.query(
        "SELECT * FROM memories WHERE tenant_id=%s AND user_id=%s",
        tenant_id, user_id
    )
```

#### 8.2.2 知识库接入

**第 1 步：约定 URL 前缀** — `"我们的应用叫 knowledgebase，URL 前缀是 /knowledgebase/"`

**第 2 步：约定接口**

普通接口:

```
GET    /knowledgebase/v1/kb              我能看到的知识库列表
POST   /knowledgebase/v1/kb              创建知识库
GET    /knowledgebase/v1/kb/{id}         查看知识库
PUT    /knowledgebase/v1/kb/{id}         编辑知识库
DELETE /knowledgebase/v1/kb/{id}         删除知识库
POST   /knowledgebase/v1/kb/{id}/share   分享知识库给别人
```

管理接口:

```
GET    /knowledgebase/v1/admin/settings   全局配置
PUT    /knowledgebase/v1/admin/settings   修改全局配置
```

**第 3 步：应用内部实现分享/权限逻辑**

知识库自己维护分享表，自己做鉴权，不需要调 IAM 接口：

```sql
CREATE TABLE kb_shares (
    kb_id        VARCHAR,
    tenant_id    VARCHAR,
    owner_id     VARCHAR,
    subject_type VARCHAR,   -- 'user' | 'group'
    subject_id   VARCHAR,
    permission   VARCHAR    -- 'reader' | 'writer'
);
```

```python
@app.get("/v1/kb")
def list_kb(request):
    user_id = request.headers["X-Auth-User-Id"]
    tenant_id = request.headers["X-Auth-Tenant"]
    groups = request.headers["X-Auth-Groups"].split(",")

    # 查我拥有的 + 分享给我的 + 分享给我所在组的
    return db.query("""
        SELECT DISTINCT k.* FROM knowledge_bases k
        LEFT JOIN kb_shares s ON k.id = s.kb_id
        WHERE k.tenant_id = %s
          AND (
            k.owner_id = %s
            OR (s.subject_type='user' AND s.subject_id = %s)
            OR (s.subject_type='group' AND s.subject_id = ANY(%s))
          )
    """, tenant_id, user_id, user_id, groups)
```

---

### 8.3 第三部分：IAM 侧配置

#### 8.3.1 配置 Gateway 路由

在 Helm chart 中添加 HTTPRoute 规则（见第 7 节）。

#### 8.3.2 创建应用管理组

```
POST /api/v1/aidp/groups → memory-admins
POST /api/v1/aidp/groups → knowledgebase-admins
```

#### 8.3.3 配置路径保护规则

```
POST /api/v1/path-rules
{
  "path_prefix": "/memory/v1/admin/",
  "required_group": "memory-admins",
  "description": "记忆库模板管理接口"
}
```

> 知识库不需要配置任何路径保护规则——它自己做鉴权，IAM 只提供身份信息。

#### 8.3.4 分配应用管理员

场景：小王是记忆库的运营，负责管理 template

```
PUT /api/v1/aidp/groups/memory-admins/members
{ "user_id": "xiaowang" }
```

场景：张三是知识库的运营，负责全局配置

```
PUT /api/v1/aidp/groups/knowledgebase-admins/members
{ "user_id": "zhangsan" }
```

#### 8.3.5 配置完成后的最终状态

```
Realm: aidp
│
├── Groups:
│   ├── tenant-admins          → [chen-admin]
│   ├── all-users              → [chen-admin, zhangsan, lisi, wangwu, xiaowang]
│   ├── data-team              → [zhangsan, lisi]
│   ├── dev-team               → [wangwu]
│   ├── memory-admins          → [xiaowang]
│   └── knowledgebase-admins   → [zhangsan]
│
├── Users:
│   ├── chen-admin  → [tenant-admins, all-users]
│   ├── zhangsan    → [data-team, all-users, knowledgebase-admins]
│   ├── lisi        → [data-team, all-users]
│   ├── wangwu      → [dev-team, all-users]
│   └── xiaowang    → [all-users, memory-admins]
│
├── Apps:
│   ├── memory        → path_prefix: /memory/
│   └── knowledgebase → path_prefix: /knowledgebase/
│
└── Path Rules:
    └── /memory/v1/admin/ → required_group: memory-admins
```

---

### 8.4 第四部分：各用户实际使用效果

#### 8.4.1 普通用户 — 李四

JWT: `groups: ["data-team", "all-users"]`

**使用记忆库：**

```
GET /memory/v1/memories
  → OPA: 未命中保护规则, "all-users" ✅ 放行
  → 记忆库返回：只有李四自己的记忆（应用按 user_id 过滤）

GET /memory/v1/memories/mem-zhangsan-001  （试图偷看张三的记忆）
  → OPA: 未命中保护规则, "all-users" ✅ 放行
  → 记忆库代码检查: owner != lisi → 403 拒绝
  → （这是应用自己做的隔离，不是 IAM）

POST /memory/v1/admin/templates （试图管理模板）
  → OPA: 命中保护规则 /memory/v1/admin/, 需要 "memory-admins"
  → 李四没有 → ❌ 403（IAM 直接挡住，请求到不了应用）
```

**使用知识库：**

```
GET /knowledgebase/v1/kb
  → OPA: 未命中任何保护规则, "all-users" ✅ 放行
  → 知识库返回：李四能看到的知识库列表
    - 自己创建的
    - 别人分享给李四的
    - 别人分享给 data-team 的

GET /knowledgebase/v1/kb/kb-001 （张三创建的，没分享给李四）
  → OPA: ✅ 放行（OPA 只管路径级）
  → 知识库代码检查: 李四不是 owner，也不在分享列表 → 403
```

#### 8.4.2 应用管理员 — 小王（memory-admins）

JWT: `groups: ["memory-admins", "all-users"]`

```
GET /memory/v1/admin/templates
  → OPA: 命中保护规则, "memory-admins" ✅ 放行
  → 返回模板列表

POST /memory/v1/admin/templates
  → OPA: ✅ 同上
  → 创建新模板

GET /knowledgebase/v1/admin/settings （尝试管理知识库）
  → OPA: 未命中任何保护规则（知识库没配路径保护）, "all-users" ✅ 放行
  → 知识库代码自行检查管理员权限 → 非管理员 → 403
  → （知识库自己决定谁能访问管理接口）

GET /knowledgebase/v1/kb （作为普通用户使用知识库）
  → OPA: ✅ 放行
  → 正常使用
```

#### 8.4.3 知识库管理员 — 张三（knowledgebase-admins）

JWT: `groups: ["data-team", "all-users", "knowledgebase-admins"]`

```
PUT /knowledgebase/v1/admin/settings （管理全局配置）
  → OPA: ✅ 放行（未命中保护规则）
  → 知识库代码检查: "knowledgebase-admins" in groups → 允许

POST /knowledgebase/v1/kb （创建知识库）
  → OPA: ✅ 放行
  → 知识库创建 kb-001，owner=zhangsan

POST /knowledgebase/v1/kb/kb-001/share （分享知识库）
  {subject_type: "group", subject_id: "data-team", permission: "reader"}
  → OPA: ✅ 放行
  → 知识库检查: 张三是 kb-001 的 owner → 允许分享
  → data-team 的人（张三、李四）都能读 kb-001

POST /memory/v1/admin/templates （尝试管理记忆库模板）
  → OPA: 命中保护规则, 需要 "memory-admins"
  → 张三没有 → ❌ 403
```

#### 8.4.4 租户管理员 — 陈管理（tenant-admins）

JWT: `groups: ["tenant-admins", "all-users"]`

```
POST /memory/v1/admin/templates        → ✅ tenant-admins 直接放行
PUT /knowledgebase/v1/admin/settings   → ✅ tenant-admins 直接放行
GET /memory/v1/memories                → ✅
DELETE /knowledgebase/v1/kb/kb-001     → ✅

PUT  /api/v1/aidp/groups/memory-admins/members  → 加人到 memory-admins
POST /api/v1/aidp/groups                         → 创建新组
GET  /api/v1/aidp/users                          → 查看用户列表
POST /api/v1/path-rules                          → 添加路径保护规则
```

---

## 9 各方职责一览

```
┌─────────────────────────────────────────────────────────────┐
│ IAM 团队负责:                                                │
│   1. 部署 Keycloak + OPA + Gateway                          │
│   2. 配置 Gateway 路由（新应用接入时加一条 HTTPRoute）         │
│   3. 提供用户/组/应用/路径规则管理 API                         │
│   4. OPA Rego 策略（写一次，以后不用改）                       │
│   5. init-job 维护默认应用和路径规则                           │
│                                                             │
│ 租户管理员负责:                                               │
│   1. 创建 {app}-admins 组                                    │
│   2. 配置路径保护规则（哪些路径需要哪个组）                     │
│   3. 把人拉进对应的组                                         │
│   4. 管理用户                                                │
│                                                             │
│ 应用团队负责:                                                 │
│   1. 和 IAM 团队约定 URL 前缀和需要保护的路径                  │
│   2. 读 X-Auth-* Header 做业务逻辑                           │
│   3. 自己实现细粒度权限（如知识库的分享功能）                    │
│   4. 不需要调 IAM 任何接口                                    │
└─────────────────────────────────────────────────────────────┘
```

---

## 10 两种应用鉴权模式对比

| 维度 | 记忆库模式（IAM 保护管理路径） | 知识库模式（应用自主鉴权） |
|------|-------------------------------|--------------------------|
| **IAM 路径规则** | 配置 `/memory/v1/admin/` → `memory-admins` | 不配置任何规则 |
| **管理接口鉴权** | IAM 拦截，请求到不了应用 | IAM 放行，应用自己检查 groups |
| **普通接口鉴权** | IAM 放行，应用按 user_id 隔离 | IAM 放行，应用按分享表鉴权 |
| **适用场景** | 管理逻辑简单，不需要细粒度控制 | 有复杂的协作/分享需求 |
| **应用改动** | 最小，只读 Header 做数据隔离 | 需要维护自己的权限表 |
