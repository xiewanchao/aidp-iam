（1）知识库管理
GET /kb/knowledge_bases/page
说明： 批量分页访问模式
入参：

{
    "page_index": int,  // 页码，从1开始
    "page_size": int    // 每页数量
}
​
响应：

{
    "data": [
        {
            "KDSID": int,              // 知识库id
            "VECTORCOLLNAME": str,     // 数据表名称
            "KMSCONFIGSTR": {
                "chunk_token_num": int,    // chunk数量
                "chunk_overlap_num": int,  // 重叠大小
                "embedding_model": str     // embedding模型
            },
            "DESCRIPTION": str,        // 知识库描述
            "KDSNAME": str,             // 知识库名称
            "STATE": int,               // 知识库状态
            "PERSONSPACE": int,         // 是否是个人空间
            "CREATE_TIME": str          // 创建时间
        },
        ...
    ],
    "result": {
        "code": int,
        "description": str,
        "suggestion": str
    }
}
​
GET /kb/knowledge_bases/count
说明： 查询知识库总数
入参：

无
​
响应：

{
    "data": {"count": int},
    "result": {
        "code": int,
        "description": str,
        "suggestion": str
    }
}
​
GET /kb/knowledge_bases
说明： 单个查询知识库
入参：

{
    "kbs_id": str  // 知识库ID
}
​
响应：

{
    "data": {
        "KDSID": int,              // 知识库id
        "VECTORCOLLNAME": str,     // 数据表名称
        "KMSCONFIGSTR": {
            "chunk_token_num": int,    // chunk数量
            "chunk_overlap_num": int,  // 重叠大小
            "embedding_model": str     // embedding模型
        },
        "DESCRIPTION": str,        // 知识库描述
        "KDSNAME": str,             // 知识库名称
        "STATE": int,               // 知识库状态
        "PERSONSPACE": int,         // 是否是个人空间
        "CREATE_TIME": str          // 创建时间
    },
    "result": {
        "code": int,
        "description": str,
        "suggestion": str
    }
}
​
POST /kb/knowledge_bases/add
说明： 创建知识库
入参：

{
    "name": str,                // 知识库名称
    "description": str,         // 知识库描述
    "embedding_model": str,     // 向量模型名称
    "chunk_token_num": int,     // 分块token数量
    "chunk_overlap_num": int,   // 分块重叠token数量
    "is_personal": int,         // 是否个人空间(0或1)
    "topk": int,                // TopK召回数量
    "similarity": float,        // 相似度阈值(0-1)
    "smartsplit": int           // 是否启用智能分片(0或1)
}
​
响应：

{
    "data": {"KDSID": str},
    "result": {
        "code": int,
        "description": str,
        "suggestion": str
    }
}
​
POST /kb/knowledge_bases/modify
说明： 修改知识库
入参：

{
    "kbs_id": str,              // 知识库ID
    "name": str,                // 知识库名称
    "description": str,         // 知识库描述
    "embedding_model": str,     // 向量模型名称
    "chunk_token_num": int,     // 分块token数量
    "chunk_overlap_num": int,   // 分块重叠token数量
    "is_personal": int,         // 是否个人空间(0或1)
    "topk": int,                // TopK召回数量
    "similarity": float,        // 相似度阈值(0-1)
    "smartsplit": int           // 是否启用智能分片(0或1)
}
​
响应：

{
    "data": null,
    "result": {
        "code": int,
        "description": str,
        "suggestion": str
    }
}
​
POST /kb/knowledge_bases/remove
说明： 删除知识库
入参：

{
    "kbs_id": str  // 知识库ID
}
​
响应：

{
    "data": null,
    "result": {
        "code": int,
        "description": str,
        "suggestion": str
    }
}
​
GET /kb/knowledge_bases/mappings
说明： 查询知识库创建的目录映射列表
入参：

{
    "kbs_id": str,      // 知识库ID
    "page_index": int,  // 页码，从1开始
    "page_size": int    // 每页数量
}
​
响应：

{
    "data": [
        {
            "CHANNELID": int,       // 管道id
            "FSID": str,            // 文件系统id
            "FSNAME": str,          // 文件系统共享名
            "SRCDIR": str,          // 映射目录
            "STATE": int,           // 管道状态
            "DSTKDSID": int,        // 所属知识库id
            "DEFAULT": bool         // 是否是默认管道
        },
        ...
    ],
    "result": {
        "code": int,
        "description": str,
        "suggestion": str
    }
}
​
GET /kb/knowledge_bases/mappings/count
说明： 查询知识库的目录映射列表数量
入参：

