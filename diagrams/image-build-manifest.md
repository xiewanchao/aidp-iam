# 镜像构建清单

版本：v1.0  
日期：2026-05-06  
说明：本文档描述项目中所有 Docker 镜像的构建信息、端口、依赖关系及构建命令。

---

## 一、镜像总览

| 镜像名称 | 标签 | 类型 | 说明 |
|---|---|---|---|
| `aidp-iam-app` | `v1` | 自建（4合1） | 生产主镜像，包含 4 个 IAM 服务 |
| `keycloak-init` | `v2` | 自建（Job） | 一次性初始化 Job，种子数据 |
| `keycloak-custom` | `26.5.2` | 自建（扩展） | Keycloak + 自定义 Mapper + 主题 |
| `keycloak-proxy` | `v3` | 自建（独立） | keycloak-proxy 独立镜像（开发用） |
| `opal-proxy` | `v2` | 自建（独立） | pep-proxy + bundle-server 独立镜像（开发用） |
| `resource-sync` | `v1` | 自建（独立） | resource-sync 独立镜像（开发用） |
| `rubik-backend` | `latest` | 自建 | Rubik 后端 API |
| `rubik-frontend` | `latest` | 自建 | Rubik 前端静态页面（nginx） |
| `postgres` | `17` | 第三方 | 数据库 |
| `envoyproxy/gateway` | `v1.7.0` | 第三方 | Envoy Gateway 控制面 |
| `envoyproxy/envoy` | `distroless-v1.37.0` | 第三方 | Envoy 数据面 |
| `openpolicyagent/opa` | `0.70.0-static` | 第三方 | OPA 策略引擎 |

---

## 二、自建镜像详情

### 2.1 aidp-iam-app（生产主镜像）

**这是生产环境使用的核心镜像**，将 4 个 Python 服务打包在一个容器中，由 supervisord 管理。

| 属性 | 值 |
|---|---|
| 镜像名 | `aidp-iam-app:v1` |
| 基础镜像 | `python:3.11-slim` |
| Dockerfile | `da-cluster/images/aidp-iam-app/Dockerfile` |
| 构建脚本 | `da-cluster/scripts/rebuild.sh` |

**包含的服务：**

| 服务 | 端口 | 协议 | 说明 |
|---|---|---|---|
| keycloak-proxy | 8090 | HTTP | 用户/组/应用/API Key 管理 API（AccessManager） |
| pep-proxy | 8000 | HTTP | 鉴权检查 HTTP 端点 |
| pep-proxy | 9000 | gRPC | Envoy ext_authz 端点 |
| bundle-server | 8001 | HTTP | 生成 OPA Rego bundle，推送到 OPA |
| resource-sync | 8080 | HTTP | ACL 管理 HTTP 端点（健康检查） |
| resource-sync | 8082 | gRPC | Envoy ext_proc 端点（拦截请求/响应写 ACL） |

**构建上下文组装（rebuild.sh 逻辑）：**

```
临时目录 $CTX/
├── Dockerfile
├── supervisord.conf
├── requirements.txt
├── keycloak-proxy/app/     ← da-idb-proxy/app/
├── pep-proxy/app/          ← opal-dynamic-policy/pep-proxy/app/
├── pep-proxy/proto/        ← opal-dynamic-policy/pep-proxy/proto/
├── bundle-server/app/      ← opal-dynamic-policy/bundle-server/app/
├── bundle-server/data/     ← opal-dynamic-policy/bundle-server/data/ (或空目录)
├── resource-sync/app/      ← resource-sync/app/
└── resource-sync/proto/    ← resource-sync/proto/
```

**构建命令：**

```bash
# 开发迭代（Kind 集群，amd64）
cd da-cluster
./scripts/rebuild.sh

# 开发迭代（外部 K8s，不使用 Kind）
./scripts/rebuild.sh --no-kind

# 跨架构构建（arm64，需要 QEMU）
./scripts/rebuild.sh --arch arm64

# 同时重建 app + init
./scripts/rebuild.sh app init
```

**健康检查：**

```
curl http://localhost:8000/health   # pep-proxy
curl http://localhost:8090/api/v1/common/health  # keycloak-proxy
curl http://localhost:8080/health   # resource-sync
```

---

### 2.2 keycloak-init（初始化 Job）

| 属性 | 值 |
|---|---|
| 镜像名 | `keycloak-init:v2` |
| 基础镜像 | `python:3.11-slim` |
| Dockerfile | `da-cluster/images/keycloak-init/Dockerfile` |
| 运行方式 | K8s Job（一次性，完成后退出） |
| 端口 | 无 |

**职责：**
- 在 Keycloak 中创建 realm、client、初始用户和用户组
- 向 IAM PostgreSQL 数据库写入种子数据（apps、permission_groups 等）
- 向 `app_manifests` 表注册各应用的 manifest（如已配置）

**构建命令：**

```bash
cd da-cluster
./scripts/rebuild.sh init
```

---

### 2.3 keycloak-custom（Keycloak 扩展镜像）

| 属性 | 值 |
|---|---|
| 镜像名 | `keycloak-custom:26.5.2` |
| 基础镜像 | `quay.io/keycloak/keycloak:26.5.2` |
| Dockerfile | `da-cluster/images/keycloak-custom/Dockerfile` |
| 端口 | 继承自基础镜像（8080） |

