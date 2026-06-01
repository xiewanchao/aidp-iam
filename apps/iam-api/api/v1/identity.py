import csv
import io
import logging
import os
import re
import requests
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status, Request, UploadFile, File, Query
from fastapi.responses import StreamingResponse
from typing import List, Optional
from app.core.keycloak import kc
from app.core.db import get_pool
from app.schemas.groups import (
    GroupCreate, GroupUpdate, GroupResponse, GroupDetailResponse,
    GroupListResponse, GroupListPageResponse, BatchMembersRequest, GroupPermission,
)
from app.schemas.users import (
    UserCreateRequest, UserUpdateRequest, PasswordResetRequest,
    PasswordVerifyRequest, PasswordVerifyResponse,
    BatchDeleteRequest, UserListResponse, UserListPageResponse, UserDetailResponse,
    BatchImportRequest, BatchOperationResponse,
    PasswordStatusResponse, PasswordPolicyRequest, PasswordPolicyResponse,
)
from app.schemas.realm import SmtpSettingsRequest, SmtpSettingsResponse, EmailSettingsRequest, EmailSettingsResponse
from app.api.v1.common import skip_master_realm


router = APIRouter(prefix="/{realm}", tags=["Identity"], dependencies=[Depends(skip_master_realm)])


PRESET_GROUPS = {"master-admins", "tenant-admins", "all-users"}
logger = logging.getLogger(__name__)


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
    attrs = g.get("attributes") or {}
    desc_list = attrs.get("description", [])
    g["description"] = desc_list[0] if desc_list else None
    g["subGroups"] = [_enrich_group(realm, sg) for sg in g.get("subGroups", [])]
    return g


# --- Groups ---
@router.get("/Groups", response_model=GroupListPageResponse)
def list_groups(
    realm: str,
    search: Optional[str] = Query(None, description="模糊搜索组名"),
    first: int = Query(0, ge=0, description="分页起始位置"),
    max_results: int = Query(50, ge=1, le=500, alias="max", description="每页条数"),
):
    """获取顶级组，附加 source 和 member_count，支持搜索和分页"""
    params: dict = {"first": first, "max": max_results, "briefRepresentation": "false"}
    count_params: dict = {}
    if search:
        params["search"] = search
        count_params["search"] = search
    groups = kc.request("GET", f"/realms/{realm}/groups", params=params).json()
    enriched = [_enrich_group(realm, g) for g in groups]
    count_resp = kc.request("GET", f"/realms/{realm}/groups/count", params=count_params).json()
    total = count_resp.get("count", 0) if isinstance(count_resp, dict) else int(count_resp)
    return {"groups": enriched, "total": total}


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


@router.put("/Groups", status_code=status.HTTP_201_CREATED, response_model=GroupResponse)
def create_group(realm: str, group: GroupCreate):
    payload = group.model_dump(exclude={"users", "description"}, exclude_none=True)
    if group.description is not None:
        attrs = payload.get("attributes") or {}
        attrs["description"] = [group.description]
        payload["attributes"] = attrs
    resp = kc.request("POST", f"/realms/{realm}/groups", json=payload)

    new_group = next(g for g in kc.request("GET", f"/realms/{realm}/groups").json() if g['name'] == group.name)
    group_id = new_group['id']

    if group.users is not None:
        sync_group_users(realm, group_id, group.users)

    # Fetch full group object (list endpoint omits attributes)
    full_group = kc.request("GET", f"/realms/{realm}/groups/{group_id}").json()
    attrs = full_group.get("attributes") or {}
    full_group["description"] = attrs.get("description", [None])[0]
    return full_group


