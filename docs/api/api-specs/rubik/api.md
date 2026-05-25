1. 数据库管理
GET /api/databases
说明: 列出所有已导入的数据库

响应:

[
    {
        "id": str,                       # 唯一数据库标识符
        "name": str,                     # 显示名称
        "type": str,             # 数据库类型
        "path": str | None,              # 文件数据库路径
        "host": str | None,              # 主机地址
        "port": int | None,              # 端口号
        "user": str | None,              # 用户名
        "password": str | None,           # 密码
        "database": str | None,          # 数据库名
        "kb_path": str | None,           # 知识库路径
        "created_at": str,                # 创建时间 (ISO 时间戳)
        "created_by": str | None,         # 创建者用户 ID
        "auths": List[str] | None,       # 权限列表
        "kb_built": bool,                # 知识库是否已构建
    }
]
​
POST /api/databases
说明: 导入新数据库


# 请求体:
{
    "name": str,               # 数据库显示名称 (必填)
    "type": str,               # 数据库类型，可选值: "sqlite" | "postgresql" | "mysql" | "duckdb" 
    "path": str | None,        # 文件路径（文件数据库）
    "host": str | None,       # 主机地址（网络数据库）
    "port": int | None,       # 端口号
    "user": str | None,       # 用户名
    "password": str | None,   # 密码
    "database": str | None,    # 数据库名
}
​
响应:

{
    "id": str,                       # 唯一数据库标识符
    "name": str,                     # 显示名称
    "type": str,
    "path": str | None,              # 文件数据库路径
    "host": str | None,              # 主机地址
    "port": int | None,              # 端口号
    "user": str | None,              # 用户名
    "password": str | None,           # 密码
    "database": str | None,          # 数据库名
    "kb_path": str | None,           # 知识库路径
    "created_at": str,                # 创建时间 (ISO 时间戳)
    "created_by": str | None,         # 创建者用户 ID
    "auths": List[str] | None,       # 权限列表
    "kb_built": bool,                # 知识库是否已构建
}
​
GET /api/databases/{db_id}
说明: 获取数据库详情

# 路径参数:
db_id: str  # 数据库 ID
​
响应:

{
    "id": str,                       # 唯一数据库标识符
    "name": str,                     # 显示名称
    "type": str,
    "path": str | None,              # 文件数据库路径
    "host": str | None,              # 主机地址
    "port": int | None,              # 端口号
    "user": str | None,              # 用户名
    "password": str | None,           # 密码
    "database": str | None,          # 数据库名
    "kb_path": str | None,           # 知识库路径
    "created_at": str,                # 创建时间 (ISO 时间戳)
    "created_by": str | None,         # 创建者用户 ID
    "auths": List[str] | None,       # 权限列表
    "kb_built": bool,                # 知识库是否已构建
}
​
DELETE /api/databases/{db_id}
说明: 删除数据库

# 路径参数:
db_id: str  # 数据库 ID
​
响应:

{"status": str, "id": str}
​
GET /api/databases/{db_id}/check
说明: 检查数据库状态

# 路径参数:
db_id: str  # 数据库 ID
​
响应:

{"exists": bool, "connected": bool, "table_count": int}
​
GET /api/databases/{db_id}/schema
说明: 获取数据库树形结构

# 路径参数:
db_id: str  # 数据库 ID
​
响应:

[
    {
        "name": str,                              # 节点名称
        "type": "schema" | "table" | "column",   # 节点类型
        "children": List[Dict] | None,           # 子节点列表
        "dataType": str | None,                  # 数据类型（仅 column 有）
    }
]
​
GET /api/databases/{db_id}/tables-columns
说明: 获取所有表及其列

# 路径参数:
db_id: str  # 数据库 ID
​
响应:

[
    {
        "table": str,            # 表名
        "columns": List[str],    # 列名列表
    }
]
​
GET /api/databases/{db_id}/tables/{table_name}
说明: 获取表数据（分页、排序）

# 路径参数:
db_id: str       # 数据库 ID
table_name: str  # 表名

# 查询参数:
limit: int = 100      # 每页行数
offset: int = 0       # 起始偏移
sort_by: str | None   # 排序列名
sort_order: str = "asc"  # asc | desc
​
响应:

