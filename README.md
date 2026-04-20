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

## 离线部署

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

### 3. 部署

```bash
cd da-cluster

# 开发/测试环境（创建新的 Kind 集群）
./scripts/setup.sh

# 生产环境（部署到已有 K8s 集群）
./scripts/setup.sh --no-kind

# 运行完整测试（94 项端到端测试）
./scripts/test.sh
```

整个部署流程约 10-12 分钟，完成后：
- Envoy Gateway 在 `aidp-iam` namespace
- Keycloak（postgres + keycloak-proxy）在 `keycloak`
- OPA（pep-proxy + opal-server）在 `opa`
- resource-sync 在 `resource-sync`
- mock-kb / mock-rubik 在各自 namespace

---

## 开发/本地构建

如果你要修改代码并本地构建镜像：

```bash
cd da-cluster

# 从源码构建所有自定义镜像，创建 Kind 集群部署
./scripts/setup.sh --build

# 只重建某个组件（快速迭代）
./scripts/rebuild.sh opa        # 重建 opal-proxy（pep-proxy + bundle-server）
./scripts/rebuild.sh proxy      # 重建 keycloak-proxy
./scripts/rebuild.sh rs         # 重建 resource-sync

# 清理
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
