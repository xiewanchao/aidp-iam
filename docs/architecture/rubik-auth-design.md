# RubikSQL 鉴权设计

> 基于 IAM v2.1（两级鉴权 + 单租户 + groups）重新设计 RubikSQL 所有接口的鉴权方案。

## 1 设计原则

IAM 对 RubikSQL 提供两级鉴权：

| 级别 | 作用 | 存储 | 匹配方式 |
|------|------|------|---------|
| 路径级 (`path_rules`) | 按用户组放行/拦截**路径+Method** | `path_rules` + `path_rule_groups` | `startswith(path, prefix)` + method 精确匹配 |
| 资源级 (`resource_acl`) | 按资源实例（`database` / `session`）验证 owner/contributor/viewer | `resource_acl` | 资源 ID 精确匹配 |

**默认动作映射**（无 `resource_actions` 记录时生效）：

| HTTP Method | 最低权限 |
|-------------|---------|
| GET | viewer |
| POST (create) | none (所有登录用户) |
| PUT / PATCH | contributor |
| DELETE | owner |

**核心业务规则**：

1. 任意登录用户可 `POST /rubik/api/databases` 创建数据库，创建者自动成为 `owner`（`resource-sync` 通过 `ext_proc` 写入 `resource_acl`）
2. `/rubik/api/databases/{db_id}/...` 下所有子资源（knowledge/skill/build/sync/metadata）**继承父 database 的权限**
3. `/rubik/api/sessions/*` 会话为用户私有资源，创建者 owner，其他用户不可见
4. 全局配置（`/rubik/api/config/*`、`/rubik/api/databases/config/*`、`/rubik/api/databases/knowledge/special`）只允许 `rubik-admins`
5. `POST /rubik/api/databases/{db_id}/execute-sql` 与 `GET /rubik/api/databases/config/data-dir` 属于**高敏操作**，需要更高权限

## 2 应用注册

```sql
-- apps
INSERT INTO apps (app_name, path_prefix, display_name, description, enabled) VALUES
  ('rubik', '/rubik/', '智能问数 (RubikSQL)', '自然语言转SQL查询平台', true);
```

## 3 resource_patterns（资源 ID 提取规则）

RubikSQL 有 **三类资源**：`database`、`session`、以及特殊的 `query`（body 中带 `database_id`）。

```sql
INSERT INTO resource_patterns (app_name, resource_prefix, resource_type, id_source, id_field) VALUES
  -- 数据库：/api/databases/{id}/... → 从 path 取 id
  ('rubik', '/api/databases',  'database', 'path', 'id'),
  -- 会话：/api/sessions/{session_id}/... → 从 path 取 id
  ('rubik', '/api/sessions',   'session',  'path', 'id'),
  -- 自然语言查询：POST /api/query → 从 body 取 database_id
  ('rubik', '/api/query',      'database', 'body', 'database_id'),
  -- 元数据：/api/metadata/{db_id}/... → 复用 database 鉴权
  ('rubik', '/api/metadata',   'database', 'path', 'id');
```

## 4 resource_actions（非标准 RESTful 操作）

为 RubikSQL 中偏离默认 Method→Permission 映射的操作配置 override。

