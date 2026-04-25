# aidp-gateway 独立部署包

只装 Envoy Gateway（含 stripped CRD），不带 IAM。适合：

- 只需要 K8s 入口网关、业务自己处理认证
- 已有别人在装 IAM，你只负责前置网关
- 想先单独验证网关层是否健康（再叠 IAM 上去）

```
package-gateway/
├── README.md                                      ← 你正在看
├── charts/
│   └── aidp-gateway/                              Helm chart（含 9 个 stripped CRD）
├── images/
│   └── arm64/
│       ├── docker.io_envoyproxy_envoy_distroless-v1.37.0.tar    37 MB
│       └── docker.io_envoyproxy_gateway_v1.7.0.tar              68 MB
└── test/
    └── whoami-test.yaml                           端到端验证用例
```

包内**只附带网关本体需要的两个第三方镜像**（envoy data-plane + envoy-gateway controller）。验证用的 `traefik/whoami` 镜像没在包里 —— 在线集群自动 pull，离线集群 `isula pull traefik/whoami` 一下，或改成你现有的任何 HTTP 后端镜像。

---

## 部署

### 1. 加载镜像（每个节点）

```bash
# iSulad
for tar in package-gateway/images/arm64/*.tar; do isula load -i "$tar"; done

# containerd
for tar in package-gateway/images/arm64/*.tar; do ctr -n k8s.io images import "$tar"; done

# Docker
for tar in package-gateway/images/arm64/*.tar; do docker load -i "$tar"; done
```

> 这俩第三方镜像 tag 是干净的，**不需要**做 arch 后缀 alias。

### 2. 装 Gateway

```bash
helm install aidp-gateway package-gateway/charts/aidp-gateway \
  --namespace aidp-iam --create-namespace \
  --wait --timeout=5m
```

期望输出末尾有 `STATUS: deployed`，并且 NOTES 部分提示 NodePort 30080 已暴露。

### 3. 验证安装

```bash
# CRD 应该有 ~20 个（8 个 EG + 12 个 Gateway API experimental）
kubectl get crd | grep -E "gateway\." | wc -l

# 控制器 Running
kubectl -n aidp-iam get pods -l control-plane=envoy-gateway

# Gateway PROGRAMMED=True，有 ADDRESS
kubectl -n envoy-gateway-system get gateway eg

# Service 是 NodePort 80:30080/TCP
kubectl -n aidp-iam get svc -l gateway.envoyproxy.io/owning-gateway-name=eg

# Envoy data-plane 在响应（无路由 → 404 是正常的）
curl -sS -o /dev/null -w "HTTP %{http_code}\n" http://<节点 IP>:30080/
# 期望：HTTP 404
```

---

## 端到端测试（whoami 后端）

跑一个最小的 backend pod 验证流量真的能落到业务侧。

### 1. apply 测试 YAML

```bash
kubectl apply -f package-gateway/test/whoami-test.yaml

# 等 backend pod ready
kubectl -n test-backend wait --for=condition=Available deployment/whoami --timeout=60s
```

这个 YAML 会建：
- `test-backend` namespace
- `whoami` Deployment + Service（用 `traefik/whoami` 镜像）
- `whoami-route` HTTPRoute（`/whoami` 前缀 → whoami Service）
- `allow-gateway-to-whoami` ReferenceGrant（跨 ns 引用授权）

### 2. 验证 HTTPRoute Accepted

```bash
kubectl -n envoy-gateway-system get httproute whoami-route -o jsonpath='{.status.parents[0].conditions[?(@.type=="Accepted")].status}'
echo
# 期望：True
```

### 3. 打一下后端

```bash
curl http://<节点 IP>:30080/whoami
```

期望返回类似：

```
Hostname: whoami-778d7d4bdf-xnzbq
IP: 10.244.0.41
RemoteAddr: 10.244.0.40:44844
GET /whoami HTTP/1.1
Host: localhost:30080
User-Agent: curl/...
Accept: */*
X-Forwarded-For: ...
```

看到 `Hostname: whoami-...` 就证明：**Envoy 收到外部请求 → HTTPRoute 路由匹配 → ReferenceGrant 跨 ns 通过 → 后端 pod 真实响应**。整条链路通了。

### 4. 清理测试

```bash
kubectl delete -f package-gateway/test/whoami-test.yaml
```

---

## 自定义 values

`charts/aidp-gateway/values.yaml` 暴露的常用配置：

```yaml
gateway:
  port: 80                 # Envoy 监听端口

proxy:
  replicas: 1              # data-plane pod 副本数（多节点高可用调高）
  service:
    type: NodePort         # NodePort | ClusterIP | LoadBalancer
    nodePort: 30080        # 30000-32767
    externalIPs: []        # 可选：直接绑外部 IP
    externalTrafficPolicy: Cluster   # Cluster | Local
  hostNetwork: false       # true 时 envoy 用宿主机网络，监听节点 :80
```

部署时覆盖：

```bash
helm install aidp-gateway package-gateway/charts/aidp-gateway \
  --namespace aidp-iam --create-namespace \
  --set proxy.service.type=LoadBalancer \
  --set proxy.replicas=2
```

---

## 清理

```bash
helm uninstall aidp-gateway -n aidp-iam
kubectl delete ns aidp-iam

# CRD 是集群级、Helm 不动；要彻底清：
kubectl delete crd -l gateway.envoyproxy.io/crd=true
kubectl delete crd \
  gateways.gateway.networking.k8s.io \
  gatewayclasses.gateway.networking.k8s.io \
  httproutes.gateway.networking.k8s.io \
  referencegrants.gateway.networking.k8s.io \
  grpcroutes.gateway.networking.k8s.io \
  tcproutes.gateway.networking.k8s.io \
  tlsroutes.gateway.networking.k8s.io \
  udproutes.gateway.networking.k8s.io \
  backendtlspolicies.gateway.networking.k8s.io \
  xbackendtrafficpolicies.gateway.networking.x-k8s.io \
  xlistenersets.gateway.networking.x-k8s.io \
  xmeshes.gateway.networking.x-k8s.io \
  --ignore-not-found
```

> **`GatewayClass` 删除卡住？** 它有 `gateway-exists-finalizer.gateway.networking.k8s.io` finalizer，要等控制器先 reconcile 清掉。如果控制器已退出导致清不动：
>
> ```bash
> kubectl patch gatewayclass eg --type=merge -p '{"metadata":{"finalizers":[]}}'
> ```

---

## 跟主包 `package/` 的关系

`package/charts/aidp-gateway` 跟这里的 chart **是同一个**（设计上用同一份）。差别只在使用场景：

| | `package/` | `package-gateway/` |
|---|-----------|---------------------|
| 范围 | Gateway + IAM + mocks 全套 | 只有 Gateway |
| 镜像 | 13 个 tar | 2 个（envoy + gateway controller） |
| 测试 | 跟着 IAM 流程 | 内置 whoami 一条龙验证 |
| 适用 | 完整 IAM 部署场景 | 只要网关 / 验证 / 沙箱 |

需要 IAM 业务栈时直接用主包 `package/`。
