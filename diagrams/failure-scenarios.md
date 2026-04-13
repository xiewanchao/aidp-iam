# 故障场景视角 — 什么会出错、怎么兜底

> 版本：v2.1 | 日期：2026-04-09

**架构要点：resource-sync 不是反向代理，而是通过 ext_proc 被调用。Gateway 直接路由到后端服务。ext_proc 配置 `failureMode: failOpen`，resource-sync 宕机不影响业务请求。**

**安全要点：Gateway 必须在 ext_authz/ext_proc 处理之前，剥离客户端请求中的 `X-Auth-*` 和 `X-Allowed-Ids` 头，防止客户端伪造内部头信息。**

> **v2.1 新增故障场景：**
> - **2.11 ext_authz forwardBody 未配置** — body 模式 ID 提取失败
> - **2.12 请求体超过 forwardBody.maxSize** — Gateway 返回 413
> - **2.13 api_keys 表查询失败** — API Key 用户被阻断，JWT 用户不受影响
> - **2.14 API Key 哈希不匹配 / 过期 / 禁用** — 正常运行场景，记录响应码

---

## 1 故障影响全景

```mermaid
flowchart TB
    subgraph 组件故障影响
        GW_FAIL["Gateway 挂了<br/>🔴 全部不可用"]
        PEP_FAIL["pep-proxy 挂了<br/>🔴 全部请求 403（ext_authz 失败）"]
        OPA_FAIL["OPA 挂了<br/>🔴 路径鉴权失败"]
        PG_FAIL["PostgreSQL 挂了<br/>🔴 资源鉴权失败 + ACL 同步失败"]
        RS_FAIL["resource-sync 挂了<br/>🟡 业务请求正常！<br/>ACL 不同步（新建/删除无 ACL）"]
        KC_FAIL["Keycloak 挂了<br/>🟡 无法登录/续 token<br/>已登录用户不受影响"]
        EP_FAIL["ext_proc 超时<br/>🟡 failOpen 放行<br/>ACL 未写入 → pending 重试"]
        KP_FAIL["keycloak-proxy 挂了<br/>🟢 管理 API 不可用<br/>业务不受影响"]
        BS_FAIL["bundle-server 挂了<br/>🟢 OPA 用旧数据<br/>业务不受影响"]
    end

    style GW_FAIL fill:#ff6b6b,color:#fff
    style PEP_FAIL fill:#ff6b6b,color:#fff
    style OPA_FAIL fill:#ff6b6b,color:#fff
    style PG_FAIL fill:#ff6b6b,color:#fff
    style RS_FAIL fill:#ffd43b,color:#000
    style KC_FAIL fill:#ffd43b,color:#000
    style EP_FAIL fill:#ffd43b,color:#000
    style KP_FAIL fill:#51cf66,color:#fff
    style BS_FAIL fill:#51cf66,color:#fff
```

| 影响等级 | 含义 |
|---------|------|
| 🔴 严重 | 业务完全不可用 |
| 🟡 中等 | 部分功能受影响，有降级/兜底方案 |
| 🟢 轻微 | 业务不受影响，只影响管理操作 |

### 新旧架构对比（关键改进）

```mermaid
flowchart LR
    subgraph 旧架构
        direction TB
        OLD_REQ[用户请求] --> OLD_GW[Gateway]
        OLD_GW --> OLD_RS[resource-sync<br/>反向代理]
        OLD_RS -->|挂了| OLD_502["🔴 502 Bad Gateway<br/>所有业务全挂"]
        OLD_RS -->|正常| OLD_APP[后端应用]
    end

    subgraph 新架构
        direction TB
        NEW_REQ[用户请求] --> NEW_GW[Gateway]
        NEW_GW --> NEW_APP[后端应用<br/>直接路由]
        NEW_GW -.->|ext_proc 回调| NEW_RS[resource-sync]
        NEW_RS -.->|挂了| NEW_OPEN["🟡 failOpen 放行<br/>业务正常，ACL 延迟同步"]
    end

    style OLD_502 fill:#ff6b6b,color:#fff
    style NEW_OPEN fill:#ffd43b,color:#000
    style OLD_RS fill:#ff6b6b,color:#fff
    style NEW_RS fill:#51cf66,color:#fff
```

> **核心改进：** 旧架构中 resource-sync 宕机 = 全部业务 502。新架构中 resource-sync 宕机 = 业务正常运行，仅 ACL 同步延迟。

---

## 2 逐个场景分析

### 2.1 🟡 ext_proc (resource-sync) 写 ACL 失败（最可能发生）

