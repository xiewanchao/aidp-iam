# 支持 API Key 认证与管理 Story 设计文档

## 2 Story概述

### 2.1 Story需求描述

#### 2.1.1 Story背景描述

1. 简要说明

本 Story 支持外部应用通过 API Key 访问受保护业务接口。API Key 由 IAM 管理接口创建，明文只在创建和轮换时返回一次，数据库仅保存 SHA-256 hash、前缀、租户、应用、服务主体、允许路径、启停状态、过期时间等元数据。请求进入 Gateway 后，pep-proxy 在 ext_authz 阶段优先识别 `X-API-Key`，验证成功后合成等效服务账号身份，再复用 OPA 路径级鉴权和 resource_acl 资源级鉴权链路。

2. Actor

租户管理员、外部应用、API Key 使用方、pep-proxy、keycloak-proxy、PostgreSQL。

3. 前置条件

IAM、Gateway、pep-proxy、PostgreSQL 已部署；业务应用路由已绑定 `SecurityPolicy`；目标路径已通过 Manifest 或 permission_groups 写入 OPA 数据；如涉及资源实例访问，需存在对应 `resource_acl`。

4. 最小保证

API Key 明文不落库；禁用、过期、不存在或路径不匹配的 Key 被拒绝；轮换 Key 不改变 `subject_id`，不影响既有资源授权；API Key 认证失败不回退到匿名身份。

5. 成功保证

管理员可创建、查询、更新、删除、轮换 API Key；外部应用携带有效 `X-API-Key` 可访问允许路径；pep-proxy 记录 `last_used_at`；禁用或删除后立即失效；资源权限通过同一套 `resource_acl` 生效。

6. 触发事件

创建 API Key、外部应用访问业务 API、更新 allowed_paths 或 enabled、轮换 Key、删除 Key、Key 到期。

7. 主成功场景

管理员调用 `/AccessManager/Tenants/{tenant}/ApiKeys` 创建 Key，系统返回 `ak_` 开头明文；外部应用请求业务接口时设置 `X-API-Key: ak_xxx`；pep-proxy 对 Key 做 hash 后查询 `api_keys` 表，校验 enabled、expires_at、allowed_paths；校验通过后合成 `subject_id`、tenant、groups，并继续执行 OPA 和资源级 ACL 鉴权；请求放行到后端。

8. 扩展场景（包括异常场景）

- 约束：API Key 只能通过 HTTPS/内网可信链路传输；明文只展示一次。
- 规格：管理员可创建、禁用、删除、轮换 API Key，并为 Key 配置租户、应用、允许路径和过期时间；列表和详情只展示可识别前缀与状态，不展示完整密钥。
- 升级：平台升级后，现有 JWT/OIDC 登录和受保护业务访问保持不变；未使用 `X-API-Key` 的客户端无需改造。
- 可靠性：API Key 校验依赖不可用时，携带 API Key 的请求应明确失败并给出可定位原因；不携带 API Key 的 JWT 访问链路不受该分支影响。
- 性能：`api_key_hash` 建唯一索引；验证一次 DB 查询和一次异步更新时间。
- 安全：Key 禁用、删除、过期立即拒绝；allowed_paths 为空表示不做路径白名单限制，非空时按前缀匹配。
- 韧性：轮换保留 `subject_id`，避免 ACL 重新授权。
- 可服务：列表和详情只返回 key_prefix，不返回 hash 和明文；记录 `last_used_at`。
- 可测试：`da-cluster/scripts/test.sh` 覆盖创建、列表、轮换、禁用、删除和业务路径访问。

#### 2.1.2 关联AR信息详情

| AR编号 | AR标题 | 架构元素 | 所属SR编号 | 所属SR标题 | 所属SR详情 | 所属SR关联功能 |
| --- | --- | --- | --- | --- | --- | --- |
| NA | NA | keycloak-proxy、pep-proxy、PostgreSQL、Envoy Gateway | SR-API-KEY | 支持 API Key 认证与管理 | 外部应用通过 API Key 访问受保护接口，支持 Key 生命周期管理 | `api_keys`、`X-API-Key`、ext_authz |

### 2.2 Story用户使用场景分析

#### 2.2.1 新增/变更的脚本

| 脚本名称 | 功能描述 | 入参 | 执行权限 |
| --- | --- | --- | --- |
| `da-cluster/scripts/test.sh` | 验证 API Key 创建、轮换、禁用、删除和访问业务路径 | `REALM`、`GATEWAY_PORT`、用户密码等 | 本地开发/CI |
| `da-cluster/scripts/setup.sh` | 部署包含 `api_keys` 表和 pep-proxy API Key 分支的 IAM | `--skip-build` | 集群管理员 |

### 2.3 升级兼容性

#### 2.3.1 升级设计编码军规

