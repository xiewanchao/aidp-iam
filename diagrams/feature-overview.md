# AIDP IAM 特性概述与业务场景

> 本章节与需求分析说明书的"特性概述"章节相同。

---

## 1 特性一览

AIDP IAM 是面向 AI 数据平台的统一身份认证与访问控制系统，为平台上多个业务应用（知识库、记忆库、模型服务等）提供**认证、鉴权、资源权限管理**的完整能力，使业务应用本身无需实现任何安全逻辑。

核心特性如下：

| 编号 | 特性名称 | 优先级 | 一句话描述 |
|------|----------|--------|-----------|
| 1 | 身份认证与应用注册 | P0 | 基于 Keycloak 的用户/组认证 + 应用 License 管理 |
| 2 | 路径级鉴权 | P0 | OPA 策略引擎按路径前缀 + 用户组进行访问控制 |
| 3 | 资源 ACL 自动同步 | P0 | 业务创建/删除资源时自动维护 owner ACL 记录 |
| 4 | 资源级鉴权 | P0 | 基于 resource_acl 的 owner/contributor/viewer 三级权限检查 |
| 5 | 资源权限分享 | P1 | 资源 owner 将资源共享给其他用户或组 |
| 6 | 列表过滤与分页 | P1 | 按用户可见资源 ID 过滤列表，注入分页计数 |
| 7 | 可靠性重试队列 | P1 | ACL 写入失败时指数退避重试，保证最终一致 |
| 8 | 应用初始化与网关集成 | P1 | Helm 部署时自动初始化数据 + 配置 Envoy Gateway 路由策略 |
| 9 | 外部 IdP 联邦认证 | P1 | SAML/OIDC 企业 SSO 接入，首次登录自动创建影子用户 |
| 10 | 集成测试套件 | P1 | 覆盖全部特性的端到端自动化测试 |
| 11 | 生产部署（Helm / TLS / 可观测性） | P2 | 离线 Helm 部署、证书管理、链路追踪 |
| 12 | API Key 认证与管理 | P1 | 服务间调用的机器身份认证，不依赖 OIDC 交互 |

---

## 2 业务场景详述

### 2.1 身份认证与应用注册

#### 2.1.1 场景触发条件及对象

| 维度 | 说明 |
|------|------|
| 角色 | **平台管理员**（admins 组成员）、**普通用户**（all-users 组成员） |
| 触发条件 | 管理员首次部署后需要注册业务应用；用户通过浏览器或 CLI 访问任何受保护接口时需先完成认证 |
| 技能要求 | 管理员需了解应用的路径前缀（如 `/knowledgebase/`）和资源结构；普通用户仅需输入用户名/密码或通过企业 SSO 跳转 |
| 使用工具 | 管理员：管理控制台 UI 或 cURL 调用 `/api/v1/apps`；用户：浏览器（Keycloak 登录页）或程序化 OIDC 流程 |

#### 2.1.2 使用时间及频度

- **应用注册**：系统初始化或新应用上线时，频率极低（每月 0~2 次）。
- **用户认证**：每次会话开始时触发，JWT 有效期内（默认 5 分钟）自动刷新。日常并发用户 10~500 人，每人每天认证 1~5 次。

#### 2.1.3 场景与关键任务

| 场景 | 子场景 | 关键任务操作 |
|------|--------|-------------|
| 应用注册 | 新增应用 | 管理员调用 `POST /api/v1/apps`，填写 `app_name`、`path_prefix`，系统自动创建 `{app}-admins` 组和默认 `resource_patterns` |
| | 停用/启用应用 | 管理员调用 `PUT /api/v1/apps/{app_name}` 设置 `enabled=false`，OPA 策略立即拦截该应用所有路径请求 |
| 用户认证 | 密码登录 | 用户在 Keycloak 登录页输入凭证 → 获取含 `groups`/`group_ids` 的 JWT |
| | SSO 登录 | 用户被重定向至企业 IdP → SAML 断言 → Keycloak 自动创建影子用户并加入 `all-users` → 签发 JWT |
| | Token 刷新 | 前端在 JWT 过期前用 Refresh Token 静默换取新 Access Token |

---

### 2.2 路径级鉴权

#### 2.2.1 场景触发条件及对象

