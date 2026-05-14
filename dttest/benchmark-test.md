# IAM 性能压测报告（OPA & Keycloak）

**版本**：v1.0 | **日期**：2026-05-14

---

## 测试环境

| 项目 | 配置 |
|------|------|
| 集群类型 | Kind 单节点，Docker Desktop，Windows 11 |
| K8s 版本 | v1.35.0 |
| 节点资源 | 15.5 GiB RAM，多核 CPU |
| Gateway | Envoy Gateway v1.7.0 |
| Keycloak | 26.5.2，2 副本，Argon2id 密码哈希 |
| OPA | 0.42.2-static，sidecar 部署 |
| 数据规模 | 6 个应用，100 个租户，300,000 条 resource_acl |
| 压测工具 | da-cluster/scripts/bench.sh |

---

## 一、OPA 路径级鉴权性能

### 1.1 单实例基线

| 指标 | 数值 |
|------|------|
| 吞吐量 | 72 req/s |
| 平均延迟 | 13ms |
| P50 延迟 | 4ms |
| 资源占用（CPU） | 含 pep-proxy + bundle-server 共 0.38% |
| 资源占用（内存） | 含 pep-proxy + bundle-server 共 238 MB |

> OPA 在内存中执行 Rego 求值，P50 仅 4ms，不是系统瓶颈。

### 1.2 pep-proxy ext_authz 端到端（含 OPA 调用 + ACL 查询）

| 指标 | 数值 |
|------|------|
| 吞吐量 | 40 req/s |
| 平均延迟 | 100–140ms |

### 1.3 并发压测结果

**200 并发 × 2000 请求**

| 场景 | 总数 | 成功 | 失败 | RPS | Avg | P50 | P95 | P99 | Max |
|------|------|------|------|-----|-----|-----|-----|-----|-----|
| 业务路由（JWT + ext_authz + ext_proc） | 2000 | 2000 | 0 | 40 | 100ms | 97ms | 116ms | 143ms | 203ms |
| 管理 API（JWT + ext_authz） | 2000 | 1998 | 2 | 38 | 139ms | 145ms | 174ms | 232ms | 336ms |

**100 并发 × 1000 请求**

| 场景 | 总数 | 成功 | 失败 | RPS | Avg | P50 | P90 | P95 | P99 |
|------|------|------|------|-----|-----|-----|-----|-----|-----|
| 业务路由（JWT + ext_authz + ext_proc） | 1000 | 1000 | 0 | 39 | 101ms | 98ms | 118ms | 118ms | 147ms |
| 管理 API（JWT + ext_authz） | 1000 | 1000 | 0 | 38 | 140ms | 150ms | 172ms | 172ms | 183ms |

### 1.4 ACL 查询性能

| 数据规模 | 查询方式 | 执行时间 |
|----------|----------|----------|
| 300,000 行 | 索引查询（idx_acl_resource） | 0.14ms |

### 1.5 OPA 结论

- 业务路由全链路（ext_authz + ext_proc）P99 < 150ms，成功率 100%
- 管理 API 链路 P99 < 250ms，成功率 99.9%
- ext_proc 本身近零开销（< 1ms），不是瓶颈
- OPA Rego 求值 P50 仅 4ms，不是瓶颈；链路延迟主要来自 ACL 数据库查询

---

## 二、Keycloak 认证性能

### 2.1 单实例基线

| 认证方式 | 吞吐量 | 平均延迟 | 说明 |
|----------|--------|----------|------|
| password grant（Argon2id） | 17 req/s | 57ms | 每次触发密码哈希，7MB 内存/次 |
| client_credentials（HMAC） | 113 req/s | 8ms | 无密码哈希，纯 JWT 签发 |

password grant 吞吐量是 client_credentials 的 **1/7**，延迟高 **7 倍**。

### 2.2 并发压测结果

**200 并发 × 500 请求（Token 签发）**

| 场景 | 总数 | 成功 | 失败 | RPS | Avg | P50 | P95 | P99 | Max |
|------|------|------|------|-----|-----|-----|-----|-----|-----|
| password grant | 500 | 240 | 260 | 35 | 167ms | 162ms | 212ms | 234ms | 256ms |
| OIDC Discovery（无鉴权） | 2000 | 2000 | 0 | 41 | 84ms | 81ms | 95ms | 155ms | 216ms |

**100 并发 × 1000 请求**

| 场景 | 总数 | 成功 | 失败 | RPS | Avg | P50 | P90 | P95 | P99 |
|------|------|------|------|-----|-----|-----|-----|-----|-----|
| OIDC Discovery | 1000 | 1000 | 0 | 41 | 84ms | 82ms | 94ms | 94ms | 103ms |
| Keycloak 静态路由 | 1000 | 1000 | 0 | 41 | 83ms | 81ms | 95ms | 95ms | 111ms |

### 2.3 Argon2id 瓶颈分析

| 参数 | 值 | 影响 |
|------|----|------|
| 算法 | Argon2id | 最安全但开销最大 |
| 内存 | 7168 KB（7MB）/次 | 50 并发 = 350MB 瞬时内存 |
| 迭代次数 | 5 | 每次哈希耗时 10–15ms |
| parallelism | 1 | 单线程，无法利用多核 |

**50 并发下认证方式对比**

| 认证方式 | 成功率 | 平均延迟 |
|----------|--------|----------|
| password grant | 38% | 90ms |
| client_credentials | 100% | 10ms |

> 200 并发下 password grant 失败率达 52%（260/500），Keycloak 是系统唯一的 CPU 密集型瓶颈。

### 2.4 Keycloak 内存分布（约 650MB / 实例）

| 用途 | 大小 |
|------|------|
| JVM Heap | 400MB |
| JVM Non-Heap（Metaspace、CodeCache） | 150MB |
| Infinispan 缓存（session、realm） | 50MB |
| Argon2 工作内存 | 7MB × 并发数（动态） |

### 2.5 Keycloak 结论

- **client_credentials / API Key** 场景性能优秀，P99 < 30ms，适合服务间高频调用
- **password grant** 受 Argon2id 限制，高并发下失败率显著，生产环境需扩副本
- 1000 并发规划需 Keycloak 扩至 4 副本（8 core / 6GB）

---

## 三、1000 并发规划目标（P95）

| 请求类型 | 目标延迟 | 吞吐量 |
|----------|----------|--------|
| 业务读（GET） | 50ms | 1000+ 并发 |
| 业务写（POST） | 60ms | 1000+ 并发 |
| 列表查询（带分页） | 55ms | 1000+ 并发 |
| 管理 API | 180ms | 500+ 并发 |
| 用户登录（password） | 200ms | 60–70 req/s |
| 服务认证（client_credentials） | 15ms | 400+ req/s |
| API Key 认证 | 30ms | 1000+ 并发 |

---

## 四、优化建议

| 场景 | 建议 |
|------|------|
| 用户登录 | 走 OIDC Authorization Code + PKCE，用户只登录一次，后续用 refresh_token，避免重复触发 Argon2 |
| 服务间调用 | 使用 client_credentials，无密码哈希，延迟仅 8ms |
| 外部系统集成 | 使用 API Key，pep-proxy 直接 SHA256 查 DB，完全绕过 Keycloak |
| Argon2 调优 | 可将 memory 从 7168 降至 4096，或将 parallelism 从 1 调至 2，在安全性与性能间取平衡 |
| Keycloak 扩容 | 高并发场景扩至 4 副本，每副本 2 core / 1.5GB |