| 序号 | 军规 | 说明 | 例外 |
| --- | --- | --- | --- |
| 1 | 节点间、组件间、设备间消息接口设计要前后兼容 | 不修改 Envoy ext_authz 协议，仅增加 header 认证分支 | 无 |
| 2 | 持久化数据要前后兼容 | `api_keys` 使用 `CREATE TABLE IF NOT EXISTS` 创建 | 无 |
| 3 | 升级后老特性不能丢失 | JWT Bearer Token 认证链路保持不变 | 无 |
| 4 | 升级后产品对外限制不能变严 | 未要求已有 JWT 客户端改造 | 无 |
| 5 | 升级后License控制不能变严 | 不涉及 | 无 |
| 6 | 升级后商用参数必须继承 | API Key 功能不改变已有 Helm 参数语义 | 无 |
| 7 | 升级后外部接口不能修改 | 新增 `/ApiKeys` 接口，不删除旧接口 | 无 |
| 8 | 升级后产品规格不能下降 | API Key 验证索引查询，不降低既有认证能力 | 无 |
| 9 | 升级后模块对外限制不能变严 | 业务仍只接收已鉴权请求，不感知 JWT/API Key 来源差异 | 无 |
| 10 | 升级后新特性默认不能打开 | 仅使用 `X-API-Key` 时进入新认证分支 | 无 |
| 11 | 禁止修改Apollo版本中的组件 | 不涉及 | 无 |
| 12 | 用户态组件需要兼容不同Apollo版本 | 容器用户态服务，不依赖 Apollo 内核接口 | 无 |

#### 2.3.2 通用升级兼容性Checklist

| 序号 | 军规 | Check项简述 | 是否涉及 | 是否做了兼容性处理 | 备注说明 |
| --- | --- | --- | --- | --- | --- |
| 1 | 持久化数据要前后兼容 | 新增 DB 表 | 涉及 | 是 | `api_keys` 幂等建表，新增索引幂等 |
| 2 | 外部接口不能修改 | 已有认证接口兼容 | 涉及 | 是 | JWT/OIDC 路由不变 |
| 3 | 新特性默认不能打开 | 是否影响老客户端 | 涉及 | 是 | 老客户端不传 `X-API-Key`，仍走 JWT |
| 4 | License 不变严 | License 控制 | 不涉及 | NA | 无 License 逻辑 |
| 5 | Apollo 组件 | 内核/OS/固件 | 不涉及 | NA | 容器应用变更 |
| 6 | 安全兼容 | 密钥明文落盘 | 涉及 | 是 | 只存 hash，明文只返回一次 |

### 2.4 是否影响性能

API Key 请求在 pep-proxy 中增加一次 `api_keys.api_key_hash` 索引查询和一次 `last_used_at` 更新。对 JWT 请求无额外影响。Key 的 allowed_paths 为内存数组匹配，路径数量应控制在合理范围；如后续高并发场景需要，可增加短 TTL 缓存，但需配套禁用/轮换后的缓存失效策略。

## 3 Story设计描述

### 3.1 Story设计

本 Story 包含三层设计：

1. 管理面：`keycloak-proxy` 暴露 `/{tenant}/ApiKeys` CRUD，创建和轮换时生成随机明文并存储 hash。
2. 认证面：pep-proxy ext_authz 检测 `x-api-key` header，优先执行 API Key 认证分支；无 API Key 时继续 JWT 分支。
3. 授权面：API Key 被转换为服务主体身份，继续使用 OPA path_rules 和 resource_acl，不新建旁路授权模型。

### 3.2 Story业务交互流程

#### 3.2.1 API Key 创建与轮换流程

```mermaid
sequenceDiagram
    participant Admin as 管理员
    participant KP as keycloak-proxy
    participant DB as PostgreSQL api_keys

    Admin->>KP: POST /AccessManager/Tenants/t1/ApiKeys
    KP->>KP: 生成 ak_ + 32 bytes 随机明文
    KP->>KP: SHA-256 hash
    KP->>DB: INSERT hash,key_prefix,subject_id,allowed_paths
    KP-->>Admin: 返回 api_key 明文，仅一次

    Admin->>KP: POST /AccessManager/Tenants/t1/ApiKeys/{id}/Rotate
    KP->>KP: 生成新明文和新 hash
    KP->>DB: UPDATE hash,key_prefix，保留 subject_id
    KP-->>Admin: 返回新 api_key 明文
```

#### 3.2.2 API Key 认证与授权流程

```mermaid
sequenceDiagram
    participant App as 外部应用
    participant GW as Envoy Gateway
    participant PEP as pep-proxy ext_authz
    participant DB as PostgreSQL
    participant OPA as OPA
    participant BE as 业务后端

    App->>GW: GET /KnowledgeBase/... + X-API-Key
    GW->>PEP: Check(headers,path,method)
    PEP->>DB: SELECT api_keys WHERE api_key_hash=hash
    PEP->>PEP: 校验 enabled/expires_at/allowed_paths
    PEP->>OPA: POST /v1/data/authz
    OPA-->>PEP: allow
    PEP->>DB: 查询 resource_acl
    DB-->>PEP: role_path
    PEP-->>GW: OK + X-Auth-* header
    GW->>BE: 转发请求
    BE-->>App: 业务响应
```

#### 3.2.3 API Key 状态机

```mermaid
stateDiagram-v2
    [*] --> Enabled: Create
    Enabled --> Disabled: PUT enabled=false
    Disabled --> Enabled: PUT enabled=true
    Enabled --> Rotated: Rotate
    Rotated --> Enabled: 新 Key 生效，旧 Key 失效
    Enabled --> Expired: expires_at 到期
    Disabled --> Deleted: DELETE
    Enabled --> Deleted: DELETE
    Expired --> Deleted: DELETE
    Deleted --> [*]
```

### 3.3 运行设计

