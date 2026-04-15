# AIDP IAM 安全基线配置规范

> 版本：v1.0
> 日期：2026-04-14
> 范围：取消默认密码、用户管理安全、API Key 安全
> 适用版本：AIDP IAM v2.0+（单租户）

---

## 1 方案总述

### 1.1 目标

按"零默认凭证、最小权限、可审计"三原则，对 AIDP IAM 的密码、用户、API Key 全生命周期建立安全基线。**任何默认密码、固定凭证、永久 Key 都视为高危项**，必须通过基线检查。

### 1.2 三大方向

| 方向 | 核心原则 | 落地手段 |
|---|---|---|
| **取消默认密码** | 平台中不预置任何固定密码 | Helm pre-install hook 随机生成、首次登录强制改密、邀请激活链接 |
| **用户管理安全** | 最小权限 + 强认证 + 异常防护 | 密码策略、MFA、会话管理、登录锁定、特权账号审计 |
| **API Key 安全** | 一次性明文 + 最小作用域 + 可轮换 | 哈希存储、前缀标识、有效期、作用域路径白名单、限流 |

### 1.3 实施方式

- **强制基线**（必选项）：未通过则部署失败 / 启动失败 / 拒绝服务
- **建议基线**（可选项）：推荐配置，按业务风险评估是否启用
- **审计基线**：必须有审计记录，但不阻断业务

每条配置项标注 `配置项 ID`，可用于自动化合规扫描脚本。

---

## 2 配置组划分

| 配置组 ID | 配置组名称 | 说明 |
|---|---|---|
| C.AIDP.IAM.G_1 | 密码策略 | 密码复杂度、有效期、历史、强制改密 |
| C.AIDP.IAM.G_2 | 用户管理 | 用户创建、登录、锁定、会话、特权账号 |
| C.AIDP.IAM.G_3 | API Key 管理 | Key 生命周期、作用域、限流、轮换 |
| C.AIDP.IAM.G_4 | 凭证存储 | DB / Keycloak admin / OPAL token 等基础设施凭证 |
| C.AIDP.IAM.G_5 | 会话与认证 | JWT / OIDC / MFA / 登录设备管理 |
| C.AIDP.IAM.G_6 | 审计与监控 | 关键操作日志、告警 |

---

## 3 详细配置项基线表

> 列说明：**强制** = 必选项；**默认安全** = 部署后无需调整就符合基线；**风险等级** = 高/中/低
>
> 表格较宽，建议用横向滚动或导入到 Excel 查看。

### 3.1 配置组：密码策略 (C.AIDP.IAM.G_1)

