# Envoy Gateway HTTPS 证书与可观测性配置指南

> 版本：v2.0（对齐 Envoy Gateway v1.7.x）
> 日期：2026-04-14
> 官方文档：https://gateway.envoyproxy.io/docs/

---

## 1 总体架构

```
          HTTPS (加密)
用户/客户端 ──────────────►  Envoy Gateway (TLS Terminate)
                              │
                              │ HTTP 明文 (集群内网)
                              ├──► keycloak         (/realms/*, /admin/*)
                              ├──► keycloak-proxy   (/api/v1/*)
                              ├──► memory-service   (/memory/*)
                              └──► kb-service       (/knowledgebase/*)

证书集中在 Gateway 这一层；后端服务收到明文 HTTP，无需各自配证书。
```

**观测链路**：

```
Envoy Gateway
  ├── 数据面 metrics  → /stats/prometheus (port 19001)   → Prometheus
  ├── 控制面 metrics  → /metrics                         → Prometheus
  ├── Access Log     → stdout / File / OTel             → Loki / 文件
  └── Traces         → OTLP gRPC (port 4317)            → Tempo / Jaeger
```

所有数据面观测能力（metrics/tracing/accesslog）都通过 **`EnvoyProxy` CRD** 配置，`EnvoyProxy` 再通过 `parametersRef` 关联到 `GatewayClass`。

---

## 2 HTTPS 配置

### 2.1 生成证书（测试环境自签）

```bash
mkdir -p example_certs && cd example_certs

# 1. 生成自签 CA
openssl req -x509 -sha256 -nodes -days 365 -newkey rsa:2048 \
  -subj '/O=example Inc./CN=example.com' \
  -keyout example.com.key -out example.com.crt

# 2. 生成服务端证书 (SAN = 实际访问域名)
openssl req -out gateway.csr -newkey rsa:2048 -nodes \
  -keyout gateway.key \
  -subj "/CN=gateway.aidp.com/O=aidp"

openssl x509 -req -days 365 \
  -CA example.com.crt -CAkey example.com.key -set_serial 0 \
  -in gateway.csr -out gateway.crt
```

> 生产环境：使用企业 PKI 签发证书，或 cert-manager 自动签发 Let's Encrypt 证书。

### 2.2 创建 Kubernetes Secret

```bash
kubectl create secret tls gateway-cert \
  -n envoy-gateway-system \
  --cert=example_certs/gateway.crt \
  --key=example_certs/gateway.key
```

### 2.3 Gateway 监听 HTTPS

**关键字段**：`listeners[].tls.mode: Terminate` + `certificateRefs`。

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: eg
  namespace: envoy-gateway-system
spec:
  gatewayClassName: eg
  listeners:
    - name: http
      protocol: HTTP
      port: 80
      allowedRoutes:
        namespaces:
          from: All
    - name: https
      protocol: HTTPS
      port: 443
      tls:
        mode: Terminate              # Gateway 解密 → 后端明文
        certificateRefs:
          - kind: Secret
            group: ""
            name: gateway-cert
      allowedRoutes:
        namespaces:
          from: All
```

> 单 Gateway、多 listener、跨 namespace 路由。跨 namespace 需 `ReferenceGrant`。

### 2.4 HTTPRoute 绑定

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: kb-route
  namespace: envoy-gateway-system
spec:
  parentRefs:
    - name: eg
      namespace: envoy-gateway-system
      sectionName: https           # 显式绑定 HTTPS listener
  hostnames:
    - gateway.aidp.com
  rules:
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
          port: 80
```

### 2.5 验证

