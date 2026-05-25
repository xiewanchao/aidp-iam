# 应用接入调研指南 — 与业务团队对齐

> 版本：v1.1 | 日期：2026-04-21
>
> 本文档面向 **IAM 平台团队**，用于指导与各业务系统对接时的信息收集和讨论。配套接入流程见 [新应用接入流程](onboarding-flow.md)。

---

## 1 目标

每个接入 IAM 的应用，我们需要搞清楚三件事：

1. **有哪些资源** — 用户创建的、需要控制"谁能看谁能改"的东西
2. **每种资源的接口长什么样** — 创建、删除、查看、修改、列表分别怎么调
3. **有没有特殊保护路径** — 管理后台、危险操作等只允许特定人访问

---

## 2 信息采集表

发给业务团队填写。不需要解释 IAM 内部原理，用业务语言沟通。

### 2.1 应用基本信息

| 项目 | 填写 | 示例 |
|------|------|------|
| 应用名称 | | knowledgebase |
| 应用显示名 | | 知识库 |
| URL 前缀 | | /knowledgebase/ |
| 负责人 | | 张三 |
| 是否有 API 文档（Swagger/Postman） | | 有，地址是 xxx |

### 2.2 资源清单

**什么算"资源"：** 用户创建出来的、有归属的、需要控制访问权限的实体。比如知识库、文档、工作流、数据集、报表。

**什么不算"资源"：** 配置项、系统设置、搜索接口、统计接口、登录接口。这些走路径级保护即可，不需要填这张表。

每种资源填一行：

| 资源名称 | 资源类型标识 | 说明 |
|----------|------------|------|
| 知识库 | kb | 用户创建的知识库实例 |
| 文档 | doc | 知识库下的文档，属于子资源 |

### 2.3 接口详情（每种资源分别填写）

#### 资源：__________（填资源名称）

**创建接口：**

| 问题 | 填写 | 示例 |
|------|------|------|
| 请求方法 | | POST |
| 请求路径 | | /v1/kb |
| 成功时返回的 HTTP 状态码 | | 201 |
| 返回体里资源 ID 的字段名 | | id |
| 如果 ID 嵌套在 JSON 里，完整路径是？ | | data.kb_id |
| 返回体示例 | | `{"id": "kb-001", "name": "我的知识库"}` |

**删除接口：**

| 问题 | 填写 | 示例 |
|------|------|------|
| 请求方法 | | DELETE |
| 请求路径 | | /v1/kb/{id} |
| 如果不是标准路径，资源 ID 怎么传的？ | | URL 路径里 / query 参数 ?id=xxx / 请求体 {"id":"xxx"} |
| 成功时返回的 HTTP 状态码 | | 200 |

**查看单个资源：**

| 问题 | 填写 | 示例 |
|------|------|------|
| 请求方法 | | GET |
| 请求路径 | | /v1/kb/{id} |
| 资源 ID 怎么传的？ | | URL 路径里 |

**修改资源：**

| 问题 | 填写 | 示例 |
|------|------|------|
| 请求方法 | | PUT |
| 请求路径 | | /v1/kb/{id} |

**列表/搜索：**

| 问题 | 填写 | 示例 |
|------|------|------|
| 请求方法 | | GET |
| 请求路径 | | /v1/kb?page=1&size=20 |
| 分页参数怎么传？ | | query: page=1&size=20 |

**子资源（如果有）：**

| 问题 | 填写 | 示例 |
|------|------|------|
| 子资源名称 | | 文档 (doc) |
| 子资源接口路径 | | /v1/kb/{kb_id}/docs |
| 子资源操作需要父资源的什么权限？ | | 查看需要 viewer，创建/修改需要 contributor |

### 2.4 特殊保护路径（选填）

某些路径希望只有特定角色才能访问？

| 路径 | 谁能访问 | 说明 |
|------|---------|------|
| /v1/admin/ | 管理员 | 应用管理后台 |
| /v1/import/ | 管理员 | 批量导入，风险操作 |

---

## 3 对齐会议模板

30 分钟对齐会，逐条过以下问题：

### 第一轮：理解业务（10 分钟）

