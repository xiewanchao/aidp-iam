import csv
import io
from fastapi import APIRouter, Depends, HTTPException, status, Request, UploadFile, File, Query
from fastapi.responses import StreamingResponse
from typing import List, Optional
from app.core.keycloak import kc
from app.core.db import get_pool
from app.schemas.roles import RoleCreate, RoleUpdate, RoleResponse, RoleUpdateByIdRequest
from app.schemas.groups import (
    GroupCreate, GroupUpdate, GroupResponse, GroupDetailResponse,
    GroupListResponse, BatchMembersRequest, GroupPermission,
)
from app.schemas.users import (
    UserResponse, UserContextResponse,
    UserCreateRequest, UserUpdateRequest, PasswordResetRequest,
    BatchDeleteRequest, UserListResponse, UserDetailResponse,
    BatchImportRequest, BatchOperationResponse,
)
from app.api.v1.common import skip_master_realm
from app.utils.opa import get_role_policy, bind_policy_to_role, update_role_policy, unbind_policy_from_role
from app.core.opa_client import OPAError


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
def list_roles(realm: str, request: Request):
    roles = kc.request("GET", f"/realms/{realm}/roles").json()

    # 过滤掉系统内置的 Client Roles，只看 Realm Roles
    filtered_roles = [
        r for r in roles
        if not r.get('clientRole') and not is_internal_role(r.get('name', ''))
    ]

    # 获取每个角色的绑定策略信息
    for role in filtered_roles:
        role_id = role.get('id')
        try:
            policy = get_role_policy(role_id, realm)
            if policy:
                role['policy'] = policy.model_dump()
        except Exception:
            pass

    return filtered_roles


@router.post("/roles", status_code=status.HTTP_201_CREATED, response_model=RoleResponse)
def create_role(realm: str, role: RoleCreate, request: Request):
    # 转换模型为 JSON，排除空字段（注意排除 policy_id，因为它不是 Keycloak 字段）
    policy_id = role.policy_id
    payload = role.model_dump(exclude_none=True, exclude={'policy_id'})
    kc.request("POST", f"/realms/{realm}/roles", json=payload)

    created_role = kc.request("GET", f"/realms/{realm}/roles/{role.name}").json()
    role_id = created_role['id']

    # 如果需要绑定策略
    if policy_id:
        try:
            bind_policy_to_role(role_id, policy_id, realm)
            # 获取绑定后的策略信息
            policy = get_role_policy(role_id, realm)
            if policy:
                created_role['policy'] = policy.model_dump()
        except Exception as e:
            # OPA绑定失败，回滚：删除刚创建的Keycloak角色
            kc.request("DELETE", f"/realms/{realm}/roles_by_id/{role_id}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to bind policy to role: {str(e)}. Role creation has been rolled back."
            )

    return created_role


@router.get("/roles/{role_name}", response_model=RoleResponse)
def get_role(realm: str, role_name: str, request: Request):
    role = kc.request("GET", f"/realms/{realm}/roles/{role_name}").json()

# 获取绑定策略信息
    role_id = role.get('id')
    try:
        policy = get_role_policy(role_id, realm)
        if policy:
            role['policy'] = policy.model_dump()
    except Exception:
        pass

    return role


@router.put("/roles/{role_name}", response_model=RoleResponse)
def update_role(realm: str, role_name: str, role_update: RoleUpdate, request: Request):
    # 提取policy_id并从update_data中移除（它不是Keycloak字段）
    new_policy_id = role_update.policy_id
    update_data = role_update.model_dump(exclude_none=True)
    update_data.pop('policy_id', None)

    # 快照原始角色状态用于回滚
    original = kc.request("GET", f"/realms/{realm}/roles/{role_name}").json()

    current = original.copy()
    current.update(update_data)
    kc.request("PUT", f"/realms/{realm}/roles/{role_name}", json=current)

    role_id = current.get('id') or original.get('id')

    if new_policy_id is not None:
        try:
            try:
                update_role_policy(role_id, new_policy_id, realm)
            except OPAError as oe:
                if oe.status_code == 404:
                    bind_policy_to_role(role_id, new_policy_id, realm)
                else:
                    raise
        except Exception as e:
            # OPA更新失败，回滚Keycloak更改
            kc.request("PUT", f"/realms/{realm}/roles/{role_name}", json=original)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to update policy binding: {str(e)}. Role update has been rolled back."
            )

    updated_role = kc.request("GET", f"/realms/{realm}/roles/{role_name}").json()

    try:
        if new_policy_id is not None:
            policy = get_role_policy(role_id, realm)
            if policy:
                updated_role['policy'] = policy.model_dump()
        else:
            # 没有指定新policy,但仍尝试获取当前绑定
            policy = get_role_policy(role_id, realm)
            if policy:
                updated_role['policy'] = policy.model_dump()
    except Exception:
        pass

    return updated_role