| 配置项 ID | 配置项名称 | 配置项说明 | 风险等级 | 风险描述 | 检查方法 | 取值范围 | 缺省值 | 推荐值范围 | 是否默认安全 | 修复建议 | 修复影响 | 是否必选项 | 备注 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C.AIDP.IAM.G_1.R_1 | 禁用平台预置默认密码 | 平台代码、镜像、values.yaml 中不得保留任何默认/固定密码 | 高 | 默认密码暴露使任意人可登录平台/数据库 | 全仓库扫描 `password:.*['"][a-zA-Z0-9]+['"]`，结果应为空 | 仓库无硬编码密码 | 当前存在多处默认密码 | 仓库无硬编码密码 | 否 | 删除 values.yaml/Python 代码中所有 password 默认值，改为 Helm pre-install hook 随机生成 | 部署流程改造，运维需通过 K8s Secret 取超级密码 | 是 | 见 docs/secrets-management-design.md |
| C.AIDP.IAM.G_1.R_2 | 密码最小长度 | 用户密码长度不得小于配置值 | 高 | 短密码易被暴力破解 | Keycloak realm → Authentication → Password Policy 包含 length(N) | 6~64 位 | 8 | 12~32 位 | 否 | Keycloak realm 添加 password policy: length(12) | 老用户下次改密时强制满足，不影响当前会话 | 是 | NIST SP 800-63B 推荐 ≥ 8 |
| C.AIDP.IAM.G_1.R_3 | 密码复杂度 | 密码须包含大小写字母、数字、特殊字符任意 3 类 | 高 | 弱密码易被字典攻击 | Keycloak password policy 包含 upperCase(1) lowerCase(1) digits(1) specialChars(1) notUsername | 0~4 类 | 不限制 | 至少 3 类 | 否 | Keycloak realm 添加 upperCase(1) digits(1) specialChars(1) notUsername(1) | 老用户下次改密时强制满足 | 是 | — |
| C.AIDP.IAM.G_1.R_4 | 密码有效期 | 密码自上次修改起的最大有效天数 | 中 | 长期不改密码增加泄露风险 | Keycloak password policy 包含 forceExpiredPasswordChange(N) | 0~365 天 | 不强制 | 60~90 天 | 否 | 设置 forceExpiredPasswordChange(90) | 用户登录时强制改密，可能影响 UX | 否 | NIST 不再强制定期改密但仍可作为合规要求 |
| C.AIDP.IAM.G_1.R_5 | 密码历史不重复 | 不允许使用最近 N 次用过的密码 | 中 | 历史密码可能已泄露 | Keycloak password policy 包含 passwordHistory(N) | 1~24 | 不限制 | 5~12 | 否 | 设置 passwordHistory(5) | 用户改密时如果用旧密码会被拒 | 否 | — |
| C.AIDP.IAM.G_1.R_6 | 首次登录强制改密 | 平台预置/邀请生成的初始密码必须 temporary=true | 高 | 初始密码若不被改，等同于默认密码 | Keycloak users API 检查 `requiredActions` 含 UPDATE_PASSWORD | true / false | true | true | 是 | init-keycloak.py 中 reset-password 时 `temporary: true` | 用户首次登录会跳到改密页 | 是 | — |
| C.AIDP.IAM.G_1.R_7 | 密码哈希算法 | Keycloak 存储密码使用强哈希算法 | 高 | 弱哈希（如 SHA-1、MD5）可被彩虹表攻击 | Keycloak realm → Password Policy 包含 hashAlgorithm(pbkdf2-sha512) hashIterations(≥210000) | argon2/pbkdf2-sha256/pbkdf2-sha512 | pbkdf2-sha256 | pbkdf2-sha512 / argon2 | 否 | 设置 hashAlgorithm(pbkdf2-sha512) + hashIterations(210000) | 改后旧密码哈希会延迟升级，无需立即改密 | 是 | OWASP 推荐 |
| C.AIDP.IAM.G_1.R_8 | 禁用与用户名相同的密码 | 密码不得与用户名相同 | 高 | 密码=用户名是常见弱密码 | Keycloak password policy 包含 notUsername(1) | true / false | false | true | 否 | 添加 notUsername(1) | 部分用户可能需要重设密码 | 是 | — |

### 3.2 配置组：用户管理 (C.AIDP.IAM.G_2)

