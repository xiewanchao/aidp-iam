# 接入流程视角 — 新应用怎么接入、各方做什么

> 版本：v2.1 | 日期：2026-04-21
>
> **架构要点**：单 realm（`aidp`） + Gateway 直连后端服务。resource-sync 通过 ext_proc 在响应阶段自动完成 ACL 同步，应用团队零 SDK 集成。Gateway 路由及策略绑定由 IAM 团队在 `da-cluster/gateway-routes/` 集中管理，应用团队只负责提供业务 Service。

---

## 1 接入全流程概览

```mermaid
flowchart TD
    START([新应用要接入]) --> STEP1[第1步：约定]
    STEP1 --> STEP2[第2步：IAM 侧注册应用]
    STEP2 --> STEP3[第3步：Gateway 路由与策略]
    STEP3 --> STEP4[第4步：应用侧部署]
    STEP4 --> STEP5[第5步：验证]
    STEP5 --> DONE([接入完成])

    STEP1 -.- WHO1[应用团队 + IAM 团队]
    STEP2 -.- WHO2[平台管理员 / IAM 团队]
    STEP3 -.- WHO3[IAM 团队集中管理]
    STEP4 -.- WHO4[应用团队]
    STEP5 -.- WHO5[双方一起]

    style START fill:#845ef7,color:#fff
    style DONE fill:#51cf66,color:#fff
```

**与旧版设计的关键差异：**

| 维度 | v2.0 原设计 | v2.1 实际 |
|------|------------|----------|
| 租户模型 | 多 tenant realm | 单 `aidp` realm，组：`admins` / `all-users` / `{app}-admins` |
| 路由归属 | 应用团队自建 HTTPRoute，IAM 团队挂策略 | IAM 团队统一维护 `da-cluster/gateway-routes/*.yaml`，`SecurityPolicy` / `EnvoyExtensionPolicy` 的 `targetRefs` 集中列举每条业务路由 |
| 资源模式注册 | 仅 `resource_prefix` + `resource_type` | 一次 POST 即可写入 `id_source` / `id_field` / `id_query_param` + 嵌套 `actions`（对齐 DB schema v2.1） |
| 路径规则 | `required_group` 单组 | `required_groups: string[]`（OR 语义） |

---

## 2 第 1 步：约定（应用团队 + IAM 团队）

对齐清单（不写代码）：

| 约定项 | 示例 | 用在哪 |
|--------|------|--------|
| 应用名称 | `newapp` | `apps.app_name`、自动派生 `{app}-admins` 组 |
| URL 前缀 | `/newapp/` | Gateway HTTPRoute + path_rules 命中 |
| 资源路径 | `/v1/items` | `resource_patterns.resource_prefix` |
| 资源类型名 | `item` | `resource_patterns.resource_type`、`resource_acl.resource_type` |
| 资源 ID 位置 | `path` / `query` / `body` | `resource_patterns.id_source` |
| ID 字段名（或嵌套路径） | `id` / `data.kb_id` | `resource_patterns.id_field` |
| query 参数名（若 id_source=query） | `item_id` | `resource_patterns.id_query_param` |
| 非标准操作规则 | 创建/删除/读/写/列表的方法 + 路径后缀 + 状态码 + 最低权限 | `resource_actions`（嵌套在 `resource_patterns.actions` 下一次性提交） |
| 管理接口路径（可选） | `/newapp/v1/admin/` | `path_rules` |
| 管理员组名 | `newapp-admins`（自动） | `admin_group`、`path_rules.required_groups` |

> 详细的应用调研和收集流程见 [应用接入调研指南](app-integration-guide.md)。

**标准 RESTful 应用（零额外配置）：**

```
POST   /v1/items          → 201 + {"id": "item-001"}
GET    /v1/items           → 列表
GET    /v1/items/item-001  → 查看
PUT    /v1/items/item-001  → 更新
DELETE /v1/items/item-001  → 200 / 204
```

标准 RESTful 应用只需填 `resource_patterns` 的基础字段（`resource_prefix` + `resource_type`），`actions` 留空 → resource-sync 与 pep-proxy 回退到代码层 `DEFAULT_ACTIONS`。

**非标准 API 应用（通过 `actions` 适配）：**

```
POST /v1/items/create  → 200 + {"item_id": "001"}
POST /v1/items/detail  body:{"item_id":"001"} → 查看
POST /v1/items/update  body:{"item_id":"001",...} → 修改
POST /v1/items/delete  body:{"item_id":"001"} → 删除
POST /v1/items/list    body:{"page":1} → 列表
```

