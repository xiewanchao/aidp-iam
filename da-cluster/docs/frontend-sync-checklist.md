# 前端联调准备清单

## 一、需要给前端的信息

### 1. 接口文档

已有完整文档：[frontend-api-reference.md](frontend-api-reference.md)

接口总览（按角色分）：

| 模块 | 接口 | 方法 | 路径 | 权限 |
|------|------|------|------|------|
| **认证** | 登录（密码） | POST | `/realms/{realm}/protocol/openid-connect/token` | 无 |
| | 登录（OIDC 跳转） | GET | `/realms/{realm}/protocol/openid-connect/auth` | 无 |
| | Token 刷新 | POST | `/realms/{realm}/protocol/openid-connect/token` | 无 |
| **租户管理** | 列表 | GET | `/api/v1/tenants` | super-admin |
| | 创建 | POST | `/api/v1/tenants` | super-admin |
| | 删除 | DELETE | `/api/v1/tenants/{realm}` | super-admin |
| **IDP 接入** | 创建 SAML IDP | POST | `/api/v1/{realm}/idp/saml/instances` | tenant-admin |
| | 查看 IDP 列表 | GET | `/api/v1/{realm}/idp/saml/instances` | tenant-admin |
| | 更新 IDP | PUT | `/api/v1/{realm}/idp/saml/instances` | tenant-admin |
| | 删除 IDP | DELETE | `/api/v1/{realm}/idp/saml/instances/{alias}` | tenant-admin |
| | 导入 SAML XML | POST | `/api/v1/{realm}/idp/saml/import` | tenant-admin |
| | *列出 IDP Mappers* | GET | `/api/v1/{realm}/idp/saml/instances/{alias}/mappers` | tenant-admin |
| | *创建 IDP Mapper* | POST | `/api/v1/{realm}/idp/saml/instances/{alias}/mappers` | tenant-admin |
| | *更新 IDP Mapper* | PUT | `/api/v1/{realm}/idp/saml/instances/{alias}/mappers/{id}` | tenant-admin |
| | *删除 IDP Mapper* | DELETE | `/api/v1/{realm}/idp/saml/instances/{alias}/mappers/{id}` | tenant-admin |
| **角色管理** | 列表 | GET | `/api/v1/{realm}/roles` | tenant-admin |
| | 创建 | POST | `/api/v1/{realm}/roles` | tenant-admin |
| | 查看 | GET | `/api/v1/{realm}/roles/{name}` | tenant-admin |
| | 编辑 | PUT | `/api/v1/{realm}/roles/{name}` | tenant-admin |
| | 删除 | DELETE | `/api/v1/{realm}/roles/{name}` | tenant-admin |
| | *按 UUID 查看* | GET | `/api/v1/{realm}/roles/by-id/{uuid}` | tenant-admin |
| | *按 UUID 编辑* | PUT | `/api/v1/{realm}/roles/by-id/{uuid}` | tenant-admin |
| | *按 UUID 删除* | DELETE | `/api/v1/{realm}/roles/by-id/{uuid}` | tenant-admin |
| **组管理** | 列表 | GET | `/api/v1/{realm}/groups` | tenant-admin |
| | 详情 | GET | `/api/v1/{realm}/groups/{id}` | tenant-admin |
| | 创建 | POST | `/api/v1/{realm}/groups` | tenant-admin |
| | 编辑 | PUT | `/api/v1/{realm}/groups/{id}` | tenant-admin |
| | 删除 | DELETE | `/api/v1/{realm}/groups/{id}` | tenant-admin |
| **用户管理** | 列表 | GET | `/api/v1/{realm}/users` | tenant-admin |
| | 详情 | GET | `/api/v1/{realm}/users/{id}/details` | tenant-admin |
| **策略管理** | 列表 | GET | `/api/v1/policies` | tenant-admin |
| | 详情 | GET | `/api/v1/policies/{id}` | tenant-admin |
| | 创建 | POST | `/api/v1/policies` | tenant-admin |
| | 编辑 | PUT | `/api/v1/policies/{id}` | tenant-admin |
| | 删除 | DELETE | `/api/v1/policies/{id}` | tenant-admin |
| | 模板列表 | GET | `/api/v1/policies/templates` | tenant-admin |
| **角色-策略绑定** | 查询绑定 | GET | `/api/v1/roles/{role_id}/policy` | tenant-admin |
| | 创建绑定 | POST | `/api/v1/roles/{role_id}/policy` | tenant-admin |
| | 更新绑定 | PUT | `/api/v1/roles/{role_id}/policy` | tenant-admin |
| **权限检查** | 检查权限 | POST | `/api/v1/auth/check` | 任意 token |
| **健康检查** | 服务状态 | GET | `/api/v1/common/health` | 任意 token |

> *斜体* 为本次新增接口

### 2. 测试环境信息

需要提供给前端：