@router.patch("/Groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def update_group(realm: str, group_id: str, group_update: GroupUpdate):
    current = kc.request("GET", f"/realms/{realm}/groups/{group_id}").json()
    base_data = group_update.model_dump(exclude={"users", "description"}, exclude_none=True)
    current.update(base_data)
    if group_update.description is not None:
        attrs = current.get("attributes") or {}
        attrs["description"] = [group_update.description]
        current["attributes"] = attrs
    kc.request("PUT", f"/realms/{realm}/groups/{group_id}", json=current)

    if group_update.users is not None:
        sync_group_users(realm, group_id, group_update.users)

    return None


def _enrich_group_member(m: dict) -> dict:
    # Skips per-user groups lookup — caller already knows the group context.
    # Avoids N+1 Keycloak round-trips that cause timeouts on large groups.
    attrs = m.get("attributes") or {}
    ts = m.get("createdTimestamp")
    return {
        **m,
        "account_type": "federated" if m.get("federationLink") else "internal",
        "groups": [],
        "nickname": attrs.get("nickname", [None])[0],
        "email": m.get("email"),
        "created_at": datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else None,
    }


async def _query_group_permissions(group_name: str) -> list:
    """Return path-rule permissions bound to a specific Keycloak group name."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT pg.id                 AS permission_group_id,
                       pg.name               AS permission_group_name,
                       pg.description        AS permission_group_description,
                       pg.app_name           AS permission_app_name,
                       pgp.path_prefix,
                       pgp.method,
                       a.app_name            AS path_app_name,
                       a.display_name        AS path_app_display_name,
                       COALESCE(
                           (SELECT array_agg(DISTINCT b.kc_group_name ORDER BY b.kc_group_name)
                              FROM permission_group_bindings b
                             WHERE b.group_id = pg.id),
                           ARRAY[]::VARCHAR[]
                       ) AS required_groups
                FROM permission_groups pg
                JOIN permission_group_bindings pgb_me ON pgb_me.group_id = pg.id
                                                    AND pgb_me.kc_group_name = $1
                JOIN permission_group_paths pgp ON pgp.group_id = pg.id
                LEFT JOIN apps a ON pgp.path_prefix LIKE a.path_prefix || '%'
                ORDER BY a.app_name NULLS LAST, pgp.path_prefix, pgp.method NULLS FIRST
            """, group_name)
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("_query_group_permissions: failed for group %s: %s", group_name, exc)
        return []


@router.get("/Groups/{group_id}", response_model=GroupDetailResponse)
async def get_group_detail(
    realm: str,
    group_id: str,
    first: int = Query(0, ge=0, description="成员分页起始位置"),
    max_results: int = Query(20, ge=1, le=200, alias="max", description="每页成员数"),
):
    """获取 Group 详情：基础 + 成员（分页）+ 权限（permission_groups 展开的路径）"""
    group_base = kc.request("GET", f"/realms/{realm}/groups/{group_id}").json()
    if not group_base or "id" not in group_base:
        raise HTTPException(status_code=404, detail="Group not found")
    group_name = group_base["name"]

    all_member_ids = kc.request(
        "GET", f"/realms/{realm}/groups/{group_id}/members",
        params={"briefRepresentation": "true"},
    ).json()
    raw_members = kc.request(
        "GET", f"/realms/{realm}/groups/{group_id}/members",
        params={"first": first, "max": max_results},
    ).json()

    return {
        "id": group_base["id"],
        "name": group_name,
        "description": (group_base.get("attributes") or {}).get("description", [None])[0],
        "source": _group_source(group_name),
        "member_total": len(all_member_ids),
        "members": [_enrich_group_member(m) for m in raw_members],
        "permissions": await _query_group_permissions(group_name),
    }