用户创建资源，后端返回 201，ext_proc 调用 resource-sync 写 ACL 时数据库写入失败。

```mermaid
sequenceDiagram
    participant U as 用户
    participant GW as Gateway
    participant KB as kb-service
    participant EP as ext_proc<br/>(resource-sync:8082)
    participant DB as resource_acl 表
    participant PA as pending_acl 表
    participant RETRY as 后台重试

    U->>GW: POST /knowledgebase/v1/kb
    GW->>KB: 直接路由到后端
    KB-->>GW: 201 { "id": "kb-001" }

    Note over GW: ext_proc 拦截响应

    GW->>EP: 响应回调（201, body 含 kb-001）
    EP->>DB: INSERT resource_acl (kb-001, owner=zhangsan)
    DB-->>EP: ❌ 超时/连接失败

    alt ACL 写入失败 → 写 pending_acl
        EP->>PA: INSERT pending_acl<br/>(kb-001, owner, zhangsan, retry=0)
        PA-->>EP: ✅ 成功
        EP-->>GW: 放行响应
        GW-->>U: 201（资源创建成功）

        Note over RETRY: 后台定时重试 pending_acl
        loop 每 5 秒重试，最多 10 次
            RETRY->>DB: INSERT resource_acl
            DB-->>RETRY: ✅ 成功 → 删除 pending 记录
        end
    end

    alt pending_acl 也写不进去（极端）
        EP->>PA: INSERT pending_acl
        PA-->>EP: ❌ 也失败了
        Note over EP: failOpen → 仍然放行响应
        EP-->>GW: 放行响应
        GW-->>U: 201
        Note over U: ACL 丢失<br/>需人工介入修复
    end
```

**影响：** 用户创建了资源但暂时无法访问，直到 pending 重试成功。极端情况下 pending_acl 也写不进去，需人工介入。

**pending_acl 表：**

```sql
CREATE TABLE pending_acl (
    id              SERIAL PRIMARY KEY,
    tenant_id       VARCHAR(128) NOT NULL,
    app_name        VARCHAR(128) NOT NULL,
    resource_type   VARCHAR(128) NOT NULL,
    resource_id     VARCHAR(256) NOT NULL,
    subject_type    VARCHAR(32) NOT NULL,
    subject_id      VARCHAR(128) NOT NULL,
    permission      VARCHAR(32) NOT NULL,
    action          VARCHAR(16) NOT NULL CHECK (action IN ('create', 'delete')),
    retry_count     INTEGER NOT NULL DEFAULT 0,
    max_retries     INTEGER NOT NULL DEFAULT 10,
    last_error      TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    next_retry      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_pending_retry ON pending_acl (next_retry) WHERE retry_count < max_retries;
```

---

### 2.2 🟡 resource-sync 整个挂了

**新架构下这不再是严重故障。** Gateway 直接路由到后端，resource-sync 通过 ext_proc 调用，配置了 `failureMode: failOpen`。

```mermaid
flowchart TD
    U[用户请求] --> GW[Gateway]
    GW --> PEP[pep-proxy ext_authz 鉴权]
    PEP -->|通过| GW2[Gateway 路由]
    GW2 --> APP[后端应用<br/>直接到达，不经过 resource-sync]
    APP --> RESP[后端返回响应]
    RESP --> EP{"ext_proc 回调<br/>resource-sync"}

    EP -->|正常| SYNC[写 ACL 成功]
    EP -->|"resource-sync 挂了<br/>connection refused / timeout"| FAILOPEN["failOpen 放行<br/>响应正常返回给用户"]

    FAILOPEN --> IMPACT["影响范围"]
    IMPACT --> I1["✅ 业务请求正常工作"]
    IMPACT --> I2["❌ 新建资源无 ACL<br/>用户暂时无法访问刚创建的资源"]
    IMPACT --> I3["❌ 删除资源有孤儿 ACL<br/>不影响安全性，仅占用存储"]
    IMPACT --> I4["❌ 分享/取消分享 API 返回 502<br/>resource-sync:8080 管理端口不可用"]
    IMPACT --> I5["❌ X-Allowed-Ids 头不会注入<br/>ext_proc 不可用，list/search 受影响"]

    style FAILOPEN fill:#ffd43b,color:#000
    style I1 fill:#51cf66,color:#fff
    style I2 fill:#ffd43b,color:#000
    style I3 fill:#ffd43b,color:#000
    style I4 fill:#ff6b6b,color:#fff
    style I5 fill:#ff6b6b,color:#fff
```

