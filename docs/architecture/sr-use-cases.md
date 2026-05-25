# SR 用例规格说明

> 版本：v1.0 | 日期：2026-04-09

---

## SR01 用户认证、应用注册与外部 IdP 接入

（原 SR01 + SR09 合并）

### 1. 简要说明

基于 Keycloak 的 groups 模型实现用户认证体系，支持本地登录和外部 IdP（SAML/OIDC）联邦登录。用户登录后获取带 groups 的 JWT。平台管理员可注册应用、控制 License、配置外部 IdP。

### 2. Actor

| Actor | 说明 |
|-------|------|
| 管理员（admins） | 注册应用、管理 License、配置外部 IdP、管理用户/组 |
| 普通用户（all-users） | 登录获取 JWT |
| 外部 IdP 用户 | 通过 SSO 首次登录，自动创建账号 |
| init-job | 系统部署时自动初始化基础数据 |

### 3. 前置条件

- PostgreSQL 和 Keycloak 已部署并可访问
- Keycloak Admin API 可用

### 4. 最小保证

- 用户登录失败不会破坏已有数据
- 应用注册失败不会产生半成品数据（事务保证）
- 外部 IdP 配置错误不影响本地登录

### 5. 成功保证

- 用户登录后 JWT 包含 groups 字段
- apps / resource_patterns 表可正常 CRUD
- 注册应用时自动创建 `{app}-admins` 组
- License 开关 `enabled` 即时生效
- 外部 IdP 用户通过 SSO 登录成功，自动创建用户并加入 all-users 组
- JWT 包含正确的 groups

### 6. 触发事件

- 用户登录（本地或外部 IdP SSO）
- 管理员注册应用 / 切换 License
- 管理员配置外部 IdP
- 外部用户首次 SSO 登录
- 系统部署（init-job）

### 7. 主成功场景

**用户认证与初始化：**
1. init-job 创建 `admins` + `all-users`（aidp realm），注册应用时自动创建 `{app}-admins` 组
2. JWT Protocol Mapper 配置为 group-mapper（groups + group_ids 写入 JWT）
3. `all-users` 设为默认组
4. 用户登录获取 JWT，包含 `groups` 字段

**应用注册：**
5. 管理员调 `POST /api/v1/apps` 注册应用
6. 系统自动写入 apps + resource_patterns + 创建 `{app}-admins` 组
7. 管理员调 `PUT /api/v1/apps/{app}` 切换 License `enabled`

**外部 IdP 接入：**
8. 管理员获取外部 IdP 的 SAML metadata XML
9. 管理员调 `POST /{realm}/idp/saml/import` 导入元数据
10. 管理员调 `POST /{realm}/idp/saml/instances` 创建 IdP 实例并配置属性映射
11. 外部用户访问登录页 → 选择外部 IdP 登录 → 跳转认证 → 回调 Keycloak
12. Keycloak 自动创建用户（first broker login flow），用户自动加入 `all-users`
13. 签发 JWT（包含 `groups: ["all-users"]`）

### 8. 扩展场景（包括异常场景）

- 8a. init-job 重复执行：使用 `ON CONFLICT DO NOTHING`，幂等不覆盖已修改数据
- 8b. 应用注册时 Keycloak 创建组失败：事务回滚，apps 表不写入
- 8c. 外部 IdP metadata 格式错误：返回 400，提示格式不合法
- 8d. 外部 IdP 认证成功但属性映射缺失：用户创建成功但缺少必要属性，管理员可后续修复
- 8e. JWT 过期：用户需重新登录或 refresh token

### 约束

- 系统面向单一客户销售，Tenant = 部门
- apps、resource_patterns、path_rules 为系统级表（无 tenant_id）
- groups 模型替代 roles 模型

### 规格

| 项目 | 规格 |
|------|------|
| 租户数量 | ≤200 |
| 单租户用户数 | ≤10,000 |
| 应用数量 | ≤100 |
| 支持认证协议 | SAML 2.0、OIDC 1.0 |

### 升级

- 支持滚动升级，init-job 使用 `ON CONFLICT DO NOTHING`，不覆盖已有数据
- Keycloak 多副本部署，升级期间不中断登录服务

### 可靠性

- Keycloak ≥2 副本，单实例故障已登录用户不受影响（pep-proxy 缓存 JWKS）
- PostgreSQL 建议主备部署

### 性能

- 用户登录 JWT 签发 < 500ms
- apps 表查询 < 1ms（数据量小）