@router.delete("/Groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(realm: str, group_id: str):
    """删除组（预置组不可删）"""
    group = kc.request("GET", f"/realms/{realm}/groups/{group_id}").json()
    if _group_source(group["name"]) == "preset":
        raise HTTPException(status_code=400, detail="Cannot delete preset group")
    kc.request("DELETE", f"/realms/{realm}/groups/{group_id}")
    return None


@router.post("/Groups/{group_id}/Members/BatchAdd", response_model=BatchOperationResponse)
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


@router.post("/Groups/{group_id}/Members/BatchRemove", response_model=BatchOperationResponse)
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


_VIEWER_ROLE = "AccessManager/Tenants/System/Roles/Viewer"


async def _write_user_self_acl(realm: str, user_id: str) -> None:
    """Grant the user Viewer access on their own user resource."""
    user_path = f"AccessManager/Tenants/{realm}/Users/{user_id}"
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO resource_acl (tenant_id, user_path, object_path, role_path)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (tenant_id, user_path, object_path) DO NOTHING
        """,
        realm, user_path, user_path, _VIEWER_ROLE,
    )


def _enrich_user(realm: str, user: dict) -> dict:
    """Add account_type, groups, nickname, email, and created_at to a raw Keycloak user dict."""
    user["account_type"] = "federated" if user.get("federationLink") else "internal"
    user_groups = kc.request("GET", f"/realms/{realm}/users/{user['id']}/groups").json()
    user["groups"] = [{"id": g["id"], "name": g["name"]} for g in user_groups]
    attrs = user.get("attributes") or {}
    user["nickname"] = attrs.get("nickname", [None])[0]
    user["email"] = user.get("email")
    ts = user.get("createdTimestamp")
    user["created_at"] = datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else None
    return user


def _keycloak_error_detail(resp) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text
    return data.get("errorMessage", resp.text) if isinstance(data, dict) else resp.text


def _create_single_user(realm: str, req: UserCreateRequest) -> dict:
    """
    Create one user in Keycloak: account + password + group bindings.
    Returns the created user dict. Raises on failure.
    """
    payload = {
        "username": req.username,
        "enabled": True,
    }
    if req.email:
        payload["email"] = req.email
    if req.nickname:
        payload["attributes"] = {"nickname": [req.nickname]}

    resp = kc.request("POST", f"/realms/{realm}/users", json=payload)
    if resp.status_code != 201:
        detail = _keycloak_error_detail(resp)
        raise HTTPException(status_code=resp.status_code, detail=detail)

    user_id = resp.headers["Location"].split("/")[-1]

    # Set initial password. `temporary_password=true` (default) forces the
    # user to change it on first login; set false for service / test accounts.
    kc.request("PUT", f"/realms/{realm}/users/{user_id}/reset-password", json={
        "type": "password",
        "value": req.password,
        "temporary": req.temporary_password,
    })

    # Bind groups
    if req.groups:
        for gid in req.groups:
            kc.request("PUT", f"/realms/{realm}/users/{user_id}/groups/{gid}")

    created_user = kc.request("GET", f"/realms/{realm}/users/{user_id}").json()
    return created_user


@router.get("/Users/Me")
def get_current_user_me(realm: str, request: Request):
    """返回当前登录用户的基本信息（含邮箱）。
    user_id 从 pep-proxy 注入的 X-Auth-User-Id 请求头获取，前端无需 decode JWT。
    """
    user_id = request.headers.get("x-auth-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing X-Auth-User-Id header")

    user = kc.request("GET", f"/realms/{realm}/users/{user_id}").json()
    if not user or "id" not in user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "id": user.get("id"),
        "username": user.get("username"),
        "email": user.get("email"),
        "enabled": user.get("enabled"),
        "account_type": "federated" if user.get("federationLink") else "internal",
    }


@router.get("/Users/ImportTemplate")
def download_import_template(realm: str):
    """Download a CSV template for batch user import."""
    csv_content = (
        "username,password,email,nickname,groups\n"
        "example_user,P@ssw0rd123,user@example.com,示例用户,\"admins,all-users\"\n"
    )
    return StreamingResponse(
        io.StringIO(csv_content),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=user_import_template.csv"},
    )


@router.get("/Users", response_model=UserListPageResponse)
def list_users(
    realm: str,
    search: Optional[str] = Query(None, description="Search by username"),
    group_id: Optional[str] = Query(None, description="Filter by group ID"),
    first: int = Query(0, ge=0, description="Pagination offset"),
    max_results: int = Query(50, ge=1, le=500, alias="max", description="Page size"),
):
    """List users with optional search, group filter, and pagination."""
    params: dict = {"first": first, "max": max_results}
    count_params: dict = {}
    if search:
        params["search"] = search
        count_params["search"] = search

    users = kc.request("GET", f"/realms/{realm}/users", params=params).json()
    enriched = [_enrich_user(realm, u) for u in users]

    if group_id:
        enriched = [u for u in enriched if any(g["id"] == group_id for g in u["groups"])]

    total = kc.request("GET", f"/realms/{realm}/users/count", params=count_params).json()
    if not isinstance(total, int):
        total = 0
    return {"users": enriched, "total": total}


@router.get("/Users/{user_id}/Details", response_model=UserDetailResponse)
async def get_user_full_context(realm: str, user_id: str):
    """Get full user detail: basic info + account type + groups + path-rule permissions."""
    user = kc.request("GET", f"/realms/{realm}/users/{user_id}").json()
    if not user or "id" not in user:
        raise HTTPException(status_code=404, detail="User not found")

    user = _enrich_user(realm, user)
    group_names = [g["name"] for g in user["groups"]]
    user["permissions"] = await _query_user_permissions(group_names)
    return user


async def _query_user_permissions(group_names: list) -> list:
    """Return path-rule permissions for all groups the user belongs to."""
    if not group_names:
        return []
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT pg.id                 AS permission_group_id,
                       pg.name               AS permission_group_name,
                       pg.description        AS permission_group_description,
                       pg.app_name           AS permission_app_name,
                       pgp.path_prefix,
                       pgp.method,
                       a.app_name            AS path_app_name,
                       a.display_name        AS path_app_display_name,
                       COALESCE(
                           (SELECT array_agg(DISTINCT b.kc_group_name ORDER BY b.kc_group_name)
                              FROM permission_group_bindings b
                             WHERE b.group_id = pg.id),
                           ARRAY[]::VARCHAR[]
                       ) AS required_groups
                FROM permission_groups pg
                JOIN permission_group_bindings pgb ON pgb.group_id = pg.id
                                                 AND pgb.kc_group_name = ANY($1)
                JOIN permission_group_paths pgp  ON pgp.group_id = pg.id
                LEFT JOIN apps a ON pgp.path_prefix LIKE a.path_prefix || '%'
                GROUP BY pg.id, pg.name, pg.description, pg.app_name,
                         pgp.path_prefix, pgp.method, a.app_name, a.display_name
                ORDER BY a.app_name NULLS LAST, pgp.path_prefix, pgp.method NULLS FIRST
            """, group_names)
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("_query_user_permissions: failed for groups %s: %s", group_names, exc)
        return []


