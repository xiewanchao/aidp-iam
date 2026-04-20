# 数据存储视角 — 数据在哪、谁读谁写、怎么同步

> 版本：v2.0 | 日期：2026-04-07

---

## 1 数据库拓扑

```
PostgreSQL 实例
├── keycloak DB — Keycloak 内部存储（不触碰）
├── opal DB    — OPAL Server pub/sub（保留给 OPAL，旧 policies/role_policy_bindings 表已废弃）
└── iam DB（新）— 所有 IAM 业务表
    ├── apps               （系统级，无 tenant_id）
    ├── resource_patterns   （系统级，无 tenant_id，含 ID 提取规则）
    ├── resource_actions    （系统级，无 tenant_id，操作识别规则）
    ├── path_rules          （系统级，无 tenant_id）
    ├── path_rule_groups    （path_rules ↔ 组的多对多关联）
    ├── resource_acl        （租户级，有 tenant_id）
    ├── pending_acl         （租户级，有 tenant_id）
    └── api_keys            （租户级，有 tenant_id）
```

**关键设计决策：** 系统面向单一客户销售，租户代表部门。因此应用注册、路径规则、资源模式都是系统级配置（无 tenant_id），只有资源 ACL 和待处理队列是租户级数据（有 tenant_id）。

---

## 2 数据存储全景

```mermaid
flowchart TB
    subgraph "iam DB（新）"
        APPS[(apps<br/>应用注册 + License<br/>系统级)]
        PR[(path_rules<br/>路径保护规则<br/>系统级)]
        RP[(resource_patterns<br/>资源匹配 + ID 提取规则<br/>系统级)]
        RA[(resource_actions<br/>操作识别规则<br/>系统级)]
        ACL[(resource_acl<br/>资源权限<br/>租户级)]
        PENDING[(pending_acl<br/>写入失败重试队列<br/>租户级)]
        AK[(api_keys<br/>API Key 管理<br/>租户级)]
    end

    subgraph "keycloak DB（不触碰）"
        USERS[(users<br/>用户)]
        GROUPS[(groups<br/>组 + 组成员)]
        CLIENTS[(clients<br/>OIDC 客户端)]
    end

    subgraph "OPA 内存"
        OPADATA[(bundle 数据<br/>apps + path_rules 快照)]
    end

    subgraph 组件
        KP[keycloak-proxy]
        BS[bundle-server]
        RS[resource-sync<br/>ext_proc 调用]
        PEP[pep-proxy]
        INIT[init-job]
    end

    %% 写入关系
    KP -->|读/写| APPS
    PEP -->|读/写| PR
    KP -->|读/写| RP
    KP -->|Admin API| USERS
    KP -->|Admin API| GROUPS
    INIT -->|写| APPS
    INIT -->|写| PR
    INIT -->|写| RP
    RS -->|读/写| ACL
    RS -->|读/写| PENDING

    %% 读取关系
    BS -->|读| APPS
    BS -->|读| PR
    BS -->|推送| OPADATA
    PEP -->|查询| OPADATA
    PEP -->|查询| ACL
    PEP -->|启动加载| APPS
    PEP -->|启动加载| RP
    RS -->|读| APPS
    RS -->|读| RP

    style APPS fill:#4a9eff,color:#fff
    style PR fill:#4a9eff,color:#fff
    style RP fill:#4a9eff,color:#fff
    style ACL fill:#845ef7,color:#fff
    style PENDING fill:#845ef7,color:#fff
    style USERS fill:#f06595,color:#fff
    style GROUPS fill:#f06595,color:#fff
    style CLIENTS fill:#f06595,color:#fff
    style OPADATA fill:#ffd43b,color:#000
```

---

## 3 表结构定义

### 3.1 系统级表（无 tenant_id）

#### apps — 应用注册表