@router.delete("/roles/{role_name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(realm: str, role_name: str, request: Request):
    """补全：删除角色"""
    role = kc.request("GET", f"/realms/{realm}/roles/{role_name}").json()
    role_id = role['id']

    try:
        unbind_policy_from_role(role_id, realm)
    except Exception:
        pass

    kc.request("DELETE", f"/realms/{realm}/roles/{role_name}")
    return None


'''
START: 问数客户要求使用uuid管理roles，需要订制by-id接口
'''
@router.get("/roles/by-id/{role_id}")
def get_role_by_id(realm: str, role_id: str, request: Request):
    # 转发给 Keycloak 的标准 roles-by-id 路径
    role = kc.request("GET", f"/realms/{realm}/roles-by-id/{role_id}").json()

# 获取绑定策略信息
    try:
        policy = get_role_policy(role_id, realm)
        if policy:
            role['policy'] = policy.model_dump()
    except Exception:
        pass

    return role


@router.put("/roles/by-id/{role_id}", response_model=RoleResponse)
def update_role_by_id(realm: str, role_id: str, payload: RoleUpdateByIdRequest, request: Request):
    """
    Through UUID update role information (supports rename)
    """
    check = kc.request("GET", f"/realms/{realm}/roles-by-id/{role_id}")
    if check.status_code == 404:
        raise HTTPException(status_code=404, detail="Role not found")

    original = check.json()

    update_data = payload.model_dump(exclude_none=True)
    update_data.pop('policy_id', None)

    new_policy_id = payload.policy_id

    current_role = original.copy()
    current_role.update(update_data)

    res = kc.request("PUT", f"/realms/{realm}/roles-by-id/{role_id}", json=current_role)

    if res.status_code not in [200, 204]:
        raise HTTPException(status_code=res.status_code, detail=res.text)

    if new_policy_id is not None:
        try:
            try:
                update_role_policy(role_id, new_policy_id, realm)
            except OPAError as oe:
                if oe.status_code == 404:
                    bind_policy_to_role(role_id, new_policy_id, realm)
                else:
                    raise
        except Exception as e:
            kc.request("PUT", f"/realms/{realm}/roles-by-id/{role_id}", json=original)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to update policy binding: {str(e)}. Role update has been rolled back."
            )

    updated_role = kc.request("GET", f"/realms/{realm}/roles-by-id/{role_id}").json()

    try:
        if new_policy_id is not None:
            policy = get_role_policy(role_id, realm)
            if policy:
                updated_role['policy'] = policy.model_dump()
        else:
            policy = get_role_policy(role_id, realm)
            if policy:
                updated_role['policy'] = policy.model_dump()
    except Exception:
        pass

    return updated_role


