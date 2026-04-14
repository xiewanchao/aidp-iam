# AIDP IAM — 密码与凭证管理设计

> 目标：消除系统中所有硬编码密码和默认密码，满足"任何密码都不可以预置"的安全合规要求。

## 1. 背景与目标

### 1.1 当前问题

系统存在 5 类硬编码密码/凭证，分布在 values.yaml、init 脚本、deployment 模板、测试脚本中。核心违规点：

- Keycloak admin 默认 `admin`
- PostgreSQL 默认 `keycloak`
- super-admin 默认 `SuperInit@123`
- tenant-admin 默认 `TenantAdmin@123`
- OPAL token 默认 `opal-server-token`
- 数据库连接串将密码内嵌在 URI（`postgresql://keycloak:keycloak@...`）

### 1.2 设计目标

| 目标 | 说明 |
|---|---|
| **零预置密码** | 代码库、values.yaml、镜像中不保留任何默认密码 |
| **零带外传输** | 优先采用邀请制/SSO，避免运维手动传递密码 |
| **首次登录改密** | 所有 bootstrap 密码强制 `temporary: true` |
| **密码即 Secret** | 所有密码以 K8s Secret 存储，Pod 通过 `secretKeyRef` 消费 |
| **气隙部署可用** | 不依赖邮件服务即可完成部署（邮件作为增强项） |
| **审计可追溯** | 密码生成、变更、使用均可审计 |

### 1.3 非目标

- 不引入外部密钥管理系统（Vault/KMS）——作为未来演进方向，本期用 K8s Secret
- 不实现密码自动轮换——依赖运维手工触发
- 不强制 MFA——作为下一期能力

## 2. 整体策略

按"谁来用、谁来改"分四类处理：

```
┌────────────────────────────────────────────────────────────────┐
│                        用户身份分类                              │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  业务用户 (all-users)         → 外部 IdP 联邦 SSO，零本地密码     │
│                                                                │
│  tenant-admin                → 邀请激活链接（首选）              │
│                                 或随机密码 + 首次改密（兜底）      │
│                                                                │
│  super-admin                 → 部署时随机生成，写 Secret，首次改密 │
│                                                                │
│  基础设施凭证                  → 部署时随机生成，写 Secret          │
│  (PG / KC admin / OPAL)                                        │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

### 2.1 策略对比

| 用户类型 | 密码来源 | 消费者 | 合规级别 |
|---|---|---|---|
| 业务用户 | 外部 IdP 持有，平台零密码 | 外部 IdP 登录流程 | 最高 |
| tenant-admin | 激活链接 → 用户自设 | Keycloak required-action | 高 |
| tenant-admin (兜底) | API 随机生成，一次性返回 | 创建租户的响应 | 中 |
| super-admin | Helm pre-install hook 随机生成 | K8s Secret + 运维 kubectl | 中 |
| 基础设施 | Helm pre-install hook 随机生成 | K8s Secret（仅 Pod 消费） | 高 |

## 3. 详细设计

### 3.1 密码清单与 Secret 规划

**Secret 1：`iam-credentials`**（namespace: `keycloak`）

| Key | 用途 | 生成方式 | 消费者 |
|---|---|---|---|
| `kc-admin-password` | Keycloak 管理员 | Helm randAlphaNum 32 | keycloak StatefulSet, keycloak-init Job, keycloak-proxy |
| `pg-password` | PostgreSQL | Helm randAlphaNum 32 | postgres StatefulSet, keycloak StatefulSet, keycloak-init, 所有服务 |
| `super-admin-password` | super-admin bootstrap | Helm randAlphaNum 16 | keycloak-init Job |

**Secret 2：`opal-credentials`**（namespace: `opa`）

| Key | 用途 | 生成方式 | 消费者 |
|---|---|---|---|
| `opal-auth-token` | OPAL 服务间认证 | Helm randAlphaNum 32 | opal-server, opal-client, pep-proxy |
| `pg-password` | PostgreSQL（同 iam-credentials） | Helm lookup 跨 namespace 复制 | opal-server, pep-proxy, bundle-server |

**Secret 3：`resource-sync-credentials`**（namespace: `resource-sync`）

| Key | 用途 | 生成方式 | 消费者 |
|---|---|---|---|
| `pg-password` | PostgreSQL | Helm lookup 复制 | resource-sync |

### 3.2 密码生成机制

**方案：Helm pre-install hook Job**

```yaml
# charts/keycloak/templates/secret-generator-job.yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: iam-credentials-generator
  annotations:
    "helm.sh/hook": pre-install
    "helm.sh/hook-weight": "-10"
    "helm.sh/hook-delete-policy": hook-succeeded