```sql
CREATE TABLE apps (
    app_name     VARCHAR(128) PRIMARY KEY,
    path_prefix  VARCHAR(256) NOT NULL UNIQUE,
    display_name VARCHAR(256),
    description  VARCHAR(512),
    admin_group  VARCHAR(128),  -- 应用管理员组名（元数据）。Rego 不读此字段，UI 展示用；实际 admin 权限靠 path_rules + path_rule_groups 显式绑定
    enabled      BOOLEAN      NOT NULL DEFAULT true,
    created_at   TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMP    NOT NULL DEFAULT NOW()
);
```

#### resource_patterns — 资源匹配与 ID 提取规则

```sql
CREATE TABLE resource_patterns (
    app_name        VARCHAR(128) NOT NULL REFERENCES apps(app_name),
    resource_prefix VARCHAR(256) NOT NULL,
    resource_type   VARCHAR(128) NOT NULL,
    -- 资源 ID 提取规则（适配非标准 API）
    id_source       VARCHAR(16)  NOT NULL DEFAULT 'path',    -- 'path' / 'query' / 'body'
    id_field        VARCHAR(128) NOT NULL DEFAULT 'id',       -- body JSON 字段名，支持嵌套 'data.id'
    id_query_param  VARCHAR(128) DEFAULT NULL,                 -- id_source=query 时的参数名
    PRIMARY KEY (app_name, resource_prefix)
);
```

`id_source` 说明：
- `path`（默认）：资源 ID 在 URL 路径段中，如 `/v1/kb/kb-001`
- `query`：资源 ID 在 query 参数中，如 `/v1/kb?id=kb-001`
- `body`：资源 ID 在请求体 JSON 中，如 `{"kb_id": "kb-001"}`（需开启 ext_authz body 转发）

`id_field` 支持嵌套路径：`data.kb_id` 表示 `body["data"]["kb_id"]`

#### resource_actions — 操作识别规则

```sql
CREATE TABLE resource_actions (
    id              SERIAL PRIMARY KEY,
    app_name        VARCHAR(128) NOT NULL,
    resource_prefix VARCHAR(256) NOT NULL,
    action          VARCHAR(32)  NOT NULL,   -- 'create' / 'delete' / 'read' / 'update' / 'list'
    method          VARCHAR(16)  NOT NULL,   -- 'GET' / 'POST' / 'PUT' / 'DELETE' / 'PATCH'
    path_suffix     VARCHAR(256) DEFAULT NULL,-- NULL=标准 RESTful 默认行为
                                              -- 非空=特定子路径如 '/create', '/delete', '/detail'
    success_status  INTEGER      DEFAULT NULL,-- ext_proc 用：创建/删除的成功状态码，NULL=不校验
    min_permission  VARCHAR(32)  NOT NULL DEFAULT 'none',
                                              -- 'none'=不查 resource_acl（创建/列表）
                                              -- 'viewer' / 'contributor' / 'owner'
    FOREIGN KEY (app_name, resource_prefix) REFERENCES resource_patterns(app_name, resource_prefix)
);
```

**不配置 resource_actions 时的默认行为（标准 RESTful）：**

| action | method | path_suffix | success_status | min_permission |
|--------|--------|-------------|---------------|---------------|
| create | POST | NULL | 201 | none |
| list | GET | NULL | NULL | none |
| read | GET | /{id} | NULL | viewer |
| update | PUT | /{id} | NULL | contributor |
| update | PATCH | /{id} | NULL | contributor |
| delete | DELETE | /{id} | 200 | owner |

默认规则在代码中兜底，标准 RESTful 应用无需插入任何 resource_actions 记录。

#### path_rules — 路径保护规则

```sql
CREATE TABLE path_rules (
    id              SERIAL PRIMARY KEY,
    path_prefix     VARCHAR(256) NOT NULL,
    method          VARCHAR(10),              -- NULL = 匹配所有 HTTP 方法；否则精确匹配
    required_group  VARCHAR(128) NOT NULL,    -- 首要绑定组（历史字段，多对多详见 path_rule_groups）
    description     VARCHAR(512),
    created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),
    UNIQUE (path_prefix, method)              -- 同一路径+Method 组合只能有一条规则
);
```

