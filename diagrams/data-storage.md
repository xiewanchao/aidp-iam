# 数据存储视角 — 数据在哪、谁读谁写、怎么同步

> 版本：v1.0 | 日期：2026-04-02

---

## 1 数据存储全景

```mermaid
flowchart TB
    subgraph PostgreSQL
        APPS[(apps<br/>应用注册 + license)]
        PR[(path_rules<br/>路径保护规则)]
        RP[(resource_patterns<br/>资源路径匹配规则)]
        ACL[(resource_acl<br/>资源权限)]
        PENDING[(pending_acl<br/>写入失败重试队列)]
    end

    subgraph Keycloak 内部存储
        USERS[(users<br/>用户)]
        GROUPS[(groups<br/>组 + 组成员)]
        CLIENTS[(clients<br/>OIDC 客户端)]
    end

    subgraph OPA 内存
        OPADATA[(bundle 数据<br/>apps + path_rules 快照)]
    end

    subgraph 组件
        KP[keycloak-proxy]
        BS[bundle-server]
        RS[resource-sync]
        PEP[pep-proxy]
        INIT[init-job]
    end

    %% 写入关系
    KP -->|写| APPS
    KP -->|写| PR
    KP -->|写| USERS
    KP -->|写| GROUPS
    INIT -->|写| APPS
    INIT -->|写| PR
    INIT -->|写| GROUPS
    RS -->|写| ACL

    %% 读取关系
    BS -->|读| APPS
    BS -->|读| PR
    BS -->|推送| OPADATA
    PEP -->|查询| OPA内存
    PEP -->|查询| ACL
    PEP -->|读| RP
    RS -->|读| RP
    RS -->|读写| ACL

    style APPS fill:#4a9eff,color:#fff
    style PR fill:#4a9eff,color:#fff
    style RP fill:#4a9eff,color:#fff
    style ACL fill:#845ef7,color:#fff
    style USERS fill:#f06595,color:#fff
    style GROUPS fill:#f06595,color:#fff
    style CLIENTS fill:#f06595,color:#fff
    style OPADATA fill:#ffd43b,color:#000
```

---

## 2 每张表的读写关系

### 2.1 apps — 应用注册表

```mermaid
flowchart LR
    subgraph 谁写
        INIT[init-job<br/>部署时写入默认应用]
        KP[keycloak-proxy<br/>管理员注册新应用<br/>License 开关]
    end

    subgraph apps表
        APPS[(apps<br/>tenant_id, app_name<br/>path_prefix, enabled)]
    end

    subgraph 谁读
        BS[bundle-server<br/>转换为 OPA bundle]
        KP2[keycloak-proxy<br/>GET /api/v1/apps]
        RS[resource-sync<br/>根据 path_prefix<br/>确定转发目标]
    end

    INIT -->|INSERT<br/>ON CONFLICT DO NOTHING| APPS
    KP -->|INSERT / UPDATE| APPS
    APPS --> BS
    APPS --> KP2
    APPS --> RS

    style APPS fill:#4a9eff,color:#fff
```

| 字段 | 写入者 | 读取者 | 用途 |
|------|--------|--------|------|
| `tenant_id` | init-job, keycloak-proxy | bundle-server | 租户隔离 |
| `app_name` | init-job, keycloak-proxy | 所有读取者 | 应用标识 |
| `path_prefix` | init-job, keycloak-proxy | bundle-server, resource-sync | 路由匹配 |
| `enabled` | keycloak-proxy | bundle-server → OPA | License 控制 |

---

### 2.2 path_rules — 路径保护规则表

```mermaid
flowchart LR
    subgraph 谁写
        INIT[init-job<br/>默认规则]
        KP[keycloak-proxy<br/>管理员配置]
    end

    subgraph path_rules表
        PR[(path_rules<br/>tenant_id, path_prefix<br/>required_group)]
    end

    subgraph 谁读
        BS[bundle-server<br/>转换为 OPA bundle]
    end

    INIT -->|INSERT<br/>ON CONFLICT DO NOTHING| PR
    KP -->|INSERT / UPDATE / DELETE| PR
    PR --> BS

    style PR fill:#4a9eff,color:#fff
```

