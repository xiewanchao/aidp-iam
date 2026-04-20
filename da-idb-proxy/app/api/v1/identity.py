import csv
import io
from fastapi import APIRouter, Depends, HTTPException, status, Request, UploadFile, File, Query
from fastapi.responses import StreamingResponse
from typing import List, Optional
from app.core.keycloak import kc
from app.core.db import get_pool
from app.schemas.groups import (
    GroupCreate, GroupUpdate, GroupResponse, GroupDetailResponse,
    GroupListResponse, BatchMembersRequest, GroupPermission,
)
from app.schemas.users import (
    UserCreateRequest, UserUpdateRequest, PasswordResetRequest,
    BatchDeleteRequest, UserListResponse, UserDetailResponse,
    BatchImportRequest, BatchOperationResponse,
)
from app.api.v1.common import skip_master_realm


router = APIRouter(prefix="/{realm}", tags=["Identity"], dependencies=[Depends(skip_master_realm)])


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



@router.post("/groups", status_code=status.HTTP_201_CREATED, response_model=GroupResponse)
def create_group(realm: str, group: GroupCreate):
    payload = group.model_dump(exclude={"users"}, exclude_none=True)
    resp = kc.request("POST", f"/realms/{realm}/groups", json=payload)

    new_group = next(g for g in kc.request("GET", f"/realms/{realm}/groups").json() if g['name'] == group.name)
    group_id = new_group['id']

    if group.users is not None:
        sync_group_users(realm, group_id, group.users)

    return new_group


@router.put("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def update_group(realm: str, group_id: str, group_update: GroupUpdate):
    current = kc.request("GET", f"/realms/{realm}/groups/{group_id}").json()
    base_data = group_update.model_dump(exclude={"users"}, exclude_none=True)
    current.update(base_data)
    kc.request("PUT", f"/realms/{realm}/groups/{group_id}", json=current)

    if group_update.users is not None:
        sync_group_users(realm, group_id, group_update.users)

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

    permissions = []
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT pr.id, pr.path_prefix, pr.method, pr.required_group, pr.description,
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
                SELECT pr.path_prefix, pr.method, pr.required_group, pr.description,
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


# ---------------------------------------------------------------------------
# Available groups for user (all groups + joined flag)
# ---------------------------------------------------------------------------

@router.get("/users/{user_id}/available-groups")
def get_user_available_groups(realm: str, user_id: str):
    """Return all groups with a joined flag for this user."""
    all_groups = kc.request("GET", f"/realms/{realm}/groups").json()
    user_groups = kc.request("GET", f"/realms/{realm}/users/{user_id}/groups").json()
    joined_ids = {g["id"] for g in user_groups}

    def _enrich(groups):
        result = []
        for g in groups:
            result.append({
                "id": g["id"],
                "name": g["name"],
                "joined": g["id"] in joined_ids,
                "source": _group_source(g["name"]),
                "subGroups": _enrich(g.get("subGroups", [])),
            })
        return result

    return _enrich(all_groups)


# ---------------------------------------------------------------------------
# Permissions: full list grouped by app + group permission binding
# ---------------------------------------------------------------------------

@router.get("/permissions")
async def list_permissions_by_app(realm: str):
    """List all path_rules grouped by application, with bound groups."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT pr.id, pr.path_prefix, pr.method, pr.description,
                   a.app_name, a.display_name as app_display_name,
                   array_agg(prg.group_name) FILTER (WHERE prg.group_name IS NOT NULL) as groups
            FROM path_rules pr
            LEFT JOIN apps a ON pr.path_prefix LIKE a.path_prefix || '%'
            LEFT JOIN path_rule_groups prg ON pr.id = prg.rule_id
            GROUP BY pr.id, pr.path_prefix, pr.method, pr.description, a.app_name, a.display_name
            ORDER BY a.app_name NULLS LAST, pr.path_prefix
        """)

    apps_map = {}
    for row in rows:
        app = row["app_name"] or "_system"
        if app not in apps_map:
            apps_map[app] = {
                "app_name": row["app_name"],
                "app_display_name": row["app_display_name"],
                "rules": [],
            }
        apps_map[app]["rules"].append({
            "id": row["id"],
            "path_prefix": row["path_prefix"],
            "method": row["method"],
            "description": row["description"],
            "groups": list(row["groups"]) if row["groups"] else [],
        })

    return list(apps_map.values())


@router.put("/groups/{group_id}/permissions")
async def set_group_permissions(realm: str, group_id: str, body: dict):
    """Set the path_rules assigned to a group (full replace). Body: {rule_ids: [1,3,5]}"""
    rule_ids = body.get("rule_ids", [])
    group = kc.request("GET", f"/realms/{realm}/groups/{group_id}").json()
    group_name = group["name"]

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM path_rule_groups WHERE group_name = $1", group_name)
            if rule_ids:
                await conn.executemany(
                    "INSERT INTO path_rule_groups (rule_id, group_name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    [(rid, group_name) for rid in rule_ids])

        rows = await conn.fetch("""
            SELECT pr.id, pr.path_prefix, pr.method, pr.description
            FROM path_rules pr JOIN path_rule_groups prg ON pr.id = prg.rule_id
            WHERE prg.group_name = $1 ORDER BY pr.path_prefix
        """, group_name)

    return {"group_id": group_id, "group_name": group_name, "permissions": [dict(r) for r in rows]}