**字段说明**：
- `method = NULL` 表示规则匹配任意 HTTP 方法；设为 `'GET'`/`'POST'`/`'PUT'`/`'DELETE'` 等则精确匹配
- `required_group` 是"首要绑定组"标识位（留作 UI 展示和审计），**真正的组绑定在 `path_rule_groups` 表里**

#### path_rule_groups — 路径规则与组的多对多关联

```sql
CREATE TABLE path_rule_groups (
    rule_id      INTEGER NOT NULL REFERENCES path_rules(id) ON DELETE CASCADE,
    group_name   VARCHAR(128) NOT NULL,
    PRIMARY KEY (rule_id, group_name)
);
```

**设计要点**：
- 一条 `path_rule` 可以绑定到**多个组**，组间是 **OR 语义**（任一组匹配即放行）
- 例：`(/kb/, NULL)` 这条规则绑定 `['admins', 'kb-admins']`，表示 `admins` OR `kb-admins` 都能访问 `/kb/*` 下的任意路径任意方法
- `ON DELETE CASCADE`：删除 path_rule 时自动清理关联组
- 所有授权完全由这两张表驱动，**Rego 不存在任何"管理员自动放行"的旁路逻辑**

**Rego 鉴权流程（简化）**：
```rego
allow {
    not app_disabled                                        # 应用未被禁用
    some rule in data.path_rules                            # 遍历 path_rules
    startswith(input.path, rule.path_prefix)                # 路径前缀匹配
    method_matches(rule)                                    # Method 匹配（NULL 则通配）
    some g in rule.required_groups                          # 该规则的绑定组
    g in input.groups                                       # 用户在其中一个绑定组里
}
```

### 3.2 租户级表（有 tenant_id）

#### resource_acl — 资源权限表

```sql
CREATE TABLE resource_acl (
    id              SERIAL PRIMARY KEY,
    tenant_id       VARCHAR(128) NOT NULL,
    app_name        VARCHAR(128) NOT NULL,
    resource_type   VARCHAR(128) NOT NULL,
    resource_id     VARCHAR(256) NOT NULL,
    subject_type    VARCHAR(32)  NOT NULL CHECK (subject_type IN ('user', 'group')),
    subject_id      VARCHAR(128) NOT NULL,
    permission      VARCHAR(32)  NOT NULL CHECK (permission IN ('owner', 'contributor', 'viewer')),
    created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, app_name, resource_type, resource_id, subject_type, subject_id)
);
CREATE INDEX idx_acl_resource ON resource_acl (tenant_id, app_name, resource_type, resource_id);
CREATE INDEX idx_acl_subject ON resource_acl (tenant_id, app_name, resource_type, subject_type, subject_id);
CREATE INDEX idx_acl_cleanup ON resource_acl (app_name, resource_type, resource_id);
```

#### pending_acl — 写入失败重试队列

```sql
CREATE TABLE pending_acl (
    id              SERIAL PRIMARY KEY,
    tenant_id       VARCHAR(128) NOT NULL,
    app_name        VARCHAR(128) NOT NULL,
    resource_type   VARCHAR(128) NOT NULL,
    resource_id     VARCHAR(256) NOT NULL,
    subject_type    VARCHAR(32)  NOT NULL,
    subject_id      VARCHAR(128) NOT NULL,
    permission      VARCHAR(32)  NOT NULL,
    action          VARCHAR(16)  NOT NULL CHECK (action IN ('create', 'delete')),
    retry_count     INTEGER      NOT NULL DEFAULT 0,
    max_retries     INTEGER      NOT NULL DEFAULT 10,
    last_error      TEXT,
    created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),
    next_retry      TIMESTAMP    NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_pending_retry ON pending_acl (next_retry) WHERE retry_count < max_retries;
```

