# Clean Code 规范

适用于本项目所有 Python 服务（da-idb-proxy、pep-proxy、bundle-server、resource-sync）。

---

## 一、行与文件

| 指标 | 推荐值 | 硬限制 | 超出时的处理 |
|---|---|---|---|
| 单行字符数 | ≤ 88 | 120 | 拆成多行或提取变量 |
| 函数行数 | ≤ 15 | 50 | 拆分为多个单职责函数 |
| 文件行数 | ≤ 300 | 600 | 按职责拆分模块 |
| 类行数 | ≤ 150 | 300 | 拆分或提取基类 |

**单行过长的典型处理：**

```python
# 不好
result = some_function(argument_one, argument_two, argument_three, keyword_one=value_one, keyword_two=value_two)

# 好
result = some_function(
    argument_one,
    argument_two,
    argument_three,
    keyword_one=value_one,
    keyword_two=value_two,
)
```

---

## 二、圈复杂度

圈复杂度 = 函数中独立执行路径的数量，每个 `if / elif / for / while / except / and / or` +1，基础值为 1。

| 等级 | 范围 | 含义 |
|---|---|---|
| 优 | 1–5 | 简单，易测试 |
| 推荐上限 | ≤ 10 | 可接受，需要完整测试覆盖 |
| 警告 | 11–15 | 必须重构计划，不得新增同类代码 |
| 禁止 | > 15 | 必须立即重构后才能合入 |

**计算示例：**

```python
# 圈复杂度 = 6（基础1 + if + elif + for + if + except）
def process(items):          # +1 基础
    if not items:            # +1
        return []
    result = []
    for item in items:       # +1
        if item.valid:       # +1
            try:
                result.append(transform(item))
            except ValueError: # +1
                pass
    return result
```

---

## 三、控制流语句数量

单个函数内：

| 语句类型 | 推荐数量 | 硬限制 |
|---|---|---|
| `if / elif / else` 分支总数 | ≤ 3 | 5 |
| `for / while` 循环 | ≤ 2 | 3 |
| `try / except` 块 | ≤ 1 | 2 |
| 控制流语句总数（以上合计） | ≤ 5 | 8 |

超出时优先考虑：
- 多个 `if/elif` → 用字典映射或策略模式替代
- 多层循环 → 提取内层为独立函数
- 多个 `try/except` → 统一在调用方处理异常

---

## 四、嵌套层次

**最大嵌套深度：4 层**。超过 4 层必须重构。

```python
# 不好：5 层嵌套
def handle(request):
    if request:                          # 第1层
        for item in request.items:       # 第2层
            if item.valid:               # 第3层
                for sub in item.subs:    # 第4层
                    if sub.active:       # 第5层 ← 超限，必须重构
                        process(sub)

# 好：提取内层逻辑，嵌套不超过3层
def handle(request):
    if not request:
        return
    for item in request.items:
        _process_item(item)

def _process_item(item):
    if not item.valid:
        return
    for sub in item.subs:
        if sub.active:
            process(sub)
```

**提前返回（Early Return）是减少嵌套的首选手段：**

```python
# 不好
def get_user_role(user):
    if user:
        if user.active:
            if user.role:
                return user.role
    return None

# 好
def get_user_role(user):
    if not user:
        return None
    if not user.active:
        return None
    return user.role
```

---

## 五、函数单一职责

一个函数只做一件事。判断标准：**能否用一句不含"和"的话描述这个函数的功能**。

```python
# 不好：一个函数做了三件事
async def create_user_and_notify(realm, req):
    # 1. 创建用户
    user = kc.request("POST", f"/realms/{realm}/users", json=req.dict())
    # 2. 写 ACL
    await _write_user_self_acl(realm, user["id"])
    # 3. 发通知
    await notify_admin(realm, user)
    return user

# 好：每个函数只做一件事，由上层编排
async def create_user(realm, req):
    return kc.request("POST", f"/realms/{realm}/users", json=req.dict()).json()

async def on_user_created(realm, user):
    await _write_user_self_acl(realm, user["id"])
    await notify_admin(realm, user)
```

**常见违反单一职责的信号：**
- 函数名里有"and"、"and_then"、"with"
- 函数有多个独立的注释块（每块注释一件事）
- 函数的参数中有 `notify=True` 这类行为开关

---

## 六、命名规范

### 6.1 通用原则

- 名称要能自解释，不需要注释补充说明
- 布尔变量/函数用 `is_`、`has_`、`can_` 前缀
- 避免缩写，除非是领域内公认的（`acl`、`jwt`、`opa`、`kc` 可以）
- 集合变量用复数：`users`、`group_ids`、`path_rules`

```python
# 不好
def chk(u, r):
    ...

# 好
def check_user_permission(user_id: str, realm: str) -> bool:
    ...
```

### 6.2 函数命名模式

