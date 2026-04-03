# 故障场景视角 — 什么会出错、怎么兜底

> 版本：v1.0 | 日期：2026-04-02

---

## 1 故障影响全景

```mermaid
flowchart TB
    subgraph 组件故障影响
        GW_FAIL[Gateway 挂了<br/>🔴 全部不可用]
        PEP_FAIL[pep-proxy 挂了<br/>🔴 全部请求 403]
        OPA_FAIL[OPA 挂了<br/>🔴 路径鉴权失败]
        RS_FAIL[resource-sync 挂了<br/>🟡 业务请求不可用<br/>ACL 不同步]
        KP_FAIL[keycloak-proxy 挂了<br/>🟢 管理API不可用<br/>业务不受影响]
        KC_FAIL[Keycloak 挂了<br/>🟡 无法登录/续token<br/>已登录用户不受影响]
        PG_FAIL[PostgreSQL 挂了<br/>🔴 资源鉴权+ACL同步全挂]
        BS_FAIL[bundle-server 挂了<br/>🟢 OPA用旧数据<br/>短时间不影响]
    end

    style GW_FAIL fill:#ff6b6b,color:#fff
    style PEP_FAIL fill:#ff6b6b,color:#fff
    style OPA_FAIL fill:#ff6b6b,color:#fff
    style RS_FAIL fill:#ffd43b,color:#000
    style KP_FAIL fill:#51cf66,color:#fff
    style KC_FAIL fill:#ffd43b,color:#000
    style PG_FAIL fill:#ff6b6b,color:#fff
    style BS_FAIL fill:#51cf66,color:#fff
```

| 影响等级 | 含义 |
|---------|------|
| 🔴 严重 | 业务完全不可用 |
| 🟡 中等 | 部分功能受影响，有降级方案 |
| 🟢 轻微 | 业务不受影响，只影响管理操作 |

---

## 2 逐个场景分析

### 2.1 🔴 resource-sync 写 ACL 失败（最可能发生）

```mermaid
sequenceDiagram
    participant U as 用户
    participant RS as resource-sync
    participant KB as kb-service
    participant DB as resource_acl 表
    participant RETRY as 重试队列

    U->>RS: POST /v1/kb
    RS->>KB: 转发
    KB-->>RS: 201 { "id": "kb-001" }

    RS->>DB: INSERT resource_acl (kb-001, owner=zhangsan)
    DB-->>RS: ❌ 超时/连接失败

    Note over RS: 资源已创建，但 ACL 没写入<br/>如果直接返回 201：<br/>zhangsan 自己都访问不了 kb-001

    alt 方案：写入重试队列
        RS->>RETRY: 写入 pending_acl<br/>(kb-001, owner, zhangsan, retry=0)
        RS-->>U: 201（资源创建成功）
        Note over RETRY: 后台定时重试
        loop 每 5 秒重试一次，最多 10 次
            RETRY->>DB: INSERT resource_acl
            DB-->>RETRY: ✅ 成功 → 删除 pending 记录
        end
    end
```

**重试队列表：**

```sql
CREATE TABLE pending_acl (
    id           SERIAL PRIMARY KEY,
    tenant_id    VARCHAR(128) NOT NULL,
    app_name     VARCHAR(128) NOT NULL,
    resource_type VARCHAR(128) NOT NULL,
    resource_id  VARCHAR(256) NOT NULL,
    subject_type VARCHAR(32) NOT NULL,
    subject_id   VARCHAR(128) NOT NULL,
    permission   VARCHAR(32) NOT NULL,
    action       VARCHAR(16) NOT NULL,     -- 'create' | 'delete'
    retry_count  INTEGER NOT NULL DEFAULT 0,
    created_at   TIMESTAMP NOT NULL DEFAULT NOW(),
    next_retry   TIMESTAMP NOT NULL DEFAULT NOW()
);
```

**兜底策略：** 如果重试队列也写不进去（极端情况），pep-proxy 鉴权时发现 resource_acl 里没记录，回退检查：

```mermaid
flowchart TD
    REQ[用户访问 /v1/kb/kb-001] --> CHECK_ACL{resource_acl 有记录?}
    CHECK_ACL -->|有| NORMAL[正常鉴权]
    CHECK_ACL -->|没有| FALLBACK{回退：检查 pending_acl}
    FALLBACK -->|有 pending 记录<br/>且 subject 匹配| ALLOW[临时放行]
    FALLBACK -->|没有| DENY[403]

    style ALLOW fill:#ffd43b,color:#000
    style DENY fill:#ff6b6b,color:#fff
```