```
1. 你们系统里用户会创建什么东西？
   → 得到资源清单

2. 这些东西之间有没有层级关系？比如知识库下面有文档？
   → 得到子资源关系

3. 有没有需要限制只有管理员能用的功能？
   → 得到保护路径
```

### 第二轮：理解接口（15 分钟）

对每种资源逐个问：

```
4. 创建一个 xxx 调的是哪个接口？返回什么？
   → 得到 create_method, create_status, id_field
   关键追问：
   - 返回的 JSON 里新建资源的 ID 叫什么字段？
   - 状态码是 201 还是 200？

5. 删除一个 xxx 调的是哪个接口？
   → 得到 delete_method, delete_path
   关键追问：
   - 用 DELETE 方法还是 POST？
   - ID 怎么传的？在 URL 路径里、query 参数里、还是 body 里？

6. 查看/修改一个 xxx 时，资源 ID 怎么传的？
   → 得到 id_source
   关键追问：
   - 是 /v1/kb/kb-001 这种（ID 在路径里）？
   - 还是 /v1/kb?id=kb-001 这种（ID 在 query 里）？
   - 还是 POST body 里传 ID？

7. 列表/搜索接口长什么样？
   → 得到列表路径和分页方式
```

### 第三轮：确认约定（5 分钟）

```
8. 接入后你们需要做的：
   - 读 X-Auth-User-Id header（IAM 自动注入的登录用户 ID）
   - 列表接口读 X-Allowed-Ids header（IAM 自动注入的可访问资源 ID 列表）
   - 不需要自己做任何鉴权
   
9. 你们的 API 文档在哪？我们后续需要参考
```

---

## 4 收集后的转换

拿到业务信息后，IAM 团队翻译成系统配置。

> `POST /api/v1/apps` 一次性把 `resource_patterns`（含 `id_source` / `id_field` / `id_query_param`）和嵌套 `actions` 全部写入数据库，不需要再单独调 `resource-actions` 接口。

### 4.1 标准 RESTful（最简单，什么都不用额外配）

**业务说的：**
> 创建知识库：POST /v1/kb → 201 {"id": "kb-001"}
> 删除知识库：DELETE /v1/kb/kb-001 → 200
> 查看：GET /v1/kb/kb-001
> 修改：PUT /v1/kb/kb-001
> 列表：GET /v1/kb?page=1

**你要做的：**

只写最基本的 resource_pattern，`actions` 留空 → resource-sync 和 pep-proxy 回退到代码层 DEFAULT_ACTIONS：

```json
POST /api/v1/apps
{
  "app_name": "knowledgebase",
  "path_prefix": "/knowledgebase/",
  "resource_patterns": [
    { "resource_prefix": "/v1/kb", "resource_type": "kb" }
  ]
}
```

### 4.2 非标准但 ID 在路径或 query 里

**业务说的：**
> 创建：POST /v1/kb → 200 {"data": {"kb_id": "kb-001"}}
> 删除：POST /v1/kb/remove?id=kb-001 → 200
> 查看：GET /v1/kb?id=kb-001

**你要做的：**

注册时同时指定 `id_source` / `id_field` / `id_query_param`，把偏离默认的动作放到嵌套 `actions`：

```json
POST /api/v1/apps
{
  "app_name": "knowledgebase",
  "path_prefix": "/knowledgebase/",
  "resource_patterns": [
    {
      "resource_prefix": "/v1/kb",
      "resource_type": "kb",
      "id_source": "query",
      "id_field": "data.kb_id",
      "id_query_param": "id",
      "actions": [
        { "action": "create", "method": "POST", "success_status": 200, "min_permission": "none" },
        { "action": "delete", "method": "POST", "path_suffix": "/remove", "success_status": 200, "min_permission": "owner" },
        { "action": "read",   "method": "GET",  "min_permission": "viewer" }
      ]
    }
  ]
}
```

### 4.3 全部用 POST，路径区分操作，ID 在 body 里

**业务说的：**
> 创建：POST /v1/kb/create → 200 {"kb_id": "kb-001"}
> 删除：POST /v1/kb/delete body: {"kb_id": "kb-001"} → 200
> 查看：POST /v1/kb/detail body: {"kb_id": "kb-001"}
> 修改：POST /v1/kb/update body: {"kb_id": "kb-001", ...}
> 列表：POST /v1/kb/list body: {"page": 1}

