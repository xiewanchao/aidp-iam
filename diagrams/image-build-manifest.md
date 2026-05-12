# 镜像构建清单

版本：v2.1
日期：2026-05-07
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
| `mock-kb` | `v1` | 自建（Mock） | KnowledgeBase 后端 Mock，RESTful 统一 URL 格式 |
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
| keycloak-proxy | 8090 | HTTP | 用户/组/应用/API Key/Manifest 管理 API（AccessManager） |
| pep-proxy | 8000 | HTTP | 鉴权检查 HTTP 端点 |
| pep-proxy | 9000 | gRPC | Envoy ext_authz 端点 |
| bundle-server | 8001 | HTTP | 生成 OPA Rego bundle，推送到 OPA；从 app_manifests 派生 path_rules |
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
- 在 Keycloak 中创建 realm、client、初始用户和用户组（`master-admins`、`all-users`）
- 向 IAM PostgreSQL 数据库写入种子数据：
  - `apps` 表：注册 KnowledgeBase、DataAgent、MemoryStore（enabled 状态，供 OPA app_disabled 检查）
  - `permission_groups` 表：仅写入系统级路径规则（`/api/v1/`、`/acl/v1/`、`/AccessManager/`）
- **不再**预置 permission_group_paths 中的业务应用路径规则；应用路径规则由 bundle-server 从 `app_manifests` 表实时派生

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

**独立镜像构建说明：**

Dockerfile 中的 `COPY` 路径（`pep-proxy/app`、`bundle-server/app`、`supervisord.conf`、`requirements.txt`）来自不同目录，无法直接以单一源目录作为 build context，需先组装临时目录：

```bash
cd /path/to/aidp-iam

# opal-proxy:v2（pep-proxy + bundle-server）
CTX=$(mktemp -d)
cp da-cluster/images/opal-proxy/Dockerfile "$CTX/"
cp da-cluster/images/opal-proxy/requirements.txt "$CTX/"
cp da-cluster/images/opal-proxy/supervisord.conf "$CTX/"
mkdir -p "$CTX/pep-proxy" "$CTX/bundle-server"
cp -r opal-dynamic-policy/pep-proxy/app   "$CTX/pep-proxy/app"
cp -r opal-dynamic-policy/pep-proxy/proto "$CTX/pep-proxy/proto"
cp -r opal-dynamic-policy/bundle-server/app "$CTX/bundle-server/app"
docker build --build-arg TARGETARCH=amd64 -t opal-proxy:v2 "$CTX"
rm -rf "$CTX"
kind load docker-image opal-proxy:v2 --name da-cluster
kubectl -n opa rollout restart deploy/pep-proxy

# resource-sync no longer has a standalone production image.
# It is built into aidp-iam-app:v1 together with keycloak-proxy,
# pep-proxy, and bundle-server.
```

---

### 2.5 mock-kb（KnowledgeBase Mock 后端）

| 属性 | 值 |
|---|---|
| 镜像名 | `mock-kb:v1` |
| 基础镜像 | `python:3.11-slim` |
| Dockerfile | `mock-kb/Dockerfile` |
| 端口 | 8080 (HTTP) |
| 命名空间 | `mock-kb` |

**API 规范：**
- 遵循统一 URL 格式：`/<NS>/Tenants/{tenantId}/{ResourceType}/{resourceId}`
- 路径前缀：`/KnowledgeBase/Tenants/{tenantId}/`
- 支持 RESTful HTTP 方法（GET/POST/PUT/DELETE）
- 响应体格式：`{"data": ..., "code": 0, "message": "success"}`
- 回显 auth 头为 `X-Debug-*` 响应头（供测试验证）

**主要资源路径：**