**关键区别：**

| 对比项 | 旧架构 | 新架构 |
|-------|--------|--------|
| resource-sync 宕机时 | 🔴 所有业务 502 | 🟡 业务正常，ACL 延迟 |
| 请求链路 | 请求必须经过 resource-sync | Gateway 直连后端 |
| 故障模式 | 串联故障，一挂全挂 | failOpen，优雅降级 |

**恢复后：** resource-sync 恢复后，pending_acl 后台重试自动修复。

**部署建议：**

```yaml
# resource-sync 部署配置
apiVersion: apps/v1
kind: Deployment
metadata:
  name: resource-sync
spec:
  replicas: 2                        # 至少 2 副本
  template:
    spec:
      containers:
        - name: resource-sync
          ports:
            - containerPort: 8080     # 管理 API（分享/取消分享）
              name: management
            - containerPort: 8082     # ext_proc gRPC（响应回调 + 请求阶段注入 X-Allowed-Ids）
              name: extproc
          livenessProbe:
            httpGet:
              path: /health
              port: 8080
            periodSeconds: 10
          readinessProbe:
            httpGet:
              path: /health/ready
              port: 8080
            periodSeconds: 5
```

---

### 2.3 🔴 PostgreSQL 挂了

影响范围最广 -- pep-proxy 查不了 resource_acl，resource-sync 写不了 ACL，pending_acl 也写不了。

```mermaid
flowchart TD
    PG_DOWN["PostgreSQL 挂了"] --> IMPACT1["pep-proxy 查 resource_acl 失败<br/>→ 资源级鉴权全挂"]
    PG_DOWN --> IMPACT2["resource-sync 写 ACL 失败<br/>→ pending_acl 也写不了（PG 全挂）"]
    PG_DOWN --> IMPACT3["bundle-server 读不到数据<br/>→ OPA 用旧 bundle，不受影响"]

    IMPACT1 --> STRATEGY1{"降级策略"}
    STRATEGY1 --> OPT1["方案A：资源级鉴权降级为只查 OPA<br/>路径有权限就放行，资源级暂时不管<br/>安全性降低但业务可用"]
    STRATEGY1 --> OPT2["方案B：所有资源级请求返回 503<br/>集合请求正常，实例请求不可用<br/>安全但影响体验"]

    IMPACT2 --> QUEUE["ext_proc failOpen 放行<br/>ACL 完全丢失<br/>等 PG 恢复后 pending 重试修复"]

    style PG_DOWN fill:#ff6b6b,color:#fff
    style IMPACT1 fill:#ff6b6b,color:#fff
    style IMPACT2 fill:#ff6b6b,color:#fff
    style IMPACT3 fill:#51cf66,color:#fff
```

**pep-proxy 降级逻辑：**

```python
async def check_resource_permission(user_id, groups, resource_id):
    try:
        acl = await db.query(
            "SELECT permission FROM resource_acl WHERE resource_id=%s AND ...",
            resource_id, user_id
        )
        return acl.permission if acl else None
    except DatabaseConnectionError:
        # 数据库不可用 → 降级
        logger.warning(f"resource_acl 查询失败，降级放行: {resource_id}")
        return "degraded"  # 标记为降级模式
        # 降级模式下放行，后端正常处理
```

**缓解措施：** PostgreSQL 主备部署或使用云托管数据库，确保高可用。

---

### 2.4 🟡 Keycloak 挂了

```mermaid
flowchart TD
    KC_DOWN["Keycloak 挂了"] --> IMPACT1["新用户无法登录<br/>拿不到 JWT"]
    KC_DOWN --> IMPACT2["JWT 过期的用户<br/>无法续签 token"]
    KC_DOWN --> NOT_IMPACT["已登录且 JWT 未过期的用户<br/>完全不受影响"]

    IMPACT1 --> BLOCK1["❌ 新用户进不来"]
    IMPACT2 --> BLOCK2["❌ token 过期后被踢出"]
    NOT_IMPACT --> OK["✅ 正常使用<br/>pep-proxy 用缓存的 JWKS 验证 JWT 签名<br/>不需要连 Keycloak"]

    style KC_DOWN fill:#ffd43b,color:#000
    style NOT_IMPACT fill:#51cf66,color:#fff
    style BLOCK1 fill:#ff6b6b,color:#fff
    style BLOCK2 fill:#ff6b6b,color:#fff
```