### 安全

- JWT 使用 RS256 签名
- JWKS 公钥由 pep-proxy 缓存，不依赖 Keycloak 在线验证
- 默认密码需在首次部署后修改

### 韧性

- Keycloak 宕机：已登录用户不受影响（缓存 JWKS），新用户无法登录
- PostgreSQL 宕机：应用注册不可用，登录不受影响（JWT 由 Keycloak 内部 DB 签发）

### 可服务

- Keycloak 恢复后自动接受新登录
- init-job 可重复执行修复数据

### 可测试

- 用户登录 → JWT 包含 `groups` 字段
- 新用户自动加入 `all-users` 组
- `apps` + `resource_patterns` 表 CRUD 正常
- `{app}-admins` 组自动创建
- License 开关 `enabled` 生效
- SAML metadata 导入正常
- 外部用户 SSO 登录成功，自动创建用户并加入 `all-users`

### 支持的产品

- AI Storage Software suite 26.0.RC1（AIDP 平台）

---

## SR02 路径级鉴权与资源访问鉴权

（原 SR02 + SR04 合并）

### 1. 简要说明

请求经过 Gateway 时，pep-proxy 通过 ext_authz 实现两级鉴权：第一级调 OPA 做路径级鉴权（应用是否启用、路径是否受保护、用户是否在允许的组中）；第二级查 resource_acl 做资源实例级鉴权（用户对具体资源的访问权限，含子资源鉴权和权限-操作映射检查）。pep-proxy 同时提供 path-rules CRUD API。

### 2. Actor

| Actor | 说明 |
|-------|------|
| 普通用户 | 发起业务请求，触发鉴权 |
| 平台管理员 | 配置 path_rules 路径保护规则 |
| pep-proxy | 执行 JWT 验证、调 OPA、查 resource_acl |
| OPA | 路径级策略计算（纯内存） |
| bundle-server | 从 DB 读取 apps + path_rules 推送到 OPA |

### 3. 前置条件

- SR01 完成（apps 表有数据，JWT 包含 groups）
- resource_acl 表已创建
- OPA 和 bundle-server 已部署

### 4. 最小保证

- 鉴权失败返回 403，不泄露内部信息
- OPA 故障时 default deny，不会误放行

### 5. 成功保证

- 所有请求经过路径级鉴权
- app 禁用 → 403
- 受保护路径无对应 group → 403
- 未受保护路径 all-users 放行
- 有资源权限 → 放行，无权限 → 403
- 权限不足（viewer 不能 PUT） → 403
- 子资源操作检查父资源权限

### 6. 触发事件

- 任意 HTTP 请求到达 Gateway
- 管理员配置 path_rules

### 7. 主成功场景

**路径级鉴权：**
1. 用户发起请求 → Gateway 调 ext_authz → pep-proxy
2. pep-proxy 验证 JWT（缓存 JWKS），提取 user_id、tenant_id、groups
3. pep-proxy 调 OPA：检查 app enabled → path_rules → groups
4. 通过 → 进入资源级鉴权

**资源级鉴权：**
5. pep-proxy 用 `apps.path_prefix` 映射请求路径 → `app_name`
6. pep-proxy 匹配 `resource_patterns` → 提取 resource_id 和 resource_type
7. 按路径段数判断：
   - 0 段（POST /v1/kb 或 GET /v1/kb）：放行（创建顶级资源 / 集合请求）
   - 1 段（/v1/kb/kb-001）：查 resource_acl，检查权限-操作映射
   - 2 段以上（/v1/kb/kb-001/docs）：查父资源 resource_acl
   - 不匹配 resource_patterns：放行
8. 权限-操作映射检查：GET→viewer，PUT/PATCH→contributor，DELETE→owner
9. 通过 → 注入 `X-Auth-User-Id` / `X-Auth-Tenant` / `X-Auth-Groups` headers
10. 拒绝 → 返回 403

**path-rules 管理：**
11. 管理员通过 pep-proxy API 配置 path_rules（`POST/GET/PUT/DELETE /api/v1/path-rules`）
12. bundle-server 读取 apps + path_rules → 生成 OPA bundle → 推送到 OPA

### 8. 扩展场景（包括异常场景）

- 8a. OPA 不可用：pep-proxy 调用超时 → default deny → 403
- 8b. resource_acl 查询超时：可降级为只查 OPA 放行，或返回 503
- 8c. JWT 签名无效：返回 401
- 8d. JWT 过期：返回 401
- 8e. bundle-server 宕机：OPA 使用最后一次拉取的 bundle，业务不受影响
- 8f. 并发请求同一资源：resource_acl 查询无写操作，无并发冲突