**扩展内容：**
- `data-agent-mapper.jar`：自定义 Token Mapper，将 groups/group_ids 注入 JWT
- `keycloak-theme.jar`：自定义登录页主题（keycloakify 构建）
- 启用 `scripts` feature：`kc.sh build --features=scripts`

---

### 2.4 独立开发镜像（非生产）

以下三个镜像是各服务的独立版本，**仅用于开发调试**，生产环境使用 `aidp-iam-app:v1`。

| 镜像名 | Dockerfile | 包含服务 | 端口 |
|---|---|---|---|
| `keycloak-proxy:v3` | `da-cluster/images/keycloak-proxy/Dockerfile` | keycloak-proxy | 8090 |
| `opal-proxy:v2` | `da-cluster/images/opal-proxy/Dockerfile` | pep-proxy + bundle-server | 8000, 8001, 9000 |
| `resource-sync:v1` | `da-cluster/images/resource-sync/Dockerfile` | resource-sync | 8080, 8082 |

---

### 2.5 rubik-backend

| 属性 | 值 |
|---|---|
| 镜像名 | `rubik-backend:latest` |
| 基础镜像 | `python:3.11-slim` |
| Dockerfile | `da-cluster/images/rubik-backend/Dockerfile` |
| 端口 | 43252 (HTTP) |
| 启动命令 | `uvicorn backend.main:app --host 0.0.0.0 --port 43252` |

---

### 2.6 rubik-frontend

| 属性 | 值 |
|---|---|
| 镜像名 | `rubik-frontend:latest` |
| 基础镜像 | 构建阶段：`node:20-alpine`；运行阶段：`nginx:1.27-alpine` |
| Dockerfile | `da-cluster/images/rubik-frontend/Dockerfile` |
| 端口 | 80 (HTTP) |
| 构建参数 | `VITE_API_BASE_URL`、`VITE_IAM_BASE_URL`（编译时注入，不可运行时修改） |

---

## 三、第三方镜像

| 镜像 | 用途 | 拉取方式 |
|---|---|---|
| `postgres:17` | IAM 数据库（Keycloak + iam DB） | Docker Hub |
| `envoyproxy/gateway:v1.7.0` | Envoy Gateway 控制面 | Docker Hub |
| `envoyproxy/envoy:distroless-v1.37.0` | Envoy 数据面（由 Gateway 管理） | Docker Hub |
| `openpolicyagent/opa:0.70.0-static` | OPA 策略引擎（与 aidp-iam-app 同 Pod） | Docker Hub |
| `kindest/node` | Kind 集群节点（本地开发） | Docker Hub |
| `alpine/helm:3.17.3` | Helm 命令行（CI/CD 用） | Docker Hub |
| `nginx:alpine` | 静态资源服务 | Docker Hub |

---

## 四、发布构建（离线包）

生产发布时使用 `build-release-images.sh` 构建离线 tar 包：

```bash
cd da-cluster
./scripts/build-release-images.sh
```

**输出目录结构：**

```
release-images/
├── amd64/
│   ├── aidp-iam-app-v1.tar
│   ├── keycloak-init-v2.tar
│   ├── keycloak-custom-26.5.2.tar
│   ├── postgres-17.tar
│   ├── envoyproxy-gateway-v1.7.0.tar
│   └── ...
└── arm64/
    └── ...（同上，arm64 架构）

offline/
├── charts/
│   └── gateway-helm-v1.7.0.tgz
└── crds/
    └── gateway-api-v1.4.1-experimental.yaml（已裁剪 description，< 500KB）
```

**加载离线包到集群：**

```bash
./scripts/load-images.sh
```

---

## 五、镜像依赖关系

```
aidp-iam-app:v1
  ├── 源码依赖：da-idb-proxy/app/
  ├── 源码依赖：opal-dynamic-policy/pep-proxy/
  ├── 源码依赖：opal-dynamic-policy/bundle-server/
  └── 源码依赖：resource-sync/

keycloak-custom:26.5.2
  └── 基础镜像：quay.io/keycloak/keycloak:26.5.2

rubik-frontend:latest
  ├── 构建阶段：node:20-alpine
  └── 运行阶段：nginx:1.27-alpine

其余自建镜像
  └── 基础镜像：python:3.11-slim
```

**运行时依赖（K8s Pod 级别）：**

```
iam-services Pod
  ├── container: aidp-iam-app:v1  （4个服务）
  └── container: opa:0.70.0-static（bundle-server 推送 bundle 到此）

keycloak Pod
  └── container: keycloak-custom:26.5.2

postgres Pod
  └── container: postgres:17（keycloak DB + iam DB 共用）
```

---

## 六、常用构建命令速查

```bash
# 重建主镜像并滚动更新（开发最常用）
cd da-cluster && ./scripts/rebuild.sh

# 重建 keycloak-init 并重新执行初始化 Job
cd da-cluster && ./scripts/rebuild.sh init

# 重建两者
cd da-cluster && ./scripts/rebuild.sh app init

# 构建发布离线包（amd64 + arm64）
cd da-cluster && ./scripts/build-release-images.sh

# 加载离线包到 Kind 集群
cd da-cluster && ./scripts/load-images.sh

# 清理集群
cd da-cluster && ./scripts/cleanup.sh
```
