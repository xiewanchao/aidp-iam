"""Generate api-spec-v2.xlsx with 2 sheets: IAM APIs + App Configurations."""
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

hf = Font(name="微软雅黑", size=11, bold=True, color="FFFFFF")
hfill = PatternFill("solid", fgColor="2F5496")
bf = Font(name="微软雅黑", size=10)
border = Border(*[Side("thin", color="BFBFBF")] * 4)
center = Alignment(horizontal="center", vertical="center", wrap_text=True)
wrap = Alignment(horizontal="left", vertical="top", wrap_text=True)

method_fill = {
    "GET": PatternFill("solid", fgColor="C6EFCE"),
    "POST": PatternFill("solid", fgColor="BDD7EE"),
    "PUT": PatternFill("solid", fgColor="FCE4D6"),
    "DELETE": PatternFill("solid", fgColor="FFC7CE"),
}


def _header(ws, headers):
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.font = hf; cell.fill = hfill; cell.alignment = center; cell.border = border


def _row(ws, r, values, method_col=None):
    for c, v in enumerate(values, 1):
        cell = ws.cell(row=r, column=c, value=v)
        cell.font = bf; cell.alignment = wrap if c > 3 else center; cell.border = border
        if method_col and c == method_col:
            cell.fill = method_fill.get(v, PatternFill())


# ============================================================
# Sheet 1: IAM System APIs
# ============================================================
IAM_HEADERS = ["模块", "接口名称", "Method", "路径", "调用方", "说明", "请求体/参数", "响应", "备注"]

