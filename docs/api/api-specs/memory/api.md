# 管理面：
## 3. 租户和实例管理端点
### 3.1 创建租户端点
**端点**: `POST /api/v1/tenants`
**描述**: 创建新租户，校验配额、初始化文件系统和数据库
**请求**:
```bash
curl -X POST http://localhost:8000/api/v1/tenants \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "new_tenant_001"
  }'
```
**请求参数**:
- `tenant_id` (string, required): 租户ID
**响应**:
```json
{
  "status": "success",
  "message": "租户创建成功"
}
```
### 3.2 删除租户端点
**端点**: `DELETE /api/v1/tenants/{tenant_id}`
**描述**: 删除租户，清理该租户下所有实例的记忆、文件和元数据
**请求**:
```bash
curl -X DELETE http://localhost:8000/api/v1/tenants/test_tenant
```
**请求参数**:
- `tenant_id` (string, required, path parameter): 租户ID
**响应**:
```json
{
  "status": "success",
  "message": "租户删除成功"
}
``
### 3.3 创建实例端点
**端点**: `POST /api/v1/tenants/{tenant_id}/instances`
**描述**: 创建实例，校验租户状态、配额，创建实例目录空间
**请求**:
```bash
curl -X POST http://localhost:8000/api/v1/tenants/test_tenant/instances \
  -H "Content-Type: application/json" \
  -d '{
    "instance_name": "new_instance_001"
  }'
```
**请求参数**:
- `tenant_id` (string, required, path parameter): 租户ID
- `instance_name` (string, required): 实例名称
**响应**:
```json
{
  "status": "success",
  "message": "实例创建成功"
}
``
### 3.4 删除实例端点
**端点**: `DELETE /api/v1/tenants/{tenant_id}/instances/{instance_name}`
**描述**: 删除实例，清理记忆、文件及SQL记录
**请求**:
```bash
curl -X DELETE "http://localhost:8000/api/v1/tenants/test_tenant/instances/test_instance_001"
```
**请求参数**:
- `tenant_id` (string, required, path parameter): 租户ID
- `instance_name` (string, required, path parameter): 实例名称
**响应**:
```json
{
  "status": "success",
  "message": "实例删除成功"
}
```
### 3.5 删除用户记忆端点
**端点**: `DELETE /api/v1/tenants/{tenant_id}/instances/{instance_name}/users/{user_id}/memories`
**描述**: 删除特定用户的所有记忆，删除FlowEngine中属于该user_id的所有记忆分片
**请求**:
```bash
curl -X DELETE "http://localhost:8000/api/v1/tenants/test_tenant/instances/test_instance_001/users/test_user_001/memories"
```
**请求参数**:
- `tenant_id` (string, required, path parameter): 租户ID
- `instance_name` (string, required, path parameter): 实例名称
- `user_id` (string, required, path parameter): 用户ID
**响应**:
```json
{
  "status": "success",
  "message": "用户记忆删除成功"
}
```
## 4. 模板管理端点
### 4.1 创建模板端点
**端点**: `POST /api/v1/templates`
**描述**: 创建新的记忆类型模板
**请求**:
```bash
curl -X POST http://localhost:8000/api/v1/templates \
  -H "Content-Type: application/json" \
  -d '{
    "instance_id": "test_instance_001",
    "template_data": {
      "tenant_id": "test_tenant",
      "template_id": "user_profile",
      "template_name": "user_profile",
      "memory_type": "fact",
      "description": "用户基本信息模板",
      "extraction_prompt": "提取用户的基本信息，包括姓名、年龄、职业等",
      "enable_llm_extraction": true
    }
  }'
```
**请求参数**:
- `instance_id` (string, required): 实例ID
- `template_data` (object, required): 模板配置数据
  - `tenant_id` (string, required): 租户ID
  - `template_id` (string, optional): 模板ID
  - `template_name` (string, required): 模板名称
  - `memory_type` (string, required): 记忆类型
  - `description` (string, optional): 描述
  - `extraction_prompt` (string, optional): 抽取提示词
  - `enable_llm_extraction` (boolean, optional): 是否启用LLM抽取
