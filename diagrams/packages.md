# Python 直接依赖版本收敛

本文只统计当前生产交付路径会构建或安装的 Python 直接依赖：

- `aidp-iam-app:v1`
- `gateway-manager:v1`
- `keycloak-init:v2`

不统计 mock、测试骨架、Kind 环境工具镜像和历史拆分镜像。

## 1. 直接替换，不需要开源引入

| 包名 | 目标版本 | 状态 |
|---|---:|---|
| `fastapi` | `0.115.11` | 已统一到生产镜像 |
| `uvicorn[standard]` | `0.34.0` | 已统一到生产镜像 |
| `pydantic` | `2.12.5` | 已统一到生产镜像，版本仍按选型中处理 |
| `requests` | `2.33.0` | 已统一到 `aidp-iam-app`、`gateway-manager`、`keycloak-init` |
| `cryptography` | `46.0.7` | 已用于 `gateway-manager` 和 `aidp-iam-app` JWT RS256 校验 |

## 2. CleanSource 只有依赖软件，需要按主软件开源引入

| 包名 | 目标版本 | 使用位置 |
|---|---:|---|
| `python-multipart` | `0.0.7` | FastAPI 文件上传/Form 解析，`aidp-iam-app`、`gateway-manager` |
| `python-dotenv` | `1.2.2` | `da-idb-proxy/app/main.py` 直接加载环境配置 |
| `httpx` | `0.28.1` | `pep-proxy`、`bundle-server` 直接 HTTP 调用 |
| `grpcio` | `1.68.1` | `pep-proxy` ext_authz、`resource-sync` ext_proc gRPC 运行时 |

## 3. 需要开源引入

| 包名 | 版本 | 使用位置 |
|---|---:|---|
| `PyJWT` | `2.12.0` | `pep-proxy` JWT/JWK 校验 |
| `grpcio-tools` | `1.68.1` | Docker build 阶段生成 gRPC stub |

## 4. 用不到的依赖，已删除或不纳入生产清单

| 包名 | 处理 |
|---|---|
| `aiofiles` | 当前生产源码没有直接 import，不纳入清单 |
| `PyYAML` | 只出现在测试骨架，不纳入生产清单 |
| `python-keycloak` | 只出现在旧独立 proxy Dockerfile，旧入口已删除 |
| `psycopg2-binary` | `keycloak-init` 已去掉该 Python DB 驱动，默认数据改由 Postgres 初始化 SQL 写入 |

## 5. 数据库侧待 GV 接管

| 包名 | 当前版本 | 说明 |
|---|---:|---|
| `asyncpg` | `0.29.0` | 当前 IAM 运行时代码仍直接依赖 PostgreSQL 异步访问。后续 GV 替换数据库访问层后由数据库/GV 侧处理开源引入，本清单不作为最终归属。 |

## 当前生产直接依赖声明

| 文件 | 直接依赖 |
|---|---|
| `da-cluster/images/aidp-iam-app/requirements.txt` | `fastapi==0.115.11`, `uvicorn[standard]==0.34.0`, `pydantic==2.12.5`, `typing-extensions==4.15.0`, `python-multipart==0.0.7`, `python-dotenv==1.2.2`, `httpx==0.28.1`, `requests==2.33.0`, `asyncpg==0.29.0`, `PyJWT==2.12.0`, `cryptography==46.0.7`, `grpcio==1.68.1`, `grpcio-tools==1.68.1` |
| `package-gateway/images/gateway-manager/requirements.txt` | `fastapi==0.115.11`, `uvicorn[standard]==0.34.0`, `python-multipart==0.0.7`, `cryptography==46.0.7`, `requests==2.33.0` |
| `da-cluster/images/keycloak-init/Dockerfile` | `requests==2.33.0`, `kubernetes==33.1.0` |
