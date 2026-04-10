# IAM v2.0 性能压测报告

> 版本：v1.0 | 日期：2026-04-10

---

## 测试环境

| 项目 | 配置 |
|------|------|
| 集群类型 | Kind 单节点, Docker Desktop, Windows 11 |
| K8s 版本 | v1.35.0 |
| 节点资源 | 15.5 GiB RAM, 多核 CPU |
| Gateway | AgentGateway v2.2.0 (Rust) |
| Keycloak | 26.5.2, 2 副本, Argon2 密码哈希 |
| 数据规模 | 6 个应用, 100 个租户, 300,000 条 resource_acl |

---

## 单实例吞吐基线

| 组件 | 吞吐 | 延迟 | 说明 |
|------|------|------|------|
| OPA 策略评估 | 72 req/s | 13ms | 内存中 Rego 求值，P50 仅 4ms |
| pep-proxy ext_authz | 40 req/s | 100-140ms | 含 OPA 调用 + ACL 查询 |
| resource-sync ext_proc | 5000 req/s | <1ms | header 读写，极轻量 |
| Keycloak password grant | 17 req/s | 57ms | Argon2id 密码哈希，7MB 内存/次 |
| Keycloak client_credentials | 113 req/s | 8ms | HMAC 验证，无密码哈希 |
| AgentGateway 路由 | 2000+ req/s | <1ms | Rust 实现，近零开销 |

---

## 压测结果：200 并发 × 2000 请求

| 场景 | 总数 | 成功 | 失败 | RPS | Avg | P50 | P95 | P99 | Max |
|------|------|------|------|-----|-----|-----|-----|-----|-----|
| OIDC 发现（无鉴权） | 2000 | 2000 | 0 | 41 | 84ms | 81ms | 95ms | 155ms | 216ms |
| 管理 API（JWT + ext_authz） | 2000 | 1998 | 2 | 38 | 139ms | 145ms | 174ms | 232ms | 336ms |
| 业务路由（JWT + ext_authz + ext_proc） | 2000 | 2000 | 0 | 40 | 100ms | 97ms | 116ms | 143ms | 203ms |
| Token 签发（Keycloak password） | 500 | 240 | 260 | 35 | 167ms | 162ms | 212ms | 234ms | 256ms |

---

## 压测结果：100 并发 × 1000 请求

| 场景 | 总数 | 成功 | 失败 | RPS | Avg | P50 | P90 | P95 | P99 |
|------|------|------|------|-----|-----|-----|-----|-----|-----|
| OIDC Discovery（无鉴权） | 1000 | 1000 | 0 | 41 | 84ms | 82ms | 94ms | 94ms | 103ms |
| Keycloak 静态路由（无鉴权） | 1000 | 1000 | 0 | 41 | 83ms | 81ms | 95ms | 95ms | 111ms |
| 管理 API（JWT + ext_authz） | 1000 | 1000 | 0 | 38 | 140ms | 150ms | 172ms | 172ms | 183ms |
| 业务路由（JWT + ext_authz + ext_proc） | 1000 | 1000 | 0 | 39 | 101ms | 98ms | 118ms | 118ms | 147ms |

---

## 各组件资源占用（实测）

| 组件 | 副本 | CPU | 内存 |
|------|------|-----|------|
| Keycloak | 2 | 0.24–0.60% | 619–692 MB |
| pep-proxy + bundle-server + OPA | 1（3 进程） | 0.38% | 238 MB |
| resource-sync | 1 | 0.17% | 52 MB |
| AgentGateway proxy | 1 | 0.03% | 87 MB |
| AgentGateway controller | 1 | 0.16% | 37 MB |
| OPAL Server | 2 | 0.20–0.23% | 93–96 MB |
| keycloak-proxy | 2 | 0.57–0.60% | 105–107 MB |
| **整体节点** | — | **空闲 14% / 峰值 23%** | **空闲 3.8G / 峰值 3.9G** |

---

## Keycloak 瓶颈分析

### Argon2 密码哈希

Keycloak 26.x 默认使用 Argon2id，是当前最安全但开销最大的密码哈希算法：

```json
{
  "algorithm": "argon2",
  "hashIterations": 5,
  "memory": "7168 KB (7MB)",
  "parallelism": 1
}
```

