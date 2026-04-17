"""Generate api-spec.xlsx for all user/group management endpoints."""
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

HEADERS = [
    "模块", "接口名称", "Method", "路径", "路径参数", "查询参数",
    "请求体 (JSON)", "Content-Type", "响应码", "响应体 (JSON)", "说明", "备注",
]

APIS = [
    # --- 用户管理 ---
    ("用户管理", "用户列表", "GET", "/api/v1/{realm}/users",
     "realm: aidp",
     "search: 可选, 模糊搜用户名/邮箱\ngroup_id: 可选, 按组过滤\nfirst: 分页起始 (默认 0)\nmax: 每页条数 (默认 50)",
     "—", "—", "200",
     '[{"id","username","email","firstName","lastName","enabled","createdTimestamp","account_type","groups":[{"id","name"}],...}]',
     "返回增强用户列表，含 account_type (internal/federated) 和所属组",
     "account_type 由 federationLink 字段推导"),

    ("用户管理", "用户详情", "GET", "/api/v1/{realm}/users/{user_id}/details",
     "realm: aidp\nuser_id: 用户UUID",
     "—", "—", "—", "200",
     '{"id","username","email","account_type","groups":[...],"permissions":[{"app_name","path_prefix","required_group","description"},...]}',
     "返回用户完整信息 + 所属组 + 路径权限聚合（从 path_rules 表按组反查）",
     "permissions 按 app_name 分组展示"),

    ("用户管理", "创建用户", "POST", "/api/v1/{realm}/users",
     "realm: aidp", "—",
     '{"username":"必填","password":"必填","email":"可选","firstName":"可选","lastName":"可选","groups":["group_id1"]}',
     "application/json", "201",
     '{"id","username","account_type":"internal","groups":[...],...}',
     "创建内部用户，设置临时密码(首次登录改密)，可选绑定组",
     "密码 temporary=true"),

    ("用户管理", "修改用户", "PUT", "/api/v1/{realm}/users/{user_id}",
     "realm: aidp\nuser_id: UUID", "—",
     '{"firstName":"可选","lastName":"可选","email":"可选","enabled":true/false}',
     "application/json", "200",
     '{"id","username","account_type",...}',
     "修改用户基本信息", "联邦用户也可改，但下次 IdP 登录可能覆盖"),

    ("用户管理", "删除用户", "DELETE", "/api/v1/{realm}/users/{user_id}",
     "realm: aidp\nuser_id: UUID", "—", "—", "—", "204",
     "—", "删除单个用户", "联邦用户删后 IdP 再登录会重建"),

    ("用户管理", "批量删除用户", "POST", "/api/v1/{realm}/users/batch-delete",
     "realm: aidp", "—",
     '{"user_ids":["uuid1","uuid2",...]}',
     "application/json", "200",
     '{"succeeded":3,"failed":0,"errors":[]}',
     "批量删除，逐个执行，返回成功/失败统计", ""),

    ("用户管理", "重置密码", "PUT", "/api/v1/{realm}/users/{user_id}/password",
     "realm: aidp\nuser_id: UUID", "—",
     '{"password":"NewP@ssw0rd"}',
     "application/json", "204",
     "—", "重置用户密码 (temporary=true, 首次登录改密)",
     "联邦用户返回 400"),

    ("用户管理", "添加用户到组", "PUT", "/api/v1/{realm}/users/{user_id}/groups/{group_id}",
     "realm: aidp\nuser_id: UUID\ngroup_id: UUID",
     "—", "—", "—", "204", "—",
     "将用户加入指定组", ""),

    ("用户管理", "移除用户出组", "DELETE", "/api/v1/{realm}/users/{user_id}/groups/{group_id}",
     "realm: aidp\nuser_id: UUID\ngroup_id: UUID",
     "—", "—", "—", "204", "—",
     "将用户从组中移除", ""),

    ("用户管理", "CSV导入模板下载", "GET", "/api/v1/{realm}/users/import-template",
     "realm: aidp", "—", "—", "—", "200",
     "CSV 文件下载\nusername,password,email,firstName,lastName,groups",
     "下载 CSV 导入模板文件",
     "Content-Type: text/csv\nContent-Disposition: attachment"),

    ("用户管理", "批量导入用户(CSV)", "POST", "/api/v1/{realm}/users/batch-import",
     "realm: aidp", "—",
     "multipart/form-data\nfield: file (CSV文件)",
     "multipart/form-data", "200",
     '{"succeeded":3,"failed":1,"errors":[{"index":2,"username":"dup","error":"409..."}]}',
     "上传 CSV 文件批量创建用户，逐行解析执行",
     "CSV 编码 UTF-8 / UTF-8 BOM 均支持"),

    # --- 用户组管理 ---
    ("用户组管理", "用户组列表", "GET", "/api/v1/{realm}/groups",
     "realm: aidp",
     "search: 可选, 模糊搜组名\nfirst: 分页起始 (默认 0)\nmax: 每页条数 (默认 50, 最大 500)",
     "—", "—", "200",
     '[{"id","name","source":"preset|app-preset|custom","member_count":5,"subGroups":[...]}]',
     "返回顶级组，含 source 来源标注和成员数，支持搜索和分页",
     "source: preset=系统预置, app-preset=应用预置, custom=自定义"),

    ("用户组管理", "创建用户组", "POST", "/api/v1/{realm}/groups",
     "realm: aidp", "—",
     '{"name":"dev-team","users":["uid1"],"roles":["role-name"]}',
     "application/json", "201",
     '{"id","name","subGroups":[]}',
     "创建自定义组，可选同时绑定用户和角色", ""),

    ("用户组管理", "用户组详情", "GET", "/api/v1/{realm}/groups/{group_id}",
     "realm: aidp\ngroup_id: UUID", "—", "—", "—", "200",
     '{"id","name","source","member_count","members":[{"id","username","email","account_type"}],"roles":[...],"permissions":[{"app_name","path_prefix","required_group","description"}]}',
     "返回组详情：成员列表 + 角色 + 关联的路径权限(path_rules)",
     "permissions 从 path_rules 表按 required_group=组名 反查"),

    ("用户组管理", "修改用户组", "PUT", "/api/v1/{realm}/groups/{group_id}",
     "realm: aidp\ngroup_id: UUID", "—",
     '{"name":"可选","users":["uid1","uid2"],"roles":["role1"]}',
     "application/json", "204",
     "—", "修改组信息，同步成员和角色（全量覆盖）", ""),

    ("用户组管理", "删除用户组", "DELETE", "/api/v1/{realm}/groups/{group_id}",
     "realm: aidp\ngroup_id: UUID", "—", "—", "—", "204",
     "—", "删除自定义组", "预置组返回 400 Cannot delete preset group"),

    ("用户组管理", "批量添加成员", "POST", "/api/v1/{realm}/groups/{group_id}/members/batch-add",
     "realm: aidp\ngroup_id: UUID", "—",
     '{"user_ids":["uid1","uid2"]}',
     "application/json", "200",
     '{"succeeded":2,"failed":0,"errors":[]}',
     "批量将用户添加到组", ""),

    ("用户组管理", "批量移除成员", "POST", "/api/v1/{realm}/groups/{group_id}/members/batch-remove",
     "realm: aidp\ngroup_id: UUID", "—",
     '{"user_ids":["uid1"]}',
     "application/json", "200",
     '{"succeeded":1,"failed":0,"errors":[]}',
     "批量从组中移除用户", ""),
]