**缓解措施：**
- Keycloak `replicas: 2`
- JWT 过期时间设长一些（如 30 分钟），给 Keycloak 恢复的时间窗口
- pep-proxy 缓存 JWKS 公钥，Keycloak 挂了也能验证 JWT 签名

---

### 2.5 🟡 后端返回 201 但实际失败（幽灵资源）

后端返回 201 但数据库事务实际回滚了，资源并不存在。ext_proc 拿到 201 后照常写 ACL，产生孤儿 ACL 记录。

```mermaid
sequenceDiagram
    participant U as 用户
    participant GW as Gateway
    participant KB as kb-service
    participant EP as ext_proc<br/>(resource-sync:8082)
    participant DB as resource_acl

    U->>GW: POST /knowledgebase/v1/kb
    GW->>KB: 直接路由
    KB-->>GW: 201 { "id": "kb-001" }
    Note over KB: 但实际上数据库事务回滚了<br/>kb-001 并没有真正创建

    GW->>EP: ext_proc 响应回调（201, kb-001）
    EP->>DB: INSERT resource_acl (kb-001, owner=zhangsan)
    Note over DB: ACL 写入成功<br/>但 kb-001 实际不存在

    GW-->>U: 201

    Note over U: zhangsan 访问 kb-001<br/>pep-proxy 鉴权通过<br/>但 kb-service 返回 404<br/>→ 孤儿 ACL 记录
```

**说明：** 这是一个已知的极端边界情况，实际发生概率极低（后端返回 201 但事务回滚属于后端 bug）。孤儿 ACL 记录不影响安全性，仅占用少量存储空间。用户访问时后端会返回 404，不会造成数据泄露。

---

### 2.6 🟡 并发创建和删除同一资源

```mermaid
sequenceDiagram
    participant A as 用户A
    participant B as 用户B
    participant GW as Gateway
    participant KB as kb-service
    participant EP as ext_proc
    participant DB as resource_acl

    Note over A,B: 几乎同时发生

    A->>GW: POST /v1/kb → 创建 kb-001
    B->>GW: DELETE /v1/kb/kb-001 → 删除 kb-001

    GW->>KB: 转发 POST
    GW->>KB: 转发 DELETE
    KB-->>GW: 201 { "id": "kb-001" }
    KB-->>GW: 200 删除成功

    Note over GW: 两个响应几乎同时触发 ext_proc

    alt 场景1：先 INSERT 后 DELETE（正确）
        GW->>EP: ext_proc 回调 201
        EP->>DB: INSERT ACL (kb-001, owner=A)
        GW->>EP: ext_proc 回调 200
        EP->>DB: DELETE ACL WHERE resource_id='kb-001'
        Note over DB: 结果：ACL 干净
    end

    alt 场景2：先 DELETE 后 INSERT（问题）
        GW->>EP: ext_proc 回调 200
        EP->>DB: DELETE ACL WHERE resource_id='kb-001'（无记录可删）
        GW->>EP: ext_proc 回调 201
        EP->>DB: INSERT ACL (kb-001, owner=A)
        Note over DB: 结果：孤儿 ACL<br/>资源已删除但 ACL 还在
    end
```

**解决：** 同一个 resource_id 的操作加锁。实际发生概率极低，孤儿 ACL 不影响安全性。

---

### 2.7 🟢 bundle-server 挂了

```mermaid
flowchart TD
    BS_DOWN["bundle-server 挂了"] --> OPA["OPA 继续使用<br/>最后一次拉取的 bundle"]
    OPA --> IMPACT["影响：管理员修改的 apps/path_rules<br/>不会生效，直到 bundle-server 恢复"]
    IMPACT --> OK["业务完全不受影响<br/>只是新配置暂时不生效"]

    style BS_DOWN fill:#51cf66,color:#fff
    style OK fill:#51cf66,color:#fff
```

**不需要特殊处理。** bundle-server 恢复后自动推送最新数据。

---

### 2.8 🔴 OPA 挂了

```mermaid
flowchart TD
    OPA_DOWN["OPA 挂了"] --> PEP["pep-proxy 调 OPA 失败"]
    PEP --> DENY["默认拒绝所有请求<br/>default deny 策略<br/>安全但全挂"]

    style OPA_DOWN fill:#ff6b6b,color:#fff
    style DENY fill:#ff6b6b,color:#fff
```

**建议：** OPA `replicas: 2`，加 readinessProbe。OPA 是纯内存服务，启动快，恢复快。

---

### 2.9 🟡 ext_proc 超时

ext_proc 有默认 200ms 超时（可配置）。超时后 failOpen 放行，ACL 未写入。

