# IAM v2.0 待办事项

记录当前已知的后续优化点，按优先级排列。标记 `[done]` 表示已完成，`[pending]` 表示待处理。

---

## 高优先级

### [pending] Manifest 自动注册机制

**背景**：当前 manifest 需要通过 REST API 手动调用注册：

```bash
curl -X PUT /AccessManager/Tenants/System/AppManifests/DataAgent \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d @manifest.json
```

**问题**：新应用部署时需要人工或额外脚本触发，容易遗漏，且与应用生命周期脱节。

**建议方案（三选一，视部署模式决定）**：

**方案 A：应用启动时自注册（推荐，改动最小）**

应用在启动时主动调用 Manifest API，把 `manifest.json` 打包进镜像：

```python
# 应用 startup 事件中
async def on_startup():
    manifest = json.load(open("/app/manifest.json"))
    await http_client.put(
        f"{IAM_BASE_URL}/AccessManager/Tenants/System/AppManifests/{NAMESPACE}",
        json={"base_url": BASE_URL, "manifest_json": manifest},
        headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
    )
```

需要改动：
- 各应用镜像中打包 `manifest.json`
- 各应用 startup 逻辑中加注册调用
- IAM 提供服务账号 token 供应用调用（或走 mTLS）

**方案 B：Helm chart post-install Job**

在各应用的 Helm chart 中加 `post-install` / `post-upgrade` Hook Job，部署完成后自动注册：

```yaml
# templates/manifest-register-job.yaml
apiVersion: batch/v1
kind: Job
metadata:
  annotations:
    helm.sh/hook: post-install,post-upgrade
spec:
  template:
    spec:
      containers:
      - name: register
        image: curlimages/curl
        command:
        - curl -X PUT .../AppManifests/DataAgent
          -d @/manifest/manifest.json
        volumeMounts:
        - name: manifest
          mountPath: /manifest
      volumes:
      - name: manifest
        configMap:
          name: dataagent-manifest
```

需要改动：各应用 Helm chart 新增 Job + ConfigMap。

**方案 C：K8s Controller 监听 ConfigMap（最云原生，实现复杂）**

定义一个 `AppManifest` CRD 或监听特定 label 的 ConfigMap，由 controller 自动同步到 Manifest API。适合应用数量多、频繁变更的场景。

**当前决策**：暂不实现，手动注册。后续根据接入应用数量决定采用哪种方案。

---

## 中优先级

### [pending] 服务间调用的身份模型

**背景**：当前鉴权系统假设所有请求都携带用户 JWT。但应用内部服务间调用（如 DataAgent 调用 MemoryStore）没有用户 JWT，只有服务账号。

**问题**：
- 服务账号的 `user_path` 格式是什么？
- 服务间调用是否需要经过资源级鉴权？
- 是否需要单独的服务账号 ACL 机制？

**建议**：定义服务账号路径格式 `AccessManager/Tenants/System/ServiceAccounts/{name}`，在 resource_acl 中为服务账号预写必要的 ACL，或通过 mTLS + 白名单绕过资源级鉴权。

---

### [pending] 新租户创建时自动同步所有 manifest 的 default_acl

**背景**：当前 `_sync_default_acls` 只在 manifest 注册时触发，遍历已有租户写入 ACL。但新租户创建时，需要反向遍历所有已注册 manifest 写入 default_acl。

**当前状态**：`da-idb-proxy/app/api/v1/manifests.py` 中的 `_sync_default_acls` 只处理"manifest 注册 → 遍历租户"方向。

**需要补充**：在租户创建接口（`da-idb-proxy/app/api/v1/tenants.py`）的成功回调中，遍历所有已注册 manifest，为新租户展开 default_acl 写入 resource_acl。

---

### [pending] pep-proxy 回调超时策略

**背景**：自定义角色触发 `Action/Authorize` 回调时，当前超时设置为 500ms（`_callback_check` 函数），fail-close（超时即拒绝）。

**待确认**：
- 500ms 是否足够？应用内部鉴权逻辑复杂时可能不够
- 是否需要按 namespace 配置不同的超时时间（在 manifest 中声明）

---

## 低优先级

### [pending] resource_acl 前缀匹配深度限制

**背景**：`query_acl` 函数将 object_path 拆解为所有前缀候选（从最长到最短），深度不限。路径很深时候选列表会很长。

**建议**：限制最大深度为 10 级，超出时截断，避免极端情况下的性能问题。

---

### [pending] ACL 变更通知机制

**背景**：当前 pep-proxy 每次请求都实时查询 resource_acl，无缓存。如果后续需要缓存，需要设计 ACL 变更时的缓存失效通知机制（如 Redis pub/sub 或 webhook）。

**当前决策**：不缓存，实时查询。待性能测试后决定是否需要引入缓存。

---

### [pending] role_path 为空的三元组语义明确化

**背景**：resource_acl 表中 role_path 字段当前设计为 NOT NULL，但设计文档中提到"role 可为空"。

**待确认**：是否需要支持空 role（仅记录关联关系，不授予权限）？如需支持，表结构需要调整为允许 NULL。

---

## 已完成

- [done] resource_acl 表结构迁移为三元组（user_path, object_path, role_path）
- [done] pep-proxy 资源级鉴权改为统一 URL 前缀匹配
- [done] ext_proc 改为按 object_path 前缀级联删除 ACL
- [done] da-idb-proxy 新增 ACL 管理 API 和 Manifest API
- [done] 统一 URL 规范：Create 改为 PUT 到 collection 路径（无 ID），服务端生成 ID
- [done] 统一返回值规范文档化
- [done] manifest-template.json 添加字段注释
- [done] app-onboarding-template.md 补充 API 标准示例