{
    "columns": List[str],                              # 列名
    "rows": List[Dict[str, Any]],                     # 数据行
    "total": int,                                     # 总行数
}
​
POST /api/databases/{db_id}/prettify-sql
说明: 美化 SQL 格式


# 路径参数:
db_id: str  # 数据库 ID

# 请求体:
{
    "sql": str,  # SQL 语句 (必填)
}
​
响应:

{
    "success": bool,
    "sql": str,
}
​
POST /api/databases/{db_id}/execute-sql
说明: 执行原生 SQL


# 路径参数:
db_id: str  # 数据库 ID

# 请求体:
{
    "sql": str,           # SQL 查询语句 (必填)
    "limit": int = 100,  # 结果行数限制
}
​
响应:

{
    "columns": List[str],
    "rows": List[Dict[str, Any]],
    "total": int,
}
​
GET /api/databases/config/data-dir
说明: 获取当前数据目录路径

响应:

{"data_dir": str}
​
2. 知识库构建
POST /api/databases/{db_id}/build
说明: 构建知识库（同步）

# 路径参数:
db_id: str  # 数据库 ID

# 查询参数:
force: bool = False  # 是否强制重建
​
响应:

{
    "database_id": str,
    "status": str,                      # "idle" | "building" | "syncing" | "completed" | "failed" | "cancelled"
    "progress": float | None,
    "message": str | None,
    "step": str | None,
    "step_progress": float | None,
    "elapsed": float | None,
    "estimated": float | None,
}
​
GET /api/databases/{db_id}/build/status
说明: 获取构建状态

# 路径参数:
db_id: str  # 数据库 ID
​
响应:

{
    "database_id": str,
    "status": str,
    "progress": float | None,
    "message": str | None,
    "step": str | None,
    "step_progress": float | None,
    "elapsed": float | None,
    "estimated": float | None,
}
​
POST /api/databases/{db_id}/build/stream
说明: 构建知识库（SSE 流式）

# 路径参数:
db_id: str  # 数据库 ID

# 查询参数:
force: bool = False                    # 是否强制重建
stages: List[str] | None = None       # 指定构建阶段
​
响应: SSE 流，返回 BuildStatus JSON

POST /api/databases/{db_id}/build/cancel
说明: 取消构建

# 路径参数:
db_id: str  # 数据库 ID
​
响应:

{"success": bool}
​
3. 知识管理
GET /api/databases/{db_id}/knowledge/types
说明: 获取知识类型统计

# 路径参数:
db_id: str  # 数据库 ID
​
响应:

[
    {
        "type": str,   # 知识类型
        "count": int,  # 数量
    }
]
​
GET /api/databases/{db_id}/knowledge/list
说明: 分页获取知识列表（多类型）

# 路径参数:
db_id: str  # 数据库 ID

# 查询参数:
types: str | None = None   # 逗号分隔的类型
page: int = 1              # 页码
limit: int = 50            # 每页数量
search: str | None = None # 搜索关键词
​
响应:

{
    "items": List[Dict],    # 知识项列表
    "total": int,           # 总数
    "page": int,            # 当前页
    "limit": int,           # 每页数量
}
​
GET /api/databases/{db_id}/knowledge/{type_}
说明: 获取指定类型的知识

# 路径参数:
db_id: str   # 数据库 ID
type_: str   # 知识类型

# 查询参数:
page: int = 1
limit: int = 50
search: str | None = None
​
响应:

{
    "items": List[Dict],    # 知识项列表
    "total": int,           # 总数
    "page": int,            # 当前页
    "limit": int,           # 每页数量
}
​
GET /api/databases/{db_id}/knowledge/{type_}/{item_id_str}
说明: 获取单个知识项

# 路径参数:
db_id: str       # 数据库 ID
type_: str       # 知识类型
item_id_str: str # 知识项 ID
​
响应:

