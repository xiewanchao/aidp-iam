# 接入流程视角 — 新应用怎么接入、各方做什么

> 版本：v1.0 | 日期：2026-04-02

---

## 1 接入全流程概览

```mermaid
flowchart TD
    START([新应用要接入]) --> STEP1[第1步：约定]
    STEP1 --> STEP2[第2步：IAM 侧配置]
    STEP2 --> STEP3[第3步：Gateway 路由配置]
    STEP3 --> STEP4[第4步：应用侧部署]
    STEP4 --> STEP5[第5步：验证]
    STEP5 --> DONE([接入完成])

    STEP1 -.- WHO1[应用团队 + IAM 团队]
    STEP2 -.- WHO2[IAM 团队 / 租户管理员]
    STEP3 -.- WHO3[IAM 团队]
    STEP4 -.- WHO4[应用团队]
    STEP5 -.- WHO5[双方一起]

    style START fill:#845ef7,color:#fff
    style DONE fill:#51cf66,color:#fff
```

---

## 2 第 1 步：约定（应用团队 + IAM 团队）

双方坐下来对齐三件事，不需要写代码：

```mermaid
flowchart LR
    subgraph 约定内容
        A1[应用名称<br/>如: newapp]
        A2[URL 前缀<br/>如: /newapp/]
        A3[RESTful 资源路径<br/>如: /v1/items]
    end

    A1 --> RESULT1[apps 表的 app_name]
    A2 --> RESULT2[Gateway 路由 + OPA 匹配]
    A3 --> RESULT3[resource_patterns 表]

    style A1 fill:#4a9eff,color:#fff
    style A2 fill:#4a9eff,color:#fff
    style A3 fill:#4a9eff,color:#fff
```

**约定清单：**

| 约定项 | 示例 | 用在哪 |
|--------|------|--------|
| 应用名称 | `newapp` | apps 表、{app}-admins 组名、环境变量 |
| URL 前缀 | `/newapp/` | Gateway 路由、OPA 路径匹配 |
| 资源路径 | `/v1/items` | resource_patterns 表 |
| 资源类型名 | `item` | resource_acl 表的 resource_type |
| 管理接口路径（可选） | `/v1/admin/` | path_rules 表（如果用 IAM 保护管理路径） |
| 响应体格式 | `{"id": "xxx"}` | resource-sync 提取资源 ID |

**RESTful 规范要求（必须遵守）：**

```
POST   /v1/items          → 创建，返回 201 + {"id": "item-001"}
GET    /v1/items           → 列表
GET    /v1/items/item-001  → 查看
PUT    /v1/items/item-001  → 更新
DELETE /v1/items/item-001  → 删除，返回 200 或 204
```

---

## 3 第 2 步：IAM 侧配置（管理员操作）

```mermaid
sequenceDiagram
    participant ADMIN as 租户管理员
    participant KP as keycloak-proxy
    participant PG as PostgreSQL
    participant KC as Keycloak
    participant BS as bundle-server
    participant OPA as OPA

    Note over ADMIN,KP: 3.1 注册应用

    ADMIN->>KP: POST /api/v1/apps<br/>{ app_name: "newapp",<br/>  path_prefix: "/newapp/",<br/>  display_name: "新应用" }

    par keycloak-proxy 自动执行
        KP->>PG: INSERT INTO apps
        KP->>PG: INSERT INTO resource_patterns<br/>(newapp, /v1/items, item)
        KP->>KC: 创建组 newapp-admins
    end

    KP-->>ADMIN: 201 注册成功

    Note over BS,OPA: bundle-server 自动同步
    BS->>PG: 读取最新 apps
    BS->>OPA: 推送 bundle（newapp.enabled=true）

    Note over ADMIN,KP: 3.2 配置路径保护规则（可选）

    ADMIN->>KP: POST /api/v1/path-rules<br/>{ path_prefix: "/newapp/v1/admin/",<br/>  required_group: "newapp-admins" }
    KP->>PG: INSERT INTO path_rules
    KP-->>ADMIN: 201

    Note over ADMIN,KP: 3.3 分配管理员

    ADMIN->>KP: PUT /api/v1/aidp/groups/newapp-admins/members<br/>{ user_id: "wangwu" }
    KP->>KC: 把 wangwu 加入 newapp-admins
    KP-->>ADMIN: 200
```

**IAM 侧完成后数据库状态：**

```
apps 表新增：
| tenant_id | app_name | path_prefix | enabled |
|-----------|----------|-------------|---------|
| aidp      | newapp   | /newapp/    | true    |

resource_patterns 表新增：
| tenant_id | app_name | resource_prefix | resource_type |
|-----------|----------|-----------------|---------------|
| aidp      | newapp   | /v1/items       | item          |

path_rules 表新增（可选）：
| tenant_id | path_prefix        | required_group |
|-----------|--------------------|----------------|
| aidp      | /newapp/v1/admin/  | newapp-admins  |

Keycloak 新增：
  组: newapp-admins → [wangwu]
```

