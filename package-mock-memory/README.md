# aidp-mock-memory 部署包

Memory 业务的标准 Helm chart 部署包。两种用法：

1. **IAM 平台 e2e 测试**
2. **真实 Memory 业务部署的样板**

```
package-mock-memory/
├── README.md
├── charts/aidp-mock-memory/
│   ├── Chart.yaml
│   ├── values.yaml
│   └── templates/
│       ├── namespace.yaml
│       ├── deployment.yaml
│       ├── route.yaml               HTTPRoute /memory (URLRewrite to /)
│       ├── reference-grant.yaml
│       └── policies.yaml
├── test/
│   └── test.sh                      端到端测试（15 项）
└── images/arm64/
    └── mock-memory_v1.tar           50 MB
```

---

## 前提

**必须**已装：
- [package-gateway](../package-gateway/)
- [package-iam](../package-iam/)

---

## 部署

```bash
isula load -i package-mock-memory/images/arm64/mock-memory_v1.tar
isula tag docker.io/library/mock-memory:v1-arm64 docker.io/library/mock-memory:v1 2>/dev/null

helm install aidp-mock-memory package-mock-memory/charts/aidp-mock-memory \
  --namespace mock-memory --create-namespace --wait --timeout=2m
```

---

## 测试

```bash
GATEWAY=http://<节点 IP>:30080 bash package-mock-memory/test/test.sh
```

测试覆盖（15 项）：

| 阶段 | 内容 |
|------|------|
| 0 | 拿 admin token |
| 1 | mock-memory pod Ready |
| 2 | 公开路由 |
| 3 | 受保护路由 401（无 token） |
| 4 | 受保护路由 200（admin） |
| 5 | IAM 注册了 memory app |
| 6 | 数据面接口（POST /memory/api/v1/memory/add 和 query） |
| 7 | Templates CRUD（POST/GET/GET by id） |
| 8 | Tenant 创建（POST /memory/api/v1/tenants） |
| 9 | 清理测试数据（DELETE template + tenant） |
| 10 | 系统恢复接口（POST /memory/api/v1/system/recovery） |

> **注意**：当前版本 IAM 的 `keycloak-init` 给 memory 写的 `resource_patterns` 是 `/v1/memories`，跟 mock-memory 的真实 API（`/api/v1/templates`、`/api/v1/tenants` 等）不匹配。**资源级 ACL 自动同步在 memory 这一版不工作**。本测试只覆盖路径级鉴权 + 实际接口的 HTTP 行为。等 init script 修了 memory 的 resource_pattern 后再补 ACL 测试。

---

## 清理

```bash
helm uninstall aidp-mock-memory -n mock-memory
kubectl delete ns mock-memory --ignore-not-found
```
