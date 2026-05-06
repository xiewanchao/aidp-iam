# Gateway 路由与初始化测试策略及用例

## 1. 测试目标

验证 Gateway 独立部署包在标准 K8s 集群上的初始化、路由转发、跨 namespace 授权、安全默认拒绝和策略绑定能力。

对应 SR：

- `【Gateway】支持应用初始化与路由集成`

本文档只覆盖 `GW-01` 到 `GW-08`。业务 IP 与容器网络前端集成见 `tests/gateway-business-ip/docs/gateway-business-ip-test-plan.md`。

## 2. 测试范围

纳入范围：

- Gateway 独立 Helm 部署。
- CRD、GatewayClass、Gateway、EnvoyProxy、Envoy data-plane 就绪性。
- HTTPRoute 基础路由转发。
- ReferenceGrant 跨 namespace 引用授权。
- ReferenceGrant 缺失负面验证。
- IP 白名单配置。
- 路径白名单缺失默认拒绝。
- 流量处理、限流、DDoS 防护类策略绑定检查。

不纳入范围：

- IAM 用户认证、JWT、路径鉴权、资源鉴权。
- ACL 同步和 API Key 管理。
- 业务 IP 与容器网络前端集成。

## 3. 测试环境

- 标准 K8s 集群。
- `kubectl` 可访问目标集群。
- `helm` 可用。
- `package-gateway` 独立部署包可用。
- Gateway 镜像已加载，或集群可从镜像仓库拉取。
- 测试后端使用 `whoami` 或其他 HTTP echo 服务。

## 4. 测试策略

1. 先验证 Gateway 一键部署和重复部署幂等性。
2. 使用最小 HTTP 后端验证 Gateway 真实转发链路。
3. 对跨 namespace 引用做正反验证，确保 ReferenceGrant 缺失时不放行。
4. 对 IP 白名单和路径白名单做默认拒绝验证。
5. 检查流量处理、限流、DDoS 防护类策略是否按 SR 绑定到 Gateway 或 Route。

## 5. 测试用例

| 用例编号 | 用例名称 | 前置条件 | 测试步骤 | 预期结果 |
|---|---|---|---|---|
| GW-01 | 一键部署 Gateway | K8s 集群就绪，Gateway 镜像已加载或可拉取 | 1. 执行 Gateway 独立 Helm 安装。<br>2. 检查 CRD、Gateway controller、Gateway、Envoy data-plane Service。<br>3. 访问 Gateway 入口根路径。 | Helm 部署成功；controller Pod Running；Gateway `Programmed=True`；Envoy Service 暴露成功；无路由时访问返回 `404`。 |
| GW-02 | 幂等部署 | GW-01 已完成 | 1. 使用相同参数再次执行 `helm upgrade --install`。<br>2. 检查核心资源状态。<br>3. 检查已有 Gateway、Service、策略资源是否异常变化。 | 重复部署成功；不产生重复资源；不破坏已有配置；Gateway 仍可访问。 |
| GW-03 | 基础路由配置 | Gateway 已部署 | 1. 部署 `whoami` 后端服务。<br>2. 创建 HTTPRoute 指向后端 Service。<br>3. 检查 HTTPRoute `Accepted` 状态。<br>4. 访问 `/whoami`。 | HTTPRoute `Accepted=True`；请求通过 Gateway 转发到后端；返回后端服务响应。 |
| GW-04 | ReferenceGrant 缺失负面验证 | Gateway 与后端分别位于不同 namespace | 1. 创建跨 namespace HTTPRoute 引用后端 Service。<br>2. 不创建 ReferenceGrant。<br>3. 检查 HTTPRoute 状态。<br>4. 访问对应路径。 | HTTPRoute 不被接受或后端引用失败；请求不能到达后端；后端无访问日志。 |
| GW-05 | ReferenceGrant 补齐恢复 | GW-04 已执行，跨 namespace 路由未生效 | 1. 在后端 namespace 创建 ReferenceGrant。<br>2. 等待 Gateway reconcile。<br>3. 检查 HTTPRoute 状态。<br>4. 再次访问对应路径。 | HTTPRoute 恢复为 `Accepted=True`；请求可到达后端；返回正常响应。 |
| GW-06 | IP 白名单配置 | Gateway 可获取真实客户端源 IP | 1. 配置 Gateway/SecurityPolicy 来源 CIDR 白名单。<br>2. 从白名单 IP 访问测试路径。<br>3. 从非白名单 IP 访问测试路径。<br>4. 检查后端日志。 | 白名单来源请求放行；非白名单来源请求拒绝；被拒绝请求不会到达后端。 |
| GW-07 | 路径白名单缺失默认拒 | Gateway 已部署，已有测试后端 | 1. 仅配置允许路径，例如 `/whoami/allow`。<br>2. 访问允许路径。<br>3. 访问未配置路径，例如 `/whoami/deny` 或 `/unknown`。<br>4. 检查后端日志。 | 允许路径可访问；未配置路径默认拒绝或返回 `404/403`；未授权路径不应转发到后端。 |
| GW-08 | DDoS/流量策略绑定 | Gateway 已部署 | 1. 检查 Gateway 初始化后是否创建流量处理、限流或 DDoS 防护类策略。<br>2. 检查策略 `targetRef` 是否绑定 Gateway 或 HTTPRoute。<br>3. 如支持限流，发起超过阈值的请求。 | 策略资源存在且绑定正确；策略状态正常；如配置限流，超限请求被拒绝或限速。 |