IAM_APIS = [
    # --- 用户管理 ---
    ("用户管理", "用户列表", "GET", "/api/v1/{realm}/users", "前端", "支持搜索/分页/按组过滤，返回 account_type + groups", "search, group_id, first, max", "List[UserListResponse]", "account_type: internal/federated"),
    ("用户管理", "用户详情", "GET", "/api/v1/{realm}/users/{user_id}/details", "前端", "用户信息 + 所属组 + 路径权限聚合", "—", "UserDetailResponse(permissions从path_rules反查)", "按应用分组展示权限"),
    ("用户管理", "创建用户", "POST", "/api/v1/{realm}/users", "前端", "创建内部用户，临时密码，可选绑组", '{"username","password","email","groups":[gid]}', "UserListResponse (201)", "temporary=true"),
    ("用户管理", "修改用户", "PUT", "/api/v1/{realm}/users/{user_id}", "前端", "修改基本信息", '{"firstName","lastName","email","enabled"}', "UserListResponse", ""),
    ("用户管理", "删除用户", "DELETE", "/api/v1/{realm}/users/{user_id}", "前端", "删除单个用户", "—", "204", "联邦用户删后IdP再登会重建"),
    ("用户管理", "批量删除", "POST", "/api/v1/{realm}/users/batch-delete", "前端", "批量删除，返回成功/失败统计", '{"user_ids":["uuid1","uuid2"]}', "BatchOperationResponse", ""),
    ("用户管理", "重置密码", "PUT", "/api/v1/{realm}/users/{user_id}/password", "前端", "重置密码(temporary=true)，联邦用户返回400", '{"password":"..."}', "204", "联邦用户无法重置"),
    ("用户管理", "添加用户到组", "PUT", "/api/v1/{realm}/users/{user_id}/groups/{group_id}", "前端", "将用户加入指定组", "—", "204", ""),
    ("用户管理", "移除用户出组", "DELETE", "/api/v1/{realm}/users/{user_id}/groups/{group_id}", "前端", "将用户从组中移除", "—", "204", ""),
    ("用户管理", "用户可选组", "GET", "/api/v1/{realm}/users/{user_id}/available-groups", "前端", "所有组+joined标记，供前端勾选", "—", '[{"id","name","joined":bool,"source"}]', ""),
    ("用户管理", "CSV导入模板", "GET", "/api/v1/{realm}/users/import-template", "前端", "下载CSV模板", "—", "text/csv attachment", "username,password,email,..."),
    ("用户管理", "批量导入", "POST", "/api/v1/{realm}/users/batch-import", "前端", "上传CSV批量创建用户", "multipart/form-data file", "BatchOperationResponse", "UTF-8/BOM均支持"),

    # --- 用户组管理 ---
    ("用户组管理", "用户组列表", "GET", "/api/v1/{realm}/groups", "前端", "支持搜索/分页，含source/member_count", "search, first, max", "List[GroupListResponse]", "source: preset/app-preset/custom"),
    ("用户组管理", "创建用户组", "POST", "/api/v1/{realm}/groups", "前端", "创建组，可选绑用户和角色", '{"name","users":[],"roles":[]}', "GroupResponse (201)", ""),
    ("用户组管理", "用户组详情", "GET", "/api/v1/{realm}/groups/{group_id}", "前端", "成员+角色+权限(path_rules反查)", "—", "GroupDetailResponse(permissions)", "按app分组展示权限"),
    ("用户组管理", "修改用户组", "PUT", "/api/v1/{realm}/groups/{group_id}", "前端", "修改名称+全量同步成员/角色", '{"name","users":[],"roles":[]}', "204", "users/roles全量覆盖"),
    ("用户组管理", "删除用户组", "DELETE", "/api/v1/{realm}/groups/{group_id}", "前端", "删除自定义组，预置组返回400", "—", "204", "preset组不可删"),
    ("用户组管理", "批量添加成员", "POST", "/api/v1/{realm}/groups/{group_id}/members/batch-add", "前端", "批量将用户添加到组", '{"user_ids":["uid1","uid2"]}', "BatchOperationResponse", ""),
    ("用户组管理", "批量移除成员", "POST", "/api/v1/{realm}/groups/{group_id}/members/batch-remove", "前端", "批量从组中移除用户", '{"user_ids":["uid1"]}', "BatchOperationResponse", ""),
    ("用户组管理", "设置组权限", "PUT", "/api/v1/{realm}/groups/{group_id}/permissions", "前端", "全量替换组绑定的path_rules", '{"rule_ids":[1,3,5]}', '{"group_name","permissions":[]}', "多对多，OR语义"),

    # --- 权限管理 ---
    ("权限管理", "权限列表(按应用分组)", "GET", "/api/v1/{realm}/permissions", "前端", "所有path_rules按应用分组，含绑定的groups", "—", '[{"app_name","rules":[{id,path_prefix,description,groups}]}]', "供前端权限勾选用"),

    # --- 应用管理 ---
    ("应用管理", "应用列表", "GET", "/api/v1/apps", "前端/内部", "所有注册的应用", "—", "List[AppResponse]", "含resource_patterns"),
    ("应用管理", "注册应用", "POST", "/api/v1/apps", "内部(管理员)", "注册新应用+资源模式+自动建{app}-admins组", '{"app_name","path_prefix","resource_patterns":[]}', "AppResponse (201)", "通常由init脚本预置"),
    ("应用管理", "应用详情", "GET", "/api/v1/apps/{app_name}", "前端/内部", "单个应用详情", "—", "AppResponse", ""),
    ("应用管理", "修改应用", "PUT", "/api/v1/apps/{app_name}", "内部(管理员)", "修改应用信息/License开关", '{"display_name","enabled",...}', "AppResponse", ""),
    ("应用管理", "删除应用", "DELETE", "/api/v1/apps/{app_name}", "内部(管理员)", "下线应用", "—", "204", ""),

    # --- 路径规则 ---
    ("路径规则", "规则列表", "GET", "/api/v1/path-rules", "内部(管理员)", "所有路径规则（pep-proxy提供）", "—", "List[PathRuleResponse]", ""),
    ("路径规则", "创建规则", "POST", "/api/v1/path-rules", "内部(管理员)", "新建路径规则", '{"path_prefix","required_group","description"}', "PathRuleResponse (201)", "通常由init脚本预置"),
    ("路径规则", "规则详情", "GET", "/api/v1/path-rules/{rule_id}", "内部(管理员)", "单条规则", "—", "PathRuleResponse", ""),
    ("路径规则", "修改规则", "PUT", "/api/v1/path-rules/{rule_id}", "内部(管理员)", "修改路径规则", '{"path_prefix","required_group","description"}', "PathRuleResponse", ""),
    ("路径规则", "删除规则", "DELETE", "/api/v1/path-rules/{rule_id}", "内部(管理员)", "删除路径规则", "—", "204", ""),

    # --- API Key ---
    ("API Key", "创建Key", "POST", "/api/v1/{realm}/api-keys", "前端", "创建API Key，明文只返回一次", '{"name","scope","expires_at"}', "ApiKeyCreateResponse (201)", "含明文api_key字段"),
    ("API Key", "Key列表", "GET", "/api/v1/{realm}/api-keys", "前端", "列出所有Key(只显示前缀)", "—", "List[ApiKeyResponse]", "无明文"),
    ("API Key", "Key详情", "GET", "/api/v1/{realm}/api-keys/{key_id}", "前端", "单个Key详情", "—", "ApiKeyResponse", "无明文"),
    ("API Key", "修改Key", "PUT", "/api/v1/{realm}/api-keys/{key_id}", "前端", "修改Key信息", '{"name","scope","enabled"}', "ApiKeyResponse", ""),
    ("API Key", "删除Key", "DELETE", "/api/v1/{realm}/api-keys/{key_id}", "前端", "删除Key", "—", "204", ""),
    ("API Key", "轮换Key", "POST", "/api/v1/{realm}/api-keys/{key_id}/rotate", "前端", "轮换Key，subject_id不变", "—", "ApiKeyCreateResponse", "旧Key立即失效"),

    # --- 资源ACL ---
    ("资源ACL", "添加权限", "POST", "/acl/v1/resources/{resource_id}/permissions", "前端/应用", "给资源添加权限（分享）", '{"subject_type","subject_id","permission"}', "PermissionResponse", "resource-sync提供"),
    ("资源ACL", "查询权限", "GET", "/acl/v1/resources/{resource_id}/permissions", "前端/应用", "查看资源的权限成员列表", "—", "List[PermissionResponse]", ""),
    ("资源ACL", "修改权限", "PUT", "/acl/v1/resources/{resource_id}/permissions/{acl_id}", "前端/应用", "修改权限等级", '{"permission":"contributor"}', "PermissionResponse", ""),
    ("资源ACL", "删除权限", "DELETE", "/acl/v1/resources/{resource_id}/permissions/{acl_id}", "前端/应用", "回收权限", "—", "204", ""),

    # --- IdP/SAML 管理 ---
    ("IdP管理", "导入SAML元数据", "POST", "/api/v1/{realm}/idp/saml/import", "前端", "上传SAML元数据XML，解析IdP配置", "multipart/form-data file (XML)", "SAMLMetadataImportResponse", "返回解析出的config字段"),
    ("IdP管理", "创建IdP实例", "POST", "/api/v1/{realm}/idp/saml/instances", "前端", "创建SAML IdP实例（每realm仅一个）", '{"displayName","enabled","trustEmail","config":{}}', "IDPInstanceResponse (201)", "alias固定为da-saml-idp"),
    ("IdP管理", "修改IdP实例", "PUT", "/api/v1/{realm}/idp/saml/instances", "前端", "修改SAML IdP配置", '{"displayName","enabled","trustEmail","config":{}}', "IDPInstanceResponse", ""),
    ("IdP管理", "IdP实例列表", "GET", "/api/v1/{realm}/idp/saml/instances", "前端", "列出realm下所有IdP实例", "—", "List[IDPInstanceResponse]", ""),
    ("IdP管理", "删除IdP实例", "DELETE", "/api/v1/{realm}/idp/saml/instances/{alias}", "前端", "删除指定IdP实例", "—", "204", ""),
    ("IdP管理", "Mapper列表", "GET", "/api/v1/{realm}/idp/saml/instances/{alias}/mappers", "前端", "获取IdP的属性映射列表", "—", "List[IdPMapperResponse]", "简化返回"),
    ("IdP管理", "创建Mapper", "POST", "/api/v1/{realm}/idp/saml/instances/{alias}/mappers", "前端", "创建属性映射", '{"name","attributeKey","attributeValue","friendlyName"}', "IdPMapperResponse (201)", "固定saml-user-attribute-idp-mapper"),
    ("IdP管理", "修改Mapper", "PUT", "/api/v1/{realm}/idp/saml/instances/{alias}/mappers/{mapper_id}", "前端", "修改属性映射", '{"name","attributeKey","attributeValue","friendlyName"}', "204", "部分更新"),
    ("IdP管理", "删除Mapper", "DELETE", "/api/v1/{realm}/idp/saml/instances/{alias}/mappers/{mapper_id}", "前端", "删除属性映射", "—", "204", ""),

    # --- Token ---
    ("Token", "授权码换Token", "POST", "/api/v1/{realm}/token/exchange", "前端", "OIDC授权码换取access_token", '{"code","redirect_uri","client_id","client_secret"}', "TokenExchangeResponse", "client_secret可选"),
]