| 资源 | 路径 |
|---|---|
| KnowledgeBases | `/KnowledgeBase/Tenants/{tid}/KnowledgeBases/{kbId}` |
| Mappings | `/KnowledgeBase/Tenants/{tid}/KnowledgeBases/{kbId}/Mappings/{mappingId}` |
| Files | `/KnowledgeBase/Tenants/{tid}/KnowledgeBases/{kbId}/Files/{fileId}` |
| Conversations | `/KnowledgeBase/Tenants/{tid}/Conversations/{threadId}` |
| ModelConfigs | `/KnowledgeBase/Tenants/System/ModelConfigs/{modelId}` |
| Prompts | `/KnowledgeBase/Tenants/System/Prompts/{promptId}` |
| JargonLibraries | `/KnowledgeBase/Tenants/{tid}/JargonLibraries/{libName}` |
| Jargons | `/KnowledgeBase/Tenants/{tid}/JargonLibraries/{libName}/Jargons/{jargonName}` |
| FusionSearch | `POST /KnowledgeBase/Tenants/{tid}/Action/FusionSearch` |

**构建命令：**

```bash
cd /path/to/aidp-iam
docker build -t mock-kb:v1 mock-kb/
kind load docker-image mock-kb:v1 --name da-cluster
kubectl -n mock-kb rollout restart deploy/mock-kb
```

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
│   ├── mock-kb-v1.tar
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

---

## 五、镜像依赖关系

```
aidp-iam-app:v1
  ├── 源码依赖：da-idb-proxy/app/          (keycloak-proxy)
  ├── 源码依赖：opal-dynamic-policy/pep-proxy/    (pep-proxy)
  ├── 源码依赖：opal-dynamic-policy/bundle-server/ (bundle-server)
  └── 源码依赖：resource-sync/             (resource-sync)

keycloak-custom:26.5.2
  └── 基础镜像：quay.io/keycloak/keycloak:26.5.2

mock-kb:v1
  └── 基础镜像：python:3.11-slim

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

mock-kb Pod（namespace: mock-kb）
  └── container: mock-kb:v1
```

---

## 六、鉴权数据流（v2.1）

```
应用注册（PUT /AccessManager/Tenants/System/AppManifests/{ns}）
  → app_manifests 表（manifest_json）
  → resource_patterns 表（由 manifests.py 同步写入）
      ├── resource-sync/ext_proc_server.py：PUT create 响应时查 resource_patterns.response_id_field
      │     / id_field，从响应体提取正确的资源 ID，写入 resource_acl（Owner）
      └── pep-proxy/main.py：check_resource_auth() 先查 resource_patterns 确认 namespace 已注册
            manifest；未注册则跳过资源级检查，DB 异常则 fail-closed（拒绝）
  → bundle-server._load_opa_data()（从 app_manifests 派生 path_rules）
      → OPA data.path_rules（all-users 可访问）
          → Rego allow rule

系统路径（/api/v1/、/acl/v1/、/AccessManager/）
  → permission_groups 表（keycloak-init 预置）
      → bundle-server._load_opa_data()（三表 JOIN）
          → OPA data.path_rules（master-admins / tenant-admins）

apps.enabled = false
  → OPA app_disabled rule → 所有路径 403
```

---

## 七、常用构建命令速查

```bash
# 重建主镜像并滚动更新（开发最常用）
cd da-cluster && ./scripts/rebuild.sh

# 重建 keycloak-init 并重新执行初始化 Job
cd da-cluster && ./scripts/rebuild.sh init

# 重建两者
cd da-cluster && ./scripts/rebuild.sh app init

# 重建 mock-kb
cd /path/to/aidp-iam
docker build -t mock-kb:v1 mock-kb/
kind load docker-image mock-kb:v1 --name da-cluster
kubectl -n mock-kb rollout restart deploy/mock-kb

# 构建发布离线包（amd64 + arm64）
cd da-cluster && ./scripts/build-release-images.sh

# 加载离线包到 Kind 集群
cd da-cluster && ./scripts/load-images.sh

# 清理集群
cd da-cluster && ./scripts/cleanup.sh
```
