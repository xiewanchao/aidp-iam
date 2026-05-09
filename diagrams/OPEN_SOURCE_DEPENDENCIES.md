# 开源引入清单

采集时间：2026-05-09

说明：

- 本清单只记录软件运行时涉及的开源组件和依赖，不记录测试 mock/demo、kind 集群环境镜像、Kubernetes 系统组件、local-path 存储组件、Helm/kubectl 等本地环境工具。随软件部署到集群内运行的 Job 工具镜像需要记录。
- Python 包版本来自当前实际运行的 `aidp-iam-app:v1` 容器：`python -m pip list --format=freeze`。下表只保留应用运行依赖，不列 Python 解释器本身以及 `pip`、`setuptools`、`wheel` 等打包工具。
- Gateway 包内目标版本按本次调整记录：控制面 `docker.io/envoyproxy/gateway:v1.7.2`，数据面改为非 distroless 的 `docker.io/envoyproxy/envoy:v1.36.5`。
- 当前集群仍在运行旧 Gateway：`gateway:v1.7.0`、`envoy:distroless-v1.37.0`，重新部署 Gateway 包后才会切换到目标版本。
- Node/前端依赖只列 `keycloak-theme/package-lock.json` 中的直接运行时依赖；构建、Lint、Storybook、TypeScript 类型等开发期依赖不纳入运行时清单。

## Gateway 运行时组件

| 类型 | 开源组件 | 版本/Tag | 运行时用途 | 本地位置 |
|---|---|---:|---|---|
| Helm Chart | Envoy Gateway `gateway-helm` | v1.7.2 | Gateway 控制面安装包 | `package-gateway/charts/aidp-gateway/charts/gateway-helm-v1.7.2.tgz` |
| Container Image | Envoy Gateway Controller | v1.7.2 | Gateway 控制面 | `package-gateway/images/arm64/docker.io_envoyproxy_gateway_v1.7.2.tar` |
| Container Image | Envoy | v1.36.5 | Gateway 数据面，非 distroless 镜像 | `package-gateway/images/arm64/docker.io_envoyproxy_envoy_v1.36.5.tar` |
| Container Image | Alpine kubectl | 1.35.3 | Gateway 卸载清理 Job | `package-gateway/images/arm64/docker.io_alpine_kubectl_1.35.3.tar` |

注意：Envoy Gateway `v1.7.2` 官方默认数据面通常跟随 `1.37.x` 线。本清单按当前要求显式覆盖为 `envoy:v1.36.5`，需要通过 Gateway 基础路由、HTTPS、超时/重试和 BIP 场景做回归验证。

## 软件运行时镜像

| 模块 | 运行时组件 | 镜像 | 说明 |
|---|---|---|---|
| Gateway | Envoy Gateway 控制面 | `docker.io/envoyproxy/gateway:v1.7.2` | 包内目标版本 |
| Gateway | Envoy 数据面 | `docker.io/envoyproxy/envoy:v1.36.5` | 包内目标版本，非 distroless |
| Gateway | 卸载清理 Job | `docker.io/alpine/kubectl:1.35.3` | Helm 卸载时清理 Gateway 相关资源 |
| IAM | IAM 服务 | `aidp-iam-app:v1` | 自研运行时镜像，Python 开源依赖见下表 |
| IAM | OPA | `openpolicyagent/opa:0.70.0-static` | 策略引擎 |
| IAM | Keycloak | `keycloak-custom:26.5.2` | 自定义 Keycloak 镜像 |
| IAM | PostgreSQL | `postgres:17` | Keycloak 数据库 |

## Python 运行时包版本

| 包名 | 实际运行版本 |
|---|---:|
| aiofiles | 23.2.1 |
| asyncpg | 0.29.0 |
| fastapi | 0.104.1 |
| grpcio | 1.68.1 |
| grpcio-tools | 1.68.1 |
| httpx | 0.25.1 |
| pydantic | 2.4.2 |
| python-dotenv | 1.2.2 |
| python-jose | 3.3.0 |
| python-keycloak | 5.1.1 |
| python-multipart | 0.0.6 |
| PyYAML | 6.0.1 |
| requests | 2.33.1 |
| typing_extensions | 4.15.0 |
| uvicorn | 0.24.0 |

## Python 运行时直接声明依赖

| 文件 | 直接声明依赖 |
|---|---|
| `da-cluster/images/aidp-iam-app/requirements.txt` | fastapi 0.104.1, uvicorn 0.24.0, pydantic 2.4.2, typing-extensions, python-multipart 0.0.6, python-dotenv, httpx 0.25.1, requests, asyncpg 0.29.0, python-jose 3.3.0, aiofiles 23.2.1, PyYAML 6.0.1, grpcio 1.68.1, grpcio-tools 1.68.1, python-keycloak |
| `resource-sync/requirements.txt` | fastapi 0.104.1, uvicorn 0.24.0, httpx 0.25.1, pydantic 2.4.2, asyncpg 0.29.0, grpcio 1.68.1, grpcio-tools 1.68.1 |

