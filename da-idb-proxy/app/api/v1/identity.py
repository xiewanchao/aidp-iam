from fastapi import APIRouter, Depends, HTTPException, status
from typing import List
from app.core.keycloak import kc
from app.schemas.roles import RoleCreate, RoleUpdate, RoleResponse, RoleUpdateByIdRequest
from app.schemas.groups import GroupCreate, GroupUpdate, GroupResponse, GroupDetailResponse
from app.schemas.users import UserResponse, UserContextResponse
from app.api.v1.common import skip_master_realm


router = APIRouter(prefix="/{realm}", tags=["Identity"], dependencies=[Depends(skip_master_realm)])


def is_internal_role(role_name: str) -> bool:
    """
    判断是否为 Keycloak 内置角色
    1. 过滤默认生成的 default-roles-{realm}
    2. 过滤常见的内置管理角色名
    """
    internal_prefixes = ["default-roles-", "offline_access", "uma_authorization"]
    # 如果角色名以这些开头，或者是常见的内置角色，则拦截
    return any(role_name.startswith(p) for p in internal_prefixes)


# --- Roles ---
@router.get("/roles", response_model=List[RoleResponse])
def list_roles(realm: str):
    roles = kc.request("GET", f"/realms/{realm}/roles").json()
    # 过滤掉系统内置的 Client Roles，只看 Realm Roles
    return [
        r for r in roles
        if not r.get('clientRole') and not is_internal_role(r.get('name', ''))
    ]


@router.post("/roles", status_code=status.HTTP_201_CREATED, response_model=RoleResponse)
def create_role(realm: str, role: RoleCreate):
    # 转换模型为 JSON，排除空字段
    payload = role.model_dump(exclude_none=True)
    kc.request("POST", f"/realms/{realm}/roles", json=payload)
    # Return the created role by fetching it
    return kc.request("GET", f"/realms/{realm}/roles/{role.name}").json()


@router.get("/roles/{role_name}", response_model=RoleResponse)
def get_role(realm: str, role_name: str):
    """补全：获取单个角色详情"""
    return kc.request("GET", f"/realms/{realm}/roles/{role_name}").json()


@router.put("/roles/{role_name}", response_model=RoleResponse)
def update_role(realm: str, role_name: str, role_update: RoleUpdate):
    """补全：更新角色"""
    current = kc.request("GET", f"/realms/{realm}/roles/{role_name}").json()
    update_data = role_update.model_dump(exclude_none=True)
    current.update(update_data)
    kc.request("PUT", f"/realms/{realm}/roles/{role_name}", json=current)
    return kc.request("GET", f"/realms/{realm}/roles/{role_name}").json()