---

### 2.2 🔴 resource-sync 整个挂了

```mermaid
flowchart TD
    U[用户请求] --> GW[Gateway]
    GW --> PEP[pep-proxy 鉴权通过]
    PEP --> GW2[Gateway 转发]
    GW2 --> RS{resource-sync}
    RS -->|正常| APP[后端应用]
    RS -->|挂了| ERR[502 Bad Gateway]

    ERR --> STRATEGY{降级策略}
    STRATEGY --> OPT1[方案A：返回 502<br/>用户看到错误页面<br/>最安全但体验差]
    STRATEGY --> OPT2[方案B：Gateway 直连后端<br/>业务可用但 ACL 不同步<br/>新建资源无 owner]

    style ERR fill:#ff6b6b,color:#fff
    style OPT1 fill:#ffd43b,color:#000
    style OPT2 fill:#ffd43b,color:#000
```

**建议：**

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
          livenessProbe:              # 挂了自动重启
            httpGet:
              path: /health
              port: 8080
            periodSeconds: 10
          readinessProbe:             # 没准备好不给流量
            httpGet:
              path: /health/ready
              port: 8080
            periodSeconds: 5
```

---

### 2.3 🔴 PostgreSQL 挂了

影响范围最广——pep-proxy 查不了 resource_acl，resource-sync 写不了 ACL。

```mermaid
flowchart TD
    PG_DOWN[PostgreSQL 挂了] --> IMPACT1[pep-proxy 查 resource_acl 失败<br/>→ 资源级鉴权全挂]
    PG_DOWN --> IMPACT2[resource-sync 写 ACL 失败<br/>→ 新资源没有 owner]
    PG_DOWN --> IMPACT3[bundle-server 读不到数据<br/>→ OPA 用旧 bundle，不受影响]

    IMPACT1 --> STRATEGY1{降级策略}
    STRATEGY1 --> OPT1[方案A：资源级鉴权降级为只查 OPA<br/>路径有权限就放行，资源级暂时不管<br/>安全性降低但业务可用]
    STRATEGY1 --> OPT2[方案B：所有资源级请求返回 503<br/>集合请求正常，实例请求不可用<br/>安全但影响体验]

    IMPACT2 --> QUEUE[resource-sync 写入本地文件队列<br/>PostgreSQL 恢复后重放]

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

---

### 2.4 🟡 Keycloak 挂了

```mermaid
flowchart TD
    KC_DOWN[Keycloak 挂了] --> IMPACT1[新用户无法登录<br/>拿不到 JWT]
    KC_DOWN --> IMPACT2[JWT 过期的用户<br/>无法续签 token]
    KC_DOWN --> NOT_IMPACT[已登录且 JWT 未过期的用户<br/>完全不受影响]

    IMPACT1 --> BLOCK1[❌ 新用户进不来]
    IMPACT2 --> BLOCK2[❌ token 过期后被踢出]
    NOT_IMPACT --> OK[✅ 正常使用<br/>pep-proxy 验证 JWT 签名<br/>不需要连 Keycloak]

    style KC_DOWN fill:#ffd43b,color:#000
    style NOT_IMPACT fill:#51cf66,color:#fff
    style BLOCK1 fill:#ff6b6b,color:#fff
    style BLOCK2 fill:#ff6b6b,color:#fff
```

**缓解措施：**
- Keycloak `replicas: 2`（你们已经配了）
- JWT 过期时间设长一些（如 30 分钟），给 Keycloak 恢复的时间窗口
- pep-proxy 缓存 JWKS 公钥，Keycloak 挂了也能验证 JWT 签名

---

### 2.5 🟡 后端应用返回成功但实际失败

```mermaid
sequenceDiagram
    participant U as 用户
    participant RS as resource-sync
    participant KB as kb-service
    participant DB as resource_acl

    U->>RS: POST /v1/kb
    RS->>KB: 转发
    KB-->>RS: 201 { "id": "kb-001" }
    Note over KB: 但实际上数据库事务回滚了<br/>kb-001 并没有真正创建

    RS->>DB: INSERT resource_acl (kb-001, owner=zhangsan)
    Note over DB: ACL 写入成功<br/>但 kb-001 实际不存在

    RS-->>U: 201

    Note over U: zhangsan 访问 kb-001<br/>pep-proxy 鉴权通过<br/>但 kb-service 返回 404<br/>→ 孤儿 ACL 记录
```