| 配置项 ID | 配置项名称 | 配置项说明 | 风险等级 | 风险描述 | 检查方法 | 取值范围 | 缺省值 | 推荐值范围 | 是否默认安全 | 修复建议 | 修复影响 | 是否必选项 | 备注 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C.AIDP.IAM.G_2.R_1 | 用户名最小长度 | 用户名不得过短 | 中 | 短用户名（如 a/b）增加暴力枚举风险 | Keycloak realm → Login → Username 长度策略 | 1~64 位 | 3 | 5~32 位 | 否 | 校验用户创建时 username 长度 ≥ 5 | 不影响已有用户 | 否 | — |
| C.AIDP.IAM.G_2.R_2 | 邀请激活链接（不预置密码） | 创建租户/普通用户时不主动设置密码，发送一次性激活链接 | 高 | 管理员设置初始密码后通过 IM 转交，存在泄露中转 | tenants.py / 用户创建 API 不应直接调 reset-password 接口 | enabled / disabled | 当前直接 reset-password | enabled | 否 | 改用 Keycloak `execute-actions-email` (UPDATE_PASSWORD/VERIFY_EMAIL) | 需要 SMTP 配置；气隙环境兜底为一次性随机密码 + temporary=true | 是 | 见 ui-wireframes.md §3.1 |
| C.AIDP.IAM.G_2.R_3 | 登录失败锁定 | 连续 N 次密码错误锁定账户 M 分钟 | 高 | 不锁定将允许暴力破解 | Keycloak realm → Brute Force Detection → enabled | 失败次数 1~50 / 锁定时长 1~1440 分钟 | enabled=false | 失败 5 次 / 锁 30 分钟 | 否 | 启用 Brute Force Detection: maxLoginFailures=5, waitIncrementSeconds=60, maxFailureWaitSeconds=900 | 用户多次输错会被锁定，需管理员解锁或等待 | 是 | — |
| C.AIDP.IAM.G_2.R_4 | 账户禁用与删除审计 | 账户禁用/删除操作必须留下审计记录 | 中 | 误操作或恶意删除无法追溯 | 检查 `audit_log` 表存在 USER_DISABLE / USER_DELETE 事件 | enabled / disabled | enabled | enabled | 是 | Keycloak Events Listener 启用 `jboss-logging` 与自定义审计 sink | — | 是 | — |
| C.AIDP.IAM.G_2.R_5 | 特权账户最小化 | admins 组成员数量不得超过阈值 | 高 | 管理员账户过多扩大攻击面 | `kubectl get keycloak-admin-group-members \| wc -l` 与策略阈值比较 | 1~50 | 当前不限 | 2~5 人 | 否 | 限制 admins 组成员，引入审批流程申请加入 | 需要建立审批流程 | 否 | — |
| C.AIDP.IAM.G_2.R_6 | 不允许使用共享账号 | 一个账号只能由一个自然人使用 | 中 | 共享账号无法溯源、密码泄露范围广 | 审计 `LOGIN` 事件按 user_id 分组，检测多 IP 高频登录 | 监控告警 | 不监控 | 启用监控 | 否 | 建立"同一账号 1 小时内来自 >2 个不同地理位置 IP 登录"告警 | 跨地区出差用户可能误报 | 否 | — |
| C.AIDP.IAM.G_2.R_7 | 离职用户即时禁用 | 员工离职 24h 内必须禁用账户 | 高 | 离职后仍可访问会造成数据泄露 | 与 HR 系统对接定期校对，差异生成清单 | enabled / disabled | 手工 | 自动同步 | 否 | 通过 SCIM 协议或定时 Job 同步 HR 数据 | 需 HR 系统提供 API | 否 | — |
| C.AIDP.IAM.G_2.R_8 | 用户操作行为日志 | 关键操作（修改组、改密、登录）必须落库且 ≥ 180 天保留 | 中 | 安全事件无法回溯 | 审计日志表存在且最早记录 ≥ 180 天前 | 7~3650 天 | 取决于 DB 大小 | 180~730 天 | 否 | Keycloak Events 持久化 + 定期归档冷数据 | DB 容量增加 | 是 | — |

### 3.3 配置组：API Key 管理 (C.AIDP.IAM.G_3)

