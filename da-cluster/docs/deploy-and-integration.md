# 从 0 部署 + 后端联调指南

> 这是 **deploy-guide** / **integration-guide** / **setup-isula 说明** 的合并版。
> 目标读者：拿到离线发布包，需要把 aidp-iam 部署到自己集群（Kind / 标准 K8s / 华为云 iSula），再把真实 KB / Rubik / Memory / … 后端挂上去联调。

---

## 1 架构一张图

```
          ┌─────────────────────────── Envoy Gateway (eg) ──────────────────────────┐
  Browser │  HTTPRoute*                                                             │
  ──────►│  ├─ keycloak-route            → Service keycloak.keycloak:8080          │
          │  ├─ keycloak-proxy-route     → Service keycloak-proxy.keycloak:8090    │
          │  │   （/api/v1/tenants, /api/v1/apps, /api/v1/permission-groups）        │
          │  ├─ identity-api-route       → Service keycloak-proxy.keycloak:8090    │
          │  │   （/api/v1/{realm}/{groups|users|idp|api-keys|permissions|token}）  │
          │  ├─ acl-api-route            → Service resource-sync.resource-sync:8080│
          │  ├─ <your-app>-route         → Service <your-backend>.<your-ns>:<port> │
          │  └─ …                                                                  │
          │                                                                         │
          │  SecurityPolicy.extAuth (pep-proxy) ──► grpc pep-proxy.opa:9000         │
          │  EnvoyExtensionPolicy.extProc (resource-sync) ──► grpc resource-sync… │
          └──────────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
     ┌─── pep-proxy ────┐   ┌── OPA（Rego + bundle-server）──┐   ┌── resource-sync ──┐
     │ JWT/API-Key 校验 │◄─►│ /authz 查 path_rule (OR-语义)   │◄─►│ ext_proc 观察 2xx  │
     │ 路径级 OPA 调用  │   │ data 从 bundle-server 周期拉取 │   │ 写 resource_acl    │
     │ 资源级 ACL 检查 │   │ (permission_groups 三张表 JOIN)│   │ 注入 X-Allowed-Ids │
     └──────┬───────────┘   └────────────────────────────────┘   └────────┬──────────┘
            │                                                              │
            └─────────── iam Postgres (keycloak.postgres:5432/iam) ────────┘
```

**请求流 (ext_authz)**：`Client → Envoy → SecurityPolicy → pep-proxy.grpc → OPA.rego → allow?`
**ACL 自写流 (ext_proc)**：`Backend 2xx 响应 → Envoy → EnvoyExtensionPolicy → resource-sync → INSERT resource_acl`

---

## 2 部署脚本选择

| 场景 | 脚本 | 说明 |
|------|------|------|
| 本地开发 / 演示 / 跑 test.sh | `./scripts/setup.sh` | 自动建 Kind 集群 + 加载所有 offline tar + 部署 + 内置三个 mock 后端 |
| 标准 K8s 集群 (离线) | `./scripts/setup.sh --no-kind` | 跳过 Kind；通过 `ctr` 或 SSH 把 tar 装进节点 |
| 华为云 K8s + iSula 运行时 | `./scripts/setup-isula.sh [--load-images]` | 针对 iSula 专门裁剪：不装 mock、补 securityContext、NodePort 暴露 30080 |
| 重建镜像 (需要互联网) | `./scripts/setup.sh --build` | 不用 offline tar，直接 `docker build` |
| 从 base 镜像加速重建 (断网) | `./scripts/setup.sh --fat-base` | 从 `base-*.tar` 装基础镜像再 slim build |

三条脚本的 **共同假设**：当前目录有 `da-cluster/offline/{charts,crds,images/{amd64,arm64}}/`。这些由 `./scripts/build-release-images.sh` 产出，也是 GitHub release 的内容。

---

## 3 从 0 部署：完整步骤

### 3.1 拿到离线包

