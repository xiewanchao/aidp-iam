# AIDP IAM

Single-tenant Identity & Access Management system. Keycloak for federated
authentication (SAML/OIDC + local), OPA for path-level authorization, custom
resource-ACL layer for per-instance permissions, all fronted by a unified
Envoy Gateway.

## Repository Structure

```
aidp-iam/
├── da-cluster/            部署系统（Helm charts / 脚本 / 离线包 / 文档）
│   ├── charts/aidp-iam/     Umbrella Helm chart（一次部署全部）
│   ├── scripts/             setup / cleanup / rebuild / test / build-release-images
│   ├── gateway-routes/      HTTPRoutes / SecurityPolicy / EnvoyExtensionPolicy
│   ├── images/              自定义镜像 Dockerfile
│   └── offline/             Gateway API CRDs + 子 chart 包
├── da-idb-proxy/          keycloak-proxy：IAM 管理 API（用户/组/应用/API Key/IdP/Token）
├── opal-dynamic-policy/
│   ├── pep-proxy/           ext_authz gRPC，路径级+资源级鉴权
│   └── bundle-server/       从 DB 生成 OPA Rego bundle
├── resource-sync/         ext_proc gRPC，自动同步 resource_acl
├── mock-kb/               模拟知识库业务后端（40+ 端点）
├── mock-rubik/            模拟智能问数业务后端（50+ 端点）
└── diagrams/              设计文档 + API 规格 Excel
```

## 架构一览

```
Client  ──HTTPS──▶  Envoy Gateway (:80)
                     ├── ext_authz gRPC ─▶ pep-proxy ─▶ OPA（path_rules）
                     │                              └─▶ Postgres（resource_acl）
                     ├── ext_proc  gRPC ─▶ resource-sync ─▶ Postgres（ACL 自动同步 / 注入 X-Allowed-Ids）
                     └── HTTPRoute   ───▶ 业务后端（keycloak / keycloak-proxy / mock-kb / mock-rubik / ...）
```

- **Default Deny 策略**：未显式命中 `path_rule` 的一律 403
- **纯 DB 驱动**：`path_rules + path_rule_groups` 多对多表达全部授权，无代码旁路
- **Method 级鉴权**：`path_rules.method` 可精确到 GET/POST/PUT/DELETE
- **资源 ID 灵活提取**：path / query / body 三种模式由 `resource_patterns.id_source` 决定

---

## 离线/生产部署

### 1. 下载镜像包

去 [GitHub Releases](https://github.com/xiewanchao/aidp-iam/releases) 下载对应架构：

- `aidp-iam-images-amd64.tar.gz`（约 3 GB）
- `aidp-iam-images-arm64.tar.gz`（约 3 GB）

每个 tar 包含全部 13 个镜像（7 个自定义 + 6 个第三方 + Kind node），可完全离线部署，不需要任何外网。

### 2. 解压到集群机器

```bash
tar xzf aidp-iam-images-amd64.tar.gz   # 解压到 amd64/
cd aidp-iam
mv ../amd64 da-cluster/offline/images/amd64
# arm64 同理
```

### 3. 生产部署

```bash
# 镜像已提前导入每个节点后，直接安装两个 Helm 包
helm install aidp-gateway aidp-gateway-1.7.2.tgz \
  --namespace aidp-iam --create-namespace \
  --wait --timeout=10m

helm install aidp-iam aidp-iam-1.3.0.tgz \
  --namespace aidp-iam --create-namespace \
  --wait --timeout=12m \
  --set keycloak.keycloak.config.hostname=http://<EIP-or-DNS>:30080
```

`aidp-gateway` 包内自带顶层 `crds/` 和内嵌的 `gateway-helm` 子 chart，
不需要生产服务器额外执行 `kubectl apply crds/` 或 `helm dependency build`。

开发/测试环境仍可用：

```bash
cd da-cluster
./scripts/setup.sh
./scripts/test.sh
```

---

## 开发/本地构建

如果你要修改 IAM 代码并本地构建镜像，在**仓库根目录**执行：

```bash
docker build -f da-cluster/images/aidp-iam-app/Dockerfile -t aidp-iam-app:v1 .
```

这个 Dockerfile 会直接从当前仓库上下文复制 `da-idb-proxy/`、`opal-dynamic-policy/`、`resource-sync/`
等源码目录，不需要 `setup.sh` 再临时拷贝代码。

其他自定义镜像：

```bash
# Keycloak 自定义镜像包含 mapper/theme/CAS provider
docker build -t keycloak-custom:26.5.2 da-cluster/images/keycloak-custom

# 初始化镜像
docker build -t keycloak-init:v2 da-cluster/images/keycloak-init
```

本地 kind 快速迭代 `aidp-iam-app`：

```bash
cd da-cluster
./scripts/rebuild.sh app
```

清理：

```bash
cd da-cluster
./scripts/cleanup.sh
```

### 访问 Gateway

Kind 模式：
```bash
EG_SVC=$(kubectl -n aidp-iam get svc -l gateway.envoyproxy.io/owning-gateway-name=eg -o jsonpath='{.items[0].metadata.name}')
kubectl -n aidp-iam port-forward svc/$EG_SVC 8080:80 &

curl http://localhost:8080/realms/aidp/.well-known/openid-configuration
```

默认账户（仅用于测试环境）：
- `admin / Admin@123` — 系统管理员
- `normal-user / NormalUser@123` — 普通用户

---

## 多架构镜像打包

维护者发布新版本时使用，**不污染本地 Docker daemon**：

```bash
cd da-cluster

# 同时打 amd64 + arm64，输出到 /c/tmp/aidp-iam-release-images/
# （Windows 路径含空格会自动改到 /c/tmp/）
./scripts/build-release-images.sh

# 或只打一个平台
./scripts/build-release-images.sh --arch amd64
./scripts/build-release-images.sh --arch arm64
```

原理：
- 自定义镜像走 `docker buildx build --output type=docker,dest=*.tar`，直接写文件
- 第三方镜像走 `skopeo copy` 运行在容器里，从 registry 直接拉到 tar
- 全程不触碰本地 Docker daemon，不会覆盖本地已有 tag

打包好之后：
```bash
cd /c/tmp/aidp-iam-release-images
tar czf aidp-iam-images-amd64.tar.gz amd64/
tar czf aidp-iam-images-arm64.tar.gz arm64/
gh release create v1.0.0 aidp-iam-images-*.tar.gz
```

---

## 核心文档

- [`diagrams/overview-design.md`](diagrams/overview-design.md) — 架构总览
- [`diagrams/data-storage.md`](diagrams/data-storage.md) — 数据库表设计（8 张表）
- [`diagrams/request-flow.md`](diagrams/request-flow.md) — 请求全链路
- [`diagrams/app-integration-guide.md`](diagrams/app-integration-guide.md) — 新业务接入指南
- [`diagrams/api-spec-v2.xlsx`](diagrams/api-spec-v2.xlsx) — 完整 API 清单（51 APIs + 34 path_rules）
- [`diagrams/rubik-auth-design.md`](diagrams/rubik-auth-design.md) — RubikSQL 接入实例
- [`da-cluster/README.md`](da-cluster/README.md) — 部署细节

---

## 许可 / 贡献

这是 AIDP 平台内部组件。Issue 和 PR 请在本仓库提。