# ============================================================
# Sheet 2: App Configurations
# ============================================================
APP_HEADERS = ["应用名称", "显示名", "Gateway前缀", "Enabled", "resource_type", "id_source", "id_field", "resource_actions"]

APPS = [
    ("knowledgebase", "知识库", "/kb/", "是", "kb", "body", "KDSID", "POST /add→create@201\nPOST /remove→delete@2xx"),
    ("rubik", "智能问数 (RubikSQL)", "/rubik/", "是", "database", "path", "id", "POST→create@201\nDELETE /{id}→delete@2xx"),
    ("memory", "记忆库", "/memory/", "是", "memory", "path", "id", "默认RESTful"),
    ("httpbin", "HTTPBin Echo", "/anything/", "是", "item", "path", "id", "默认RESTful"),
]

RULE_HEADERS = ["ID", "应用", "路径前缀", "描述", "绑定的组", "鉴权效果"]

RULES = [
    # KB
    (3, "knowledgebase", "/kb/knowledge_bases/add", "知识库创建", "kb-admins", "仅kb-admins可调"),
    (4, "knowledgebase", "/kb/knowledge_bases/modify", "知识库修改", "kb-admins", ""),
    (5, "knowledgebase", "/kb/knowledge_bases/remove", "知识库删除", "kb-admins", ""),
    (6, "knowledgebase", "/kb/knowledge_bases/mappings/add", "目录映射创建", "kb-admins", ""),
    (7, "knowledgebase", "/kb/knowledge_bases/mappings/remove", "目录映射删除", "kb-admins", ""),
    (8, "knowledgebase", "/kb/knowledge_bases/files/upload", "文件上传", "kb-admins", ""),
    (9, "knowledgebase", "/kb/knowledge_bases/files/remove", "文件删除", "kb-admins", ""),
    (10, "knowledgebase", "/kb/knowledge_bases/files/filesystem/add", "文件系统创建", "kb-admins", ""),
    (11, "knowledgebase", "/kb/knowledge_bases/files/filesystem/remove", "文件系统删除", "kb-admins", ""),
    (12, "knowledgebase", "/kb/models/config/add", "模型配置新增", "kb-admins", ""),
    (13, "knowledgebase", "/kb/models/config/modify", "模型配置修改", "kb-admins", ""),
    (14, "knowledgebase", "/kb/models/config/remove", "模型配置删除", "kb-admins", ""),
    (15, "knowledgebase", "/kb/models/config/set", "模型配置启用", "kb-admins", ""),
    (16, "knowledgebase", "/kb/prompts/add", "提示词创建", "kb-admins", ""),
    (17, "knowledgebase", "/kb/prompts/modify", "提示词修改", "kb-admins", ""),
    (18, "knowledgebase", "/kb/prompts/remove", "提示词删除", "kb-admins", ""),
    (19, "knowledgebase", "/kb/jargon_groups/add", "黑话库创建", "kb-admins", ""),
    (20, "knowledgebase", "/kb/jargon_groups/remove", "黑话库删除", "kb-admins", ""),
    (21, "knowledgebase", "/kb/jargon_groups/knowledge_bases/add", "黑话库绑定知识库", "kb-admins", ""),
    (22, "knowledgebase", "/kb/jargon_groups/knowledge_bases/remove", "黑话库解绑知识库", "kb-admins", ""),
    (23, "knowledgebase", "/kb/jargons/add", "黑话创建", "kb-admins", ""),
    (24, "knowledgebase", "/kb/jargons/modify", "黑话修改", "kb-admins", ""),
    (25, "knowledgebase", "/kb/jargons/remove", "黑话删除", "kb-admins", ""),
    (26, "knowledgebase", "/kb/knowledge_bases/", "知识库资源操作（兜底）", "kb-admins", "startswith兜底：捕获所有/{kb_id}/...子路径"),
    # Rubik
    (33, "rubik", "/rubik/api/databases/knowledge/special", "添加特殊知识", "rubik-admins", "仅rubik-admins可调"),
    (36, "rubik", "/rubik/api/config/models/", "更新模型预设", "rubik-admins", ""),
    (37, "rubik", "/rubik/api/config/database-providers/", "更新数据库提供者", "rubik-admins", ""),
    (38, "rubik", "/rubik/api/config/language", "设置语言", "rubik-admins", ""),
    (39, "rubik", "/rubik/api/config/languages", "分别设置语言", "rubik-admins", ""),
    (40, "rubik", "/rubik/api/config/app/", "设置应用配置", "rubik-admins", ""),
    (41, "rubik", "/rubik/api/config/reload", "重载配置", "rubik-admins", ""),
    (42, "rubik", "/rubik/api/config/setup", "初始化配置", "rubik-admins", ""),
    (43, "rubik", "/rubik/api/config/llm-providers", "LLM提供者管理", "rubik-admins", ""),
    (44, "rubik", "/rubik/api/databases/", "数据库资源操作（兜底）", "rubik-admins", "startswith兜底：捕获所有/{db_id}/...子路径"),
]