- API Key 管理接口挂载在 `/AccessManager/Tenants/{tenant}/ApiKeys`。
- Key 明文格式为 `ak_` 前缀，便于识别和审计；`key_prefix` 用于列表展示。
- 数据库存储 `api_key_hash`，不存明文；`subject_id` 创建后保持稳定。
- pep-proxy 对 `X-API-Key` 请求返回的身份结构与 JWT 解码结果兼容，包含 `user_id`、`tenant_id`、`groups`、`subject_type`、`app_name`。
- `allowed_paths` 非空时使用 `request_path.startswith(path)` 前缀匹配。
- 禁用、删除和过期均在下一次请求时实时生效。

### 3.4 SFMEA分析

| 子功能 | 子功能输入 | 大类 | 小类 | 故障模式 | 说明 | 是否涉及 | 可能的故障原因 | 已有容错规避措施 | 故障影响（对功能） | 严酷度（影响程度） | 故障恢复步骤和恢复时间 | 故障注入方法 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| API Key 创建 | app_name、allowed_paths、expires_at | 管理服务 | 创建失败 | API Key 创建失败 | 管理员调用创建接口后未返回可用 Key | 是 | 管理员 token 无权限、请求体非法、随机数生成失败、PostgreSQL 写入失败 | 创建接口鉴权；参数校验；只保存 hash；创建失败不返回半成品 Key | 外部应用无法获取访问凭据 | 高 | 1.检查创建接口返回码和 keycloak-proxy 日志 2.修正请求体或权限 3.恢复 DB 后重新创建 4.恢复时间 2min | 使用普通用户 token 创建 Key 或停止 PostgreSQL |
| API Key 创建 | 随机数、tenant、app_name | 安全服务 | Key 弱随机或重复 | 生成的 Key 可预测或 hash 冲突 | API Key 随机性不足，或同一 hash 被重复写入 | 是 | 随机源不安全、长度不足、唯一索引缺失、异常重试复用明文 | Key 格式为 `ak_` + 32 bytes 随机 hex；DB 保存 hash 并应唯一索引 | 攻击者可猜测 Key 或覆盖已有 Key | 极高 | 1.立即禁用受影响 Key 2.更换安全随机源 3.轮换全部疑似 Key 4.恢复时间 30min | mock 随机函数返回固定值后连续创建 Key |
| API Key 创建 | tenant、app_name、subject_id | 身份服务 | subject_id 冲突 | 多个 Key 复用错误服务主体 | 不同应用或租户创建出的 API Key 使用同一个 subject_id | 是 | subject_id 生成规则错误、tenant 隔离缺失、复制旧记录、DB 唯一约束不足 | 创建时生成服务主体；轮换保留 subject_id；租户字段随 Key 保存 | 不同外部应用共享权限，资源 ACL 隔离失效 | 极高 | 1.禁用冲突 Key 2.修复 subject_id 生成与唯一约束 3.重建受影响服务主体 4.恢复时间 15min | 手工复制 api_keys 记录或 mock subject_id 生成固定值 |
| API Key 明文保护 | api_key 明文 | 安全服务 | 密钥泄露 | 完整 API Key 被存储或重复展示 | 创建或轮换后的明文被日志、列表或详情接口泄露 | 是 | 接口误返回 `api_key_hash` 或完整明文、调用方记录完整 header、日志脱敏缺失 | 数据库只保存 SHA-256 hash；列表和详情只返回 `key_prefix`；明文只在创建和轮换时返回一次 | 泄露 Key 可被外部应用越权调用允许路径 | 极高 | 1.立即禁用或删除泄露 Key 2.轮换新 Key 3.审计 `last_used_at` 和访问日志 4.恢复时间 5min | 检查列表/详情响应是否包含 `api_key` 或在日志中打印 `X-API-Key` |
| API Key 明文保护 | api_key_hash、key_prefix | 管理服务 | 元数据过度暴露 | 列表或详情返回 hash 或过多敏感字段 | 管理接口暴露可用于离线破解或枚举的信息 | 是 | 响应 schema 未过滤、ORM 直接序列化 DB 模型、调试字段未关闭 | 列表和详情只返回 key_prefix、状态和元数据，不返回 hash/明文 | 增加密钥暴力破解和横向移动风险 | 高 | 1.下线异常接口版本 2.清理响应字段 3.轮换已暴露 Key 4.恢复时间 10min | 调用 GET `/ApiKeys` 检查是否含 `api_key_hash` |
| API Key 认证 | X-API-Key、request_path | 认证服务 | 认证失败 | 有效 Key 被拒绝或未知 Key 被接受 | pep-proxy 对 API Key 的 hash、状态、过期时间校验异常 | 是 | DB 不可用、hash 计算错误、Key 禁用或过期、请求 header 缺失、实现误匹配 | API Key 分支失败时明确拒绝；JWT 分支不受影响；禁用、删除、过期实时生效 | 外部应用访问失败或非法 Key 越权进入鉴权链路 | 极高 | 1.查看 pep-proxy 失败原因 2.核对 Key 元数据和 `last_used_at` 3.修复 DB 或配置后重试 4.恢复时间 5min | 使用无效 Key、过期 Key 或停止 PostgreSQL 后访问业务路径 |
| API Key 认证 | X-API-Key、Authorization | 认证服务 | 认证分支混淆 | API Key 失败后错误回退 JWT 或匿名 | 同一请求携带错误 Key 和其他身份时认证结果不符合预期 | 是 | 认证分支优先级错误、异常处理 fallback、空 Key 被当作无 Key、header 大小写处理不一致 | pep-proxy 优先识别 `X-API-Key`；API Key 认证失败不回退匿名身份 | 攻击者可通过构造 header 绕过 Key 失败 | 极高 | 1.修复认证分支优先级 2.补充混合 header 测试 3.审计异常访问 4.恢复时间 5min | 同时发送无效 `X-API-Key` 和有效 JWT 验证拒绝行为 |
| API Key 认证 | api_key_hash 查询 | 数据库服务 | DB 不可用 | API Key 请求全部认证失败 | PostgreSQL 不可用时无法查询 Key 元数据 | 是 | DB 宕机、连接池耗尽、慢查询、网络中断 | JWT 用户不依赖该 DB 查询；API Key 失败明确拒绝；后续可引入短 TTL 缓存 | 外部应用 API Key 访问中断 | 高 | 1.恢复 PostgreSQL 或连接池 2.验证 JWT 链路 3.重试 API Key 请求 4.恢复时间 10min | 停止 PostgreSQL 或耗尽连接池后用 Key 访问 |
| allowed_paths 授权 | allowed_paths、request_path | 授权服务 | 路径配置错误 | 路径白名单过宽或过窄 | API Key 可访问超出预期路径，或合法路径被拒绝 | 是 | 创建参数配置错误、前缀匹配边界错误、路径大小写或尾斜杠不一致 | 列表和详情展示 allowed_paths；非空时按前缀匹配；建议最小路径配置 | 过宽会造成越权，过窄会导致业务访问 403 | 高 | 1.查询 Key 详情确认 allowed_paths 2.修正路径后重新访问 3.补充访问测试 4.恢复时间 2min | 配置 `/` 或错误前缀后访问允许路径和非允许路径 |
| allowed_paths 授权 | allowed_paths、request_path | 授权服务 | 前缀绕过 | 非预期路径被前缀匹配放行 | `/app` 白名单误放 `/app-admin` 等相邻路径 | 是 | 简单 `startswith` 未检查路径边界、路径编码未规范化、重复斜杠或大小写绕过 | 非空时按前缀匹配；创建参数应按最小路径配置 | Key 可访问相邻应用或管理路径 | 极高 | 1.收紧 allowed_paths 2.修复路径规范化和边界判断 3.轮换受影响 Key 4.恢复时间 5min | allowed_paths 配 `/Knowledge` 后访问 `/KnowledgeAdmin` |
| API Key 过期 | expires_at、系统时间 | 生命周期服务 | 过期判断错误 | Key 提前失效或过期后仍可用 | 过期时间解析、时区或系统时钟导致失效行为错误 | 是 | 时区未统一、expires_at 为空处理错误、节点时钟漂移、比较符错误 | 列表/详情展示 expires_at；过期在下一次请求实时生效 | 合法业务中断，或过期凭据继续可用 | 高 | 1.校准系统时钟 2.修正 expires_at 解析 3.重新设置过期时间 4.恢复时间 5min | 设置临界时间、过去时间和不同时区时间访问 |
| API Key 轮换 | key_id | 生命周期服务 | 轮换异常 | 新 Key 不可用或旧 Key 未失效 | 轮换后 hash、prefix 或 `subject_id` 状态不符合预期 | 是 | DB 更新事务失败、客户端未更新新 Key、缓存未失效、实现误改 `subject_id` | 轮换保留 `subject_id`；替换 hash 和 prefix 后旧 Key 立即失效；不改变既有 ACL | 新 Key 无法访问，或旧 Key 继续可用造成安全风险 | 高 | 1.记录轮换前后 `subject_id` 2.用旧 Key 和新 Key 分别验证 3.异常时禁用 Key 后重新创建 4.恢复时间 5min | 轮换后继续使用旧 Key 访问，或模拟 DB 更新失败 |
| API Key 轮换 | key_id、subject_id | 生命周期服务 | 权限丢失 | 轮换后资源 ACL 不再生效 | 轮换错误改变 subject_id，导致原资源授权失效 | 是 | UPDATE 时重建 subject_id、创建新记录替代旧记录、ACL 绑定到 key_id 而非 subject_id | 设计要求轮换保留 `subject_id`，避免重新授权 | 外部应用新 Key 认证成功但资源访问 403 | 高 | 1.恢复原 subject_id 或重写 ACL 2.修复轮换逻辑 3.重新轮换验证 4.恢复时间 10min | 轮换后对比 subject_id 并访问原授权资源 |
| API Key 禁用删除 | key_id、enabled | 生命周期服务 | 失效不及时 | 禁用或删除后的 Key 仍可认证 | Key 状态变更后下一次请求未立即生效 | 是 | DB 更新失败、认证缓存未失效、删除接口未命中目标 Key、租户不匹配 | 当前设计实时查询 DB；删除返回 `204`；禁用、删除和过期均在下一次请求生效 | 被禁用或删除的凭据仍可访问受保护业务 | 极高 | 1.立即再次禁用或删除 2.检查 DB 记录和认证日志 3.必要时重启 pep-proxy 清理缓存 4.恢复时间 5min | 禁用 Key 后立即使用同一 Key 访问业务路径 |
| API Key 更新 | enabled、allowed_paths、rate_limit | 管理服务 | 部分更新 | 元数据部分字段更新失败 | 更新接口返回成功，但 allowed_paths/enabled/rate_limit 只有部分生效 | 是 | PATCH/PUT 合并逻辑错误、JSON 字段空值处理错误、事务不完整、并发覆盖 | 更新后返回完整元数据；禁用和路径变更下一次请求实时生效 | 管理页面显示与实际鉴权不一致 | 中 | 1.查询详情核对字段 2.重新 PUT 全量目标值 3.修复事务和空值处理 4.恢复时间 3min | 同时更新 enabled=false 和 allowed_paths，验证两者均生效 |
| API Key 限流 | rate_limit、调用频率 | 流量控制服务 | 限流不生效 | 超过 Key 限额仍全部放行 | Key 元数据包含 rate_limit，但运行时未按 Key 限制请求 | 是 | 限流执行未接入、Key 维度识别错误、计数器丢失、集群多副本计数不一致 | 创建/更新接口保存 rate_limit；后续需配套运行时限流实现 | 单个 Key 可压垮后端或绕过配额 | 高 | 1.临时禁用异常 Key 2.启用网关级限流 3.修复 Key 维度计数 4.恢复时间 10min | 用同一 Key 以超过 rate_limit 的 QPS 连续请求 |
| 使用审计记录 | 认证成功事件 | 可观测服务 | 审计缺失 | `last_used_at` 未更新 | 有效 Key 访问成功后详情接口仍看不到最近使用时间 | 是 | 异步更新时间失败、DB 写入失败、认证成功后异常退出 | `last_used_at` 作为元数据展示；认证主链路与审计更新解耦 | 管理员无法判断 Key 是否被使用，影响泄露排查 | 中 | 1.查看 pep-proxy 日志确认认证成功 2.检查 DB 更新时间字段 3.修复异步更新逻辑后重试 4.恢复时间 3min | 阻断 `api_keys.last_used_at` 更新 SQL 或模拟 DB 写入失败 |
| 使用审计记录 | 请求来源、key_prefix | 可观测服务 | 异常使用不可定位 | Key 泄露后无法定位使用来源 | 日志或详情缺少 key_prefix、租户、路径、失败原因或来源信息 | 是 | 日志字段不足、脱敏过度、访问日志未关联 key_id、失败分支未记录 | 列表/详情展示 key_prefix 和 last_used_at；pep-proxy 记录失败原因 | 泄露排查和封禁决策变慢 | 中 | 1.补充审计字段 2.按 key_prefix 检索访问记录 3.轮换可疑 Key 4.恢复时间 10min | 使用 Key 从异常来源访问，检查审计是否可定位 |
| 资源级授权衔接 | subject_id、resource_acl | 授权服务 | 身份映射错误 | API Key 认证成功但资源权限使用错误主体 | pep-proxy 合成身份与 resource_acl 存储主体不一致 | 是 | `user_id` 使用 key_id 而非 subject_id、tenant_id 错误、groups 缺失或默认组错误 | API Key 转换为服务主体身份；返回结构兼容 JWT 解码结果 | Key 对已授权资源被误拒，或继承错误主体权限 | 高 | 1.对比认证返回身份与 ACL 主体 2.修复身份映射 3.重写受影响 ACL 4.恢复时间 10min | 给 subject_id 授权后用 Key 访问资源，验证是否放行 |

