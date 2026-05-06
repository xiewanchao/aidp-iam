# Gateway 业务 IP 与容器网络前端集成测试策略及用例

## 1. 测试目标

验证 Gateway 在指定前端网络平面后，Pod 能自动获取业务 IP，并且该业务 IP 能作为 Gateway 对外入口；同时验证 Pod 重建、滚动升级变更网络平面、节点故障漂移时业务 IP 行为符合 SR。

对应 SR：

- `【Gateway】支持业务 IP 配置与容器网络前端集成`

本文档只覆盖 `BIP-01` 到 `BIP-04`。Gateway 基础安装与路由测试见 `tests/gateway-routing/docs/gateway-routing-test-plan.md`。

## 2. 测试范围

纳入范围：

- 指定前端网络平面部署 Gateway。
- Gateway Pod 自动获取业务 IP。
- 外部通过业务 IP 访问 Gateway。
- Pod 异常重建后业务 IP 自动恢复。
- 变更网络平面后滚动升级，新业务 IP 生效。
- 节点故障或 Pod 漂移后业务 IP 随 Pod 迁移。

不纳入范围：

- DM 平台内部创建网络平面的具体实现。
- 容器网络插件 IPAM 内部算法。
- IAM 用户认证、JWT、ACL、API Key、资源鉴权。

## 3. 测试环境

- 标准 K8s 集群。
- `kubectl` 和 `helm` 可用。
- `package-gateway` 独立部署包可用。
- Gateway 基础路由测试后端已准备，例如 `whoami`。
- DM 已创建前端网络平面，并且 IP 池有可用业务 IP。
- Gateway Pod YAML 可增加平台要求的网络注解。
- Gateway data-plane 以 StatefulSet 或其他稳定身份形态运行，以支持保 IP。
- 集群至少有两个可调度节点。

## 4. 平台集成流程

该 SR 与平台网络能力相关，平台内部不写细粒度测试用例，只验证集成结果。

1. 在 DM 界面创建前端网络平面。
2. 在 Gateway 部署参数中指定前端网络平面标识。
3. 在 Gateway Pod YAML 中增加平台要求的网络注解。
4. Pod 启动后由平台自动分配业务 IP。
5. Gateway 使用业务 IP 对外提供入口。
6. Pod 重建、升级或节点故障时，平台按稳定身份恢复或漂移业务 IP。

## 5. 测试策略

1. 先完成 Gateway 基础安装和基础路由验证，确保业务 IP 测试失败时能排除普通路由问题。
2. BIP 测试只验证业务 IP 分配、绑定、可达和保持行为。
3. 每个场景都记录 Pod 名称、节点、业务 IP、网络平面标识和访问结果。
4. 对重建、升级、漂移场景，必须记录变更前后的业务 IP 对比。

## 6. 测试用例

| 用例编号 | 用例名称 | 前置条件 | 测试步骤 | 预期结果 |
|---|---|---|---|---|
| BIP-01 | 指定前端网络平面，Gateway 自动获取业务 IP 并对外可达 | DM 已创建前端网络平面；IP 池有可用 IP；Gateway 部署参数支持网络平面标识；Pod YAML 支持平台注解 | 1. 在 DM 创建前端网络平面并记录标识。<br>2. 部署 Gateway 时指定该网络平面。<br>3. 确认 Gateway Pod 启动并绑定业务 IP。<br>4. 创建基础路由。<br>5. 从外部访问 `http://<业务IP>/<测试路径>`。 | Gateway Pod 自动获取业务 IP；业务 IP 与 Pod 绑定；外部可通过业务 IP 访问 Gateway；请求可转发到后端。 |
| BIP-02 | Pod 异常重建：业务 IP 自动恢复 | BIP-01 已完成，Gateway 以 StatefulSet 或稳定身份运行 | 1. 记录 Gateway Pod 名称、所在节点、业务 IP。<br>2. 删除 Gateway Pod 或触发容器异常。<br>3. 等待 Pod 自动重建 Ready。<br>4. 再次查询业务 IP。<br>5. 通过原业务 IP 访问测试路径。 | Pod 重建后业务 IP 保持不变；原业务 IP 可继续访问；路由能力恢复正常。 |
| BIP-03 | 变更网络平面：滚动升级后新业务 IP 生效 | 已存在两个前端网络平面，且新网络平面 IP 池可用 | 1. 记录当前网络平面和业务 IP。<br>2. 修改 Gateway 部署参数为新网络平面标识。<br>3. 执行 Helm upgrade 或平台滚动升级。<br>4. 等待 Gateway Pod Ready。<br>5. 查询新业务 IP。<br>6. 通过新业务 IP 访问测试路径。 | 滚动升级完成；Gateway 绑定新网络平面业务 IP；新业务 IP 可访问；旧业务 IP 不再作为当前 Gateway 入口。 |
| BIP-04 | 节点故障漂移：业务 IP 随 Pod 迁移 | Gateway Pod 已绑定业务 IP；集群有至少两个可调度节点 | 1. 记录 Gateway Pod 所在节点和业务 IP。<br>2. 模拟节点故障、节点不可调度或驱逐 Pod。<br>3. 等待 Pod 调度到新节点并 Ready。<br>4. 查询业务 IP 绑定状态。<br>5. 通过原业务 IP 访问测试路径。 | Pod 迁移到新节点后业务 IP 保持不变；业务 IP 随 Pod 漂移；外部访问恢复，无需人工重新绑定。 |

