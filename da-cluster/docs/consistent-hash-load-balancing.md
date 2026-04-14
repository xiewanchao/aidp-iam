# Envoy Gateway 一致性哈希负载均衡设计

> 版本：v1.0（对齐 Envoy Gateway v1.7.x）
> 日期：2026-04-14
> 官方文档：https://gateway.envoyproxy.io/docs/tasks/traffic/load-balancing/

---

## 1 背景与目标

### 1.1 什么是一致性哈希

一致性哈希（Consistent Hash）是一种"**把请求的某个特征**（Header/Cookie/源 IP/查询参数）**映射到固定后端 Pod**"的负载均衡算法。相同特征的请求始终命中同一个后端，直到该后端不可用。

Envoy Gateway 内部使用 **Maglev 算法** 实现，特点是：

- **稳定性**：Pod 扩缩容时，只有 `1/N` 左右的 key 被重新分布（N = 表大小），大多数请求仍路由到原来的后端
- **均匀性**：默认 `tableSize=65537`，足够保证后端负载偏差小
- **无状态**：Gateway 不维护会话表，靠算法本身的确定性

### 1.2 为什么需要

AIDP 是多租户 IAM 平台，业务后端中**有几个场景天然需要会话粘性或缓存亲和**，否则性能会明显下降：

| 场景 | 痛点 | 一致性哈希的价值 |
|---|---|---|
| **LLM 会话** | 多轮对话上下文缓存在某个 Pod 内存 | 按 `session_id` hash，同一会话始终命中同 Pod，避免上下文跨 Pod 重建 |
| **知识库向量检索** | 同一知识库的索引加载到内存后，切换 Pod 需重新加载 | 按 `kb_id` hash，让同一 KB 的请求落到同一 Pod |
| **WebSocket / SSE 长连接** | 连接状态在 Pod 内存 | 按 `X-Auth-User-Id` hash，同一用户的所有连接到同一 Pod |
| **高频 JWT 用户** | 每次命中不同 Pod 都要走一次缓存预热 | 按 `X-Auth-User-Id` hash，用户缓存命中率高 |

### 1.3 非目标

- **不替换默认 LB**：管理 API（`/api/v1/**`）、鉴权链路（pep-proxy）、资源 ACL 管理（resource-sync）**继续使用默认的 LeastRequest**——这些服务无状态，追求均衡优于粘性
- **不解决状态持久化**：一致性哈希只是把**请求路由到"同一"Pod**；真正的状态持久化仍需 Redis/DB，不能靠哈希替代
- **不处理跨集群路由**：本文只覆盖集群内 Gateway → 业务 Pod

---

## 2 Envoy Gateway 一致性哈希能力

### 2.1 4 种负载均衡算法对比

| 算法 | 适用场景 | 缺点 |
|---|---|---|
| **LeastRequest**（默认） | 无状态服务，动态平衡 | 无粘性，缓存命中率低 |
| **RoundRobin** | 后端同构且无状态 | 不考虑后端实际负载 |
| **Random** | 极简场景、小流量 | 无任何优化 |
| **ConsistentHash** | 有会话/缓存亲和需求 | 后端权重变化可能造成热点 |

> **重点**：未指定 `loadBalancer` 时默认是 **LeastRequest**，不是 RoundRobin。

### 2.2 ConsistentHash 支持的 hash key 类型

官方（v1.7 alpha API）支持 5 种：

| `type` 枚举 | Hash 来源 | 典型场景 |
|---|---|---|
| `SourceIP` | 客户端 IP | 简单会话粘性（不推荐走 Gateway 的场景，因为通常拿到的是 Gateway 前面那层的 IP） |
| `Header` | 单个 HTTP 请求头（已标记 Deprecated，保留兼容） | 老版本 API |
| `Headers` | 多个 HTTP 请求头组合 | **推荐**：按 `X-Auth-User-Id` / `X-Auth-Tenant` 等鉴权结果 hash |
| `Cookie` | Cookie 值（可由 Envoy 自动生成） | 浏览器前端的会话粘性 |
| `QueryParams` | URL 查询参数 | 按 `?kb_id=...` 等显式业务标识 hash |