@router.put("/Users", status_code=status.HTTP_201_CREATED, response_model=UserListResponse)
async def create_user(realm: str, req: UserCreateRequest):
    """Create a new user with password and optional group bindings."""
    created = _create_single_user(realm, req)
    await _write_user_self_acl(realm, created["id"])
    return _enrich_user(realm, created)


@router.patch("/Users/{user_id}", response_model=UserListResponse)
def update_user(realm: str, user_id: str, req: UserUpdateRequest):
    """Update user info (enabled, nickname, email, groups)."""
    current = kc.request("GET", f"/realms/{realm}/users/{user_id}").json()
    if not current or "id" not in current:
        raise HTTPException(status_code=404, detail="User not found")

    update_data = req.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    # nickname lives in Keycloak attributes, not top-level fields
    if "nickname" in update_data:
        attrs = current.get("attributes") or {}
        attrs["nickname"] = [update_data.pop("nickname")]
        current["attributes"] = attrs

    # groups are managed via membership API, not the user PUT body
    new_groups = update_data.pop("groups", None)

    current.update(update_data)
    resp = kc.request("PUT", f"/realms/{realm}/users/{user_id}", json=current)
    if resp.status_code not in (200, 204):
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    if new_groups is not None:
        existing = kc.request("GET", f"/realms/{realm}/users/{user_id}/groups").json()
        existing_ids = {g["id"] for g in existing}
        new_ids = set(new_groups)
        for gid in new_ids - existing_ids:
            kc.request("PUT", f"/realms/{realm}/users/{user_id}/groups/{gid}")
        for gid in existing_ids - new_ids:
            kc.request("DELETE", f"/realms/{realm}/users/{user_id}/groups/{gid}")

    updated = kc.request("GET", f"/realms/{realm}/users/{user_id}").json()
    return _enrich_user(realm, updated)


@router.delete("/Users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(realm: str, user_id: str):
    """Delete a single user."""
    resp = kc.request("DELETE", f"/realms/{realm}/users/{user_id}")
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="User not found")
    return None


@router.post("/Users/BatchDelete", response_model=BatchOperationResponse)
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


@router.put("/Users/{user_id}/Password", status_code=status.HTTP_204_NO_CONTENT)
def reset_user_password(realm: str, user_id: str, req: PasswordResetRequest):
    """Reset a user's password. Federated users are rejected.

    temporary is always True: the user must change the password on next login.
    """
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


@router.post("/Users/{user_id}/PasswordVerify", response_model=PasswordVerifyResponse)
def verify_user_password(realm: str, user_id: str, req: PasswordVerifyRequest):
    """Verify whether the supplied password matches the user's current password."""
    user = kc.request("GET", f"/realms/{realm}/users/{user_id}").json()
    if not user or "id" not in user:
        raise HTTPException(status_code=404, detail="User not found")

    username = user.get("username")
    if not username:
        raise HTTPException(status_code=400, detail="User has no username")

    token_url = (
        f"{os.getenv('KEYCLOAK_URL', 'http://localhost:8080').rstrip('/')}"
        f"/realms/{realm}/protocol/openid-connect/token"
    )
    data = {
        "grant_type": "password",
        "client_id": os.getenv("KC_CLIENT_ID", "aidp-client"),
        "client_secret": os.getenv("KC_CLIENT_SECRET", ""),
        "username": username,
        "password": req.password,
    }

    try:
        resp = requests.post(token_url, data=data, timeout=10)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Keycloak token endpoint unavailable: {exc}") from exc

    if resp.status_code == 200:
        return PasswordVerifyResponse(valid=True)
    if resp.status_code in (400, 401):
        return PasswordVerifyResponse(valid=False)
    raise HTTPException(status_code=resp.status_code, detail=resp.text)