{
    "kbs_id": str  // 知识库ID
}
​
响应：

{
    "data": {"count": int},
    "result": {
        "code": int,
        "description": str,
        "suggestion": str
    }
}
​
POST /kb/knowledge_bases/mappings/add
说明： 从知识库创建目录映射
入参：

{
    "kbs_dm_id": str,     // 目录映射ID
    "kbs_id": str,        // 知识库ID
    "src_dir": str,       // 源目录路径
    "fs_name": str,       // 文件系统名称
    "fs_id": str,         // 文件系统ID
    "channel_name": str   // 管道名称
}
​
响应：

{
    "data": {"CHANNELID": str},     // 管道id
    "result": {
        "code": int,
        "description": str,
        "suggestion": str
    }
}
​
POST /kb/knowledge_bases/mappings/remove
说明： 从知识库删除目录映射
入参：

{
    "kbs_dm_id": str  // 目录映射ID
}
​
响应：

{
    "data": null,
    "result": {
        "code": int,
        "description": str,
        "suggestion": str
    }
}
​
（2）文件系统
POST /kb/knowledge_bases/files
说明： 分页查询指定知识库下已入库文件

入参：

{
    "kbs_id": str,      // 查询的知识库ID
    "page_index": int,  // 当前页码
    "page_size": int    // 每页条数
}
​
响应：

// 查询成功
{
  "code": 0,
  "message": "query knowledge files success",
  "data": {
    "files": [
      {
        "file_name": "test_100K.txt",
        "file_type": "txt",
        "file_size": 96644,
        "first_upload_time": 1776757330,
        "update_time": 1776757330,
        "import_source_dir": "/21"
      }
    ],
    "total": 1  // 当前页的文件总数
  }
}
​
GET /kb/knowledge_bases/files/count
说明： 查询指定知识库下已入库文件总数

入参：

"kbs_id": str   // 查询的知识库ID
​
响应：

// 查询成功
{
  "code": 0,
  "message": "query knowledge files count success",
  "data": {
    "total": 1
  }
}
​
POST /kb/knowledge_bases/files/history
说明： 分页查询指定目录下文件上传/入库状态

入参：

{
    "kbs_id": str,      // 查询的知识库ID
    "page_index": int,  // 当前页码
    "page_size": int,   // 每页条数
    "dir_path": str | None,     // 查询目录相对于文件系统的绝对路径（不传代表查询的是手动上传目录）
    "fs_id": str | None         // 查询目录所在的文件系统ID（不传代表查询的是手动上传目录）
}
​
响应：

// 查询成功
{
  "code": 0,
  "message": "query knowledge files history success",
  "data": {
    "history_list": [
      {
        "file_name": "test_95K.txt",
        "file_type": "txt",
        "file_size": 96644,
        "status": "COMPLETED"
      }
    ],
    "summary": {
      "created_at": 1776688199,
      "last_pipeline_time": null,
      "total": 0,
      "success": 0
    }
  }
}
​
GET /kb/knowledge_bases/files/filesystem
暂未支持

POST /kb/knowledge_bases/files/upload
说明： 手动上传文件到指定知识库

这里需要前端对上传文件的文件类型、文件大小、文件数量做拦截

入参：

{
    "kbs_id": str,      // 上传文件的目标知识库ID
    "files": List[UploadFile]   // 上传文件列表
}

// 注意这里参数为表单形式
// 调用形式如下
curl -X 'POST' \
  'http://localhost:8888/kb/knowledge_bases/files/upload' \
  -H 'accept: application/json' \
  -H 'Content-Type: multipart/form-data' \
  -F 'kbs_id=2' \
  -F 'files=@test.png;type=image/png'
​
响应：

// 上传成功
{
  "code": 0,
  "message": "操作成功",
  "data": {
    "summary": {
      "total": 1,
      "success": 1,
      "failed": 0
    },
    "success_list": [
      {
        "filename": "test.png",
        "path": "/opt/upload_fs/21/test.png",
        "size": 207056
      }
    ],
    "failed_list": []
  }
}
​
POST /kb/knowledge_bases/files/remove
暂未支持

（3）模型配置
GET /kb/models/config
说明： 查询所有/指定模型配置

入参：

model_api_id: str | None    // 传id时代表查询指定模型配置，传空代表查询所有模型配置
​
响应：