### 3.5 Onetrack设计

NA

### 3.6 可定位设计

可定位设计的目的是把“Key 是否被使用、Key 当前是否有效、认证为什么失败、认证后为什么仍被拒绝”拆成可观察的检查点。每个值用于区分密钥生命周期问题、认证问题和授权问题。

| 定位项 | 查看方式 | 为什么要查看这个值 | 异常指向 |
| --- | --- | --- | --- |
| 最近使用时间 | 查看 `api_keys.last_used_at` 或 API Key 详情返回值 | 确认客户端是否真正使用了该 Key，以及最近一次成功认证时间；排查“客户端说已调用但平台无记录”的问题。 | 客户端未发送 Key、发送到错误环境、Key 在认证前被拒绝或请求未到达 Gateway。 |
| Key 展示元数据 | 列表/详情接口查看 `key_prefix`、enabled、expires_at、allowed_paths | 确认调用方使用的是哪一把 Key，以及 Key 是否启用、是否过期、是否允许目标路径。 | Key 被禁用、过期、路径白名单过窄或调用方拿错 Key。 |
| API Key 失败原因 | 查看 pep-proxy 对 API Key 失败记录的原因：不存在、禁用、过期、路径不允许 | 同样是 401/403，需要区分是 Key 本身无效还是路径不允许。 | 密钥错误、生命周期状态错误、路径配置错误或鉴权依赖不可用。 |
| 后续授权分类 | 查看 OPA/资源级拒绝返回的 `path_rule`、`resource_acl` 分类 | 确认 Key 已认证成功但被后续授权拒绝，避免误判为 Key 无效。 | `path_rule` 指向业务路径未授权；`resource_acl` 指向资源实例权限不足。 |