---

## 4 每张表的读写关系

### 4.1 apps — 应用注册表（系统级）

```mermaid
flowchart LR
    subgraph 谁写
        INIT[init-job<br/>部署时写入默认应用]
        KP[keycloak-proxy<br/>管理员注册新应用<br/>License 开关]
    end

    subgraph apps表
        APPS[(apps<br/>app_name, path_prefix<br/>enabled<br/>系统级 · 无 tenant_id)]
    end

    subgraph 谁读
        BS[bundle-server<br/>转换为 OPA bundle]
        KP2[keycloak-proxy<br/>GET /api/v1/apps]
        PEP[pep-proxy<br/>启动时加载<br/>path_prefix → app_name 映射]
        RS[resource-sync<br/>确定 app_name]
    end

    INIT -->|INSERT<br/>ON CONFLICT DO NOTHING| APPS
    KP -->|INSERT / UPDATE| APPS
    APPS --> BS
    APPS --> KP2
    APPS --> PEP
    APPS --> RS

    style APPS fill:#4a9eff,color:#fff
```

| 字段 | 写入者 | 读取者 | 用途 |
|------|--------|--------|------|
| `app_name` | init-job, keycloak-proxy | 所有读取者 | 应用标识（主键） |
| `path_prefix` | init-job, keycloak-proxy | bundle-server, pep-proxy, resource-sync | 路由匹配（pep-proxy 和 resource-sync 启动时加载，用于将请求路径映射到 app_name） |
| `enabled` | keycloak-proxy | bundle-server → OPA | License 控制 |

---

### 4.2 path_rules — 路径保护规则表（系统级）

```mermaid
flowchart LR
    subgraph 谁写
        INIT[init-job<br/>默认规则]
        PEP_W[pep-proxy<br/>管理员配置<br/>path-rules CRUD API]
    end

    subgraph path_rules表
        PR[(path_rules<br/>path_prefix + method<br/>required_group<br/>系统级 · 无 tenant_id)]
    end

    subgraph 谁读
        BS[bundle-server<br/>转换为 OPA bundle]
    end

    INIT -->|INSERT<br/>ON CONFLICT DO NOTHING| PR
    PEP_W -->|INSERT / UPDATE / DELETE| PR
    PR --> BS

    style PR fill:#4a9eff,color:#fff
```

| 字段 | 写入者 | 读取者 | 用途 |
|------|--------|--------|------|
| `path_prefix` | init-job, pep-proxy | bundle-server → OPA | 哪些路径需要保护 |
| `required_group` | init-job, pep-proxy | bundle-server → OPA | 需要哪个组才能访问 |

---

### 4.3 resource_patterns + resource_actions — 资源识别规则（系统级）

```mermaid
flowchart LR
    subgraph 谁写
        INIT[init-job<br/>默认配置]
        KP[keycloak-proxy<br/>注册应用时写入]
    end

    subgraph 资源识别规则
        RP[(resource_patterns<br/>资源匹配 + ID 提取<br/>系统级)]
        RA[(resource_actions<br/>操作识别 + 权限要求<br/>系统级)]
    end

    subgraph 谁读
        PEP[pep-proxy<br/>启动时加载<br/>鉴权时按规则提取 ID<br/>按 action 判断所需权限]
        RS[resource-sync<br/>按规则识别创建/删除<br/>自动同步 ACL]
    end

    INIT -->|INSERT| RP
    INIT -->|INSERT| RA
    KP -->|INSERT| RP
    KP -->|INSERT| RA
    RP --> PEP
    RA --> PEP
    RP --> RS
    RA --> RS

    style RP fill:#4a9eff,color:#fff
    style RA fill:#4a9eff,color:#fff
```