spec:
  template:
    spec:
      serviceAccountName: secret-generator-sa
      restartPolicy: OnFailure
      containers:
      - name: generator
        image: bitnami/kubectl:latest
        command:
        - sh
        - -c
        - |
          # 仅当 Secret 不存在时生成，已存在则保留（支持 helm upgrade）
          if ! kubectl get secret iam-credentials -n keycloak >/dev/null 2>&1; then
            KC_PASS=$(openssl rand -base64 32 | tr -d "=+/" | head -c 32)
            PG_PASS=$(openssl rand -base64 32 | tr -d "=+/" | head -c 32)
            SA_PASS=$(openssl rand -base64 16 | tr -d "=+/" | head -c 16)
            kubectl create secret generic iam-credentials -n keycloak \
              --from-literal=kc-admin-password="$KC_PASS" \
              --from-literal=pg-password="$PG_PASS" \
              --from-literal=super-admin-password="$SA_PASS"
          fi
```

**为什么不用 Helm template 内置的 `randAlphaNum`？**
因为 Helm template 每次 render 都会重新生成，`helm upgrade` 会破坏现有密码。用 pre-install hook + `kubectl get` 保护存量。

**替代方案：运维预创建（生产推荐）**

生产环境由运维在部署前创建 Secret：

```bash
kubectl create secret generic iam-credentials -n keycloak \
  --from-literal=kc-admin-password="$(openssl rand -base64 32)" \
  --from-literal=pg-password="$(openssl rand -base64 32)" \
  --from-literal=super-admin-password="$(openssl rand -base64 16)"
```

Helm chart 通过 `existingSecret.name` 识别：

```yaml
# values.yaml
existingSecret:
  name: ""  # 空 → Helm 自己生成；非空 → 跳过 hook，直接引用
```

### 3.3 DB 连接串改造

**当前（违规）**：

```yaml
env:
- name: IAM_DB_URL
  value: "postgresql://keycloak:keycloak@postgres:5432/iam"  # 密码明文
```

**改造后**：

```yaml
env:
- name: PG_PASSWORD
  valueFrom:
    secretKeyRef:
      name: iam-credentials
      key: pg-password
- name: IAM_DB_URL
  value: "postgresql://keycloak:$(PG_PASSWORD)@postgres:5432/iam"
  # K8s 支持 $(VAR) 语法做 env var substitution，前提是引用的 var 先定义
```

**跨 namespace 的密码共享**：

PG 密码在 `keycloak` namespace 创建，但 `opa`、`resource-sync` namespace 也需要。方案：

- **方案 A（推荐）**：运维同时在三个 namespace 创建同一密码
- **方案 B**：用 `reflector`/`kubed` 等工具自动同步 Secret
- **方案 C**：Helm pre-install hook 跨 namespace 读写

本期采用 **方案 A**：运维脚本一次性创建三份 Secret。

### 3.4 bootstrap 密码交付流程

#### 3.4.1 super-admin 首次使用

```
[运维] helm install
   │
   ▼
[Helm hook] 生成随机密码 → 写入 iam-credentials Secret
   │
   ▼
[运维] kubectl get secret iam-credentials -n keycloak \
         -o jsonpath='{.data.super-admin-password}' | base64 -d
   │
   ▼
[运维] 登录 Keycloak: username=super-admin, password=<一次性密码>
   │
   ▼