| 维度 | 说明 |
|------|------|
| 角色 | **Envoy Gateway**（系统组件，自动调用）、**管理员**（配置路径规则） |
| 触发条件 | 每一个经过 Gateway 的 HTTP 请求都自动触发路径级鉴权 |
| 技能要求 | 管理员需理解"路径前缀 + 用户组"的访问控制模型；普通用户无感知 |
| 使用接口 | 系统侧：Envoy ext_authz gRPC → pep-proxy → OPA；管理侧：`/api/v1/path-rules` CRUD |

#### 2.2.2 使用时间及频度

- **鉴权检查**：每次 HTTP 请求触发，频率等于系统总 QPS（日常 100~10,000 req/s）。
- **规则变更**：管理员偶尔调整，频率极低（每周 0~3 次）。规则变更后 ≤30 秒生效（bundle 刷新周期）。

#### 2.2.3 场景与关键任务

| 场景 | 子场景 | 关键任务操作 |
|------|--------|-------------|
| 请求鉴权 | 管理员访问管理接口 | `admins` 组成员访问 `/api/v1/*` 或 `/acl/v1/*` → OPA 直接放行 |
| | 普通用户访问业务路径 | 请求路径匹配 `path_rules` 中某条规则 → 检查 `required_group in user.groups` → 放行或拒绝 |
| | 普通用户访问未保护路径 | 路径不在任何 `path_rules` 中 + 非管理路径 + 用户在 `all-users` 组 → 放行 |
| | 应用被停用 | 请求路径匹配已停用应用的 `path_prefix` → 直接拒绝，无论用户组 |
| 规则管理 | 创建路径规则 | 管理员调用 `POST /api/v1/path-rules`，指定 `path_prefix` + `required_group` |
| | 删除路径规则 | 管理员调用 `DELETE /api/v1/path-rules/{id}`，规则 ≤30 秒后从 OPA 移除 |

---

### 2.3 资源 ACL 自动同步

#### 2.3.1 场景触发条件及对象

| 维度 | 说明 |
|------|------|
| 角色 | **Envoy ext_proc**（系统组件）、**业务应用后端**（被观察对象） |
| 触发条件 | 业务应用返回 `POST + 201 Created`（创建资源）或 `DELETE + 2xx`（删除资源）时自动触发 |
| 技能要求 | 业务应用开发者需在应用注册时配置 `resource_patterns`（id_source、id_field 等），之后完全自动化 |
| 使用接口 | ext_proc gRPC 流式处理（port 8082），业务应用无需主动调用 |

#### 2.3.2 使用时间及频度

- **ACL 写入**：与业务资源创建/删除频率一致。典型场景：知识库系统每天新建 10~100 个文档，每次新建触发一次 ACL 写入。
- **失败重试**：仅在 DB 故障时触发，正常运行频率为 0。

#### 2.3.3 场景与关键任务

| 场景 | 子场景 | 关键任务操作 |
|------|--------|-------------|
| 资源创建 | POST + 201 匹配 resource_pattern | ext_proc 拦截响应 → 按 `id_source`（path/body/query）提取 `resource_id` → 写入 `resource_acl(permission=owner, subject_id=当前用户)` |
| | POST + 201 不匹配任何 pattern | 静默忽略，不写 ACL |
| | POST + 非 201（如 200、400） | 不触发 ACL 写入（仅 201 表示资源新建成功） |
| 资源删除 | DELETE + 2xx 匹配 resource_pattern | 从 URL 提取 `resource_id` → `DELETE FROM resource_acl WHERE resource_id = ?`（级联删除所有权限行） |
| 写入失败 | DB 连接异常 | ACL 写入失败 → 记入 `pending_acl` 表 → 后台 worker 指数退避重试（5s → 10s → ... → 2560s，最多 10 次） |

---

### 2.4 资源级鉴权

#### 2.4.1 场景触发条件及对象

| 维度 | 说明 |
|------|------|
| 角色 | **pep-proxy**（系统组件，自动执行）；**普通用户**（被检查对象） |
| 触发条件 | 请求通过路径级鉴权后，若匹配 `resource_patterns` 中已注册的资源路径，自动触发资源级检查 |
| 技能要求 | 用户无感知；系统管理员需理解权限层级 owner > contributor > viewer |
| 使用接口 | pep-proxy 内部逻辑，查询 `resource_acl` 表 |