{
    "id_str": str,                              # 知识项 ID
    "type": str,                                # 知识类型
    "name": str,                                # 名称
    "short_description": str | None,           # 简短描述
    "description": str | None,                  # 完整描述
    "skill_body": str | None,                   # 技能内容
    "synonyms": List[str] | None,               # 同义词列表
    "tags": List[str] | None,                   # 标签列表
    "content_resources": Dict[str, Any] | None, # 内容资源
    "source": str,                              # 来源
    "creator": str,                             # 创建者
    "owner": str,                               # 所有者
    "workspace": str,                           # 工作空间
    "processing_status": str,                    # 处理状态
    "metadata": Dict[str, Any] | None,          # 元数据
    "created_at": str | None,                   # 创建时间
    "updated_at": str | None,                   # 更新时间
}
​
GET /api/databases/{db_id}/knowledge/{type_}/{item_id_str}/dict
说明: 获取知识项原始字典

# 路径参数:
db_id: str       # 数据库 ID
type_: str       # 知识类型
item_id_str: str # 知识项 ID
​
响应:

Dict[str, Any]
​
PUT /api/databases/{db_id}/knowledge/{type_}/{item_id_str}
说明: 更新知识项


# 路径参数:
db_id: str       # 数据库 ID
type_: str       # 知识类型
item_id_str: str # 知识项 ID

# 请求体:
{
    "short_description": str | None,             # 简短描述
    "description": str | None,                  # 完整描述
    "synonyms": List[str] | None,                # 同义词列表
    "tags": List[str] | None,                    # 标签列表
    "content_resources": Dict[str, Any] | None, # 内容资源
}
​
响应:

{
    "id_str": str,                              # 知识项 ID
    "type": str,                                # 知识类型
    "name": str,                                # 名称
    "short_description": str | None,           # 简短描述
    "description": str | None,                  # 完整描述
    "skill_body": str | None,                   # 技能内容
    "synonyms": List[str] | None,               # 同义词列表
    "tags": List[str] | None,                   # 标签列表
    "content_resources": Dict[str, Any] | None, # 内容资源
    "source": str,                              # 来源
    "creator": str,                             # 创建者
    "owner": str,                               # 所有者
    "workspace": str,                           # 工作空间
    "processing_status": str,                    # 处理状态
    "metadata": Dict[str, Any] | None,          # 元数据
    "created_at": str | None,                   # 创建时间
    "updated_at": str | None,                   # 更新时间
}
​
DELETE /api/databases/{db_id}/knowledge/{type_}/{item_id_str}
说明: 删除知识项

# 路径参数:
db_id: str       # 数据库 ID
type_: str       # 知识类型
item_id_str: str # 知识项 ID
​
响应:

{"success": True}
​
POST /api/databases/{db_id}/knowledge/taxonomy
说明: 添加分类知识


# 路径参数:
db_id: str  # 数据库 ID

# 请求体:
{
    "name": str,                           # 分类名称 (必填)
    "table": str,                          # 表名 (必填)
    "columns": List[str],                   # 列名列表 (必填)
    "short_description": str | None,       # 简短描述
    "description": str | None,              # 完整描述
}
​
响应:

{
    "id_str": str,                              # 知识项 ID
    "type": str,                                # 知识类型
    "name": str,                                # 名称
    "short_description": str | None,           # 简短描述
    "description": str | None,                  # 完整描述
    "skill_body": str | None,                   # 技能内容
    "synonyms": List[str] | None,               # 同义词列表
    "tags": List[str] | None,                   # 标签列表
    "content_resources": Dict[str, Any] | None, # 内容资源
    "source": str,                              # 来源
    "creator": str,                             # 创建者
    "owner": str,                               # 所有者
    "workspace": str,                           # 工作空间
    "processing_status": str,                    # 处理状态
    "metadata": Dict[str, Any] | None,          # 元数据
    "created_at": str | None,                   # 创建时间
    "updated_at": str | None,                   # 更新时间
}
​
POST /api/databases/{db_id}/knowledge/custom
说明: 添加自定义知识


# 路径参数:
db_id: str  # 数据库 ID

# 请求体:
{
    "name": str,                           # 知识名称 (必填)
    "content": str,                        # 知识内容 (必填)
    "synonyms": List[str] | None,          # 同义词列表，默认 []
    "short_description": str | None,       # 简短描述
    "description": str | None,              # 完整描述
}
​
响应:

{
    "id_str": str,                              # 知识项 ID
    "type": str,                                # 知识类型
    "name": str,                                # 名称
    "short_description": str | None,           # 简短描述
    "description": str | None,                  # 完整描述
    "skill_body": str | None,                   # 技能内容
    "synonyms": List[str] | None,               # 同义词列表
    "tags": List[str] | None,                   # 标签列表
    "content_resources": Dict[str, Any] | None, # 内容资源
    "source": str,                              # 来源
    "creator": str,                             # 创建者
    "owner": str,                               # 所有者
    "workspace": str,                           # 工作空间
    "processing_status": str,                    # 处理状态
    "metadata": Dict[str, Any] | None,          # 元数据
    "created_at": str | None,                   # 创建时间
    "updated_at": str | None,                   # 更新时间
}
​
POST /api/databases/knowledge/special
说明: 添加特殊知识


# 请求体:
{
    "name": str,                                        # 知识名称 (必填)
    "content": str,                                     # 知识内容 (必填)
    "synonyms": List[str] | None,                      # 同义词列表，默认 []
    "short_description": str | None,                    # 简短描述
    "description": str | None,                          # 完整描述
    "db_id": str | None,                                # 数据库 ID
    "workspace": str,                                    # 工作空间，可选值: "user" | "db" | "global"，默认 "user"
}
​
响应:

{
    "id_str": str,                              # 知识项 ID
    "type": str,                                # 知识类型
    "name": str,                                # 名称
    "short_description": str | None,           # 简短描述
    "description": str | None,                  # 完整描述
    "skill_body": str | None,                   # 技能内容
    "synonyms": List[str] | None,               # 同义词列表
    "tags": List[str] | None,                   # 标签列表
    "content_resources": Dict[str, Any] | None, # 内容资源
    "source": str,                              # 来源
    "creator": str,                             # 创建者
    "owner": str,                               # 所有者
    "workspace": str,                           # 工作空间
    "processing_status": str,                    # 处理状态
    "metadata": Dict[str, Any] | None,          # 元数据
    "created_at": str | None,                   # 创建时间
    "updated_at": str | None,                   # 更新时间
}
​
POST /api/databases/{db_id}/knowledge/experience
说明: 添加 NL2SQL 经验


# 路径参数:
db_id: str  # 数据库 ID

# 请求体:
{
    "question": str,                          # 自然语言问题 (必填)
    "sql": str,                               # SQL 查询语句 (必填)
    "context": Dict[str, Any] | None,         # 查询上下文
    "hints": List[str] | None,               # 提示词列表，默认 []
}
​
响应:

{
    "id_str": str,                              # 知识项 ID
    "type": str,                                # 知识类型
    "name": str,                                # 名称
    "short_description": str | None,           # 简短描述
    "description": str | None,                  # 完整描述
    "skill_body": str | None,                   # 技能内容
    "synonyms": List[str] | None,               # 同义词列表
    "tags": List[str] | None,                   # 标签列表
    "content_resources": Dict[str, Any] | None, # 内容资源
    "source": str,                              # 来源
    "creator": str,                             # 创建者
    "owner": str,                               # 所有者
    "workspace": str,                           # 工作空间
    "processing_status": str,                    # 处理状态
    "metadata": Dict[str, Any] | None,          # 元数据
    "created_at": str | None,                   # 创建时间
    "updated_at": str | None,                   # 更新时间
}
​
POST /api/databases/{db_id}/knowledge/import
说明: 导入知识（JSON）


# 路径参数:
db_id: str  # 数据库 ID

# 请求体:
Dict[str, Any]  # 知识数据字典
​
响应:

{
    "id_str": str,                              # 知识项 ID
    "type": str,                                # 知识类型
    "name": str,                                # 名称
    "short_description": str | None,           # 简短描述
    "description": str | None,                  # 完整描述
    "skill_body": str | None,                   # 技能内容
    "synonyms": List[str] | None,               # 同义词列表
    "tags": List[str] | None,                   # 标签列表
    "content_resources": Dict[str, Any] | None, # 内容资源
    "source": str,                              # 来源
    "creator": str,                             # 创建者
    "owner": str,                               # 所有者
    "workspace": str,                           # 工作空间
    "processing_status": str,                    # 处理状态
    "metadata": Dict[str, Any] | None,          # 元数据
    "created_at": str | None,                   # 创建时间
    "updated_at": str | None,                   # 更新时间
}
​
POST /api/databases/{db_id}/knowledge/export/stream
说明: 批量导出知识（SSE + ZIP）


