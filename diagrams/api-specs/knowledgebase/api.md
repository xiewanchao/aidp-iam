	模块	子模块	功能	备注	对应后端接口(路径/请求方法）	后端接口入参	后端返回参数	后端接口错误码	后端接口责任人	后端支持联调时间点	向前端提供的接口路径	HTTP 方法	说明
				分页查询	/query/kdspage   post	 {"PAGEINDEX": "10",  "PAGESIZE": 10}				3.9/3.28	/kb/knowledge_bases/page	GET	
			查询知识库数量		/query/kdscount post	无参数			赵小明	3.9/3.28	/kb/knowledge_bases/count	GET	返回总数
			单个查询知识库		/query/kds    post	 {"KDSID": str(id)}			赵小明	3.9/3.28	/kb/knowledge_bases	GET	根据 ID 查询
			创建知识库（字段变化）		/create/kds   post	"{""NAME"": ""param_str"",
                    ""DESCRIPTION"": ""test_description"",
                    ""CHUNKTOKENNUM"": 1,
                    ""CHUNKOVERLAPNUM"": 1,
                    ""EMBEDDINGMODEL"": ""default"",
                    }"			赵小明	3.9/3.28	/kb/knowledge_bases/add	POST	包含名称、描述、状态等
			修改知识库		/update/kds post	"{""KDSID"": ""3"",
                    ""NAME"": ""test_name1"",
                    ""DESCRIPTION"": ""test_decription"",
                    ""CHUNKTOKENNUM"": 11,
                    ""CHUNKOVERLAPNUM"": 11,
                    }"			赵小明	3.9/3.28	/kb/knowledge_bases/modify	POST	全量更新
			删除知识库		/delete/kds  post	{"KDSID": str(id) }			赵小明	3.9/3.28	/kb/knowledge_bases/remove	POST	删除知识库
			查询知识库创建的目录映射列表		/query/channelbykds  post	 {"KDSID": "10","PAGEINDEX": "10",  "PAGESIZE": 10}			赵小明	3.9/3.28	/kb/knowledge_bases/mappings	GET	根据知识库 ID 查询
			查询知识库创建的目录映射列表数量		/query/channelcountbykds post	 {"KDSID": "10"}				3.9/3.28	/kb/knowledge_bases/mappings/count	GET	根据 ID 查询
			从知识库创建目录映射	"只支持文本输入目录路径
是否需要选择支持的文件类型"	/create/channel  post	"{
                            ""SRCDIR"": src_name,
                            ""KDSID"": ""0"",
                            ""FSNAME"": fs_name,
                            ""FSID"": fs_id,
                            ""CHANNELNAME"": channel_name
                            }"			赵小明	3.9/3.28	/kb/knowledge_bases/mappings/add	POST	创建映射
			从知识库删除目录映射		/delete/channel  post	{"CHANNELID": str(id) }			赵小明	3.9/3.28	/kb/knowledge_bases/mappings/remove	POST	根据映射ID删除映射
													
		文件系统与知识入库	查询目录映射文件	获取指定目录下面文件					王成	4.15	/kb/knowledge_bases/files/history	GET	
			"查询知识库知识提取结果
（查看目录入库进度）"	"管道id和目录映射一一对应
通过目录映射去管控面拿管道id
通过管道id去kmsdriver查看任务进度"									根据映射ID查询映射进度
			批量查询知识库中的文件	"通过知识库ID从管控面获取目录列表
通过目录从KmsDriver获取文件列表"					"赵小明：知识库->目录
王成：  目录->文件"	4.15	/kb/knowledge_bases/files	GET	支持分页
			查询知识库中的文件数量						"赵小明：知识库->目录
王成：  目录->文件"	4.15	/kb/knowledge_bases/files/count	GET	返回总数
			创建文件系统并挂载	需要找NET开放容器网关8088端口					施豪杰	3.28/4.8	/kb/knowledge_bases/files/filesystem/add	POST	初始化容器时创建
			删除文件系统						施豪杰	3.28/4.9	/kb/knowledge_bases/files/filesystem/remove	POST	容器销毁时删除
			批量查询文件系统	需要切宿主机网络					赵小明	3.28/4.10	/kb/knowledge_bases/files/filesystem	GET	返回文件系统列表
			"文件上传