```bash
# 取 Gateway 外部地址
export GATEWAY_HOST=$(kubectl get gateway/eg -n envoy-gateway-system \
  -o jsonpath='{.status.addresses[0].value}')

# HTTPS 请求验证
curl -v -HHost:gateway.aidp.com \
  --resolve "gateway.aidp.com:443:${GATEWAY_HOST}" \
  --cacert example_certs/example.com.crt \
  https://gateway.aidp.com/knowledgebase/v1/kb

# 无 LoadBalancer（Kind/本地）：port-forward
export ENVOY_SVC=$(kubectl get svc -n envoy-gateway-system \
  --selector=gateway.envoyproxy.io/owning-gateway-namespace=envoy-gateway-system,\
gateway.envoyproxy.io/owning-gateway-name=eg \
  -o jsonpath='{.items[0].metadata.name}')

kubectl -n envoy-gateway-system port-forward svc/${ENVOY_SVC} 8443:443 &

curl -v -HHost:gateway.aidp.com \
  --resolve "gateway.aidp.com:8443:127.0.0.1" \
  --cacert example_certs/example.com.crt \
  https://gateway.aidp.com:8443/knowledgebase/v1/kb
```

### 2.6 证书热更新

Envoy 通过 **SDS（Secret Discovery Service）**动态加载证书。修改 Secret 后 Envoy 自动重新加载，**不中断服务、不重启 Pod**。

```bash
# 用新证书覆盖同名 Secret
kubectl create secret tls gateway-cert \
  -n envoy-gateway-system \
  --cert=new-gateway.crt --key=new-gateway.key \
  --dry-run=client -o yaml | kubectl apply -f -
```

**替换前校验脚本**：

```bash
#!/bin/bash
CERT=$1; KEY=$2

echo "== 有效期 =="
openssl x509 -noout -dates -in "$CERT"

echo "== 域名 (SAN) =="
openssl x509 -noout -text -in "$CERT" | grep -A1 "Subject Alternative Name"

echo "== 证书-私钥匹配 =="
CERT_MD5=$(openssl x509 -noout -modulus -in "$CERT" | md5sum | awk '{print $1}')
KEY_MD5=$(openssl rsa  -noout -modulus -in "$KEY"  | md5sum | awk '{print $1}')
[ "$CERT_MD5" = "$KEY_MD5" ] && echo "OK" || { echo "MISMATCH"; exit 1; }

echo "== 证书链 =="
openssl verify -CAfile ca.crt "$CERT" || exit 1
```

**常见替换失败原因**：

| 现象 | 根因 | 解决 |
|---|---|---|
| TLS 握手失败 | 证书和私钥不匹配 | `modulus md5` 对比 |
| 浏览器警告 | 证书链不完整 | 拼接 `cat server.crt intermediate.crt > fullchain.crt` |
| 客户端 "hostname doesn't match" | SAN 不含访问域名 | 重新签发包含正确 SAN 的证书 |
| 握手成功但 5xx | 证书已过期 | `openssl x509 -noout -dates` 检查 |

### 2.7 后端为什么不配证书

Keycloak、pep-proxy、keycloak-proxy、resource-sync 全部在 Gateway 后面，通过集群内网 HTTP 通信，证书集中在 Gateway 一处管理。

Keycloak 的关键配置确认：
- `KC_HTTP_ENABLED=true`（启用 HTTP，不是 HTTPS）
- `KC_PROXY_HEADERS=xforwarded`（信任 Gateway 的 `X-Forwarded-*`）
- 监听 8080（HTTP）

**SAML/OIDC 登录也不需要后端配证书**，因为 SAML 断言是浏览器重定向转发的，Keycloak 不直接回访外部 IdP。

---

## 3 可观测性总体架构

Envoy Gateway v1.7 的观测能力分两层：

| 层 | 组件 | 暴露什么 | 默认开启？ |
|---|---|---|---|
| **控制面** | envoy-gateway controller | reconcile 耗时、xDS 推送统计 | 是（Prometheus 格式） |
| **数据面** | envoy-proxy Pod | 请求级 metrics、traces、access log | Metrics 默认开；Trace/AccessLog 需显式开 |

所有数据面观测配置统一通过 **`EnvoyProxy` CRD** 完成，挂到 GatewayClass：

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: GatewayClass
metadata:
  name: eg
spec:
  controllerName: gateway.envoyproxy.io/gatewayclass-controller
  parametersRef:
    group: gateway.envoyproxy.io
    kind: EnvoyProxy
    name: custom-proxy-config
    namespace: envoy-gateway-system