[Keycloak] temporary=true 触发 UPDATE_PASSWORD 必填动作
   │
   ▼
[运维] 设置新密码，由运维个人记忆/存入密码管理器
   │
   ▼
[运维] kubectl delete secret iam-credentials -n keycloak
         # 可选：首次改密后删除 Secret 中的临时密码字段
```

**关键点**：
- Secret 中的 `super-admin-password` 只在首次登录前有意义
- 首次改密后该字段失效（Keycloak 内部已是新密码）
- 建议运维首次登录后**删除 Secret 中的 super-admin-password key**（kc-admin-password 和 pg-password 保留）

#### 3.4.2 tenant-admin 创建流程（优先：邀请制）

前提：平台配置了 SMTP。

```
master-admin → POST /api/v1/tenants
  {
    "realm": "customer-a",
    "admin_email": "admin@customer-a.com"
  }
       │
       ▼
keycloak-proxy
  1. 创建 realm
  2. 创建用户 (无密码)
  3. 用户加入 tenant-admins 组
  4. 调用 Keycloak API:
     POST /admin/realms/{realm}/users/{id}/execute-actions-email
     Body: ["UPDATE_PASSWORD", "VERIFY_EMAIL"]
     Query: redirect_uri=<平台 UI>
       │
       ▼
Keycloak 发送邮件 → tenant-admin 邮箱
       │
       ▼
tenant-admin 点击链接 → 设置密码 → 进入平台
```

#### 3.4.3 tenant-admin 创建流程（兜底：随机密码）

前提：气隙/无邮件环境。

```
master-admin → POST /api/v1/tenants
  {
    "realm": "customer-a",
    "admin_username": "admin"
  }
       │
       ▼
keycloak-proxy
  1. 创建 realm
  2. 生成随机密码 (secrets.token_urlsafe(16))
  3. 创建用户，设置密码 (temporary=true)
  4. 返回 API Response:
     {
       "realm": "customer-a",
       "admin_username": "admin",
       "initial_password": "<一次性明文>",
       "expires_in": 3600
     }
       │
       ▼
master-admin 通过带外通道（企业 IM/当面/加密文件）转交
       │
       ▼
tenant-admin 登录 → 强制改密 → 进入平台
```

**安全要点**：
- `initial_password` 只在 API 响应中返回一次，不落库
- master-admin API 路径受 `path_rules` 保护，仅 `master-admins` 组可访问
- 所有 API 响应体不写入 access log

### 3.5 init-keycloak.py 改造

```python
# Before —— 有默认值兜底，违规
SUPER_ADMIN_INIT_PASSWORD = os.getenv("SUPER_ADMIN_INIT_PASSWORD", "SuperInit@123")

# After —— 无默认值，未设置立即崩溃
try:
    SUPER_ADMIN_INIT_PASSWORD = os.environ["SUPER_ADMIN_INIT_PASSWORD"]
except KeyError:
    raise SystemExit("SUPER_ADMIN_INIT_PASSWORD must be provided via Secret")

# 可选：完成初始化后擦除 env var（防止进程内存转储泄露）
import ctypes
# 或在容器内脚本结束前 unset 环境变量
```

**init-job 的 temporary 设置**：

```python
# 创建 super-admin 时强制首次改密
kc.request("PUT", f"/admin/realms/master/users/{uid}/reset-password", json={
    "type": "password",
    "value": SUPER_ADMIN_INIT_PASSWORD,
    "temporary": True,  # 必填：首次登录强制改密
})
```

### 3.6 测试脚本改造

**当前（违规）**：

```bash
# test.sh
TADMIN_PASS="${TADMIN_PASS:-TenantAdmin@123}"  # 有默认值
```

**改造后**：

```bash
# test.sh
: "${TADMIN_PASS:?TADMIN_PASS env required, run: export TADMIN_PASS=<pass>}"
: "${SUPER_ADMIN_PASS:?SUPER_ADMIN_PASS env required}"