```mermaid
sequenceDiagram
    participant U as 用户
    participant GW as Gateway
    participant KB as kb-service
    participant EP as ext_proc<br/>(resource-sync:8082)
    participant PA as pending_acl

    U->>GW: POST /knowledgebase/v1/kb
    GW->>KB: 直接路由
    KB-->>GW: 201 { "id": "kb-001" }

    GW->>EP: ext_proc 响应回调
    Note over EP: 处理耗时超过 200ms...
    EP--xGW: ⏰ 超时

    Note over GW: failureMode: failOpen<br/>超时 → 放行响应
    GW-->>U: 201（资源创建成功）

    Note over EP: ACL 未写入

    alt ext_proc 内部处理完成但响应已发出
        EP->>PA: 写入 pending_acl
        Note over PA: 后台重试补写 ACL
    end

    alt ext_proc 完全没处理
        Note over EP: ACL 丢失<br/>需人工介入修复
    end
```

**影响：** 用户创建了资源但暂时无法访问，等待 pending 重试修复。

**缓解措施：**
- 调大 ext_proc 超时时间（如 500ms）以覆盖大多数正常场景
- resource-sync 优化写入性能，确保 P99 < 100ms

---

### 2.10 🟡 ext_proc 请求阶段不可用

ext_proc 在请求阶段负责为 list/search 请求注入 `X-Allowed-Ids` 头。当 ext_proc 不可用时，该头不会被注入，影响集合查询结果。

```mermaid
flowchart TD
    EP_DOWN["ext_proc 请求阶段不可用<br/>resource-sync 挂了或超时"] --> IMPACT1["X-Allowed-Ids 头不会注入到请求中"]
    IMPACT1 --> STRATEGY{"后端如何处理缺少 X-Allowed-Ids 的请求?"}

    STRATEGY --> OPT1["方案A：返回空列表<br/>安全优先，用户暂时看不到任何资源"]
    STRATEGY --> OPT2["方案B：返回 503<br/>告知用户稍后重试"]

    NOTE["注意：单资源访问不受影响<br/>GET/PUT/DELETE /v1/kb/kb-001<br/>走 pep-proxy 鉴权，不依赖 X-Allowed-Ids"]

    style EP_DOWN fill:#ffd43b,color:#000
    style OPT1 fill:#51cf66,color:#fff
    style OPT2 fill:#ffd43b,color:#000
    style NOTE fill:#dee2e6,color:#000
```

**后端处理逻辑：**

```python
# 后端 list/search handler
def list_resources(request):
    allowed_ids_header = request.headers.get("X-Allowed-Ids")
    if allowed_ids_header is None:
        # ext_proc 未注入 → failOpen 场景
        # 安全优先：返回空列表
        logger.warning("X-Allowed-Ids header missing, returning empty list")
        return []
    elif allowed_ids_header == "*":
        # 用户有 admin 权限，返回全部
        return db.query("SELECT * FROM resources WHERE ...")
    else:
        # 正常过滤
        ids = allowed_ids_header.split(",")
        return db.query("SELECT * FROM resources WHERE id IN %s", ids)
```

---

### 2.11 🟡 ext_authz bodyToExtAuth 未配置（v2.1 新增）

Gateway 的 SecurityPolicy 没有配置 `spec.extAuth.bodyToExtAuth.maxRequestBytes`，而某个 `resource_patterns` 条目的 `id_source=body`。结果 pep-proxy 收到的 CheckRequest.body 为空，无法按 `id_field` 提取 resource_id，鉴权失败。

```mermaid
sequenceDiagram
    participant U as 用户
    participant GW as Gateway
    participant PEP as pep-proxy
    participant DB as resource_patterns

    Note over GW: SecurityPolicy 未配置 bodyToExtAuth<br/>或 maxRequestBytes 缺失
    U->>GW: POST /legacy/v1/items/detail<br/>{ "item_id": "item-001" }
    GW->>PEP: ext_authz gRPC<br/>body = <空> ❌

    PEP->>PEP: JWT 验证通过
    PEP->>DB: 匹配 resource_patterns<br/>app=legacy, resource_type=item<br/>id_source=body, id_field=item_id
    DB-->>PEP: 命中规则

    PEP->>PEP: 尝试解析 CheckRequest.body<br/>→ body 为空，无法提取 item_id
    Note over PEP: 日志: DENIED<br/>reason="cannot extract resource_id from body"

    PEP-->>GW: 403
    GW-->>U: 403 Forbidden
```