非标准应用在 `resource_patterns[i].actions` 数组里逐条写入方法 + 路径后缀 + 最低权限。

**唯一硬性要求：创建接口必须返回资源 ID**（否则 ext_proc 无法写 owner ACL）。

---

## 3 第 2 步：IAM 侧注册应用（平台管理员 / IAM 团队）

```mermaid
sequenceDiagram
    participant ADMIN as 平台管理员
    participant KP as keycloak-proxy
    participant PP as pep-proxy
    participant PG as PostgreSQL (iam DB)
    participant KC as Keycloak (aidp realm)
    participant BS as bundle-server
    participant OPA as OPA

    Note over ADMIN,KP: 3.1 一次性注册应用 + 资源模式 + 非标准动作

    ADMIN->>KP: POST /api/v1/apps<br/>{ app_name, path_prefix,<br/>  resource_patterns: [{<br/>    resource_prefix, resource_type,<br/>    id_source, id_field, id_query_param,<br/>    actions: [...]<br/>  }] }

    par keycloak-proxy 在单事务内
        KP->>PG: INSERT INTO apps
        KP->>PG: INSERT INTO resource_patterns<br/>(含 id_source/id_field/id_query_param)
        KP->>PG: INSERT INTO resource_actions<br/>(逐条 action)
        KP->>KC: 在 aidp realm 创建组 {app_name}-admins
    end

    KP-->>ADMIN: 201 AppResponse（含完整 patterns + actions）

    Note over BS,OPA: bundle-server 周期性刷新（≤30s）
    BS->>PG: 读 apps + path_rules + path_rule_groups
    BS->>OPA: 推送 Rego bundle（newapp.enabled=true）

    Note over ADMIN,PP: 3.2 配置路径保护规则（可选）

    ADMIN->>PP: POST /api/v1/path-rules<br/>{ path_prefix: "/newapp/v1/admin/",<br/>  required_groups: ["newapp-admins"] }
    PP->>PG: INSERT path_rules + path_rule_groups
    PP-->>ADMIN: 201

    Note over ADMIN,KP: 3.3 分配管理员

    ADMIN->>KP: PUT /api/v1/aidp/groups/newapp-admins/members<br/>{ user_id: "wangwu" }
    KP->>KC: 把 wangwu 加入 newapp-admins
    KP-->>ADMIN: 200
```

**IAM 侧完成后数据库状态：**

```
apps:
| app_name | path_prefix | admin_group    | enabled |
| newapp   | /newapp/    | newapp-admins  | true    |

resource_patterns:
| app_name | resource_prefix | resource_type | id_source | id_field | id_query_param |
| newapp   | /v1/items       | item          | path      | id       | NULL           |

resource_actions（仅非标准 API 才会有行；标准 RESTful 下为空）：
  （空）

path_rules + path_rule_groups（可选）：
| rule_id | path_prefix        | method | required_groups  |
| 12      | /newapp/v1/admin/  | NULL   | [newapp-admins]  |

Keycloak（aidp realm）：
  组: newapp-admins → [wangwu]
```

> `apps`、`resource_patterns`、`resource_actions`、`path_rules`、`path_rule_groups` 均为系统级表，没有 `tenant_id`。
>
> 路径规则的 `required_groups` 是 OR 语义：用户只要命中其中任意一个组即放行。

**一次性注册的 curl 示例（含 flex 字段和嵌套 actions）：**

```bash
curl -X POST "$BASE/api/v1/apps" -H "Content-Type: application/json" -d '{
  "app_name": "newapp",
  "path_prefix": "/newapp/",
  "display_name": "新应用",
  "resource_patterns": [
    {
      "resource_prefix": "/v1/items",
      "resource_type": "item",
      "id_source": "body",
      "id_field": "data.item_id",
      "actions": [
        {"action": "create", "method": "POST", "path_suffix": "/create", "success_status": 200, "min_permission": "none"},
        {"action": "delete", "method": "POST", "path_suffix": "/delete", "min_permission": "owner"},
        {"action": "read",   "method": "POST", "path_suffix": "/detail", "min_permission": "viewer"},
        {"action": "update", "method": "POST", "path_suffix": "/update", "min_permission": "contributor"},
        {"action": "list",   "method": "POST", "path_suffix": "/list",   "min_permission": "none"}
      ]
    }
  ]
}'
```

---

## 4 第 3 步：Gateway 路由与策略（IAM 团队集中管理）

