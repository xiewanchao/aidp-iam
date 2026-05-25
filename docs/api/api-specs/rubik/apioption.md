数据库管理
option1：数据库导入与删除
POST /api/databases 导入新数据库
DELETE /api/databases/{db_id} 删除数据库
option2: 查看数据库
GET /api/databases 列出所有已导入的数据库
GET /api/databases/config/data-dir 获取当前数据目录路径
GET /api/databases/{db_id} 获取数据库详情
GET /api/databases/{db_id}/check 检查数据库状态
GET /api/databases/{db_id}/schema 获取数据库树形结构
GET /api/databases/{db_id}/tables-columns 获取所有表及其列
GET /api/databases/{db_id}/tables/{table_name} 获取表数据（分页、排序）
POST /api/databases/{db_id}/prettify-sql 美化 SQL 格式
POST /api/databases/{db_id}/execute-sql 执行原生 SQL
POST /api/databases/test

知识库构建
option3：知识库构建
POST /api/databases/{db_id}/build 构建知识库（同步）
GET /api/databases/{db_id}/build/status 获取构建状态
POST /api/databases/{db_id}/build/stream 构建知识库（SSE 流式）
POST /api/databases/{db_id}/build/cancel 取消构建

知识管理
option4：知识增删
PUT /api/databases/{db_id}/knowledge/{type_}/{item_id_str} 更新知识项
DELETE /api/databases/{db_id}/knowledge/{type_}/{item_id_str} 删除知识项
POST /api/databases/{db_id}/knowledge/taxonomy 添加分类知识
POST /api/databases/{db_id}/knowledge/custom 添加自定义知识
POST /api/databases/knowledge/special 添加特殊知识
POST /api/databases/{db_id}/knowledge/experience 添加 NL2SQL 经验
POST /api/databases/{db_id}/knowledge/import 导入知识（JSON）
option5：知识查看
GET /api/databases/{db_id}/knowledge/types 获取知识类型统计
GET /api/databases/{db_id}/knowledge/list 分页获取知识列表（多类型）
GET /api/databases/{db_id}/knowledge/{type_} 获取指定类型的知识
GET /api/databases/{db_id}/knowledge/{type_}/{item_id_str} 获取单个知识项
GET /api/databases/{db_id}/knowledge/{type_}/{item_id_str}/dict 获取知识项原始字典
POST /api/databases/{db_id}/sync 同步所有失同步的知识引擎
POST /api/databases/{db_id}/sync/stream 同步知识（流式）
POST /api/databases/{db_id}/knowledge/export/stream 批量导出知识（SSE + ZIP）

技能管理
option6：技能管理
POST /api/databases/{db_id}/skill/custom 添加技能
PUT /api/databases/{db_id}/skill/{item_id_str} 更新技能

数据库元数据管理
option7：数据库元数据管理
GET /api/metadata/{db_id}/init 初始化数据库元数据
GET /api/metadata/{db_id} 获取数据库元数据
PUT /api/metadata/{db_id}/description 更新数据库对象描述

问答
option8：问数
POST /api/query 自然语言转 SQL（流式 SSE）
GET /api/sessions 列出会话（按时间分组）
POST /api/sessions 创建新会话
DELETE /api/sessions/{session_id} 删除会话
POST /api/sessions/replay 分页回放会话事件
GET /api/sessions/{session_id}/replay 流式回放会话（SSE）
GET /api/sessions/{session_id}/turns 列出会话轮次
POST /api/sessions/{session_id}/turns/{turn_id}/feedback 设置轮次反馈

配置管理
option9：配置管理
GET /api/config 获取所有配置
GET /api/config/models 获取所有模型预设
PUT /api/config/models/{preset_name} 更新模型预设
GET /api/config/database-providers 获取数据库提供者默认值
PUT /api/config/database-providers/{provider} 更新数据库提供者默认值
GET /api/config/language 获取语言设置
PUT /api/config/language 设置语言（app + query 相同）
PUT /api/config/languages 分别设置 app 和 query 语言
GET /api/config/app 获取应用配置
PUT /api/config/app/{key} 设置应用配置项
POST /api/config/reload 重载配置
POST /api/config/setup 初始化/重置配置
GET /api/config/paths/logs 获取日志文件信息
POST /api/config/open-path 在文件管理器中打开路径
GET /api/config/llm-providers 获取所有 LLM 提供
PUT /api/config/llm-providers/{provider_name} 更新 LLM 提供者配置
POST /api/config/llm-providers 创建新的 LLM 提供者
DELETE /api/config/llm-providers/{provider_name} 删除 LLM 提供者

仪表盘
option10：查看仪表盘
GET /api/dashboards 获取仪表板列表
GET /api/dashboards/:dashboard_id 获取单个仪表板
GET /api/dashboards/:chart_id 获取图表
GET /api/dashboards/:id/report 获取仪表板报告

option11：增删仪表盘
POST /api/dashboards 创建仪表板
PUT /api/dashboards/:dashboard_id 更新仪表板
DELETE /api/dashboards/:dashboard_id 删除仪表板

default:
GET, /api/refresh/{db_id}/status
POST,/api/refresh/{db_id}/execute/stream