@router.get("/Users/{user_id}/PasswordStatus", response_model=PasswordStatusResponse)
def get_password_status(realm: str, user_id: str):
    """Return password status for a user: creation time, temporary flag, expiry info.

    Expiry calculation uses the Realm's forceExpiredPasswordChange policy value.
    Returns null for expiry fields when no policy is configured.
    """
    user = kc.request("GET", f"/realms/{realm}/users/{user_id}").json()
    if not user or "id" not in user:
        raise HTTPException(status_code=404, detail="User not found")

    # Fetch credentials to get password creation time and temporary flag
    creds_resp = kc.request("GET", f"/realms/{realm}/users/{user_id}/credentials")
    if creds_resp.status_code != 200:
        raise HTTPException(status_code=creds_resp.status_code, detail=creds_resp.text)

    credential_created_at: Optional[datetime] = None
    is_temporary = False
    for cred in creds_resp.json():
        if cred.get("type") == "password":
            ts_ms = cred.get("createdDate")
            if ts_ms:
                credential_created_at = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            break

    # Keycloak signals "must change password" via requiredActions, not the
    # credential object's temporary flag (which is write-only on reset-password).
    is_temporary = "UPDATE_PASSWORD" in (user.get("requiredActions") or [])

    # Fetch Realm password policy to get expiry days
    realm_resp = kc.request("GET", f"/realms/{realm}")
    if realm_resp.status_code != 200:
        raise HTTPException(status_code=realm_resp.status_code, detail=realm_resp.text)

    expiry_days: Optional[int] = _parse_expire_days(realm_resp.json().get("passwordPolicy", ""))

    days_remaining: Optional[int] = None
    is_expired = False
    if expiry_days is not None and credential_created_at is not None:
        now = datetime.now(tz=timezone.utc)
        elapsed = (now - credential_created_at).days
        days_remaining = expiry_days - elapsed
        is_expired = days_remaining <= 0

    return PasswordStatusResponse(
        user_id=user_id,
        credential_created_at=credential_created_at,
        is_temporary=is_temporary,
        expiry_days=expiry_days,
        days_remaining=days_remaining,
        is_expired=is_expired,
    )


@router.get("/PasswordPolicy", response_model=PasswordPolicyResponse)
def get_password_policy(realm: str):
    """Return the current Realm password policy as structured fields."""
    realm_resp = kc.request("GET", f"/realms/{realm}")
    if realm_resp.status_code != 200:
        raise HTTPException(status_code=realm_resp.status_code, detail=realm_resp.text)
    return _parse_password_policy(realm_resp.json().get("passwordPolicy", ""))


@router.put("/PasswordPolicy", response_model=PasswordPolicyResponse)
def update_password_policy(realm: str, req: PasswordPolicyRequest):
    """Update the Realm password policy.

    Only fields present in the request body are changed; omitted fields keep
    their current values. Pass null to remove a specific policy clause.
    """
    realm_resp = kc.request("GET", f"/realms/{realm}")
    if realm_resp.status_code != 200:
        raise HTTPException(status_code=realm_resp.status_code, detail=realm_resp.text)

    realm_data = realm_resp.json()
    current = _parse_password_policy(realm_data.get("passwordPolicy", ""))

    # Merge: only override fields explicitly set in the request
    update = req.model_dump(exclude_unset=True)
    merged = current.model_copy(update=update)

    realm_data["passwordPolicy"] = _build_password_policy(merged)
    resp = kc.request("PUT", f"/realms/{realm}", json=realm_data)
    if resp.status_code not in (200, 204):
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    return merged


