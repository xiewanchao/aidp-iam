# 最小文件清单

版本：v1.0
日期：2026-05-09
说明：基于4合1构建方式（`aidp-iam-app:v1`），列出运行本项目所需的最小文件集合。与 [image-build-manifest.md](./image-build-manifest.md) 配合阅读。

---

## 一、4个服务源码

这4个目录的内容最终被 `rebuild.sh` 组装进 `aidp-iam-app:v1` 镜像。

| 路径 | 映射到镜像内 | 说明 |
|---|---|---|
| `da-idb-proxy/app/` | `/app/keycloak_proxy/` | keycloak-proxy：租户/用户/组/应用/API Key 管理 API（端口 8090） |
| `opal-dynamic-policy/pep-proxy/app/` | `/app/pep_proxy/` | pep-proxy：JWT/API-Key 验证 + OPA 路径鉴权 + 资源级 ACL（端口 8000/9000） |
| `opal-dynamic-policy/pep-proxy/proto/ext_authz.proto` | 编译为 `/app/pep_proxy/` | Envoy ext_authz gRPC 协议定义 |
| `opal-dynamic-policy/bundle-server/app/` | `/app/bundle_server/` | bundle-server：从 DB 生成 OPA Rego bundle，每 30s 轮询推送到 OPA（端口 8001） |
| `resource-sync/app/` | `/app/resource_sync/` | resource-sync：ext_proc 监听 + ACL 自动写入（端口 8080/8082） |
| `resource-sync/proto/ext_proc.proto` | 编译为 `/app/resource_sync/` | Envoy ext_proc gRPC 协议定义 |

---

## 二、4合1镜像构建文件

| 路径 | 说明 |
|---|---|
| `da-cluster/images/aidp-iam-app/Dockerfile` | 主镜像构建文件，基于 `python:3.11-slim`，编译 proto、烟雾测试、暴露6个端口 |
| `da-cluster/images/aidp-iam-app/supervisord.conf` | 4个服务的进程管理配置，自动启动/重启，日志到 `/var/log/supervisor/` |
| `da-cluster/images/aidp-iam-app/requirements.txt` | 4个服务合并后的 Python 依赖清单 |

---

## 三、辅助镜像

| 路径 | 镜像名 | 说明 |
|---|---|---|
| `da-cluster/images/keycloak-init/` | `keycloak-init:v2` | 一次性初始化 Job：创建 realm、groups、初始用户、apps 种子数据 |
| `da-cluster/images/keycloak-custom/` | `keycloak-custom:26.5.2` | 自定义 Keycloak：groups claim Mapper + 自定义登录主题 |

---

## 四、Helm 部署图表

| 路径 | 说明 |
|---|---|
| `da-cluster/charts/aidp-iam/` | 伞形图表（入口），依赖下方4个子图表 |
| `da-cluster/charts/keycloak/` | Keycloak + Postgres，含 `postgres-init-configmap.yaml`（DB schema） |
| `da-cluster/charts/opa/` | OPA + OPAL，从 bundle-server 拉取策略 |
| `da-cluster/charts/envoy-gateway/` | Gateway + EnvoyProxy + GatewayClass（Envoy Gateway v1.7.0） |

---

## 五、网关路由

| 路径 | 说明 |
|---|---|
| `da-cluster/gateway-routes/reference-grants.yaml` | 跨命名空间引用授权（ReferenceGrant） |
| `da-cluster/gateway-routes/keycloak-routes.yaml` | Keycloak 登录/Token 路由 |
| `da-cluster/gateway-routes/protected-routes.yaml` | 受保护路由，挂载 ext_authz（pep-proxy）和 ext_proc（resource-sync） |

---

## 六、脚本与集群配置

| 路径 | 说明 |
|---|---|
| `da-cluster/scripts/setup.sh` | 完整部署：构建镜像 → 部署 Helm → 初始化种子数据 |
| `da-cluster/scripts/rebuild.sh` | 开发迭代：重建4合1镜像并滚动更新集群 |
| `da-cluster/scripts/cleanup.sh` | 清理集群 |
| `da-cluster/scripts/test.sh` | 完整测试套件（~192 个用例） |
| `da-cluster/kind-config.yaml` | Kind 集群配置（本地开发） |

---

## 七、可省略文件

以下文件不影响项目的核心运行，可在精简部署时移除。

### 旧版独立镜像（已被4合1取代）

| 路径 | 说明 |
|---|---|
| `da-cluster/images/opal-proxy/` | pep-proxy + bundle-server 旧版独立镜像，仅供开发调试参考 |
| `da-cluster/images/keycloak-proxy/` | keycloak-proxy 旧版独立镜像，仅供开发调试参考 |
| `da-cluster/images/resource-sync/` | resource-sync 旧版独立镜像，仅供开发调试参考 |

### 已删除：OPAL 相关文件（v1.1 清理）

以下文件已从仓库中删除，bundle-server 改用时间间隔轮询直接推送到 OPA，不再需要 OPAL：

| 路径 | 说明 |
|---|---|
| `opal-dynamic-policy/data/` | 旧版 OPAL 静态策略数据（templates/ + tenants/），bundle-server 不读取 |
| `opal-dynamic-policy/*.sh`, `Dockerfile` 等 | 旧版独立部署脚本和构建文件 |
| `da-cluster/charts/opa/templates/opal-server-deployment.yaml` | OPAL Server 部署，已删除 |
| `da-cluster/charts/opa/templates/opal-server-service.yaml` | OPAL Server 服务，已删除 |

### Mock 服务（非核心）

| 路径 | 说明 |
|---|---|
| `da-cluster/images/rubik-backend/` | Rubik 后端 Mock，仅用于集成测试 |
| `da-cluster/images/rubik-frontend/` | Rubik 前端 Mock，仅用于集成测试 |
| `da-cluster/mock-deployments/` | Mock 服务 K8s 部署配置 |

### 离线/气隙环境（按需）

| 路径 | 说明 |
|---|---|
| `da-cluster/offline/` | 离线镜像包、CRD、Helm chart 离线包，仅气隙环境需要 |
| `da-cluster/scripts/export-images.sh` | 构建离线 tar 包 |
| `da-cluster/scripts/load-images.sh` | 加载离线镜像到节点 |

### 辅助脚本（非最小运行）

| 路径 | 说明 |
|---|---|
| `da-cluster/scripts/build-release-images.sh` | 发布版本镜像构建 |
| `da-cluster/scripts/bench.sh` | 压测（1000并发/100租户） |
| `da-cluster/scripts/test-external-idp.sh` | 外部 IdP 集成测试 |
| `da-cluster/scripts/strip-crd-descriptions.py` | CRD 体积压缩工具 |

### 文档（不影响运行）

| 路径 | 说明 |
|---|---|
| `diagrams/` | 架构图、设计文档 |
| `da-cluster/docs/` | 部署指南、集成指南、API 参考 |
| `README.md` | 项目总览 |
| `RELEASE_NOTES.md` | 版本历史 |