| 表/字段 | 用途 |
|---------|------|
| `resource_patterns.id_source` | 告诉 pep-proxy 从哪里提取资源 ID（path/query/body） |
| `resource_patterns.id_field` | body 模式下 JSON 字段名，支持嵌套如 `data.kb_id` |
| `resource_patterns.id_query_param` | query 模式下 URL 参数名 |
| `resource_actions.action` | 操作类型：create/delete/read/update/list |
| `resource_actions.method` + `path_suffix` | 识别条件：HTTP 方法 + 路径后缀 |
| `resource_actions.success_status` | ext_proc 判断创建/删除是否成功的状态码 |
| `resource_actions.min_permission` | pep-proxy 该操作所需的最低权限等级 |

**示例：标准 RESTful（无需 resource_actions，用代码默认规则）**

```
resource_patterns:
| app_name       | resource_prefix | resource_type | id_source | id_field | id_query_param |
|----------------|-----------------|---------------|-----------|----------|----------------|
| knowledgebase  | /v1/kb          | kb            | path      | id       | NULL           |
```

**示例：非标准 API（POST 操作 + ID 在 body 中）**

```
resource_patterns:
| app_name | resource_prefix | resource_type | id_source | id_field | id_query_param |
|----------|-----------------|---------------|-----------|----------|----------------|
| legacy   | /v1/kb          | kb            | body      | kb_id    | NULL           |

resource_actions:
| action  | method | path_suffix | success_status | min_permission |
|---------|--------|-------------|---------------|----------------|
| create  | POST   | /create     | 200           | none           |
| read    | POST   | /detail     | NULL          | viewer         |
| update  | POST   | /update     | NULL          | contributor    |
| delete  | POST   | /delete     | 200           | owner          |
| list    | POST   | /list       | NULL          | none           |
```

---

### 4.4 resource_acl — 资源权限表（租户级）

```mermaid
flowchart LR
    subgraph 谁写
        RS_AUTO[resource-sync<br/>ext_proc 自动同步<br/>201→加owner<br/>DELETE→删ACL]
        RS_API[resource-sync<br/>ACL API<br/>分享/取消分享]
    end

    subgraph resource_acl表
        ACL[(resource_acl<br/>tenant_id, app_name<br/>resource_type, resource_id<br/>subject_type, subject_id<br/>permission<br/>租户级 · 有 tenant_id)]
    end

    subgraph 谁读
        PEP[pep-proxy<br/>资源实例鉴权<br/>这个用户对这个资源有没有权限?]
        RS_QUERY[resource-sync<br/>ACL API 查询<br/>GET /acl/v1/resources/{resource_id}/permissions]
        RS_EXTPROC[resource-sync ext_proc<br/>请求阶段查询 resource_acl<br/>注入 X-Allowed-Ids Header]
    end

    RS_AUTO -->|INSERT / DELETE| ACL
    RS_API -->|INSERT / UPDATE / DELETE| ACL
    ACL --> PEP
    ACL --> RS_QUERY
    ACL --> RS_EXTPROC

    style ACL fill:#845ef7,color:#fff
```

| 字段 | 写入者 | 读取者 | 用途 |
|------|--------|--------|------|
| `tenant_id` | resource-sync | pep-proxy, resource-sync | 租户隔离 |
| `resource_type` | resource-sync | pep-proxy | 资源类型（kb, memory） |
| `resource_id` | resource-sync | pep-proxy | 资源实例 ID |
| `subject_type` | resource-sync | pep-proxy | user 或 group |
| `subject_id` | resource-sync | pep-proxy | 用户 ID 或 组名 |
| `permission` | resource-sync | pep-proxy | owner / contributor / viewer |

**resource_acl 的数据量预估：**

```
200 租户 × 5 应用 × 平均每应用 1000 资源 × 平均每资源 3 条 ACL
= 200 × 5 × 1000 × 3
= 300 万条

pep-proxy 每次请求查 1 条（按 tenant_id + resource_id + subject_id 精确查询）
有联合索引，查询 < 1ms
```