---
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: EnvoyProxy
metadata:
  name: custom-proxy-config
  namespace: envoy-gateway-system
spec:
  telemetry:
    metrics:     { ... }
    tracing:     { ... }
    accessLog:   { ... }
```

---

## 4 Metrics

### 4.1 数据面 Metrics（默认开启）

每个 `envoy-proxy` Pod 在 **admin port `19001`** 暴露 Prometheus 格式指标：

```bash
ENVOY_POD=$(kubectl get pod -n envoy-gateway-system \
  -l gateway.envoyproxy.io/owning-gateway-name=eg -o jsonpath='{.items[0].metadata.name}')

kubectl port-forward -n envoy-gateway-system pod/$ENVOY_POD 19001:19001
curl http://localhost:19001/stats/prometheus
```

**关键指标**（都以 `envoy_` 前缀，每个 listener/route/cluster 都有独立 label）：

| 指标 | 含义 |
|---|---|
| `envoy_http_downstream_rq_total` | 入站请求总数 |
| `envoy_http_downstream_rq_xx{envoy_response_code_class="2"}` | 2xx 响应数 |
| `envoy_http_downstream_rq_time_bucket` | 请求总耗时直方图（P50/P99 分位） |
| `envoy_cluster_upstream_rq_total` | 后端请求总数 |
| `envoy_cluster_upstream_rq_time_bucket` | 后端响应耗时直方图 |
| `envoy_listener_downstream_cx_active` | 当前活跃连接数 |
| `envoy_cluster_upstream_cx_connect_fail` | 后端连接失败数 |

**禁用 / 切换到 OpenTelemetry**：

```yaml
# 禁用 Prometheus
spec:
  telemetry:
    metrics:
      prometheus:
        disable: true

# 额外导出到 OTel Collector
spec:
  telemetry:
    metrics:
      sinks:
        - type: OpenTelemetry
          openTelemetry:
            host: otel-collector.monitoring.svc.cluster.local
            port: 4317
```

### 4.2 控制面 Metrics

`envoy-gateway` controller 自身的指标（reconcile 性能、xDS 推送）：

```bash
kubectl port-forward -n envoy-gateway-system \
  deployment/envoy-gateway 19001:19001
curl http://localhost:19001/metrics
```

| 指标 | 含义 |
|---|---|
| `envoy_gateway_controller_reconcile_duration_seconds` | 每次 reconcile 耗时 |
| `envoy_gateway_controller_reconciliations_total{result=...}` | reconcile 总次数（按 success/error） |
| `envoy_gateway_xds_auth_rq_total` / `_success_total` / `_failure_total` | xDS 认证统计 |
| `envoy_gateway_xds_rejects_total` | 代理拒绝的 xDS 配置数 |

### 4.3 Prometheus Operator 抓取

如果集群已部署 kube-prometheus-stack，添加 `PodMonitor`：

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PodMonitor
metadata:
  name: envoy-gateway-proxy
  namespace: envoy-gateway-system
spec:
  selector:
    matchLabels:
      gateway.envoyproxy.io/owning-gatewayclass: eg
  podMetricsEndpoints:
    - port: metrics                # admin port 19001
      path: /stats/prometheus
      interval: 15s
---
apiVersion: monitoring.coreos.com/v1
kind: PodMonitor
metadata:
  name: envoy-gateway-controller
  namespace: envoy-gateway-system
spec:
  selector:
    matchLabels:
      control-plane: envoy-gateway
  podMetricsEndpoints:
    - port: metrics
      path: /metrics
      interval: 15s
```

---

## 5 分布式追踪（Tracing）

### 5.1 开启 Tracing

**所有 tracing 配置都在 `EnvoyProxy.spec.telemetry.tracing`**。支持 OpenTelemetry、Zipkin、Datadog 三类 provider。

**OpenTelemetry（推荐，可接 Jaeger/Tempo）**：

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: EnvoyProxy
metadata:
  name: custom-proxy-config
  namespace: envoy-gateway-system