@router.delete("/roles/by-id/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role_by_id(realm: str, role_id: str, request: Request):
    """通过 UUID 删除角色"""
    check = kc.request("GET", f"/realms/{realm}/roles-by-id/{role_id}")
    if check.status_code == 404:
        raise HTTPException(status_code=404, detail="Role not found")

    try:
        unbind_policy_from_role(role_id, realm)
    except Exception:
        pass

    res = kc.request("DELETE", f"/realms/{realm}/roles-by-id/{role_id}")
    if res.status_code == 404:
        raise HTTPException(status_code=404, detail="Role not found")

    return None
'''
END: 问数客户要求使用uuid管理roles，需要订制by-id接口
'''

PRESET_GROUPS = {"admins", "all-users"}

def _group_source(name: str) -> str:
    if name in PRESET_GROUPS:
        return "preset"
    if name.endswith("-admins") and name != "admins":
        return "app-preset"
    return "custom"


def _enrich_group(realm: str, g: dict) -> dict:
    g["source"] = _group_source(g["name"])
    members = kc.request("GET", f"/realms/{realm}/groups/{g['id']}/members").json()
    g["member_count"] = len(members)
    for sg in g.get("subGroups", []):
        _enrich_group(realm, sg)
    return g


# --- Groups ---
@router.get("/groups", response_model=List[GroupListResponse])
def list_groups(
    realm: str,
    search: Optional[str] = Query(None, description="模糊搜索组名"),
    first: int = Query(0, ge=0, description="分页起始位置"),
    max: int = Query(50, ge=1, le=500, description="每页条数"),
):
    """获取顶级组，附加 source 和 member_count，支持搜索和分页"""
    params: dict = {"first": first, "max": max}
    if search:
        params["search"] = search
    groups = kc.request("GET", f"/realms/{realm}/groups", params=params).json()
    return [_enrich_group(realm, g) for g in groups]


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
async def get_group_detail(realm: str, group_id: str):
    """获取 Group 详情：基础 + 成员 + 角色 + 权限(path_rules)"""

    group_base = kc.request("GET", f"/realms/{realm}/groups/{group_id}").json()
    group_name = group_base["name"]

    raw_members = kc.request("GET", f"/realms/{realm}/groups/{group_id}/members").json()
    members = [
        {
            "id": m["id"],
            "username": m["username"],
            "email": m.get("email"),
            "account_type": "federated" if m.get("federationLink") else "internal",
        }
        for m in raw_members
    ]

    role_mappings = kc.request("GET", f"/realms/{realm}/groups/{group_id}/role-mappings").json()
    realm_roles = role_mappings.get("realmMappings", [])
    filtered_roles = [r for r in realm_roles if not is_internal_role(r['name'])]

    permissions = []
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT pr.id, pr.path_prefix, pr.required_group, pr.description,
                       a.app_name, a.display_name as app_display_name
                FROM path_rules pr
                LEFT JOIN apps a ON pr.path_prefix LIKE a.path_prefix || '%'
                WHERE pr.required_group = $1
                ORDER BY a.app_name, pr.path_prefix
            """, group_name)
            permissions = [dict(r) for r in rows]
    except Exception:
        pass

    return {
        "id": group_base["id"],
        "name": group_name,
        "source": _group_source(group_name),
        "member_count": len(members),
        "members": members,
        "roles": filtered_roles,
        "permissions": permissions,
    }


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(realm: str, group_id: str):
    """删除组（预置组不可删）"""
    group = kc.request("GET", f"/realms/{realm}/groups/{group_id}").json()
    if _group_source(group["name"]) == "preset":
        raise HTTPException(status_code=400, detail="Cannot delete preset group")
    kc.request("DELETE", f"/realms/{realm}/groups/{group_id}")
    return None


@router.post("/groups/{group_id}/members/batch-add", response_model=BatchOperationResponse)
def batch_add_members(realm: str, group_id: str, body: BatchMembersRequest):
    """批量添加用户到组"""
    succeeded = 0
    failed = 0
    errors = []
    for uid in body.user_ids:
        try:
            resp = kc.request("PUT", f"/realms/{realm}/users/{uid}/groups/{group_id}")
            if resp.status_code < 300:
                succeeded += 1
            else:
                failed += 1
                errors.append({"user_id": uid, "error": resp.text})
        except Exception as e:
            failed += 1
            errors.append({"user_id": uid, "error": str(e)})
    return {"succeeded": succeeded, "failed": failed, "errors": errors}


@router.post("/groups/{group_id}/members/batch-remove", response_model=BatchOperationResponse)
def batch_remove_members(realm: str, group_id: str, body: BatchMembersRequest):
    """批量从组中移除用户"""
    succeeded = 0
    failed = 0
    errors = []
    for uid in body.user_ids:
        try:
            resp = kc.request("DELETE", f"/realms/{realm}/users/{uid}/groups/{group_id}")
            if resp.status_code < 300:
                succeeded += 1
            else:
                failed += 1
                errors.append({"user_id": uid, "error": resp.text})
        except Exception as e:
            failed += 1
            errors.append({"user_id": uid, "error": str(e)})
    return {"succeeded": succeeded, "failed": failed, "errors": errors}


# --- Users ---


def _enrich_user(realm: str, user: dict) -> dict:
    """Add account_type and groups to a raw Keycloak user dict."""
    user["account_type"] = "federated" if user.get("federationLink") else "internal"
    user_groups = kc.request("GET", f"/realms/{realm}/users/{user['id']}/groups").json()
    user["groups"] = [{"id": g["id"], "name": g["name"]} for g in user_groups]
    return user


def _create_single_user(realm: str, req: UserCreateRequest) -> dict:
    """
    Create one user in Keycloak: account + password + group bindings.
    Returns the created user dict. Raises on failure.
    """
    payload = {
        "username": req.username,
        "enabled": True,
    }
    if req.email is not None:
        payload["email"] = req.email
    if req.firstName is not None:
        payload["firstName"] = req.firstName
    if req.lastName is not None:
        payload["lastName"] = req.lastName

    resp = kc.request("POST", f"/realms/{realm}/users", json=payload)
    if resp.status_code != 201:
        detail = resp.text
        try:
            detail = resp.json().get("errorMessage", resp.text)
        except Exception:
            pass
        raise HTTPException(status_code=resp.status_code, detail=detail)

    user_id = resp.headers["Location"].split("/")[-1]

    # Set initial password (temporary)
    kc.request("PUT", f"/realms/{realm}/users/{user_id}/reset-password", json={
        "type": "password",
        "value": req.password,
        "temporary": True,
    })

    # Bind groups
    if req.groups:
        for gid in req.groups:
            kc.request("PUT", f"/realms/{realm}/users/{user_id}/groups/{gid}")

    created_user = kc.request("GET", f"/realms/{realm}/users/{user_id}").json()
    return created_user


@router.get("/users/import-template")
def download_import_template(realm: str):
    """Download a CSV template for batch user import."""
    csv_content = (
        "username,password,email,firstName,lastName,groups\n"
        "example_user,P@ssw0rd123,user@example.com,John,Doe,\"admins,all-users\"\n"
    )
    return StreamingResponse(
        io.StringIO(csv_content),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=user_import_template.csv"},
    )


@router.get("/users", response_model=List[UserListResponse])
def list_users(
    realm: str,
    search: Optional[str] = Query(None, description="Search by username, email, first/last name"),
    group_id: Optional[str] = Query(None, description="Filter by group ID"),
    first: int = Query(0, ge=0, description="Pagination offset"),
    max: int = Query(50, ge=1, le=500, description="Page size"),
):
    """List users with optional search, group filter, and pagination."""
    params: dict = {"first": first, "max": max}
    if search:
        params["search"] = search

    users = kc.request("GET", f"/realms/{realm}/users", params=params).json()

    enriched = [_enrich_user(realm, u) for u in users]

    if group_id:
        enriched = [
            u for u in enriched
            if any(g["id"] == group_id for g in u["groups"])
        ]

    return enriched


@router.get("/users/{user_id}/details", response_model=UserDetailResponse)
async def get_user_full_context(realm: str, user_id: str):
    """
    Get full user detail: basic info + account type + groups + path-rule permissions.
    """
    user = kc.request("GET", f"/realms/{realm}/users/{user_id}").json()
    if not user or "id" not in user:
        raise HTTPException(status_code=404, detail="User not found")

    _enrich_user(realm, user)

    # Fetch permissions from path_rules based on user's group memberships
    permissions: list = []
    group_names = [g["name"] for g in user["groups"]]
    if group_names:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT pr.path_prefix, pr.required_group, pr.description,
                       a.app_name, a.display_name as app_display_name
                FROM path_rules pr
                LEFT JOIN apps a ON pr.path_prefix LIKE a.path_prefix || '%'
                WHERE pr.required_group = ANY($1)
                ORDER BY a.app_name, pr.path_prefix
            """, group_names)
            permissions = [dict(r) for r in rows]

    user["permissions"] = permissions
    return user


