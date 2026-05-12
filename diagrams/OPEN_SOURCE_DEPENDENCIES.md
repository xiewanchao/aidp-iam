# 开源引入清单

本文按当前生产交付路径整理，不包含 mock、测试骨架、Kind 环境工具镜像和历史拆分镜像。

## 直接替换，不需要开源引入

| 包名 | 原版本 | 目标版本 | 处理 |
|---|---:|---:|---|
| `fastapi` | `0.104.1` | `0.115.11` | 已统一 |
| `uvicorn` | `0.24.0` | `0.34.0` | 已统一 |
| `pydantic` | `2.4.2` | `2.12.5` | 已统一，按选型中版本处理 |
| `requests` | `2.31.0` / 无约束 | `2.33.0` | 已统一 |
| `cryptography` | 无 | `46.0.7` | 已统一，用于 `gateway-cert-manager` 和 `aidp-iam-app` JWT RS256 校验 |

## CleanSource 只有依赖软件，需要作为主软件开源引入

| 包名 | 开源引入版本 | 说明 |
|---|---:|---|
| `python-multipart` | `0.0.7` | FastAPI 文件上传/Form 解析 |
| `python-dotenv` | `1.2.2` | 环境变量配置加载 |
| `httpx` | `0.28.1` | 异步 HTTP 客户端 |
| `grpcio` | `1.68.1` | gRPC 运行时 |

## 需要开源引入

| 包名 | 开源引入版本 | 说明 |
|---|---:|---|
| `PyJWT` | `2.12.0` | JWT/JWK 校验 |
| `grpcio-tools` | `1.68.1` | 构建阶段生成 gRPC stub |

## 删除或不纳入生产清单

| 包名 | 处理 |
|---|---|
| `aiofiles` | 当前生产代码未使用，删除旧记录 |
| `PyYAML` | 当前生产路径未直接使用，测试骨架不纳入本清单 |
| `python-keycloak` | 旧独立 proxy 入口已删除，不纳入本清单 |
| `psycopg2-binary` | 已从 `keycloak-init` 删除，默认数据库种子改为 Postgres 初始化 SQL |

## 数据库侧待 GV 接管

| 包名 | 当前版本 | 说明 |
|---|---:|---|
| `asyncpg` | `0.29.0` | 当前 IAM 服务仍直接访问 PostgreSQL，后续 GV 替换数据库访问层后由数据库/GV 侧处理归属。当前保留是为了保证现有测试可运行。 |

## 仍需补查公司库

| 包名 | 当前版本 | 使用位置 |
|---|---:|---|
| `kubernetes` | `33.1.0` | `keycloak-init` 写入 Kubernetes Secret |
| `supervisor` | `4.3.0` | `aidp-iam-app` 容器内守护多个 Python 服务 |