@router.put("/Users/{user_id}/Groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def add_user_to_group(realm: str, user_id: str, group_id: str):
    """Add a user to a group."""
    resp = kc.request("PUT", f"/realms/{realm}/users/{user_id}/groups/{group_id}")
    if resp.status_code not in (200, 204):
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return None


@router.delete("/Users/{user_id}/Groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_user_from_group(realm: str, user_id: str, group_id: str):
    """Remove a user from a group."""
    resp = kc.request("DELETE", f"/realms/{realm}/users/{user_id}/groups/{group_id}")
    if resp.status_code not in (200, 204):
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return None


@router.post("/Users/BatchCreate", response_model=BatchOperationResponse)
async def batch_create_users(realm: str, req: BatchImportRequest):
    """
    Batch-create users from a JSON list (e.g. after the frontend has parsed a CSV).

    Each item in `users` follows the same schema as POST /users:
      username, password, groups (list of group IDs), temporary_password.

    Processing is best-effort: failures are collected and returned alongside
    successes so the caller can retry individual rows without re-submitting
    the whole batch.
    """
    succeeded = 0
    failed = 0
    errors: list = []

    for idx, user_req in enumerate(req.users):
        try:
            created = _create_single_user(realm, user_req)
            await _write_user_self_acl(realm, created["id"])
            succeeded += 1
        except HTTPException as e:
            failed += 1
            errors.append({
                "index": idx,
                "username": user_req.username,
                "error": e.detail,
            })
        except Exception as e:
            failed += 1
            errors.append({
                "index": idx,
                "username": user_req.username,
                "error": str(e),
            })

    return BatchOperationResponse(succeeded=succeeded, failed=failed, errors=errors)


@router.post("/Users/BatchImport", response_model=BatchOperationResponse)
def _parse_csv_user_row(row: dict) -> UserCreateRequest | None:
    """Parse a CSV row into a UserCreateRequest. Returns None when required fields are missing."""
    username = (row.get("username") or "").strip()
    password = (row.get("password") or "").strip()
    if not username or not password:
        return None
    groups_str = (row.get("groups") or "").strip()
    return UserCreateRequest(
        username=username,
        password=password,
        email=(row.get("email") or "").strip() or None,
        nickname=(row.get("nickname") or "").strip() or None,
        groups=[g.strip() for g in groups_str.split(",") if g.strip()] if groups_str else None,
    )


def _make_batch_error(idx: int, username: str, error: str) -> dict:
    return {"index": idx, "username": username or "(empty)", "error": error}


@router.post("/Users/BatchImport", response_model=BatchOperationResponse)
async def batch_import_users(realm: str, file: UploadFile = File(...)):
    """Batch import users from a CSV file (columns: username, password, email, nickname, groups)."""
    content = await file.read()
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))

    succeeded = 0
    failed = 0
    errors: list = []

    for idx, row in enumerate(reader):
        req = _parse_csv_user_row(row)
        if req is None:
            failed += 1
            username = (row.get("username") or "").strip()
            errors.append(_make_batch_error(idx, username, "username and password are required"))
            continue
        try:
            created = _create_single_user(realm, req)
            await _write_user_self_acl(realm, created["id"])
            succeeded += 1
        except HTTPException as exc:
            failed += 1
            errors.append(_make_batch_error(idx, req.username, exc.detail))
        except Exception as exc:
            failed += 1
            errors.append(_make_batch_error(idx, req.username, str(exc)))

    return BatchOperationResponse(succeeded=succeeded, failed=failed, errors=errors)


# ---------------------------------------------------------------------------
# Password policy helpers
# ---------------------------------------------------------------------------

# Mapping: our field name → (Keycloak policy name, value type)
# bool policies use the policy name alone (no value) when True, absent when False.
_POLICY_MAP = {
    "expire_days": ("forceExpiredPasswordChange", "int"),
    "min_length": ("length", "int"),
    "require_uppercase": ("upperCase", "bool"),
    "require_lowercase": ("lowerCase", "bool"),
    "require_digits": ("digits", "bool"),
    "require_special": ("specialChars", "bool"),
    "history_count": ("passwordHistory", "int"),
}


def _parse_expire_days(policy_str: str) -> Optional[int]:
    """Extract forceExpiredPasswordChange value from a Keycloak policy string."""
    m = re.search(r"forceExpiredPasswordChange\((\d+)\)", policy_str)
    return int(m.group(1)) if m else None


def _parse_password_policy(policy_str: str) -> PasswordPolicyResponse:
    """Parse a Keycloak passwordPolicy string into a PasswordPolicyResponse."""
    result: dict = {}
    for field, (kc_name, vtype) in _POLICY_MAP.items():
        if vtype == "int":
            m = re.search(rf"{re.escape(kc_name)}\((\d+)\)", policy_str)
            result[field] = int(m.group(1)) if m else None
        else:  # bool
            result[field] = kc_name in policy_str
    return PasswordPolicyResponse(**result)