**解决：定期对账**

```mermaid
flowchart LR
    CRON[定时任务<br/>每天凌晨] --> SCAN[扫描 resource_acl]
    SCAN --> CHECK{对每个 resource_id<br/>调后端 HEAD 请求<br/>资源还存在吗?}
    CHECK -->|存在| KEEP[保留]
    CHECK -->|404 不存在| CLEAN[清理孤儿 ACL]

    style CRON fill:#845ef7,color:#fff
    style CLEAN fill:#ff6b6b,color:#fff
```

```python
# 对账脚本
async def reconcile():
    acl_resources = db.query(
        "SELECT DISTINCT app_name, resource_type, resource_id FROM resource_acl"
    )
    for r in acl_resources:
        resp = await http.head(f"http://{r.app_name}-service/v1/{r.resource_type}/{r.resource_id}")
        if resp.status_code == 404:
            db.execute(
                "DELETE FROM resource_acl WHERE app_name=%s AND resource_id=%s",
                r.app_name, r.resource_id
            )
            logger.info(f"清理孤儿 ACL: {r.app_name}/{r.resource_id}")
```

---

### 2.6 🟡 并发创建和删除同一资源

```mermaid
sequenceDiagram
    participant A as 用户A
    participant B as 用户B
    participant RS as resource-sync
    participant KB as kb-service
    participant DB as resource_acl

    Note over A,B: 几乎同时发生

    A->>RS: POST /v1/kb → 创建 kb-001
    B->>RS: DELETE /v1/kb/kb-001 → 删除 kb-001

    RS->>KB: 转发 POST
    RS->>KB: 转发 DELETE
    KB-->>RS: 201 { "id": "kb-001" }
    KB-->>RS: 200 删除成功

    Note over RS: 两个响应几乎同时回来

    alt 场景1：先 INSERT 后 DELETE（正确）
        RS->>DB: INSERT ACL (kb-001, owner=A)
        RS->>DB: DELETE ACL WHERE resource_id='kb-001'
        Note over DB: 结果：ACL 干净 ✅
    end

    alt 场景2：先 DELETE 后 INSERT（问题）
        RS->>DB: DELETE ACL WHERE resource_id='kb-001'（无记录可删）
        RS->>DB: INSERT ACL (kb-001, owner=A)
        Note over DB: 结果：孤儿 ACL ❌<br/>资源已删除但 ACL 还在
    end
```

**解决：** 同一个 resource_id 的操作加锁，或者靠定期对账清理。实际发生概率极低。

---

### 2.7 🟢 bundle-server 挂了

```mermaid
flowchart TD
    BS_DOWN[bundle-server 挂了] --> OPA[OPA 继续使用<br/>最后一次推送的 bundle]
    OPA --> IMPACT[影响：管理员修改的 apps/path_rules<br/>不会生效，直到 bundle-server 恢复]
    IMPACT --> OK[业务完全不受影响<br/>只是新配置暂时不生效]

    style BS_DOWN fill:#51cf66,color:#fff
    style OK fill:#51cf66,color:#fff
```

**不需要特殊处理。** bundle-server 恢复后自动推送最新数据。

---

### 2.8 🟢 OPA 挂了

```mermaid
flowchart TD
    OPA_DOWN[OPA 挂了] --> PEP[pep-proxy 调 OPA 失败]
    PEP --> STRATEGY{策略}
    STRATEGY --> DENY[默认拒绝所有请求<br/>安全但全挂]
    STRATEGY --> CACHE[使用本地缓存的<br/>上一次 OPA 结果<br/>可能不是最新]

    style OPA_DOWN fill:#ff6b6b,color:#fff
    style DENY fill:#ff6b6b,color:#fff
    style CACHE fill:#ffd43b,color:#000
```

**建议：** OPA `replicas: 2`，加 readinessProbe。OPA 是纯内存服务，启动快，恢复快。

---

### 2.9 🟡 resource-sync 内部接口不可用

后端调 `http://resource-sync:8081/internal/v1/resources` 失败时，list/search 接口受影响。