```sql
INSERT INTO resource_actions (app_name, resource_prefix, method, path_suffix, action, success_status, min_permission) VALUES
  -- ========== Database CRUD ==========
  -- POST /api/databases → create（成功状态 201/200 均可），none（所有登录用户可创建）
  ('rubik', '/api/databases', 'POST',   NULL,        'create', 201,  'none'),
  -- DELETE /api/databases/{id} → delete（需要 owner）
  ('rubik', '/api/databases', 'DELETE', '/{id}',     'delete', NULL, 'owner'),

  -- ========== 数据库级敏感操作（提升权限） ==========
  -- SQL 执行：对数据修改可能造成不可逆影响，需 contributor 而非 viewer
  ('rubik', '/api/databases', 'POST',   '/{id}/execute-sql',   'write', NULL, 'contributor'),
  -- SQL 美化：纯展示，降级到 viewer 即可
  ('rubik', '/api/databases', 'POST',   '/{id}/prettify-sql',  'read',  NULL, 'viewer'),

  -- ========== 知识库构建 ==========
  ('rubik', '/api/databases', 'POST',   '/{id}/build',          'write',  NULL, 'contributor'),
  ('rubik', '/api/databases', 'POST',   '/{id}/build/stream',   'write',  NULL, 'contributor'),
  ('rubik', '/api/databases', 'POST',   '/{id}/build/cancel',   'write',  NULL, 'contributor'),

  -- ========== 知识管理（写操作） ==========
  ('rubik', '/api/databases', 'POST',   '/{id}/knowledge/taxonomy',   'write', NULL, 'contributor'),
  ('rubik', '/api/databases', 'POST',   '/{id}/knowledge/custom',     'write', NULL, 'contributor'),
  ('rubik', '/api/databases', 'POST',   '/{id}/knowledge/experience', 'write', NULL, 'contributor'),
  ('rubik', '/api/databases', 'POST',   '/{id}/knowledge/import',     'write', NULL, 'contributor'),
  ('rubik', '/api/databases', 'POST',   '/{id}/knowledge/export/stream', 'read', NULL, 'viewer'),

  -- ========== 技能管理 ==========
  ('rubik', '/api/databases', 'POST',   '/{id}/skill/custom',   'write', NULL, 'contributor'),

  -- ========== 知识同步 ==========
  ('rubik', '/api/databases', 'POST',   '/{id}/sync',           'write', NULL, 'contributor'),
  ('rubik', '/api/databases', 'POST',   '/{id}/sync/stream',    'write', NULL, 'contributor'),

  -- ========== 元数据 ==========
  -- GET /api/metadata/{db_id}/init 名义是 GET 但会初始化数据，按 write 对待
  ('rubik', '/api/metadata',  'GET',    '/{id}/init',           'write', NULL, 'contributor'),

  -- ========== 查询（body 中带 database_id） ==========
  -- POST /api/query：消耗资源（LLM 调用），需要 viewer 权限才能查询
  ('rubik', '/api/query',     'POST',   NULL,        'read',   NULL, 'viewer'),

  -- ========== 会话管理（session 为用户私有资源） ==========
  -- POST /api/sessions → create，所有登录用户可创建
  ('rubik', '/api/sessions',  'POST',   NULL,        'create', 201,  'none'),
  -- DELETE /api/sessions/{id} → owner
  ('rubik', '/api/sessions',  'DELETE', '/{id}',     'delete', NULL, 'owner'),
  -- 回放、turns、反馈等都是对自己会话的操作，owner
  ('rubik', '/api/sessions',  'POST',   '/replay',           'read', NULL, 'owner'),
  ('rubik', '/api/sessions',  'GET',    '/{id}/replay',      'read', NULL, 'owner'),
  ('rubik', '/api/sessions',  'GET',    '/{id}/turns',       'read', NULL, 'owner'),
  ('rubik', '/api/sessions',  'POST',   '/{id}/turns/{turn_id}/feedback', 'write', NULL, 'owner');
```

## 5 path_rules（路径级组鉴权）

只列出需要**限定 `rubik-admins` 的全局管理路径**。其余业务路径不写 `path_rules`，走默认的 `all-users` 放行，再由 `resource_acl` 做实例级控制。