## 7. 命令合集

以下命令是标准 K8s 示例。前端网络平面参数、Pod 注解、业务 IP 字段需要替换成实际平台字段。

### 7.1 通用变量

```bash
export GW_NS=aidp-iam
export EG_NS=envoy-gateway-system
export TEST_NS=test-backend
export RELEASE=aidp-gateway
export CHART=package-gateway/charts/aidp-gateway
export FRONTEND_PLANE_OLD=<frontend-plane-id-old>
export FRONTEND_PLANE_NEW=<frontend-plane-id-new>
export BUSINESS_IP=<gateway-business-ip>
export BUSINESS_URL=http://${BUSINESS_IP}
```

### 7.2 BIP-01 指定前端网络平面并验证业务 IP 可达

DM 侧流程：

1. 在 DM 界面创建前端网络平面。
2. 记录前端网络平面标识。
3. 确认前端网络平面 IP 池有可用 IP。

K8s 侧示例命令：

```bash
helm upgrade --install ${RELEASE} ${CHART} \
  --namespace ${GW_NS} --create-namespace \
  --set <frontendNetworkPlaneKey>=${FRONTEND_PLANE_OLD} \
  --wait --timeout=5m

kubectl -n ${EG_NS} get pods -o wide
kubectl -n ${EG_NS} get pod <gateway-pod-name> -o yaml | grep -i -E "business|frontend|network|ip"

kubectl apply -f package-gateway/test/whoami-test.yaml
kubectl -n ${TEST_NS} wait --for=condition=Available deployment/whoami --timeout=60s

curl -sS ${BUSINESS_URL}/whoami
```

### 7.3 BIP-02 Pod 异常重建后业务 IP 自动恢复

```bash
kubectl -n ${EG_NS} get pods -o wide
kubectl -n ${EG_NS} get pod <gateway-pod-name> -o yaml > /tmp/gateway-pod-before.yaml

kubectl -n ${EG_NS} delete pod <gateway-pod-name>
kubectl -n ${EG_NS} wait --for=condition=Ready pod -l gateway.envoyproxy.io/owning-gateway-name=eg --timeout=180s

kubectl -n ${EG_NS} get pods -o wide
kubectl -n ${EG_NS} get pod <new-gateway-pod-name> -o yaml > /tmp/gateway-pod-after.yaml

grep -i -E "business|frontend|network|ip" /tmp/gateway-pod-before.yaml
grep -i -E "business|frontend|network|ip" /tmp/gateway-pod-after.yaml

curl -sS ${BUSINESS_URL}/whoami
```

期望重建前后业务 IP 一致。

### 7.4 BIP-03 变更网络平面并滚动升级

```bash
kubectl -n ${EG_NS} get pods -o wide
kubectl -n ${EG_NS} get pod <gateway-pod-name> -o yaml > /tmp/gateway-pod-plane-old.yaml

helm upgrade --install ${RELEASE} ${CHART} \
  --namespace ${GW_NS} \
  --set <frontendNetworkPlaneKey>=${FRONTEND_PLANE_NEW} \
  --wait --timeout=10m

kubectl -n ${EG_NS} rollout status statefulset/<gateway-statefulset-name> --timeout=10m
kubectl -n ${EG_NS} get pods -o wide
kubectl -n ${EG_NS} get pod <gateway-pod-name> -o yaml > /tmp/gateway-pod-plane-new.yaml

grep -i -E "business|frontend|network|ip" /tmp/gateway-pod-plane-old.yaml
grep -i -E "business|frontend|network|ip" /tmp/gateway-pod-plane-new.yaml

export BUSINESS_IP_NEW=<new-gateway-business-ip>
curl -sS http://${BUSINESS_IP_NEW}/whoami
```

期望新网络平面对应的新业务 IP 生效。

### 7.5 BIP-04 节点故障漂移

非破坏性模拟方式优先使用 `cordon + drain`，实际故障演练需遵循集群变更规范。

```bash
kubectl -n ${EG_NS} get pod <gateway-pod-name> -o wide
export OLD_NODE=<gateway-pod-node>

kubectl cordon ${OLD_NODE}
kubectl drain ${OLD_NODE} --ignore-daemonsets --delete-emptydir-data

kubectl -n ${EG_NS} wait --for=condition=Ready pod -l gateway.envoyproxy.io/owning-gateway-name=eg --timeout=300s
kubectl -n ${EG_NS} get pods -o wide
kubectl -n ${EG_NS} get pod <new-gateway-pod-name> -o yaml | grep -i -E "business|frontend|network|ip"

curl -sS ${BUSINESS_URL}/whoami

kubectl uncordon ${OLD_NODE}
```

期望 Pod 迁移到其他节点后，原业务 IP 仍可访问。

## 8. 记录模板

| 用例编号 | 执行人 | 执行时间 | 结果 | 业务 IP 变化 | 证据 | 备注 |
|---|---|---|---|---|---|---|
| BIP-01 |  |  | Pass/Fail |  |  |  |
| BIP-02 |  |  | Pass/Fail |  |  |  |
| BIP-03 |  |  | Pass/Fail |  |  |  |
| BIP-04 |  |  | Pass/Fail |  |  |  |