| 配置项 ID | 配置项名称 | 配置项说明 | 风险等级 | 风险描述 | 检查方法 | 取值范围 | 缺省值 | 推荐值范围 | 是否默认安全 | 修复建议 | 修复影响 | 是否必选项 | 备注 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C.AIDP.IAM.G_3.R_1 | API Key 哈希存储 | DB 中只存哈希，不存明文 | 高 | 明文存储 = 数据库泄露即全量 Key 泄露 | `SELECT api_key FROM api_keys LIMIT 1` 应为 SHA256 hex（64 字符） | sha256 / sha512 / argon2 | sha256 | sha256 | 是 | 当前已实现 | — | 是 | — |
| C.AIDP.IAM.G_3.R_2 | API Key 明文一次性展示 | 创建后只在响应中返回一次明文，后续不可再查 | 高 | 可重复查询导致明文长期可被截获 | API Key 详情接口不返回明文字段 | enabled / disabled | enabled | enabled | 是 | UI 创建后 30 秒跳转，明文不入数据库 | 用户遗失需轮换 | 是 | 见 ui-wireframes.md §8 |
| C.AIDP.IAM.G_3.R_3 | API Key 前缀标识 | Key 由前缀（如 `aidp_live_`）+ 随机串组成，便于识别和泄露检测 | 中 | 无前缀的 Key 在 GitHub 等扫描器中无法识别 | `echo $key \| grep -E '^aidp_(live\|test)_[A-Za-z0-9]{32}$'` | 自定义前缀 | 当前 `aidp_live_` | `aidp_live_` / `aidp_test_` | 是 | 当前已实现 | — | 是 | 类似 GitHub `ghp_...` |
| C.AIDP.IAM.G_3.R_4 | API Key 强制有效期 | Key 不得设为永不过期 | 高 | 长期有效 Key 一旦泄露危害巨大 | `SELECT COUNT(*) FROM api_keys WHERE expires_at IS NULL` 应为 0 | 永不过期 / 30~365 天 | 允许永不过期 | 强制 30~365 天 | 否 | 后端校验 expires_at NOT NULL，UI 删除"永不过期"选项 | 老 Key 需逐步迁移 | 是 | — |
| C.AIDP.IAM.G_3.R_5 | API Key 作用域路径白名单 | Key 必须配置可访问路径，不允许 `/**` 全通 | 高 | 全通 Key = 拥有该用户所有权限 | `SELECT scope FROM api_keys` 不应包含 `**` | 路径前缀列表 | 允许 `**` | 必须显式列出路径 | 否 | UI 删除"全部"选项；后端拒绝 `**` 通配 | 老 Key 迁移工作量大 | 是 | — |
| C.AIDP.IAM.G_3.R_6 | API Key 限流 | 每个 Key 必须配置每分钟最大请求数 | 中 | 无限流的 Key 被滥用可拖垮服务 | `SELECT rate_limit FROM api_keys WHERE rate_limit IS NULL` 应为 0 | 1~100000 / 永不限速 | 不限速 | 100~10000 req/min | 否 | 强制 rate_limit NOT NULL，缺省 1000 | Key 调用方需控制速率 | 是 | 通过 Envoy BackendTrafficPolicy 实现 |
| C.AIDP.IAM.G_3.R_7 | API Key 轮换支持 | 提供轮换接口，subject_id 不变，密码换 | 中 | 不支持轮换则泄露后只能新建 Key + 重新授权 | `POST /api/v1/api-keys/{id}/rotate` 接口存在 | enabled / disabled | enabled | enabled | 是 | 当前已实现 | 调用方需切换 Key | 是 | — |
| C.AIDP.IAM.G_3.R_8 | API Key 最后使用时间记录 | 每次调用更新 last_used_at 字段，便于发现僵尸 Key | 中 | 长期未用的 Key 未被清理是潜在风险 | `SELECT name FROM api_keys WHERE last_used_at < NOW() - INTERVAL '90 days'` 应被清单告警 | 异步写入 | 已实现 | 已实现 | 是 | 当前已实现 | — | 是 | — |
| C.AIDP.IAM.G_3.R_9 | API Key 创建/删除审计 | 关键操作落审计日志（不含明文） | 高 | 无审计无法溯源滥用 | audit_log 包含 API_KEY_CREATE / ROTATE / REVOKE 事件 | enabled / disabled | enabled | enabled | 是 | 当前已实现 | — | 是 | — |
| C.AIDP.IAM.G_3.R_10 | API Key 数量上限 | 每个用户/服务账号最多持有 N 个有效 Key | 低 | 防止滥用导致管理混乱 | `SELECT user_id, COUNT(*) FROM api_keys WHERE status='active' GROUP BY user_id HAVING COUNT(*) > 10` | 1~100 | 不限 | 5~10 个 | 否 | 后端创建时校验 | 用户超限需先删旧 Key | 否 | — |

### 3.4 配置组：凭证存储 (C.AIDP.IAM.G_4)