// 查询成功
{
  "code": 0,
  "message": "query model config success",
  "data": [
    {
      "ID": 0,
      "MODELAPI": "{\"APIUrl\": \"http://mindie-svc:18088/v1\", \"APIKey\": \"sk-1234\", \"ModelID\": \"qwen3-30b-a3b-thinking-2507-w8a8\", \"ModelName\": \"qwen3-30b-a3b\", \"ModelType\": \"LLM\", \"Provider\": \"Admin\"}"
    }
  ]
}
​
POST /kb/models/config/add
说明： 新增模型配置

入参：

{
  "model_type": "string",   // 模型类型，支持 LLM | EMBEDDING | RERANKER | OCR
  "model_name": "string",   // 模型名称
  "model_id": "string",     // 模型ID
  "api_url": "string",      // 模型API地址
  "api_key": "string",      // 模型API密钥
  "provider": "string" | None      // 模型供应商
}
​
响应：

// 添加成功
{
  "code": 0,
  "message": "add model config success",
  "data": 2     // 模型配置ID
}
​
POST /kb/models/config/modify
说明： 修改模型配置

入参：

{
  "id": "string", // 预修改的模型ID
  "model_api": {
    "model_type": "string",   // 模型类型，支持 LLM | EMBEDDING | RERANKER | OCR
    "model_name": "string",   // 模型名称
    "model_id": "string",     // 模型ID
    "api_url": "string",      // 模型API地址
    "api_key": "string",      // 模型API密钥
    "provider": "string" | None      // 模型供应商
  }
}
​
响应：

// 修改成功
{
  "code": 0,
  "message": "modify model config success",
  "data": true
}
​
POST /kb/models/config/remove
说明： 删除模型配置

入参：

{
  "id": "string"  // 预删除的模型ID
}
​
响应：

// 删除成功
{
  "code": 0,
  "message": "remove model config success",
  "data": true
}
​
（4）提示词管理
GET /kb/prompts/detail
说明： 获取单个prompt组详情（单个查询）

入参：

{
    "id": int  // 提示词组ID，必填
}
​
响应：

{
    "code": int,
    "message": str,
    "data": {
        "id": int,
        "title": str,
        "description": str,
        "mode": str,
        "source": str,
        "prompts": {
                        "  " : "  ", 
                        "  ": "   "
                         },          // 提示词集合，key为提示词类型，value为提示词内容
        "kb_ids": [],           // 关联知识库ID列表
        "created_at": str,
        "updated_at": str
    }
}
​
GET /kb/prompts/options
说明： 获取菜单精简列表

入参：

{
    "mode": str  // 关联模式（NAIVE/DEEP_QA/DEEP_REPORT），可选
}
​
响应：

{
    "code": int,
    "message": str,
    "data": [
        {
            "id": int,
            "title": str,
            "description": str,
            "mode": str
        },
        ...
    ]
}
​
GET /kb/prompts/page
说明： 分页查询prompt组列表（批量查询）

入参：

{
    "page": int,       // 页码，默认1
    "size": int,       // 每页条数，默认20
    "mode": str,       // 按模式过滤（NAIVE/DEEP_QA/DEEP_REPORT），可选
    "keyword": str     // 按标题或简介模糊搜索，可选
}
​
响应：

{
    "code": int,
    "message": str,
    "data": {
        "total": int,
        "pages": int,
        "page": int,
        "size": int,
        "items": [
            {
                "id": int,
                "title": str,
                "description": str,
                "mode": str,
                "source": str,
                "created_at": str,
                "updated_at": str
            },
            ...
        ]
    }
}
​
POST /kb/prompts/add
说明： 创建提示词

入参：

{
    "title": str,        // 提示词组标题，必填，最大长度128
    "description": str,  // 提示词组描述信息，必填
    "mode": str,         // 关联模式（NAIVE/DEEP_QA/DEEP_REPORT），必填
    "prompts": {}        // 提示词集合，key为提示词类型，value为提示词内容
}
​
响应：

{
    "code": int,
    "message": str,
    "data": {"id": int}  // 新创建的提示词组ID
}
​
POST /kb/prompts/modify
说明： 修改提示词

入参：

{
    "id": int,           // 提示词组ID，必填
    "title": str,        // 提示词组标题，可选
    "description": str,  // 提示词组描述信息，可选
    "prompts": {}        // 提示词集合，可选
}
​
响应：

