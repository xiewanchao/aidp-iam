# 请求链路视角 — 六种典型场景

> 版本：v1.1 | 日期：2026-04-02

---

## 0 普通业务请求（最常见，不涉及权限变动）

日常使用中 90% 以上的请求都是这种：查看数据、编辑已有资源、调用业务接口。不创建、不删除、不分享，权限数据不变。

### 0.1 查看单个资源（GET 实例）

张三查看自己的知识库详情。

```mermaid
sequenceDiagram
    participant U as 张三
    participant GW as Gateway
    participant PEP as pep-proxy
    participant OPA as OPA
    participant DB as resource_acl 表
    participant RS as resource-sync
    participant KB as kb-service

    U->>GW: GET /knowledgebase/v1/kb/kb-001<br/>Authorization: Bearer JWT
    GW->>PEP: ext-authz 鉴权请求

    Note over PEP: 第1步：路径级鉴权
    PEP->>PEP: 验证 JWT<br/>提取 user_id=zhangsan
    PEP->>OPA: path=/knowledgebase/v1/kb/kb-001
    OPA->>OPA: app enabled ✅<br/>未命中保护规则<br/>all-users ✅
    OPA-->>PEP: allow

    Note over PEP: 第2步：资源级鉴权
    PEP->>DB: SELECT FROM resource_acl<br/>WHERE resource_id='kb-001'<br/>AND (subject_id='zhangsan'<br/>OR subject_id IN groups)
    DB-->>PEP: permission=owner

    PEP-->>GW: 通过<br/>注入 X-Auth-User-Id: zhangsan<br/>注入 X-Auth-Tenant: aidp<br/>注入 X-Auth-Groups: data-team,all-users

    GW->>RS: 转发
    RS->>KB: 透传（GET 不处理 ACL）
    KB-->>RS: 200 { 知识库数据 }
    RS-->>GW: 200（直接返回）
    GW-->>U: 200 知识库详情
```

### 0.2 编辑已有资源（PUT 实例）

张三编辑自己的知识库。resource-sync 不做任何 ACL 操作（PUT 不影响权限）。

```mermaid
sequenceDiagram
    participant U as 张三
    participant GW as Gateway
    participant PEP as pep-proxy
    participant OPA as OPA
    participant DB as resource_acl 表
    participant RS as resource-sync
    participant KB as kb-service

    U->>GW: PUT /knowledgebase/v1/kb/kb-001<br/>{ "name": "新名字" }
    GW->>PEP: ext-authz 鉴权请求

    PEP->>PEP: 验证 JWT<br/>提取 user_id=zhangsan
    PEP->>OPA: 路径鉴权
    OPA-->>PEP: allow
    PEP->>DB: 资源鉴权：zhangsan 对 kb-001?
    DB-->>PEP: permission=owner ✅

    PEP-->>GW: 通过
    GW->>RS: 转发
    RS->>KB: 透传（PUT 不处理 ACL）
    KB->>KB: 更新知识库名称
    KB-->>RS: 200
    RS-->>GW: 200（直接返回，不拦截）
    GW-->>U: 200 更新成功

    Note over RS: resource-sync 对 PUT 完全透传<br/>不解析响应体，不写 ACL<br/>权限没有任何变化
```

### 0.3 viewer 尝试编辑（pep-proxy 直接拒绝）

李四是 viewer，尝试编辑张三的知识库。pep-proxy 发现 viewer 权限不足以执行 PUT 操作，直接拒绝，请求不会到达后端。

```mermaid
sequenceDiagram
    participant U as 李四
    participant GW as Gateway
    participant PEP as pep-proxy
    participant OPA as OPA
    participant DB as resource_acl 表

    U->>GW: PUT /knowledgebase/v1/kb/kb-001<br/>{ "name": "李四改的" }
    GW->>PEP: ext-authz 鉴权

    PEP->>PEP: 验证 JWT<br/>提取 user_id=lisi
    PEP->>OPA: 路径鉴权
    OPA-->>PEP: allow
    PEP->>DB: 资源鉴权：lisi 对 kb-001?
    DB-->>PEP: permission=viewer

    PEP->>PEP: 权限检查：PUT 需要 contributor 以上<br/>viewer 不够 → 拒绝
    PEP-->>GW: 403 权限不足
    GW-->>U: 403

    Note over PEP: 请求到不了 resource-sync 和后端<br/>应用不做任何鉴权
```

**pep-proxy 的权限-操作映射：**

