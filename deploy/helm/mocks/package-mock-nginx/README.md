# aidp-mock-nginx 部署包

前端 nginx 的 HTTPRoute 占位包，给以后接入前端团队留的位置。

```
mocks/package-mock-nginx/
├── README.md
└── charts/aidp-mock-nginx/
    ├── Chart.yaml
    ├── values.yaml
    └── templates/
        └── route.yaml         只有 HTTPRoute
```

## 跟其他 mock 包的差别

| 资源 | mock-kb | mock-nginx |
|------|-----------------------------------|-----------|
| Namespace | ✓ | ❌ |
| Deployment + Service | ✓（自带 mock 后端） | ❌（前端团队自己出） |
| HTTPRoute | ✓ | **✓** |
| ReferenceGrant | ✓ | ❌（前端团队自己出） |
| SecurityPolicy | ✓（绑 ext_authz） | ❌（前端通常公开，不挂鉴权） |
| EnvoyExtensionPolicy | ✓（绑 ext_proc） | ❌（前端没用户资源 ACL） |

mock-nginx 装上去 → HTTPRoute 显示 `ResolvedRefs=False`（指向不存在的 Service）—— 正常。等前端团队补 Deployment/Service + ReferenceGrant 后自动 resolve。

## 路径优先级（Gateway API 自动处理，无需排序）

```
/realms/...        → Keycloak              （aidp-iam 提供）
/admin/...         → Keycloak admin console（aidp-iam 提供）
/api/v1/...        → keycloak-proxy         （aidp-iam 提供）
/acl/v1/...        → resource-sync          （aidp-iam 提供）
/kb/...            → mock-kb / 真实 KB 业务
/...（其他全部）    → frontend nginx        （本包提供路由 + 前端团队后续部署 Service）
```

`/` 是兜底 —— 跑在所有更具体路径之后，按 path 越具体越优先匹配。

## 部署

```bash
helm install aidp-mock-nginx mocks/package-mock-nginx/charts/aidp-mock-nginx \
  --namespace aidp-iam \
  --set backend.service=my-frontend \
  --set backend.namespace=frontend \
  --set backend.port=80
```

values 默认指向 `agentinfra-agentinfra-frontend.agentinfra:9080`（agentinfra 项目里 nginx 的命名约定），按需 `--set` 改。

## 前端团队接入完整步骤

```bash
# 1. 前端团队自己装 nginx（在 frontend 自己的 chart 里）
helm install my-frontend my-frontend-chart -n frontend --create-namespace

# 2. 前端团队 apply 跨 ns 引用授权
cat <<EOF | kubectl apply -f -
apiVersion: gateway.networking.k8s.io/v1beta1
kind: ReferenceGrant
metadata: { name: allow-gateway-to-frontend, namespace: frontend }
spec:
  from: [{ group: gateway.networking.k8s.io, kind: HTTPRoute, namespace: envoy-gateway-system }]
  to:   [{ group: "", kind: Service }]
EOF

# 3. 平台团队装本路由包（如果还没装）
helm install aidp-mock-nginx mocks/package-mock-nginx/charts/aidp-mock-nginx -n aidp-iam \
  --set backend.service=my-frontend --set backend.namespace=frontend --set backend.port=80

# 4. 浏览器访问 http://<EIP>:30080/ → nginx
```

## 清理

```bash
helm uninstall aidp-mock-nginx -n aidp-iam
```
