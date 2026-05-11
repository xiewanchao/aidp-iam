# aidp-iam 独立部署包 (v1.4)

IAM 业务栈：Keycloak + 合并版 IAM 服务 + OPA + IAM 自身路由（Keycloak / 身份 API / ACL API / path-rules）。

**前提**：先装 [package-gateway](../package-gateway/) —— 本包不包含 Gateway 控制器和 CRD，依赖 Gateway 的 `eg` Gateway 资源已存在于 `aidp-gateway` 命名空间。

## v1.4 关键变化

把原本 4 个 Python 服务（keycloak-proxy / pep-proxy / bundle-server / resource-sync）合成一个 `aidp-iam-app:v1` 镜像，由 supervisord 统一守护；OPAL Server / Client 整套删掉，只留一个干净的 `openpolicyagent/opa:0.70.0-static`。所有 IAM 服务都跑在同一个 `aidp-iam` 命名空间的同一个 Pod（2 容器）里 —— 单节点 5 个 Pod、双节点 9 个 Pod。

```
package-iam/
├── README.md
├── charts/
│   └── aidp-iam/                                     IAM Helm chart
│       ├── Chart.yaml
│       ├── values.yaml
│       ├── charts/
│       │   ├── keycloak/                             Keycloak StatefulSet + Postgres + 跨 ns 写 Secret 的 init Job
│       │   └── iam-app/                              iam-services Deployment（aidp-iam-app + opa）+ 5 个 Service + RBAC
│       └── templates/                                IAM 自身路由
│           ├── namespaces.yaml                       keycloak / aidp-iam
│           ├── reference-grants.yaml                 2 条跨 ns 引用授权
│           ├── routes-public.yaml                    /realms / /admin / /resources
│           ├── routes-protected.yaml                 /api/v1/* / /acl/v1/*
│           └── security-policy.yaml                  ext_authz 绑 pep-proxy
└── images/<arch>/                                    镜像 tar
    ├── aidp-iam-app_v1.tar                           4-in-1 合并镜像
    ├── keycloak-init_v2.tar
    ├── keycloak-custom_26.5.2.tar
    ├── postgres_17.tar
    └── openpolicyagent_opa_0.70.0-static.tar
```

---

## 部署

### 1. 加载镜像（每个节点）

```bash
# iSulad
for tar in package-iam/images/<arch>/*.tar; do isula load -i "$tar"; done

# containerd
for tar in package-iam/images/<arch>/*.tar; do ctr -n k8s.io images import "$tar"; done

# Docker
for tar in package-iam/images/<arch>/*.tar; do docker load -i "$tar"; done
```

### 2. 确认镜像 tag

Chart 直接引用干净 tag。生产服务器只要提前把这些 tag 导入到每个节点的
容器运行时即可，不需要 arch 后缀 alias：

```text
aidp-iam-app:v1
keycloak-init:v2
keycloak-custom:26.5.2
postgres:17
openpolicyagent/opa:0.70.0-static
```

> `rancher/kubectl` 不再需要；`wait-for-secret` initContainer 复用 `aidp-iam-app:v1` 访问 Kubernetes API。旧版拆分镜像 `keycloak-proxy:v3`、`opal-proxy:v2`、`resource-sync:v1`、`permitio/opal-*` 也不应再放入本包。

### 3. helm install

```bash
helm install aidp-iam aidp-iam-1.3.0.tgz \
  --namespace aidp-iam --create-namespace \
  --wait --timeout=12m \
  --set keycloak.keycloak.config.hostname=http://<EIP>:30080
```

源码目录部署时也可以直接指向 chart 目录：

```bash
helm install aidp-iam package-iam/charts/aidp-iam \
  --namespace aidp-iam --create-namespace \
  --wait --timeout=12m \
  --set keycloak.keycloak.config.hostname=http://<EIP>:30080
```

集群没有默认 StorageClass 时加上：

```bash
  --set keycloak.postgres.persistence.storageClass=dorado-inner-nas
```

> `keycloak.keycloak.config.hostname` 两层 `keycloak` 是因为第一层是子 chart 名（umbrella 里的依赖名），第二层是子 chart 内部的 `keycloak:` 顶级键。

单节点开发环境建议额外加：

```bash
  --set keycloak.keycloak.replicas=1 \
  --set iam-app.replicas=1 \
  --set iam-app.podAntiAffinity.enabled=false
```

---

## 开发镜像构建

修改 IAM 代码后可以直接从仓库根目录 build，不再需要 `setup.sh` 临时拷贝源码。
注意这个命令必须在仓库根目录执行，因为 Docker build context 要包含
`da-idb-proxy/`、`opal-dynamic-policy/`、`resource-sync/` 等源码目录：

```bash
docker build -f da-cluster/images/aidp-iam-app/Dockerfile -t aidp-iam-app:v1 .
```

build 完后把新镜像推到 registry 或导入生产节点，再执行 `helm upgrade`。

Keycloak 自定义镜像包含 mapper、theme 和 CAS provider：

```bash
docker build -t keycloak-custom:26.5.2 da-cluster/images/keycloak-custom
```

