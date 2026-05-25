# db-provisioner — openGauss 自动建库模板

一个最小可复用的 Helm 模板，演示 **"共享 openGauss + 自动为每个应用建 DB/User/Secret + 应用通过 Secret 零侵入消费"** 的整套编排。

用来当骨架改：想换成 MySQL / Oracle / 别的数据库时，只需改 `provisioner.py` 里的 SQL 和 `statefulset.yaml` 里的镜像 & env。

---

## 1. 这是什么

部署完成后会得到：

- 一个单副本的 openGauss StatefulSet（namespace `opengauss`）
- 一个随机生成的超级用户 Secret（`opengauss/opengauss-super`）
- 每个在 `values.yaml::databases` 里声明的应用：
  - 对应的 database
  - 独立的 user（独立密码）
  - Secret 写进应用自己的 namespace（`<ns>/<secretName>`，字段 `host/port/database/username/password`）
- 示例 `test-app` chart：通过 `secretKeyRef` 从 Secret 读连接信息，initContainer 用 psycopg2 验证能连通

全程 **helm 原生 hook** 编排，无额外 operator 依赖。

## 2. 前置要求

- Docker（构建 provisioner 镜像 + Kind）
- [kind](https://kind.sigs.k8s.io/) v0.22+
- kubectl、helm v3

## 3. 架构

```
┌── pre-install hooks (-30 → -10) ──────────────────────────────┐
│  -30  Namespace/opengauss                                      │
│  -20  SA/Role/Binding/super-secret-generator                   │
│  -10  Job/opengauss-super-secret-init                          │
│        └─ python bootstrap.py                                  │
│        └─ 幂等：Secret 不存在 → 生成 24 位强密码 → 写 Secret     │
└────────────────────────────────────────────────────────────────┘
                           ↓
┌── regular resources ───────────────────────────────────────────┐
│  Namespace/<app-ns> × N    (Secret 落地位置)                   │
│  Service/opengauss                                             │
│  StatefulSet/opengauss (GS_PASSWORD from super Secret)         │
└────────────────────────────────────────────────────────────────┘
                           ↓
┌── post-install/upgrade hooks (5 → 10) ─────────────────────────┐
│  5   SA/ClusterRole/Binding/db-provisioner                     │
│  5   ConfigMap/db-provisioner-config (databases.yaml)          │
│  10  Job/db-provisioner                                        │
│       └─ initContainer: TCP 探活 opengauss:5432                │
│       └─ python provisioner.py                                 │
│          · 读 Secret → 复用密码（或生成新的）                  │
│          · CREATE/ALTER USER、CREATE DATABASE                   │
│          · GRANT ALL ON SCHEMA public                          │
│          · 创建/patch 应用 Secret                              │
└────────────────────────────────────────────────────────────────┘
                           ↓
┌── 应用 chart（独立 release）───────────────────────────────────┐
│  Deployment                                                     │
│    initContainer wait-secret: 轮询 Secret 就绪                 │
│    initContainer verify-db:   psycopg2 连接 + 读写验证         │
│    container app:             从 Secret 读 DB_HOST/USER/...    │
└─────────────────────────────────────────────────────────────────┘
```

## 4. 快速上手

```bash
cd db-provisioner-test

# 1. 创建 kind 集群
kind create cluster --config kind-config.yaml
kubectl config use-context kind-db-test

# 2. 构建并 load provisioner 镜像到 kind
./images/db-provisioner/build.sh
# 生成 db-provisioner:local，同时注入 kind 集群 "db-test"

# 3. 安装 openGauss + 自动建库
helm install opengauss charts/opengauss --wait --timeout 10m

# 4. 安装示例应用，观察它通过 Secret 连 DB
helm install test-kb charts/test-app --wait --timeout 3m
POD=$(kubectl -n knowledgebase get pod -l app=test-kb-app -o jsonpath='{.items[0].metadata.name}')
kubectl -n knowledgebase logs $POD -c verify-db
# 预期末尾看到：[verify] OK

# 5. 清理
helm uninstall test-kb
helm uninstall opengauss
kind delete cluster --name db-test
```

## 5. 自定义

### 5.1 新增一个应用 DB

编辑 `charts/opengauss/values.yaml` 追加一项：

```yaml
databases:
  - name: kb
    user: kb
    secretName: db-credentials-kb
    secretNamespace: knowledgebase
  - name: memory
    user: memory
    secretName: db-credentials-memory
    secretNamespace: memory
  # 追加 ↓
  - name: llm
    user: llm
    secretName: db-credentials-llm
    secretNamespace: llm
```

```bash
helm upgrade opengauss charts/opengauss --wait
```

provisioner 是幂等的：老应用的密码和 Secret 完全不会变，仅为新 `llm` 创建 DB/user/Secret。

### 5.2 换一个数据库

|   | 要改的位置 |
|---|---|
| 镜像 | `values.yaml::image.{repository,tag}` |
| 容器 env（密码怎么传给数据库） | `statefulset.yaml` 中 `GS_PASSWORD/GS_USERNAME` → `POSTGRES_PASSWORD` / `MYSQL_ROOT_PASSWORD` 等 |
| 端口 | `values.yaml::service.port` + `statefulset.yaml` 里 `containerPort` |
| SQL 方言 | `images/db-provisioner/provisioner.py`：`CREATE USER`/`CREATE DATABASE`/`GRANT` 几条语句 |
| 驱动 | `images/db-provisioner/Dockerfile` 里把 `psycopg2-binary` 换成对应 driver |
| 密码复杂度 | `provisioner.py::gen_password` 的字符集和规则 |

所以 Python 版本相比 shell 的好处就是：**SQL 分散在几个命名良好的函数里，换方言只改这几处**；参数都走 `psycopg2.sql.Identifier` + bind 参数，不用担心转义。

### 5.3 密码轮换

单个应用强制换密码 = 删 Secret 再 upgrade：

```bash
kubectl -n knowledgebase delete secret db-credentials-kb
helm upgrade opengauss charts/opengauss --wait
# provisioner 发现 Secret 不在 → 生成新密码 → ALTER USER kb → 重建 Secret
kubectl -n knowledgebase rollout restart deployment/test-kb-app
```

超级用户密码一旦生成，helm 不会动它。要轮换就手动：`kubectl delete secret opengauss-super -n opengauss && helm upgrade ...`（注意 openGauss StatefulSet 需要同步重启）。

## 6. 文件布局

```
db-provisioner-test/
├── kind-config.yaml
├── images/db-provisioner/
│   ├── Dockerfile                 # python:3.12-slim + psycopg2 + kubernetes
│   ├── provisioner.py             # DB/User/Secret 幂等 upsert
│   ├── bootstrap.py               # 首次生成超级 Secret
│   └── build.sh                   # docker build + kind load
├── charts/opengauss/
│   ├── Chart.yaml
│   ├── values.yaml                # 镜像、databases 清单
│   └── templates/
│       ├── ns.yaml                # pre-install hook(-30)
│       ├── provisioner-ns.yaml    # 目标应用 ns
│       ├── super-secret-hook.yaml # pre-install(-20/-10)：生成超级 Secret
│       ├── service.yaml
│       ├── statefulset.yaml       # openGauss 主体
│       ├── provisioner-rbac.yaml  # post-install(5)
│       ├── provisioner-config.yaml# databases.yaml ConfigMap
│       └── provisioner-job.yaml   # post-install(10)：调用 provisioner.py
└── charts/test-app/               # 示例消费方
    ├── Chart.yaml
    ├── values.yaml
    └── templates/
        ├── deployment.yaml        # wait-secret + verify-db + app
        └── rbac.yaml              # 允许 default SA 读自己 ns 的 Secret
```

## 7. 关键设计决策

| 决策 | 原因 |
|---|---|
| 超级密码放 **pre-install hook** 里生成，而非 Helm template `randAlphaNum` | template 每次 render 会重算，helm upgrade 会误改密码 |
| 所有 hook 加 `hook-delete-policy: before-hook-creation,hook-succeeded` | 成功清理，失败保留便于排障 |
| 超级密码 hook 幂等（`read_namespaced_secret` 判存在） | helm upgrade 不破坏已有密码 |
| provisioner 先读 Secret 再决定用旧密码还是生成新的 | 应用不中断 |
| Secret 不存在但 user 已存在 → `ALTER USER` | state drift 自愈 |
| Secret 写到**应用自己的** namespace | 消费端不用跨 ns 引用 |
| Namespace 加 `helm.sh/resource-policy: keep` | `helm uninstall` 不删业务 ns 数据 |
| 验证 container 用 psycopg2 而非 psql | openGauss 的 SHA256 认证跟 PG 标准 libpq 有差异，psycopg2-binary 更稳 |

## 8. 已知限制 & 生产改造方向

- **provisioner 镜像是本地 build + kind load**：生产应推到镜像仓库，并在 `values.yaml` 里指定 tag
- **Secret drift 恢复依赖 helm upgrade 触发**：Secret 被删后不 upgrade 不会自愈。生产要么定期 `helm upgrade`，要么独立写个 reconcile CronJob
- **DB 权限粒度 = GRANT ALL**：真实场景应拆分"只读/读写"两个 role
- **无 TLS**：集群内明文连接。生产启用 openGauss SSL 并把 CA 也塞进 Secret
- **单副本 StatefulSet**：高可用要换 openGauss 的主备集群（CM + cm_agent），超出本模板范围
- **密码复杂度假设了 openGauss 的规则**（≥8 位、3/4 字符类）：换别的 DB 要改 `gen_password`