| 场景 | 推荐前缀 | 示例 |
|---|---|---|
| 查询/获取 | `get_` | `get_user_groups` |
| 检查/验证 | `check_` / `is_` / `has_` | `check_acl`, `is_admin` |
| 创建 | `create_` | `create_realm` |
| 写入/更新 | `write_` / `update_` | `_write_user_self_acl` |
| 构建数据结构 | `build_` | `build_rego_bundle` |
| 内部/私有 | `_` 前缀 | `_enrich_user` |

### 6.3 禁止的命名

```python
# 禁止
data, info, result, temp, tmp, obj, val, flag, x, y, n
do_stuff(), handle_it(), process_data(), manage()
```

---

## 七、注释规范

**默认不写注释**。只在以下情况写：

1. **隐藏约束**：外部系统的限制、非显而易见的边界条件
2. **反直觉的实现**：看起来像 bug 但实际上是正确的
3. **已知的 workaround**：绕过某个具体 bug 的代码

```python
# 不需要注释（代码已自解释）
def is_admin(groups: list[str]) -> bool:
    return "tenant-admins" in groups or "master-admins" in groups

# 需要注释（外部系统约束，看代码看不出来）
# Envoy ext_proc 在 Streamed body 模式下不会将 request_headers 阶段的
# header mutation 应用到转发请求，必须通过 ext_authz OkHttpResponse 注入。
response.headers["X-Allowed-Ids"] = allowed_ids
```

**禁止的注释类型：**
- 解释代码在做什么（好的命名已经说明了）
- TODO/FIXME 超过 2 周未处理的（直接建 issue）
- 注释掉的旧代码（用 git 管理历史）
- 函数/类的多行 docstring（一行足够，或者不写）

---

## 八、函数参数

| 指标 | 推荐值 | 硬限制 |
|---|---|---|
| 参数个数 | ≤ 3 | 5 |
| 布尔参数 | 0 | 1 |

超过 3 个参数时，考虑将相关参数封装为 dataclass 或 Pydantic model：

```python
# 不好
def create_user(realm, username, password, email, nickname, groups, temporary):
    ...

# 好
class UserCreateRequest(BaseModel):
    username: str
    password: str
    email: Optional[str] = None
    nickname: Optional[str] = None
    groups: List[str] = []
    temporary_password: bool = False

def create_user(realm: str, req: UserCreateRequest):
    ...
```

**布尔参数是行为开关，意味着函数在做两件事，应该拆分：**

```python
# 不好
def get_users(realm, include_groups=False):
    ...

# 好
def get_users(realm):
    ...

def get_users_with_groups(realm):
    ...
```

---

## 九、错误处理

- 只在**系统边界**（外部 API 调用、用户输入）捕获异常，内部函数让异常自然传播
- 捕获具体异常类型，不用裸 `except:`
- 不吞掉异常（`except: pass` 只在有充分理由时使用，且必须加注释说明原因）

```python
# 不好
try:
    result = do_something()
except:
    pass

# 好
try:
    result = kc.request("GET", f"/realms/{realm}/users/{user_id}")
except httpx.TimeoutException:
    raise HTTPException(status_code=504, detail="Keycloak timeout")
```

---

## 十、重构触发条件

遇到以下任一情况，必须重构后才能继续添加功能：

| 触发条件 | 说明 |
|---|---|
| 圈复杂度 > 15 | 立即重构，不得合入 |
| 函数超过 50 行 | 拆分为多个函数 |
| 嵌套超过 4 层 | 提取内层逻辑或使用 Early Return |
| 参数超过 5 个 | 封装为 model |
| 同一逻辑出现 3 次以上 | 提取为公共函数 |
| 函数名含"and" | 拆分为两个函数 |
| 修改一处需要同时修改多处 | 消除重复，统一来源 |

---

## 十一、本项目的典型反例

以下是项目中实际出现过的违规模式，供参考：

**反例 1：`_enrich_user` 做了太多事**（`da-idb-proxy/app/api/v1/identity.py:287`）

`_enrich_user` 同时处理 account_type、groups、nickname、email、created_at，是典型的多职责函数。在大量用户场景下还引发了 N+1 查询导致超时（all-users 组详情 500 bug）。应拆分为 `_get_user_groups`、`_get_user_attributes` 等独立函数。

**反例 2：`get_user_full_context` 混合了 HTTP 调用和 DB 查询**（`identity.py:408`）

单个接口处理函数直接内嵌了复杂的 SQL 查询逻辑，应提取为 `_query_user_permissions(conn, group_names)` 独立函数。

**反例 3：`BatchImportRequest` 缺少长度约束**（`da-idb-proxy/app/schemas/users.py:131`）

`users: List[UserCreateRequest]` 无上限，导致超大 payload 被 Envoy 的 `maxRequestBytes=8192` 拦截报错。应加 `max_length=100` 约束，在业务层提前拒绝，而不是依赖网关报错。