### 2.3 完整 API 结构

```yaml
loadBalancer:
  type: ConsistentHash
  consistentHash:
    type: Headers | Cookie | SourceIP | QueryParams
    tableSize: 65537          # 可选，默认 65537，最大 5000011（必须质数）

    # type=Headers 时用
    headers:
      - name: X-Auth-User-Id
      - name: X-Auth-Tenant

    # type=Cookie 时用
    cookie:
      name: SESSION_ID
      ttl: 3600s              # 客户端未携带时，Envoy 自动生成的 cookie 过期时间
      attributes:
        SameSite: Strict
        HttpOnly: "true"

    # type=QueryParams 时用
    queryParams:
      - name: kb_id
      - name: session_id
```

### 2.4 扩缩容时的行为

- **Pod 新增**：新 Pod 按 hash 分到一部分 key，其他 key 不受影响
- **Pod 移除**：该 Pod 上的 key 被均匀分到剩余 Pod；其他 key 不受影响
- **tableSize 越大**：分布越均匀，但内存占用越高。默认 65537 满足 99% 场景，无需调整

**最坏情况**：某 Pod 挂掉瞬间，路由到它的 key 会集中跳到接替它的下一个 Pod，造成短暂热点。需要配合 **outlierDetection**（熔断）+ **retries**（重试）缓解。

---

## 3 AIDP 项目落地设计

### 3.1 需要启用一致性哈希的路由

按 AIDP 业务场景梳理：

| 路由 | 启用？ | Hash 来源 | 理由 |
|---|---|---|---|
| `/knowledgebase/v1/kb/*` | ✅ | Headers（`X-Auth-Tenant` + `kb_id` 从 path） | KB 索引驻留内存，需亲和 |
| `/memory/v1/*` | ✅ | Headers（`X-Auth-User-Id`） | 用户记忆缓存在 Pod |
| `/llm/v1/chat/*` | ✅ | Headers（`X-Auth-Session-Id`） | 多轮对话上下文 |
| `/api/v1/**`（管理 API） | ❌ | - | 无状态，用 LeastRequest |
| `/realms/*`（Keycloak） | ❌ | - | Keycloak 自带集群化，无需亲和 |
| `/acl/v1/**`（resource-sync） | ❌ | - | 无状态，直读 DB |

**关键原则**：只有"**后端 Pod 有内存状态/缓存**"的路由才启用一致性哈希；无状态服务用默认 LeastRequest。

### 3.2 Hash key 选择策略

AIDP 的鉴权链路会在 `ext_authz` 阶段给后端请求注入 `X-Auth-*` Header（由 pep-proxy 通过 `CheckResponse` 写入）：

```
X-Auth-User-Id: zhangsan
X-Auth-Tenant:  aidp
X-Auth-Groups:  data-team,all-users,knowledgebase-admins
```

**这些 Header 是 pep-proxy 鉴权通过后注入的，不是客户端直接传入**，因此不会被伪造——非常适合作为 hash key。

#### 推荐组合

| 业务 | 主 key | 副 key | 为什么 |
|---|---|---|---|
| 知识库 KB 粒度操作 | `X-Auth-Tenant` | `kb_id`（QueryParam 或 Header） | 同一租户同一 KB 落同一 Pod；不同 KB 分散负载 |
| 用户记忆 | `X-Auth-User-Id` | `X-Auth-Tenant` | 同一用户的请求亲和 |
| LLM 会话 | `X-Auth-Session-Id` | — | 按会话亲和，会话结束自然失效 |
| 匿名登录前请求 | `SourceIP` | — | 无 JWT 时退化到 IP 粘性 |

**避坑**：
- ❌ 不要直接用 `X-Auth-Groups` 作 hash：groups 可能变更，且多值
- ❌ 不要用整个 path 作 hash：每个资源 ID 不同，等于退化成 Random
- ✅ 用 `Headers` 组合优于单 `Header`：多维度更均匀，单 Header 全部重复时会造成热点

### 3.3 具体 YAML 配置