**响应**:
```json
{
  "success": true,
  "template": {
    "template_id": "user_profile",
    "template_name": "user_profile",
    "memory_type": "fact",
    "description": "用户基本信息模板"
  }
}
```
### 4.2 获取所有模板端点
**端点**: `GET /api/v1/templates`
**描述**: 获取指定租户下的所有模板
**请求**:
```bash
curl -X GET "http://localhost:8000/api/v1/templates?tenant_id=test_tenant&instance_id=test_instance_001"
```
**请求参数**:
- `tenant_id` (string, required, query parameter): 租户ID
- `instance_id` (string, required, query parameter): 实例ID
**响应**:
```json
{
  "templates": [
    {
      "template_id": "user_profile",
      "template_name": "user_profile",
      "memory_type": "fact",
      "description": "用户基本信息模板"
    }
  ],
  "total": 1
}
```
### 4.3 获取单个模板端点
**端点**: `GET /api/v1/templates/{template_id}`
**描述**: 获取指定模板的详细配置
**请求**:
```bash
curl -X GET "http://localhost:8000/api/v1/templates/user_profile?tenant_id=test_tenant&instance_id=test_instance_001"
```
**请求参数**:
- `template_id` (string, required, path parameter): 模板ID
- `tenant_id` (string, required, query parameter): 租户ID
- `instance_id` (string, required, query parameter): 实例ID
**响应**:
```json
{
  "template_id": "user_profile",
  "mem_name": "User Profile",
  "mem_type": "fact",
  "description": "用户基本信息模板",
  "status": "SUCCESS",
  "enable_llm_extraction": true,
  "created_at": "2026-04-20T12:00:00Z",
  "agent_filter": {
    "mode": "whitelist",
    "agent_ids": ["agent_001", "agent_002"]
  }
}
```
**测试结果**: ✓ 通过 (HTTP 200)
### 4.4 更新模板端点
**端点**: `PUT /api/v1/templates/{template_id}`
**描述**: 更新指定模板的配置
**请求**:
```bash
curl -X PUT "http://localhost:8000/api/v1/templates/user_profile?tenant_id=test_tenant&instance_id=test_instance_001" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "test_tenant",
    "instance_id": "test_instance_001",
    "template_id": "user_profile",
    "update_data": {
      "description": "更新的用户信息模板"
    }
  }'
```
**请求参数**:
- `template_id` (string, required, path parameter): 模板ID
- `tenant_id` (string, required, query parameter): 租户ID
- `instance_id` (string, required, query parameter): 实例ID
- `update_data` (object, required): 更新数据
**响应**:
```json
{
  "template_id": "user_profile",
  "mem_name": "User Profile",
  "mem_type": "fact",
  "description": "更新的用户信息模板",
  "status": "SUCCESS"
}
```
### 4.5 删除模板端点
**端点**: `DELETE /api/v1/templates/{template_id}`
**描述**: 删除指定模板
**请求**:
```bash
curl -X DELETE "http://localhost:8000/api/v1/templates/user_profile?tenant_id=test_tenant&instance_id=test_instance_001"
```
**请求参数**:
- `template_id` (string, required, path parameter): 模板ID
- `tenant_id` (string, required, query parameter): 租户ID
- `instance_id` (string, required, query parameter): 实例ID
**响应**:
```json
{
  "status": "success",
  "message": "Template user_profile deleted"
}
```
### 4.6 更新模板过滤器端点
**端点**: `POST /api/v1/templates/{template_id}/filters`
**描述**: 更新模板的黑白名单配置
**请求**:
```bash
curl -X POST "http://localhost:8000/api/v1/templates/user_profile/filters" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "test_tenant",
    "instance_id": "test_instance_001",
    "template_id": "user_profile",
    "whitelist": ["agent_001", "agent_002"],
    "blacklist": ["agent_999"]
  }'
```
**请求参数**:
- `template_id` (string, required, path parameter): 模板ID
- `tenant_id` (string, required): 租户ID
- `instance_id` (string, required): 实例ID
- `whitelist` (array, optional): 白名单Agent ID列表
- `blacklist` (array, optional): 黑名单Agent ID列表
**响应**:
```json
{
  "whitelist": ["agent_001", "agent_002"],
  "blacklist": ["agent_999"]
}
```
### 4.7 获取模板过滤器端点
**端点**: `GET /api/v1/templates/{template_id}/filters`
**描述**: 查询模板的黑白名单配置
**请求**:
```bash
curl -X GET "http://localhost:8000/api/v1/templates/user_profile/filters?tenant_id=test_tenant&instance_id=test_instance_001"
```
**请求参数**:
- `template_id` (string, required, path parameter): 模板ID
- `tenant_id` (string, required, query parameter): 租户ID
- `instance_id` (string, required, query parameter): 实例ID
**响应**:
```json
{
  "whitelist": ["agent_001", "agent_002"],
  "blacklist": ["agent_999"]
}
```
### 4.8 设置LLM抽取端点
**端点**: `POST /api/v1/templates/{template_id}/llm-extraction`
**描述**: 动态控制该模板是否启用LLM抽取特征
**请求**:
```bash
curl -X POST "http://localhost:8000/api/v1/templates/user_profile/llm-extraction" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "test_tenant",
    "instance_id": "test_instance_001",
    "template_id": "user_profile",
    "enable": true
  }'
```
**请求参数**:
- `template_id` (string, required, path parameter): 模板ID
- `tenant_id` (string, required): 租户ID
- `instance_id` (string, required): 实例ID
- `enable` (boolean, required): 是否启用LLM抽取
**响应**:
```json
{
  "status": "success",
  "template_id": "user_profile",
  "enable_llm_extraction": true
}
```
### 4.9 批量设置LLM抽取端点
**端点**: `POST /api/v1/templates/batch/llm-extraction`
**描述**: 批量更新多个模板的LLM抽取开关配置
**请求**:
```bash
curl -X POST http://localhost:8000/api/v1/templates/batch/llm-extraction \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "test_tenant",
    "instance_id": "test_instance_001",
    "update_map": {
      "user_profile": true,
      "user_interests": false
    }
  }'
```
**请求参数**:
- `tenant_id` (string, required): 租户ID
- `instance_id` (string, required): 实例ID
- `update_map` (object, required): 模板ID启用状态映射，key为模板ID，value为布尔值
**响应**:
```json
{
  "status": "success",
  "updated_count": 2
}
```
### 3.2 获取所有模板端点
**端点**: `GET /api/v1/templates`
**描述**: 获取指定租户下的所有模板
**请求**:
```bash
curl -X GET "http://localhost:8000/api/v1/templates?tenant_id=test_tenant&instance_id=test_instance_001"
```
### 3.3 获取单个模板端点
**端点**: `GET /api/v1/templates/{template_id}`
**描述**: 获取指定模板的详细配置
**请求**:
```bash
curl -X GET "http://localhost:8000/api/v1/templates/user_profile?tenant_id=test_tenant&instance_id=test_instance_001"
```
**响应**:
```json
{
  "template_id": "user_profile",
  "mem_name": "User Profile",
  "mem_type": "fact",
  "description": "用户基本信息模板",
  "status": "SUCCESS",
  "enable_llm_extraction": true
}
```
### 3.4 更新模板端点
**端点**: `PUT /api/v1/templates/{template_id}`
**描述**: 更新指定模板的配置
**请求**:
```bash
curl -X PUT "http://localhost:8000/api/v1/templates/user_profile?tenant_id=test_tenant&instance_id=test_instance_001" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "test_tenant",
    "instance_id": "test_instance_001",
    "template_id": "user_profile",
    "update_data": {
      "description": "更新的用户信息模板"
    }
  }'
```
### 3.5 删除模板端点
**端点**: `DELETE /api/v1/templates/{template_id}`
**描述**: 删除指定模板
**请求**:
```bash
curl -X DELETE "http://localhost:8000/api/v1/templates/user_profile?tenant_id=test_tenant&instance_id=test_instance_001"
```
### 3.6 更新模板过滤器端点
**端点**: `POST /api/v1/templates/{template_id}/filters`
**描述**: 更新模板的黑白名单配置
**请求**:
```bash
curl -X POST "http://localhost:8000/api/v1/templates/user_profile/filters?tenant_id=test_tenant&instance_id=test_instance_001" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "test_tenant",
    "instance_id": "test_instance_001",
    "template_id": "user_profile",
    "whitelist": ["agent_001", "agent_002"],
    "blacklist": ["agent_999"]
  }'
```
### 3.7 获取模板过滤器端点
**端点**: `GET /api/v1/templates/{template_id}/filters`
**描述**: 查询模板的黑白名单配置
**请求**:
```bash
curl -X GET "http://localhost:8000/api/v1/templates/user_profile/filters?tenant_id=test_tenant&instance_id=test_instance_001"
```
### 3.8 批量设置LLM抽取端点
**端点**: `POST /api/v1/templates/batch/llm-extraction`
**描述**: 批量更新多个模板的LLM抽取开关配置
**请求**:
```bash
curl -X POST http://localhost:8000/api/v1/templates/batch/llm-extraction \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "test_tenant",
    "instance_id": "test_instance_001",
    "update_map": {
      "user_profile": true,
      "user_interests": false
    }
  }'
```