## 6. 命令合集

### 6.1 通用变量

```bash
export GW_NS=aidp-iam
export EG_NS=envoy-gateway-system
export TEST_NS=test-backend
export RELEASE=aidp-gateway
export CHART=package-gateway/charts/aidp-gateway
export NODE_IP=<node-ip>
export GW_URL=http://${NODE_IP}:30080
```

### 6.2 GW-01 一键部署

```bash
helm install ${RELEASE} ${CHART} \
  --namespace ${GW_NS} --create-namespace \
  --wait --timeout=5m

kubectl get crd | grep -E "gateway\.|gateway.envoyproxy.io"
kubectl -n ${GW_NS} get pods -l control-plane=envoy-gateway
kubectl -n ${EG_NS} get gateway eg
kubectl -n ${GW_NS} get svc -l gateway.envoyproxy.io/owning-gateway-name=eg

curl -sS -o /dev/null -w "HTTP %{http_code}\n" ${GW_URL}/
```

### 6.3 GW-02 幂等部署

```bash
helm upgrade --install ${RELEASE} ${CHART} \
  --namespace ${GW_NS} --create-namespace \
  --wait --timeout=5m

helm -n ${GW_NS} status ${RELEASE}
kubectl -n ${EG_NS} get gateway eg
curl -sS -o /dev/null -w "HTTP %{http_code}\n" ${GW_URL}/
```

### 6.4 GW-03 基础路由配置

```bash
kubectl apply -f package-gateway/test/whoami-test.yaml
kubectl -n ${TEST_NS} wait --for=condition=Available deployment/whoami --timeout=60s

kubectl -n ${EG_NS} get httproute whoami-route -o jsonpath='{.status.parents[0].conditions[?(@.type=="Accepted")].status}'
echo

curl -sS ${GW_URL}/whoami
kubectl -n ${TEST_NS} logs deploy/whoami --tail=20
```

### 6.5 GW-04 ReferenceGrant 缺失负面验证

```bash
kubectl -n ${TEST_NS} delete referencegrant allow-gateway-to-whoami

kubectl -n ${EG_NS} get httproute whoami-route -o yaml
curl -sS -o /tmp/gw-refgrant-missing.out -w "HTTP %{http_code}\n" ${GW_URL}/whoami
cat /tmp/gw-refgrant-missing.out

kubectl -n ${TEST_NS} logs deploy/whoami --tail=20
```