{
    "code": int,
    "message": str,
    "data": null //返回的实际数据内容
}
​
POST /kb/prompts/remove
说明： 删除提示词

入参：

{
    "id": int  // 提示词组ID，必填
}
​
响应：

{
    "code": int,
    "message": str,
    "data": null //返回的实际数据内容
}
​
（5）术语管理
GET /kb/jargon_groups
说明： 查询黑话库列表，支持分页

入参：

{
    "page": int,  // 页码，默认1
    "size": int   // 每页条数，默认10
}
​
响应：

{
    "code": int,
    "message": str,
    "data": {
        "total": int,
        "page": int,
        "size": int,
        "total_pages": int,
        "list": [
            {
                "jargon_lib_id": int,
                "jargon_lib_name": str,
                "description": str,
                "entry_count": int,
                "bound_kb_ids": [],   // 关联的知识库ID列表
                "created_at": int     // 时间戳
            },
            ...
        ]
    }
}
​
GET /kb/jargon_groups/jargons
说明： 查询黑话库中的黑话信息

入参：

{
    "jargon_lib_name": str,  // 黑话库名称
    "page": int,             // 页码，默认1
    "size": int              // 每页条数，默认20
}
​
响应：

{
    "code": int,
    "message": str,
    "data": {
        "total": int,
        "page": int,
        "size": int,
        "list": [
            {
                "jargon_id": int,
                "jargon_name": str,
                "target_term": [],     // 目标词条
                "description": str,
                "created_at": int      // 时间戳
            },
            ...
        ]
    }
}
​
GET /kb/knowledge_bases/jargon_groups
说明： 查询指定知识库绑定的黑话库名称

入参：

{
    "kb_name": str  // 知识库名称
}
​
响应：

{
    "code": int,
    "message": str,
    "jargon_lib_name": str
}
​
GET /kb/jargons_groups/jargon
说明： 查询指定黑话库里的指定黑话

入参：

{
    "jargon_lib_name": str,
    "jargon_name": []  // 黑话名称列表
}
​
响应：

{
    "code": int,
    "message": str,
    "data": [
        {
            "jargon_name": str,
            "target_term": [],     // 目标词条
            "description": str,
            "created_at": int,     // 时间戳
            "exist": bool
        },
        ...
    ]
}
​
GET /kb/jargon_groups/version
说明： 查询指定黑话库的版本号

入参：

{
    "jargon_lib_name": str  // 黑话库名称
}
​
响应：

{
    "code": int,
    "message": str,
    "sequence_id": int
}
​
POST /kb/jargon_groups/add
说明： 创建黑话库

入参：

{
    "jargon_lib_name": str,  // 黑话库名称
    "description": str       // 描述
}
​
响应：

{
    "code": int,
    "message": str,
    "data": {
        "jargon_lib_id": int,
        "jargon_lib_name": str,
        "created_at": str
    }
}
​
POST /kb/jargon_groups/remove
说明： 删除黑话库

入参：

{
    "jargon_lib_name": str  // 黑话库名称
}
​
响应：

{
    "code": int,
    "message": str
}
​
POST /kb/jargon_groups/knowledge_bases/add
说明： 将黑话库配置到指定知识库

入参：

{
    "kb_name": str,         // 知识库名称
    "jargon_lib_name": str  // 黑话库名称
}
​
响应：

{
    "code": int,
    "message": str
}
​
POST /kb/jargon_groups/knowledge_bases/remove
说明： 从指定知识库移除黑话库

入参：

{
    "kb_name": str,
    "jargon_lib_name": str
}
​
响应：

{
    "code": int,
    "message": str
}
​
POST /kb/jargons/add（需等小庆哥反馈）
说明： 创建黑话（批量接口）

入参：

{
    "jargon_info_list": [
        {
            "jargon_name": str,       // 黑话名称
            "description": str,       // 描述
            "target_term": [],        // 目标词条
            "jargon_lib_name": str,   // 黑话库名称
            "creator_name": str,      // 创建人名称
            "creator_id": str         // 创建人ID
        },
        ...
    ]
}
​
响应：

{
    "code": int,
    "message": str
}
​
POST /kb/jargons/modify
说明： 修改指定黑话库中指定黑话的描述

入参：

{
    "jargon_lib_name": str,  // 黑话库名称
    "jargon_name": str,      // 黑话名称
    "description": str       // 新描述
}
​
响应：