**检测方式：**
- pep-proxy 日志出现 `DENIED reason=cannot extract resource_id from body`
- 按 `app_name` / `resource_type` 维度聚合，某个遗留应用集中出现此错误 → 立刻怀疑 Gateway 侧配置缺失

**修复步骤：**
1. 检查对应网关的 SecurityPolicy：
   ```yaml
   apiVersion: gateway.envoyproxy.io/v1alpha1
   kind: SecurityPolicy
   spec:
     extAuth:
       bodyToExtAuth:
         maxRequestBytes: 8192
         allowPartialMessage: false
   ```
2. 确认应用到目标路由的 SecurityPolicy 资源
3. pep-proxy 无需重启，Gateway 策略变更即时生效

**影响等级：🟡 中等**
- 只影响配置了 `id_source=body` 的特定资源模式（通常是少量遗留 API）
- **标准 RESTful 应用（id_source=path）不受影响**
- **API Key 认证本身不受影响**（API Key 验证不读 body）
- 相当于该遗留应用的实例级接口全挂，但集合接口和其他应用仍可用

**预防措施：**
- 将 `bodyToExtAuth.maxRequestBytes=8192` 作为默认模板的强制字段
- CI 检查：扫描所有 SecurityPolicy 资源，确保启用了 bodyToExtAuth
- 文档中标注：新增 `id_source=body` 的 resource_pattern 时必须同步确认 Gateway 侧配置

---

### 2.12 🟡 请求体超过 bodyToExtAuth.maxRequestBytes（v2.1 新增）

客户端发送的请求体超过 `bodyToExtAuth.maxRequestBytes` 配置（默认 8192 字节）。Gateway 在 ext_authz 阶段就拒绝请求，**请求根本不会到达 pep-proxy**。

```mermaid
flowchart TD
    REQ[客户端请求<br/>body = 12 KB] --> GW[Gateway 接收]
    GW --> BUFFER{缓冲 body<br/>maxRequestBytes=8192?}
    BUFFER -->|body > maxRequestBytes| REJECT[413 Payload Too Large<br/>ext_authz 不调用 pep-proxy]
    BUFFER -->|body ≤ maxRequestBytes| FORWARD[转发到 pep-proxy]

    REJECT --> CLIENT[客户端收到 413]

    style REJECT fill:#ff6b6b,color:#fff
    style FORWARD fill:#51cf66,color:#fff
```

**检测方式：**
- Gateway 访问日志中出现大量 `413` 响应，聚合在特定路由上
- 对比业务日志：业务侧看不到这些请求（因为没到后端）

**修复方式（任选其一）：**
1. **调大 maxRequestBytes**：在 SecurityPolicy 中增大 `bodyToExtAuth.maxRequestBytes`（权衡内存占用）
2. **拆分大请求**：让客户端改造接口，减少单次 body 体积
3. **移除 body 模式**：如果该资源的 ID 能放到 URL 路径或查询参数，改用 `id_source=path/query`，彻底绕过 body 大小限制

**影响等级：🟡 中等**
- 仅影响需要 body 解析的大请求（通常是批量接口）
- 不影响 JWT 验证、API Key 验证、标准 RESTful 接口
- 客户端会立即收到明确的 413 响应，不会出现"请求卡住"这种模糊现象

> **为什么不能把 maxSize 设得极大？** Gateway 需要在内存中缓冲整个 body 才能转发给 pep-proxy。过大的 maxSize 会让 Gateway 内存吃紧，在高并发下有 OOM 风险。推荐 8-32 KB，覆盖 99% 的正常 API 请求。

---

### 2.13 🟡 api_keys 表查询失败（v2.1 新增）

pep-proxy 验证 API Key 时，必须查 iam 数据库的 `api_keys` 表。如果 iam 数据库连接失败（PG 短暂抖动、网络分区等），API Key 认证走不下去。

```mermaid
flowchart TD
    REQ[外部应用请求<br/>X-API-Key: ak_xxx] --> PEP[pep-proxy]
    PEP --> HASH[SHA256 哈希]
    HASH --> DB[(api_keys 表)]
    DB -->|DB 不可达| FAIL[查询失败]
    FAIL --> RESP503[pep-proxy 返回 503<br/>error=auth_backend_unavailable]
    RESP503 --> GW[Gateway 返回 503 给客户端]

    DB -->|正常| OK[继续鉴权流程]

    NOTE[⚠️ JWT 用户不受影响<br/>pep-proxy 缓存了 JWKS<br/>不需要访问数据库]

    style FAIL fill:#ff6b6b,color:#fff
    style RESP503 fill:#ff6b6b,color:#fff
    style NOTE fill:#51cf66,color:#fff
    style OK fill:#51cf66,color:#fff
```

