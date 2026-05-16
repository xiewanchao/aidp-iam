# AIDP Gateway 接入生成器

打开 `index.html` 即可使用，不需要启动服务。

默认值按当前本地部署写死：

- Gateway namespace：`aidp-gateway`
- Gateway 名称：`eg`
- IAM namespace：`aidp-iam`
- 鉴权服务：`pep-proxy.aidp-iam.svc:9000`
- ACL 自动同步服务：`resource-sync.aidp-iam.svc:8082`

输出内容：

- `K8s YAML`：HTTPRoute、ReferenceGrant、SecurityPolicy、EnvoyExtensionPolicy、BackendTrafficPolicy、ClientTrafficPolicy。
- `Manifest JSON`：用于 `PUT /AccessManager/Tenants/System/AppManifests/{namespace}` 的应用接入草稿。
- `命令`：apply、检查资源状态、注册 Manifest 和简单 curl 验证。

注意：ACL 自动同步需要同时 apply `EnvoyExtensionPolicy` 并注册 Manifest；只生成 Gateway 资源不会自动写入 `resource_patterns`。