@router.post("/users", status_code=status.HTTP_201_CREATED, response_model=UserListResponse)
def create_user(realm: str, req: UserCreateRequest):
    """Create a new user with password and optional group bindings."""
    created = _create_single_user(realm, req)
    return _enrich_user(realm, created)


@router.put("/users/{user_id}", response_model=UserListResponse)
def update_user(realm: str, user_id: str, req: UserUpdateRequest):
    """Update user info (firstName, lastName, email, enabled)."""
    current = kc.request("GET", f"/realms/{realm}/users/{user_id}").json()
    if not current or "id" not in current:
        raise HTTPException(status_code=404, detail="User not found")

    update_data = req.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    current.update(update_data)
    resp = kc.request("PUT", f"/realms/{realm}/users/{user_id}", json=current)
    if resp.status_code not in (200, 204):
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    updated = kc.request("GET", f"/realms/{realm}/users/{user_id}").json()
    return _enrich_user(realm, updated)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(realm: str, user_id: str):
    """Delete a single user."""
    resp = kc.request("DELETE", f"/realms/{realm}/users/{user_id}")
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="User not found")
    return None


@router.post("/users/batch-delete", response_model=BatchOperationResponse)
def batch_delete_users(realm: str, req: BatchDeleteRequest):
    """Delete multiple users. Returns a summary of succeeded/failed."""
    succeeded = 0
    failed = 0
    errors: list = []

    for idx, uid in enumerate(req.user_ids):
        resp = kc.request("DELETE", f"/realms/{realm}/users/{uid}")
        if resp.status_code in (200, 204):
            succeeded += 1
        else:
            failed += 1
            errors.append({
                "index": idx,
                "username": uid,
                "error": resp.text,
            })

    return BatchOperationResponse(succeeded=succeeded, failed=failed, errors=errors)