spec:
  telemetry:
    tracing:
      samplingRate: 100        # 开发：100%；生产建议 1-10%
      provider:
        type: OpenTelemetry
        backendRefs:
          - name: otel-collector
            namespace: monitoring
            port: 4317
      customTags:
        # 把鉴权后的 Header 加入 Span，便于按 user/tenant 分析
        "auth.user_id":
          type: RequestHeader
          requestHeader:
            name: X-Auth-User-Id
            defaultValue: "-"
        "auth.tenant":
          type: RequestHeader
          requestHeader:
            name: X-Auth-Tenant
            defaultValue: "-"
        "auth.groups":
          type: RequestHeader
          requestHeader:
            name: X-Auth-Groups
            defaultValue: "-"
        "k8s.pod.name":
          type: Environment
          environment:
            name: ENVOY_POD_NAME
            defaultValue: "-"
```

**Zipkin**：

```yaml
spec:
  telemetry:
    tracing:
      samplingRate: 100
      provider:
        type: Zipkin
        backendRefs:
          - name: zipkin
            namespace: monitoring
            port: 9411
        zipkin:
          enable128BitTraceId: true
```

**低采样率（< 1%）**：

```yaml
tracing:
  samplingFraction:
    numerator: 1
    denominator: 1000        # 0.1%
```

### 5.2 部署 Jaeger（OTLP 接收端）

```yaml
apiVersion: v1
kind: Namespace
metadata: { name: monitoring }
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: jaeger
  namespace: monitoring
spec:
  replicas: 1
  selector: { matchLabels: { app: jaeger } }
  template:
    metadata: { labels: { app: jaeger } }
    spec:
      containers:
        - name: jaeger
          image: jaegertracing/all-in-one:1.60
          env:
            - name: COLLECTOR_OTLP_ENABLED
              value: "true"
          ports:
            - { name: ui,       containerPort: 16686 }
            - { name: otlp-grpc,containerPort: 4317  }
---
apiVersion: v1
kind: Service
metadata:
  name: otel-collector       # EnvoyProxy.tracing.backendRefs 指向这里
  namespace: monitoring
spec:
  selector: { app: jaeger }
  ports:
    - { name: otlp-grpc, port: 4317,  targetPort: 4317  }
    - { name: ui,        port: 16686, targetPort: 16686 }
```

查看 UI：

```bash
kubectl port-forward -n monitoring svc/otel-collector 16686:16686
# 浏览器打开 http://localhost:16686
```

### 5.3 Span 内容

Envoy Gateway 生成的每个 Span 包含以下标准字段（来自 Envoy 本身，遵循 OTel semantic conventions）：

| 类别 | 字段 | 示例 |
|---|---|---|
| Span 元数据 | `traceId` / `spanId` / `parentId` | 128/64 bit hex |
| | `name` | `ingress` / `egress` |
| | `kind` | `SERVER` |
| 时间 | `startTime` / `endTime` | RFC3339 纳秒 |
| HTTP | `http.method` | `GET` |
| | `http.url` | `https://gateway.aidp.com/knowledgebase/v1/kb/kb-001` |
| | `http.status_code` | `200` |
| | `http.user_agent` | `curl/8.0` |
| 网络 | `net.peer.ip` | `10.244.0.31` |
| | `net.peer.port` | `8080` |
| Envoy 专有 | `guid:x-request-id` | Envoy 生成的请求 ID |
| | `upstream_cluster` | `httproute/kb-route/rule/0` |
| | `response_flags` | `-` / `UH`（upstream unhealthy）等 |
| 自定义（customTags） | `auth.user_id` / `auth.tenant` / `auth.groups` | 由 header 注入 |

### 5.4 端到端链路

业务后端接入 OpenTelemetry SDK 后（同样发到 `otel-collector:4317`），通过共享 `traceId` 自动串联：

```
Gateway Span (ingress)        traceId=abc123  spanId=s1  duration=158ms
└─ Backend Span (kb-service)  traceId=abc123  spanId=s2  parent=s1  duration=145ms
   └─ DB Span (postgres)      traceId=abc123  spanId=s3  parent=s2  duration=12ms
```