---

## 4 第 3 步：Gateway 路由配置（IAM 团队）

```mermaid
flowchart LR
    subgraph Gateway HTTPRoute
        ROUTE[新增路由<br/>/newapp/ → resource-sync]
    end

    subgraph resource-sync
        RS[根据 apps 表<br/>知道 /newapp/ 转发到<br/>newapp-service]
    end

    subgraph 后端
        APP[newapp-service:80]
    end

    ROUTE --> RS --> APP
```

在 Helm chart 中添加 HTTPRoute：

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: newapp-route
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: https
      namespace: agentgateway-system
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
        - name: resource-sync       # 先到 resource-sync
          port: 8080
```

resource-sync 根据 apps 表的 `path_prefix` 知道 `/newapp/` 的请求应该转发到 `newapp-service`。

---

## 5 第 4 步：应用侧部署（应用团队）

```mermaid
flowchart TD
    subgraph 应用团队要做的
        D1[部署应用到 K8s]
        D2[环境变量 APP_NAME=newapp]
        D3[环境变量 RESOURCE_SYNC_URL=http://resource-sync:8081]
        D4[遵守 RESTful 规范<br/>POST 返回 201 + id<br/>DELETE 返回 200/204]
        D5[list/search 接口调内部 API 过滤]
    end

    subgraph 应用团队不用做的
        N1[不用做任何鉴权]
        N2[不用维护权限表]
        N3[不用验证 JWT]
        N4[不用检查 permission]
    end

    style D1 fill:#51cf66,color:#fff
    style D2 fill:#51cf66,color:#fff
    style D3 fill:#51cf66,color:#fff
    style N1 fill:#dee2e6,color:#000
    style N2 fill:#dee2e6,color:#000
    style N3 fill:#dee2e6,color:#000
    style N4 fill:#dee2e6,color:#000
```

应用的 Deployment 示例：

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: newapp-service
spec:
  replicas: 2
  template:
    spec:
      containers:
        - name: newapp
          image: newapp:latest
          env:
            - name: APP_NAME
              value: "newapp"
            - name: RESOURCE_SYNC_URL
              value: "http://resource-sync:8081"
          ports:
            - containerPort: 80
---
apiVersion: v1
kind: Service
metadata:
  name: newapp-service
spec:
  selector:
    app: newapp
  ports:
    - port: 80
```

**应用代码只需要读 Header（可选）：**

```python
@app.get("/v1/items/{item_id}")
def get_item(item_id, request):
    # 鉴权已经在 pep-proxy 做完了
    # 如果能走到这里，说明用户一定有权限
    item = db.get_item(item_id)
    return item

@app.put("/v1/items/{item_id}")
def update_item(item_id, request):
    # pep-proxy 已经检查了 PUT 需要 contributor 以上权限
    # 如果能走到这里，说明权限已经够了
    db.update_item(item_id, request.body)
    return {"status": "ok"}

@app.get("/v1/items")
def list_items(request):
    from aidp_acl import get_allowed_resources

    # 调 resource-sync 内部接口，获取可访问 ID 列表
    allowed_ids = get_allowed_resources(request, "newapp", "item")
    items = db.get_items_by_ids(allowed_ids)
    return items
```

---

## 6 第 5 步：验证

```mermaid
flowchart TD
    V1[验证1：创建资源] --> CHECK1{POST /newapp/v1/items<br/>返回 201?}
    CHECK1 -->|✅| V1_ACL{resource_acl 里<br/>有 owner 记录?}
    V1_ACL -->|✅| V2

    V2[验证2：访问资源] --> CHECK2{GET /newapp/v1/items/item-001<br/>owner 能访问?}
    CHECK2 -->|✅| CHECK3{其他用户访问<br/>返回 403?}
    CHECK3 -->|✅| V3

    V3[验证3：分享资源] --> CHECK4{POST /acl/v1/permissions<br/>分享给李四?}
    CHECK4 -->|✅| CHECK5{李四能访问?<br/>权限是 viewer?}
    CHECK5 -->|✅| V4

    V4[验证4：删除资源] --> CHECK6{DELETE /newapp/v1/items/item-001<br/>返回 200?}
    CHECK6 -->|✅| CHECK7{resource_acl 里<br/>记录已清除?}
    CHECK7 -->|✅| V5

    V5[验证5：管理接口] --> CHECK8{newapp-admins 能访问<br/>/newapp/v1/admin/?}
    CHECK8 -->|✅| CHECK9{普通用户访问<br/>返回 403?}
    CHECK9 -->|✅| DONE([✅ 接入验证通过])

    style DONE fill:#51cf66,color:#fff

    CHECK1 -->|❌| FIX1[检查 Gateway 路由<br/>和后端 Service]
    V1_ACL -->|❌| FIX2[检查 resource_patterns<br/>和 resource-sync 日志]
    CHECK3 -->|❌| FIX3[检查 pep-proxy<br/>resource_acl 查询逻辑]

    style FIX1 fill:#ff6b6b,color:#fff
    style FIX2 fill:#ff6b6b,color:#fff
    style FIX3 fill:#ff6b6b,color:#fff
```

**验证命令：**

```bash
# 获取 JWT
TOKEN=$(curl -s -X POST "https://gateway.aidp.com/realms/aidp/protocol/openid-connect/token" \
  -d "grant_type=password&client_id=data-agent&username=zhangsan&password=xxx" \
  | jq -r '.access_token')

# 验证1：创建资源
curl -X POST https://gateway.aidp.com/newapp/v1/items \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "测试数据"}' -v
# 预期：201 + {"id": "item-001"}

# 检查 ACL 是否自动写入
curl https://gateway.aidp.com/acl/v1/permissions?app_name=newapp\&resource_id=item-001 \
  -H "Authorization: Bearer $TOKEN"
# 预期：[{"subject_id": "zhangsan", "permission": "owner"}]

# 验证2：其他用户访问（用李四的 token）
curl https://gateway.aidp.com/newapp/v1/items/item-001 \
  -H "Authorization: Bearer $LISI_TOKEN" -v
# 预期：403

# 验证3：分享给李四
curl -X POST https://gateway.aidp.com/acl/v1/permissions \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"app_name":"newapp","resource_type":"item","resource_id":"item-001","subject_type":"user","subject_id":"lisi","permission":"viewer"}'
# 预期：201

# 验证4：李四现在能访问
curl https://gateway.aidp.com/newapp/v1/items/item-001 \
  -H "Authorization: Bearer $LISI_TOKEN" -v
# 预期：200（有权限就能访问）

# 验证5：删除资源
curl -X DELETE https://gateway.aidp.com/newapp/v1/items/item-001 \
  -H "Authorization: Bearer $TOKEN" -v
# 预期：200 + resource_acl 记录全部清除
```

---

## 7 接入清单（Checklist）

```mermaid
flowchart TD
    subgraph 约定阶段
        C1[☐ 确定应用名称 app_name]
        C2[☐ 确定 URL 前缀 path_prefix]
        C3[☐ 确定资源路径 resource_prefix]
        C4[☐ 确定资源类型 resource_type]
        C5[☐ 确认遵守 RESTful 规范]
        C6[☐ 确定是否需要管理接口保护]
    end

    subgraph IAM侧
        I1[☐ POST /api/v1/apps 注册应用]
        I2[☐ 确认 newapp-admins 组已创建]
        I3[☐ 配置 path_rules 可选]
        I4[☐ 分配管理员到 newapp-admins]
        I5[☐ 添加 Gateway HTTPRoute]
        I6[☐ 确认 resource_patterns 已写入]
    end

    subgraph 应用侧
        A1[☐ 部署应用到 K8s]
        A2[☐ 设置环境变量 APP_NAME]
        A3[☐ POST 返回 201 + id 字段]
        A4[☐ DELETE 返回 200 或 204]
        A5[☐ 配置 RESOURCE_SYNC_URL 环境变量]
        A6[☐ list/search 接口调内部 API 获取可访问 ID]
    end

    subgraph 验证
        V1[☐ 创建资源 → ACL 自动写入]
        V2[☐ 访问资源 → owner 可访问]
        V3[☐ 无权用户 → 403]
        V4[☐ 分享 → 被分享者可访问]
        V5[☐ 删除资源 → ACL 自动清除]
        V6[☐ 管理接口 → 只有 admins 可访问]
    end

    C1 --> C2 --> C3 --> C4 --> C5 --> C6
    C6 --> I1 --> I2 --> I3 --> I4 --> I5 --> I6
    I6 --> A1 --> A2 --> A3 --> A4 --> A5 --> A6
    A6 --> V1 --> V2 --> V3 --> V4 --> V5 --> V6
```

---

## 8 对比：接入前 vs 接入后应用的工作量

| 维度 | 没有 IAM（应用自己做） | 接入 IAM 后 |
|------|----------------------|------------|
| JWT 验证 | 自己实现 | 不用做（pep-proxy 做） |
| 用户/组管理 | 自己建表 | 不用做（Keycloak 管） |
| 路径权限 | 自己写中间件 | 不用做（OPA 管） |
| 资源权限表 | 自己建 shares 表 | 不用做（resource_acl 管） |
| 分享功能 | 自己写 API | 不用做（resource-sync ACL API） |
| 鉴权逻辑 | 每个接口都要写 | 不用做（pep-proxy + resource-sync 管） |
| **应用只需要做** | 全部自己做 | **遵守 RESTful 规范 + list/search 调一次内部接口** |