**索引策略：**

| 索引 | 用途 |
|------|------|
| `idx_acl_resource` (tenant_id, app_name, resource_type, resource_id) | pep-proxy 按资源查权限 |
| `idx_acl_subject` (tenant_id, app_name, resource_type, subject_type, subject_id) | ext_proc 请求阶段按用户查可访问资源列表 |
| `idx_acl_cleanup` (app_name, resource_type, resource_id) | 资源删除时跨租户清理 ACL |

---

### 4.5 pending_acl — 写入失败重试队列（租户级）

```mermaid
flowchart LR
    subgraph 谁写
        RS_FAIL[resource-sync<br/>ACL 写入失败时<br/>插入重试记录]
    end

    subgraph pending_acl表
        PENDING[(pending_acl<br/>tenant_id, app_name<br/>resource_id, action<br/>retry_count, next_retry<br/>租户级 · 有 tenant_id)]
    end

    subgraph 谁读
        RS_RETRY[resource-sync<br/>后台定时重试<br/>WHERE next_retry <= NOW<br/>AND retry_count < max_retries]
    end

    RS_FAIL -->|INSERT| PENDING
    PENDING --> RS_RETRY
    RS_RETRY -->|成功后 DELETE<br/>失败后 UPDATE retry_count| PENDING

    style PENDING fill:#845ef7,color:#fff
```

---

### 4.6 Keycloak 内部存储 — 用户/组

```mermaid
flowchart LR
    subgraph 谁写
        KP[keycloak-proxy<br/>用户/组 CRUD API]
        INIT[init-job<br/>初始化基础组]
        SAML[SAML SSO<br/>联邦登录自动创建用户]
    end

    subgraph "keycloak DB"
        KC[(users + groups<br/>+ group_membership<br/>+ clients)]
    end

    subgraph 谁读
        KP2[keycloak-proxy<br/>查询用户/组列表]
        JWT[JWT 签发<br/>把 groups 写入 token]
        PEP[pep-proxy<br/>验证 JWT 时<br/>从 token 读 groups]
    end

    KP -->|Admin API| KC
    INIT -->|Admin API| KC
    SAML -->|登录时| KC
    KC --> KP2
    KC --> JWT
    JWT -.->|groups 在 JWT 里| PEP

    style KC fill:#f06595,color:#fff
```

**关键点：** pep-proxy 不直接读 Keycloak，而是从 JWT 中提取 groups。Keycloak 的数据通过 JWT 间接传递。

---

### 4.7 OPA 内存 — bundle 数据

```mermaid
flowchart LR
    subgraph "iam DB"
        APPS[(apps)]
        PR[(path_rules)]
    end

    subgraph bundle-server
        BS[定时读取<br/>转换格式<br/>推送 bundle]
    end

    subgraph OPA 内存
        OPADATA[(apps 快照<br/>+ path_rules 快照)]
    end

    subgraph pep-proxy
        PEP[调用 OPA<br/>路径级鉴权]
    end

    APPS --> BS
    PR --> BS
    BS -->|HTTP Bundle API<br/>定时推送| OPADATA
    OPADATA --> PEP

    style OPADATA fill:#ffd43b,color:#000
```

**OPA bundle 数据结构（系统级，无租户嵌套）：**

```json
{
  "apps": {
    "knowledgebase": { "path_prefix": "/knowledgebase/", "enabled": true },
    "memory": { "path_prefix": "/memory/", "enabled": false }
  },
  "path_rules": [
    { "path_prefix": "/memory/v1/admin/", "method": null, "required_groups": ["memory-admins"] },
    { "path_prefix": "/kb/knowledge_bases/add", "method": "POST", "required_groups": ["kb-admins"] }
  ]
}
```