### 约束

- OPA 只做路径级策略（apps + path_rules），不做资源实例级鉴权（数据量太大不适合加载到内存）
- resource_acl 由 pep-proxy 直接查 PostgreSQL（有联合索引，毫秒级）
- pep-proxy 启动时加载 apps 和 resource_patterns 到内存

### 规格

| 项目 | 规格 |
|------|------|
| OPA 路径鉴权延迟 | 微秒级（纯内存计算） |
| 资源级鉴权延迟（含 DB 查询） | ≤10ms（P99，联合索引） |
| bundle 推送延迟 | 秒级（策略变更后几秒生效） |
| resource_acl 数据量 | ≤300 万条 |

### 升级

- 支持滚动升级，pep-proxy 多副本部署，逐个重启不中断鉴权
- OPA bundle 格式变更时，先推新 bundle 再升级 pep-proxy

### 可靠性

- pep-proxy ≥2 副本，单实例故障不影响鉴权
- OPA ≥2 副本，纯内存服务启动快恢复快
- bundle-server 宕机不影响业务（OPA 用旧数据）

### 性能

- OPA 路径鉴权：微秒级（纯内存）
- resource_acl 查询：≤10ms（联合索引等值查询）
- pep-proxy 总鉴权延迟（含 JWT 验证 + OPA + DB）：≤20ms（P99）

### 安全

- default deny 策略：OPA 故障时拒绝所有请求
- JWT 验证在鉴权最前端执行，无效 JWT 不进入后续逻辑
- 未授权请求不会到达后端应用

### 韧性

- OPA 宕机：全部请求 403（安全但全挂），建议 ≥2 副本
- PostgreSQL 宕机：资源级鉴权失败，可降级为只查 OPA 或返回 503
- bundle-server 宕机：OPA 用旧 bundle，新配置暂时不生效

### 可服务

- OPA 恢复后自动接受查询
- pep-proxy 启动时自动加载 apps 和 resource_patterns
- bundle-server 恢复后自动推送最新数据

### 可测试

- app disabled → 该应用所有请求 403
- 受保护路径无对应 group → 403
- 未受保护路径 + all-users → 放行
- master-admins / tenant-admins 访问 `/api/v1/*` → 放行
- owner GET/PUT/DELETE → 放行
- viewer GET → 放行，PUT → 403
- contributor GET/PUT → 放行，DELETE → 403
- 无权限用户 → 403
- 子资源操作 → 检查父资源权限
- path-rules CRUD API 正常

### 支持的产品

- AI Storage Software suite 26.0.RC1（AIDP 平台）

---

## SR03 资源 ACL 管理（自动同步、权限修改与可靠性保障）

（原 SR03 + SR05 + SR07 合并）

### 1. 简要说明

通过 ext_proc 在 Gateway 响应阶段自动拦截资源创建（POST+201）和删除（DELETE+2xx）响应，实现 ACL 自动同步；提供 ACL 管理 API 支持资源分享/取消分享/权限修改；通过 pending_acl 重试队列保障 ACL 数据最终一致性。

### 2. Actor

| Actor | 说明 |
|-------|------|
| 普通用户 | 创建/删除资源触发 ACL 自动同步 |
| 资源 owner | 通过 ACL API 分享/修改/撤销资源权限 |
| ext_proc（resource-sync:8082） | 拦截 Gateway 响应，自动写入/清理 ACL |
| resource-sync:8080 | 提供 ACL 管理 API |
| 后台重试任务 | 定时重试 pending_acl 中失败的记录 |

### 3. 前置条件

- SR01 完成（apps / resource_patterns 表有数据）
- SR02 完成（pep-proxy 能验证 owner 权限，用于 ACL API 鉴权）
- Gateway ext_proc 策略已绑定

### 4. 最小保证

- ACL 写入失败时记录到 pending_acl，不丢失
- ext_proc 配置 failOpen，resource-sync 故障不阻塞业务请求
- ACL API 操作失败不影响已有权限数据

### 5. 成功保证

- 创建资源后 resource_acl 自动写入 owner 记录
- 删除资源后 resource_acl 中该资源所有记录被清除
- 分享后目标用户/组可按对应权限访问资源
- 修改权限后立即生效
- 撤销分享后目标用户无法访问
- 写入失败时 pending_acl 有记录，后台重试最终成功
- 所有 pending 记录最终被处理（成功写入或达到最大重试次数）