```
权限等级：owner > contributor > viewer

路径段数判断（匹配 resource_patterns 后）：
  POST + 0段（/v1/kb）          → 创建顶级资源 → 不查 resource_acl（OPA path_rules 控制）
  GET  + 1段（/v1/kb/kb-001）   → 查看资源 → 需要 viewer
  PUT  + 1段（/v1/kb/kb-001）   → 编辑资源 → 需要 contributor
  DELETE + 1段（/v1/kb/kb-001） → 删除资源 → 需要 owner
  POST + 2段（/v1/kb/kb-001/docs）       → 创建子资源 → 需要对父资源 contributor
  GET  + 2段以上（/v1/kb/kb-001/docs/d1） → 查看子资源 → 需要对父资源 viewer
  PUT  + 2段以上                          → 编辑子资源 → 需要对父资源 contributor
  DELETE + 2段以上                        → 删除子资源 → 需要对父资源 contributor
```

### 0.3.1 管理员创建资源（OPA path_rules 拦截）

只有 memory-admins 能创建记忆库模板。普通用户被 OPA 直接拦住，不到 resource_acl 这一步。

```mermaid
sequenceDiagram
    participant U as 李四
    participant GW as Gateway
    participant PEP as pep-proxy
    participant OPA as OPA

    U->>GW: POST /memory/v1/admin/templates<br/>{ "name": "新模板" }
    GW->>PEP: ext-authz 鉴权

    PEP->>PEP: 验证 JWT<br/>提取 user_id=lisi
    PEP->>OPA: 路径鉴权<br/>path=/memory/v1/admin/templates
    OPA->>OPA: 命中 path_rules<br/>/memory/v1/admin/ → 需要 memory-admins<br/>李四不在 memory-admins 组
    OPA-->>PEP: deny

    PEP-->>GW: 403
    GW-->>U: 403 需要 memory-admins 权限

    Note over OPA: 管理员资源的创建权限由 OPA path_rules 控制<br/>不需要查 resource_acl
```

### 0.3.2 子资源创建（检查父资源权限）

李四是 kb-001 的 viewer，尝试在 kb-001 下创建文档。pep-proxy 检查父资源权限，viewer 不够创建子资源。

```mermaid
sequenceDiagram
    participant U as 李四
    participant GW as Gateway
    participant PEP as pep-proxy
    participant OPA as OPA
    participant DB as resource_acl 表

    U->>GW: POST /knowledgebase/v1/kb/kb-001/docs<br/>{ "title": "新文档" }
    GW->>PEP: ext-authz 鉴权

    PEP->>PEP: 验证 JWT<br/>提取 user_id=lisi
    PEP->>OPA: 路径鉴权
    OPA-->>PEP: allow

    Note over PEP: 路径匹配 resource_patterns /v1/kb<br/>剩余段：["kb-001", "docs"]（2段）<br/>→ 子资源操作，检查父资源 kb-001 的权限

    PEP->>DB: 查 lisi 对 kb-001 的权限
    DB-->>PEP: permission=viewer

    PEP->>PEP: POST 子资源需要 contributor 以上<br/>viewer 不够 → 拒绝
    PEP-->>GW: 403
    GW-->>U: 403 权限不足，无法创建子资源

    Note over PEP: 如果李四是 contributor 或 owner<br/>就会放行，请求到达后端
```

### 0.4 搜索接口（后端调内部接口过滤）

搜索、list 等接口不包含资源 ID，pep-proxy 不做资源级鉴权。但搜索结果需要只包含用户有权限的资源，后端调 resource-sync 内部接口获取可访问 ID 列表。

```mermaid
sequenceDiagram
    participant U as 用户
    participant GW as Gateway
    participant PEP as pep-proxy
    participant OPA as OPA
    participant RS as resource-sync
    participant KB as kb-service
    participant DB as resource_acl 表

    U->>GW: GET /knowledgebase/v1/search?q=关键词
    GW->>PEP: ext-authz 鉴权

    PEP->>PEP: 验证 JWT<br/>提取 user_id=lisi
    PEP->>OPA: 路径鉴权
    OPA-->>PEP: allow（未命中保护规则, all-users）

    Note over PEP: 路径不包含资源 ID<br/>不查 resource_acl<br/>直接放行

    PEP-->>GW: 通过<br/>注入 X-Auth-User-Id / Groups
    GW->>RS: 转发
    RS->>KB: 透传

    Note over KB: 后端需要知道用户能访问哪些资源
    KB->>RS: 集群内部调用<br/>GET http://resource-sync:8081/internal/v1/resources<br/>?user_id=lisi&groups=data-team,all-users<br/>&app_name=knowledgebase&resource_type=kb
    RS->>DB: SELECT resource_id FROM resource_acl<br/>WHERE user/group 匹配
    DB-->>RS: [kb-001, kb-005, kb-012]
    RS-->>KB: { "resource_ids": ["kb-001","kb-005","kb-012"] }

    KB->>KB: 搜索时加过滤<br/>WHERE id IN (allowed_ids)<br/>AND name LIKE '%关键词%'
    KB-->>RS: 200 过滤后的搜索结果
    RS-->>GW: 200
    GW-->>U: 200

    Note over RS: resource-sync 对业务请求仍然透传<br/>内部接口是后端主动调用的，不在转发链路上
```