# 或从 Secret 读取
SUPER_ADMIN_PASS="${SUPER_ADMIN_PASS:-$(kubectl get secret iam-credentials -n keycloak -o jsonpath='{.data.super-admin-password}' | base64 -d)}"
```

**CI/CD 中**：

```yaml
# GitHub Actions / GitLab CI 用 secrets 注入
env:
  SUPER_ADMIN_PASS: ${{ secrets.SUPER_ADMIN_PASS }}
  TADMIN_PASS: ${{ secrets.TADMIN_PASS }}
```

### 3.7 Helm values.yaml 改造

```yaml
# charts/keycloak/values.yaml

keycloak:
  admin:
    username: admin
    # password 字段整体删除

postgres:
  auth:
    username: keycloak
    # password 字段整体删除

keycloakInit:
  superAdmin:
    username: "super-admin"
    # password 字段整体删除
  defaultTenant:
    realm: "data-agent"
    adminUser: "tenant-admin"
    adminEmail: ""  # 新增：邀请制要求
    # adminPassword 字段整体删除
    # normalPassword 字段整体删除（生产不预置 normal-user）

# 新增：外部 Secret 配置
existingSecret:
  name: ""  # 空=自动生成，非空=使用外部 Secret
  kcAdminKey: "kc-admin-password"
  pgPasswordKey: "pg-password"
  superAdminKey: "super-admin-password"
```

## 4. 部署模式

### 4.1 开发/测试模式（setup.sh）

```bash
./scripts/setup.sh
  │
  ├── Helm pre-install hook 自动生成随机密码 → Secret
  ├── 部署 Keycloak + 所有服务
  ├── 部署完成后打印：
  │     "Super admin initial password:"
  │     "  kubectl get secret iam-credentials -n keycloak \\"
  │     "    -o jsonpath='{.data.super-admin-password}' | base64 -d"
  └── 提示运维首次登录改密
```

### 4.2 生产模式（setup.sh --no-kind）

```bash
# 步骤 1：运维预创建 Secret
kubectl create secret generic iam-credentials -n keycloak \
  --from-literal=kc-admin-password="$(openssl rand -base64 32)" \
  --from-literal=pg-password="$PG_PASS_FROM_VAULT" \
  --from-literal=super-admin-password="$(openssl rand -base64 16)"

kubectl create secret generic opal-credentials -n opa \
  --from-literal=opal-auth-token="$(openssl rand -base64 32)" \
  --from-literal=pg-password="$PG_PASS_FROM_VAULT"

kubectl create secret generic resource-sync-credentials -n resource-sync \
  --from-literal=pg-password="$PG_PASS_FROM_VAULT"

# 步骤 2：部署时校验并引用
./scripts/setup.sh --no-kind
  │
  ├── 校验 iam-credentials 存在 → 否则 exit 1
  ├── Helm install --set existingSecret.name=iam-credentials
  ├── init-job 从 Secret 读取 SUPER_ADMIN_INIT_PASSWORD
  └── 完成部署