#### 2.4.2 使用时间及频度

- 每次访问具体资源（如 `GET /knowledgebase/kb-123`）时触发。频率与用户对资源的操作频率一致，日常 50~5,000 req/s。
- 访问集合路径（如 `GET /knowledgebase/`）不触发资源级鉴权（走列表过滤场景）。

#### 2.4.3 场景与关键任务

| 场景 | 子场景 | 关键任务操作 |
|------|--------|-------------|
| 读取资源 | GET /app/resource-123 | 检查 `resource_acl` 中用户对该资源的权限 ≥ viewer → 放行 |
| 修改资源 | PUT/PATCH /app/resource-123 | 权限 ≥ contributor → 放行 |
| 删除资源 | DELETE /app/resource-123 | 权限 = owner → 放行 |
| 子资源继承 | GET /app/resource-123/comments/5 | 无独立 ACL 条目时，查父资源 `resource-123` 的权限 → 继承 |
| 无权限 | 用户无任何 ACL 记录 | 返回 403 Forbidden |
| 管理员豁免 | admins 组成员 | 跳过资源级鉴权，直接放行 |

---

### 2.5 资源权限分享

#### 2.5.1 场景触发条件及对象

| 维度 | 说明 |
|------|------|
| 角色 | **资源 owner**（发起分享）、**被分享者**（用户或用户组） |
| 触发条件 | 资源 owner 希望将自己拥有的资源授权给他人查看或编辑 |
| 技能要求 | owner 需知道被分享者的 user_id 或 group_name，以及权限级别（viewer/contributor/owner） |
| 使用接口 | `/acl/v1/resources/{resource_id}/permissions` CRUD |

#### 2.5.2 使用时间及频度

- 用户主动操作，频率取决于协作需求。典型：每个资源平均分享给 1~5 人，每天系统总分享操作 10~200 次。
- 取消分享（DELETE）频率更低，约为分享频率的 10%。

#### 2.5.3 场景与关键任务

| 场景 | 子场景 | 关键任务操作 |
|------|--------|-------------|
| 分享资源 | owner 授权他人 | `POST /acl/v1/resources/{id}/permissions`，body 指定 `subject_type=user/group`、`subject_id`、`permission=viewer/contributor/owner` |
| 查看已分享 | 查看资源的全部权限条目 | `GET /acl/v1/resources/{id}/permissions?app_name=x&resource_type=y` |
| 变更权限 | 将 viewer 提升为 contributor | `PUT /acl/v1/resources/{id}/permissions/{acl_id}`，body `{permission: "contributor"}` |
| 取消分享 | 撤销某用户的访问权 | `DELETE /acl/v1/resources/{id}/permissions/{acl_id}` |
| 越权尝试 | 非 owner 尝试分享 | 系统返回 403，只有 owner 可以管理权限 |

---

### 2.6 列表过滤与分页

#### 2.6.1 场景触发条件及对象

| 维度 | 说明 |
|------|------|
| 角色 | **ext_proc**（系统组件）、**业务后端**（读取注入的 header） |
| 触发条件 | 用户发起 GET 请求访问资源集合路径（如 `GET /knowledgebase/`），且该路径匹配 `resource_patterns` 的集合路径模式 |
| 技能要求 | 业务后端开发者需读取 `X-Allowed-Ids`（逗号分隔的可见 ID 列表）和 `X-Allowed-Total`（总数）header，在 SQL 中追加 `WHERE id IN (...)` |
| 使用接口 | ext_proc 请求阶段注入 header，业务后端被动读取 |

#### 2.6.2 使用时间及频度

- 每次列表/搜索请求触发。典型：用户每天浏览知识库列表 5~20 次，系统总频率 50~2,000 req/s。
- 分页参数默认 `page=1, size=20`，`size` 上限 100。

#### 2.6.3 场景与关键任务

| 场景 | 子场景 | 关键任务操作 |
|------|--------|-------------|
| 列表查询 | 用户浏览资源列表 | ext_proc 查 `resource_acl` → 注入 `X-Allowed-Ids: id1,id2,...` + `X-Allowed-Total: N` → 业务后端按 header 过滤 |
| 空列表 | 用户无任何可见资源 | `X-Allowed-Ids` 为空，`X-Allowed-Total: 0`，后端返回空数组 |
| 分页翻页 | 用户请求第 2 页 | `GET /app/?page=2&size=20` → ext_proc 查 ACL 按分页偏移返回对应 ID 切片 |
| 管理员列表 | admins 组成员 | 不注入过滤 header，后端返回全量数据 |