初始化镜像：

```bash
docker build -t keycloak-init:v2 da-cluster/images/keycloak-init
```

### 离线镜像打包

离线包只需要当前 chart 实际引用的 5 个运行时镜像。直接用各自 Dockerfile 构建并导出 tar，不再额外维护打包脚本：

```bash
mkdir -p package-iam/images/arm64

docker buildx build --platform linux/arm64 \
  -f da-cluster/images/aidp-iam-app/Dockerfile \
  -t aidp-iam-app:v1 \
  --output type=docker,dest=package-iam/images/arm64/aidp-iam-app_v1.tar \
  .

docker buildx build --platform linux/arm64 \
  -f da-cluster/images/keycloak-custom/Dockerfile \
  -t keycloak-custom:26.5.2 \
  --output type=docker,dest=package-iam/images/arm64/keycloak-custom_26.5.2.tar \
  da-cluster/images/keycloak-custom

docker buildx build --platform linux/arm64 \
  -f da-cluster/images/keycloak-init/Dockerfile \
  -t keycloak-init:v2 \
  --output type=docker,dest=package-iam/images/arm64/keycloak-init_v2.tar \
  da-cluster/images/keycloak-init

docker pull --platform linux/arm64 postgres:17
docker save --platform linux/arm64 -o package-iam/images/arm64/postgres_17.tar postgres:17

docker pull --platform linux/arm64 openpolicyagent/opa:0.70.0-static
docker save --platform linux/arm64 -o package-iam/images/arm64/openpolicyagent_opa_0.70.0-static.tar openpolicyagent/opa:0.70.0-static
```

旧版拆分镜像 `keycloak-proxy:v3`、`opal-proxy:v2`、`resource-sync:v1`、`permitio/opal-*` 不再打包。

---

## 验证

```bash
# 1. Pod 全 Ready
kubectl get pods -n keycloak -n aidp-iam

# 2. Keycloak 通过 Gateway 可达
curl http://<节点 IP>:30080/realms/aidp/.well-known/openid-configuration | head -c 200

# 3. 拿一个 admin token
SECRET=$(kubectl -n aidp-iam get secret keycloak-aidp-client \
  -o go-template='{{`{{`}}index .data "client-secret" | base64decode{{`}}`}}')
curl -X POST http://<节点 IP>:30080/realms/aidp/protocol/openid-connect/token \
  -d "grant_type=password&client_id=aidp-client&client_secret=$SECRET&username=admin&password=Admin@123" \
  | python -c "import sys,json; print(json.load(sys.stdin)['access_token'][:50])"
```

---

## 内置数据

`keycloak-init` Job 在首次部署时**幂等**写入：

| 表 / 资源 | 内容 |
|---------|------|
| Keycloak realms | `master`（默认）+ `aidp`（业务 realm） |
| Keycloak groups | `admins` / `all-users` / `{kb,rubik,memory}-admins` |
| Keycloak clients | `aidp-client`（业务用） + 各应用 OIDC client |
| `apps` 表 | `knowledgebase` / `rubik` / `memory` 三个预置应用 |
| `resource_patterns` / `resource_actions` | 三个应用的 ID 提取与动作分类配置 |
| `path_rules` + `permission_groups` | 管理路径白名单 |
| K8s Secret `keycloak-aidp-client` | 写到 **aidp-iam** ns 给 iam-services 读取 |

重复执行 `helm upgrade` 不覆盖人工修改的字段。新业务接入需要的 `POST /api/v1/apps` 仅用于「**预置之外**的全新应用」。

---

## 装完之后能干嘛

- ✅ Keycloak 登录 / 用户管理 / OIDC 已经可用
- ✅ IAM 控制面 API（`/api/v1/*`、`/acl/v1/*`、`/api/v1/path-rules`）已经可用
- ✅ pep-proxy 已就绪，能被业务的 SecurityPolicy 引用做 ext_authz（`pep-proxy.aidp-iam.svc:9000`）
- ✅ resource-sync 已就绪，能被业务的 EnvoyExtensionPolicy 引用做 ext_proc（`resource-sync.aidp-iam.svc:8082`）
- ❌ **没装任何业务后端** —— 真要看到端到端流程要加装 [mocks/package-mock-kb/](../mocks/package-mock-kb/)（KB 业务样例）

---

## 清理

```bash
helm uninstall aidp-iam -n aidp-iam
kubectl delete ns aidp-iam keycloak --ignore-not-found
```

---

## 跟主包关系

| 包 | 作用 | 是否依赖此包 |
|----|------|-----------|
| `package-gateway/` | 网关基建 + CRD | ❌ 它先装，IAM 装在它之上 |
| `package-iam/`（本包） | IAM 业务栈 | — |
| `mocks/package-mock-kb/` | KB 业务样例 | ✅ 依赖本包提供的 pep-proxy / resource-sync |
| `mocks/package-mock-rubik/` | Rubik 业务样例 | ✅ 同上 |
| `mocks/package-mock-memory/` | Memory 业务样例 | ✅ 同上 |
