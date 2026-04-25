# 一键 Helm 部署 — `charts/aidp-iam` umbrella

把整个 IAM 栈（Envoy Gateway controller + data plane、Keycloak + Postgres、OPA/OPAL + pep-proxy、resource-sync、gateway routes、mock 后端）打包成**单个 Helm release**，用一条 `helm install` 部署、一条 `helm uninstall` 清理。

适合纯 Helm / GitOps 场景。不想折腾脚本的可以用这条路径。`setup-isula.sh` 仍然可用，两者不冲突。

---

## 前提

1. `kubectl` 可以连上目标集群（`kubectl cluster-info` 正常返回）
2. `helm 3.x` 可用
3. **所有节点**上的容器运行时里已经 load 好这些镜像（tag 必须是**无 arch 后缀的干净形式**）：

   | 镜像 | tag |
   |------|-----|
   | keycloak-proxy | v3 |
   | opal-proxy | v2 |
   | keycloak-init | v2 |
   | resource-sync | v1 |
   | keycloak-custom | 26.5.2 |
   | postgres | 17 |
   | envoyproxy/gateway | v1.7.0 |
   | envoyproxy/envoy | distroless-v1.37.0 |
   | permitio/opal-server | 0.7.4 |
   | permitio/opal-client | 0.7.4 |
   | mock-kb / mock-rubik / mock-memory（可选）| v1 |

   如果镜像只有 `:<tag>-arm64` 后缀（从离线 tar 装出来），先做一次 alias：

   ```bash
   for img in keycloak-proxy:v3 opal-proxy:v2 keycloak-init:v2 resource-sync:v1 \
              mock-kb:v1 mock-rubik:v1 mock-memory:v1; do
     isula tag docker.io/library/${img}-arm64 docker.io/library/${img}
   done
   ```

4. **CRD 必须先用 `offline/crds/` 里的"压缩版"装好**。umbrella 的 `gateway-helm` 子 chart 自带的是**完整原版** CRD（带 description，可能 > 500 KB），部分 K8s API server 会拒绝。用压缩版手动 apply，再让 helm 跳过自带 CRD：

   ```bash
   kubectl apply --server-side --force-conflicts \
     -f offline/crds/gateway-api-v1.4.1-experimental.yaml
   for f in offline/crds/gateway.envoyproxy.io_*.yaml; do
     kubectl apply --server-side --force-conflicts -f "$f"
   done
   ```

   后面 `helm install` 一定要带 `--skip-crds`（详见部署命令），让 Helm 不要再去碰 CRD。

---

## 部署

### 第一步：刷新 umbrella 的子 chart 副本

`charts/aidp-iam/charts/` 是 `.gitignore` 的，需要从顶层 `charts/envoy-gateway/`、`charts/keycloak/`、`charts/opa/`、`charts/resource-sync/` 打包进来，否则 Gitee 拉下来的仓库里 umbrella 根本没子 chart。

```bash
cd da-cluster
./scripts/package-umbrella.sh
```

> 任何一次顶层 `charts/<name>/` 改动（包括 `values.yaml` 改参数）后都要重跑这一步，再 `helm upgrade`。

### 第二步：一键 helm install

```bash
helm upgrade -i aidp-iam charts/aidp-iam \
  --namespace aidp-iam --create-namespace \
  --skip-crds \
  --wait --timeout=10m \
  --set keycloak.keycloak.config.hostname=http://141.112.135.70:30080 \
  --set keycloak.postgres.persistence.storageClass=dorado-inner-nas
```

> `--skip-crds` 强制让 Helm 不要应用子 chart 里自带的完整版 CRD —— 集群里用的是【前提】第 4 步 kubectl apply 的压缩版。
>
> `--set keycloak.keycloak.config.hostname=...` 有两层 `keycloak` 是因为第一层是 umbrella 里的子 chart 名，第二层是子 chart 内部的 `keycloak:` 顶级键。同理 `--set keycloak.postgres.persistence.storageClass=...`。嫌长可以写进 `my-values.yaml` 然后 `-f my-values.yaml`。
>
> `--set keycloak.postgres.persistence.storageClass=...` 是按需的：集群有标 default 的 SC（Kind 自带 `standard`）就不用传；华为集群通常没有默认 SC，要传具体名字（例如 `dorado-inner-nas`）。`kubectl get sc` 看一下。