---

### 2.7 可靠性重试队列

#### 2.7.1 场景触发条件及对象

| 维度 | 说明 |
|------|------|
| 角色 | **retry_worker**（系统后台进程），完全自动化 |
| 触发条件 | 资源 ACL 自动同步写入因 DB 故障、网络超时等原因失败，失败记录写入 `pending_acl` 表 |
| 技能要求 | 运维人员需了解 `pending_acl` 表用于排障；日常运行无需人工干预 |
| 使用接口 | 无对外接口，纯后台任务 |

#### 2.7.2 使用时间及频度

- worker 每 5 秒轮询一次 `pending_acl` 表。
- 正常运行时 `pending_acl` 为空，无实际重试发生。
- DB 故障恢复后批量重试，频率取决于故障期间积压量（通常 0~100 条）。

#### 2.7.3 场景与关键任务

| 场景 | 子场景 | 关键任务操作 |
|------|--------|-------------|
| 正常重试 | DB 临时不可用后恢复 | worker 取出 `next_retry < NOW()` 的记录 → 重新执行 ACL 写入 → 成功则删除 `pending_acl` 行 |
| 持续失败 | DB 长时间不可用 | 每次重试失败后 `retry_count += 1`、`next_retry` 按指数退避延后（5s → 10s → 20s → ... → 2560s） |
| 彻底放弃 | 达到最大重试次数（10 次） | 不再重试，记录保留在 `pending_acl` 供人工排查 |

---

### 2.8 应用初始化与网关集成

#### 2.8.1 场景触发条件及对象

| 维度 | 说明 |
|------|------|
| 角色 | **运维工程师 / DevOps**（执行部署）、**Helm**（编排工具） |
| 触发条件 | 首次部署或版本升级执行 `helm install/upgrade` 时触发 |
| 技能要求 | 运维人员需熟悉 Helm + kubectl + Kind/K8s 基本操作 |
| 使用接口 | `./scripts/setup.sh`（一键部署）、`helm install aidp-iam charts/aidp-iam` |

#### 2.8.2 使用时间及频度

- 首次部署 1 次，后续版本升级每月 0~2 次。
- 部署过程 3~10 分钟（取决于镜像拉取速度）。

#### 2.8.3 场景与关键任务

| 场景 | 子场景 | 关键任务操作 |
|------|--------|-------------|
| 首次部署 | 全新环境 | `setup.sh` 创建 Kind 集群 → 部署 Keycloak → init-job 初始化 aidp realm/groups/client → 部署 pep-proxy/OPA/resource-sync → 配置 Gateway 路由 |
| 版本升级 | 已有集群 | `helm upgrade` → init-job 幂等跳过已有数据 → 滚动更新各组件 |
| 离线部署 | 无网络环境 | 从 `offline/` 目录加载镜像 tar + Helm charts + CRDs → `setup.sh --no-kind` |
| 初始数据 | 默认应用注册 | init-job 向 `apps` 表插入预配置的应用（如 httpbin）+ `resource_patterns` + `path_rules` |

---

### 2.9 外部 IdP 联邦认证

#### 2.9.1 场景触发条件及对象

| 维度 | 说明 |
|------|------|
| 角色 | **平台管理员**（配置 IdP）、**企业员工**（通过 SSO 登录） |
| 触发条件 | 企业客户要求员工使用现有企业 AD/LDAP/SAML IdP 登录，不在 IAM 系统中单独建账 |
| 技能要求 | 管理员需从企业 IdP 导出 SAML Metadata XML；普通员工仅需点击"企业登录"按钮 |
| 使用接口 | `POST /api/v1/{realm}/idp/saml/import`（上传 Metadata）、`GET/PUT/DELETE /api/v1/{realm}/idp/instances/{alias}` |

#### 2.9.2 使用时间及频度

- **IdP 配置**：一次性操作或企业更换 IdP 时操作，频率极低（每季度 0~1 次）。
- **SSO 登录**：每个企业员工每天首次访问时触发，频率等同用户数（10~500 次/天）。

