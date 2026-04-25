# aidp-mock-kb 部署包

KB（知识库）业务的标准 Helm chart 部署包。两种用法：

1. **IAM 平台 e2e 测试** —— IAM 团队装它做端到端验证（mock-kb 实现了完整的 KB 业务接口）
2. **真实 KB 业务部署的样板** —— 业务团队拿这个 chart 做模板，把 mock-kb 镜像换成真实 KB 镜像后即可上生产

```
package-mock-kb/
├── README.md
├── charts/aidp-mock-kb/
│   ├── Chart.yaml
│   ├── values.yaml
│   └── templates/
│       ├── namespace.yaml         mock-kb namespace
│       ├── deployment.yaml        mock-kb Deployment + Service
│       ├── route.yaml             HTTPRoute /kb → mock-kb
│       ├── reference-grant.yaml   跨 ns 引用授权
│       └── policies.yaml          SecurityPolicy + EnvoyExtensionPolicy
├── test/
│   └── test.sh                    端到端测试（14 项）
└── images/arm64/
    └── mock-kb_v1.tar             50 MB
```

## 测试

装完后跑端到端测试：

```bash
GATEWAY=http://<节点 IP>:30080 bash package-mock-kb/test/test.sh
```

覆盖：pod 健康 / 公开路由 / 鉴权拒绝 / 鉴权通过 / 应用注册 / 创建资源 / ext_proc 自动写 owner ACL / 读 / 修改（contributor）/ 删除（owner）/ ACL 级联清除。共 14 项断言。

依赖：bash + curl + python3 + kubectl，**不依赖** jq / base64 / lsof（华为最小镜像也够）。

---

## 前提

**必须**已装好以下两个包：

1. [package-gateway](../package-gateway/) → 提供 Gateway `eg`、CRD、控制器
2. [package-iam](../package-iam/) → 提供 pep-proxy（鉴权）、resource-sync（ACL 同步）

KB 在 IAM 中的元数据（`apps.knowledgebase`、`resource_patterns`、`path_rules`）已经在 `aidp-iam` 装的时候由 `keycloak-init` Job **内置写入**了 —— 装这个包**不需要**调任何注册接口。

---

## 部署

### 1. 加载镜像

```bash
# iSulad
isula load -i package-mock-kb/images/arm64/mock-kb_v1.tar

# containerd
ctr -n k8s.io images import package-mock-kb/images/arm64/mock-kb_v1.tar
```

### 2. arch 后缀 alias

mock-kb tar 内 tag 是 `mock-kb:v1-arm64`，chart 引用的是 `mock-kb:v1`。

```bash
# iSulad
isula tag docker.io/library/mock-kb:v1-arm64 docker.io/library/mock-kb:v1 2>/dev/null

# containerd
ctr -n k8s.io images tag docker.io/library/mock-kb:v1-arm64 docker.io/library/mock-kb:v1 2>/dev/null
```

### 3. helm install

```bash
helm install aidp-mock-kb package-mock-kb/charts/aidp-mock-kb \
  --namespace mock-kb --create-namespace \
  --wait --timeout=2m
```

> 不需要 `--set keycloak.*` 这种参数，本 chart 完全独立于 IAM 配置。

---

## 验证

### 拿一个 JWT

```bash
# admin 用户拿 token
TOKEN=$(curl -s -X POST http://<EIP>:30080/realms/aidp/protocol/openid-connect/token \
  -d "client_id=aidp-client" \
  -d "grant_type=password" \
  -d "username=admin" \
  -d "password=Admin@123" | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
```

### 端到端流程

```bash
# 1. 创建 KB → 自动写 owner ACL
curl -X POST http://<EIP>:30080/kb/v1/kb \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"我的 KB"}'
# 期望：201 + {"id":"kb-xxx"}

# 2. 列表 → ext_proc 注入 X-Allowed-Ids
curl http://<EIP>:30080/kb/v1/kb \
  -H "Authorization: Bearer $TOKEN"
# 期望：返回的列表只包含当前用户有权访问的 KB

# 3. 删除 KB → 级联清 ACL
curl -X DELETE http://<EIP>:30080/kb/v1/kb/kb-xxx \
  -H "Authorization: Bearer $TOKEN"
# 期望：200 / 204
```

---

## 替换为真实 KB 业务（生产部署）

业务团队接管时只需改 `values.yaml` 的 image 字段：

```bash
helm upgrade aidp-mock-kb package-mock-kb/charts/aidp-mock-kb \
  -n mock-kb \
  --set image=my-registry/real-kb:v1.0 \
  --set namespace=knowledgebase    # 改 namespace 顺便
```

`pathPrefix=/kb` **不要改** —— 必须跟 IAM 中 `apps.knowledgebase.path_prefix=/kb/` 对齐，否则 pep-proxy 找不到 app。

> chart 结构（namespace + deployment + service + httproute + reference-grant + policies）就是新业务接入的标准模板。Rubik / Memory 之外接入第四个全新业务时，照抄这个 chart 改名即可，并通过 `POST /api/v1/apps` 在 IAM 注册新业务。

---

## 清理

```bash
helm uninstall aidp-mock-kb -n mock-kb
kubectl delete ns mock-kb --ignore-not-found
```

---

## 跟其他包关系

```
package-gateway   网关基建（必装）
     ↓
package-iam       IAM 平台 + 内置 KB/Rubik/Memory 注册数据（必装）
     ↓
package-mock-kb   KB 业务（本包，可选）
```

`package-mock-rubik` / `package-mock-memory` 后续按相同模板新增。