@router.delete("/roles/{role_name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(realm: str, role_name: str):
    """补全：删除角色"""
    kc.request("DELETE", f"/realms/{realm}/roles/{role_name}")
    return None


'''
START: 问数客户要求使用uuid管理roles，需要订制by-id接口
'''
@router.get("/roles/by-id/{role_id}") #TODO: /roles/by-id
def get_role_by_id(realm: str, role_id: str):
    """通过 UUID 获取角色详情"""
    # 转发给 Keycloak 的标准 roles-by-id 路径
    return kc.request("GET", f"/realms/{realm}/roles-by-id/{role_id}").json()


@router.put("/roles/by-id/{role_id}", response_model=RoleResponse)
def update_role_by_id(realm: str, role_id: str, payload: RoleUpdateByIdRequest):
    """
    通过 UUID 修改角色信息（支持改名）
    :param realm: "my-realm"
    :param role_id: "uuid-xxx"
    :param payload: RoleUpdateByIdRequest with optional fields
    """
    # 1. 先获取当前角色完整对象（防止覆盖掉隐藏属性）
    check = kc.request("GET", f"/realms/{realm}/roles-by-id/{role_id}")
    if check.status_code == 404:
        raise HTTPException(status_code=404, detail="Role not found")

    current_role = check.json()

    # 2. 合并更新
    update_data = payload.model_dump(exclude_none=True)
    current_role.update(update_data)

    # 3. 发送更新 (Keycloak 规范：roles-by-id 路径使用 PUT)
    # 注意：即便改了 name，这个 id 依然有效
    res = kc.request("PUT", f"/realms/{realm}/roles-by-id/{role_id}", json=current_role)

    if res.status_code not in [200, 204]:
        raise HTTPException(status_code=res.status_code, detail=res.text)

    # Return the updated role
    return kc.request("GET", f"/realms/{realm}/roles-by-id/{role_id}").json()


@router.delete("/roles/by-id/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role_by_id(realm: str, role_id: str):
    """通过 UUID 删除角色"""
    res = kc.request("DELETE", f"/realms/{realm}/roles-by-id/{role_id}")

    if res.status_code == 404:
        raise HTTPException(status_code=404, detail="Role not found")

    return None
'''
END: 问数客户要求使用uuid管理roles，需要订制by-id接口
'''

# --- Groups ---
@router.get("/groups", response_model=List[GroupResponse])
def list_groups(realm: str):
    """获取所有顶级组及其子树"""
    return kc.request("GET", f"/realms/{realm}/groups").json()


# 辅助工具：同步 Group 的 Users
def sync_group_users(realm: str, group_id: str, target_user_ids: List[str]):
    # 1. 获取当前成员
    current_members = kc.request("GET", f"/realms/{realm}/groups/{group_id}/members").json()
    current_ids = {m['id'] for m in current_members}
    target_ids = set(target_user_ids)

    # 2. 移除不再需要的
    for uid in current_ids - target_ids:
        kc.request("DELETE", f"/realms/{realm}/users/{uid}/groups/{group_id}")

    # 3. 添加新增的
    for uid in target_ids - current_ids:
        kc.request("PUT", f"/realms/{realm}/users/{uid}/groups/{group_id}")


# 辅助工具：同步 Group 的 Roles
def sync_group_roles(realm: str, group_id: str, target_role_names: List[str]):
    # 1. 获取当前角色映射
    mappings = kc.request("GET", f"/realms/{realm}/groups/{group_id}/role-mappings/realm").json()
    current_names = {r['name'] for r in mappings}
    target_names = set(target_role_names)

    # 2. 移除角色
    to_delete = [r for r in mappings if r['name'] in (current_names - target_names)]
    if to_delete:
        kc.request("DELETE", f"/realms/{realm}/groups/{group_id}/role-mappings/realm", json=to_delete)

    # 3. 添加角色 (需要先获取角色的完整对象)
    to_add_names = target_names - current_names
    if to_add_names:
        roles_to_add = []
        for name in to_add_names:
            role_obj = kc.request("GET", f"/realms/{realm}/roles/{name}").json()
            roles_to_add.append(role_obj)
        kc.request("POST", f"/realms/{realm}/groups/{group_id}/role-mappings/realm", json=roles_to_add)


@router.post("/groups", status_code=status.HTTP_201_CREATED, response_model=GroupResponse)
def create_group(realm: str, group: GroupCreate):
    payload = group.model_dump(exclude={"users", "roles"}, exclude_none=True)
    resp = kc.request("POST", f"/realms/{realm}/groups", json=payload)

    new_group = next(g for g in kc.request("GET", f"/realms/{realm}/groups").json() if g['name'] == group.name)
    group_id = new_group['id']

    if group.users is not None:
        sync_group_users(realm, group_id, group.users)
    if group.roles is not None:
        sync_group_roles(realm, group_id, group.roles)

    return new_group


@router.put("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def update_group(realm: str, group_id: str, group_update: GroupUpdate):
    current = kc.request("GET", f"/realms/{realm}/groups/{group_id}").json()
    base_data = group_update.model_dump(exclude={"users", "roles"}, exclude_none=True)
    current.update(base_data)
    kc.request("PUT", f"/realms/{realm}/groups/{group_id}", json=current)

    if group_update.users is not None:
        sync_group_users(realm, group_id, group_update.users)

    if group_update.roles is not None:
        sync_group_roles(realm, group_id, group_update.roles)

    return None


@router.get("/groups/{group_id}", response_model=GroupDetailResponse)
def get_group_detail(realm: str, group_id: str):
    """获取 Group 详情：基础 + 成员(仅名) + 角色(过滤内置)"""

    # 1. 基础信息
    group_base = kc.request("GET", f"/realms/{realm}/groups/{group_id}").json()

    # 2. 获取成员并清洗字段
    raw_members = kc.request("GET", f"/realms/{realm}/groups/{group_id}/members").json()
    # 显式提取，确保只给前端 id 和 username
    members = [{"id": m["id"], "username": m["username"]} for m in raw_members]

    # 3. 获取角色映射并过滤
    role_mappings = kc.request("GET", f"/realms/{realm}/groups/{group_id}/role-mappings").json()
    # Keycloak 返回的 realmMappings 结构通常是 [{'id': '...', 'name': '...'}, ...]
    realm_roles = role_mappings.get("realmMappings", [])

    # 过滤掉内置角色 (如 default-roles-xxx)
    filtered_roles = [r for r in realm_roles if not is_internal_role(r['name'])]

    # 4. 组装返回，FastAPI 会自动根据 RoleResponse 过滤 roles 里的多余字段
    return {
        "id": group_base["id"],
        "name": group_base["name"],
        "members": members,
        "roles": filtered_roles
    }


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(realm: str, group_id: str):
    """删除组"""
    kc.request("DELETE", f"/realms/{realm}/groups/{group_id}")
    return None


# --- Users ---
@router.get("/users", response_model=List[UserResponse])
def list_users(realm: str):
    return kc.request("GET", f"/realms/{realm}/users").json()


@router.get("/users/{user_id}/details", response_model=UserContextResponse)
def get_user_full_context(realm: str, user_id: str):
    """
    获取用户的完整上下文：所属组 + 拥有的角色 (已过滤内置角色)
    注意：此接口已通过 router 级别的 skip_master_realm 依赖自动拦截 master
    """
    # 1. 获取用户所属的组
    groups = kc.request("GET", f"/realms/{realm}/users/{user_id}/groups").json()

    # 2. 获取用户的角色映射
    # Keycloak 返回结构: {"realmMappings": [...], "clientMappings": {...}}
    role_mappings = kc.request("GET", f"/realms/{realm}/users/{user_id}/role-mappings").json()

    # 3. 提取 Realm 级别角色并过滤内置角色
    realm_roles = role_mappings.get("realmMappings", [])
    filtered_roles = [
        r for r in realm_roles
        if not is_internal_role(r.get("name", ""))
    ]

    # 4. (可选) 如果你也需要过滤 Client 级别的内置角色，可以在这里处理 clientMappings
    # 目前根据你的需求，我们重点拦截 Realm 级别的内置角色

    return {
        "groups": groups,
        "roles": filtered_roles
    }