### 3.7 风险分析

| 风险 | 等级 | 应对 |
| --- | --- | --- |
| API Key 被当作长期万能凭据使用 | 高 | 强制配置 expires_at 和 allowed_paths 的产品规范；上线前审计 |
| API Key 明文被日志打印 | 高 | 管理接口不打印明文；调用方规范禁止记录完整 Key |
| 大量 API Key 请求造成 DB 压力 | 中 | hash 索引；后续可引入缓存和限流 |
| 服务账号资源权限模型不清晰 | 中 | `subject_id` 固定，并统一写入 `resource_acl`；不复用个人用户 ID |

## 4 Shard设计描述

接口描述统一汇总如下，后续小节保留每个接口的详细入参、返回值和失败行为。

| Shard | 接口/入口 | 提供方 | 使用方 | 黑盒能力 | 成功可见结果 |
| --- | --- | --- | --- | --- | --- |
| Shard 1 创建 API Key | `POST /AccessManager/Tenants/{tenant}/ApiKeys` | `keycloak-proxy` | 租户管理员 | 创建服务主体 API Key 并返回一次性明文。 | 返回 `201`、Key 元数据和 `api_key` 明文。 |
| Shard 1 查询 API Key 列表 | `GET /AccessManager/Tenants/{tenant}/ApiKeys` | `keycloak-proxy` | 租户管理员/页面 | 查询租户下 Key 元数据。 | 返回 key_prefix、enabled、last_used_at 等，不返回明文和 hash。 |
| Shard 1 查询 API Key 详情 | `GET /AccessManager/Tenants/{tenant}/ApiKeys/{key_id}` | `keycloak-proxy` | 租户管理员/页面 | 查询单个 Key 元数据。 | 返回单个 Key 状态；不存在返回 `404`。 |
| Shard 1 更新 API Key | `PUT /AccessManager/Tenants/{tenant}/ApiKeys/{key_id}` | `keycloak-proxy` | 租户管理员 | 更新描述、启停、允许路径、限流和过期时间。 | 返回更新后的元数据。 |
| Shard 1 删除 API Key | `DELETE /AccessManager/Tenants/{tenant}/ApiKeys/{key_id}` | `keycloak-proxy` | 租户管理员 | 删除 Key 并使其立即失效。 | 返回 `204 No Content`，后续使用该 Key 认证失败。 |
| Shard 1 轮换 API Key | `POST /AccessManager/Tenants/{tenant}/ApiKeys/{key_id}/Rotate` | `keycloak-proxy` | 租户管理员 | 生成新 Key，保留服务主体身份。 | 返回新明文和更新后的元数据。 |
| Shard 2 API Key 认证分支 | 受保护业务路径 + `X-API-Key` | `pep-proxy` | 外部应用/Envoy Gateway | 验证 API Key 并转换为服务主体身份。 | ext_authz 返回 OK 并注入 `x-auth-*`。 |
| Shard 2 API Key hash 查询 | `verify_api_key(api_key, request_path)` | `pep-proxy` | API Key 认证分支 | 校验 Key 状态、过期时间和允许路径。 | 返回等效身份结构，失败映射为鉴权拒绝。 |
| Shard 3 API Key 数据模型 | PostgreSQL `api_keys` | PostgreSQL/IAM 服务 | 管理与认证流程 | 保存 Key 元数据和不可逆校验值。 | 外部接口只返回元数据；创建/轮换时仅返回临时明文。 |