def build():
    wb = Workbook()

    # ---- Sheet 1: IAM APIs ----
    ws1 = wb.active
    ws1.title = "IAM系统接口"
    _header(ws1, IAM_HEADERS)
    mod_fill = {
        "用户管理": PatternFill("solid", fgColor="D9E2F3"),
        "用户组管理": PatternFill("solid", fgColor="E2EFDA"),
        "权限管理": PatternFill("solid", fgColor="FFF2CC"),
        "应用管理": PatternFill("solid", fgColor="FCE4D6"),
        "路径规则": PatternFill("solid", fgColor="DDEBF7"),
        "API Key": PatternFill("solid", fgColor="F2DCDB"),
        "资源ACL": PatternFill("solid", fgColor="E4DFEC"),
        "IdP管理": PatternFill("solid", fgColor="D5E8D4"),
        "Token": PatternFill("solid", fgColor="DAE8FC"),
    }
    for r, api in enumerate(IAM_APIS, 2):
        _row(ws1, r, api, method_col=3)
        ws1.cell(row=r, column=1).fill = mod_fill.get(api[0], PatternFill())

    widths1 = {1: 12, 2: 22, 3: 8, 4: 48, 5: 14, 6: 40, 7: 36, 8: 40, 9: 28}
    for col, w in widths1.items():
        ws1.column_dimensions[get_column_letter(col)].width = w
    ws1.row_dimensions[1].height = 24
    for r in range(2, len(IAM_APIS) + 2):
        ws1.row_dimensions[r].height = 60
    ws1.freeze_panes = "E2"
    ws1.auto_filter.ref = f"A1:I{len(IAM_APIS) + 1}"

    # ---- Sheet 2: App Configurations ----
    ws2 = wb.create_sheet("应用接入配置")

    # 2a: 应用注册信息
    ws2.cell(row=1, column=1, value="已注册应用").font = Font(name="微软雅黑", size=12, bold=True)
    start = 2
    _header_at = lambda ws, row, headers: [
        setattr(ws.cell(row=row, column=c+1, value=h), 'font', hf) or
        setattr(ws.cell(row=row, column=c+1), 'fill', hfill) or
        setattr(ws.cell(row=row, column=c+1), 'alignment', center) or
        setattr(ws.cell(row=row, column=c+1), 'border', border)
        for c, h in enumerate(headers)
    ]
    _header_at(ws2, start, APP_HEADERS)
    for i, app in enumerate(APPS):
        r = start + 1 + i
        for c, v in enumerate(app, 1):
            cell = ws2.cell(row=r, column=c, value=v)
            cell.font = bf; cell.alignment = wrap; cell.border = border

    # 2b: 路径规则配置
    gap = start + len(APPS) + 3
    ws2.cell(row=gap, column=1, value=f"路径规则 (path_rules) — {len(RULES)}条").font = Font(name="微软雅黑", size=12, bold=True)
    _header_at(ws2, gap + 1, RULE_HEADERS)
    for i, rule in enumerate(RULES):
        r = gap + 2 + i
        for c, v in enumerate(rule, 1):
            cell = ws2.cell(row=r, column=c, value=v)
            cell.font = bf; cell.alignment = wrap; cell.border = border

    widths2 = {1: 16, 2: 18, 3: 48, 4: 20, 5: 16, 6: 20, 7: 14, 8: 30}
    for col, w in widths2.items():
        ws2.column_dimensions[get_column_letter(col)].width = w

    # ---- Save ----
    out = "diagrams/api-spec-v2.xlsx"
    wb.save(out)
    print(f"OK: {out} ({len(IAM_APIS)} IAM APIs, {len(APPS)} apps, {len(RULES)} rules)")


if __name__ == "__main__":
    build()