#### 3.3.1 知识库（按 tenant + kb_id）

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: BackendTrafficPolicy
metadata:
  name: kb-consistent-hash
  namespace: envoy-gateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: kb-route
  loadBalancer:
    type: ConsistentHash
    consistentHash:
      type: Headers
      headers:
        - name: X-Auth-Tenant          # 同租户的请求亲和
        - name: X-Kb-Id                # 后端先把 path 中的 kb_id 提取到 header
      tableSize: 65537
```

> **注意**：`X-Kb-Id` 无法直接从 URL path 提取作为 hash key（`QueryParams` 只能用查询参数）。两种实现方式：
> 1. **后端自行处理**：Gateway 按 `X-Auth-Tenant` 粘性，后端拿到请求后按 kb_id 进程内路由
> 2. **HTTPRoute Filter**：通过 `RequestHeaderModifier` 从 path 提取（需业务侧约定）
> 3. **查询参数**：改业务 API 约定，比如所有 KB 操作都带 `?kb_id=xxx`

#### 3.3.2 用户记忆（按用户 + 租户）

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: BackendTrafficPolicy
metadata:
  name: memory-consistent-hash
  namespace: envoy-gateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: memory-route
  loadBalancer:
    type: ConsistentHash
    consistentHash:
      type: Headers
      headers:
        - name: X-Auth-User-Id
        - name: X-Auth-Tenant
```

#### 3.3.3 LLM 会话（按 session cookie）

前端 WebUI 登录后 Envoy 自动种下 session cookie：

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: BackendTrafficPolicy
metadata:
  name: llm-session-hash
  namespace: envoy-gateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: llm-route
  loadBalancer:
    type: ConsistentHash
    consistentHash:
      type: Cookie
      cookie:
        name: AIDP_SESSION
        ttl: 3600s                     # 1 小时无请求自动过期
        attributes:
          SameSite: Strict
          HttpOnly: "true"