```sql
-- 配置管理（全局）：rubik-admins 专属
INSERT INTO path_rules (path_prefix, method, required_group, description) VALUES
  -- 数据库全局配置
  ('/rubik/api/databases/config/data-dir', 'GET',  'rubik-admins', '获取数据目录路径（敏感：暴露后端文件系统布局）'),

  -- 全局知识（跨库）
  ('/rubik/api/databases/knowledge/special', 'POST', 'rubik-admins', '添加特殊知识（全局共享）'),

  -- 配置管理：所有 PUT / POST / DELETE 操作
  ('/rubik/api/config/models/',             'PUT',    'rubik-admins', '更新模型预设'),
  ('/rubik/api/config/database-providers/', 'PUT',    'rubik-admins', '更新数据库提供者默认值'),
  ('/rubik/api/config/language',            'PUT',    'rubik-admins', '设置语言（全局）'),
  ('/rubik/api/config/languages',           'PUT',    'rubik-admins', '分别设置 app/query 语言'),
  ('/rubik/api/config/app/',                'PUT',    'rubik-admins', '设置应用配置项'),
  ('/rubik/api/config/reload',              'POST',   'rubik-admins', '重载配置'),
  ('/rubik/api/config/setup',               'POST',   'rubik-admins', '初始化/重置配置'),
  ('/rubik/api/config/open-path',           'POST',   'rubik-admins', '在文件管理器中打开路径（敏感）'),
  ('/rubik/api/config/llm-providers',       'POST',   'rubik-admins', '创建 LLM 提供者'),
  ('/rubik/api/config/llm-providers/',      'PUT',    'rubik-admins', '更新 LLM 提供者'),
  ('/rubik/api/config/llm-providers/',      'DELETE', 'rubik-admins', '删除 LLM 提供者');
```

`path_rule_groups` 对应插入：每条 `path_rule` → `rubik-admins`。

## 6 完整鉴权决策矩阵

按接口 × 鉴权层级逐条说明：

### 6.1 数据库管理

| Method | 路径 | 路径级 | 资源级 | 最终要求 |
|--------|------|-------|-------|---------|
| GET | `/api/databases` | all-users 通过 | 无（列表由 `X-Allowed-Ids` 过滤） | 登录 |
| POST | `/api/databases` | all-users 通过 | `create` → none | 登录（创建后自动成为 owner） |
| GET | `/api/databases/{db_id}` | all-users 通过 | `read` → viewer | 对该 db 有 viewer+ |
| DELETE | `/api/databases/{db_id}` | all-users 通过 | `delete` → owner | 对该 db 有 owner |
| GET | `/api/databases/{db_id}/check` | all-users 通过 | `read` → viewer | viewer+ |
| GET | `/api/databases/{db_id}/schema` | all-users 通过 | `read` → viewer | viewer+ |
| GET | `/api/databases/{db_id}/tables-columns` | all-users 通过 | `read` → viewer | viewer+ |
| GET | `/api/databases/{db_id}/tables/{table}` | all-users 通过 | `read` → viewer | viewer+ |
| POST | `/api/databases/{db_id}/prettify-sql` | all-users 通过 | `read` → viewer (override) | viewer+ |
| POST | `/api/databases/{db_id}/execute-sql` | all-users 通过 | `write` → contributor (override) | contributor+ |
| GET | `/api/databases/config/data-dir` | **path_rule: rubik-admins** | — | rubik-admins |

### 6.2 知识库构建

| Method | 路径 | 路径级 | 资源级 | 最终要求 |
|--------|------|-------|-------|---------|
| POST | `/api/databases/{db_id}/build` | all-users | `write` → contributor | contributor+ |
| GET | `/api/databases/{db_id}/build/status` | all-users | `read` → viewer | viewer+ |
| POST | `/api/databases/{db_id}/build/stream` | all-users | `write` → contributor | contributor+ |
| POST | `/api/databases/{db_id}/build/cancel` | all-users | `write` → contributor | contributor+ |

### 6.3 知识管理

| Method | 路径 | 路径级 | 资源级 | 最终要求 |
|--------|------|-------|-------|---------|
| GET | `/api/databases/{db_id}/knowledge/**` | all-users | `read` → viewer | viewer+ |
| PUT | `/api/databases/{db_id}/knowledge/{type}/{item}` | all-users | `write` → contributor | contributor+ |
| DELETE | `/api/databases/{db_id}/knowledge/{type}/{item}` | all-users | `delete` → owner（默认 map） | owner（可讨论降为 contributor） |
| POST | `/api/databases/{db_id}/knowledge/taxonomy` | all-users | contributor (override) | contributor+ |
| POST | `/api/databases/{db_id}/knowledge/custom` | all-users | contributor (override) | contributor+ |
| POST | `/api/databases/{db_id}/knowledge/experience` | all-users | contributor (override) | contributor+ |
| POST | `/api/databases/{db_id}/knowledge/import` | all-users | contributor (override) | contributor+ |
| POST | `/api/databases/{db_id}/knowledge/export/stream` | all-users | viewer (override) | viewer+ |
| POST | `/api/databases/knowledge/special` | **path_rule: rubik-admins** | — | rubik-admins |