### 4.1 Shard 1：API Key 生命周期管理接口

该 Shard 由 `keycloak-proxy` 提供，负责 API Key 的创建、查询、更新、删除和轮换。

#### 4.1.1 创建 API Key

- 接口路径：`POST /AccessManager/Tenants/{tenant}/ApiKeys`
- 功能：为指定租户和应用创建 API Key，生成服务主体 `subject_id`，保存密钥 hash，返回明文密钥。
- 入参：
  - 路径参数：`tenant`，租户 ID。
  - Header：`Authorization: Bearer <admin-token>`、`Content-Type: application/json`。
  - Body：`app_name`、`description`、`allowed_paths`、`rate_limit`、`expires_at`。
- 返回值：
  - 成功：`201`，返回 `id`、`key_prefix`、`tenant_id`、`app_name`、`subject_id`、`subject_type`、`allowed_paths`、`rate_limit`、`expires_at`、`enabled`、`api_key`。
  - 失败：`401/403` 未授权；`400` 入参非法。

#### 4.1.2 查询 API Key 列表

- 接口路径：`GET /AccessManager/Tenants/{tenant}/ApiKeys`
- 功能：查询租户下所有 API Key 元数据，不返回明文和 hash。
- 入参：
  - 路径参数：`tenant`。
  - Header：`Authorization: Bearer <admin-token>`。
- 返回值：
  - 成功：API Key 元数据数组，包含 `key_prefix`、`enabled`、`last_used_at`，不包含 `api_key`、`api_key_hash`。

#### 4.1.3 查询 API Key 详情

- 接口路径：`GET /AccessManager/Tenants/{tenant}/ApiKeys/{key_id}`
- 功能：查询单个 API Key 元数据。
- 入参：
  - 路径参数：`tenant`、`key_id`。
- 返回值：
  - 成功：单个 API Key 元数据。
  - 失败：`404` Key 不存在。

#### 4.1.4 更新 API Key

- 接口路径：`PUT /AccessManager/Tenants/{tenant}/ApiKeys/{key_id}`
- 功能：更新 API Key 元数据，包括描述、启停、允许路径、限流值、过期时间。
- 入参：
  - 路径参数：`tenant`、`key_id`。
  - Body：`description`、`enabled`、`allowed_paths`、`rate_limit`、`expires_at`，均为可选。