这样才能拆分出 Gateway 开销 vs 后端处理 vs DB 耗时。仅开 Gateway 追踪的情况下，`duration` 包含后端处理全部时间，无法进一步拆分。

---

## 6 访问日志（Access Log）

### 6.1 默认行为

Envoy Gateway 默认启用 **File Sink** 到 stdout，以 JSON 格式输出：

```json
{
  "start_time": "2026-04-14T08:23:15.123Z",
  "method": "GET",
  "path": "/knowledgebase/v1/kb",
  "protocol": "HTTP/1.1",
  "response_code": 200,
  "response_flags": "-",
  "duration": 158,
  "bytes_received": 0,
  "bytes_sent": 2048,
  "upstream_host": "10.244.0.31:8080",
  "downstream_remote_address": "192.168.1.100:50314",
  "x-request-id": "abc-123-def",
  "user-agent": "curl/8.0"
}
```

通过 `kubectl logs` 即可查看。

### 6.2 自定义文本格式

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: EnvoyProxy
metadata:
  name: custom-proxy-config
  namespace: envoy-gateway-system
spec:
  telemetry:
    accessLog:
      settings:
        - format:
            type: Text
            text: |
              [%START_TIME%] "%REQ(:METHOD)% %REQ(X-ENVOY-ORIGINAL-PATH?:PATH)% %PROTOCOL%" %RESPONSE_CODE% %DURATION%ms user=%REQ(X-AUTH-USER-ID)% tenant=%REQ(X-AUTH-TENANT)%
          sinks:
            - type: File
              file:
                path: /dev/stdout
```

### 6.3 发送到 OpenTelemetry / Loki

```yaml
spec:
  telemetry:
    accessLog:
      settings:
        - format:
            type: JSON
            json:
              start_time: "%START_TIME%"
              method:     "%REQ(:METHOD)%"
              path:       "%REQ(X-ENVOY-ORIGINAL-PATH?:PATH)%"
              status:     "%RESPONSE_CODE%"
              duration:   "%DURATION%"
              user_id:    "%REQ(X-AUTH-USER-ID)%"
              tenant_id:  "%REQ(X-AUTH-TENANT)%"
              route:      "%ROUTE_NAME%"
          sinks:
            - type: OpenTelemetry
              openTelemetry:
                host: otel-collector.monitoring.svc.cluster.local
                port: 4317
                resources:
                  k8s.cluster.name: "aidp-prod"
```

### 6.4 基于 CEL 过滤（只记录错误）

```yaml
spec:
  telemetry:
    accessLog:
      settings:
        - matches:
            - "response.code >= 400"       # 只记录 4xx/5xx
          format:
            type: Text
            text: "[%START_TIME%] %RESPONSE_CODE% %REQ(:PATH)% - %RESPONSE_FLAGS%"
          sinks:
            - type: File
              file:
                path: /dev/stdout
```

### 6.5 完全禁用

```yaml
spec:
  telemetry:
    accessLog:
      disable: true
```

---

## 7 一键部署可观测性栈（Addons Helm Chart）

官方提供 Addons Chart，一次安装 Prometheus + Grafana + 可选 OTel Collector：

```bash
# 基础版（Prometheus + Grafana）
helm install eg-addons oci://docker.io/envoyproxy/gateway-addons-helm \
  --version v1.7.1 \
  -n monitoring --create-namespace

# 启用 OTel Collector（Tracing/Log 需要）
helm install eg-addons oci://docker.io/envoyproxy/gateway-addons-helm \
  --version v1.7.1 \
  -n monitoring --create-namespace \
  --set opentelemetry-collector.enabled=true