新架构下，Gateway 路由 / `SecurityPolicy` / `EnvoyExtensionPolicy` 统一由 IAM 团队维护在 `da-cluster/gateway-routes/protected-routes.yaml`，应用团队只需提供 Service 及其 DNS 名称。

```mermaid
flowchart TD
    subgraph IAM团队一次性改动
        YML["编辑 da-cluster/gateway-routes/protected-routes.yaml：<br/>① 新增一条 HTTPRoute: /newapp/ → newapp-service<br/>② 将 newapp-route 加入 SecurityPolicy.targetRefs (ext_authz)<br/>③ 将 newapp-route 加入 EnvoyExtensionPolicy.targetRefs (ext_proc)"]
        RG["在 reference-grants.yaml 中追加对 newapp 命名空间的授权（若跨命名空间）"]
        UPG["helm upgrade（或 kubectl apply）"]
    end

    subgraph 请求流转
        direction LR
        CLIENT[客户端] -->|1. 请求| GW[Envoy Gateway]
        GW -->|"2. ext_authz (gRPC)"| PEP[pep-proxy]
        PEP -->|3. 鉴权通过 + 注入 X-Auth-*| GW
        GW -->|4. 转发 + X-Auth-*| APP[newapp-service]
        APP -->|5. 响应| GW
        GW -->|"6. ext_proc 响应阶段"| RS[resource-sync]
        RS -->|7. 写入 resource_acl / 清除| GW
        GW -->|8. 返回| CLIENT
    end

    style YML fill:#4a9eff,color:#fff
    style RG fill:#4a9eff,color:#fff
    style UPG fill:#845ef7,color:#fff
```

**安全要求：** Gateway 在进入 ext_authz / ext_proc 之前必须清除客户端传入的 `X-Auth-*` 和 `X-Allowed-*` Header，防止伪造。

### 4.1 `protected-routes.yaml` 追加路由

```yaml
# da-cluster/gateway-routes/protected-routes.yaml（片段）
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: newapp-route
  namespace: envoy-gateway-system
spec:
  parentRefs:
  - name: eg
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /newapp/
    filters:
    - type: URLRewrite
      urlRewrite:
        path:
          type: ReplacePrefixMatch
          replacePrefixMatch: /
    backendRefs:
    - name: newapp-service      # 直连后端服务，不经过 resource-sync
      namespace: newapp
      port: 80
```

### 4.2 同一文件末尾扩展策略 targetRefs

```yaml
# SecurityPolicy: ext_authz → pep-proxy（所有需鉴权的路由都要在此列出）
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: SecurityPolicy
metadata:
  name: pep-proxy-extauthz
  namespace: envoy-gateway-system
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: keycloak-proxy-route
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: identity-api-route
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: acl-api-route
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: path-rules-route
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: mock-kb-route
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: mock-rubik-route
  - group: gateway.networking.k8s.io    # ← 新增
    kind: HTTPRoute
    name: newapp-route
  extAuth:
    grpc:
      backendRefs:
      - name: pep-proxy
        namespace: opa
        port: 9000
    failOpen: false
    bodyToExtAuth:
      maxRequestBytes: 8192
---
# EnvoyExtensionPolicy: ext_proc → resource-sync（仅业务路由需要 ACL 自动同步）
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: EnvoyExtensionPolicy
metadata:
  name: resource-sync-extproc
  namespace: envoy-gateway-system
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: mock-kb-route
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: mock-rubik-route
  - group: gateway.networking.k8s.io    # ← 新增
    kind: HTTPRoute
    name: newapp-route
  extProc:
  - backendRefs:
    - name: resource-sync
      namespace: resource-sync
      port: 8082
    processingMode:
      request:
        body: Streamed
      response:
        body: Streamed
    failOpen: true
```

### 4.3 跨命名空间授权

若 `newapp-service` 位于独立命名空间，在 `reference-grants.yaml` 中为 `newapp` 命名空间追加 `ReferenceGrant`，允许 Gateway 命名空间访问其 Service。

**ext_proc 响应阶段工作原理（创建场景）：**