**检测方式：**
- pep-proxy 日志：`ERROR api_keys query failed: connection refused`
- 指标告警：`pep_proxy_apikey_auth_errors_total` 上升
- 交叉验证：JWT 用户仍能正常登录和使用 → 确认是数据库问题而非 pep-proxy 自身问题

**影响等级：🟡 中等**

| 用户类型 | 影响 | 原因 |
|---------|------|------|
| **JWT 用户（浏览器登录）** | ✅ 不受影响 | JWKS 缓存在内存中，不查数据库 |
| **API Key 用户（外部应用）** | ❌ 全部阻断 | 每次请求都需要查 api_keys 表 |
| **已有 resource_acl 查询** | ❌ 资源级鉴权也受影响 | 同一个数据库 |

**降级策略思考：**
- **方案 A（拒绝）**：api_keys 查询失败直接返回 503 → 安全优先，避免未知 Key 被放行
- **方案 B（缓存）**：pep-proxy 在内存中缓存"最近验证过的 API Key 哈希→身份"映射，TTL 60 秒 → DB 短暂抖动时仍能服务老客户端，但新 Key 无法验证

**当前实现采用方案 A（直接 503）**，因为 API Key 通常用于服务间调用，客户端有重试机制，短暂 503 比错误放行更安全。

**缓解措施：**
- iam 数据库主备部署（与场景 2.3 同策略）
- pep-proxy 做连接池，避免连接风暴
- 监控 api_keys 查询 P99 延迟，超过阈值告警

---

### 2.14 🟢 API Key 哈希不匹配 / 过期 / 禁用（v2.1 正常运行场景）

这**不是故障**，而是正常运行时的鉴权拒绝路径。为便于运维排障，统一记录所有可能的响应码。

| 场景 | 响应码 | 响应头示例 | 说明 |
|------|--------|-----------|------|
| `X-API-Key` 头不存在且无 Authorization | 401 | `WWW-Authenticate: Bearer, APIKey` | 客户端未携带任何凭证 |
| SHA256 哈希在 api_keys 表中无匹配 | 401 | `WWW-Authenticate: APIKey error="invalid_key"` | 密钥错误或被 rotate 后仍使用旧值 |
| 命中记录但 `enabled=false` | 401 | `error="key_disabled"` | 管理员已禁用该 Key |
| 命中记录但 `expired_at < now` | 401 | `error="key_expired"` | 过期自动失效 |
| 请求路径不在 `allowed_paths` 前缀列表 | 403 | `error="path_not_allowed"` | Key 存在且有效，但越权访问 |
| 前面都通过，但 resource_acl 无记录 | 403 | `error="resource_forbidden"` | 已认证但无资源权限 |

**运维参考：**
- 客户端收到 401 → 检查密钥本身（是否被 rotate / 过期 / 禁用）
- 客户端收到 403 `path_not_allowed` → 检查 api_keys 记录的 `allowed_paths` 是否覆盖目标路径
- 客户端收到 403 `resource_forbidden` → 检查 resource_acl 是否已为该 service 身份授权

**告警阈值建议：**
- 单个 API Key 连续 10 次 401 → 告警（可能是密钥泄露或配置错误）
- 某租户的 401/403 比例突增 → 告警（可能是大规模配置变更或攻击）

---

## 3 故障总览

