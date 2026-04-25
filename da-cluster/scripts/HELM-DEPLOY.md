# 一键 Helm 部署 — `charts/aidp-iam` umbrella

把整个 IAM 栈（Envoy Gateway + Keycloak + OPA + resource-sync + 路由 + mock 后端）打包成**单个 Helm release**，一条 `helm uninstall` 就能清理干净。

`setup-isula.sh` / `setup.sh` 仍然可用，两条路径互相独立。本文档适用于：纯 Helm、GitOps（ArgoCD / Flux）、已有 K8s 集群。

---

## 先确认你的集群类型

| 类型 | 推荐路径 | 说明 |
|------|---------|------|
| **本地 Kind** | `./scripts/setup.sh`（自动） | Kind 节点是 Docker 容器，host 的 `isula`/`ctr` 进不去；用 setup.sh 一键解决。要硬走 umbrella 见末尾【Kind 走 umbrella 路径】 |
| **华为 K8s + iSulad** | 本文档（4 步） | `load-images.sh` 自动走 `isula` |
| **一般 K8s + containerd** | 本文档（4 步） | `load-images.sh` 自动 fallback 到 `ctr` |
| **没装 K8s** | 先建集群再回来 | kubeadm / k3s / 华为 CCE 任选 |

---

## 四步部署

```bash
cd da-cluster

# ── 1. 加载镜像（每个节点的容器运行时）
./scripts/load-images.sh --with-mocks

# ── 2. 刷 umbrella subchart
#     charts/aidp-iam/charts/ 是 gitignored 的，必须从顶层 charts/ 打包进来
./scripts/package-umbrella.sh

# ── 3. 装 stripped 版 CRD
#     避开 helm 自带的完整版（> 500 KB 可能被 API server 拒）
./scripts/install-crds.sh

# ── 4. 一键 helm install
helm upgrade -i aidp-iam charts/aidp-iam \
  --namespace aidp-iam --create-namespace \
  --skip-crds --wait --timeout=10m \
  --set keycloak.keycloak.config.hostname=http://141.112.135.70:30080
```

**两个关键 flag**：
- `--skip-crds` 让 helm 别去 apply 自带的完整版 CRD（已经在第 3 步装了压缩版）
- `--set keycloak.postgres.persistence.storageClass=...` **按需**：集群有标 default 的 SC（Kind 自带 `standard`）就不用传；华为集群通常**没有**默认 SC，要传具体名字（例如 `dorado-inner-nas`）。先 `kubectl get sc` 看一下

---

## 多节点 K8s

镜像必须每个节点都有一份。`load-images.sh` 通过 SSH 分发：

```bash
K8S_NODES="141.112.135.70 141.112.135.71" \
  ./scripts/load-images.sh --with-mocks
```

后面三步（package-umbrella / install-crds / helm install）只需在**一个能连 K8s API server 的节点**上跑即可。

---

## 配置示例（写进 my-values.yaml）

```yaml
keycloak:
  keycloak:
    config:
      hostname: http://141.112.135.70:30080
  postgres:
    persistence:
      storageClass: dorado-inner-nas

# 生产环境关 mock
mocks:
  kb:     { enabled: false }
  rubik:  { enabled: false }
  memory: { enabled: false }

# 改默认 NodePort
envoy-gateway:
  proxy:
    service:
      nodePort: 32080
```

```bash
helm upgrade -i aidp-iam charts/aidp-iam \
  --namespace aidp-iam --create-namespace \
  --skip-crds --wait --timeout=10m \
  -f my-values.yaml
```

> **关于 `--set keycloak.keycloak.config.hostname=...` 的两层 keycloak**：第一层是 umbrella 里的子 chart 名，第二层是子 chart 内部的 `keycloak:` 顶级键。同理 `keycloak.postgres.persistence.storageClass`。嫌长就用 `-f my-values.yaml`。

---

## 验证

```bash
# 1. Pod 全部 Ready
kubectl get pods -A | grep -E "keycloak|opa|resource-sync|envoy-gateway|mock-"

# 2. Gateway Service 必须是 NodePort 30080
kubectl -n aidp-iam get svc -l gateway.envoyproxy.io/owning-gateway-name=eg
# 期望 PORT(S) 列出现 80:30080/TCP

# 3. 外部访问（EIP 已绑节点 + 安全组放行 30080）
curl -sSf http://141.112.135.70:30080/realms/master/.well-known/openid-configuration | head -c 200
```