- 返回值：
  - 成功：更新后的 API Key 元数据。
  - 失败：`400` 无可更新字段；`404` Key 不存在。

#### 4.1.5 删除 API Key

- 接口路径：`DELETE /AccessManager/Tenants/{tenant}/ApiKeys/{key_id}`
- 功能：永久删除 API Key，使该 Key 立即失效。
- 入参：
  - 路径参数：`tenant`、`key_id`。
- 返回值：
  - 成功：`204 No Content`。
  - 失败：`404` Key 不存在。

#### 4.1.6 轮换 API Key

- 接口路径：`POST /AccessManager/Tenants/{tenant}/ApiKeys/{key_id}/Rotate`
- 功能：生成新明文 Key，替换数据库中的 hash 和 prefix，保留 `subject_id`，避免重新配置 ACL。
- 入参：
  - 路径参数：`tenant`、`key_id`。
- 返回值：
  - 成功：返回更新后的元数据和新的 `api_key` 明文。
  - 失败：`404` Key 不存在。

### 4.2 Shard 2：API Key 认证接口

该 Shard 由 `pep-proxy` 在 Gateway ext_authz 阶段执行，不直接暴露为终端 REST API。

#### 4.2.1 X-API-Key 认证分支

- 接口路径：受保护业务路径，例如 `GET /KnowledgeBase/Tenants/{tenant}/KnowledgeBases`，Header 携带 `X-API-Key`。
- 功能：验证 API Key，转换为服务主体身份，继续执行 OPA 路径级鉴权和 resource_acl 资源级鉴权。
- 入参：
  - Header：`X-API-Key: ak_xxx`。
  - Gateway ext_authz 入参：path、method、headers、可选 body。
- 返回值：
  - 成功：Gateway ext_authz 返回 OK，并注入 `x-auth-user-id=<subject_id>`、`x-auth-tenant=<tenant_id>`、`x-auth-groups=all-users`。
  - 失败：`401` Key 不存在/禁用/过期；`403` allowed_paths 不匹配或授权不足；`503` 鉴权依赖不可用。

#### 4.2.2 API Key hash 查询

- 接口路径：内部函数 `verify_api_key(api_key, request_path)`。
- 功能：将明文 Key 做 SHA-256，查询 `api_keys` 表，校验状态和路径白名单，更新 `last_used_at`。
- 入参：
  - `api_key`：客户端传入的明文。
  - `request_path`：当前请求路径，用于 allowed_paths 前缀匹配。
- 返回值：
  - 成功：`{"user_id": subject_id, "tenant_id": tenant_id, "groups": ["all-users"], "subject_type": "service", "app_name": app_name}`。
  - 失败：抛出 HTTPException，映射为 ext_authz 拒绝。

### 4.3 Shard 3：API Key 数据模型

#### 4.3.1 api_keys 表

- 接口路径：PostgreSQL 表 `api_keys`。
- 功能：保存 API Key 元数据和 hash，不保存明文。
- 入参：
  - 写入字段：`id`、`api_key_hash`、`key_prefix`、`tenant_id`、`app_name`、`description`、`subject_id`、`subject_type`、`allowed_paths`、`rate_limit`、`expires_at`、`enabled`、`created_by`。
- 返回值：
  - 查询字段：不返回 `api_key_hash` 给外部接口；创建/轮换时仅返回临时明文 `api_key`。

## 5 验收测试用例

| 用例编号 | 用例名称 | 预置条件 | 测试步骤 | 预期结果 |
| --- | --- | --- | --- | --- |
| AK-AT-001 | 创建 Key 返回明文 | 管理员 token 可用 | 1. POST `/AccessManager/Tenants/{tenant}/ApiKeys`。<br>2. 检查响应字段。 | HTTP 201；`api_key` 以 `ak_` 开头；返回 `key_prefix`、`subject_id`、`enabled=true`。 |
| AK-AT-002 | DB 不保存明文 | 已创建 Key | 1. 查询 `api_keys` 表。<br>2. 对比返回明文。 | 表中只有 `api_key_hash` 和 `key_prefix`，无完整明文。 |
| AK-AT-003 | 列表不泄露明文和 hash | 已创建 Key | 1. GET `/ApiKeys`。 | 响应包含 `key_prefix`，不包含 `api_key` 和 `api_key_hash`。 |
| AK-AT-004 | 详情不泄露明文和 hash | 已创建 Key | 1. GET `/ApiKeys/{key_id}`。 | 响应包含元数据，不包含明文和 hash。 |
| AK-AT-005 | 更新 allowed_paths | 已创建 Key | 1. PUT `/ApiKeys/{key_id}` 更新 `allowed_paths`。<br>2. 用 Key 访问允许路径和非允许路径。 | 允许路径继续进入鉴权；非允许路径返回 403。 |
| AK-AT-006 | 禁用 Key 即时生效 | 已创建 Key | 1. PUT `/ApiKeys/{key_id}` 设置 `enabled=false`。<br>2. 用旧 Key 访问业务路径。 | 返回 401；`last_used_at` 不作为放行依据。 |
| AK-AT-007 | 启用 Key 恢复 | 已禁用 Key | 1. PUT `/ApiKeys/{key_id}` 设置 `enabled=true`。<br>2. 访问允许路径。 | 认证通过，继续执行 OPA/resource_acl 鉴权。 |
| AK-AT-008 | 轮换 Key 保留 subject_id | 已创建 Key | 1. 记录旧 `subject_id` 和旧 Key。<br>2. POST `/Rotate`。<br>3. GET 详情。 | 返回新明文；`subject_id` 不变；`key_prefix` 更新。 |
| AK-AT-009 | 轮换后旧 Key 失效 | 已轮换 Key | 1. 使用旧 Key 访问业务路径。<br>2. 使用新 Key 访问业务路径。 | 旧 Key 返回 401；新 Key 可进入鉴权链路。 |
| AK-AT-010 | 删除 Key 即时失效 | 已创建 Key | 1. DELETE `/ApiKeys/{key_id}`。<br>2. 使用该 Key 访问业务路径。 | DELETE 返回 204；后续访问返回 401。 |
| AK-AT-011 | 过期 Key 拒绝 | 创建 Key 时设置过去时间或修改 expires_at 为过去时间 | 1. 使用过期 Key 访问业务路径。 | 返回 401，原因是 Key expired。 |
| AK-AT-012 | 无效 Key 拒绝 | 无 | 1. 使用 `X-API-Key: ak_invalid_xxx` 访问业务路径。 | 返回 401。 |
| AK-AT-013 | API Key 资源 ACL 生效 | Key 对应 subject_id 已被授权某资源 | 1. 使用 Key 访问有 ACL 资源。<br>2. 访问无 ACL 资源。 | 有 ACL 资源放行；无 ACL 资源 403。 |
| AK-AT-014 | last_used_at 更新 | 已创建且有效 Key | 1. 使用 Key 成功访问一次。<br>2. GET `/ApiKeys/{key_id}`。 | `last_used_at` 非空并晚于创建时间。 |