```mermaid
sequenceDiagram
    participant C as 客户端
    participant GW as Gateway
    participant APP as newapp-service
    participant EP as resource-sync<br/>(ext_proc :8082)
    participant DB as PostgreSQL

    C->>GW: POST /newapp/v1/items
    Note over GW: ext_authz 鉴权通过<br/>注入 X-Auth-User-Id / X-Auth-Tenant / X-Auth-Groups
    GW->>EP: 请求阶段：headers (+ body 流式)
    EP-->>GW: 继续（记录请求上下文）
    GW->>APP: POST /v1/items
    APP-->>GW: 201 {"id": "item-001"}
    GW->>EP: 响应阶段：headers + body
    EP->>DB: INSERT INTO resource_acl<br/>(owner=zhangsan, resource_id=item-001)
    EP-->>GW: 继续（不修改响应）
    GW-->>C: 201 {"id": "item-001"}
```

---

## 5 第 4 步：应用侧部署（应用团队）

```mermaid
flowchart TD
    subgraph 应用团队要做的
        D1[部署应用到 K8s]
        D2[创建接口返回资源 ID<br/>唯一硬性要求]
        D3[读取 X-Auth-User-Id header<br/>用于数据归属]
        D4["list/search 接口读取 X-Allowed-Ids Header<br/>（ext_proc 请求阶段自动注入）"]
    end

    subgraph 应用团队不用做的
        N1[不用集成任何 SDK]
        N2[不用验证 JWT]
        N3[不用检查 permission]
        N4[不用维护权限表]
        N5[不用改成标准 RESTful<br/>IAM 通过 resource_actions 适配]
    end

    style D1 fill:#51cf66,color:#fff
    style D2 fill:#51cf66,color:#fff
    style D3 fill:#51cf66,color:#fff
    style D4 fill:#ffd43b,color:#000
    style N1 fill:#dee2e6,color:#000
    style N2 fill:#dee2e6,color:#000
    style N3 fill:#dee2e6,color:#000
    style N4 fill:#dee2e6,color:#000
    style N5 fill:#dee2e6,color:#000
```

> D4 标黄表示轻量约定：后端只需读取 ext_proc 自动注入的 `X-Allowed-Ids` Header。

应用的 Deployment 示例（与内置 `mock-kb` / `mock-rubik` 保持同一形态）：

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: newapp-service
  namespace: newapp
spec:
  replicas: 2
  template:
    spec:
      containers:
        - name: newapp
          image: newapp:latest
          ports:
            - containerPort: 80
---
apiVersion: v1
kind: Service
metadata:
  name: newapp-service
  namespace: newapp
spec:
  selector:
    app: newapp
  ports:
    - port: 80
```

> **无需特殊环境变量**：ext_authz / ext_proc 自动基于请求路径匹配 apps 与 resource_patterns。

**应用代码示例（极简）：**

```python
@app.post("/v1/items")
def create_item(request):
    # 鉴权已在 pep-proxy 完成（ext_authz）
    # ACL 同步由 ext_proc 自动完成（无需任何代码）
    user_id = request.headers.get("X-Auth-User-Id")
    item = db.create_item(data=request.body, created_by=user_id)
    return JSONResponse({"id": item.id}, status_code=201)  # 必须返回创建态 + id

@app.get("/v1/items/{item_id}")
def get_item(item_id, request):
    # 能走到这里，说明 pep-proxy 已确认用户有权限访问此资源
    item = db.get_item(item_id)
    return item

@app.delete("/v1/items/{item_id}")
def delete_item(item_id, request):
    # ext_proc 看到 2xx 后自动清除 resource_acl
    db.delete_item(item_id)
    return JSONResponse(status_code=200)

@app.get("/v1/items")
def list_items(request):
    # ext_proc 在请求阶段自动注入 X-Allowed-Ids
    allowed_ids = request.headers.get("X-Allowed-Ids", "")
    if not allowed_ids:
        return []
    items = db.get_items_by_ids(allowed_ids.split(","))
    return items