典型的 `my-values.yaml` 示例：

```yaml
keycloak:
  keycloak:
    config:
      hostname: http://141.112.135.70:30080
  postgres:
    persistence:
      storageClass: dorado-inner-nas

# 要关 mock 后端（生产环境）
mocks:
  kb:
    enabled: false
  rubik:
    enabled: false
  memory:
    enabled: false

# 要换 NodePort
envoy-gateway:
  proxy:
    service:
      nodePort: 32080
```

---

## 验证

```bash
# 1. Pod 全部 Ready
kubectl get pods -A | grep -E "keycloak|opa|resource-sync|envoy-gateway|mock-"

# 2. Gateway Service 应该是 NodePort 30080
kubectl -n aidp-iam get svc -l gateway.envoyproxy.io/owning-gateway-name=eg
# 期望输出里 PORT(S) 列出现 80:30080/TCP

# 3. 外部访问测试（EIP 已绑到节点 + 安全组放行 30080）
curl -sSf http://141.112.135.70:30080/realms/master/.well-known/openid-configuration | head -c 200
```

---

## 清理

```bash
helm uninstall aidp-iam -n aidp-iam
kubectl delete ns aidp-iam keycloak opa resource-sync mock-kb mock-rubik mock-memory envoy-gateway-system
```

CRD 是集群级资源，`helm uninstall` 不会删，要手动：

```bash
kubectl delete crd -l gateway.envoyproxy.io/crd=true
kubectl delete crd -l gateway.networking.k8s.io/bundle-version=
```

---

## 常见问题

### 外部 `http://<EIP>:30080/` 连不通
1. `kubectl get svc -n aidp-iam -l gateway.envoyproxy.io/owning-gateway-name=eg` — Service 必须是 `NodePort`、包含 `30080`
2. 云平台安全组 / 网络 ACL 必须放行入站 TCP 30080
3. EIP 必须已绑到**有 Envoy data-plane pod 的节点**（多节点场景下检查 pod 调度位置）

### `helm install` 卡在等待 Keycloak
Keycloak 首次启动要初始化 DB schema，慢机器上 3-5 分钟正常。`--timeout=10m` 给足时间。要看进度：

```bash
kubectl -n keycloak logs -f statefulset/keycloak --tail=50
kubectl -n keycloak get job keycloak-init -w
```

### `mock-*` 启动失败 ImagePullBackOff
镜像没 alias 到干净 tag，回到【前提】第 3 步。

### 想切回 `setup-isula.sh`
两种方式互相独立。如果之前是 `setup-isula.sh` 装的（5 个独立 release），切 umbrella 要先清理：

```bash
helm uninstall eg envoy-gateway-proxy -n aidp-iam
helm uninstall keycloak -n keycloak
helm uninstall opa -n opa
helm uninstall resource-sync -n resource-sync
# 再 helm install 新的 umbrella
```

---

## 与 `setup-isula.sh` 的差异

| 维度 | `setup-isula.sh` | Helm umbrella |
|------|------------------|---------------|
| 镜像加载 | 脚本自动 `isula load` 离线 tar | 运维手动处理 |
| CRD 安装 | 脚本 `kubectl apply` | 运维手动 / 脚本可选 |
| 部署方式 | 5 个独立 helm release + kubectl apply | 1 个 helm release |
| Keycloak init-job 依赖 | 脚本显式 `rollout restart keycloak-proxy` 让它拿到 client secret | 依赖 Deployment 自动 CrashLoop 重试（首次启动可能多慢几十秒） |
| 配置入口 | 环境变量（KC_HOSTNAME、STORAGE_CLASS） | `--set` 或 `-f values.yaml` |
| 清理 | `cleanup.sh` | `helm uninstall` |

日常开发 + 华为集群现场：继续用 `setup-isula.sh`。
CI / GitOps（ArgoCD、Flux）：用 umbrella。