**你要做的：**

```json
POST /api/v1/apps
{
  "app_name": "knowledgebase",
  "path_prefix": "/knowledgebase/",
  "resource_patterns": [
    {
      "resource_prefix": "/v1/kb",
      "resource_type": "kb",
      "id_source": "body",
      "id_field": "kb_id",
      "actions": [
        { "action": "create", "method": "POST", "path_suffix": "/create", "success_status": 200, "min_permission": "none" },
        { "action": "delete", "method": "POST", "path_suffix": "/delete", "success_status": 200, "min_permission": "owner" },
        { "action": "read",   "method": "POST", "path_suffix": "/detail", "min_permission": "viewer" },
        { "action": "update", "method": "POST", "path_suffix": "/update", "min_permission": "contributor" },
        { "action": "list",   "method": "POST", "path_suffix": "/list",   "min_permission": "none" }
      ]
    }
  ]
}
```

### 4.4 评估是否建议业务改造

有时候让业务改一点点比我们配一堆规则更划算：

| 业务现状 | 改造成本 | 建议 |
|----------|---------|------|
| POST 创建返回 200 而不是 201 | 改一行代码 | **建议改**，省得配 create_status |
| 删除用 POST /delete | 中等 | 评估，配规则也能解决 |
| ID 在 body 里，所有操作都用 POST | 高 | **不建议改**，配规则适配 |
| 完全不返回 ID | 高 | **必须改**，至少创建时返回 ID，否则无法自动 ACL |

**底线：创建接口必须返回资源 ID。** 如果创建接口不返回 ID，ext_proc 无法自动创建 owner ACL，这是唯一的硬性要求。

---

## 5 常见问题

### Q: 我们的搜索接口用 POST，不是 GET，怎么办？

没问题。在 resource_pattern 的 `actions` 中添加一条 `{ action: "list", method: "POST", path_suffix: "/xxx" }` 即可。ext_proc 会在请求阶段识别出这是列表请求并注入 `X-Allowed-Ids`。

### Q: 我们有些接口不涉及具体资源，比如 /v1/statistics，需要配吗？

不需要。不涉及资源的接口只走路径级鉴权（OPA 检查 app 启用 + path_rules），不需要配 resource_patterns。

### Q: 我们的子资源 ID 和父资源 ID 都在 body 里怎么办？

resource_patterns 支持多层配置。比如知识库下的文档：

```
resource_patterns:
  (app, /v1/kb,           kb,  body, kb_id, NULL)    -- 父资源
  (app, /v1/kb/docs,      doc, body, doc_id, NULL)   -- 子资源
```

pep-proxy 匹配到子资源时，会从 body 里找 kb_id 做父资源权限校验。

### Q: 我们的 API 完全不 RESTful，所有操作都是 POST /api，靠 body 里的 action 字段区分

这种极端情况建议用 path_rules 做粗粒度保护，不配 resource_patterns。等后续业务有意愿改造 API 时再接入资源级鉴权。

### Q: 接入后我们的代码需要改什么？

| 必须做 | 工作量 |
|--------|--------|
| 列表接口读 `X-Allowed-Ids` header 过滤结果 | 几行代码 |
| 读 `X-Auth-User-Id` header 做数据归属 | 几行代码 |
| 创建接口返回资源 ID | 通常已有 |

| 不需要做 | |
|----------|--|
| 验证 JWT | IAM 做 |
| 检查权限 | IAM 做 |
| 管理分享/ACL | IAM 做 |
| 集成任何 SDK | 不需要 |

---

## 6 接入优先级评估

收集完所有应用信息后，按这个顺序排优先级：

| 优先级 | 条件 | 说明 |
|--------|------|------|
| P0 先接 | 标准 RESTful，无需额外配置 | 零配置接入，快速验证 |
| P1 次接 | ID 在 path/query，只是方法或状态码不标准 | 配几条 resource_actions |
| P2 后接 | ID 在 body，需要开启 body 转发 | 需要 Gateway 配置 includeRequestBody |
| P3 评估 | API 极度非标准 | 先用 path_rules 粗保护，后续推动改造 |