| 字段 | 写入者 | 读取者 | 用途 |
|------|--------|--------|------|
| `path_prefix` | init-job, keycloak-proxy | bundle-server → OPA | 哪些路径需要保护 |
| `required_group` | init-job, keycloak-proxy | bundle-server → OPA | 需要哪个组才能访问 |

---

### 2.3 resource_patterns — 资源路径匹配规则

```mermaid
flowchart LR
    subgraph 谁写
        INIT[init-job<br/>默认配置]
        KP[keycloak-proxy<br/>注册应用时自动写入]
    end

    subgraph resource_patterns表
        RP[(resource_patterns<br/>tenant_id, app_name<br/>resource_prefix<br/>resource_type)]
    end

    subgraph 谁读
        RS[resource-sync<br/>判断哪些路径需要<br/>自动同步 ACL]
    end

    INIT -->|INSERT| RP
    KP -->|INSERT| RP
    RP --> RS

    style RP fill:#4a9eff,color:#fff
```

| 字段 | 写入者 | 读取者 | 用途 |
|------|--------|--------|------|
| `app_name` | init-job, keycloak-proxy | resource-sync | 关联到哪个应用 |
| `resource_prefix` | init-job, keycloak-proxy | resource-sync | 匹配路径前缀（如 `/v1/kb`） |
| `resource_type` | init-job, keycloak-proxy | resource-sync | 写入 ACL 时标识资源类型 |

**示例数据：**

```
| tenant_id | app_name       | resource_prefix | resource_type |
|-----------|----------------|-----------------|---------------|
| aidp      | knowledgebase  | /v1/kb          | kb            |
| aidp      | memory         | /v1/memories    | memory        |
```

---

### 2.4 resource_acl — 资源权限表（核心新增）

```mermaid
flowchart LR
    subgraph 谁写
        RS_AUTO[resource-sync<br/>自动同步<br/>POST→加owner<br/>DELETE→删ACL]
        RS_API[resource-sync<br/>ACL API<br/>分享/取消分享]
    end

    subgraph resource_acl表
        ACL[(resource_acl<br/>tenant_id, app_name<br/>resource_type, resource_id<br/>subject_type, subject_id<br/>permission)]
    end

    subgraph 谁读
        PEP[pep-proxy<br/>资源实例鉴权<br/>这个用户对这个资源有没有权限?]
        RS_QUERY[resource-sync<br/>ACL API 查询<br/>GET /acl/v1/permissions]
        RS_INTERNAL[resource-sync 内部 API<br/>GET /internal/v1/resources<br/>后端 list/search 时调用]
    end

    RS_AUTO -->|INSERT / DELETE| ACL
    RS_API -->|INSERT / UPDATE / DELETE| ACL
    ACL --> PEP
    ACL --> RS_QUERY
    ACL --> RS_INTERNAL

    style ACL fill:#845ef7,color:#fff
```

| 字段 | 写入者 | 读取者 | 用途 |
|------|--------|--------|------|
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

pep-proxy 每次请求查 1 条（按 resource_id + subject_id 精确查询）
有联合索引，查询 < 1ms
```

---

### 2.5 Keycloak 内部存储 — 用户/组

```mermaid
flowchart LR
    subgraph 谁写
        KP[keycloak-proxy<br/>用户/组 CRUD API]
        INIT[init-job<br/>初始化基础组]
        SAML[SAML SSO<br/>联邦登录自动创建用户]
    end

    subgraph Keycloak
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

### 2.6 OPA 内存 — bundle 数据

```mermaid
flowchart LR
    subgraph PostgreSQL
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

| 数据 | 来源 | 同步方式 | 延迟 |
|------|------|---------|------|
| apps (enabled 状态) | PostgreSQL → bundle-server → OPA | 定时推送（秒级） | 规则变更后几秒生效 |
| path_rules | PostgreSQL → bundle-server → OPA | 定时推送（秒级） | 同上 |
| **resource_acl** | **不推送到 OPA** | **pep-proxy 直接查数据库** | **实时** |

**为什么 resource_acl 不推 OPA：**

```
apps + path_rules：几百条数据，适合全量加载到内存
resource_acl：几百万条数据，推到 OPA 内存会爆

所以：
  路径级鉴权 → OPA 内存（快，微秒级）
  资源级鉴权 → 直接查 PostgreSQL（有索引，毫秒级）