def build():
    wb = Workbook()
    ws = wb.active
    ws.title = "用户管理 API"

    hf = Font(name="微软雅黑", size=11, bold=True, color="FFFFFF")
    hfill = PatternFill("solid", fgColor="2F5496")
    border = Border(*[Side("thin", color="BFBFBF")] * 4)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    wrap = Alignment(horizontal="left", vertical="top", wrap_text=True)

    for c, h in enumerate(HEADERS, 1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.font = hf; cell.fill = hfill; cell.alignment = center; cell.border = border

    method_fill = {
        "GET": PatternFill("solid", fgColor="C6EFCE"),
        "POST": PatternFill("solid", fgColor="BDD7EE"),
        "PUT": PatternFill("solid", fgColor="FCE4D6"),
        "DELETE": PatternFill("solid", fgColor="FFC7CE"),
    }
    mod_fill = {
        "用户管理": PatternFill("solid", fgColor="D9E2F3"),
        "用户组管理": PatternFill("solid", fgColor="E2EFDA"),
    }

    for r, api in enumerate(APIS, 2):
        for c, v in enumerate(api, 1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.font = Font(name="微软雅黑", size=10)
            cell.alignment = wrap if c > 4 else center
            cell.border = border
        ws.cell(row=r, column=1).fill = mod_fill.get(api[0], PatternFill())
        ws.cell(row=r, column=3).fill = method_fill.get(api[2], PatternFill())

    widths = {1:12, 2:18, 3:8, 4:42, 5:20, 6:30, 7:40, 8:16, 9:8, 10:50, 11:36, 12:28}
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = w
    ws.row_dimensions[1].height = 24
    for r in range(2, len(APIS)+2):
        ws.row_dimensions[r].height = 80

    ws.freeze_panes = "E2"
    ws.auto_filter.ref = f"A1:L{len(APIS)+1}"

    out = "diagrams/api-spec-v2.xlsx"
    wb.save(out)
    print(f"OK: {out}  ({len(APIS)} APIs)")


if __name__ == "__main__":
    build()
