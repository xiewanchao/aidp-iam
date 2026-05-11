# Python Package Dependencies

采集时间：2026-05-11

说明：
- 直接依赖：在 requirements.txt 中显式声明的包。
- 传递依赖：由直接依赖自动拉取，未在 requirements.txt 中声明。
- 实际运行版本来自 `aidp-iam-app:v1` 容器 `pip list`。
- `ext_authz_pb2`、`ext_proc_pb2` 及对应 `_grpc` 文件为本地生成的 protobuf stub，不是 pip 包，不纳入本表。

## 完整包列表

| 包名 | 实际运行版本 | 依赖类型 | 来源（直接依赖的上游） | 用途 |
|------|---:|:---:|---|---|
| aiofiles | 23.2.1 | 直接 | — | 异步文件 I/O（pep-proxy bundle 读写） |
| annotated-types | 0.7.0 | 传递 | pydantic | pydantic 类型注解支持 |
| anyio | 3.7.1 | 传递 | fastapi / httpx | 异步 I/O 抽象层 |
| async-property | 0.2.2 | 传递 | python-keycloak | 异步属性装饰器 |
| asyncpg | 0.29.0 | 直接 | — | 异步 PostgreSQL 驱动 |
| certifi | 2026.4.22 | 传递 | requests / httpx | CA 证书包 |
| cffi | 2.0.0 | 传递 | cryptography | C 外部函数接口（cryptography 底层） |
| charset-normalizer | 3.4.7 | 传递 | requests | HTTP 响应字符集检测 |
| click | 8.3.3 | 传递 | uvicorn | CLI 参数解析 |
| cryptography | 47.0.0 | 传递 | python-jose / grpcio | JWT 签名/验证加密原语 |
| deprecation | 2.1.0 | 传递 | python-keycloak | 废弃警告工具 |
| ecdsa | 0.19.2 | 传递 | python-jose | ECDSA 签名算法 |
| fastapi | 0.104.1 | 直接 | — | Web 框架（全部四个服务） |
| grpcio | 1.68.1 | 直接 | — | gRPC 运行时（ext_authz + ext_proc） |
| grpcio-tools | 1.68.1 | 直接 | — | gRPC protobuf 代码生成 |
| h11 | 0.16.0 | 传递 | uvicorn / httpcore | HTTP/1.1 协议实现 |
| httpcore | 1.0.9 | 传递 | httpx | httpx 底层连接池 |
| httptools | 0.7.1 | 传递 | uvicorn[standard] | 高性能 HTTP 解析器 |
| httpx | 0.25.1 | 直接 | — | 异步 HTTP 客户端（OPA、Keycloak 调用） |
| idna | 3.13 | 传递 | requests / httpx / anyio | 国际化域名编码 |
| jwcrypto | 1.5.7 | 传递 | python-keycloak | JWK/JWE 加密操作 |
| packaging | 26.2 | 传递 | python-keycloak | 版本号解析工具 |
| protobuf | 5.29.6 | 传递 | grpcio-tools | protobuf 序列化运行时 |
| pyasn1 | 0.6.3 | 传递 | rsa | ASN.1 编解码（RSA 密钥格式） |
| pycparser | 3.0 | 传递 | cffi | C 代码解析器（cffi 构建依赖） |
| pydantic | 2.4.2 | 直接 | — | 数据校验与序列化 |
| pydantic_core | 2.10.1 | 传递 | pydantic | pydantic v2 Rust 核心 |
| python-dotenv | 1.2.2 | 直接 | — | `.env` 文件加载（keycloak-proxy） |
| python-jose | 3.3.0 | 直接 | — | JWT 解析与验证（pep-proxy） |
| python-keycloak | 5.1.1 | 直接 | — | Keycloak Admin API 客户端 |
| python-multipart | 0.0.6 | 直接 | — | multipart/form-data 解析（文件上传） |
| PyYAML | 6.0.1 | 直接 | — | YAML 解析（bundle-server） |
| requests | 2.33.1 | 直接 | — | 同步 HTTP 客户端（keycloak-proxy） |
| requests-toolbelt | 1.0.0 | 传递 | python-keycloak | multipart 上传工具 |
| rsa | 4.9.1 | 传递 | python-jose | RSA 签名算法 |
| six | 1.17.0 | 传递 | ecdsa | Python 2/3 兼容层（ecdsa 遗留依赖） |
| sniffio | 1.3.1 | 传递 | anyio / httpcore | 异步库检测 |
| starlette | 0.27.0 | 传递 | fastapi | ASGI 框架基础层 |
| typing_extensions | 4.15.0 | 直接 | — | 类型注解向后兼容 |
| urllib3 | 2.6.3 | 传递 | requests | HTTP 连接池（requests 底层） |
| uvicorn | 0.24.0 | 直接 | — | ASGI 服务器 |
| uvloop | 0.22.1 | 传递 | uvicorn[standard] | 高性能事件循环（Linux） |
| watchfiles | 1.1.1 | 传递 | uvicorn[standard] | 文件变更监听（热重载） |
| websockets | 16.0 | 传递 | uvicorn[standard] | WebSocket 支持 |

## 直接依赖汇总

| 包名 | 版本约束 | 所在 requirements.txt |
|------|---:|---|
| fastapi | ==0.104.1 | aidp-iam-app, resource-sync |
| uvicorn[standard] | ==0.24.0 | aidp-iam-app, resource-sync |
| pydantic | ==2.4.2 | aidp-iam-app, resource-sync |
| typing-extensions | 无约束 | aidp-iam-app |
| python-multipart | ==0.0.6 | aidp-iam-app |
| python-dotenv | 无约束 | aidp-iam-app |
| httpx | ==0.25.1 | aidp-iam-app, resource-sync |
| requests | 无约束 | aidp-iam-app |
| asyncpg | ==0.29.0 | aidp-iam-app, resource-sync |
| python-jose[cryptography] | ==3.3.0 | aidp-iam-app |
| aiofiles | ==23.2.1 | aidp-iam-app |
| PyYAML | ==6.0.1 | aidp-iam-app |
| grpcio | ==1.68.1 | aidp-iam-app, resource-sync |
| grpcio-tools | ==1.68.1 | aidp-iam-app, resource-sync |
| python-keycloak | 无约束 | aidp-iam-app |