### 6. 触发事件

- 用户创建资源（POST 返回 201）
- 用户删除资源（DELETE 返回 2xx）
- owner 调用 ACL API 分享/修改/撤销
- 定时任务触发 pending_acl 重试（每 5 秒）

### 7. 主成功场景

**ACL 自动同步（创建）：**
1. 用户 `POST /knowledgebase/v1/kb` → 后端返回 `201 { "id": "kb-001" }`
2. Gateway 调 ext_proc → resource-sync:8082
3. ext_proc 检测 POST + 201 + 路径匹配 resource_patterns
4. 从 response body 提取 id，写入 resource_acl（owner=当前用户）
5. 响应正常返回给用户

**ACL 自动同步（删除）：**
6. 用户 `DELETE /knowledgebase/v1/kb/kb-001` → 后端返回 `200`
7. ext_proc 检测 DELETE + 2xx + 路径匹配 resource_patterns
8. 删除 resource_acl 中该 resource_id 的所有记录
9. 响应正常返回给用户

**权限修改（分享）：**
10. owner 调 `POST /acl/v1/resources/kb-001/permissions { subject_id: "lisi", permission: "viewer" }`
11. Gateway ext_authz → pep-proxy 从 URL 提取 resource_id=kb-001，验证请求者是 owner
12. Gateway HTTPRoute → resource-sync:8080
13. resource-sync INSERT INTO resource_acl
14. 李四现在可以 GET kb-001

**可靠性重试：**
15. 定时任务每 5 秒执行 `SELECT FROM pending_acl WHERE next_retry <= NOW() AND retry_count < max_retries`
16. 逐条重试写入/删除 resource_acl
17. 成功 → 删除 pending 记录；失败 → retry_count++，next_retry = 指数退避

### 8. 扩展场景（包括异常场景）

- 8a. ACL 写入 DB 失败：写入 pending_acl，响应仍正常返回给用户（failOpen）
- 8b. pending_acl 也写不进去（极端）：failOpen 放行响应，ACL 丢失，等待定期对账修复
- 8c. resource-sync 整个宕机：业务请求正常（failOpen），ACL 不同步，恢复后 pending 重试 + 对账修复
- 8d. ext_proc 超时（>200ms）：failOpen 放行，ACL 未写入，后台补写
- 8e. 后端返回 201 但实际事务回滚（幽灵资源）：ACL 写入成功但资源不存在，定期对账清理
- 8f. 并发创建和删除同一资源：可能产生孤儿 ACL，定期对账清理
- 8g. 非 owner 调用 ACL API 分享：pep-proxy 返回 403
- 8h. 分享目标用户不存在：resource_acl 写入成功（不校验用户存在性），该 ACL 无实际效果
- 8i. 达到 max_retries（10 次）：不再重试，记录保留，等待人工介入或对账清理

### 约束

- ext_proc 仅拦截 POST+201 和 DELETE+2xx，其他方法/状态码直接放行
- 子资源（POST /v1/kb/kb-001/docs）的创建不触发 ACL 写入，子资源权限继承父资源
- ACL API 路径中包含 resource_id，pep-proxy 从 URL 提取验证 owner，无需读 Body
- resource-sync 两个端口：8080（ACL API，走 Gateway + ext_authz）、8082（ext_proc gRPC，Gateway 响应阶段调用）

### 规格

| 项目 | 规格 |
|------|------|
| resource_acl 数据量 | ≤300 万条 |
| ext_proc ACL 同步延迟 | ≤200ms（P99，含 DB 写入） |
| pending_acl 重试间隔 | 5 秒起，指数退避至 ~42 分钟 |
| 最大重试次数 | 10 次 |

### 升级

- 支持滚动升级，resource-sync 多副本部署，逐个重启
- ext_proc failOpen 保证升级期间业务请求不中断
- pending_acl 表结构变更需提前迁移

### 可靠性

- resource-sync ≥2 副本
- 三道防线保障 ACL 一致性：① ext_proc 实时写入 → ② pending_acl 后台重试 → ③ 定期对账
- resource-sync 宕机 = 业务正常，仅 ACL 同步延迟（架构核心改进）

### 性能

- ext_proc ACL 写入：≤200ms（P99，单次 INSERT）
- ACL API 响应：≤50ms
- pending 重试吞吐：每 5 秒一批，逐条处理

### 安全