```

---

## 3 数据同步流程

### 3.1 管理员注册新应用时的数据流

```mermaid
sequenceDiagram
    participant ADMIN as 管理员
    participant KP as keycloak-proxy
    participant PG as PostgreSQL
    participant KC as Keycloak
    participant BS as bundle-server
    participant OPA as OPA

    ADMIN->>KP: POST /api/v1/apps<br/>{ app_name: "app3", path_prefix: "/app3/" }

    par 并行写入
        KP->>PG: INSERT INTO apps
        KP->>PG: INSERT INTO resource_patterns
        KP->>KC: 创建组 app3-admins
    end

    KP-->>ADMIN: 201 注册成功

    Note over BS,OPA: 几秒后...
    BS->>PG: SELECT * FROM apps, path_rules
    BS->>OPA: 推送更新后的 bundle
    Note over OPA: OPA 内存更新<br/>app3 可用
```

### 3.2 用户创建资源时的数据流

```mermaid
sequenceDiagram
    participant USER as 用户
    participant PEP as pep-proxy
    participant OPA as OPA
    participant RS as resource-sync
    participant APP as 后端应用
    participant PG as PostgreSQL

    USER->>PEP: POST /knowledgebase/v1/kb
    PEP->>OPA: 路径鉴权
    OPA-->>PEP: allow（从内存读 apps + path_rules）
    PEP-->>USER: 通过

    USER->>RS: 请求到达 resource-sync
    RS->>APP: 转发
    APP-->>RS: 201 { "id": "kb-001" }

    RS->>PG: 读 resource_patterns<br/>匹配 /v1/kb → resource_type=kb
    RS->>PG: INSERT INTO resource_acl<br/>(kb-001, user, zhangsan, owner)

    RS-->>USER: 201

    Note over PEP,PG: 下次访问 kb-001 时
    PEP->>PG: SELECT FROM resource_acl<br/>WHERE resource_id='kb-001'
    Note over PEP: 实时查询，无同步延迟
```

### 3.3 License 变更时的数据流

```mermaid
sequenceDiagram
    participant ADMIN as 管理员
    participant KP as keycloak-proxy
    participant PG as PostgreSQL
    participant BS as bundle-server
    participant OPA as OPA
    participant USER as 用户
    participant PEP as pep-proxy

    ADMIN->>KP: PUT /api/v1/apps/app3<br/>{ "enabled": false }
    KP->>PG: UPDATE apps SET enabled=false<br/>WHERE app_name='app3'
    KP-->>ADMIN: 200

    Note over BS,OPA: 几秒后...
    BS->>PG: SELECT * FROM apps
    BS->>OPA: 推送更新 bundle<br/>app3.enabled=false

    USER->>PEP: GET /app3/v1/data
    PEP->>OPA: 路径鉴权
    OPA->>OPA: app3.enabled=false → app_disabled
    OPA-->>PEP: deny
    PEP-->>USER: 403 应用未授权

    Note over USER: 几秒的窗口期内<br/>OPA 还没收到更新<br/>可能放行 1-2 个请求
```

---

## 4 数据一览表

| 数据 | 存储位置 | 写入者 | 读取者 | 数据量 | 同步方式 |
|------|---------|--------|--------|--------|---------|
| 用户/组 | Keycloak | keycloak-proxy, init-job, SAML | JWT → pep-proxy | 1w 用户 | JWT 携带 |
| apps | PostgreSQL | keycloak-proxy, init-job | bundle-server → OPA | 几十条 | 定时推送（秒级延迟） |
| path_rules | PostgreSQL | keycloak-proxy, init-job | bundle-server → OPA | 几十条 | 定时推送（秒级延迟） |
| resource_patterns | PostgreSQL | keycloak-proxy, init-job | resource-sync | 几十条 | resource-sync 启动时加载 |
| **resource_acl** | **PostgreSQL** | **resource-sync** | **pep-proxy（鉴权）, resource-sync（ACL API + 内部查询 API → 后端调用）** | **几百万条** | **直接查数据库（实时）** |
| pending_acl | PostgreSQL | resource-sync（写入失败时） | resource-sync（后台重试） | 通常为 0，故障时几十条 | 后台定时重试 |
| OPA bundle | OPA 内存 | bundle-server | pep-proxy | 几 KB | 定时推送 |