从 GitHub Releases 下载对应平台的 `aidp-iam-offline-<version>-<arch>.tar.gz`，解压到仓库根：

```bash
tar xzf aidp-iam-offline-v0.5.0-amd64.tar.gz
# 得到
# da-cluster/offline/images/amd64/*.tar    ← 所有镜像
# da-cluster/offline/charts/               ← helm 包（upstream gateway + 本仓库打包）
# da-cluster/offline/crds/                 ← Gateway API + Envoy Gateway CRD
```

### 3.2 Kind 模式（本地开发）

```bash
cd da-cluster
./scripts/setup.sh
# 等约 2-5 分钟，输出最后会列出所有 pod
./scripts/test.sh        # 126 断言；跑通即部署 OK
```

访问：`kubectl -n aidp-iam port-forward svc/<envoy-proxy-svc> 8080:80` → `http://localhost:8080`。

### 3.3 标准 K8s (离线) 模式

```bash
export K8S_NODES="10.0.0.1 10.0.0.2"   # 集群里所有节点的 IP
export K8S_NODE_USER=root
./scripts/setup.sh --no-kind
# 脚本会 scp offline tar 到每台节点、ctr 导入、再 helm install aidp-iam
```

如果节点 `ctr` 不在 PATH，需要手工导 tar（见 `setup.sh` 第 440-480 行的示例命令）。

### 3.4 华为云 K8s + iSula

```bash
export KC_HOSTNAME=http://<EIP 或 域名>:30080    # 强制必填
export STORAGE_CLASS=dorado-inner-nas              # 可选，自动侦测
./scripts/setup-isula.sh --load-images
```

iSula 脚本的几个特殊点：
1. `KC_HOSTNAME` 必填 — 不填的话 Keycloak 的 OIDC redirect 会回到 `http://keycloak:8080`，浏览器打不开。
2. 自动把 Envoy Gateway controller 改为 `runAsUser: 0 / fsGroup: 0`（华为容器镜像要求）。
3. 把 service 类型改成 `NodePort`，端口 **30080**（业务路径 `http://NodeIP:30080/...`）。
4. 不装 `mock-kb / mock-rubik / mock-memory`——生产环境要由你自己挂真实后端。
5. 自动删除了 `mock-kb-route / mock-rubik-route`、以及 `resource-sync-extproc`（因为没有目标 HTTPRoute 时 Envoy Gateway 会挂 Ready=False）。联入真实后端后记得补 `EnvoyExtensionPolicy`（见 §5）。

---

## 4 端口与主机名速查

| 名字 | 默认端口 | 用途 | 怎么改 |
|------|---------|------|------|
| Kind port-forward | 8080 | 本地开发 | `kubectl port-forward -n aidp-iam svc/envoy-eg 8080:80` 任意改 |
| iSula NodePort | 30080 | 生产对外访问 | 改 `setup-isula.sh` 里的 NodePort（搜 `30080`）或部署后 `kubectl -n aidp-iam patch svc <envoy-svc> -p '{"spec":{"ports":[{"port":80,"nodePort":<NEW>}]}}'` |
| keycloak 内部 | 8080 | pod-to-pod | 不要改；Deployment/Statefulset 固定 |
| keycloak-proxy | 8090 | IAM REST | Service 固定；前端请求应走 gateway 的 80/30080，**不要** 直连 8090 |
| postgres | 5432 | iam 数据库 | `package-iam/charts/aidp-iam/charts/keycloak/values.yaml` 里的 `postgres.port` |
| pep-proxy | 8000 HTTP + 9000 gRPC | ext_authz | 固定；Envoy Gateway 的 SecurityPolicy 调用 9000 |
| resource-sync | 8080 HTTP + 8082 gRPC | ACL 管理 + ext_proc | 固定；EnvoyExtensionPolicy 调用 8082 |

### `KC_HOSTNAME` / Keycloak 外部主机名

