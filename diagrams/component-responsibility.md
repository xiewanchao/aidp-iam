# 组件职责视角 — 谁负责什么、不负责什么

> 版本：v1.0 | 日期：2026-04-02

---

## 1 组件全景

```mermaid
flowchart TB
    subgraph 用户层
        USER[用户浏览器/客户端]
    end

    subgraph Gateway层
        GW[AgentGateway<br/>HTTPS Terminate + 路由]
    end

    subgraph 鉴权层
        PEP[pep-proxy<br/>JWT验证 + 鉴权决策]
        OPA[OPA<br/>路径级策略引擎]
    end

    subgraph 资源权限层
        RS[resource-sync<br/>资源代理 + ACL管理]
    end

    subgraph IAM管理层
        KP[keycloak-proxy<br/>用户/组/应用管理API]
        KC[Keycloak<br/>身份认证 + JWT签发]
        BS[bundle-server<br/>策略数据推送]
    end

    subgraph 应用层
        APP1[记忆库]
        APP2[知识库]
        APP3[应用N...]
    end

    subgraph 数据层
        PG[(PostgreSQL)]
    end

    USER -->|HTTPS| GW
    GW -->|ext-authz| PEP
    PEP -->|策略查询| OPA
    PEP -->|资源鉴权| PG
    GW -->|业务请求| RS
    GW -->|管理API| KP
    GW -->|登录认证| KC
    RS -->|转发| APP1
    RS -->|转发| APP2
    RS -->|转发| APP3
    RS -->|读写 resource_acl| PG
    KP -->|用户/组管理| KC
    KP -->|读写 apps, path_rules| PG
    BS -->|读 apps, path_rules| PG
    BS -->|推送 bundle| OPA

    style GW fill:#4a9eff,color:#fff
    style PEP fill:#ff6b6b,color:#fff
    style OPA fill:#ffd43b,color:#000
    style RS fill:#51cf66,color:#fff
    style KP fill:#ff922b,color:#fff
    style KC fill:#f06595,color:#fff
    style BS fill:#20c997,color:#fff
    style PG fill:#845ef7,color:#fff
```

---

## 2 每个组件的职责边界

### 2.1 Gateway（蓝色）

```mermaid
flowchart LR
    subgraph 做什么
        A1[HTTPS TLS Terminate]
        A2[路由匹配 + URL Rewrite]
        A3[调用 ext-authz 鉴权]
        A4[TrafficPolicy tracing]
    end

    subgraph 不做什么
        B1[不做 JWT 验证]
        B2[不做业务逻辑]
        B3[不读写数据库]
        B4[不做资源级鉴权]
    end

    style A1 fill:#4a9eff,color:#fff
    style A2 fill:#4a9eff,color:#fff
    style A3 fill:#4a9eff,color:#fff
    style A4 fill:#4a9eff,color:#fff
    style B1 fill:#dee2e6,color:#000
    style B2 fill:#dee2e6,color:#000
    style B3 fill:#dee2e6,color:#000
    style B4 fill:#dee2e6,color:#000
```

| 做 | 不做 |
|---|------|
| 接收 HTTPS 请求，解密 | 不验证 JWT |
| 按路径匹配路由到后端 | 不做任何业务逻辑 |
| 调 pep-proxy 做鉴权 | 不直接读写数据库 |
| URL Rewrite（`/knowledgebase/v1/kb` → `/v1/kb`） | 不做资源级权限判断 |
| 采集 trace 数据（TrafficPolicy） | 不管证书续期（cert-manager 管） |

---

### 2.2 pep-proxy（红色）