| 故障 | 影响等级 | 影响描述 | 降级/兜底方案 | 预防措施 |
|------|---------|---------|-------------|---------|
| Gateway 挂了 | 🔴 | 全部不可用 | 无 | `replicas: 2` |
| pep-proxy 挂了 | 🔴 | 全部请求 403 | 无 | `replicas: 2` |
| OPA 挂了 | 🔴 | 路径鉴权全挂，default deny | 无 | `replicas: 2` |
| PostgreSQL 挂了 | 🔴 | 资源鉴权全挂 + ACL 同步全挂 | 降级为只查 OPA 放行，或资源级返回 503 | 主备部署 |
| resource-sync 挂了 | 🟡 | **业务正常！** ACL 不同步 | failOpen 放行，pending 重试 | `replicas: 2` |
| ext_proc 超时 | 🟡 | 响应正常返回，ACL 未写入 | pending 重试 | 调大超时，优化写入性能 |
| Keycloak 挂了 | 🟡 | 新用户无法登录 | 已登录用户不受影响（缓存 JWKS） | `replicas: 2` + 缓存 JWKS |
| ext_proc 请求阶段不可用 | 🟡 | X-Allowed-Ids 不注入，list/search 受影响 | 后端返回空列表或 503，单资源不受影响 | `replicas: 2` |
| **bodyToExtAuth 未配置（v2.1）** | 🟡 | body 模式 ID 提取失败，标准 REST 应用不受影响 | 立即修正 SecurityPolicy | 模板强制启用 + CI 检查 |
| **请求体超过 maxRequestBytes（v2.1）** | 🟡 | Gateway 返回 413，请求不到 pep-proxy | 调大 maxRequestBytes 或拆分请求 | 合理设置默认 maxRequestBytes（8-32 KB） |
| **api_keys 表查询失败（v2.1）** | 🟡 | API Key 用户 503，**JWT 用户不受影响** | 直接 503（方案 A，安全优先） | iam 数据库主备 + 连接池 |
| 并发竞争 | 🟡 | 孤儿 ACL 记录 | 操作加锁，极小概率 | 同 resource_id 加锁 |
| **API Key 无效/过期/禁用（v2.1）** | 🟢 | 401/403（正常鉴权拒绝） | — | 客户端监控 + 告警 |
| keycloak-proxy 挂了 | 🟢 | 管理 API 不可用 | 业务不受影响 | 非热路径，1 副本即可 |
| bundle-server 挂了 | 🟢 | 新配置不生效 | OPA 用旧 bundle，业务不受影响 | 恢复后自动推送 |

---

## 4 ACL 一致性保障两道防线

```mermaid
flowchart TD
    LINE1["第一道防线<br/>ext_proc 实时写入"] --> SUCCESS{"写入成功?"}
    SUCCESS -->|是| DONE["ACL 正常"]
    SUCCESS -->|否| LINE2["第二道防线<br/>pending_acl 后台重试"]
    LINE2 --> RETRY{"重试成功?<br/>每 5 秒一次，最多 10 次"}
    RETRY -->|是| DONE
    RETRY -->|否| ALERT["告警 + 人工介入"]

    style LINE1 fill:#51cf66,color:#fff
    style LINE2 fill:#ffd43b,color:#000
    style DONE fill:#51cf66,color:#fff
    style ALERT fill:#ff6b6b,color:#fff
```

| 防线 | 触发条件 | 延迟 | 覆盖场景 |
|------|---------|------|---------|
| ext_proc 实时写入 | 每次创建/删除响应 | 毫秒级 | 99%+ 正常场景 |
| pending_acl 后台重试 | ACL 写入失败时 | 5~50 秒 | DB 短暂不可用、ext_proc 部分失败 |

---

## 5 推荐的副本数配置

```mermaid
flowchart LR
    subgraph "必须高可用（热路径）×2"
        GW["Gateway ×2"]
        PEP["pep-proxy ×2"]
        OPA["OPA ×2"]
    end

    subgraph "重要但 failOpen（可降级）×2"
        RS["resource-sync ×2<br/>挂了业务不中断"]
    end

    subgraph "已有高可用 ×2"
        KC["Keycloak ×2"]
    end

    subgraph "1 副本即可"
        KP["keycloak-proxy ×1<br/>管理 API，非热路径"]
        BS["bundle-server ×1<br/>挂了 OPA 用旧数据"]
    end

    subgraph "建议主备"
        PG[("PostgreSQL<br/>主备或云托管")]
    end

    style GW fill:#ff6b6b,color:#fff
    style PEP fill:#ff6b6b,color:#fff
    style OPA fill:#ff6b6b,color:#fff
    style RS fill:#ffd43b,color:#000
    style KC fill:#ff6b6b,color:#fff
    style KP fill:#51cf66,color:#fff
    style BS fill:#51cf66,color:#fff
    style PG fill:#845ef7,color:#fff
```

| 组件 | 副本数 | 原因 |
|------|-------|------|
| Gateway | ×2 | 热路径，挂了全部不可用 |
| pep-proxy | ×2 | 热路径，挂了全部 403 |
| OPA | ×2 | 热路径，挂了路径鉴权全挂 |
| resource-sync | ×2 | 重要但 failOpen，挂了业务不中断 |
| Keycloak | ×2 | 已配置，挂了新用户无法登录 |
| keycloak-proxy | ×1 | 管理 API，非热路径 |
| bundle-server | ×1 | 挂了 OPA 用旧数据，不影响业务 |
| PostgreSQL | 主备 | 影响范围最广，建议高可用部署 |