| 数据 | 来源 | 同步方式 | 延迟 |
|------|------|---------|------|
| apps (enabled 状态) | iam DB → bundle-server → OPA | 定时推送（秒级） | 规则变更后几秒生效 |
| path_rules | iam DB → bundle-server → OPA | 定时推送（秒级） | 同上 |
| **resource_acl** | **不推送到 OPA** | **pep-proxy 直接查 iam DB** | **实时** |

**为什么 resource_acl 不推 OPA：**

```
apps + path_rules：几百条数据，适合全量加载到内存
resource_acl：几百万条数据，推到 OPA 内存会爆

所以：
  路径级鉴权 → OPA 内存（快，微秒级）
  资源级鉴权 → 直接查 PostgreSQL（有索引，毫秒级）
```

---

## 5 组件读写总览

| 组件 | 数据库 | 读/写 | 操作的表 |
|------|--------|-------|---------|
| keycloak-proxy | iam | 读/写 | apps, resource_patterns, resource_actions, api_keys |
| bundle-server | iam | 只读 | apps, path_rules |
| pep-proxy | iam | 读/写 | apps（启动加载）, resource_patterns（启动加载）, resource_actions（启动加载）, resource_acl（查询）, path_rules（读/写）, api_keys（查询） |
| resource-sync | iam | 读/写 | apps（读）, resource_patterns（读）, resource_actions（读）, resource_acl（读/写）, pending_acl（读/写） |
| init-job | iam | 写 | apps, path_rules, resource_patterns, resource_actions |
| Keycloak | keycloak | 读/写 | 内部表 |
| OPAL Server | opal | 读/写 | pub/sub 内部 |

---

## 6 数据同步流程

### 6.1 管理员注册新应用时的数据流

```mermaid
sequenceDiagram
    participant ADMIN as 管理员
    participant KP as keycloak-proxy
    participant IAM as iam DB
    participant KC as Keycloak
    participant BS as bundle-server
    participant OPA as OPA

    ADMIN->>KP: POST /api/v1/apps<br/>{ app_name: "app3", path_prefix: "/app3/" }

    par 并行写入
        KP->>IAM: INSERT INTO apps
        KP->>IAM: INSERT INTO resource_patterns
        KP->>KC: 创建组 app3-admins
    end

    KP-->>ADMIN: 201 注册成功

    Note over BS,OPA: 几秒后...
    BS->>IAM: SELECT * FROM apps, path_rules
    BS->>OPA: 推送更新后的 bundle
    Note over OPA: OPA 内存更新<br/>app3 可用
```

### 6.2 用户创建资源时的数据流（ext_proc 模式）

```mermaid
sequenceDiagram
    participant USER as 用户
    participant GW as Envoy Gateway
    participant PEP as pep-proxy
    participant OPA as OPA
    participant APP as 后端应用
    participant RS as resource-sync<br/>（ext_proc）
    participant IAM as iam DB

    USER->>GW: POST /knowledgebase/v1/kb
    GW->>PEP: 路径鉴权
    PEP->>OPA: 查询
    OPA-->>PEP: allow（从内存读 apps + path_rules）
    PEP-->>GW: 通过

    GW->>APP: 转发请求
    APP-->>GW: 201 { "id": "kb-001" }

    GW->>RS: ext_proc 调用<br/>（响应阶段）
    RS->>RS: 从启动加载的 apps 表<br/>匹配 /knowledgebase/ → app_name=knowledgebase
    RS->>IAM: 读 resource_patterns<br/>匹配 /v1/kb → resource_type=kb
    RS->>IAM: INSERT INTO resource_acl<br/>(tenant_id, kb-001, user, zhangsan, owner)

    GW-->>USER: 201

    Note over PEP,IAM: 下次访问 kb-001 时
    PEP->>PEP: 从启动加载的 apps 表<br/>匹配 /knowledgebase/ → app_name=knowledgebase
    PEP->>PEP: 从启动加载的 resource_patterns<br/>匹配 /v1/kb → resource_type=kb
    PEP->>IAM: SELECT FROM resource_acl<br/>WHERE tenant_id=? AND resource_id='kb-001'
    Note over PEP: 实时查询，无同步延迟
```