- ACL API 由 pep-proxy 鉴权，只有 owner 可分享/修改/撤销
- resource_id 从 URL 提取，不依赖请求体（防篡改）
- failOpen 模式下业务可用但权限延迟生效，非安全降级

### 韧性

- resource-sync 宕机：业务正常（failOpen），ACL 延迟同步
- PostgreSQL 宕机：ext_proc 写入失败 → pending_acl 也失败 → ACL 丢失 → 定期对账修复
- ext_proc 超时：放行响应，ACL 后台补写

### 可服务

- resource-sync 恢复后自动处理 pending_acl 队列
- 定期对账（每天凌晨）自动清理孤儿 ACL、补写缺失 ACL
- 可通过 pending_acl 表查看未处理的 ACL 操作

### 可测试

- POST 创建资源 → resource_acl 自动写入 owner 记录
- DELETE 资源 → 所有 ACL 记录被清理
- DB 超时 → pending_acl 有记录
- ext_proc 仅拦截 POST+201 和 DELETE+2xx
- failOpen 工作正常
- owner 分享 viewer → 目标用户可 GET
- 非 owner 分享 → 403
- 修改权限（viewer → contributor）生效
- 撤销分享 → 目标用户 403
- pending 重试成功后记录被删除
- 指数退避正确执行

### 支持的产品

- AI Storage Software suite 26.0.RC1（AIDP 平台）

---

## SR06 资源列表过滤（分页）

### 1. 简要说明

ext_proc 在请求阶段感知分页参数，查询 resource_acl 按分页取出当前页的资源 ID，注入 `X-Allowed-Ids` 和 `X-Allowed-Total` 请求头，后端读取 Header 查询业务数据并返回分页结果。

### 2. Actor

| Actor | 说明 |
|-------|------|
| 普通用户 | 发起 GET 集合请求（如 GET /v1/kb?page=1&size=20） |
| ext_proc（resource-sync:8082） | 请求阶段查询 resource_acl，注入 Header |
| 后端应用 | 读取 X-Allowed-Ids Header，查询业务数据返回分页结果 |

### 3. 前置条件

- SR03 完成（resource_acl 有数据，ext_proc 基础设施就绪）

### 4. 最小保证

- ext_proc 处理失败时 failOpen 放行，后端收不到 Header 返回空列表
- 不会返回用户无权限的资源

### 5. 成功保证

- 列表结果只包含用户有权限的资源
- 分页准确（total、page、size）
- 每次注入的 ID 数量等于 page size，Header 不会超限

### 6. 触发事件

- 用户 GET 集合路径（如 `GET /knowledgebase/v1/kb?page=1&size=20`）

### 7. 主成功场景

1. 用户 `GET /knowledgebase/v1/kb?page=2&size=20`
2. pep-proxy 路径鉴权通过（集合路径，不查 resource_acl）
3. ext_proc 请求阶段：检测 GET + 集合路径（匹配 resource_patterns 且路径段数=0）
4. ext_proc 从 URL query 解析 page=2, size=20
5. ext_proc 查 resource_acl：LIMIT 20 OFFSET 20，同时查 COUNT 总数
6. ext_proc 注入 `X-Allowed-Ids: kb-021,kb-022,...,kb-040` + `X-Allowed-Total: 328`
7. Gateway 转发到后端
8. 后端读取 Header，查询业务数据，返回分页结果

### 8. 扩展场景（包括异常场景）

- 8a. ext_proc 查询 resource_acl 超时：failOpen 放行，后端收不到 Header，返回空列表
- 8b. 无分页参数：默认 page=1, size=20
- 8c. size 超过 100：按 100 处理
- 8d. page 超出范围：X-Allowed-Ids 为空，X-Allowed-Total 正常，后端返回空列表
- 8e. 非 GET 请求或非集合路径：ext_proc 不处理，直接放行

### 约束

- ext_proc 仅处理 GET + 集合路径（匹配 resource_patterns 且路径段数=0）
- page size 上限 100
- 排序方式为 resource_acl.created_at DESC（按授权时间倒序）

### 规格

| 项目 | 规格 |
|------|------|
| 单页最大 ID 数量 | 100 |
| 默认分页 | page=1, size=20 |
| ext_proc 请求阶段延迟 | ≤20ms（P99，两次 SQL 查询） |

### 升级

- 支持滚动升级，ext_proc failOpen 保证升级期间列表返回空而非报错

### 可靠性

- resource-sync 宕机：failOpen，列表返回空，单资源访问不受影响