```

### 4.3 气隙/离线模式

与生产模式相同，Secret 由运维从离线密码管理器（如 KeePass）取出后创建。

## 5. 审计与监控

### 5.1 关键事件

所有以下事件必须落审计日志：

| 事件 | 来源 | 审计字段 |
|---|---|---|
| Secret 创建/更新 | K8s audit log | actor, namespace, secret_name, operation |
| super-admin 首次登录改密 | Keycloak event | user_id, timestamp, event_type=UPDATE_PASSWORD |
| tenant-admin 创建 | keycloak-proxy | actor, tenant_realm, admin_email |
| tenant-admin initial_password 返回 | keycloak-proxy | actor, tenant_realm（**不记录密码值**） |
| 密码过期/强制改密 | Keycloak event | user_id, event_type |

### 5.2 监控告警

- Secret 超过 90 天未轮换 → 告警
- Bootstrap 密码字段（如 `super-admin-password`）在 Secret 中超过 7 天未删除 → 告警
- 任何 Pod 环境变量中出现密码字面量（通过 K8s audit hook 检查） → 告警

## 6. 迁移计划

### 6.1 阶段划分

| 阶段 | 范围 | 交付物 |
|---|---|---|
| P0：values.yaml 清理 | 删除所有默认密码字段 | 改造后的 values.yaml + required 校验 |
| P1：Helm secret.yaml 改造 | pre-install hook + existingSecret 支持 | secret-generator-job.yaml |
| P2：deployment 模板改造 | 所有 env 改为 secretKeyRef | keycloak/postgres/pep-proxy/resource-sync 模板 |
| P3：init-keycloak.py 改造 | 移除默认值，temporary=true | 改造后的 init 脚本 |
| P4：tenants.py 改造 | 随机密码 + API 响应返回 | 改造后的租户创建逻辑 |
| P5：测试脚本改造 | 所有测试脚本从 env/Secret 读密码 | test.sh, bench.sh, uitest.sh |
| P6：文档更新 | README + 部署手册更新 | 部署指南 v2 |
| P7（可选）：邀请制 | SMTP 集成 + execute-actions-email | 邀请邮件流程 |

### 6.2 向后兼容

- 不兼容：旧的 `SuperInit@123` 等默认密码完全移除
- 升级路径：已部署的集群需运维手工改密，然后删除旧 Secret 的明文字段
- 灰度：建议新集群直接采用，老集群在下次停机窗口统一迁移

## 7. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| Helm hook Job 失败 | 部署中断 | `hook-delete-policy: hook-failed` 保留失败 Pod 日志；运维可手动创建 Secret 后重跑 |
| 运维丢失 super-admin 密码 | 无法登录平台 | Keycloak `kcadm.sh` 可从 master admin 重置；或提供 recovery Job |
| Secret 被意外删除 | 所有服务无法启动 | 启用 K8s RBAC 限制 delete 权限；启用 Secret 备份（Velero 等） |
| Pod 环境变量被其他容器读取 | 密码泄露 | Pod 级 SecurityContext 限制；禁用 shareProcessNamespace |
| DB_URL 中的 `$(PG_PASSWORD)` 被日志捕获 | 密码泄露 | 禁用 FastAPI/psycopg2 的 DEBUG 日志；review 所有 log 输出 |
| 邀请邮件被中间人截获 | tenant-admin 账号被劫持 | 激活链接短时效（24h）+ 一次性 token + HTTPS |
| 运维脚本中硬编码密码 | 密码泄露 | 脚本从 `openssl rand` / Vault 读取，review 禁止硬编码 |

## 8. 未来演进

### 8.1 短期（3 个月内）

- 所有 bootstrap 密码支持自动轮换（运维触发）
- Secret 变更后自动触发相关 Pod 滚动重启（如 stakater/Reloader）

### 8.2 中期（6 个月内）

- 集成 HashiCorp Vault 或云厂商 KMS
- 支持动态 DB 凭证（Vault dynamic credentials）
- MFA 能力（TOTP/WebAuthn）

### 8.3 长期

- 零信任架构：移除长期凭证，全部改为短期 token
- SPIFFE/SPIRE 做服务间认证，替代 OPAL token

## 9. 实现检查清单

编码阶段参照此清单逐项完成：

### values.yaml 清理
- [ ] `charts/keycloak/values.yaml`：删除 `keycloak.admin.password`
- [ ] `charts/keycloak/values.yaml`：删除 `postgres.auth.password`
- [ ] `charts/keycloak/values.yaml`：删除 `keycloakInit.superAdmin.password`
- [ ] `charts/keycloak/values.yaml`：删除 `keycloakInit.defaultTenant.adminPassword`
- [ ] `charts/keycloak/values.yaml`：删除 `keycloakInit.defaultTenant.normalPassword`
- [ ] `charts/keycloak/values.yaml`：新增 `existingSecret` 配置块
- [ ] `charts/opa/values.yaml`：删除 `opalServer.authToken`
- [ ] `charts/opa/values.yaml`：DB URI 移除密码（改为 host/user/db，密码拆分）
- [ ] `charts/resource-sync/values.yaml`：DB URI 移除密码

### Helm 模板改造
- [ ] `charts/keycloak/templates/secret.yaml`：改为条件渲染 + pre-install hook
- [ ] `charts/keycloak/templates/_helpers.tpl`：新增 `keycloak.secretName` helper
- [ ] `charts/keycloak/templates/keycloak-statefulset.yaml`：env 改 secretKeyRef
- [ ] `charts/keycloak/templates/keycloak-init-job.yaml`：3 个密码 env 改 secretKeyRef
- [ ] `charts/keycloak/templates/keycloak-proxy-deployment.yaml`：env 改 secretKeyRef
- [ ] `charts/keycloak/templates/postgres-statefulset.yaml`：env 改 secretKeyRef
- [ ] `charts/opa/templates/opal-server-deployment.yaml`：token + DB URI 改 secretKeyRef
- [ ] `charts/opa/templates/pep-proxy-deployment.yaml`：token + DB URI 改 secretKeyRef
- [ ] `charts/resource-sync/templates/deployment.yaml`：DB URI 改 secretKeyRef

### 应用代码改造
- [ ] `images/keycloak-init/init-keycloak.py`：`os.getenv` 改 `os.environ[]`
- [ ] `images/keycloak-init/init-keycloak.py`：super-admin 密码 `temporary=true`
- [ ] `da-idb-proxy/app/api/v1/tenants.py`：tenant-admin 改随机密码 + 响应返回
- [ ] `da-idb-proxy/app/schemas/realm.py`：`TenantResponse` 新增 `initial_password` 字段
- [ ] （可选）`da-idb-proxy/app/api/v1/tenants.py`：支持邀请邮件模式（需 SMTP）

### 部署脚本改造
- [ ] `scripts/setup.sh`：`--no-kind` 模式校验外部 Secret 存在
- [ ] `scripts/setup.sh`：默认模式部署后打印 super-admin 一次性密码获取命令
- [ ] `scripts/cleanup.sh`：清理 Secret

### 测试脚本改造
- [ ] `scripts/test.sh`：所有密码 `:-` 默认值改为 `:?` 必填校验
- [ ] `scripts/bench.sh`：同上
- [ ] `scripts/uitest.sh`：同上
- [ ] `scripts/test-external-idp.sh`：同上

### 文档
- [ ] 更新 `README.md`：移除默认密码，替换为"首次部署"章节
- [ ] 更新 `da-cluster/docs/deployment-guide.md`：加入 Secret 预创建步骤
- [ ] 新增 `da-cluster/docs/secrets-operation-guide.md`：Secret 管理手册

### 验证
- [ ] 从干净环境 `setup.sh` → 全流程通过 → 所有密码都不是默认值
- [ ] `setup.sh --no-kind` 未创建 Secret → 部署失败并给出清晰错误
- [ ] `setup.sh --no-kind` 预创建 Secret → 部署成功
- [ ] super-admin 首次登录 → 触发 UPDATE_PASSWORD
- [ ] tenant-admin 创建 → API 响应包含 `initial_password`，后续查询不再返回
- [ ] 所有 Pod 环境变量 `kubectl describe pod` 不含明文密码（value 为 `<set to the key 'xxx' in secret 'xxx'>`）
- [ ] 代码扫描工具（truffleHog / gitleaks）扫描无高危发现

## 10. 附录

### 10.1 相关设计文档

- `diagrams/story-breakdown.md` — SR01/SR02 用户组模型
- `diagrams/onboarding-flow.md` — 租户接入流程
- `diagrams/component-responsibility.md` — keycloak-proxy 职责
- `diagrams/data-storage.md` — api_keys 表定义

### 10.2 参考标准

- NIST SP 800-63B §5.1.1.2：禁止预置密码
- OWASP ASVS V2.1 §2.1.1：密码不应硬编码
- CIS Kubernetes Benchmark §5.4.1：Secret 而非环境变量明文
