# aidp-iam v0.6.0 — Offline Debug + Deploy Release

> Branch: `feature/envoy-gateway-migration` · Commit: `77f4ba0`
>
> 该版本在离线环境下可独立部署 + 联调，无需互联网。发布包同时提供 **amd64** 与 **arm64** 两个平台。

## 亮点（本次）

### 1. 权限系统完整 CRUD（关键能力）

之前只有 `apps` 有完整 CRUD，`resource_patterns` / `resource_actions` 只能通过 `POST /apps` 嵌套创建，`permission_groups` 根本没有 REST 入口——导致离线调试必须直接改 `postgres` 或重跑 `init` job。

新增 15 条 REST 接口（见 `diagrams/api-spec-v2.xlsx` sheet 1）：

```
# 资源模式
POST   /api/v1/apps/{app}/resource-patterns
PUT    /api/v1/apps/{app}/resource-patterns?resource_prefix=&method=
DELETE /api/v1/apps/{app}/resource-patterns?resource_prefix=&method=

# 资源动作
POST   /api/v1/apps/{app}/resource-actions?resource_prefix=
PUT    /api/v1/apps/{app}/resource-actions/{id}
DELETE /api/v1/apps/{app}/resource-actions/{id}

# 权限点
POST   /api/v1/permission-groups
GET    /api/v1/permission-groups?app_name=
GET    /api/v1/permission-groups/{id}
PUT    /api/v1/permission-groups/{id}
DELETE /api/v1/permission-groups/{id}
POST   /api/v1/permission-groups/{id}/paths
DELETE /api/v1/permission-groups/{id}/paths/{path_id}
POST   /api/v1/permission-groups/{id}/bindings/{kc_group_name}
DELETE /api/v1/permission-groups/{id}/bindings/{kc_group_name}
```

`PUT /permission-groups/{id}` 支持 `paths` / `bindings` 的 replace-all 语义（发 `[]` 清空，不发字段保持原值）；便于批量修改后一次生效。

**修的 bug**：`POST /api/v1/apps` 的嵌套 resource_patterns INSERT 忽略了 `method` / `share_to_admin_group_on_create` / `share_to_all_users_on_create` 三列，导致通过 REST 注册的 app 都是 method='' fallback。现已修好。

### 2. 用户模型简化（username-only）

去掉 email / firstName / lastName / emailVerified，保留 `username` + Keycloak UUID。
- `init-keycloak` 通过 `ensure_user_profile()` 在 realm 级把 User Profile 缩成 username-required；
- 后端 pydantic schemas、identity.py REST、auth.py identity dict 全部清理；
- Keycloak 26 不允许物理移除 email/firstName/lastName，本次保留声明但取消 required 角色，兼容 password-grant 登录。

### 3. Memory 三层 RBAC

`memory-admins` 租户管理员只能管 `/tenants/*` 子路径、`/templates*`、`/memory/*` 数据；`POST /tenants` 与 `/system/recovery` 这类超管动作收紧回 `admins`。全流程细粒度 RBAC 在 `test-memory-full.sh` 45/45 覆盖。

### 4. SPI mapper + offline tar 刷新

`keycloak-custom:26.5.2` 离线 tar 原先是 3 月版本，缺了 `StructuredGroupMapper` JAR；重新打包到 release 中后，`init-keycloak` 一次装 SPI 成功，JWT 里正确下发 `groups` + `group_ids`。

### 5. 部署 + 联调文档

`da-cluster/docs/deploy-and-integration.md` 新写了一份合并版：
- 架构图（ext_authz + ext_proc 两条流）
- Kind / 标准 K8s / iSula 三种部署脚本选择
- 端口 / `KC_HOSTNAME` / StorageClass 速查
- 接入真实后端 5 步完整流程
- 常见问题排查矩阵（含 pep-proxy 401 / app_disabled / resource-sync 空表 / SPI 404）

## 测试

从零全新部署（`cleanup + setup` on Kind）后：

| suite | 断言数 | 结果 |
|-------|-------|------|
| test.sh | 127 | ✅ 全过 |
| test-kb-full.sh | 69 | ✅ 全过 |
| test-rubik-full.sh | 51 | ✅ 全过 |
| test-memory-full.sh | 45 | ✅ 全过 |
| **合计** | **292** | ✅ |

## 发布包下载与使用

**GitHub 单个 release asset 2GB 上限**，所以拆成 3 个：

| 文件 | 大小 | 内容 | 必下？ |
|------|------|------|------|
| `aidp-iam-v0.6.0-77f4ba0-source-and-common.tar.gz` | ~59 MB | 源码 + charts + CRDs + 脚本 + 文档 | **✅ 必下** |
| `aidp-iam-v0.6.0-77f4ba0-images-amd64.tar.gz` | ~1.5 GB | amd64 镜像 tar（11 个） | x86_64 服务器下这个 |
| `aidp-iam-v0.6.0-77f4ba0-images-arm64.tar.gz` | ~1.7 GB | arm64 镜像 tar（14 个） | 鲲鹏/arm 服务器下这个 |

> **arm64 用户注意**：`opal-proxy_v2.tar` 是 2026-04-14 构建的（本次 buildx QEMU 下 `apt-get update` 连 debian/aliyun 都超时，暂无更新）。它**功能完整**——只是少了 7b09aec 里"从 identity dict 删除死字段 email/name"这个纯内部清理，业务行为一致。如需 100% 同步，建议在原生 arm64 机器上执行 `docker build -t opal-proxy:v2 da-cluster/images/opal-proxy` 后 `docker save` 覆盖即可。

### 快速使用

```bash
# 1. 下载并解压 common（**不管什么平台都要**）
tar xzf aidp-iam-v0.6.0-77f4ba0-source-and-common.tar.gz
cd da-cluster   # tar 里没多一层目录，直接进子目录

# 2. 下载并解压对应平台镜像到同一位置
#    （解压到 cwd 的父目录，让 da-cluster/offline/images/{amd64|arm64}/ 恰好合并）
cd ..
tar xzf /path/to/aidp-iam-v0.6.0-77f4ba0-images-amd64.tar.gz
# 校验：
ls da-cluster/offline/images/amd64/ | wc -l    # 应 >= 11

# 3. 选部署脚本（根据场景）
cd da-cluster

# Kind 本地开发
./scripts/setup.sh

# 标准 K8s（离线，SSH 推镜像到节点）
K8S_NODES="10.0.0.1 10.0.0.2" ./scripts/setup.sh --no-kind

# 华为云 + iSula
KC_HOSTNAME=http://EIP:30080 ./scripts/setup-isula.sh --load-images

# 3. 跑测试验证（仅 Kind 模式自带 mock 后端）
./scripts/test.sh
```

详细步骤（包括接入真实 KB / Rubik / Memory 后端）见 `da-cluster/docs/deploy-and-integration.md`。

## 兼容性与升级

- **数据库 schema 无破坏性改动**；本次只加字段绑定，没删除现有列。
- 从 v0.5.x 升级：照常 `helm upgrade aidp-iam`；重启 `keycloak-proxy`, `pep-proxy`, `resource-sync` 三个 Deployment 即可生效新 CRUD 与 SPI。
- **如果你从旧离线包升级**：一定要用本次 release 的 `keycloak-custom_26.5.2.tar`（旧 tar 是 3 月 20 日的，没 `StructuredGroupMapper` JAR）。