### 性能

- resource_acl 分页查询 + COUNT：≤20ms（联合索引 + LIMIT/OFFSET）

### 安全

- 不会返回用户无权限的资源 ID
- Header 注入由 ext_proc 完成，后端不可伪造

### 韧性

- ext_proc 故障：failOpen 放行，后端降级返回空列表

### 可服务

- resource-sync 恢复后自动恢复列表过滤功能

### 可测试

- X-Allowed-Ids header 注入当前页 ID
- X-Allowed-Total header 注入总数
- 分页参数正确解析（page、size、默认值）
- 列表结果只包含有权限的资源
- 组分享的资源也包含在内
- size 上限 100，超过按 100 处理

### 支持的产品

- AI Storage Software suite 26.0.RC1（AIDP 平台）

---

## SR08 应用初始化与 Gateway 集成

### 1. 简要说明

系统首次部署时通过 init-job 自动初始化默认数据（应用、资源规则、路径保护、组），配置 Gateway 路由规则和 ext_authz + ext_proc 策略绑定。

### 2. Actor

| Actor | 说明 |
|-------|------|
| 运维人员 | 执行 helm install / helm upgrade |
| init-job | 自动初始化默认数据 |

### 3. 前置条件

- SR01~SR07、SR12 功能开发完成
- PostgreSQL 和 Keycloak 已部署并可访问

### 4. 最小保证

- init-job 幂等，重复执行不破坏已有数据
- 部署失败不影响已运行的服务

### 5. 成功保证

- helm install 后所有默认数据到位
- 所有路由转发正确
- ext_authz + ext_proc 策略绑定正常

### 6. 触发事件

- `helm install` / `helm upgrade`

### 7. 主成功场景

1. init-job 等待 PostgreSQL + Keycloak 就绪
2. 写入默认 apps（knowledgebase、memory 等）`ON CONFLICT DO NOTHING`
3. 写入默认 resource_patterns `ON CONFLICT DO NOTHING`
4. 写入默认 path_rules `ON CONFLICT DO NOTHING`
5. 从 apps 表读取，创建 `{app}-admins` 组
6. Gateway HTTPRoute 配置所有路由
7. ext_authz SecurityPolicy + ext_proc EnvoyExtensionPolicy 绑定

### 8. 扩展场景（包括异常场景）

- 8a. init-job 超时（Keycloak/PG 未就绪）：重试等待，K8s Job 设置 backoffLimit
- 8b. 部分数据已存在：ON CONFLICT DO NOTHING，不覆盖
- 8c. Gateway CRD 未安装：路由配置失败，需先安装 Envoy Gateway CRD

### 约束

- Gateway 路由规则由 Helm Chart 管理
- 业务团队的 HTTPRoute 由各团队自行管理

### 规格

| 路径 | 后端 | 鉴权 |
|------|------|------|
| `/realms/*`, `/admin/*`, `/resources/*` | keycloak:8080 | 无（登录接口） |
| `/api/v1/*` | keycloak-proxy:8090 + pep-proxy:8090 | ext_authz |
| `/acl/v1/*` | resource-sync:8080 | ext_authz |
| `/knowledgebase/*`, `/memory/*` | 各自后端 | ext_authz + ext_proc |

### 升级

- 支持 helm upgrade 滚动升级，init-job 幂等不覆盖已修改数据
- Gateway 路由变更自动热加载

### 可靠性

- init-job 可重复执行，任何时候运行结果一致

### 性能

- init-job 执行时间 < 60s

### 安全

- 默认密码在 values.yaml 中配置，部署后需修改
- Gateway TLS 证书配置

### 韧性

- init-job 失败可手动重新触发

### 可服务

- helm upgrade 不中断在线服务
- init-job 日志输出完整，便于排查

### 可测试

- helm install → 所有默认数据到位
- 幂等验证（重复部署不覆盖已修改数据）
- Gateway 所有路由转发正确
- ext_authz + ext_proc policy 绑定正常

### 支持的产品

- AI Storage Software suite 26.0.RC1（AIDP 平台）

---

## SR11 Helm Chart 与证书、可观测性

### 1. 简要说明

提供一键 Helm 部署所有 IAM 组件的能力，支持 TLS 证书管理（cert-manager 或手动 Secret），集成链路追踪可观测性（Jaeger/OTel）。

### 2. Actor

| Actor | 说明 |
|-------|------|
| 运维人员 | 部署、升级、管理证书 |
| SRE | 查看链路追踪、排查问题 |