```

**包含的 Grafana Dashboard**（`Dashboards → envoy-gateway`）：

| Dashboard | 内容 |
|---|---|
| **Envoy Proxy Global** | 每个代理实例的下游/上游 QPS、延迟、错误率 |
| **Envoy Clusters** | 后端集群聚合指标（连接池、熔断、重试） |
| **Envoy Gateway Global** | 控制面 xDS 推送、reconcile 性能 |
| **Resources Monitor** | 控制面/数据面 Pod 的 CPU/内存 |

访问 Grafana：

```bash
kubectl port-forward -n monitoring svc/eg-addons-grafana 3000:80
# 浏览器打开 http://localhost:3000
# 默认 admin / admin
```

---

## 8 AIDP 项目落地推荐

基于本项目（多租户 IAM + Keycloak + OPA）的特点，推荐分阶段落地：

### 8.1 阶段 1：基线可观测（零额外成本）

- **HTTPS**：配置 Gateway listener + `gateway-cert` Secret，cert-manager 自动续期
- **Metrics**：什么都不做 — 数据面 `/stats/prometheus` 默认开启，控制面 `/metrics` 默认开启
- **Access Log**：什么都不做 — 默认输出 JSON 到 stdout，`kubectl logs` 查看

### 8.2 阶段 2：集中采集（引入 Prometheus + Grafana）

- 部署 kube-prometheus-stack 或官方 `gateway-addons-helm`
- 添加 `PodMonitor` 抓取数据面和控制面
- 导入官方 4 个 Dashboard

### 8.3 阶段 3：请求级追踪（排查性能和鉴权问题）

- 部署 Jaeger（OTLP 接收） → namespace `monitoring`
- 配置 `EnvoyProxy.spec.telemetry.tracing`，指向 `otel-collector:4317`
- **customTags 必须注入 `X-Auth-User-Id / X-Auth-Tenant / X-Auth-Groups`**，才能按租户/用户维度过滤链路
- 生产环境 `samplingRate: 10`（10%）控制成本

### 8.4 阶段 4：完整可观测平台（可选）

- 日志：EnvoyProxy AccessLog OTel sink → Loki
- 指标：Prometheus → 长期存储 Mimir/Thanos
- 链路：OTel Collector → Tempo
- 业务后端（keycloak-proxy、pep-proxy、resource-sync）接入 OTel SDK，与 Gateway 的 Span 通过 `traceId` 串联

---

## 9 常见问题速查

| 问题 | 排查起点 |
|---|---|
| HTTPS 访问 404 | `kubectl describe httproute`，检查 `parentRefs.sectionName` 是否匹配 listener |
| 证书替换后仍用旧证书 | Envoy SDS 秒级热加载，若 >30s 仍旧，检查 `kubectl describe gateway eg`，确认 `Accepted` 状态 |
| Prometheus 抓不到数据 | `kubectl port-forward pod/$ENVOY_POD 19001 && curl :19001/stats/prometheus`；无输出则检查 EnvoyProxy 是否禁用了 metrics |
| Jaeger UI 无 trace | 检查 `samplingRate`（是否为 0）、`backendRefs` 服务名是否正确、otel-collector 是否启动 |
| 只看到 Gateway Span 没有后端 Span | 后端服务需自行接入 OTel SDK，共享 `traceparent` Header |
| AccessLog 里看不到 `X-Auth-*` | 确认 pep-proxy 在 `CheckResponse` 中注入了 header；访问日志 format 中要用 `%REQ(X-AUTH-USER-ID)%` |

---

## 10 参考链接

- HTTPS 配置：https://gateway.envoyproxy.io/docs/tasks/security/secure-gateways/
- 证书轮换：https://gateway.envoyproxy.io/docs/tasks/security/tls-cert-manager/
- 数据面 Metrics：https://gateway.envoyproxy.io/docs/tasks/observability/proxy-metric/
- 数据面 Tracing：https://gateway.envoyproxy.io/docs/tasks/observability/proxy-trace/
- 数据面 AccessLog：https://gateway.envoyproxy.io/docs/tasks/observability/proxy-accesslog/
- 控制面 Metrics：https://gateway.envoyproxy.io/docs/tasks/observability/control-plane-metrics/
- Grafana 集成：https://gateway.envoyproxy.io/docs/tasks/observability/grafana-integration/
- EnvoyProxy CRD：https://gateway.envoyproxy.io/docs/api/extension_types/#envoyproxy
