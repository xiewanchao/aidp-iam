（1）知识库管理
读权限
GET /kb/knowledge_bases/page 查询分页的知识库列表
GET /kb/knowledge_bases/count 查询知识库总数
GET /kb/knowledge_bases 查询单个知识库
GET /kb/knowledge_bases/mappings 查询指定知识库创建的目录映射列表
GET /kb/knowledge_bases/mappings/count 查询知识库创建的目录映射列表数量
GET /kb/knowledge_bases/files/history 查询目录映射文件
GET /kb/knowledge_bases/files 查询知识库文件
GET /kb/knowledge_bases/files/count 查询知识库中文件数量
GET /kb/knowledge_bases/files/filesystem 查询文件系统
GET /kb/knowledge_bases/jargon_groups 查询指定知识库绑定的术语

编辑权限
POST /kb/knowledge_bases/add 创建知识库
POST /kb/knowledge_bases/modify 修改知识库
POST /kb/knowledge_bases/remove 删除知识库
POST /kb/knowledge_bases/mappings/add 从指定知识库创建目录映射
POST /kb/knowledge_bases/mappings/remove 从指定知识库创建目录映射
POST /kb/knowledge_bases/files/upload 上传文件
POST /kb/knowledge_bases/files/remove 删除文件

（2）模型配置
读权限
GET /kb/models/config 查询模型配置信息

编辑权限
POST /kb/models/config/add 新增大模型配置信息
POST /kb/models/config/modify 修改大模型配置信息
POST /kb/models/config/remove 删除大模型配置信息

（3）提示词管理
读权限
GET /kb/prompts/detail 查询提示词
GET /kb/prompts/options 查询提示词菜单
GET /kb/prompts/page 分页查询提示词

编辑权限
POST /kb/prompts/add 创建提示词
POST /kb/prompts/modify 修改提示词
POST /kb/prompts/remove 删除提示词

（4）术语管理
读权限
GET /kb/jargon_groups 查询术语库
GET /kb/jargon_groups/jargons 查询术语库中的术语
GET /kb/jargons_groups/jargon 查询指定术语库的指定术语
GET /kb/jargon_groups/version 查询指定术语库版本号

编辑权限
POST /kb/jargon_groups/add 创建术语库
POST /kb/jargon_groups/remove 删除术语库
POST /kb/jargons/add 创建术语
POST /kb/jargons/modify 修改术语
POST /kb/jargons/remove 删除术语

（5）问答
读权限
GET /kb/conversations/list 查看历史对话列表
GET /kb/conversations 查询单个对话结果
GET /kb/conversations/images/download 通过图片链接访问图片

编辑权限
POST /kb/conversations/start 提问
POST /kb/conversations/stop 停止回答
POST /kb/conversations/images/generate 图片链接生成
POST /kb/conversations/remove 删除单个对话

（6）检索
检索权限
POST /kb/retrieval/fusion_search 融合检索

（7）知识库和术语库关联
知识库编辑权限和术语库读权限
POST /kb/jargon_groups/knowledge_bases/add 给指定知识库添加术语库
POST /kb/jargon_groups/knowledge_bases/remove 从指定知识库删除术语库