```

---

## 6 第 5 步：验证

```mermaid
flowchart TD
    V1[验证1：创建资源] --> CHECK1{POST /newapp/v1/items<br/>返回 201?}
    CHECK1 -->|是| V1_ACL{resource_acl 里<br/>有 owner 记录?}
    V1_ACL -->|是| V2

    V2[验证2：访问资源] --> CHECK2{GET /newapp/v1/items/item-001<br/>owner 能访问?}
    CHECK2 -->|是| CHECK3{其他用户访问<br/>返回 403?}
    CHECK3 -->|是| V3

    V3[验证3：分享资源] --> CHECK4{POST /acl/v1/resources/item-001/permissions<br/>分享给李四?}
    CHECK4 -->|是| CHECK5{李四能访问?<br/>权限是 viewer?}
    CHECK5 -->|是| V4

    V4[验证4：删除资源] --> CHECK6{DELETE /newapp/v1/items/item-001<br/>返回 200?}
    CHECK6 -->|是| CHECK7{resource_acl 里<br/>记录已清除?}
    CHECK7 -->|是| V5

    V5[验证5：管理接口] --> CHECK8{newapp-admins 能访问<br/>/newapp/v1/admin/?}
    CHECK8 -->|是| CHECK9{普通用户访问<br/>返回 403?}
    CHECK9 -->|是| DONE([接入验证通过])

    style DONE fill:#51cf66,color:#fff

    CHECK1 -->|否| FIX1[检查 HTTPRoute<br/>和后端 Service 配置]
    V1_ACL -->|否| FIX2[检查 ext_proc 策略绑定<br/>和 resource_patterns 配置]
    CHECK3 -->|否| FIX3[检查 ext_authz 策略绑定<br/>和 pep-proxy resource_acl 查询]

    style FIX1 fill:#ff6b6b,color:#fff
    style FIX2 fill:#ff6b6b,color:#fff
    style FIX3 fill:#ff6b6b,color:#fff
```

**验证命令：**

```bash
# 获取 JWT（单 realm：aidp）
TOKEN=$(curl -s -X POST "https://gateway.aidp.com/realms/aidp/protocol/openid-connect/token" \
  -d "grant_type=password&client_id=data-agent&username=zhangsan&password=xxx" \
  | jq -r '.access_token')

# 验证1：创建资源
curl -X POST https://gateway.aidp.com/newapp/v1/items \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "测试数据"}' -v
# 预期：201 + {"id": "item-001"}，ext_proc 自动写入 resource_acl(owner=zhangsan)

# 检查 ACL 是否自动写入
curl https://gateway.aidp.com/acl/v1/resources/item-001/permissions?app_name=newapp&resource_type=item \
  -H "Authorization: Bearer $TOKEN"
# 预期：[{"subject_id": "zhangsan", "permission": "owner"}]

# 验证2：其他用户访问
curl https://gateway.aidp.com/newapp/v1/items/item-001 \
  -H "Authorization: Bearer $LISI_TOKEN" -v
# 预期：403

# 验证3：分享给李四
curl -X POST https://gateway.aidp.com/acl/v1/resources/item-001/permissions \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"app_name":"newapp","resource_type":"item","subject_type":"user","subject_id":"lisi","permission":"viewer"}'
# 预期：201

# 验证4：李四现在能访问
curl https://gateway.aidp.com/newapp/v1/items/item-001 \
  -H "Authorization: Bearer $LISI_TOKEN" -v
# 预期：200

# 验证5：删除资源
curl -X DELETE https://gateway.aidp.com/newapp/v1/items/item-001 \
  -H "Authorization: Bearer $TOKEN" -v
# 预期：200 / 204，resource_acl 记录全部清除（ext_proc 自动处理）
```

---

## 7 接入清单（Checklist）

```mermaid
flowchart TD
    subgraph 约定阶段
        C1["[ ] 应用名称 app_name"]
        C2["[ ] URL 前缀 path_prefix"]
        C3["[ ] 资源路径 resource_prefix + resource_type"]
        C4["[ ] 资源 ID 位置 id_source (path/query/body) + id_field / id_query_param"]
        C5["[ ] 非标准动作列表（actions[]：method/path_suffix/success_status/min_permission）"]
        C6["[ ] 管理接口保护路径（可选）"]
    end

    subgraph IAM侧注册
        I1["[ ] POST /api/v1/apps（含 resource_patterns + 嵌套 actions）"]
        I2["[ ] 自动创建 {app}-admins 组（aidp realm）"]
        I3["[ ] 配置 path_rules + required_groups（可选）"]
        I4["[ ] 分配管理员到 {app}-admins"]
        I5["[ ] DB 校验 patterns + actions 已写入"]
    end

    subgraph IAM侧路由
        R1["[ ] 编辑 da-cluster/gateway-routes/protected-routes.yaml：追加 HTTPRoute"]
        R2["[ ] 将新路由加入 SecurityPolicy.targetRefs"]
        R3["[ ] 将新路由加入 EnvoyExtensionPolicy.targetRefs"]
        R4["[ ] 必要时更新 reference-grants.yaml"]
        R5["[ ] helm upgrade / kubectl apply"]
    end

    subgraph 应用侧部署
        A1["[ ] 部署 Deployment + Service"]
        A2["[ ] 创建接口返回资源 ID（唯一硬性要求）"]
        A3["[ ] 读取 X-Auth-User-Id header"]
        A4["[ ] list/search 读取 X-Allowed-Ids Header"]
    end

    subgraph 验证
        V1["[ ] 创建资源 → ACL 自动写入"]
        V2["[ ] 访问资源 → owner 可访问"]
        V3["[ ] 无权用户 → 403"]
        V4["[ ] 分享 → 被分享者可访问"]
        V5["[ ] 删除资源 → ACL 自动清除"]
        V6["[ ] 管理接口 → 只有 admins 可访问"]
    end

    C1 --> C2 --> C3 --> C4 --> C5 --> C6
    C6 --> I1 --> I2 --> I3 --> I4 --> I5
    I5 --> R1 --> R2 --> R3 --> R4 --> R5
    R5 --> A1 --> A2 --> A3 --> A4
    A4 --> V1 --> V2 --> V3 --> V4 --> V5 --> V6