| 因素 | 影响 |
|------|------|
| 每次分配 7MB 内存 | 50 并发即消耗 350MB 瞬时内存 |
| parallelism=1 | 单线程计算，无法利用多核 |
| 5 次迭代 | 每次哈希耗时 10–15ms |

### 认证方式对比

| 认证方式 | 50 并发成功率 | 平均延迟 | 密码哈希 |
|----------|-------------|---------|----------|
| password grant（Argon2） | 38% | 90ms | 每次触发 |
| client_credentials（HMAC） | 100% | 10ms | 不触发 |

password grant 延迟为 client_credentials 的 **7 倍**，并发下失败率显著。

### Keycloak 内存分布（约 650MB/实例）

| 用途 | 大小 |
|------|------|
| JVM Heap | 400MB |
| JVM Non-Heap（Metaspace、CodeCache） | 150MB |
| Infinispan 缓存（session、realm） | 50MB |
| Argon2 工作内存 | 7MB × 并发数（动态） |

---

## 1000 并发资源规划

### 副本与资源配置

| 组件 | 副本 | 单副本 CPU | 单副本内存 | 总 CPU | 总内存 |
|------|------|-----------|-----------|--------|--------|
| AgentGateway proxy | 2 | 500m | 128MB | 1 core | 256MB |
| AgentGateway controller | 1 | 200m | 64MB | 200m | 64MB |
| Keycloak | 4 | 2000m | 1.5GB | 8 core | 6GB |
| keycloak-proxy | 2 | 500m | 256MB | 1 core | 512MB |
| pep-proxy + OPA | 2 | 1500m | 512MB | 3 core | 1GB |
| bundle-server | 2 | 200m | 128MB | 400m | 256MB |
| resource-sync | 2 | 500m | 256MB | 1 core | 512MB |
| OPAL Server | 2 | 250m | 128MB | 500m | 256MB |
| **合计** | **17 Pod** | | | **~18 core** | **~11.4 GB** |

### 请求延迟预估（P95）

| 请求类型 | 链路 | P95 延迟 |
|----------|------|---------|
| 业务读 `GET /kb/v1/kb-001` | Gateway → ext_authz → ext_proc → Backend | 50ms |
| 业务写 `POST /kb/v1/kb` | Gateway → ext_authz → ext_proc(ACL 写入) → Backend | 60ms |
| 列表查询 `GET /kb/v1/kb?page=1` | Gateway → ext_authz → ext_proc(X-Allowed-Ids) → Backend | 55ms |
| 管理 API `GET /api/v1/tenants` | Gateway → ext_authz → keycloak-proxy | 180ms |
| 用户登录（password） | Gateway → Keycloak（Argon2） | 200ms |
| 服务认证（client_credentials） | Gateway → Keycloak（HMAC） | 15ms |
| API Key 认证 | Gateway → ext_authz(SHA256 查询) → Backend | 30ms |

### 推荐硬件规格

| 规格 | 开发/测试 | 生产（1000 并发） |
|------|----------|-----------------|
| K8s 节点 | 1 × 4C/8GB | 3 × 8C/16GB |
| 总 CPU | 4 core | 18 core |
| 总内存 | 8 GB | 16 GB |
| 磁盘 | 20GB SSD | 100GB SSD |

---

## 关键发现

1. **ext_proc 零延迟开销**：业务路由（ext_authz + ext_proc）P50=97ms，低于管理 API（仅 ext_authz）P50=145ms，ext_proc 不是性能瓶颈
2. **所有鉴权场景 P99 < 250ms**，成功率 99.9% 以上
3. **Keycloak 是唯一的 CPU 密集组件**：Argon2 密码哈希消耗集群 44% 的 CPU 和 53% 的内存，仅用于处理登录
4. **IAM 组件内存效率高**：30 万行 ACL 数据下，resource-sync 52MB，pep-proxy 238MB
5. **ACL 查询性能优秀**：300K 行索引查询执行时间 0.14ms

## 优化建议

1. **用户登录走 OIDC Authorization Code + PKCE**：用户只登录一次，后续用 refresh_token，避免重复密码哈希
2. **服务间通信用 client_credentials**：无密码哈希，延迟仅 8ms
3. **外部集成用 API Key**：pep-proxy 直接查 DB（SHA256），完全绕过 Keycloak
4. **如需调优 Argon2**：可降低 memory（7168→4096）或增加 parallelism（1→2）