def _build_password_policy(policy: PasswordPolicyResponse) -> str:
    """Serialise a PasswordPolicyResponse back to a Keycloak policy string."""
    clauses = []
    for field, (kc_name, vtype) in _POLICY_MAP.items():
        val = getattr(policy, field)
        if vtype == "int":
            if val is not None:
                clauses.append(f"{kc_name}({val})")
        else:  # bool
            if val:
                clauses.append(kc_name)
    return " and ".join(clauses)


# ---------------------------------------------------------------------------
# SMTP Settings
# ---------------------------------------------------------------------------

# Mapping: Python field name → Keycloak smtpServer key (string fields only)
_SMTP_STR_MAP = {
    "host": "host",
    "from_address": "from",
    "from_display_name": "fromDisplayName",
    "reply_to": "replyTo",
    "reply_to_display_name": "replyToDisplayName",
    "envelope_from": "envelopeFrom",
    "user": "user",
    "password": "password",
}

_SMTP_BOOL_FIELDS = ("ssl", "starttls", "auth")


def _kc_smtp_to_response(smtp: dict) -> SmtpSettingsResponse:
    """Convert Keycloak smtpServer dict (all-string values) to SmtpSettingsResponse."""
    def _bool(v: str) -> Optional[bool]:
        return (v == "true") if v else None

    def _int(v: str) -> Optional[int]:
        return int(v) if v else None

    return SmtpSettingsResponse(
        host=smtp.get("host") or None,
        port=_int(smtp.get("port")),
        from_address=smtp.get("from") or None,
        from_display_name=smtp.get("fromDisplayName") or None,
        reply_to=smtp.get("replyTo") or None,
        reply_to_display_name=smtp.get("replyToDisplayName") or None,
        envelope_from=smtp.get("envelopeFrom") or None,
        ssl=_bool(smtp.get("ssl")),
        starttls=_bool(smtp.get("starttls")),
        auth=_bool(smtp.get("auth")),
        user=smtp.get("user") or None,
        # password intentionally omitted
    )


def _merge_smtp_request(current: dict, req: SmtpSettingsRequest) -> dict:
    """Merge only the explicitly-set fields from SmtpSettingsRequest into the
    existing Keycloak smtpServer dict. Returns a new dict."""
    result = dict(current)
    update = req.model_dump(exclude_unset=True)

    for py_field, kc_key in _SMTP_STR_MAP.items():
        if py_field in update:
            val = update[py_field]
            result[kc_key] = val if val is not None else ""

    if "port" in update:
        result["port"] = str(update["port"]) if update["port"] is not None else ""

    for field in _SMTP_BOOL_FIELDS:
        if field in update:
            val = update[field]
            result[field] = "true" if val else "false"

    return result


@router.get("/SmtpSettings", response_model=SmtpSettingsResponse)
def get_smtp_settings(realm: str):
    """Return the current SMTP server configuration for the realm."""
    realm_resp = kc.request("GET", f"/realms/{realm}")
    if realm_resp.status_code != 200:
        raise HTTPException(status_code=realm_resp.status_code, detail=realm_resp.text)
    return _kc_smtp_to_response(realm_resp.json().get("smtpServer") or {})


@router.put("/SmtpSettings", response_model=SmtpSettingsResponse)
def update_smtp_settings(realm: str, req: SmtpSettingsRequest):
    """
    Update SMTP server configuration. Only provided fields are changed.
    """
    realm_resp = kc.request("GET", f"/realms/{realm}")
    if realm_resp.status_code != 200:
        raise HTTPException(status_code=realm_resp.status_code, detail=realm_resp.text)

    realm_data = realm_resp.json()
    realm_data["smtpServer"] = _merge_smtp_request(
        realm_data.get("smtpServer") or {}, req
    )

    resp = kc.request("PUT", f"/realms/{realm}", json=realm_data)
    if resp.status_code not in (200, 204):
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    return _kc_smtp_to_response(realm_data["smtpServer"])


# ---------------------------------------------------------------------------
# Email Settings
# ---------------------------------------------------------------------------


@router.get("/EmailSettings", response_model=EmailSettingsResponse)
def get_email_settings(realm: str):
    """
    Return email feature toggles for the realm.
    """
    realm_resp = kc.request("GET", f"/realms/{realm}")
    if realm_resp.status_code != 200:
        raise HTTPException(status_code=realm_resp.status_code, detail=realm_resp.text)
    data = realm_resp.json()
    return EmailSettingsResponse(
        reset_password_allowed=data.get("resetPasswordAllowed"),
        verify_email=data.get("verifyEmail"),
    )