### 0.4.1 不需要资源过滤的接口

系统健康检查、全局配置等接口，不涉及资源，不需要调内部接口。

```mermaid
sequenceDiagram
    participant U as 用户
    participant GW as Gateway
    participant PEP as pep-proxy
    participant OPA as OPA
    participant RS as resource-sync
    participant KB as kb-service

    U->>GW: GET /knowledgebase/v1/health
    GW->>PEP: ext-authz 鉴权
    PEP->>PEP: 验证 JWT
    PEP->>OPA: 路径鉴权
    OPA-->>PEP: allow
    PEP-->>GW: 通过
    GW->>RS: 转发
    RS->>KB: 透传
    KB-->>RS: 200 { "status": "ok" }
    RS-->>GW: 200
    GW-->>U: 200
```

### 0.5 普通请求的性能路径总结

```mermaid
flowchart LR
    U[用户] -->|HTTPS| GW[Gateway<br/>~1ms]
    GW -->|ext-authz| PEP[pep-proxy<br/>JWT验证 ~2ms<br/>OPA ~1ms<br/>ACL查询 ~1ms]
    GW -->|转发| RS[resource-sync<br/>透传 ~1ms]
    RS --> APP[后端应用<br/>业务处理]

    TOTAL1[单资源请求额外开销 ≈ 5-6ms]
    TOTAL2[list/search 额外开销 ≈ 7-8ms<br/>含内部接口调用 ~2-3ms]

    style TOTAL1 fill:#51cf66,color:#fff
    style TOTAL2 fill:#ffd43b,color:#000
```

**关键点：**
- 单资源请求（GET/PUT/DELETE /v1/kb/kb-001）：resource-sync 透传，额外开销约 5-6ms
- list/search 请求：后端多一次 resource-sync 内部接口调用，额外开销约 7-8ms

---

## 1 创建顶级资源

用户创建一个知识库（顶级资源），resource-sync 自动注册 owner。OPA 通过 path_rules 控制谁能创建（未命中保护规则的路径 all-users 可创建）。

```mermaid
sequenceDiagram
    participant U as 用户
    participant GW as Gateway
    participant PEP as pep-proxy
    participant OPA as OPA
    participant RS as resource-sync
    participant KB as kb-service
    participant DB as resource_acl 表

    U->>GW: POST /knowledgebase/v1/kb<br/>Authorization: Bearer JWT
    GW->>PEP: ext-authz 鉴权请求
    PEP->>PEP: 验证 JWT<br/>提取 user_id=zhangsan, tenant=aidp
    PEP->>OPA: 路径鉴权<br/>path=/knowledgebase/v1/kb
    OPA->>OPA: 1. app enabled? ✅<br/>2. 未命中保护规则<br/>3. all-users ✅
    OPA-->>PEP: allow=true
    PEP-->>GW: 通过，注入 X-Auth-* Header
    GW->>RS: 转发请求<br/>X-Auth-User-Id: zhangsan
    RS->>RS: 记录：POST, /v1/kb, user=zhangsan
    RS->>KB: 转发给后端
    KB->>KB: 创建知识库 kb-001
    KB-->>RS: 201 { "id": "kb-001" }
    RS->>RS: 检测：POST + 201 + 命中 /v1/kb 规则
    RS->>DB: INSERT resource_acl<br/>(kb-001, user, zhangsan, owner)
    RS-->>GW: 201 { "id": "kb-001" }
    GW-->>U: 201 创建成功
```

---

## 2 访问资源

李四访问张三的知识库，pep-proxy 查 resource_acl 判断权限。