（支持手动选择文件/手动上传文本）"	"上传到upload_fs/{kbs_id}
通知feeder"					"赵小明：权限校验，文件保存
贾振一：Feeder通知任务id生成
何枳肖：管道id查询"	3.28/4.11	/kb/knowledge_bases/files/upload	POST	支持多文件上传
			文件删除（待对齐）	本地文件：删文件+删除向量数据库的数据，文件同步：只删除向量数据库的数据					王成		/kb/knowledge_bases/files/remove	POST	删除文件
	全局设置（IR2-SR6-AR6)	模型与提示词管理	查询模型配置信息		POST    /ModelAPI	"请求体：
{""ModelAPI"":{
    ""ModelType"": ""LLM"",
    ""ModelName"": ""GPT-4 Turbo"",
    ""ModelID"": ""gpt-4-turbo-previw"",
    ""Provider"": ""Admin"",
    ""APIUrl"": ""https://api.openai.com/v1/chat/completions"",
    ""APIKey"": ""******""
}}
返回值：
STATUS 200
{""ModelAPIID"": ""1""}"			李文轩	3.24	/kb/models/config	GET	获取所有模型配置
			新增大模型配置信息						李文轩	3.24	/kb/models/config/add	POST	新增模型配置
			修改大模型配置信息						李文轩	3.24	/kb/models/config/modify	POST	修改模型配置
			删除大模型配置信息						李文轩	3.24	/kb/models/config/remove	POST	删除模型配置
			配置系统使用模型	仅能配置vllm和问答模型					李文轩	3.24	/kb/models/config/set	GET	选择模型配置
			获取单个prompt组详情（单个查询）						李小庆	4.15	/kb/prompts	GET	支持分页
			创建提示词						李小庆	4.15	/kb/prompts/add	POST	JSON 请求体
			修改提示词						李小庆	4.15	/kb/prompts/modify	POST	全量更新
			删除提示词						李小庆	4.15	/kb/prompts/remove		
			获取菜单精简列表	QA界面					李小庆	4.15	/kb/prompts/menu		
			分页查询prompt组列表（批量查询）						李小庆	4.15	/kb/prompts/page	POST	删除单个提示词
													
	黑话管理（IR2-SR6-AR4)	黑话管理	查询黑话库列表	包含：名称、创建时间、归属知识库	POST /kmsctrl/jargon/list/library	"PARAM_LIST = [
        Argument(name='PAGE', type=str, location='json', required=True),
        Argument(name='SIZE', type=str, location='json', required=True),
    ]"			李小庆/项全	3.26/3.30	/kb/jargon_groups	GET	支持分页
			创建黑话库	支持配置黑话库和知识库绑定关联关系	POST /kmsctrl/jargon/create/library	"PARAM_LIST = [
        Argument(name='JARGON_LIB_NAME', type=str, location='json', required=True),
        Argument(name='DESCRIPTION', type=str, location='json', required=True),
    ]"			李小庆/项全	3.26/3.30	/kb/jargon_groups/add	POST	JSON 请求体
			删除黑话库	删除黑话库对象以及包含的黑话，并取消该黑话库和知识库的关联关系	POST /kmsctrl/jargon/delete/library	"PARAM_LIST = [
        Argument(name='JARGON_LIB_NAME', type=str, location='json', required=True),
    ]"			李小庆/项全	3.26/3.30	/kb/jargon_groups/remove	POST	删除黑话库
			查询黑话库中的黑话信息	包含：名称、说明、目标词条、创建时间等	POST /kmsctrl/jargon/list/entry	"PARAM_LIST = [
        Argument(name='JARGON_LIB_NAME', type=str, location='json', required=True),
        Argument(name='PAGE', type=str, location='json', required=True),
        Argument(name='SIZE', type=str, location='json', required=True),
    ]"			李小庆/项全	3.26/3.30	/kb/jargon_groups/jargons	GET	支持分页
			将黑话库配置到指定知识库	配置成功后，该知识库的检索问答服务可以识别到黑话	POST /kmsctrl/jargon/bind/knowledge-base	"PARAM_LIST = [
        Argument(name='KB_NAME', type=str, location='json', required=True),
        Argument(name='JARGON_LIB_NAME', type=str, location='json', required=True),
    ]"			李小庆/项全	3.26/3.30	/kb/jargon_groups/knowledge_bases/add	POST	
			从指定知识库移除黑话库	取消后，该知识库的检索问答服务不识别黑话	POST /kmsctrl/jargon/unbind/knowledge-base	"PARAM_LIST = [
        Argument(name='KB_NAME', type=str, location='json', required=True),
        Argument(name='JARGON_LIB_NAME', type=str, location='json', required=True),
    ]"			李小庆/项全	3.26/3.30	/kb/jargon_groups/knowledge_bases/remove	POST	
			查询指定黑话库里的指定黑话	入参：黑话库的名称、黑话的名称	POST /kmsctrl/jargon/query/entry	"PARAM_LIST = [
        Argument(name='JARGON_LIB_NAME', type=str, location='json', required=True),
        Argument(name='JARGON_NAME', type=list, location='json', required=True),
    ]"			李小庆/项全	3.26/3.30	/kb/jargons_groups/{jargon_lib_name}	GET	根据 ID 查询
			创建黑话	包含：名称、说明、目标词条、归属黑话库	POST /kmsctrl/jargon/create/entry	"PARAM_LIST = [
        Argument(name='JARGON_NAME', type=str, location='json', required=True),
        Argument(name='DESCRIPTION', type=str, location='json', required=True),
        Argument(name='TARGET_TERM', type=list[str], location='json', required=True),
        Argument(name='JARGON_LIB_NAME', type=str, location='json', required=True),
    ]"			李小庆/项全	3.26/3.30	/kb/jargons/add	POST	JSON 请求体
			修改黑话	修改解释、同义词	POST /kmsctrl/jargon/update/entry	"PARAM_LIST = [
        Argument(name='JARGON_LIB_NAME', type=str, location='json', required=True),
        Argument(name='JARGON_NAME', type=str, location='json', required=True),
        Argument(name='DESCRIPTION', type=str, location='json', required=True),
    ]"			李小庆/项全	3.26/3.30	/kb/jargons/modify	POST	更新
			删除黑话	黑话库中不再包含该黑话	POST /kmsctrl/jargon/delete/entry	"PARAM_LIST = [
        Argument(name='JARGON_LIB_NAME', type=str, location='json', required=True),
        Argument(name='JARGON_NAME', type=str, location='json', required=True),
    ]"			李小庆/项全	3.26/3.30	/kb/jargons/remove	POST	删除
			查询指定知识库绑定的黑话库名称（保留）		POST /kmsctrl/Knowledge_base/query/jargon-library	"PARAM_LIST = [
        Argument(name='KB_NAME', type=str, location='json', required=True),
    ]"			李小庆/项全	3.26/3.30	/kb/knowledge_bases/{kb_name}/jargon	GET	根据 ID 查询
			查询指定黑话库的版本号		POST /kmsctrl/jargon/query/libversion	"PARAM_LIST = [
        Argument(name='JARGON_LIB_NAME', type=str, location='json', required=True),
    ]"			李小庆/项全	3.26/3.30	/kb/jargon_groups/version/{jargon_lib_name}	GET	根据 ID 查询
	问答服务（IR2-SR6-AR8)	chat	提问（问答结果）	"需要向后端接口提供