**什么时候需要改？**

| 场景 | 需要配 KC_HOSTNAME 吗？ | 原因 |
|-----|------------------------|-----|
| Kind 本机 port-forward :8080 | 不需要 | 默认 `http://localhost:8080` 够用 |
| K8s Ingress / LB 暴露给浏览器 | **必填** | 否则 OIDC discovery 把 issuer 写成 `http://keycloak:8080`，浏览器解析失败 |
| iSula + NodePort | **必填** | 同上；脚本已把它提升为必填环境变量 |

**怎么配？** 在 helm install 时追加：

```bash
helm upgrade -i aidp-iam package-iam/charts/aidp-iam -n aidp-iam \
  --set keycloak.keycloak.config.hostname=http://iam.example.com:30080
```

（`setup-isula.sh` 已经自动帮你 set。）改了之后必须 rollout restart keycloak statefulset。

---

## 5 接入一个真实后端（以"Foo 应用"为例）

> 这是最常见的联调动作：已经部署了 aidp-iam，现在要把 `foo-backend` (Service 在 `foo-ns` 命名空间) 接入授权。

### 5.1 在 iam DB 注册应用 + 资源模式

用管理员账号登录，拿到 token，然后 CURL `POST /api/v1/apps`：

```bash
TOKEN=$(curl -s -X POST http://<GATEWAY>/realms/aidp/protocol/openid-connect/token \
  -d "client_id=aidp-client" -d "client_secret=$CS" \
  -d "grant_type=password" -d "username=admin" -d "password=Admin@123" \
  | jq -r .access_token)

curl -X POST http://<GATEWAY>/api/v1/apps \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "app_name": "foo",
    "path_prefix": "/foo/",
    "display_name": "Foo 应用",
    "resource_patterns": [{
      "resource_prefix": "/v1/items",
      "method": "",
      "resource_type": "item",
      "id_source": "path",
      "id_field": "id",
      "actions": []
    }]
  }'
```

调用成功后：
- `apps` 表新增一行；
- `resource_patterns` 表新增一行；
- Keycloak 自动创建 `foo-admins` 组（归属应用超级管理员）；
- bundle-server 下一次 tick（≤10s）会把这条资源模式加进 OPA data。

如果需要给应用设置权限点（路径级白名单）：`POST /api/v1/permission-groups`（见 `diagrams/api-spec-v2.xlsx` sheet 1 "权限点CRUD" 区域）。

### 5.2 声明 HTTPRoute（把路径导向后端 Service）

假设 `foo-backend` Service 在 `foo-ns:8000`：

```yaml
# foo-route.yaml
apiVersion: gateway.networking.k8s.io/v1beta1
kind: ReferenceGrant
metadata:
  name: foo-ns-from-gateway
  namespace: foo-ns
spec:
  from:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    namespace: envoy-gateway-system
  to:
  - group: ""
    kind: Service
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: foo-route
  namespace: envoy-gateway-system
spec:
  parentRefs:
  - name: eg
  rules:
  - matches:
    - path: {type: PathPrefix, value: /foo/}
    filters:
    - type: URLRewrite
      urlRewrite:
        path: {type: ReplacePrefixMatch, replacePrefixMatch: /}
    backendRefs:
    - name: foo-backend
      namespace: foo-ns
      port: 8000
```

```bash
kubectl apply -f foo-route.yaml
```

### 5.3 把 HTTPRoute 挂到 ext_authz + ext_proc