#### 2.9.3 场景与关键任务

| 场景 | 子场景 | 关键任务操作 |
|------|--------|-------------|
| 导入 IdP | 上传 SAML Metadata | 管理员调用 `POST /{realm}/idp/saml/import`，上传 XML → 系统自动创建 Keycloak IdP 实例 + 默认 Mapper |
| 配置 Mapper | 属性映射 | 管理员配置 SAML Assertion 中的属性（如 email、department）到 Keycloak 用户属性的映射 |
| 首次登录 | 企业员工 SSO | 员工点击企业登录 → 跳转企业 IdP → SAML 断言回传 → Keycloak 自动创建影子用户 → 加入 `all-users` 默认组 → 签发 JWT |
| 停用 IdP | 解除 SSO 绑定 | 管理员删除 IdP 实例，已有影子用户保留但无法再通过 SSO 登录 |

---

### 2.10 API Key 认证与管理

#### 2.10.1 场景触发条件及对象

| 维度 | 说明 |
|------|------|
| 角色 | **平台管理员**（创建/管理 Key）、**外部服务/脚本**（使用 Key 调用 API） |
| 触发条件 | 需要服务间调用（如定时任务、CI/CD 流水线、第三方系统集成）而无法走浏览器 OIDC 流程时 |
| 技能要求 | 管理员需了解 Key 的 `allowed_paths` 白名单机制；服务开发者仅需在请求中添加 `X-API-Key` header |
| 使用接口 | 管理侧：`/api/v1/{tenant}/api-keys` CRUD；使用侧：请求 header `X-API-Key: ak_xxx` |

#### 2.10.2 使用时间及频度

- **Key 管理**：创建/轮换/撤销操作频率很低（每月 1~10 次）。
- **Key 使用**：自动化服务 7×24 持续调用，频率取决于业务量（10~10,000 req/s）。

#### 2.10.3 场景与关键任务

| 场景 | 子场景 | 关键任务操作 |
|------|--------|-------------|
| 创建 Key | 管理员为服务创建 | `POST /api/v1/{tenant}/api-keys`，指定 `app_name`、`allowed_paths`、`expires_at`（可选）→ 返回 `ak_xxx` 明文（仅此一次） |
| 使用 Key | 服务调用业务 API | 请求携带 `X-API-Key: ak_xxx` → pep-proxy 校验 hash/enabled/expiry/allowed_paths → 合成身份 → OPA + resource_acl 鉴权 |
| 轮换 Key | 定期更新凭证 | `POST /api/v1/{tenant}/api-keys/{id}/rotate` → 生成新 Key，保留原 `subject_id`（下游 ACL 不受影响）→ 返回新明文 |
| 停用 Key | 紧急撤销 | `PUT /api/v1/{tenant}/api-keys/{id}` 设 `enabled=false` → 立即生效，使用该 Key 的请求即刻返回 401 |
| Key 过期 | 到达 expires_at | 系统自动拒绝，无需人工操作 |
| 路径越权 | 请求路径不在 allowed_paths 内 | 返回 403，Key 本身有效但不被授权访问该路径 |

---

## 3 场景交互关系

一次典型的业务请求涉及多个特性的串联协作：

**场景一：用户访问具体资源**
```
用户请求 → [身份认证] → [路径级鉴权] → [资源级鉴权] → 业务后端处理
                                                            ↓
                                                      业务返回 201
                                                            ↓
                                                    [ACL 自动写入]
                                                            ↓ (失败)
                                                    [重试队列]
```

**场景二：用户浏览资源列表**
```
用户请求 → [身份认证] → [路径级鉴权] → [列表过滤注入] → 业务后端按 header 过滤返回
```

**场景三：资源 owner 分享资源**
```
owner 请求 → [身份认证] → [路径鉴权 /acl/v1/*] → [分享 API] → 被分享者下次访问时 [资源级鉴权] 放行
```

**场景四：服务间 API Key 调用**
```
服务请求 → [API Key 认证] → [路径级鉴权] → [资源级鉴权] → 业务处理
```

**场景五：系统首次部署与企业 SSO 接入**
```
首次部署 → [Helm 初始化] → [配置企业 IdP] → [员工 SSO 首次登录]
```