| 配置项 ID | 配置项名称 | 配置项说明 | 风险等级 | 风险描述 | 检查方法 | 取值范围 | 缺省值 | 推荐值范围 | 是否默认安全 | 修复建议 | 修复影响 | 是否必选项 | 备注 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C.AIDP.IAM.G_4.R_1 | Postgres 超级密码非预置 | postgres-super 密码由部署时随机生成，不在仓库中 | 高 | 默认密码 `keycloak/keycloak` 等同于无密码 | `kubectl -n postgres get secret postgres-super -o json` 存在；values.yaml 中无 password 字段 | 32 位随机 | 当前 `keycloak` | 32 位随机 | 否 | Helm pre-install hook 随机生成（已在 db-provisioner-test 验证） | 老集群需走密码轮换流程 | 是 | — |
| C.AIDP.IAM.G_4.R_2 | Keycloak admin 密码非预置 | Keycloak admin 用户密码由随机生成 | 高 | 默认 `admin/admin` 是最严重漏洞 | `kubectl -n keycloak get secret keycloak-credentials` 存在；values.yaml 无 admin.password 字段 | 32 位随机 | 当前 `admin` | 32 位随机 | 否 | Helm pre-install hook 随机生成 | 老集群需迁移 | 是 | — |
| C.AIDP.IAM.G_4.R_3 | Super-admin 初始密码非预置 | super-admin bootstrap 密码由部署时生成且 temporary=true | 高 | 默认 `SuperInit@123` 等同于公开密码 | values.yaml 无 superAdmin.password；Keycloak 用户 requiredActions 含 UPDATE_PASSWORD | 16+ 位随机 | 当前固定值 | 16+ 位随机 + temporary=true | 否 | 改 init-keycloak.py 从 Secret 读取，temporary=true | 首次登录强制改密 | 是 | — |
| C.AIDP.IAM.G_4.R_4 | OPAL token 非预置 | OPAL 服务间认证 token 随机生成 | 高 | 默认 `opal-server-token` 公开 | values.yaml 无 authToken；K8s Secret `opal-credentials` 存在 | 32 位随机 | 当前 `opal-server-token` | 32 位随机 | 否 | Helm pre-install hook 随机生成 | 各组件 reload | 是 | — |
| C.AIDP.IAM.G_4.R_5 | DB 连接串不内嵌密码 | DB_URL 类环境变量不得明文包含密码 | 中 | Pod env 在 `kubectl describe pod` 中可见 | `kubectl get pod -o yaml` 检查 env，密码应来自 secretKeyRef | secretKeyRef / 明文 | 当前明文 | secretKeyRef + `$(VAR)` 拼接 | 否 | DB_URL 拆分为 URL 模板 + PG_PASSWORD secretKeyRef，K8s env var 替换 | Chart 模板改造 | 是 | — |
| C.AIDP.IAM.G_4.R_6 | Secret 加密落盘 | etcd 中的 Secret 启用加密 | 高 | etcd 备份泄露 = 全部 Secret 明文泄露 | `kubectl get secrets -A -o yaml \| grep "ENC:" `，加密后字段以 `ENC:` 开头 | enabled / disabled | disabled | enabled | 否 | K8s API Server 启用 EncryptionConfiguration (aescbc/secretbox) | 影响 API Server 启动参数 | 是 | 集群级别配置 |
| C.AIDP.IAM.G_4.R_7 | Secret 不被无关 SA 读取 | RBAC 限制 Secret 读取范围 | 高 | 任意 Pod 读 Secret 等同于明文存储 | `kubectl auth can-i get secrets --as=system:serviceaccount:default:default` 应为 no | RBAC 配置 | 默认 default SA 不能读 | 显式收敛到必要 SA | 是 | 各组件用专属 SA + 最小 RBAC | — | 是 | — |
| C.AIDP.IAM.G_4.R_8 | 凭证轮换周期 | 基础设施凭证（DB / KC admin / OPAL）≥ 90 天轮换一次 | 中 | 长期不换 = 累积泄露风险 | Secret annotation `last-rotated` 与当前时间差 < 90 天 | 0~365 天 | 不轮换 | 90 天 | 否 | 提供 `helm upgrade --set rotate=<comp>` 流程 | 各组件需重启 | 否 | — |

### 3.5 配置组：会话与认证 (C.AIDP.IAM.G_5)