```mermaid
flowchart LR
    subgraph 做什么
        A1[JWT 签名验证]
        A2[提取 user_id / tenant_id / groups]
        A3[调 OPA 做路径级鉴权]
        A4[查 resource_acl 做资源实例鉴权]
        A5[权限-操作映射检查]
        A6[注入 X-Auth-* Header]
    end

    subgraph 不做什么
        B1[不签发 JWT]
        B2[不管理用户/组]
        B3[不写 resource_acl]
        B4[不转发业务请求]
        B5[不提供资源 ID 列表查询]
    end

    style A1 fill:#ff6b6b,color:#fff
    style A2 fill:#ff6b6b,color:#fff
    style A3 fill:#ff6b6b,color:#fff
    style A4 fill:#ff6b6b,color:#fff
    style A5 fill:#ff6b6b,color:#fff
    style A6 fill:#ff6b6b,color:#fff
    style B1 fill:#dee2e6,color:#000
    style B2 fill:#dee2e6,color:#000
    style B3 fill:#dee2e6,color:#000
    style B4 fill:#dee2e6,color:#000
    style B5 fill:#dee2e6,color:#000
```

| 做 | 不做 |
|---|------|
| 验证 JWT 签名是否合法 | 不签发 JWT（Keycloak 做） |
| 从 JWT 提取用户信息 | 不管理用户/组（keycloak-proxy 做） |
| 调 OPA 判断路径权限 | **不写入 resource_acl**（resource-sync 做） |
| 查 resource_acl 判断资源实例权限（有资源 ID 时） | 不转发业务请求（Gateway → resource-sync 做） |
| 检查权限-操作映射（viewer 不能 PUT 等） | **不提供资源 ID 列表查询**（resource-sync 内部接口做） |
| 注入 `X-Auth-User-Id`, `X-Auth-Tenant`, `X-Auth-Groups` | 不注入 `X-Auth-Permission`（后端不需要，鉴权已全部完成） |

**读写分离原则：pep-proxy 只读 resource_acl（做单资源鉴权决策），resource-sync 写 resource_acl + 提供内部查询 API。**

**鉴权分两步：**

```mermaid
flowchart TD
    REQ[请求进来] --> STEP1{第1步：OPA 路径鉴权}
    STEP1 -->|app未启用| DENY1[403 应用未授权]
    STEP1 -->|路径被保护,不在指定group| DENY2[403 无路径权限]
    STEP1 -->|通过| MATCH{匹配 resource_patterns?}

    MATCH -->|不匹配<br/>/v1/search, /v1/health| PASS_OTHER[直接放行<br/>不查 resource_acl<br/>后端按需调内部接口过滤]

    MATCH -->|匹配| SEGMENTS{路径段数?<br/>去掉 resource_prefix 后}

    SEGMENTS -->|0段<br/>GET /v1/kb 或 POST /v1/kb| PASS_COLLECTION[放行<br/>GET: 后端调内部接口拿 ID 列表<br/>POST: 创建顶级资源, OPA 已控制]

    SEGMENTS -->|1段: /v1/kb/kb-001| CHECK1[查 resource_acl<br/>用户对 kb-001 的权限]
    CHECK1 -->|无记录| DENY3[403 无资源权限]
    CHECK1 -->|有记录| METHOD1{权限够吗?<br/>GET→viewer<br/>PUT/PATCH→contributor<br/>DELETE→owner}
    METHOD1 -->|够| PASS1[放行]
    METHOD1 -->|不够| DENY4[403 权限不足]

    SEGMENTS -->|2段以上: /v1/kb/kb-001/docs| CHECK2[查 resource_acl<br/>用户对父资源 kb-001 的权限]
    CHECK2 -->|无记录| DENY5[403 无父资源权限]
    CHECK2 -->|有记录| METHOD2{权限够吗?<br/>GET→viewer<br/>POST/PUT/PATCH→contributor<br/>DELETE→contributor}
    METHOD2 -->|够| PASS2[放行]
    METHOD2 -->|不够| DENY6[403 权限不足]

    style DENY1 fill:#ff6b6b,color:#fff
    style DENY2 fill:#ff6b6b,color:#fff
    style DENY3 fill:#ff6b6b,color:#fff
    style DENY4 fill:#ff6b6b,color:#fff
    style DENY5 fill:#ff6b6b,color:#fff
    style DENY6 fill:#ff6b6b,color:#fff
    style PASS_OTHER fill:#51cf66,color:#fff
    style PASS_COLLECTION fill:#51cf66,color:#fff
    style PASS1 fill:#51cf66,color:#fff
    style PASS2 fill:#51cf66,color:#fff
```