```

---

## 8 对比：接入前 vs 接入后应用的工作量

| 维度 | 没有 IAM（应用自己做） | 接入 IAM 后 |
|------|----------------------|------------|
| JWT 验证 | 自己实现中间件 | 不用做（ext_authz → pep-proxy） |
| 用户/组管理 | 自己建表和 API | 不用做（Keycloak aidp realm 管理） |
| 路径权限 | 自己写中间件 | 不用做（OPA + path_rules） |
| 资源权限表 | 自己建 shares 表 | 不用做（resource_acl 由 ext_proc 自动维护） |
| 分享功能 | 自己写分享 API | 不用做（`/acl/v1/resources/*` API） |
| 鉴权逻辑 | 每个接口都要写 | 不用做（ext_authz + ext_proc 全自动） |
| SDK 集成 | 引入鉴权 SDK | **不需要任何 SDK** |
| 环境变量 | 配置各种密钥 | **不需要特殊环境变量** |
| Gateway 路由 | 自建或走平台 | **IAM 团队集中维护 `gateway-routes/*.yaml`** |
| **应用只需要做** | 全部自己做 | **创建接口返回资源 ID + 读 X-Auth-User-Id + list/search 读 X-Allowed-Ids** |

### 工作量直观对比

```mermaid
flowchart LR
    subgraph 没有IAM时应用要做的
        direction TB
        W1[JWT 验证中间件]
        W2[用户管理模块]
        W3[权限检查中间件]
        W4[ACL 表设计 + 维护]
        W5[分享 API]
        W6[鉴权 SDK 集成]
        W7[业务逻辑]
    end

    subgraph 接入IAM后应用只做
        direction TB
        S1["遵守约定（POST→创建态+id, DELETE→2xx）"]
        S2["读 X-Auth-User-Id"]
        S3["读 X-Allowed-Ids（ext_proc 注入）"]
        S4[业务逻辑]
    end

    style W1 fill:#ff6b6b,color:#fff
    style W2 fill:#ff6b6b,color:#fff
    style W3 fill:#ff6b6b,color:#fff
    style W4 fill:#ff6b6b,color:#fff
    style W5 fill:#ff6b6b,color:#fff
    style W6 fill:#ff6b6b,color:#fff
    style W7 fill:#51cf66,color:#fff
    style S1 fill:#ffd43b,color:#000
    style S2 fill:#ffd43b,color:#000
    style S3 fill:#ffd43b,color:#000
    style S4 fill:#51cf66,color:#fff
```

红色 = 不需要做了 | 黄色 = 轻量约定 | 绿色 = 业务逻辑

---

## 9 内置应用与新应用的区别

| 项 | 内置应用（`knowledgebase` / `rubik` / `memory`） | 后续接入的新应用 |
|----|----------------------------------|----------------|
| 注册方式 | 由 `da-cluster/images/keycloak-init/init-keycloak.py` 在首次部署时幂等写入 `apps` / `resource_patterns` / `resource_actions` / `path_rules` + `path_rule_groups` | `POST /api/v1/apps`（单次调用即可把 patterns + actions 写全） |
| Gateway 路由 | 随 Helm chart 一起在 `gateway-routes/protected-routes.yaml` 里预置 | IAM 团队按第 4 步手动追加 |
| 幂等性 | init-job 使用 `ON CONFLICT DO UPDATE / DO NOTHING`，不覆盖人工修改的字段 | REST API 按资源语义处理 409 冲突 |

内置三个应用的 `resource_patterns` / `resource_actions` 配置可作为新应用的最佳实践参考。