```mermaid
sequenceDiagram
    participant U as 李四
    participant GW as Gateway
    participant PEP as pep-proxy
    participant OPA as OPA
    participant DB as resource_acl 表
    participant RS as resource-sync
    participant KB as kb-service

    U->>GW: GET /knowledgebase/v1/kb/kb-001<br/>Authorization: Bearer JWT
    GW->>PEP: ext-authz 鉴权请求
    PEP->>PEP: 验证 JWT<br/>提取 user_id=lisi
    PEP->>OPA: 路径鉴权<br/>path=/knowledgebase/v1/kb/kb-001
    OPA-->>PEP: allow=true（未命中保护规则, all-users）
    PEP->>DB: 资源级鉴权<br/>SELECT * FROM resource_acl<br/>WHERE resource_id='kb-001'<br/>AND subject_id='lisi'
    DB-->>PEP: 找到记录：permission=viewer
    PEP-->>GW: 通过
    GW->>RS: 转发请求
    RS->>KB: 透传（GET 不处理）
    KB-->>RS: 200 { 知识库数据 }
    RS-->>GW: 200（GET 不拦截，直接返回）
    GW-->>U: 200 返回数据
```

**李四没有权限的情况：**

```mermaid
sequenceDiagram
    participant U as 李四
    participant GW as Gateway
    participant PEP as pep-proxy
    participant OPA as OPA
    participant DB as resource_acl 表

    U->>GW: GET /knowledgebase/v1/kb/kb-001
    GW->>PEP: ext-authz 鉴权请求
    PEP->>PEP: 验证 JWT, user=lisi

    PEP->>OPA: 路径鉴权
    OPA-->>PEP: allow（未命中保护规则, all-users）

    PEP->>DB: SELECT * FROM resource_acl<br/>WHERE resource_id='kb-001'<br/>AND (subject_id='lisi'<br/>OR (subject_type='group'<br/>AND subject_id IN (lisi 的 groups)))
    DB-->>PEP: 无记录
    PEP-->>GW: 拒绝 403
    GW-->>U: 403 无权访问此资源
```

---

## 3 删除资源

张三删除自己的知识库，resource-sync 自动清理所有 ACL。

```mermaid
sequenceDiagram
    participant U as 张三
    participant GW as Gateway
    participant PEP as pep-proxy
    participant OPA as OPA
    participant DB as resource_acl 表
    participant RS as resource-sync
    participant KB as kb-service

    U->>GW: DELETE /knowledgebase/v1/kb/kb-001
    GW->>PEP: ext-authz 鉴权请求
    PEP->>PEP: 验证 JWT, user=zhangsan
    PEP->>OPA: 路径鉴权
    OPA-->>PEP: allow
    PEP->>DB: 资源级鉴权<br/>zhangsan 对 kb-001 的权限?
    DB-->>PEP: permission=owner ✅
    PEP-->>GW: 通过
    GW->>RS: 转发请求
    RS->>RS: 记录：DELETE, /v1/kb/kb-001
    RS->>KB: 转发给后端
    KB->>KB: 删除 kb-001
    KB-->>RS: 200 删除成功
    RS->>RS: 检测：DELETE + 200 + 命中 /v1/kb 规则
    RS->>DB: DELETE FROM resource_acl<br/>WHERE resource_id='kb-001'<br/>（删除所有 ACL，包括分享记录）
    RS-->>GW: 200
    GW-->>U: 200 删除成功
```

---

## 4 分享资源

张三把 kb-001 分享给李四。直接走 IAM 的 ACL API，不经过后端应用。

```mermaid
sequenceDiagram
    participant U as 张三
    participant GW as Gateway
    participant PEP as pep-proxy
    participant OPA as OPA
    participant DB as resource_acl 表
    participant RS as resource-sync

    U->>GW: POST /acl/v1/permissions<br/>{ resource_id: "kb-001",<br/>  subject_id: "lisi",<br/>  permission: "viewer" }
    GW->>PEP: ext-authz 鉴权请求
    PEP->>PEP: 验证 JWT, user=zhangsan
    PEP->>OPA: 路径鉴权
    OPA-->>PEP: allow（/acl/v1/ 未命中保护规则, all-users）
    PEP->>DB: zhangsan 是 kb-001 的 owner?
    DB-->>PEP: owner ✅（只有 owner 能分享）
    PEP-->>GW: 通过
    GW->>RS: 转发到 resource-sync 的 ACL API
    RS->>DB: INSERT resource_acl<br/>(kb-001, user, lisi, viewer)
    RS-->>GW: 201 分享成功
    GW-->>U: 201

    Note over U,RS: kb-service 完全不参与，不知道分享发生了
```