```bash
# 1) SecurityPolicy 加进 spec.targetRefs
kubectl -n envoy-gateway-system get securitypolicy pep-proxy-extauthz -o yaml > sp.yaml
# 编辑 sp.yaml，在 spec.targetRefs 下 append：
#   - group: gateway.networking.k8s.io
#     kind: HTTPRoute
#     name: foo-route
kubectl apply -f sp.yaml

# 2) EnvoyExtensionPolicy 加一份新的（或 append targetRefs）
cat <<EOF | kubectl apply -f -
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: EnvoyExtensionPolicy
metadata:
  name: resource-sync-extproc-foo
  namespace: envoy-gateway-system
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: foo-route
  extProc:
  - backendRefs:
    - name: resource-sync
      namespace: resource-sync
      port: 8082
    messageTimeout: 30s
    processingMode:
      request: {headerMode: Send, bodyMode: Streamed}
      response: {headerMode: Send, bodyMode: Streamed}
EOF
```

### 5.4 联调验证

```bash
# 未带 token → 401
curl -i http://<GATEWAY>/foo/v1/items         # 应看到 WWW-Authenticate

# 带 admin token → 到达 foo-backend
curl -H "Authorization: Bearer $TOKEN" http://<GATEWAY>/foo/v1/items

# 看 pep-proxy 日志
kubectl -n opa logs -l app=pep-proxy -f | grep foo

# 看 resource-sync 是否写 ACL
kubectl -n keycloak exec postgres-0 -c postgres -- \
  psql -U keycloak -d iam -c "SELECT * FROM resource_acl WHERE app_name='foo';"
```

---

## 6 常用 CRUD 接口（调试时超高频）

完整表格见 `diagrams/api-spec-v2.xlsx` sheet1 "IAM 系统接口"（65 行）。这里列出最常用的 15 条：

| 动作 | Method + 路径 | Body |
|-----|---------------|------|
| 拿 admin token | POST `/realms/aidp/protocol/openid-connect/token` | 标准 OIDC password grant |
| 列应用 | GET `/api/v1/apps` | — |
| 注册应用 | POST `/api/v1/apps` | `{app_name, path_prefix, resource_patterns}` |
| 列权限点 | GET `/api/v1/permission-groups?app_name=foo` | — |
| 新建权限点 | POST `/api/v1/permission-groups` | `{app_name, name, paths, bindings}` |
| 改权限点路径 | PUT `/api/v1/permission-groups/{id}` | `{paths:[...]}` （replace-all） |
| 给权限点加绑定 | POST `/api/v1/permission-groups/{id}/bindings/{kc_group_name}` | — |
| 列用户 | GET `/api/v1/aidp/users` | `?search=` |
| 建用户 | POST `/api/v1/aidp/users` | `{username, password, groups:[gid], temporary_password:false}` |
| 配组权限 | PUT `/api/v1/aidp/groups/{gid}/permissions` | `{permission_group_ids:[1,3,5]}` |
| 列组 | GET `/api/v1/aidp/groups` | — |
| 新增 ACL | POST `/acl/v1/resources/{rid}/permissions` | `{app_name, resource_type, subject_type:"group", subject_id:"all-users", permission:"viewer"}` |
| 查看 ACL | GET `/acl/v1/resources/{rid}/permissions?app_name=foo&resource_type=item` | — |
| 启停 app | PUT `/api/v1/apps/{app_name}` | `{enabled:false}` |
| 改资源模式 | PUT `/api/v1/apps/{app_name}/resource-patterns?resource_prefix=&method=` | `{id_source, id_field, share_to_...}` |

---

## 7 iSula 特别注意

1. **chart 模板**：v1.6.2 起部署入口统一为 `package-gateway/charts/aidp-gateway` 和 `package-iam/charts/aidp-iam`；旧的 `da-cluster/charts/*` 独立 chart 已移除。
2. **gateway-routes 独立文件**：路由定义在 `da-cluster/gateway-routes/{reference-grants,keycloak-routes,protected-routes}.yaml`，脚本会 `kubectl apply` 三个文件。改路由优先改这三个文件而不是 chart template。
3. **mock 后端**：iSula 模式不部署 mock-*。如果 `test.sh` 的 Section 10/11 报 FAIL，那是预期内——你的真实后端部署好之后，可以把 mock 相关断言改掉重跑。
4. **resource-sync-extproc**：脚本删除了这个默认策略。当你接入真实后端后，**必须**自己重建一份指向真实 HTTPRoute 的 `EnvoyExtensionPolicy`（见 §5.3）。
5. **StorageClass**：华为云常见值是 `dorado-inner-nas` 或 `csi-disk`，脚本会自动取第一个可用 SC。手动：`STORAGE_CLASS=xxx`。

