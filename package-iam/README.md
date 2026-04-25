# aidp-iam 独立部署包

IAM 业务栈：Keycloak + OPA/OPAL/pep-proxy + resource-sync + IAM 自身路由（Keycloak / 身份 API / ACL API / path-rules）。

**前提**：先装 [package-gateway](../package-gateway/) —— 本包不包含 Gateway 控制器和 CRD，依赖 Gateway 的 `eg` Gateway 资源已存在于 `envoy-gateway-system` 命名空间。

```
package-iam/
├── README.md                                     ← 你正在看
├── charts/
│   └── aidp-iam/                                 IAM Helm chart
│       ├── Chart.yaml
│       ├── values.yaml
│       ├── charts/                               vendored 子 chart
│       │   ├── keycloak/                         Keycloak + Postgres + 内置 init Job
│       │   ├── opa/                              OPAL Server + bundle-server + pep-proxy
│       │   └── resource-sync/                    resource-sync HTTP + ext_proc
│       └── templates/                            IAM 自身路由 4 件套
│           ├── namespaces.yaml                   keycloak / opa / resource-sync ns
│           ├── reference-grants.yaml             3 条跨 ns 引用授权
│           ├── routes-public.yaml                /realms / /admin / /resources
│           ├── routes-protected.yaml             /api/v1/* / /acl/v1/*
│           └── security-policy.yaml              ext_authz 绑 pep-proxy
└── images/arm64/                                 8 个镜像 tar (~1.4 GB)
    ├── keycloak-custom_26.5.2.tar
    ├── keycloak-init_v2.tar                      自定义，需 alias
    ├── keycloak-proxy_v3.tar                     自定义，需 alias
    ├── postgres_17.tar
    ├── permitio_opal-server_0.7.4.tar
    ├── permitio_opal-client_0.7.4.tar
    ├── opal-proxy_v2.tar                         自定义，需 alias
    └── resource-sync_v1.tar                      自定义，需 alias
```

---

## 部署

### 1. 加载镜像（每个节点）

```bash
# iSulad
for tar in package-iam/images/arm64/*.tar; do isula load -i "$tar"; done

# containerd
for tar in package-iam/images/arm64/*.tar; do ctr -n k8s.io images import "$tar"; done

# Docker
for tar in package-iam/images/arm64/*.tar; do docker load -i "$tar"; done
```

### 2. arch 后缀 alias（自定义镜像必须做）

`keycloak-init` / `keycloak-proxy` / `opal-proxy` / `resource-sync` 这 4 个 tar 内 tag 是 `<name>:<tag>-arm64`，chart 引用的是干净 tag。按运行时挑一段：

```bash
# iSulad
ARCH=arm64
for img in keycloak-init:v2 keycloak-proxy:v3 opal-proxy:v2 resource-sync:v1; do
  isula tag docker.io/library/${img}-${ARCH} docker.io/library/${img} 2>/dev/null
done

# containerd
ARCH=arm64
for img in keycloak-init:v2 keycloak-proxy:v3 opal-proxy:v2 resource-sync:v1; do
  ctr -n k8s.io images tag docker.io/library/${img}-${ARCH} docker.io/library/${img} 2>/dev/null
done
```

`keycloak-custom` / `postgres` / `permitio/*` tag 是干净的，**不需要** alias。

### 3. helm install

```bash
helm install aidp-iam package-iam/charts/aidp-iam \
  --namespace aidp-iam \
  --wait --timeout=10m \
  --set keycloak.keycloak.config.hostname=http://<EIP>:30080
```

集群没有默认 StorageClass 时加上：

```bash
  --set keycloak.postgres.persistence.storageClass=dorado-inner-nas
```

> `keycloak.keycloak.config.hostname` 两层 `keycloak` 是因为第一层是子 chart 名（umbrella 里的依赖名），第二层是子 chart 内部的 `keycloak:` 顶级键。

---

## 验证

```bash
# 1. Pod 全 Ready
kubectl get pods -n keycloak -n opa -n resource-sync

# 2. Keycloak 通过 Gateway 可达
curl http://<节点 IP>:30080/realms/master/.well-known/openid-configuration | head -c 200

# 3. 管理员 token
curl -X POST http://<节点 IP>:30080/realms/master/protocol/openid-connect/token \
  -d "grant_type=password&client_id=admin-cli&username=admin&password=admin" \
  | python -c "import sys,json; print(json.load(sys.stdin)['access_token'][:50])"

# 4. IAM API（aidp realm 已自动创建）
curl http://<节点 IP>:30080/realms/aidp/.well-known/openid-configuration | head -c 200
```

---

## 内置数据

`keycloak-init` Job 在首次部署时**幂等**写入：

| 表 / 资源 | 内容 |
|---------|------|
| Keycloak realms | `master`（默认）+ `aidp`（业务 realm） |
| Keycloak groups | `master-admins` / `tenant-admins` / `all-users` / `{app}-admins` |
| Keycloak clients | `aidp-client`（业务用） + 各应用 OIDC client |
| `apps` 表 | `knowledgebase` / `rubik` / `memory` 三个预置应用 |
| `resource_patterns` | 这三个应用的 `id_source` / `id_field` / `actions` 配置 |
| `path_rules` + `path_rule_groups` | 管理路径白名单 |

重复执行 `helm upgrade` 不覆盖人工修改的字段。新业务接入需要的`POST /api/v1/apps` 仅用于「**预置之外**的全新应用」。

---

## 装完之后能干嘛

- ✅ Keycloak 登录 / 用户管理 / OIDC 已经可用
- ✅ IAM 控制面 API（`/api/v1/*`、`/acl/v1/*`、`/api/v1/path-rules`）已经可用
- ✅ pep-proxy 已就绪，能被业务的 SecurityPolicy 引用做 ext_authz
- ✅ resource-sync 已就绪，能被业务的 EnvoyExtensionPolicy 引用做 ext_proc
- ❌ **没装任何业务后端** —— 真要看到端到端流程要加装 [package-mock-kb](../package-mock-kb/)（KB 业务样例）

---

## 清理

```bash
helm uninstall aidp-iam -n aidp-iam
kubectl delete ns keycloak opa resource-sync --ignore-not-found
```

---

## 跟主包关系

| 包 | 作用 | 是否依赖此包 |
|----|------|-----------|
| `package-gateway/` | 网关基建 + CRD | ❌ 它先装，IAM 装在它之上 |
| `package-iam/`（本包） | IAM 业务栈 | — |
| `package-mock-kb/` | KB 业务样例 | ✅ 依赖本包提供的 pep-proxy / resource-sync |
| `package-mock-rubik/`（待出） | Rubik 业务样例 | ✅ 同上 |
| `package-mock-memory/`（待出） | Memory 业务样例 | ✅ 同上 |