**分享给组：**

```mermaid
sequenceDiagram
    participant U as 张三
    participant GW as Gateway
    participant PEP as pep-proxy
    participant OPA as OPA
    participant DB as resource_acl 表
    participant RS as resource-sync

    U->>GW: POST /acl/v1/permissions<br/>{ resource_id: "kb-001",<br/>  subject_type: "group",<br/>  subject_id: "data-team",<br/>  permission: "viewer" }
    GW->>PEP: ext-authz 鉴权
    PEP->>PEP: 验证 JWT, user=zhangsan
    PEP->>OPA: 路径鉴权
    OPA-->>PEP: allow
    PEP->>DB: zhangsan 是 kb-001 的 owner? ✅
    PEP-->>GW: 通过
    GW->>RS: ACL API
    RS->>DB: INSERT resource_acl<br/>(kb-001, group, data-team, viewer)
    RS-->>GW: 201
    GW-->>U: 201

    Note over U: data-team 的所有成员<br/>（张三、李四）都能访问 kb-001
```

---

## 5 列出我的资源

用户查看自己能访问的知识库列表。pep-proxy 只做路径鉴权，后端主动调 resource-sync 内部接口获取可访问 ID 列表。

```mermaid
sequenceDiagram
    participant U as 李四
    participant GW as Gateway
    participant PEP as pep-proxy
    participant OPA as OPA
    participant RS as resource-sync
    participant DB as resource_acl 表
    participant KB as kb-service

    U->>GW: GET /knowledgebase/v1/kb
    GW->>PEP: ext-authz 鉴权

    PEP->>PEP: 验证 JWT<br/>user=lisi, groups=[data-team, all-users]
    PEP->>OPA: 路径鉴权
    OPA-->>PEP: allow（未命中保护规则, all-users）

    Note over PEP: 集合路径，不包含资源 ID<br/>不查 resource_acl，直接放行

    PEP-->>GW: 通过<br/>注入 X-Auth-User-Id, X-Auth-Groups
    GW->>RS: 转发
    RS->>KB: 透传

    Note over KB: 后端调 resource-sync 内部接口
    KB->>RS: GET http://resource-sync:8081/internal/v1/resources<br/>?user_id=lisi&groups=data-team,all-users<br/>&app_name=knowledgebase&resource_type=kb
    RS->>DB: SELECT resource_id FROM resource_acl<br/>WHERE user/group 匹配
    DB-->>RS: [kb-001, kb-005, kb-012]
    RS-->>KB: { "resource_ids": ["kb-001","kb-005","kb-012"] }

    KB->>KB: SELECT * FROM knowledge_bases<br/>WHERE id IN ('kb-001','kb-005','kb-012')
    KB-->>RS: 200 [kb-001, kb-005, kb-012 的数据]
    RS-->>GW: 200
    GW-->>U: 200 返回李四能看到的知识库列表
```

---

## 6 链路总览

```mermaid
flowchart LR
    U[用户] -->|HTTPS| GW[Gateway]
    GW -->|ext-authz| PEP[pep-proxy]
    PEP -->|路径鉴权| OPA[OPA]
    PEP -->|资源鉴权| DB[(resource_acl)]
    GW -->|转发| RS[resource-sync]
    RS -->|业务请求| APP[后端应用]
    RS -->|自动同步 ACL| DB
    RS -->|ACL API| DB
    APP -.->|内部接口<br/>查可访问资源| RS

    style GW fill:#4a9eff,color:#fff
    style PEP fill:#ff6b6b,color:#fff
    style RS fill:#51cf66,color:#fff
    style OPA fill:#ffd43b,color:#000
    style DB fill:#845ef7,color:#fff
    style APP fill:#868e96,color:#fff
```

**每个组件在链路中的角色：**

| 颜色 | 组件 | 链路中做什么 |
|------|------|-------------|
| 蓝色 | Gateway | HTTPS 解密、路由转发 |
| 红色 | pep-proxy | JWT 验证、路径鉴权(OPA)、资源实例鉴权(ACL 表) |
| 绿色 | resource-sync | 反向代理、自动注册/删除 ACL、ACL 管理 API、内部查询 API |
| 黄色 | OPA | 路径级策略判断（内存计算） |
| 紫色 | resource_acl | 资源权限数据存储 |
| 灰色 | 后端应用 | 纯业务逻辑，list/search 时调内部接口 |