### 3. 前置条件

- K8s 集群就绪，Envoy Gateway CRD 已安装
- 可选：cert-manager 已安装

### 4. 最小保证

- helm install 失败不影响已有集群资源
- 证书过期前有预警

### 5. 成功保证

- helm install 一键部署所有 IAM 组件
- Gateway HTTPS 监听正常
- Jaeger UI 可查看请求链路
- helm upgrade 不破坏现有数据

### 6. 触发事件

- 运维人员执行 helm install / upgrade
- 证书即将过期
- SRE 排查线上问题

### 7. 主成功场景

**部署：**
1. 运维人员执行 `helm install iam ./charts`
2. 所有组件（Keycloak、keycloak-proxy、pep-proxy、OPA、bundle-server、resource-sync、Gateway）启动
3. init-job 初始化默认数据

**证书管理：**
4. cert-manager 自动签发 TLS 证书，或运维手动创建 TLS Secret
5. Gateway HTTPS Listener 引用证书
6. cert-manager 自动在到期前 30 天续期

**可观测性：**
7. Jaeger all-in-one 部署（测试环境）
8. Gateway TrafficPolicy 配置 tracing（采样率可配置）
9. SRE 通过 Jaeger UI 查看请求链路

### 8. 扩展场景（包括异常场景）

- 8a. cert-manager 未安装：使用手动 TLS Secret 方式
- 8b. 证书过期未续期：HTTPS 连接失败，需手动更新 Secret
- 8c. Jaeger 宕机：不影响业务，链路追踪不可用

### 约束

- 支持离线部署（离线镜像包 amd64/arm64）

### 规格

| 项目 | 规格 |
|------|------|
| 部署交付工时 | < 0.5 天 |
| 支持架构 | amd64、arm64 |
| 追踪采样率 | 可配置（测试 100%，生产 10%） |

### 升级

- 支持 helm upgrade 滚动升级，不中断业务
- 证书轮换不中断服务（Gateway 自动热加载）

### 可靠性

- 热路径组件（Gateway、pep-proxy、OPA）≥2 副本
- resource-sync ≥2 副本（failOpen）
- Keycloak ≥2 副本
- PostgreSQL 建议主备部署

### 性能

- Jaeger 追踪对请求延迟增加 < 1ms

### 安全

- HTTPS 全链路加密
- 证书由 cert-manager 或手动管理，不存储在代码仓库

### 韧性

- Jaeger 宕机不影响业务
- 证书 Secret 删除后 Gateway 降级为 HTTP（需手动恢复）

### 可服务

- 提供部署指南、接入规范、API 文档
- Jaeger UI 支持按 trace ID 查询完整链路
- Grafana Dashboard 展示 Gateway 控制面指标

### 可测试

- helm install 一键部署成功
- helm upgrade 不破坏现有数据
- Gateway HTTPS 监听正常
- cert-manager 或手动 Secret 均可工作
- 证书轮换不中断服务
- Jaeger UI 可查看每个请求的链路
- 采样率可配置

### 支持的产品

- AI Storage Software suite 26.0.RC1（AIDP 平台）

---

## SR12 API Key 认证与管理

### 1. 简要说明

支持外部应用通过 API Key 访问知识库、记忆库等服务。API Key 认证后转换为等效的服务账号身份，复用现有 OPA 路径鉴权和 resource_acl 资源鉴权流程。提供 API Key 的完整生命周期管理（创建、查看、更新、删除、轮换）。

### 2. Actor

| Actor | 说明 |
|-------|------|
| 管理员（admins） | 创建/管理 API Key，授权服务账号访问资源 |
| 外部应用 | 携带 API Key 调用知识库/记忆库 API |
| pep-proxy | 识别 API Key，转换为 Identity，执行鉴权 |
| keycloak-proxy | 提供 API Key 管理 API |

### 3. 前置条件

- SR02 完成（pep-proxy 路径鉴权正常）
- SR02 中的资源访问鉴权完成（pep-proxy 资源级鉴权正常）

### 4. 最小保证

- API Key 明文只返回一次，数据库只存哈希
- API Key 禁用/删除后立即失效
- 鉴权失败返回 401/403，不泄露内部信息

### 5. 成功保证

- 外部应用通过 API Key 可访问已授权的资源
- 轮换 Key 不影响已有资源授权（subject_id 不变）
- 鉴权流程与用户 JWT 完全复用
- API Key 创建的资源由 ext_proc 正常写入 ACL（subject_type=service）