## Keycloak 主题运行时依赖

| 包名 | 精确版本 | 类型 |
|---|---:|---|
| keycloakify | 11.15.0 | dependency |
| react | 18.3.1 | dependency |
| react-dom | 18.3.1 | dependency |

---

## 清单审查备注（2026-05-09）

### 遗漏项

以下包在容器实际运行版本表中存在，但未在直接声明依赖中说明其来源：

| 包名 | 实际版本 | 来源（传递依赖） |
|---|---:|---|
| ecdsa | 0.19.2 | python-jose 的传递依赖 |
| rsa | 4.9.1 | python-jose 的传递依赖 |
| six | 1.17.0 | python-jose / ecdsa 的传递依赖 |
| jwcrypto | 1.5.7 | python-keycloak 的传递依赖 |
| protobuf | 5.29.6 | grpcio-tools 的传递依赖 |
| pyasn1 | 0.6.3 | rsa 的传递依赖 |
| pycparser | 3.0 | cffi 的传递依赖 |
| cffi | 2.0.0 | cryptography 的传递依赖 |

另：`opal-dynamic-policy/requirements.txt` 未列入直接声明依赖表。该文件声明 `grpcio==1.59.3`，与容器实际运行的 `1.68.1` 不同，仅用于本地开发环境，不影响生产镜像，但应在清单中说明。

### 版本升级风险分析

#### 高风险：python-jose 3.3.0 + cryptography 47.0.0（当前已存在）

python-jose 3.3.0 依赖 cryptography 的旧式内部 API，而容器中实际运行的 cryptography 为 47.0.0（远超 jose 测试时的版本范围）。已知风险：

- `cryptography >= 42.0` 移除了 `load_pem_private_key` 的旧调用方式，python-jose 在使用 RSA 私钥签名时可能抛 `TypeError`。
- `cryptography >= 44.0` 对 `cffi` 绑定层做了重构，`cffi 2.0.0` 是全新大版本，与旧版行为有差异。
- **建议**：将 python-jose 升级到社区维护的 fork `joserfc`（API 兼容），或在 requirements.txt 中显式固定 `cryptography>=41.0,<42.0` 并锁定镜像构建时的版本。

#### 高风险：FastAPI 0.104.1 ↔ Pydantic 2.4.2（强耦合，升级必须成对）

FastAPI 0.104.x 强依赖 Pydantic v2 的内部 API（`model_fields`、`model_validate`、`model_dump` 等）。升级任意一方时的约束：

- 升级 Pydantic 到 2.7+：需同步升级 FastAPI 到 0.111+，否则 `pydantic_core` ABI 不兼容会在启动时崩溃。
- 升级 FastAPI 到 0.115+：需 Pydantic >= 2.7，且 starlette 需同步升级（当前 starlette 0.27.0 对应 FastAPI 0.104.x，0.115+ 要求 starlette >= 0.40.0）。
- **建议**：FastAPI、Pydantic、starlette 三个包作为一组同时升级，参考 FastAPI 官方 changelog 中的兼容矩阵。

#### 中风险：grpcio 版本不统一

| 文件 | grpcio | grpcio-tools |
|---|---:|---:|
| `aidp-iam-app/requirements.txt`（生产镜像） | 1.68.1 | 1.68.1 |
| `resource-sync/requirements.txt` | 1.68.1 | 1.68.1 |
| `opal-dynamic-policy/requirements.txt`（本地开发） | 1.59.3 | 1.59.3 |

生产镜像内部一致，无运行时风险。但本地开发环境与生产镜像版本差距较大（1.59 vs 1.68），可能导致本地生成的 protobuf stub 与生产环境行为不一致。**建议**：将 `opal-dynamic-policy/requirements.txt` 中的 grpcio 统一到 1.68.1。

#### 中风险：protobuf 5.29.6（大版本跨越）

protobuf 从 3.x 升级到 4.x/5.x 有 breaking changes：移除了 `MessageToJson`/`MessageToDict` 的部分参数，`descriptor_pool` 行为变更。当前 grpcio-tools 1.68.1 拉取的是 protobuf 5.29.6，如果代码中有直接使用 `google.protobuf` API（而非只用生成的 stub），需要验证兼容性。

#### 低风险：无版本锁定的包

| 包名 | 实际运行版本 | 风险说明 |
|---|---:|---|
| python-dotenv | 1.2.2 | API 稳定，低风险 |
| requests | 2.33.1 | 2.x 系列 API 稳定；3.x 尚未发布，暂无风险 |
| typing-extensions | 4.15.0 | 向后兼容，低风险 |
| python-keycloak | 5.1.1 | 该库历史上有多次 API 重构（3.x→4.x→5.x 均有 breaking changes），**建议在 requirements.txt 中固定版本** |