```mermaid
flowchart TD
    RS_INTERNAL_DOWN[resource-sync 内部接口不可用<br/>8081 端口无响应] --> IMPACT1[后端 list/search 拿不到可访问 ID 列表]
    IMPACT1 --> STRATEGY{降级策略}

    STRATEGY --> OPT1[方案A：list/search 返回空列表<br/>安全但用户看不到任何资源]
    STRATEGY --> OPT2[方案B：list/search 返回 503<br/>告知用户稍后重试]
    STRATEGY --> OPT3[方案C：SDK 内置缓存<br/>用上一次成功的 ID 列表<br/>可能不是最新]

    NOTE[注意：单资源访问不受影响<br/>GET/PUT/DELETE /v1/kb/kb-001<br/>走 pep-proxy 鉴权，不依赖内部接口]

    style RS_INTERNAL_DOWN fill:#ffd43b,color:#000
    style OPT1 fill:#51cf66,color:#fff
    style OPT2 fill:#ffd43b,color:#000
    style OPT3 fill:#ffd43b,color:#000
    style NOTE fill:#dee2e6,color:#000
```

**SDK 降级逻辑：**

```python
# aidp_acl/client.py
import httpx
from functools import lru_cache

@lru_cache(maxsize=1000)
def _cached_resources(tenant_id, app_name, resource_type, user_id, groups):
    """缓存上一次成功的结果，TTL 由外部控制"""
    return None

def get_allowed_resources(request, app_name, resource_type):
    try:
        resp = httpx.get(f"{RESOURCE_SYNC_URL}/internal/v1/resources", params={...}, timeout=3)
        result = resp.json()["resource_ids"]
        # 缓存成功结果
        _cached_resources.cache_clear()
        return result
    except (httpx.ConnectError, httpx.TimeoutException):
        # 降级：返回空列表（安全优先）
        logger.warning("resource-sync 内部接口不可用，降级返回空列表")
        return []
```

---

## 3 故障总览

| 故障 | 影响 | 降级方案 | 预防措施 |
|------|------|---------|---------|
| resource-sync 写 ACL 失败 | 新资源无人能访问 | pending_acl 重试队列 + pep-proxy 回退查 pending | 数据库连接池 + 超时配置 |
| resource-sync 挂了 | 业务请求 502 | 多副本 + 健康检查自动重启 | `replicas: 2` |
| resource-sync 内部接口不可用 | list/search 拿不到 ID 列表 | SDK 降级返回空列表，单资源访问不受影响 | `replicas: 2` + SDK 超时 3s |
| PostgreSQL 挂了 | 资源鉴权全挂 | pep-proxy 降级为只查 OPA，resource-sync 写本地队列 | 数据库主备 |
| Keycloak 挂了 | 新用户无法登录 | 已登录用户不受影响（JWT 未过期） | `replicas: 2` + 缓存 JWKS |
| 后端返回成功但实际失败 | 孤儿 ACL 记录 | 定期对账清理 | 对账脚本（每天凌晨） |
| 并发竞争 | 孤儿 ACL 记录 | 定期对账清理 | 极小概率，对账兜底即可 |
| bundle-server 挂了 | 新配置不生效 | OPA 用旧数据，业务不受影响 | 恢复后自动推送 |
| OPA 挂了 | 路径鉴权全挂 | 默认拒绝 或 pep-proxy 缓存 | `replicas: 2` |

---

## 4 推荐的副本数配置

```mermaid
flowchart LR
    subgraph 至少2副本
        GW[Gateway ×2]
        PEP[pep-proxy ×2]
        OPA[OPA ×2]
        RS[resource-sync ×2]
        KC[Keycloak ×2]
    end

    subgraph 1副本即可
        KP[keycloak-proxy ×1<br/>管理API,非热路径]
        BS[bundle-server ×1<br/>挂了OPA用旧数据]
    end

    subgraph 建议主备
        PG[(PostgreSQL<br/>主备或云托管)]
    end

    style GW fill:#ff6b6b,color:#fff
    style PEP fill:#ff6b6b,color:#fff
    style OPA fill:#ff6b6b,color:#fff
    style RS fill:#ff6b6b,color:#fff
    style KC fill:#ff6b6b,color:#fff
    style KP fill:#51cf66,color:#fff
    style BS fill:#51cf66,color:#fff
    style PG fill:#845ef7,color:#fff
```
