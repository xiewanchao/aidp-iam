# aidp-iam v1.2.0 — KB Permission Matrix Reorg + Flexible ID Extraction

> Branch: `feature/envoy-gateway-migration` · Commit: `c164162`
>
> 继续向 1.0 生产就绪靠拢。本次重点是 **KB 权限点按业务功能重组**（对齐
> `diagrams/api-specs/knowledgebase/apioption.md`）、支持**请求端和响应端字段名不一致**
> 的场景（KB：请求 `kbs_id`，响应 `data.KDSID`），以及全链路对新 api.md 的回归。

## 与 v0.6.0 的差异

| 领域 | 改动 |
|-----|-----|
| **KB permission_groups** | 22 条扁平功能点 → 按 apioption.md 的 **7 大类 × 读/编辑 = 12 条**（+1 `kb_admin_full`，共 13 个 kb 功能点） |
| **resource_patterns 新列** | 增加 `response_id_field VARCHAR(128)`，可空 override 响应端 id 字段名；解决 KB "请求 `kbs_id` / 响应 `data.KDSID`" 的命名不对称问题；Rubik/Memory **零影响** |
| **KB seed id_field** | `KDSID` → `kbs_id`（对齐 api.md 请求字段）；POST `/knowledge_bases` 单独设 `response_id_field='data.KDSID'` |
| **mock-kb 响应结构** | `/add` 现在返回嵌套 `{"data": {"KDSID": ...}}`（严格按 api.md），`/modify` `/remove` 只收 `kbs_id` |
| **测试同步迁移** | `test-kb-full.sh` ~40 处 `KDSID` → `kbs_id` / `data.KDSID`；`test.sh` Section 10 同步 |
| **resource_actions 新增** | `POST /knowledge_bases/files` (exact) + `POST /knowledge_bases/files/history` → `read/viewer`（api.md 把列表查询从 GET 改 POST） |
| **KB 七大功能类 matrix（api-spec-v2.xlsx Sheet 2+4）** | 46→36 权限点，137→134 业务接口矩阵行 |

## 七大 KB 功能类

| # | permission_group | 绑定 Keycloak 组 |
|---|-----------------|---------------|
| 1 | `kb_browse` / `kb_edit` | all-users（编辑走 resource_acl 再过滤） |
| 2 | `kb_model_view` / `kb_model_edit` | **kb-admins** 独占 |
| 3 | `kb_prompt_view` | all-users（问答场景要用） |
| 3 | `kb_prompt_edit` | kb-admins |
| 4 | `kb_jargon_view` / `kb_jargon_edit` | kb-admins 独占 |
| 5 | `kb_conv_view` / `kb_conv_edit` | all-users |
| 6 | `kb_retrieval` | all-users |
| 7 | `kb_jargon_bind` | kb-admins（同时动 KB 和全局术语库） |

## 二级资源鉴权策略

继续保持**父继承**模型（`/mappings/*`、`/files/*` 下的子资源不单独写 ACL，继承所属 KB 的权限）。`apioption.md` 没引入"单独分享某个 mapping/某个 file"的语义，父继承已足够。

已知小 gap：`POST /mappings/remove` 请求体只带 `kbs_dm_id` 不带 `kbs_id`，无法找到父 KB —— 假设前端按新 api.md 补齐 `kbs_id` 即可；若将来真要走"二级资源独立 ACL"再改，已有扩展点（在 resource-sync 里给 mapping 独立 resource_type）。

## 测试

从零重跑（清 seed + re-init + rollout restart 4 个服务）后：

| suite | 结果 |
|-------|-----|
| test.sh（含 Section 10 ACL 级联测试 + 27/28 KB+CRUD CRUD 新测试） | **129/129** ✅ |
| test-kb-full.sh | **69/69** ✅ |
| **合计** | **198/198** ✅ |

## 发布包下载与使用

GitHub 单 asset 2 GB 上限，拆成 3 个：

| 文件 | 大小 | 内容 | 必下？ |
|------|-----|------|------|
| `aidp-iam-v1.2.0-source-and-common.tar.gz` | ~60 MB | 源码 + helm charts + **CRDs** + 脚本 + 文档 | **✅ 必下** |
| `aidp-iam-v1.2.0-images-amd64.tar.gz` | ~1.5 GB | amd64 镜像 tar（11 个） | x86_64 服务器下这个 |
| `aidp-iam-v1.2.0-images-arm64.tar.gz` | ~1.8 GB | arm64 镜像 tar（12 个） | 鲲鹏/arm 服务器下这个 |

CRDs 已随 common 包压缩，解压后在 `da-cluster/offline/crds/`：
- `gateway-api-v1.4.1-experimental.yaml`（Gateway API 实验通道）
- `gateway.envoyproxy.io_*.yaml`（Envoy Gateway 8 个 CRD）

### 快速部署

```bash
# 1. 解压 common 包（每个平台都需要）
tar xzf aidp-iam-v1.2.0-source-and-common.tar.gz

# 2. 解压对应平台的 images 包到同一位置，与 common 合并
tar xzf aidp-iam-v1.2.0-images-amd64.tar.gz     # 或 -arm64

# 3. 部署脚本（根据场景选择）
cd da-cluster
./scripts/setup.sh                               # Kind 本地
./scripts/setup.sh --no-kind                     # 标准 K8s 离线
KC_HOSTNAME=http://EIP:30080 ./scripts/setup-isula.sh --load-images   # 华为 iSula
```

详见 `da-cluster/docs/deploy-and-integration.md`。

## 升级指南（从 v0.6.0）

1. `helm upgrade aidp-iam da-cluster/charts/aidp-iam -n aidp-iam --reuse-values`
2. **对 schema 执行 ALTER**（pod 会自动跑，但手动保险）：
   ```bash
   kubectl -n keycloak exec postgres-0 -c postgres -- \
     psql -U keycloak -d iam -c \
     "ALTER TABLE resource_patterns ADD COLUMN IF NOT EXISTS response_id_field VARCHAR(128) DEFAULT NULL;"
   ```
3. 重新跑 init job（清旧 KB 权限点，装新的 13 个）：
   ```bash
   kubectl -n keycloak delete job keycloak-init
   helm upgrade aidp-iam da-cluster/charts/aidp-iam -n aidp-iam --reuse-values
   ```
4. Rollout restart: `keycloak-proxy`、`pep-proxy`、`resource-sync`、`mock-kb`（如果你用 mock）。

## 兼容性

- `resource_patterns.response_id_field` 新列是 **nullable**，旧 pattern 行 NULL fallback 到 `id_field`；Rubik / Memory 现有 pattern 零改动。
- 前端：如果之前发 `{"KDSID": "..."}` 做 KB CRUD，需改成 `{"kbs_id": "..."}`；响应解析从 `KDSID` 改成 `data.KDSID`。`diagrams/api-specs/knowledgebase/api.md` 是最终契约。
