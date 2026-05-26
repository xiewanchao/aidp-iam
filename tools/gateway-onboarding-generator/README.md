# AIDP Gateway 接入生成器

打开 `index.html` 即可使用，不需要启动服务。

默认值按当前本地部署写死：

- Gateway namespace：`aidp-gateway`
- Gateway 名称：`eg`
- IAM namespace：`aidp-iam`
- 鉴权服务：`pep-proxy.aidp-iam.svc:9000`
- ACL 自动同步服务：`resource-sync.aidp-iam.svc:8082`

输出内容：

- `K8s YAML`：HTTPRoute、ReferenceGrant、SecurityPolicy、EnvoyExtensionPolicy、BackendTrafficPolicy、ClientTrafficPolicy。
- `Manifest JSON`：用于 `PUT /AccessManager/Tenants/System/AppManifests/{namespace}` 的应用接入草稿。
- `命令`：apply、检查资源状态、注册 Manifest 和简单 curl 验证。

业务接入限流需求建议先按下表收集：

| 应用名 | 请求超时时间/s | 限流请求数/s/m/h/d | 入口 TCP 最大连接数 | 业务后端连接数限制 | 重试 |
|---|---:|---|---:|---|---|
| KnowledgeBase | 30s | 60/Minute | 1000 | maxConnections=500，maxParallelRequests=200 | 2 次，5xx/gateway-error/connect-failure，单次 2s |
| 业务应用名 | 例如 30s | 例如 100/Second | 例如 2000 | 例如 maxConnections=500，maxParallelRequests=200 | 次数、触发条件、单次超时、退避间隔 |

鉴权 `SecurityPolicy` 默认生成 `bodyToExtAuth.maxRequestBytes=1048576`，用于支持较大的 Manifest 或业务请求体进入 ext_auth 鉴权。业务不需要 body 鉴权时，可清空“鉴权请求体上限 bytes”，生成结果将不包含 `bodyToExtAuth`。

勾选“Gateway 级 TCP 连接数限制”后，会生成 `ClientTrafficPolicy.spec.connection.connectionLimit`。默认限制整个 Gateway，也可以选择只作用于 `http` 或 `https` listener。这个能力作用在入口侧，不按单条业务路由区分。

勾选“Route 级业务后端连接保护”后，会生成 `BackendTrafficPolicy.spec.circuitBreaker`，target 到当前 `HTTPRoute`，用于限制 Envoy 转发到业务后端时的连接数、等待请求数、并发请求数和并发重试数。这个能力可以按 route 配置，但它保护的是 Gateway 到后端 Service 的连接，不是客户端到 Gateway 的入口 TCP socket。

勾选“重试 BackendTrafficPolicy”后，会生成 `BackendTrafficPolicy.spec.retry`，支持配置重试次数、触发条件、HTTP 状态码、单次重试超时和退避间隔。

注意：ACL 自动同步需要同时 apply `EnvoyExtensionPolicy` 并注册 Manifest；只生成 Gateway 资源不会自动写入 `resource_patterns`。