**三层创建权限：**

| 创建类型 | 路径示例 | 谁控制 | 怎么控制 |
|---------|---------|--------|---------|
| 管理员资源 | POST /v1/admin/templates | OPA + path_rules | 路径需要 `{app}-admins` 组 |
| 顶级资源 | POST /v1/kb | OPA | 未命中 path_rules + all-users → 放行 |
| 子资源 | POST /v1/kb/kb-001/docs | pep-proxy + resource_acl | 检查父资源权限，需 contributor 以上 |

---

### 2.3 OPA（黄色）

```mermaid
flowchart LR
    subgraph 做什么
        A1[路径级策略判断]
        A2[app 是否启用]
        A3[master-admins / tenant-admins 角色判断]
        A4[path_rules 规则匹配]
    end

    subgraph 不做什么
        B1[不做资源级鉴权]
        B2[不查 resource_acl]
        B3[不验证 JWT]
        B4[不读数据库]
    end

    style A1 fill:#ffd43b,color:#000
    style A2 fill:#ffd43b,color:#000
    style A3 fill:#ffd43b,color:#000
    style A4 fill:#ffd43b,color:#000
    style B1 fill:#dee2e6,color:#000
    style B2 fill:#dee2e6,color:#000
    style B3 fill:#dee2e6,color:#000
    style B4 fill:#dee2e6,color:#000
```

| 做 | 不做 |
|---|------|
| 纯内存策略计算（微秒级） | **不做资源实例级鉴权**（数据量太大） |
| 判断 app 是否启用（apps.enabled） | 不查 resource_acl 表 |
| 判断系统角色（master-admins, tenant-admins） | 不验证 JWT（pep-proxy 做） |
| 匹配 path_rules 保护规则 | 不直接读数据库（bundle-server 推送） |

---

### 2.4 resource-sync（绿色）— 新增核心组件

```mermaid
flowchart LR
    subgraph 做什么
        A1[反向代理: 转发请求给后端]
        A2[自动注册: POST成功 → 写入owner ACL]
        A3[自动清理: DELETE成功 → 删除所有ACL]
        A4[ACL API: 分享/取消分享/查权限<br/>端口8080, 路径/acl/v1/permissions]
        A5[内部查询API: 查可访问资源ID列表<br/>端口8081, 路径/internal/v1/resources]
    end

    subgraph 不做什么
        B1[不做 JWT 验证]
        B2[不做鉴权决策（路径级和资源级都不做）]
        B3[不管理用户/组]
        B4[GET/PUT/PATCH 完全透传]
    end

    style A1 fill:#51cf66,color:#fff
    style A2 fill:#51cf66,color:#fff
    style A3 fill:#51cf66,color:#fff
    style A4 fill:#51cf66,color:#fff
    style A5 fill:#51cf66,color:#fff
    style B1 fill:#dee2e6,color:#000
    style B2 fill:#dee2e6,color:#000
    style B3 fill:#dee2e6,color:#000
    style B4 fill:#dee2e6,color:#000
```

| 做 | 不做 |
|---|------|
| 作为后端的反向代理，转发请求 | 不验证 JWT（pep-proxy 做） |
| POST 成功 → 自动写入 owner ACL | 不做路径级鉴权（OPA 做） |
| DELETE 成功 → 自动删除资源所有 ACL | 不做资源级鉴权决策（pep-proxy 做） |
| 提供 ACL 管理 API（/acl/v1/permissions，端口 8080） | 不管理用户/组（keycloak-proxy 做） |
| 提供内部查询 API（/internal/v1/resources，端口 8081） | GET/PUT/PATCH 完全透传，不解析响应体 |

