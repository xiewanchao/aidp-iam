# KMS 权限说明

## 角色定义

| 角色 | 说明 | 默认授予对象 |
|---|---|---|
| Owner | 完整控制权，可执行所有操作 | tenant-admins 组（类型级）、知识库创建者（实例级） |
| Contributor | 可创建和修改资源，但不能管理权限 | — |
| Viewer | 只读权限，仅可查询 | all-users 组（类型级） |

> **管理员**：属于 `tenant-admins` 组的用户，拥有类型级 Owner ACL，并通过 `admin_bypass` 绕过实例级检查。
> **普通用户**：属于 `all-users` 组，拥有类型级 Viewer ACL，对自己创建的资源拥有实例级 Owner ACL。

---

## 知识库类型说明

KMS 支持两种知识库类型，区分由 **KMS 应用层**在创建时决定，IAM 通过 ACL 条目体现：

| 类型 | ACL 特征 | 可见范围 |
|---|---|---|
| 企业知识库 | 实例级有 `all-users → Viewer` 条目 | 租户内所有用户可见、可查询 |
| 个人知识库 | 实例级仅有 `creator → Owner` 条目 | 仅创建者本人可见可操作 |

---

## KnowledgeBases（知识库）

### GET — 查询列表 / 查询单条

| 调用方 | 行为 | 机制 |
|---|---|---|
| 管理员 | 可查看所有知识库（企业 + 所有人的个人） | `admin_bypass` 绕过实例级检查，X-Allowed-Ids 不限制 |
| 普通用户 | 可查看企业知识库 + 自己的个人知识库 | X-Allowed-Ids 只注入有 ACL 的 ID；企业 KB 有 all-users→Viewer，个人 KB 只有创建者有 Owner |

### PUT — 创建

| 调用方 | 行为 | 机制 |
|---|---|---|
| 管理员 | 可创建企业知识库和个人知识库 | `admin_bypass`；ext_proc 写 creator→Owner；KMS 应用层按类型决定是否额外写 all-users→Viewer |
| 普通用户 | 只能创建个人知识库 | `allow_create_without_acl=true` 放行 PUT；ext_proc 写 creator→Owner；KMS 应用层不写 all-users→Viewer（保持个人隔离） |

### PATCH — 修改

| 调用方 | 行为 | 机制 |
|---|---|---|
| 管理员 | 可修改企业知识库和所有个人知识库 | `admin_bypass` |
| 普通用户 | 只能修改自己创建的个人知识库 | 要求实例级 ACL，普通用户只有自己 KB 的 Owner ACL |

### DELETE — 删除

| 调用方 | 行为 | 机制 |
|---|---|---|
| 管理员 | 可删除企业知识库和所有个人知识库 | `admin_bypass`；ext_proc 级联清除该实例所有 ACL |
| 普通用户 | 只能删除自己创建的个人知识库 | 要求实例级 ACL，普通用户只有自己 KB 的 Owner ACL |

### Count — 查询数量（类型级 Action）

| 调用方 | 行为 | 机制 |
|---|---|---|
| 管理员 | 返回全部知识库数量 | `required_role: Viewer`，admin_bypass 满足 |
| 普通用户 | 返回自己有权限的知识库数量 | `required_role: Viewer`，Viewer 满足；KMS 应用层按 X-Allowed-Ids 过滤后计数 |

### GetFilesystem — 查询文件系统（类型级 Action）

| 调用方 | 行为 | 机制 |
|---|---|---|
| 管理员 | 允许 | `required_role: Owner`，管理员有类型级 Owner ACL |
| 普通用户 | **拒绝（403）** | 普通用户类型级角色为 Viewer，不满足 Owner 要求 |

### GetNfsshare — 查询 NFS 共享名（类型级 Action）

| 调用方 | 行为 | 机制 |
|---|---|---|
| 管理员 | 允许 | `required_role: Owner`，管理员有类型级 Owner ACL |
| 普通用户 | **拒绝（403）** | 普通用户类型级角色为 Viewer，不满足 Owner 要求 |

---

## Channels（知识库管道）

Channels 是管理员专属资源，普通用户无任何操作权限（查、创、删均不允许）。

| 操作 | 管理员 | 普通用户 | 拦截层 |
|---|---|---|---|
| GET 列表 | 允许 | **拒绝** | KMS 应用层（gateway 无法区分企业/个人 KB Owner） |
| GET 单条 | 允许 | **拒绝** | KMS 应用层 |
| PUT 创建 | 允许 | **拒绝** | 企业 KB：gateway 拦截（Viewer 不允许 PUT）；个人 KB：**KMS 应用层必须拦截** |
| DELETE | 允许 | **拒绝** | gateway 拦截（无 channel 实例 ACL） + KMS 应用层兜底 |
| Count | 允许 | **拒绝** | KMS 应用层 |