# 路径参数:
db_id: str  # 数据库 ID

# 请求体:
{
    "types": List[str],  # 要导出的知识类型列表 (必填)
}
​
响应: SSE 流 + ZIP 二进制数据

4. 技能管理
POST /api/databases/{db_id}/skill/custom
说明: 添加技能


# 路径参数:
db_id: str  # 数据库 ID

# 请求体:
{
    "name": str,        # 技能名称 (必填)
    "description": str, # 技能描述 (必填)
    "content": str,     # 技能内容 (必填)
}
​
响应:

{
    "id_str": str,                              # 知识项 ID
    "type": str,                                # 知识类型
    "name": str,                                # 名称
    "short_description": str | None,           # 简短描述
    "description": str | None,                  # 完整描述
    "skill_body": str | None,                   # 技能内容
    "synonyms": List[str] | None,               # 同义词列表
    "tags": List[str] | None,                   # 标签列表
    "content_resources": Dict[str, Any] | None, # 内容资源
    "source": str,                              # 来源
    "creator": str,                             # 创建者
    "owner": str,                               # 所有者
    "workspace": str,                           # 工作空间
    "processing_status": str,                    # 处理状态
    "metadata": Dict[str, Any] | None,          # 元数据
    "created_at": str | None,                   # 创建时间
    "updated_at": str | None,                   # 更新时间
}
​
PUT /api/databases/{db_id}/skill/{item_id_str}
说明: 更新技能


# 路径参数:
db_id: str       # 数据库 ID
item_id_str: str # 技能项 ID

# 请求体:
{
    "name": str,        # 技能名称 (必填)
    "description": str, # 技能描述 (必填)
    "content": str,     # 技能内容 (必填)
}
​
响应:

{
    "id_str": str,                              # 知识项 ID
    "type": str,                                # 知识类型
    "name": str,                                # 名称
    "short_description": str | None,           # 简短描述
    "description": str | None,                  # 完整描述
    "skill_body": str | None,                   # 技能内容
    "synonyms": List[str] | None,               # 同义词列表
    "tags": List[str] | None,                   # 标签列表
    "content_resources": Dict[str, Any] | None, # 内容资源
    "source": str,                              # 来源
    "creator": str,                             # 创建者
    "owner": str,                               # 所有者
    "workspace": str,                           # 工作空间
    "processing_status": str,                    # 处理状态
    "metadata": Dict[str, Any] | None,          # 元数据
    "created_at": str | None,                   # 创建时间
    "updated_at": str | None,                   # 更新时间
}
​
5. 知识同步
POST /api/databases/{db_id}/sync
说明: 同步所有失同步的知识引擎

# 路径参数:
db_id: str  # 数据库 ID
​
响应:

{
    "success": bool,
    "message": str,
    "result": Dict[str, Any],
}
​
POST /api/databases/{db_id}/sync/stream
说明: 同步知识（流式）

# 路径参数:
db_id: str  # 数据库 ID
​
响应: SSE 流

6. 元数据管理
所有元数据接口的前缀为 /api/metadata

GET /api/metadata/{db_id}/init
说明: 初始化数据库元数据

# 路径参数:
db_id: str  # 数据库 ID

# 查询参数:
reset: bool = False  # 是否重置
​
响应:

{
    "database_id": str,
    "tables": int,
    "columns": int,
    "initialized": bool,
}
​
GET /api/metadata/{db_id}
说明: 获取数据库元数据

# 路径参数:
db_id: str  # 数据库 ID
​
响应: 元数据字典

PUT /api/metadata/{db_id}/description
说明: 更新数据库对象描述


# 路径参数:
db_id: str  # 数据库 ID