---

## 8 常见问题排查

| 症状 | 根因 | 修复 |
|------|------|------|
| 浏览器登录跳回 `http://keycloak:8080` 404 | `KC_HOSTNAME` 没设 | `helm upgrade keycloak --set keycloak.config.hostname=<外部URL>` + rollout restart |
| `Invalid client or Invalid client credentials` (keycloak-proxy 日志) | keycloak-proxy pod 启动早于 `keycloak-aidp-client` Secret 创建 | `kubectl -n keycloak rollout restart deployment/keycloak-proxy` |
| `apps table empty (attempt 30/30)` (resource-sync 日志) | init job 启动失败过一次；resource-sync 只在启动时读一次 | 先等 init 成功（`kubectl -n keycloak get jobs`），再 `kubectl -n resource-sync rollout restart deployment/resource-sync` |
| pep-proxy 返回 `app_disabled` | `apps.enabled = false`（手动 `PUT /apps/{name} {"enabled":false}` 过） | `PUT /apps/{name} {"enabled":true}`，等 10s OPA tick |
| 404 on `/api/v1/permission-groups` | gateway 旧路由没更新 | 查 `kubectl -n envoy-gateway-system get httproute keycloak-proxy-route -o yaml`，对比 `da-cluster/gateway-routes/protected-routes.yaml`，重新 apply |
| ACL 没写（bob 能看到 alice 的资源） | `resource-sync` pod 启动时 apps 表还是空的 → 缓存了空 pattern | rollout restart resource-sync |
| `SPI mapper failed (404)` → JWT 缺 `group_ids` | keycloak-custom 镜像里没打包 `StructuredGroupMapper.jar` | 用最新 offline tar；或 `docker build -t keycloak-custom:26.5.2 da-cluster/images/keycloak-custom` 重建 |

---

## 9 Offline 发布包怎么打

```bash
cd da-cluster
./scripts/build-release-images.sh --arch amd64   # 或 arm64
# 产出：
#   release-images/amd64/*.tar   (所有镜像)
#   offline/charts/              (helm tgz)
#   offline/crds/                (CRD YAML)
# 再 tar.gz 成一个分发包：
tar czf aidp-iam-offline-$(git rev-parse --short HEAD)-amd64.tar.gz \
  offline/ release-images/amd64/
```

两种架构都要出包：
```bash
./scripts/build-release-images.sh --arch amd64
./scripts/build-release-images.sh --arch arm64
```

arm64 要么在 arm 主机上跑脚本，要么 docker buildx + QEMU 交叉编译。

---

## 10 联调 checklist（剪下来用）

- [ ] `kubectl get ns keycloak opa resource-sync aidp-iam envoy-gateway-system` 五个 ns 都在
- [ ] `kubectl -n keycloak get jobs keycloak-init` 是 `Completed`
- [ ] `kubectl -n keycloak get secret keycloak-aidp-client` 存在且 `client-secret` 非空
- [ ] admin token 能拿到（`POST /realms/aidp/protocol/openid-connect/token`）
- [ ] `GET /api/v1/apps` 返回 3 条（knowledgebase / rubik / memory）
- [ ] `GET /api/v1/permission-groups` 返回 46 条（5 平台级 + 22 kb + 14 rubik + 5 memory）
- [ ] pep-proxy 启动日志看到 `Loaded X apps, Y resource_patterns`（X/Y 非 0）
- [ ] resource-sync 启动日志同上
- [ ] `test.sh` 全绿（126 断言）