@router.put("/EmailSettings", response_model=EmailSettingsResponse)
def update_email_settings(realm: str, req: EmailSettingsRequest):
    """
    Update email feature toggles. Only provided fields are changed.
    """
    realm_resp = kc.request("GET", f"/realms/{realm}")
    if realm_resp.status_code != 200:
        raise HTTPException(status_code=realm_resp.status_code, detail=realm_resp.text)

    realm_data = realm_resp.json()
    update = req.model_dump(exclude_unset=True)

    if "reset_password_allowed" in update:
        realm_data["resetPasswordAllowed"] = update["reset_password_allowed"]
    if "verify_email" in update:
        realm_data["verifyEmail"] = update["verify_email"]

    # Enforce invariant: email is never used as a login credential
    realm_data["loginWithEmailAllowed"] = False

    resp = kc.request("PUT", f"/realms/{realm}", json=realm_data)
    if resp.status_code not in (200, 204):
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    return EmailSettingsResponse(
        reset_password_allowed=realm_data.get("resetPasswordAllowed"),
        verify_email=realm_data.get("verifyEmail"),
    )


# ---------------------------------------------------------------------------
# Available groups for user (all groups + joined flag)
# ---------------------------------------------------------------------------


@router.get("/Users/{user_id}/AvailableGroups")
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

def _build_paths_by_group(path_rows) -> dict:
    paths: dict = {}
    for r in path_rows:
        paths.setdefault(r["group_id"], []).append({
            "path_prefix": r["path_prefix"],
            "method": r["method"],
        })
    return paths


def _build_permission_apps_list(pg_rows, paths_by_group: dict) -> list:
    apps_map: dict = {}
    for row in pg_rows:
        app_key = row["app_name"] or "_system"
        display = row["app_display_name"] or ("平台级" if app_key == "_system" else row["app_name"])
        apps_map.setdefault(app_key, {
            "app_name": row["app_name"] or "",
            "app_display_name": display,
            "permission_groups": [],
        })
        apps_map[app_key]["permission_groups"].append({
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "paths": paths_by_group.get(row["id"], []),
            "bound_groups": list(row["bound_groups"]) if row["bound_groups"] else [],
        })
    return list(apps_map.values())


@router.get("/Permissions")
async def list_permissions_by_app(realm: str):
    """列出所有 permission_groups 按 app 分类，每个包含它的路径和已绑定的 Keycloak 组。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        pg_rows = await conn.fetch("""
            SELECT pg.id, pg.app_name, pg.name, pg.description,
                   a.display_name AS app_display_name,
                   COALESCE(
                       (SELECT array_agg(DISTINCT b.kc_group_name ORDER BY b.kc_group_name)
                          FROM permission_group_bindings b
                         WHERE b.group_id = pg.id),
                       ARRAY[]::VARCHAR[]
                   ) AS bound_groups
            FROM permission_groups pg
            LEFT JOIN apps a ON pg.app_name = a.app_name
            ORDER BY pg.app_name NULLS FIRST, pg.name
        """)
        path_rows = await conn.fetch("""
            SELECT group_id, path_prefix, method
            FROM permission_group_paths
            ORDER BY group_id, path_prefix, method NULLS FIRST
        """)

    return _build_permission_apps_list(pg_rows, _build_paths_by_group(path_rows))


@router.put("/Groups/{group_id}/Permissions")
async def set_group_permissions(realm: str, group_id: str, body: dict):
    """把指定 Keycloak 组绑定到一批 permission_groups（全量替换）。
    Body: {permission_group_ids: [1, 3, 5]}
    也接受老字段 {rule_ids: [...]}，兼容旧前端（语义上现在是 permission_group_ids）。
    """
    pg_ids = body.get("permission_group_ids", body.get("rule_ids", []))
    group = kc.request("GET", f"/realms/{realm}/groups/{group_id}").json()
    group_name = group["name"]

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM permission_group_bindings WHERE kc_group_name = $1",
                group_name,
            )
            if pg_ids:
                await conn.executemany(
                    "INSERT INTO permission_group_bindings (group_id, kc_group_name) "
                    "VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    [(pid, group_name) for pid in pg_ids],
                )

        rows = await conn.fetch("""
            SELECT pg.id, pg.name, pg.description, pg.app_name
            FROM permission_groups pg
            JOIN permission_group_bindings pgb ON pg.id = pgb.group_id
            WHERE pgb.kc_group_name = $1
            ORDER BY pg.app_name NULLS FIRST, pg.name
        """, group_name)

    return {
        "group_id": group_id,
        "group_name": group_name,
        "permission_groups": [dict(r) for r in rows],
    }