{
    "code": int,
    "message": str,
    "data": {
        "jargon_id": int,       //黑话词条的唯一ID 
        "jargon_name": str,   //黑话名称
        "target_term": [" "],      //目标词条 （例如黑话名称为“白嫖”，则目标词条为["免费获取"]）
        "description": str,         // 黑话说明
        "update_at": int          // 修改时间（时间戳格式）
    }
}
​
POST /kb/jargons/remove
说明： 删除黑话

入参：

{
    "jargon_lib_name": str,  // 黑话库名称
    "jargon_name": str       // 黑话名称
}
​
响应：

{
    "code": int,
    "message": str
}
​
（6）问答
POST /kb/conversations/start
说明： 问答接口

入参：

{
    "query": str,   // 查询内容
    "resources": List[Resource],    // 知识库列表，支持长度为1-10
    "thread_id": str,   // 会话ID
    "user_id": str,     // 用户ID
    "output_format": str,   // 输出格式，rag_mode为"deep_research"时支持"qa"和"report"，rag_mode为"naive_rag"时仅支持"qa"
    "llm": ModelConfig,     // LLM模型配置
    "embedding": ModelConfig | None,    // Embedding模型配置
    "reranker": ModelConfig | None,     // Reranker模型配置
    "rag_mode": str,    // 支持"deep_research"和"naive_rag"
    "enable_proxy": bool    // 是否启用代理（模型是否为外部模型）
}

// Resource
{
    "collection_name": str,     // 知识库表名
    "title": str | None,        // 标题
    "db_url": str | None,       // 知识库URL
    "token": str | None         // 知识库令牌
}

// ModelConfig
{
    "id": str,          // 模型ID
    "name": str | None, // 模型名称
    "type": str,        // 支持 LLM | EMBEDDING | RERANKER
    "provider": str | None, // 模型供应商
    "base_url": str | None, // 模型API地址
    "api_key": str | None,  // 模型API密钥
    "model": str | None,    // 模型名称
    "temperature": float | None,    // 温度参数
    "top_k": int | None,
    "top_p": float | None,
    "max_tokens": int | None
}
​
响应：

// 流式响应
{
  "event": str,
  "data": Dict[str, str]
}
​
POST /kb/conversations/stop
说明： 停止问答接口

入参：

{
  "thread_id": str
}
​
响应：

// 停止成功
{
  "code": 0,
  "message": "stop chat success"
}
​
POST /kb/conversations/images/generate
说明： 图片加密接口

入参：

{
  "image_url": str  // 图片路径
}
​
响应：

// 加密成功
{
  "code": 0,
  "message": "generate image link success",
  "data": {
    "token": "P0jxCzd1KIwIgx7MAcowGEqOQT82uKQrCVpoP49BTfsJ-vUtwrZSB1UIHzPoapzt6MsoqGmPpd1HQYunSC0Vp_rUQPmkvC_g4B70ShDZJSck0xCGDvILgDxrJvpSKpY7EAHl--9jTLl-5y-g"
  }
}
​
GET /kb/conversations/images/download
说明： 图片解密接口

入参：

"token": str
​
响应：

返回图片二进制流
​
GET /kb/conversations/list
暂未支持

GET /kb/conversations
暂未支持

POST /kb/conversations/remove
暂未支持

（7）检索
POST /kb/retrieval/fusion_search
说明： 多模态检索接口

入参：

{
    "query": str,            // 待检索的问题
    "kds_list": List[str],   // 检索的知识库ID列表
    "search_method": str,    // 检索方法，支持 hybrid_search | vector_search | full_text_search
    "reranking_enable": bool | None, // 是否重排序，默认为False
    "reranking_mode": str | None,   // 重排模式，支持重排序时有效，支持 performance | high_accuracy，默认为performance
    "top_k": int | None,     // 返回的匹配结果数，范围为[1, 100]，默认为10
    "multi_model": bool | None  // 是否多模态检索，默认为False
}
​
响应：

// 检索成功
{
  "code": 0,
  "message": "retrieve success",
  "data": {
    "data": [
      {
        "chunk_type": "text",
        "file_url": "",
        "id": 0,
        "score": 1.043726921081543,
        "text": "大耳朵图图动画片"
      },
      {
        "chunk_type": "image",
        "file_url": "/tmp/cpr_video/1.jpg",
        "id": 1,
        "score": 0.6488802433013916,
        "text": "汤姆和杰瑞"
      }
    ],
    "total_return_count": 2
  }
}