# 请求体:
{
    "table": str | None,        # 表名
    "column": str | None,       # 列名
    "description": str | None,   # 描述
}
​
响应:

{"status": "updated"}
​
7. 查询
所有查询接口的前缀为 /api/query

POST /api/query
说明: 自然语言转 SQL（流式 SSE）


# 请求体:
{
    "session_id": str,                                    # 会话 ID (必填)
    "database_id": str,                                        # 数据库 ID (必填)
    "query": str,                                          # 自然语言问题 (必填)
    "hints": List[str],                                       # 提示词列表，默认 []
    "mode": "flash" | "auto" | "heavy" | "beta",             # 模式，默认 "auto"
    "agent_type": "rubik" | "analysis",                       # Agent 类型，默认 "rubik"
}
​
响应: SSE 流

# QueryEvent
{
    "event": str,                      # 事件类型
    "data": Dict[str, Any],            # 事件数据
    "hidden": bool,                     # 是否隐藏
    "metadata": Dict[str, Any] | None, # 元数据
}
​
8. 会话管理
所有会话接口的前缀为 /api/sessions

GET /api/sessions
说明: 列出会话（按时间分组）

# 查询参数:
database_id: str | None = None  # 按数据库筛选
​
响应:

# Dict[str, List[Dict]]
{
    "today": [
        {
            "session_id": str,           # 会话 ID
            "database_id": str,          # 数据库 ID
            "title": str,               # 会话标题
            "dialect": str | None,      # SQL 方言
            "created_at": str | None,   # 创建时间
            "updated_at": str | None,   # 更新时间
            "preview": str | None,      # 预览文本
            "message_count": int,        # 消息数量
        }
    ]
}
​
POST /api/sessions
说明: 创建新会话


# 请求体:
{
    "database_id": str,           # 数据库 ID (必填)
    "title": str | None,          # 会话标题
}
​
响应:

{
    "session_id": str,           # 会话 ID
    "database_id": str,          # 数据库 ID
    "title": str,               # 会话标题
    "dialect": str | None,      # SQL 方言
    "created_at": str | None,   # 创建时间
    "updated_at": str | None,   # 更新时间
    "preview": str | None,      # 预览文本
    "message_count": int,        # 消息数量
}
​
DELETE /api/sessions/{session_id}
说明: 删除会话

# 路径参数:
session_id: str  # 会话 ID
​
响应: 204 No Content

POST /api/sessions/replay
说明: 分页回放会话事件


# 请求体:
{
    "session_id": str,             # 会话 ID (必填)
    "offset": int | None,         # 从哪个 turn_idx 往前翻，None 表示从最新
    "turn_count": int,             # 返回的轮次数 (必填)
}
​
响应:

{
    "next_offset": int | None,      # 下一次请求的 offset，None 表示结束
    "has_more": bool,               # 是否还有更早的数据
    "events": List[Dict],          # 事件列表
}
​
GET /api/sessions/{session_id}/replay
说明: 流式回放会话（SSE）

# 路径参数:
session_id: str  # 会话 ID
​
响应: SSE 流

GET /api/sessions/{session_id}/turns
说明: 列出会话轮次

# 路径参数:
session_id: str  # 会话 ID
​
响应:

[
    {
        "turn_idx": int,
        "question": str,
    }
]
​
POST /api/sessions/{session_id}/turns/{turn_id}/feedback
说明: 设置轮次反馈


# 路径参数:
session_id: str  # 会话 ID
turn_id: str     # 轮次 ID（内部转换为整数）

# 请求体:
{
    "target_type": str,            # 目标类型，可选值: "report" | "question" (必填)
    "feedback_type": str,           # 反馈类型，可选值: "like" | "dislike" (必填)
    "comment": str | None,          # 纠正建议（dislike 时可填）
}
​
响应:

{
    "turn_info": {
        "turn_idx": int,                 # 轮次索引
        "user_query": {
            "question": str,             # 用户问题
            "context": Dict[str, Any] | None,  # 上下文
            "schema_": Any | None,       # 数据库结构
            "hints": List[str] | None,   # 提示词
            "user_id": str | None,       # 用户 ID
        },
        "total_elapsed_seconds": float,   # 总耗时（秒）
        "feedback": {
            "type": str | None,         # 反馈类型 "like" | "dislike"
            "timestamp": str | None,     # 反馈时间
            "comment": str | None,       # 纠正建议
        } | None,
        "status": "pending" | "streaming" | "completed" | "error",  # 状态
        "error": str | None,             # 错误信息
        "created_at": str | None,         # 创建时间
        "completed_at": str | None,       # 完成时间
        "mode": "flash" | "auto" | "heavy" | "beta",  # 模式
    },
    "agent_runs": List[Dict],            # Agent 运行记录
}
​
9. 配置管理
所有配置接口的前缀为 /api/config

GET /api/config
说明: 获取所有配置

响应:

Dict[str, Any]
​
GET /api/config/models
说明: 获取所有模型预设

响应:

Dict[str, Any]
​
PUT /api/config/models/{preset_name}
说明: 更新模型预设


# 路径参数:
preset_name: str  # 预设名称

# 请求体:
{
    "provider": str | None,  # 提供者名称
    "model": str,            # 模型名称 (必填)
}
​
响应:

Dict[str, Any]
​
GET /api/config/database-providers
说明: 获取数据库提供者默认值

响应:

Dict[str, Any]
​
PUT /api/config/database-providers/{provider}
说明: 更新数据库提供者默认值


# 路径参数:
provider: str  # 提供者名称

# 请求体:
{
    "host": str | None,         # 主机地址
    "port": int | None,         # 端口号
    "user": str | None,         # 用户名
    "password": str | None,      # 密码
}
​
响应:

Dict[str, Any]
​
GET /api/config/language
说明: 获取语言设置

响应:

{"app_lang": str, "query_lang": str}
​
PUT /api/config/language
说明: 设置语言（app + query 相同）


# 请求体:
{
    "language": str,  # 语言，可选值: "en" | "zh" (必填)
}
​
响应:

{"app_lang": str, "query_lang": str}
​
PUT /api/config/languages
说明: 分别设置 app 和 query 语言


# 请求体:
{
    "app_lang": str | None,   # 应用语言，可选值: "en" | "zh"
    "query_lang": str | None, # 查询语言，可选值: "en" | "zh"
}
​
响应:

{"app_lang": str, "query_lang": str}
​
GET /api/config/app
说明: 获取应用配置

响应:

Dict[str, Any]
​
PUT /api/config/app/{key}
说明: 设置应用配置项


# 路径参数:
key: str  # 配置键

# 请求体:
{
    "value": Any,  # 配置值 (必填)
}
​
响应:

Dict[str, Any]
​
POST /api/config/reload
说明: 重载配置

响应:

{"status": "reloaded"}
​
POST /api/config/setup
说明: 初始化/重置配置

# 查询参数:
reset: bool = False  # 是否重置
​
响应:

{"status": "setup_completed"}
​
GET /api/config/paths/logs
说明: 获取日志文件信息

响应:

Dict[str, Any]
​
POST /api/config/open-path
说明: 在文件管理器中打开路径


# 请求体:
{
    "path": str,  # 路径 (必填)
}
​
响应:

Dict[str, Any]
​
GET /api/config/llm-providers
说明: 获取所有 LLM 提供者

响应:

Dict[str, Any]
​
PUT /api/config/llm-providers/{provider_name}
说明: 更新 LLM 提供者配置


# 路径参数:
provider_name: str  # 提供者名称

# 请求体:
{
    "updates": Dict[str, Any],  # 更新的键值对，支持点号嵌套 (必填)
}
​
响应:

Dict[str, Any]
​
POST /api/config/llm-providers
说明: 创建新的 LLM 提供者


# 请求体:
{
    "name": str,               # 提供者名称 (必填)
    "backend": str,            # 后端类型 (必填)
    "api_key": str | None,     # API 密钥
    "api_base": str | None,    # 自定义 API 地址
}
​
响应:

Dict[str, Any]
​
DELETE /api/config/llm-providers/{provider_name}
说明: 删除 LLM 提供者

# 路径参数:
provider_name: str  # 提供者名称
​
响应:

{"status": "deleted", "name": str}