```

**行为**：
- 首次请求无 cookie → Envoy 生成随机 cookie 值并写入 `Set-Cookie` 响应头
- 后续请求携带 cookie → 同一后端
- cookie 过期 → 重新生成并可能分到新 Pod

#### 3.3.4 匿名流量（按 SourceIP）

登录前的流量（比如 `/realms/*` OIDC 回调前）没有 JWT，用 SourceIP 兜底：

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: BackendTrafficPolicy
metadata:
  name: anon-source-ip-hash
  namespace: envoy-gateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: realms-route
  loadBalancer:
    type: ConsistentHash
    consistentHash:
      type: SourceIP
```

> **重要**：如果 Envoy Gateway 前面还有 LoadBalancer/Ingress，SourceIP 会是那层的 IP，需要启用 **`useClientAsRemoteAddress: true`** 配合 `X-Forwarded-For` 处理。

### 3.4 配合熔断和重试（推荐）

一致性哈希 + 熔断 + 重试 是生产环境的"黄金组合"，在同一个 `BackendTrafficPolicy` 里配置：

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: BackendTrafficPolicy
metadata:
  name: kb-full-policy
  namespace: envoy-gateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: kb-route
  loadBalancer:
    type: ConsistentHash
    consistentHash:
      type: Headers
      headers:
        - name: X-Auth-Tenant

  # 熔断：防止雪崩
  circuitBreaker:
    maxConnections: 1024
    maxPendingRequests: 1024
    maxParallelRequests: 1024
    maxParallelRetries: 3

  # 异常检测：自动剔除慢/挂的 Pod
  healthCheck:
    passive:
      baseEjectionTime: 30s
      consecutive5XxErrors: 3
      consecutiveGatewayErrors: 3
      maxEjectionPercent: 30           # 最多剔除 30% Pod

  # 重试：Pod 临时故障时切到下一个
  retry:
    numRetries: 2
    perRetry:
      timeout: 5s
    retryOn:
      httpStatusCodes: [502, 503, 504]
      triggers: [connect-failure, refused-stream, reset]
```

---

## 4 验证与监控

### 4.1 验证粘性生效

部署 kb-service 多个副本，观察同一 tenant 的请求是否落到同一 Pod：

```bash
# 在 kb-service 中加 echo pod-name 的 /debug/whoami 端点
for i in {1..10}; do
  curl -s -H "Authorization: Bearer $TOKEN_TENANT_A" \
    https://gateway.aidp.com/knowledgebase/v1/debug/whoami
done | sort | uniq -c

# 预期：10 次请求都命中同一个 Pod（同一 tenant）
# 10 kb-service-abcdef-x1y2z

for i in {1..10}; do
  curl -s -H "Authorization: Bearer $TOKEN_TENANT_B" \
    https://gateway.aidp.com/knowledgebase/v1/debug/whoami
done | sort | uniq -c
# 预期：10 次命中另一个 Pod（不同 tenant）
```

### 4.2 验证扩缩容稳定性

```bash
# 当前 3 个 Pod，记录 tenant-a 落到哪个
ORIG=$(curl -s -H "Auth-Bearer: $TOKEN_A" .../whoami)

# 扩容到 5 个
kubectl scale deploy kb-service --replicas=5

# 等配置推送（~3s）后再次请求
sleep 5
NEW=$(curl -s -H "Auth-Bearer: $TOKEN_A" .../whoami)

# 统计视角：约 40% key 会重新分布到新 Pod（2/5）
# 个体 tenant：约 60% 概率仍落到原 Pod
```

### 4.3 监控指标

通过 Envoy 数据面 metrics（`/stats/prometheus`）观察：

| 指标 | 含义 | 预期 |
|---|---|---|
| `envoy_cluster_upstream_rq_total{envoy_cluster_name="kb-service"}` | 各 Pod 请求量 | 粘性流量下，不同 Pod 之间差异应在 ±30% 内 |
| `envoy_cluster_lb_subsets_fallback` | fallback 到默认 LB 的次数 | 应接近 0；否则说明 hash key 缺失 |
| `envoy_cluster_outlier_detection_ejections_enforced_total` | 被动熔断剔除次数 | 异常 Pod 会被剔除，key 临时迁移 |

Grafana Dashboard 里直接看 **Envoy Clusters** 面板。

---

## 5 常见陷阱

| 陷阱 | 现象 | 解决 |
|---|---|---|
| Hash key 不存在 | Envoy 退化为 Random LB（无粘性） | 确认 `X-Auth-*` Header 在鉴权后注入；日志确认 ext_authz 阶段已产生 Header |
| 所有请求命中同一 Pod | 热点过载 | 用多个 header 组合（`Headers` 而非 deprecated `Header`）；检查 hash key 是否过于集中 |
| 扩容后流量迁移过多 | 缓存大规模失效 | 检查 `tableSize`；确认不是 Pod 全部重建 |
| 前面挂 LB 导致 SourceIP 都一样 | 所有流量打到一个 Pod | 用 Header 替代 SourceIP；或启用 `useClientAsRemoteAddress` + XFF 解析 |
| Cookie 被禁用 | 每次生成新 cookie，无粘性 | 改用 Headers 类型；或在前端确保 cookie 接受 |
| Pod 频繁重启 | 大量重新哈希 + 热点 | 增加 PDB、优化启动探针、配合 `healthCheck.passive` |

---

## 6 落地路线

| 阶段 | 内容 | 工作量 |
|---|---|---|
| P0 | 选定首个试点路由（推荐 `/knowledgebase/v1/kb/*`） | 0.5 天 |
| P1 | 编写 `BackendTrafficPolicy` 并部署 | 0.5 天 |
| P2 | 在 kb-service 加 `/debug/whoami` 端点辅助验证 | 0.5 天 |
| P3 | 压测对比开启前/后的缓存命中率、P99 延迟 | 1 天 |
| P4 | 推广到 memory/llm 路由 | 1 天 |
| P5 | 加熔断/重试，进入生产 | 1 天 |

---

## 7 参考

- Envoy Gateway Load Balancing：https://gateway.envoyproxy.io/docs/tasks/traffic/load-balancing/
- BackendTrafficPolicy API：https://gateway.envoyproxy.io/docs/api/extension_types/#backendtrafficpolicy
- ConsistentHash API：https://gateway.envoyproxy.io/docs/api/extension_types/#consistenthash
- Envoy Maglev 算法：https://www.envoyproxy.io/docs/envoy/latest/intro/arch_overview/upstream/load_balancing/load_balancers#maglev