### 6. 触发事件

- 租户管理员创建/管理 API Key
- 外部应用携带 API Key 发起请求
- 管理员通过 ACL API 给服务账号授权资源

### 7. 主成功场景

**创建 Key：**
1. 租户管理员调 `POST /api/v1/{tenant}/api-keys`
2. keycloak-proxy 生成随机 Key，SHA256 哈希后存入 api_keys 表
3. 自动生成服务账号 ID（`{app_name}-svc`）
4. 返回明文 Key（仅此一次）
5. 管理员将 Key 分发给外部应用
6. 管理员通过 ACL API 给服务账号授权具体资源

**请求鉴权：**
7. 外部应用 `GET /knowledgebase/v1/kb/kb-001` + `X-API-Key: ak_3f8a...`
8. Gateway 调 ext_authz → pep-proxy
9. pep-proxy 检测无 Authorization header，有 X-API-Key header → 走 API Key 认证
10. SHA256 哈希后查 api_keys 表，验证 enabled + 未过期
11. 检查 allowed_paths 白名单
12. 转换为 Identity（subject_id, tenant_id, subject_type=service）
13. 后续走 OPA 路径鉴权 + resource_acl 资源鉴权（完全复用）
14. 注入 `X-Auth-User-Id: {subject_id}`, `X-Auth-Tenant: {tenant_id}`, `X-Auth-Groups: {groups}`（与 JWT 路径完全相同，后端无感知）

**轮换 Key：**
15. 管理员调 `POST /api/v1/{tenant}/api-keys/{id}/rotate`
16. 生成新 Key，旧 Key 立即失效，subject_id 不变
17. resource_acl 中的授权记录不受影响

### 8. 扩展场景（包括异常场景）

- 8a. API Key 无效（不在数据库中）：返回 401
- 8b. API Key 已禁用：返回 401
- 8c. API Key 已过期：返回 401
- 8d. API Key 访问 allowed_paths 以外的路径：返回 403
- 8e. API Key 超过 rate_limit：返回 429
- 8f. 非 admins 管理 API Key：返回 403
- 8g. API Key 创建资源：ext_proc 正常写入 ACL，subject_type=service
- 8h. 轮换后使用旧 Key：返回 401
- 8i. API Key 明文丢失：只能删除旧 Key 创建新 Key

### 约束

- API Key 明文只在创建和轮换时返回一次，后续无法查看
- 数据库只存储 SHA256 哈希值
- API Key 管理接口由 path_rules 保护，需要 admins
- subject_type 区分 user 和 service，同一资源可同时授权给用户和外部应用

### 规格

| 项目 | 规格 |
|------|------|
| API Key 长度 | 67 字符（"ak_" + 64 位 hex） |
| 哈希算法 | SHA256 |
| 默认 rate_limit | 100 次/分钟 |
| 单租户 API Key 数量 | 无硬性上限 |

### 升级

- 支持滚动升级，pep-proxy 多副本逐个重启
- api_keys 表结构变更需提前迁移

### 可靠性

- API Key 认证依赖 PostgreSQL，DB 不可用则认证失败返回 503
- pep-proxy ≥2 副本保证可用性

### 性能

- API Key 认证延迟：≤5ms（SHA256 + 索引查询）
- 后续鉴权延迟与 JWT 用户一致

### 安全

- 不存明文，数据库只存哈希
- 传输层依赖 HTTPS 加密（API Key 在 Header 中明文传输）
- 支持过期时间、禁用开关、路径白名单、限流
- 审计：last_used_at 追踪使用情况，created_by 追溯创建者

### 韧性

- PostgreSQL 宕机：API Key 认证失败，返回 503
- pep-proxy 宕机：所有请求 403（ext_authz 失败）

### 可服务

- API Key 管理接口支持列出所有 Key（只显示前缀）
- last_used_at 可识别长期未使用的 Key 进行清理
- 轮换 Key 不影响授权，运维可定期轮换

### 可测试

- 创建 API Key → 返回明文，数据库存哈希
- 外部应用携带 API Key 访问已授权资源 → 200
- 外部应用访问未授权资源 → 403
- API Key 禁用后 → 401
- API Key 过期后 → 401
- 轮换 Key → 新 Key 可用，旧 Key 失效
- 路径白名单外的请求 → 403
- 限流超限 → 429
- 非 tenant-admins 管理 API Key → 403

### 支持的产品

- AI Storage Software suite 26.0.RC1（AIDP 平台）