| 配置项 ID | 配置项名称 | 配置项说明 | 风险等级 | 风险描述 | 检查方法 | 取值范围 | 缺省值 | 推荐值范围 | 是否默认安全 | 修复建议 | 修复影响 | 是否必选项 | 备注 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C.AIDP.IAM.G_5.R_1 | JWT 有效期 | Access Token 有效期不得超过阈值 | 中 | 长 Token 泄露后影响窗口长 | Keycloak realm → Tokens → Access Token Lifespan | 60~28800 秒 | 300 秒 | 300~3600 秒 | 是 | — | — | 是 | 配合 refresh token 续期 |
| C.AIDP.IAM.G_5.R_2 | Refresh Token 绝对超时 | Refresh Token 绝对最大寿命 | 中 | 永久 refresh = 永久会话 | Keycloak realm → SSO Session Max | 60~604800 秒 | 36000 秒 | 36000~86400 秒 | 是 | — | — | 是 | — |
| C.AIDP.IAM.G_5.R_3 | 强制 HTTPS | OIDC 端点必须 HTTPS（除 localhost） | 高 | HTTP 传输 token = 中间人可窃取 | Keycloak realm → Require SSL = external | none / external / all | external | external | 是 | — | — | 是 | — |
| C.AIDP.IAM.G_5.R_4 | MFA 双因素认证（管理员） | admins 组成员必须启用 MFA | 高 | 单密码保护管理员账号风险高 | Keycloak required action OTP 配置 + admins 组用户已绑定 OTP | enabled / disabled | disabled | enabled (admins) | 否 | 管理员组 conditional MFA flow | 管理员需绑定 Authenticator | 是 | — |
| C.AIDP.IAM.G_5.R_5 | 登录设备/会话管理 | 用户可查看活跃会话并强制下线 | 中 | 无会话管理 = 设备遗失无法补救 | 用户中心存在"登录设备"页 | enabled / disabled | enabled | enabled | 是 | 当前已实现 | — | 是 | — |
| C.AIDP.IAM.G_5.R_6 | 单点退出 (SLO) | 用户登出时所有应用同时失效 | 中 | 不登出导致 token 在多端继续有效 | OIDC backchannel logout 启用 | enabled / disabled | disabled | enabled | 否 | Keycloak client `Backchannel Logout URL` 配置 | 业务需实现 logout endpoint | 否 | — |
| C.AIDP.IAM.G_5.R_7 | 拒绝弱 Cipher Suite | TLS 仅启用强加密套件 | 高 | 弱套件易被降级攻击 | Envoy ClientTrafficPolicy.tls.minProtocolVersion ≥ 1.2 | TLS 1.0~1.3 | 1.0+ | 1.2+ | 否 | ClientTrafficPolicy 配置 minVersion=TLSv1_2 + 强 ciphers | 老客户端不兼容 | 是 | — |
| C.AIDP.IAM.G_5.R_8 | 异常登录告警 | 异地登录、IP 异常等触发告警 | 中 | 不告警则攻击长期未发现 | 审计日志监控规则存在 + alertmanager 路由 | enabled / disabled | disabled | enabled | 否 | Prometheus rule + Loki 关联查询 | 需要观测平台 | 否 | — |

### 3.6 配置组：审计与监控 (C.AIDP.IAM.G_6)

| 配置项 ID | 配置项名称 | 配置项说明 | 风险等级 | 风险描述 | 检查方法 | 取值范围 | 缺省值 | 推荐值范围 | 是否默认安全 | 修复建议 | 修复影响 | 是否必选项 | 备注 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C.AIDP.IAM.G_6.R_1 | 审计日志独立存储 | 审计日志不与业务数据共表，不可被业务删 | 高 | 攻击者可删除痕迹 | 审计表权限：业务 user 仅 INSERT，查询走管理员视图 | 共表 / 独立表 | 共表 | 独立表 + 只追加 | 否 | 拆分 audit_log schema，业务 user GRANT INSERT only | 改造工作量 | 是 | — |
| C.AIDP.IAM.G_6.R_2 | 审计日志保留周期 | 关键审计 ≥ 180 天，建议 ≥ 1 年 | 中 | 周期不足无法应对延迟发现的事件 | `SELECT MIN(created_at) FROM audit_log` 距今 ≥ 180 天 | 7~3650 天 | 不限制 | 180~730 天 | 否 | 定期归档冷数据到对象存储 | DB 容量管理 | 是 | — |
| C.AIDP.IAM.G_6.R_3 | 审计日志不含敏感数据 | 不得在日志中记录密码、Key 明文、token 等 | 高 | 日志泄露 = 凭证泄露 | grep audit_log 不应出现 password/secret/api_key 字面值 | 黑名单字段 | 部分字段未脱敏 | 全部敏感字段脱敏 | 否 | 应用层日志中间件统一脱敏 | 改造各服务日志输出 | 是 | — |
| C.AIDP.IAM.G_6.R_4 | 关键操作告警 | 高危操作（删租户、批量删用户、修改 path_rules）触发实时告警 | 中 | 告警延迟扩大事件影响 | alertmanager 规则覆盖 TENANT_DELETE / BULK_USER_OP / PATH_RULE_CHANGE | 全部 / 部分 / 无 | 无 | 全部 | 否 | 定义高危操作清单 + alertmanager 规则 | 需要告警渠道 | 否 | — |
| C.AIDP.IAM.G_6.R_5 | 审计日志完整性校验 | 审计日志支持完整性校验（哈希链/签名） | 低 | 日志被篡改无法察觉 | audit_log 表含 prev_hash 字段并定期 verify | 简单 / 哈希链 / 数字签名 | 无 | 哈希链 | 否 | audit_log 写入时计算 prev_hash | 性能略降 | 否 | 高合规要求场景启用 |