@router.put("/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def reset_user_password(realm: str, user_id: str, req: PasswordResetRequest):
    """Reset a user's password. Federated users are rejected."""
    user = kc.request("GET", f"/realms/{realm}/users/{user_id}").json()
    if not user or "id" not in user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.get("federationLink"):
        raise HTTPException(
            status_code=400,
            detail="Cannot reset password for federated users",
        )

    resp = kc.request("PUT", f"/realms/{realm}/users/{user_id}/reset-password", json={
        "type": "password",
        "value": req.password,
        "temporary": True,
    })
    if resp.status_code not in (200, 204):
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return None


@router.put("/users/{user_id}/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def add_user_to_group(realm: str, user_id: str, group_id: str):
    """Add a user to a group."""
    resp = kc.request("PUT", f"/realms/{realm}/users/{user_id}/groups/{group_id}")
    if resp.status_code not in (200, 204):
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return None


@router.delete("/users/{user_id}/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_user_from_group(realm: str, user_id: str, group_id: str):
    """Remove a user from a group."""
    resp = kc.request("DELETE", f"/realms/{realm}/users/{user_id}/groups/{group_id}")
    if resp.status_code not in (200, 204):
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return None


@router.post("/users/batch-import", response_model=BatchOperationResponse)
async def batch_import_users(realm: str, file: UploadFile = File(...)):
    """
    Batch import users from a CSV file.
    CSV columns: username, password, email, firstName, lastName, groups
    The groups column is a comma-separated list of group IDs.
    """
    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    succeeded = 0
    failed = 0
    errors: list = []

    for idx, row in enumerate(reader):
        username = (row.get("username") or "").strip()
        password = (row.get("password") or "").strip()
        if not username or not password:
            failed += 1
            errors.append({
                "index": idx,
                "username": username or "(empty)",
                "error": "username and password are required",
            })
            continue

        groups_str = (row.get("groups") or "").strip()
        group_ids = [g.strip() for g in groups_str.split(",") if g.strip()] if groups_str else None

        req = UserCreateRequest(
            username=username,
            password=password,
            email=(row.get("email") or "").strip() or None,
            firstName=(row.get("firstName") or "").strip() or None,
            lastName=(row.get("lastName") or "").strip() or None,
            groups=group_ids,
        )

        try:
            _create_single_user(realm, req)
            succeeded += 1
        except HTTPException as e:
            failed += 1
            errors.append({
                "index": idx,
                "username": username,
                "error": e.detail,
            })
        except Exception as e:
            failed += 1
            errors.append({
                "index": idx,
                "username": username,
                "error": str(e),
            })

    return BatchOperationResponse(succeeded=succeeded, failed=failed, errors=errors)