**gateway 无法完全覆盖的原因：**
个人 KB 的创建者有父 KB 的 Owner ACL，Owner 允许 PUT，所以 gateway 不会拦截该用户创建 Channels。KMS 应用层必须在所有 Channels 接口入口处强制检查管理员身份。

**KMS 应用层必须实现：**

```python
def require_admin(request: Request):
    groups = request.headers.get("X-Auth-Groups", "").split(",")
    if not ({"tenant-admins", "master-admins"} & set(g.strip() for g in groups)):
        raise HTTPException(403, "Channels 仅管理员可操作")
```

---

## KnowledgeFiles（知识库文件）

单例子资源，挂在 KnowledgeBases 实例下，权限完全继承父 KB 的实例级 ACL。

**权限规则：**
- 管理员：可对所有 KB 下的文件执行 GET 和全部自定义操作（admin_bypass）
- 普通用户对企业 KB：仅允许 GET（继承 Viewer，自定义 Action 均需 Contributor，被 gateway 拦截）
- 普通用户对自己的个人 KB：GET + 全部自定义操作（继承 Owner，满足 Contributor 要求）
- 普通用户对他人个人 KB：完全无权限（无 ACL → gateway 拦截）

| 操作 | required_role | 管理员 | 企业 KB 普通用户 | 个人 KB 创建者 |
|---|---|---|---|---|
| GET 列表 | Viewer | 允许 | 允许（Viewer 满足） | 允许 |
| Upload | Contributor | 允许 | **拒绝（403）** | 允许 |
| History | Contributor | 允许 | **拒绝（403）** | 允许 |
| Count | Contributor | 允许 | **拒绝（403）** | 允许 |
| Remove（批量删除） | Contributor | 允许 | **拒绝（403）** | 允许 |

**机制说明：**
- 企业 KB：all-users→Viewer 继承到 KnowledgeFiles，Viewer 不满足 Contributor → 自定义 Action 被 gateway 拦截
- 个人 KB：creator→Owner 继承到 KnowledgeFiles，Owner 满足 Contributor → 允许所有操作
- 他人个人 KB：无 ACL → gateway 拦截包括 GET 在内的所有操作
- gateway 层完全覆盖此场景，KMS 应用层无需额外检查

---

## Retrieval（检索）

FusionSearch 接口对所有租户用户开放，default_acl 写入 all-users→Contributor（Viewer 不允许 POST，需要 Contributor 才能调用）。

| 操作 | required_role | 管理员 | 普通用户 |
|---|---|---|---|
| FusionSearch | Viewer | 允许 | 允许（Contributor 满足 POST） |

---

## JargonGroups（术语库）

集合级资源，所有操作打到同一路径，无实例 ID，由类型级 ACL + 角色矩阵直接控制。

| 操作 | required_role | 管理员 | 普通用户 |
|---|---|---|---|
| GET 列表 | — | 允许 | 允许（Viewer 满足 GET） |
| PUT 创建 | — | 允许 | **拒绝（403）**，Viewer 不允许 PUT |
| PATCH 修改 | — | 允许 | **拒绝（403）**，Viewer 不允许 PATCH |
| DELETE 删除 | — | 允许 | **拒绝（403）**，Viewer 不允许 DELETE |
| Version（查询版本号） | Viewer | 允许 | 允许 |

---

## IAM 层与 KMS 应用层职责边界

| 职责 | IAM 层（Gateway） | KMS 应用层 |
|---|---|---|
| 身份验证 | JWT / API Key 验证 | — |
| 路径级访问控制 | OPA policy，按 namespace 放行 | — |
| 类型级权限（能否访问该类资源） | default_acl + 角色矩阵 | — |
| 实例级权限（能否操作某个具体 KB） | resource_acl 前缀匹配 | — |
| 创建者隔离（个人 KB 只有自己能操作） | allow_create_without_acl + ext_proc 自动写 creator→Owner | — |
| 企业 KB 对全员可见 | — | 创建后透传调用方 token 调用 `PUT /AccessManager/Tenants/{tid}/ACLs` 写 all-users→Viewer |
| 知识库类型判断（企业/个人） | — | 创建接口入参判断，决定写哪些 ACL |
| 管理员不能修改个人 KB（如有此需求） | — | PATCH 接口检查 KB 类型字段，拒绝修改非自建个人 KB |