---

## 4 实施优先级建议

按"影响范围 × 实施成本"分四象限：

```
                  高影响
                    │
   ┌── 立即修 ──────┼────── 计划修 ───┐
   │ G_1.R_1 默认密码  │ G_1.R_2 密码长度│
   │ G_4.R_1~R_4 凭证   │ G_4.R_6 etcd 加密
   │ G_3.R_1~R_2 Key 哈希│ G_5.R_4 admin MFA
   │ G_2.R_2 邀请激活    │ G_3.R_4~R_6 Key 强约束
低成本├──────────────┼────────────── 高成本
   │ G_1.R_6 强制改密   │ G_2.R_7 HR 同步
   │ G_3.R_8 Key last_used│ G_4.R_8 凭证轮换
   │ G_5.R_5 会话管理   │ G_6.R_5 审计哈希链
   └── 顺手修 ──────┼────── 评估后修 ─┘
                    │
                  低影响
```

**P0（立即修，2 周内）**：所有"默认密码"相关 → G_1.R_1、G_4.R_1~R_4、G_3.R_1~R_2

**P1（计划修，1~2 个月）**：密码策略加强 + Key 强约束 → G_1.R_2~R_8、G_3.R_4~R_6

**P2（季度修）**：MFA、etcd 加密、HR 同步 → G_5.R_4、G_4.R_6、G_2.R_7

**P3（评估）**：审计哈希链、SLO、ABAC 升级等

---

## 5 自动化检查脚本约定

每条 `R_x` 应有可执行的 check 命令。建议放到 `scripts/security-check/` 目录：

```
scripts/security-check/
├── G_1_R_1_no_default_password.sh
├── G_1_R_2_password_length.sh
├── ...
├── G_4_R_1_pg_super_random.sh
└── run-all.sh                    # 批量执行，输出合规报告
```

**`run-all.sh` 输出示例**：

```
[PASS] G_1.R_6   首次登录强制改密
[PASS] G_3.R_1   API Key 哈希存储
[FAIL] G_1.R_1   禁用平台预置默认密码 — 发现 5 处硬编码密码
[FAIL] G_4.R_1   Postgres 超级密码非预置 — values.yaml 仍存在 password 字段
[WARN] G_5.R_4   MFA 未启用（推荐项）
─────────────────────────────────────
合规率: 32/40 = 80%, 致命项失败 2 条
```

集成到 CI：致命项失败 → 阻断发布。

---

## 6 与现有设计文档的关系

| 关联文档 | 关系 |
|---|---|
| `diagrams/secrets-management-design.md` | 本表 G_4 配置组对应该文档的实施方案 |
| `diagrams/ui-wireframes.md` §8 | API Key 管理 UI = G_3 配置项的人机交互层 |
| `db-provisioner-test/README.md` | Postgres 超级密码随机生成方案的 PoC |
| `diagrams/story-breakdown.md` | SR07 / SR10 关联用户管理与安全 |

---

## 7 附录：术语表

| 术语 | 含义 |
|---|---|
| 必选项 | 不满足则视为不合规，应在发布前修复 |
| 默认安全 | 平台开箱即用就符合该项基线，无需额外配置 |
| 推荐值 | 综合行业标准（NIST/OWASP/CIS）给出的建议范围 |
| temporary password | Keycloak `temporary: true` 标记的密码，登录时强制改密 |
| ext_authz / ext_proc | Envoy 外部鉴权 / 外部处理过滤器，用于挂载 pep-proxy / resource-sync |
| MFA / 2FA | 多因素认证 |
| SLO | Single Logout，OIDC 单点退出 |