**两个端口两个职责：**
- 8080：对外（走 Gateway）— 反向代理 + ACL 管理 API
- 8081：对内（集群内直连）— 内部查询 API，后端 list/search 时调用

**核心原则：resource-sync 管"ACL 数据的写入和查询"，pep-proxy 管"鉴权决策"。**

**resource-sync 的处理逻辑：**

```mermaid
flowchart TD
    REQ[收到请求] --> METHOD{HTTP 方法?}

    METHOD -->|GET| FWD1[直接透传给后端<br/>不做任何处理]
    METHOD -->|PUT/PATCH| FWD1

    METHOD -->|POST| MATCH_POST{命中 resource_patterns?<br/>且路径段数=0?}
    MATCH_POST -->|是: POST /v1/kb| FWD2[转发给后端]
    MATCH_POST -->|否| FWD1
    FWD2 --> STATUS1{响应 201?}
    STATUS1 -->|是| CREATE[从响应体提取 id<br/>写入 resource_acl<br/>permission=owner]
    STATUS1 -->|否| RETURN1[直接返回]

    METHOD -->|DELETE| MATCH_DEL{命中 resource_patterns?<br/>且路径段数=1?}
    MATCH_DEL -->|是: DELETE /v1/kb/kb-001| FWD3[转发给后端]
    MATCH_DEL -->|否| FWD1
    FWD3 --> STATUS2{响应 2xx?}
    STATUS2 -->|是| DELETE_ACL[从 URL 提取 resource_id<br/>删除该资源所有 ACL]
    STATUS2 -->|否| RETURN2[直接返回]

    CREATE --> RETURN3[返回响应给用户]
    DELETE_ACL --> RETURN4[返回响应给用户]

    style CREATE fill:#51cf66,color:#fff
    style DELETE_ACL fill:#ff6b6b,color:#fff
    style FWD1 fill:#dee2e6,color:#000
```

**注意：子资源（POST /v1/kb/kb-001/docs）的创建不触发 resource-sync 的 ACL 写入。** 子资源的权限继承父资源——能访问 kb-001 就能访问它下面的文档，不需要每个子资源单独一条 ACL。

**为什么 GET 不需要 resource-sync 处理：**

```
GET /v1/kb/kb-001（单个资源）：
  pep-proxy 查 resource_acl 做鉴权 → 通过或 403
  resource-sync 纯透传
  → 不需要 resource-sync 参与

GET /v1/kb（集合列表）：
  pep-proxy 只做路径鉴权，直接放行
  resource-sync 纯透传
  后端主动调 resource-sync 内部接口获取可访问 ID 列表
  后端按 ID 列表过滤返回

所有 GET 的鉴权在 pep-proxy 完成，
列表过滤由后端调内部接口完成，
resource-sync 在转发链路上只做透传。
```

---

### 2.5 keycloak-proxy（橙色）

```mermaid
flowchart LR
    subgraph 做什么
        A1[用户 CRUD API]
        A2[组 CRUD API]
        A3[应用注册 API<br/>自动创建 app-admins 组]
        A4[路径规则 CRUD API]
        A5[租户管理 API]
        A6[SAML IdP 配置 API]
    end

    subgraph 不做什么
        B1[不做鉴权]
        B2[不管 resource_acl]
        B3[不转发业务请求]
    end

    style A1 fill:#ff922b,color:#fff
    style A2 fill:#ff922b,color:#fff
    style A3 fill:#ff922b,color:#fff
    style A4 fill:#ff922b,color:#fff
    style A5 fill:#ff922b,color:#fff
    style A6 fill:#ff922b,color:#fff
    style B1 fill:#dee2e6,color:#000
    style B2 fill:#dee2e6,color:#000
    style B3 fill:#dee2e6,color:#000
```