执行前后对比 `whoami` 日志，确认请求没有新增命中。

### 6.6 GW-05 ReferenceGrant 补齐恢复

```bash
kubectl apply -f package-gateway/test/whoami-test.yaml

kubectl -n ${EG_NS} get httproute whoami-route -o jsonpath='{.status.parents[0].conditions[?(@.type=="Accepted")].status}'
echo

curl -sS ${GW_URL}/whoami
kubectl -n ${TEST_NS} logs deploy/whoami --tail=20
```

### 6.7 GW-06 IP 白名单配置

先确认 Gateway 能识别真实客户端 IP。如果 NodePort 或外部负载均衡发生 SNAT，需要按平台要求启用保源 IP。

```bash
export ALLOW_CIDR=<allowed-client-cidr>

kubectl -n ${EG_NS} apply -f - <<EOF
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: SecurityPolicy
metadata:
  name: whoami-source-ip-allowlist
spec:
  targetRef:
    group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: whoami-route
  authorization:
    defaultAction: Deny
    rules:
    - name: allow-source-cidr
      action: Allow
      principal:
        clientCIDRs:
        - ${ALLOW_CIDR}
EOF

kubectl -n ${EG_NS} get securitypolicy whoami-source-ip-allowlist -o yaml

curl -sS -o /tmp/gw-ip-allow.out -w "HTTP %{http_code}\n" ${GW_URL}/whoami
cat /tmp/gw-ip-allow.out
```

从非白名单来源重复访问，期望被拒绝，并检查后端日志没有新增命中。

### 6.8 GW-07 路径白名单缺失默认拒

```bash
kubectl -n ${EG_NS} apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: whoami-route
spec:
  parentRefs:
  - name: eg
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /whoami/allow
    backendRefs:
    - name: whoami
      namespace: test-backend
      port: 80
EOF

curl -sS -o /tmp/gw-path-allow.out -w "HTTP %{http_code}\n" ${GW_URL}/whoami/allow
cat /tmp/gw-path-allow.out

curl -sS -o /tmp/gw-path-deny.out -w "HTTP %{http_code}\n" ${GW_URL}/whoami/deny
cat /tmp/gw-path-deny.out

kubectl -n ${TEST_NS} logs deploy/whoami --tail=50
```

### 6.9 GW-08 DDoS/流量策略绑定

```bash
kubectl get backendtrafficpolicy,clienttrafficpolicy,securitypolicy -A
kubectl -n ${EG_NS} describe gateway eg
kubectl -n ${EG_NS} describe httproute whoami-route
```

如部署包内置限流策略，可用如下方式做粗略验证：

```bash
for i in $(seq 1 50); do
  curl -sS -o /dev/null -w "%{http_code}\n" ${GW_URL}/whoami &
done
wait
```

## 7. 清理命令

```bash
kubectl delete -f package-gateway/test/whoami-test.yaml --ignore-not-found
kubectl -n ${EG_NS} delete securitypolicy whoami-source-ip-allowlist --ignore-not-found

helm uninstall ${RELEASE} -n ${GW_NS}
kubectl delete ns ${GW_NS} --ignore-not-found
kubectl delete ns ${TEST_NS} --ignore-not-found
```

CRD 是集群级资源。除非是独立测试集群，否则不要默认清理 CRD。

## 8. 记录模板

| 用例编号 | 执行人 | 执行时间 | 结果 | 证据 | 备注 |
|---|---|---|---|---|---|
| GW-01 |  |  | Pass/Fail |  |  |
| GW-02 |  |  | Pass/Fail |  |  |
| GW-03 |  |  | Pass/Fail |  |  |
| GW-04 |  |  | Pass/Fail |  |  |
| GW-05 |  |  | Pass/Fail |  |  |
| GW-06 |  |  | Pass/Fail |  |  |
| GW-07 |  |  | Pass/Fail |  |  |
| GW-08 |  |  | Pass/Fail |  |  |