> 说明：`DELETE /knowledge/{item}` 默认 map 到 `owner`，但知识项只是 database 的子资源。可考虑在 `resource_actions` 中 override 为 `contributor`（需要业务侧确认是否允许 contributor 删知识）。

### 6.4 技能管理

| Method | 路径 | 路径级 | 资源级 | 最终要求 |
|--------|------|-------|-------|---------|
| POST | `/api/databases/{db_id}/skill/custom` | all-users | contributor (override) | contributor+ |
| PUT | `/api/databases/{db_id}/skill/{id}` | all-users | `write` → contributor | contributor+ |

### 6.5 知识同步

| Method | 路径 | 路径级 | 资源级 | 最终要求 |
|--------|------|-------|-------|---------|
| POST | `/api/databases/{db_id}/sync` | all-users | contributor (override) | contributor+ |
| POST | `/api/databases/{db_id}/sync/stream` | all-users | contributor (override) | contributor+ |

### 6.6 元数据管理

| Method | 路径 | 路径级 | 资源级 | 最终要求 |
|--------|------|-------|-------|---------|
| GET | `/api/metadata/{db_id}/init` | all-users | `write` → contributor (override) | contributor+ |
| GET | `/api/metadata/{db_id}` | all-users | `read` → viewer | viewer+ |
| PUT | `/api/metadata/{db_id}/description` | all-users | `write` → contributor | contributor+ |

### 6.7 查询

| Method | 路径 | 路径级 | 资源级 | 最终要求 |
|--------|------|-------|-------|---------|
| POST | `/api/query` | all-users | `read` → viewer（对 body.database_id） | 对查询的 db 有 viewer+ |

### 6.8 会话管理

| Method | 路径 | 路径级 | 资源级 | 最终要求 |
|--------|------|-------|-------|---------|
| GET | `/api/sessions` | all-users | 无（列表由 `X-Allowed-Ids` 过滤） | 登录 |
| POST | `/api/sessions` | all-users | `create` → none | 登录（创建后 owner） |
| DELETE | `/api/sessions/{id}` | all-users | `delete` → owner | owner |
| POST | `/api/sessions/replay` | all-users | owner (override，从 body 取 session_id) | owner |
| GET | `/api/sessions/{id}/replay` | all-users | owner (override) | owner |
| GET | `/api/sessions/{id}/turns` | all-users | owner (override) | owner |
| POST | `/api/sessions/{id}/turns/{turn}/feedback` | all-users | owner (override) | owner |

> 说明：`POST /api/sessions/replay` 从 body 取 `session_id`。当前 `resource_patterns.id_source` 是 per-prefix 的，需要 resource-sync/pep-proxy 额外支持 "prefix + path_suffix 匹配时覆盖 id_source" 的能力；或者把这个接口改成 `/api/sessions/{id}/replay` 的 POST 版本，使其与其他会话接口一致。建议业务侧改造 API 路径。

### 6.9 配置管理

| Method | 路径 | 路径级 | 最终要求 |
|--------|------|-------|---------|
| GET | `/api/config` | all-users | 登录 |
| GET | `/api/config/models` | all-users | 登录 |
| PUT | `/api/config/models/{preset}` | **rubik-admins** | rubik-admins |
| GET | `/api/config/database-providers` | all-users | 登录 |
| PUT | `/api/config/database-providers/{provider}` | **rubik-admins** | rubik-admins |
| GET | `/api/config/language` | all-users | 登录 |
| PUT | `/api/config/language` | **rubik-admins** | rubik-admins |
| PUT | `/api/config/languages` | **rubik-admins** | rubik-admins |
| GET | `/api/config/app` | all-users | 登录 |
| PUT | `/api/config/app/{key}` | **rubik-admins** | rubik-admins |
| POST | `/api/config/reload` | **rubik-admins** | rubik-admins |
| POST | `/api/config/setup` | **rubik-admins** | rubik-admins |
| GET | `/api/config/paths/logs` | all-users | 登录（或升级为 admins） |
| POST | `/api/config/open-path` | **rubik-admins** | rubik-admins |
| GET | `/api/config/llm-providers` | all-users | 登录 |
| POST | `/api/config/llm-providers` | **rubik-admins** | rubik-admins |
| PUT | `/api/config/llm-providers/{name}` | **rubik-admins** | rubik-admins |
| DELETE | `/api/config/llm-providers/{name}` | **rubik-admins** | rubik-admins |