```
Gateway 地址:       http://<server-ip>:8080  (或 port-forward 后的地址)
Keycloak 控制台:    http://<server-ip>:8080/admin/

默认租户:           data-agent
Super-admin 账号:   super-admin / SuperInit@123
Tenant-admin 账号:  tenant-admin / TenantAdmin@123
Normal-user 账号:   normal-user / NormalUser@123

Service Client:     idb-proxy-client (secret 在 K8s Secret keycloak-idb-proxy-client 中)
Tenant Client:      data-agent-client (secret 在 K8s Secret keycloak-data-agent-client 中)
```

### 3. Token 结构

前端需要知道 JWT 解码后的结构：

```json
{
  "iss": "http://<gateway>/realms/data-agent",
  "sub": "user-uuid",
  "preferred_username": "tenant-admin",
  "email": "tenant-admin@data-agent.local",
  "roles": [
    {"id": "uuid-1", "name": "tenant-admin"},
    {"id": "uuid-2", "name": "normal-user"}
  ]
}
```

- `roles` 字段是 `[{id, name}]` 结构，前端用 `name` 做显示，用 `id` 做 API 调用
- `iss` 中的 realm 名就是 tenant-id

---

## 二、需要和前端确认的事项

### 1. 登录方式

- [ ] super-admin 登录：直接用密码表单？还是也走 OIDC redirect？
- [ ] 租户用户登录：走 Keycloak 登录页 redirect（标准 OIDC code flow），还是前端自己做登录表单 + password grant？
- [ ] client_secret 如何给前端？写死在前端配置里？还是前端调后端中转？
  > 如果是 SPA（纯前端），建议用 public client（无 secret），Keycloak 已创建的 `data-agent` client 就是 public client

### 2. 前端部署方式

- [ ] 前端是 SPA（Nginx 托管静态文件）还是 SSR（Node.js 服务）？
- [ ] 是否部署在 K8s 集群内？还是独立部署？
- [ ] 如果在 K8s 内，需要：
  - 提供 Docker 镜像
  - 创建 Deployment + Service
  - 创建 HTTPRoute 将前端路径（如 `/ui/*`）路由到前端服务
  - 创建 ReferenceGrant（如果前端不在 agentgateway-system 命名空间）

### 3. 前端路由 vs Gateway 路由

- [ ] 前端的路径前缀是什么？（如 `/ui/`、`/dashboard/`、`/`）
- [ ] 前端路由是否需要 Gateway 做 fallback 到 index.html？（SPA history mode）
- [ ] 前端是否需要直接访问 Keycloak 的登录页？（需要 `/realms/*` 路由不做 ext-authz）

### 4. CORS

- [ ] 前端的 origin 是什么？（如 `http://localhost:3000`）
- [ ] 目前 keycloak-proxy 和 pep-proxy 都配了 `allow_origins=["*"]`，生产环境需要收紧
- [ ] Gateway 层面是否需要额外配置 CORS？

### 5. Token 管理

- [ ] Token 过期时间当前是 Keycloak 默认（access_token 5 分钟，refresh_token 30 分钟），是否需要调整？
- [ ] 前端如何处理 Token 过期？自动 refresh 还是跳回登录页？
- [ ] Token 存储方式？localStorage / sessionStorage / httpOnly cookie？

### 6. 页面/功能范围

- [ ] 第一期联调覆盖哪些页面？建议优先级：
  1. 登录页
  2. 租户列表/创建/删除（super-admin）
  3. 角色管理（tenant-admin）
  4. 策略管理（tenant-admin）
  5. 用户/组管理（tenant-admin）
  6. IDP 接入配置（tenant-admin）

---

## 三、前端在 K8s 部署示例

如果前端是 SPA（Nginx），部署步骤：

```yaml
# 1. Deployment + Service
apiVersion: apps/v1
kind: Deployment
metadata:
  name: auth-dashboard
  namespace: frontend
spec:
  replicas: 1
  selector:
    matchLabels:
      app: auth-dashboard
  template:
    metadata:
      labels:
        app: auth-dashboard
    spec:
      containers:
      - name: auth-dashboard
        image: auth-dashboard:v1
        ports:
        - containerPort: 80
---
apiVersion: v1
kind: Service
metadata:
  name: auth-dashboard
  namespace: frontend
spec:
  selector:
    app: auth-dashboard
  ports:
  - port: 80
    targetPort: 80

---
# 2. ReferenceGrant (允许 Gateway 跨命名空间路由)
apiVersion: gateway.networking.k8s.io/v1beta1
kind: ReferenceGrant
metadata:
  name: allow-gateway-to-frontend
  namespace: frontend
spec:
  from:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    namespace: agentgateway-system
  to:
  - group: ""
    kind: Service
    name: auth-dashboard

---
# 3. HTTPRoute (前端路由，不挂 ext-authz)
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: dashboard-route
  namespace: agentgateway-system
spec:
  parentRefs:
  - name: agentgateway-proxy
    namespace: agentgateway-system
  hostnames:
  - "localhost"
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /ui
    backendRefs:
    - name: auth-dashboard
      namespace: frontend
      port: 80
```

> 前端路由不需要挂 ext-authz（AgentgatewayPolicy），因为前端页面本身不需要鉴权，鉴权发生在前端调 API 时。