## 6 开发自验证用例

### 6.1 开发自验证用例设计

使用 `da-cluster/scripts/test.sh` 中 API Key section 验证管理接口和认证分支；安装 mock-kb 后补充验证 `X-API-Key` 对业务路由的端到端访问，以及禁用/非法 Key 的拒绝行为。

### 6.2 开发自验证用例详情

| Depth | 用例_名称 | 用例_编号 | 用例_级别 | 用例_自动化类型 | 用例_测试活动 | 用例_适用版本 | 用例_当前部署形态 | 用例_支持部署形态 | 关联_需求资源_编号 | 用例_设计描述 | 用例_预置条件 | 用例_测试步骤 | 用例_预期结果 | 用例_备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 创建 API Key | AK-001 | L1 | 自动化 | 开发自验证 | v1.8+ | Kind | K8s | SR-API-KEY | 验证 POST 创建 Key | IAM 已部署，管理员 token 可用 | `test.sh` 调用 POST `/ApiKeys` | 返回 `ak_` 明文和 key id | 管理接口 |
| 1 | 列表隐藏明文 | AK-002 | L1 | 自动化 | 开发自验证 | v1.8+ | Kind | K8s | SR-API-KEY | 验证列表只显示 prefix | 已创建 Key | GET `/ApiKeys` | 不包含完整 `api_key` | 安全用例 |
| 1 | 轮换 Key | AK-003 | L1 | 自动化 | 开发自验证 | v1.8+ | Kind | K8s | SR-API-KEY | 验证 Rotate 返回新明文 | 已创建 Key | POST `/ApiKeys/{id}/Rotate` | 返回新 `ak_`，id 不变 | 生命周期 |
| 1 | 禁用 Key | AK-004 | L1 | 自动化 | 开发自验证 | v1.8+ | Kind | K8s | SR-API-KEY | 验证 enabled=false 后拒绝 | 已创建 Key | PUT `enabled=false` 后访问业务路径 | 返回 401 | 认证分支 |
| 1 | 删除 Key | AK-005 | L1 | 自动化 | 开发自验证 | v1.8+ | Kind | K8s | SR-API-KEY | 验证 DELETE 后拒绝 | 已创建 Key | DELETE 后使用旧 Key 访问 | 返回 401 | 生命周期 |
| 1 | 非法 Key 拒绝 | AK-006 | L1 | 自动化 | 开发自验证 | v1.8+ | Kind | K8s | SR-API-KEY | 验证未知 Key 不可访问 | IAM 已部署 | 使用 `ak_invalid_xxx` 访问业务路径 | 返回 401 | 安全用例 |
| 1 | allowed_paths 生效 | AK-007 | L1 | 自动化/手工 | 开发自验证 | v1.8+ | Kind | K8s | SR-API-KEY | 验证路径白名单 | Key 配置 allowed_paths | 分别访问允许和不允许路径 | 允许路径进入鉴权，不允许路径 403 | 可补充手工验证 |
| 1 | API Key 业务路由访问 | AK-008 | L1 | 自动化 | 开发自验证 | v1.8+ | Kind | K8s | SR-API-KEY | 携带 `X-API-Key` 访问 KnowledgeBase | mock-kb route 可用 | GET `/KnowledgeBase/...` | 有效 Key 返回 200 或资源级 403；非法/禁用 Key 401 | mock-kb 未装时跳过 |
| 1 | subject_id 权限保持 | AK-009 | L1 | 手工/自动化 | 开发自验证 | v1.8+ | Kind | K8s | SR-API-KEY | 验证 Rotate 不影响 ACL | Key subject 已写 ACL | Rotate 后用新 Key 访问原资源 | 按原 ACL 放行 | 资源权限用例 |

## 7 文档评审会议纪要

NA