**关键区别：** resource-sync 不是反向代理，而是通过 Envoy ext_proc 协议被 Gateway 调用。Gateway 在收到后端 201 响应后，通过 ext_proc 通知 resource-sync 进行 ACL 同步。

### 6.3 License 变更时的数据流

```mermaid
sequenceDiagram
    participant ADMIN as 管理员
    participant KP as keycloak-proxy
    participant IAM as iam DB
    participant BS as bundle-server
    participant OPA as OPA
    participant USER as 用户
    participant PEP as pep-proxy

    ADMIN->>KP: PUT /api/v1/apps/app3<br/>{ "enabled": false }
    KP->>IAM: UPDATE apps SET enabled=false<br/>WHERE app_name='app3'
    KP-->>ADMIN: 200

    Note over BS,OPA: 几秒后...
    BS->>IAM: SELECT * FROM apps
    BS->>OPA: 推送更新 bundle<br/>app3.enabled=false

    USER->>PEP: GET /app3/v1/data
    PEP->>OPA: 路径鉴权
    OPA->>OPA: app3.enabled=false → app_disabled
    OPA-->>PEP: deny
    PEP-->>USER: 403 应用未授权

    Note over USER: 几秒的窗口期内<br/>OPA 还没收到更新<br/>可能放行 1-2 个请求
```

---

## 7 数据一览表

| 数据 | 存储位置 | 级别 | 写入者 | 读取者 | 数据量 | 同步方式 |
|------|---------|------|--------|--------|--------|---------|
| 用户/组 | keycloak DB | - | keycloak-proxy, init-job, SAML | JWT → pep-proxy | 1w 用户 | JWT 携带 |
| apps | iam DB | 系统级 | keycloak-proxy, init-job | bundle-server → OPA, pep-proxy, resource-sync | 几十条 | 定时推送（秒级延迟）；pep-proxy、resource-sync 启动时加载 |
| path_rules | iam DB | 系统级 | pep-proxy, init-job | bundle-server → OPA | 几十条 | 定时推送（秒级延迟） |
| resource_patterns | iam DB | 系统级 | keycloak-proxy, init-job | pep-proxy, resource-sync | 每应用 1-3 条 | 启动时加载 |
| resource_actions | iam DB | 系统级 | keycloak-proxy, init-job | pep-proxy, resource-sync | 每资源 0-6 条 | 启动时加载（标准 RESTful 无需记录，用代码默认规则） |
| **resource_acl** | **iam DB** | **租户级** | **resource-sync** | **pep-proxy（鉴权）, resource-sync（ACL API + ext_proc）** | **约 300 万条** | **直接查数据库（实时）** |
| pending_acl | iam DB | 租户级 | resource-sync（写入失败时） | resource-sync（后台重试） | 通常为 0 | 后台定时重试 |
| api_keys | iam DB | 租户级 | keycloak-proxy | pep-proxy（认证） | 几十至几百条 | 直接查数据库（实时） |
| OPA bundle | OPA 内存 | 系统级 | bundle-server | pep-proxy | 几 KB | 定时推送 |

---

## 8 数据量预估

| 表 | 预估数据量 | 说明 |
|----|-----------|------|
| apps | 几十条 | 系统级，应用数量有限 |
| path_rules | 几十条 | 系统级，保护规则有限 |
| resource_patterns | 每应用 1-3 条 | 系统级，含 ID 提取规则 |
| resource_actions | 每资源 0-6 条 | 系统级，标准 RESTful 无需记录 |
| resource_acl | 约 300 万条 | 200 租户 x 5 应用 x 1000 资源 x 3 ACL |
| pending_acl | 通常为 0 | 仅在写入失败时产生，成功重试后删除 |
| api_keys | 几十至几百条 | 租户级，外部应用接入凭证 |