## 7 用户组

```
admins            — IAM 全局管理员（可跨应用）
all-users         — 所有登录用户（默认加入）
rubik-admins      — RubikSQL 管理员，负责全局配置和特殊知识
```

## 8 实现要点

### 8.1 body-based 资源 ID 提取

`POST /api/query` 和 `POST /api/sessions/replay` 的资源 ID 都在请求体中。当前 `SecurityPolicy.bodyToExtAuth.maxRequestBytes=8192` 已开启，pep-proxy 会收到完整 body。

- `/api/query` 走 `resource_patterns.id_source='body'` + `id_field='database_id'`，已配置
- `/api/sessions/replay` 建议改路径为 `/api/sessions/{id}/replay`（POST 版本），避免 body-based 提取

### 8.2 列表过滤（X-Allowed-Ids）

- `GET /api/databases` → resource-sync 通过 `ext_proc` 注入 `X-Allowed-Ids` 为当前用户有 viewer+ 的 database ID 列表，Rubik 后端据此过滤响应
- `GET /api/sessions` → 同理，注入当前用户拥有的 session_id 列表

### 8.3 database 删除级联

`DELETE /api/databases/{db_id}` 成功后（默认 200/204），resource-sync 需要：
- 删除 `resource_acl` 中所有 `resource_type='database' AND resource_id=db_id` 的记录
- 删除 `resource_type='session'` 中所有 metadata 引用该 db_id 的 session ACL（按需实现）

### 8.4 创建即 owner

`POST /api/databases` 成功响应 201 后，resource-sync 从响应 body 的 `id` 字段提取资源 ID，写入 `resource_acl(tenant_id, app_name='rubik', resource_type='database', resource_id=<id>, subject_id=<user_id>, permission='owner')`。

## 9 与 KB 对比

| 维度 | KB | RubikSQL |
|------|----|---------| 
| 创建模式 | POST /knowledge_bases/add，body 返 `KDSID` | POST /api/databases，body 返 `id` |
| 写操作路径 | `/knowledge_bases/add` / `/remove` / `/modify`（扁平） | `/api/databases/{id}/...`（嵌套子资源） |
| 默认 method→perm 能否覆盖大多数 | 否（全部 POST，需要手工在 resource_actions 里配） | 是（大部分标准 RESTful，少数 POST 需要 override） |
| 路径级组限制 | 大量（每个写接口一条 path_rule 给 kb-admins） | 少量（只有全局 config 给 rubik-admins） |

## 10 最终 SQL 种子

参考第 2-5 章的 SQL，整合到 `da-cluster/images/keycloak-init/init-keycloak.py` 的 `seed_iam_db()` 中。

## 11 待确认问题

1. **knowledge 删除权限**：默认 `DELETE /knowledge/{item}` → owner，是否允许 contributor 删除本人创建的知识项？如允许，需加入 `resource_actions` override。
2. **`/api/sessions/replay`（body-based session_id）**：建议业务侧统一为 path 版本 `/api/sessions/{id}/replay`。
3. **`GET /api/config/paths/logs`**：是否限制 admins 可见？日志路径暴露系统信息，倾向于升级为 rubik-admins。
4. **`POST /api/databases`**：是否需要限制只有 `rubik-users` 组（预置）可创建数据库？当前设计所有登录用户均可，若要限制需加 path_rule。
5. **`execute-sql`**：敏感程度是否需要升级为 owner？当前按 contributor 设计。