---

## 清理

```bash
helm uninstall aidp-iam -n aidp-iam
kubectl delete ns aidp-iam keycloak opa resource-sync mock-kb mock-rubik mock-memory envoy-gateway-system

# CRD 是集群级资源，helm uninstall 不动
kubectl delete crd -l gateway.envoyproxy.io/crd=true
kubectl delete crd -l gateway.networking.k8s.io/bundle-version=
```

---

## Kind 走 umbrella 路径（不推荐）

Kind 节点 = Docker 容器，host 的 `isula` / `ctr` 操作不到里面的 containerd。`load-images.sh` 在 Kind 上跑会失败。要走 umbrella 必须手动：

```bash
CLUSTER=kind          # 你的 Kind 集群名
ARCH=amd64            # 或 arm64

# 1. 加载镜像（per tar，kind 自己的命令）
for tar in offline/images/$ARCH/*.tar; do
  kind load image-archive "$tar" --name "$CLUSTER"
done

# 2. Alias arch-suffixed tag（要 docker exec 进 Kind 节点容器）
NODE="${CLUSTER}-control-plane"
for img in keycloak-proxy:v3 opal-proxy:v2 keycloak-init:v2 resource-sync:v1 \
           mock-kb:v1 mock-rubik:v1 mock-memory:v1; do
  docker exec "$NODE" ctr -n k8s.io images tag \
    "docker.io/library/${img}-${ARCH}" "docker.io/library/${img}" 2>/dev/null || true
done

# 3-4. package-umbrella / install-crds / helm install 同前
```

更简单的方式：直接用 `./scripts/setup.sh`，已经把 Kind 起集群 + 加载镜像 + 部署一条龙做完。

---

## 常见问题

### 外部 `http://<EIP>:30080/` 连不通
1. `kubectl -n aidp-iam get svc ...` Service 必须是 `NodePort`、PORT(S) 含 `30080`
2. 云平台安全组 / 网络 ACL 放行 TCP 30080
3. EIP 必须绑到**有 Envoy data-plane pod 的节点**（多节点检查 pod 调度）

### `helm install` 卡在等 Keycloak
Keycloak 首次启动要初始化 DB schema，慢机器 3-5 分钟正常：

```bash
kubectl -n keycloak logs -f statefulset/keycloak --tail=50
kubectl -n keycloak get job keycloak-init -w
```

### `mock-*` Pod ImagePullBackOff
镜像没 alias 到 clean tag。重跑：

```bash
./scripts/load-images.sh --alias-only --with-mocks
```

### 想切回 `setup-isula.sh`
两种方式互相独立。如果之前是 `setup-isula.sh` 装的（5 个独立 release），切 umbrella 要先清理：

```bash
helm uninstall eg envoy-gateway-proxy -n aidp-iam
helm uninstall keycloak -n keycloak
helm uninstall opa -n opa
helm uninstall resource-sync -n resource-sync
# 然后回到【四步部署】
```

---

## 与 `setup-isula.sh` 的差异

| 维度 | `setup-isula.sh` | Helm umbrella |
|------|------------------|---------------|
| 镜像加载 | 脚本内置 Step 2 | 单独走 `load-images.sh` |
| CRD 安装 | 脚本内置 Step 3 | 单独走 `install-crds.sh` |
| 部署方式 | 5 个独立 helm release + raw kubectl apply | 1 个 umbrella release |
| Keycloak init-job 时序 | 脚本显式 `rollout restart keycloak-proxy` 让它拿 client secret | 依赖 Deployment CrashLoop 自愈（首次启动多慢几十秒） |
| 配置入口 | 环境变量（KC_HOSTNAME、STORAGE_CLASS） | `--set` / `-f values.yaml` |
| 清理 | `cleanup.sh` | `helm uninstall` |

**日常华为现场**：继续 `setup-isula.sh`。
**CI / GitOps**：用 umbrella。