1、模型配置
2、提示词配置
3、知识库配置（包含黑话库配置）
4、QA模式"					钱行锴、陈浩翔	3.23	/kb/conversations/start	POST	发起问答请求
			停止回答						施豪杰、胡静	3.23	/kb/conversations/stop	GET	停止问答请求
		图片访问	生成图片链接		POST  /kb/conversations/images/generate	"{
    ""kbs_id"": str,
    ""image_url"": str
}"			施豪杰、胡静	4.15/4.10	/kb/conversations/images/generate	POST	接受图片存储路径和知识库信息，转为图片访问链接
			通过图片链接访问图片	与OCR对齐图片访问方式					施豪杰、胡静	4.15/4.10	/kb/conversations/images/download	GET	接受图片链接访问请求，鉴权成功后，返回图片数据
		文件访问	文件下载						施豪杰、胡静	4.15	/kb/knowledge_bases/files/download	GET	访问文件
	历史对话（IR2-SR6-AR9）	历史对话	查询历史对话列表						钱行锴、陈浩翔	4.15	/kb/conversations/query/batch	GET	支持分页、筛选
			查询单个对话结果						钱行锴、陈浩翔	4.15	/kb/conversations/query/single	GET	获取完整对话
			删除单个对话记录						钱行锴、陈浩翔	4.15	/kb/conversations/remove	POST	删除对话
													
其它	检索服务接口（IR2-SR6-AR13)	文本+向量检索	融合检索	"用户下发检索请求，WS完成鉴权校验
（知识库访问权限认证）
将请求转发到检索服务"					罗万千	4.15/4.10	/kb/retrieval/fusion_search	POST	支持用户指定检索模式和检索类型