| 做 | 不做 |
|---|------|
| 用户/组增删改查（调 Keycloak Admin API） | 不做任何鉴权判断 |
| 应用注册（写 apps 表 + 自动创建 {app}-admins 组） | **不管 resource_acl**（resource-sync 管） |
| 路径保护规则增删改查（写 path_rules 表） | 不转发业务请求 |
| 租户创建（创建 Keycloak Realm） | |
| SAML IdP 配置（导入元数据、创建映射） | |

---

### 2.6 Keycloak（粉色）

| 做 | 不做 |
|---|------|
| 用户/组存储 | 不对外暴露 API（keycloak-proxy 封装） |
| JWT 签发 | 不做鉴权判断 |
| OIDC/SAML 联邦登录 | 不管业务权限 |
| Session 管理 | |

### 2.7 bundle-server（青色）

| 做 | 不做 |
|---|------|
| 从 PostgreSQL 读 apps + path_rules | 不读 resource_acl（数据量太大，不适合推 OPA） |
| 转换为 OPA bundle 格式 | 不做鉴权 |
| 推送到 OPA | |

### 2.8 后端应用（灰色）

| 做 | 不做 |
|---|------|
| 纯业务逻辑（CRUD 数据） | **不做任何鉴权判断**（pep-proxy 已全部完成） |
| 读 `X-Auth-User-Id` 做数据归属 | 不验证 JWT |
| list/search 时调 resource-sync 内部接口获取可访问 ID 列表 | 不检查 permission（pep-proxy 已按方法拦截） |
| | 不维护权限表 |

---

## 3 关键分工对比

```mermaid
flowchart LR
    subgraph 鉴权决策<br/>pep-proxy
        P1[路径能不能访问?]
        P2[资源有没有权限?]
    end

    subgraph 资源同步<br/>resource-sync
        R1[创建 → 注册 owner]
        R2[删除 → 清理 ACL]
        R3[分享 → ACL API]
    end

    subgraph 策略计算<br/>OPA
        O1[app 是否启用?]
        O2[path_rules 匹配]
    end

    subgraph 身份管理<br/>keycloak-proxy
        K1[用户/组 CRUD]
        K2[应用注册]
    end

    style P1 fill:#ff6b6b,color:#fff
    style P2 fill:#ff6b6b,color:#fff
    style R1 fill:#51cf66,color:#fff
    style R2 fill:#51cf66,color:#fff
    style R3 fill:#51cf66,color:#fff
    style O1 fill:#ffd43b,color:#000
    style O2 fill:#ffd43b,color:#000
    style K1 fill:#ff922b,color:#fff
    style K2 fill:#ff922b,color:#fff
```

| 维度 | pep-proxy | resource-sync | OPA | keycloak-proxy | 后端应用 |
|------|-----------|---------------|-----|----------------|---------|
| **核心职责** | 鉴权决策 | ACL 数据管理 | 策略计算 | 身份管理 | 纯业务 |
| **读 resource_acl** | ✅ 单资源鉴权 | ✅ ACL API + 内部查询 API | ❌ | ❌ | ❌（通过内部接口间接读） |
| **写 resource_acl** | ❌ | ✅ 自动同步 + ACL API | ❌ | ❌ | ❌ |
| **读 OPA** | ✅ 调用查询 | ❌ | — | ❌ | ❌ |
| **在请求链路上** | ✅ ext-authz | ✅ 反向代理 | ✅ 被调用 | ❌ 独立 API | ✅ 最终处理 |
| **暴露端口** | — | 8080 对外 + 8081 对内 | — | — | — |

**职责分离总结：**

```
resource_acl 表：
  写入 → resource-sync（自动同步 + ACL API）
  鉴权读 → pep-proxy（单资源实例鉴权）
  列表读 → resource-sync 内部 API → 后端应用调用
```