# 数据面：
## 1. 系统管理端点
### 1.1 健康检查端点
**端点**: `GET /api/v1/health`
**描述**: 检查API服务健康状态
**请求**:
```bash
curl -X GET http://localhost:8000/api/v1/health
```
**请求参数**: 无
**响应**:
```json
{
  "status": "healthy",
  "service": "UnifiedMem",
  "version": "1.0.0"
}
```
### 1.2 故障恢复端点
**端点**: `POST /api/v1/system/recovery`
**描述**: 触发系统故障恢复
**请求**:
```bash
curl -X POST http://localhost:8000/api/v1/system/recovery \
  -H "Content-Type: application/json" \
  -d '{"recovery_point": "latest", "scope": "all"}'
```
**请求参数**:
- `recovery_point` (string, required): 恢复点，"latest"或时间戳
- `scope` (string, required): 恢复范围，"all"或agent_id
**响应**:
```json
{
  "recovered": 10,
  "failed": 0,
  "stale": 2,
  "details": [],
  "completed_at": "2026-04-20T12:00:00Z"
}
```
## 2. 记忆管理端点
### 2.1 添加记忆端点
**端点**: `POST /api/v1/memory/add`
**描述**: 添加新的记忆内容到系统
**请求**:
```bash
curl -X POST http://localhost:8000/api/v1/memory/add \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "test_tenant",
    "instance_id": "test_instance_001",
    "user_id": "test_user_001",
    "content": "我的生日是1990年5月15日。",
    "metadata": {"msg_type": "user"}
  }'
```
**请求参数**:
- `tenant_id` (string, required): 租户ID
- `instance_id` (string, required): 实例ID
- `user_id` (string, required): 用户ID
- `app_id` (string, optional): 应用ID，默认为"default"
- `session_id` (string, optional): 会话ID，默认为"default"
- `agent_id` (string, optional): Agent ID，默认为"default"
- `content` (string, required): 记忆内容
- `metadata` (object, optional): 附加元数据，默认为空对象
**响应**:
```json
{
  "ack_id": "6f52d8a1-3038-454f-ad75-af32610291e9",
  "status": "SUCCESS",
  "message": "Memory queued. Window: c53ebd10-9dc7-4a27-a078-70b1f05bce31, offset: 735"
}
```
### 2.2 查询记忆端点
**端点**: `POST /api/v1/memory/query`
**描述**: 根据查询条件检索相关记忆
**请求**:
```bash
curl -X POST http://localhost:8000/api/v1/memory/query \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "test_tenant",
    "instance_id": "test_instance_001",
    "user_id": "test_user_001",
    "query": "生日",
    "top_k": 5
  }'
```
**请求参数**:
- `tenant_id` (string, required): 租户ID
- `instance_id` (string, required): 实例ID
- `user_id` (string, required): 用户ID
- `query` (string, required): 查询文本
- `app_id` (string, optional): 应用ID，默认为"default"
- `agent_id` (string, optional): Agent ID，默认为"default"
- `memory_type` (array, optional): 记忆类型过滤，如 ['Fact', 'Preference']
- `top_k` (integer, required): 返回结果数量，范围1-100，默认为10
- `threshold` (integer, required): 相似度阈值，范围0-100，默认为0
**响应**:
```json
{
  "memories": [
    {
      "memory_id": "col_test_tenant_test_instance_001_user_abc123",
      "content": "我的生日是1990年5月15日",
      "memory_type": "fact",
      "created_at": "2026-04-20T12:00:00Z",
      "relevance_score": 0.95
    }
  ],
  "total": 1
}
```
**注意**: 查询返回空结果是因为记忆需要等待窗口处理器和异步抽取完成，但API接口本身正常工作。
### 2.3 更新记忆端点
**端点**: `POST /api/v1/memory/update`
**描述**: 更新现有记忆的内容
**请求**:
```bash
curl -X POST http://localhost:8000/api/v1/memory/update \
  -H "Content-Type: application/json" \
  -d '{
    "memory_id": "test_memory_id",
    "content": "更新后的记忆内容"
  }'
```
**请求参数**:
- `tenant_id` (string, required): 租户ID
- `instance_id` (string, required): 实例ID
- `memory_id` (string, required): 记忆ID
- `content` (string, required): 更新后的内容
**响应**:
```json
{
  "status": "SUCCESS",
  "message": "更新成功"
}
```
### 2.4 删除记忆端点
**端点**: `POST /api/v1/memory/delete`
**描述**: 根据记忆ID和过滤条件删除记忆
**请求**:
```bash
curl -X POST http://localhost:8000/api/v1/memory/delete \
  -H "Content-Type: application/json" \
  -d '{
    "memory_id": "test_memory_id"
  }'
```
**请求参数**:
- `memory_id` (string, required): 记忆ID
- `filters` (object, optional): 过滤条件，可以为空
**响应**:
```json
{
  "status": "SUCCESS",
  "message": "删除成功"
}
```