# aidp-mock-rubik 部署包

Rubik（数据分析）业务的标准 Helm chart 部署包。两种用法：

1. **IAM 平台 e2e 测试**（mock-rubik 实现完整的 Rubik API）
2. **真实 Rubik 业务部署的样板**（业务团队拿这个 chart 做模板，把 mock-rubik 镜像换成真实 Rubik 镜像）

```
package-mock-rubik/
├── README.md
├── charts/aidp-mock-rubik/
│   ├── Chart.yaml
│   ├── values.yaml
│   └── templates/
│       ├── namespace.yaml
│       ├── deployment.yaml          mock-rubik Deployment + Service
│       ├── route.yaml               HTTPRoute /rubik (URLRewrite to /)
│       ├── reference-grant.yaml
│       └── policies.yaml            SecurityPolicy + EnvoyExtensionPolicy
├── test/
│   └── test.sh                      端到端测试（16 项，依赖 bash + curl + python + kubectl）
└── images/arm64/
    └── mock-rubik_v1.tar            50 MB
```

---

## 前提

**必须**已装：
- [package-gateway](../package-gateway/) → Gateway 基建
- [package-iam](../package-iam/) → pep-proxy / resource-sync

Rubik 元数据（`apps.rubik` / `resource_patterns` / `path_rules`）已经在 `aidp-iam` 装的时候由 `keycloak-init` Job 内置写入。

---

## 部署

```bash
# 1. 加载镜像
isula load -i package-mock-rubik/images/arm64/mock-rubik_v1.tar
isula tag docker.io/library/mock-rubik:v1-arm64 docker.io/library/mock-rubik:v1 2>/dev/null

# 2. helm install
helm install aidp-mock-rubik package-mock-rubik/charts/aidp-mock-rubik \
  --namespace mock-rubik --create-namespace --wait --timeout=2m
```

---

## 测试

```bash
GATEWAY=http://<节点 IP>:30080 bash package-mock-rubik/test/test.sh
```

测试覆盖（16 项）：

| 阶段 | 内容 |
|------|------|
| 0 | 拿 admin token (从 Keycloak Secret 取 client-secret) |
| 1 | mock-rubik pod Ready |
| 2 | 公开路由（/realms/aidp/.well-known/openid-configuration）|
| 3 | 受保护路由 401（无 token） |
| 4 | 受保护路由 200（admin token） |
| 5 | IAM 注册了 rubik app |
| 6 | POST /rubik/api/databases → 201（创建 database）|
| 7 | resource_acl 自动写入 owner（database）|
| 8 | POST /rubik/api/sessions → 201（创建 session）+ ACL 写入 |
| 9 | DELETE /rubik/api/databases/{id} → 200/204 + ACL 级联清 |
| 10 | DELETE /rubik/api/sessions/{id} → 200/204 + ACL 级联清 |

---

## 清理

```bash
helm uninstall aidp-mock-rubik -n mock-rubik
kubectl delete ns mock-rubik --ignore-not-found
```
