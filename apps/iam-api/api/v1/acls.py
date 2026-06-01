"""
ACL Management API for AccessManager.

Endpoints:
  PUT    /AccessManager/Tenants/{tid}/ACLs                              Grant/update a permission
  PUT    /AccessManager/Tenants/{tid}/ACLs/Batch                        Batch grant/update permissions
  GET    /AccessManager/Tenants/{tid}/ACLs                              Query ACLs by object or user
  DELETE /AccessManager/Tenants/{tid}/ACLs                              Revoke a permission
  DELETE /AccessManager/Tenants/{tid}/ACLs/Batch                        Batch revoke permissions
  POST   /AccessManager/Tenants/{tid}/Action/QueryACLs                  Batch check permissions
  GET    /AccessManager/Tenants/{tid}/AppObjects                        List enabled apps + their resource objects
  PUT    /AccessManager/Tenants/{tid}/Groups/{group_name}/ObjectPermissions  Batch set group ACLs
"""

import json
import re
from typing import Any, Dict, List, Optional

from app.core.db import get_pool
from fastapi import APIRouter, HTTPException, Request, Query as QParam
from pydantic import BaseModel

router = APIRouter(tags=["ACLs"])

OWNER_ROLE = "AccessManager/Tenants/System/Roles/Owner"
METHOD_LABELS: Dict[str, str] = {
    "GET": "查看",
    "PUT": "创建",
    "PATCH": "编辑",
    "POST": "创建",
    "DELETE": "删除",
}
DEFAULT_ROLE_MATRIX = {
    "AccessManager/Tenants/System/Roles/Owner": {"GET", "PUT", "PATCH", "DELETE", "POST"},
    "AccessManager/Tenants/System/Roles/Contributor": {"GET", "PUT", "PATCH", "POST"},
    "AccessManager/Tenants/System/Roles/Viewer": {"GET"},
}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class AclEntry(BaseModel):
    user_path: str
    object_path: str
    role_path: str


class AclBatchRequest(BaseModel):
    entries: List[AclEntry]


class AclDeleteRequest(BaseModel):
    user_path: str
    object_path: str


class AclBatchDeleteRequest(BaseModel):
    entries: List[AclDeleteRequest]


class QueryAclItem(BaseModel):
    user_path: str
    object_path: str


class QueryAclRequest(BaseModel):
    queries: List[QueryAclItem]


class ListAllowedIdsRequest(BaseModel):
    user_path: str
    type_prefix: str   # e.g. "MemoryStore/Tenants/aidp/Instances"
    page: int = 1
    page_size: int = 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_caller_role(pool, tenant_id: str, caller_path: str, object_path: str) -> Optional[str]:
    parts = object_path.split("/")
    candidates = ["/".join(parts[:i]) for i in range(len(parts), 0, -1)]
    row = await pool.fetchrow(
        """
        SELECT role_path FROM resource_acl
        WHERE tenant_id=$1 AND user_path=$2 AND object_path=ANY($3)
        ORDER BY LENGTH(object_path) DESC LIMIT 1
        """,
        tenant_id, caller_path, candidates,
    )
    return row["role_path"] if row else None


def _is_admin(caller_groups: List[str], tenant_id: str) -> bool:
    # X-Auth-Groups header carries short names ("admins", "all-users");
    # full paths are also accepted for forward-compatibility.
    admin_short = {"master-admins", "tenant-admins", "admins"}
    admin_full = {
        f"AccessManager/Tenants/{tenant_id}/Groups/master-admins",
        f"AccessManager/Tenants/{tenant_id}/Groups/tenant-admins",
        f"AccessManager/Tenants/{tenant_id}/Groups/admins",
    }
    return bool((admin_short | admin_full) & set(caller_groups))


def _role_level(role: str) -> int:
    levels = {
        "AccessManager/Tenants/System/Roles/Owner": 3,
        "AccessManager/Tenants/System/Roles/Contributor": 2,
        "AccessManager/Tenants/System/Roles/Viewer": 1,
    }
    return levels.get(role, 0)


def _caller_context(tenant_id: str, request: Request) -> tuple[List[str], str]:
    caller_id = request.headers.get("x-auth-user-id", "")
    caller_groups_raw = request.headers.get("x-auth-groups", "")
    caller_groups = [g.strip() for g in caller_groups_raw.split(",") if g.strip()]
    caller_path = f"AccessManager/Tenants/{tenant_id}/Users/{caller_id}" if caller_id else ""
    return caller_groups, caller_path


def _validate_object_tenant(tenant_id: str, object_path: str) -> None:
    obj_parts = object_path.split("/")
    if len(obj_parts) >= 3 and obj_parts[1] == "Tenants" and obj_parts[2] != tenant_id:
        raise HTTPException(
            status_code=400,
            detail=f"Cross-tenant ACL not allowed: {object_path}",
        )


async def _ensure_can_manage_acl(
    db,
    tenant_id: str,
    caller_path: str,
    caller_groups: List[str],
    object_path: str,
    action: str,
    role_path: Optional[str] = None,
) -> None:
    if _is_admin(caller_groups, tenant_id):
        return

    caller_role = await _get_caller_role(db, tenant_id, caller_path, object_path)
    if caller_role != OWNER_ROLE:
        raise HTTPException(
            status_code=403,
            detail=f"Only Owner can {action} permissions on {object_path}",
        )
    if role_path and _role_level(role_path) > _role_level(caller_role):
        raise HTTPException(
            status_code=403,
            detail=f"Cannot grant a role higher than your own on {object_path}",
        )


async def _upsert_acl(
    db,
    tenant_id: str,
    user_path: str,
    object_path: str,
    role_path: str,
    created_by: str,
) -> None:
    await db.execute(
        """
        INSERT INTO resource_acl (tenant_id, user_path, object_path, role_path, created_by)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (tenant_id, user_path, object_path)
        DO UPDATE SET role_path=$4, created_by=$5
        """,
        tenant_id,
        user_path,
        object_path,
        role_path,
        created_by,
    )


async def _delete_acl(db, tenant_id: str, user_path: str, object_path: str) -> bool:
    result = await db.execute(
        "DELETE FROM resource_acl WHERE tenant_id=$1 AND user_path=$2 AND object_path=$3",
        tenant_id,
        user_path,
        object_path,
    )
    return result.endswith("1")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.put("/AccessManager/Tenants/{tid}/ACLs")
async def grant_acl(tid: str, body: AclEntry, request: Request):
    """Grant or update a permission. Caller must be Owner of the object or admin."""
    caller_groups, caller_path = _caller_context(tid, request)
    _validate_object_tenant(tid, body.object_path)
    pool = await get_pool()
    await _ensure_can_manage_acl(
        pool,
        tid,
        caller_path,
        caller_groups,
        body.object_path,
        "grant",
        body.role_path,
    )
    await _upsert_acl(pool, tid, body.user_path, body.object_path, body.role_path, caller_path)
    return {
        "status": "ok",
        "user_path": body.user_path,
        "object_path": body.object_path,
        "role_path": body.role_path,
    }


@router.put("/AccessManager/Tenants/{tid}/ACLs/Batch")
async def grant_acl_batch(tid: str, body: AclBatchRequest, request: Request):
    """
    Batch grant or update permissions for multiple users/groups on one or more objects.
    Caller must be Owner of each object or admin.
    All entries are applied in a single transaction; any failure rolls back the whole batch.
    """
    caller_groups, caller_path = _caller_context(tid, request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            for entry in body.entries:
                _validate_object_tenant(tid, entry.object_path)
                await _ensure_can_manage_acl(
                    conn,
                    tid,
                    caller_path,
                    caller_groups,
                    entry.object_path,
                    "grant",
                    entry.role_path,
                )
                await _upsert_acl(conn, tid, entry.user_path, entry.object_path, entry.role_path, caller_path)

    return {"status": "ok", "upserted": len(body.entries)}


def _subject_path_like(tenant_id: str, subject_type: Optional[str]) -> Optional[str]:
    if subject_type == "user":
        return f"AccessManager/Tenants/{tenant_id}/Users/%"
    if subject_type == "group":
        return f"AccessManager/Tenants/{tenant_id}/Groups/%"
    return None


async def _fetch_object_acls(
    pool,
    tenant_id: str,
    object_path: str,
    subject_type: Optional[str],
    page_size: int,
    offset: int,
):
    like = _subject_path_like(tenant_id, subject_type)
    if like is None:
        total = await pool.fetchval(
            "SELECT COUNT(*) FROM resource_acl WHERE tenant_id=$1 AND object_path=$2",
            tenant_id,
            object_path,
        )
        rows = await pool.fetch(
            """
            SELECT user_path, object_path, role_path, created_at, created_by
            FROM resource_acl WHERE tenant_id=$1 AND object_path=$2
            ORDER BY created_at
            LIMIT $3 OFFSET $4
            """,
            tenant_id,
            object_path,
            page_size,
            offset,
        )
        return total, rows

    total = await pool.fetchval(
        "SELECT COUNT(*) FROM resource_acl WHERE tenant_id=$1 AND object_path=$2 AND user_path LIKE $3",
        tenant_id,
        object_path,
        like,
    )
    rows = await pool.fetch(
        """
        SELECT user_path, object_path, role_path, created_at, created_by
        FROM resource_acl WHERE tenant_id=$1 AND object_path=$2 AND user_path LIKE $3
        ORDER BY created_at
        LIMIT $4 OFFSET $5
        """,
        tenant_id,
        object_path,
        like,
        page_size,
        offset,
    )
    return total, rows


async def _fetch_user_acls(pool, tenant_id: str, user_path: str, page_size: int, offset: int):
    total = await pool.fetchval(
        "SELECT COUNT(*) FROM resource_acl WHERE tenant_id=$1 AND user_path=$2",
        tenant_id,
        user_path,
    )
    rows = await pool.fetch(
        """
        SELECT user_path, object_path, role_path, created_at, created_by
        FROM resource_acl WHERE tenant_id=$1 AND user_path=$2
        ORDER BY object_path
        LIMIT $3 OFFSET $4
        """,
        tenant_id,
        user_path,
        page_size,
        offset,
    )
    return total, rows


async def _fetch_all_acls(pool, tenant_id: str, page_size: int, offset: int):
    total = await pool.fetchval(
        "SELECT COUNT(*) FROM resource_acl WHERE tenant_id=$1",
        tenant_id,
    )
    rows = await pool.fetch(
        """
        SELECT user_path, object_path, role_path, created_at, created_by
        FROM resource_acl WHERE tenant_id=$1
        ORDER BY object_path, user_path
        LIMIT $2 OFFSET $3
        """,
        tenant_id,
        page_size,
        offset,
    )
    return total, rows


@router.get("/AccessManager/Tenants/{tid}/ACLs")
async def query_acls(
    tid: str,
    object_path: Optional[str] = QParam(None, alias="object", description="Filter by object path (exact match)"),
    user: Optional[str] = QParam(None, description="Filter by user/group path (exact match)"),
    subject_type: Optional[str] = QParam(
        None,
        description="When combined with ?object: 'user' or 'group' to narrow results",
    ),
    page: int = QParam(1, ge=1, description="Page number (1-based)"),
    page_size: int = QParam(50, ge=1, description="Page size, max 200"),
):
    """
    Query ACLs. Provide ?object or ?user to filter; omit both to list all ACLs.
    """
    if subject_type and subject_type not in ("user", "group"):
        raise HTTPException(status_code=400, detail="subject_type must be 'user' or 'group'")

    pool = await get_pool()
    page_size = min(page_size, 200)
    offset = (page - 1) * page_size

    if object_path:
        total, rows = await _fetch_object_acls(pool, tid, object_path, subject_type, page_size, offset)
    elif user:
        total, rows = await _fetch_user_acls(pool, tid, user, page_size, offset)
    else:
        total, rows = await _fetch_all_acls(pool, tid, page_size, offset)

    return {
        "acls": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.delete("/AccessManager/Tenants/{tid}/ACLs")
async def revoke_acl(tid: str, body: AclDeleteRequest, request: Request):
    """Revoke a permission. Caller must be Owner of the object or admin."""
    caller_groups, caller_path = _caller_context(tid, request)
    _validate_object_tenant(tid, body.object_path)
    pool = await get_pool()
    await _ensure_can_manage_acl(pool, tid, caller_path, caller_groups, body.object_path, "revoke")
    deleted = await _delete_acl(pool, tid, body.user_path, body.object_path)
    if not deleted:
        raise HTTPException(status_code=404, detail="ACL entry not found")
    return {"status": "deleted", "user_path": body.user_path, "object_path": body.object_path}


@router.delete("/AccessManager/Tenants/{tid}/ACLs/Batch")
async def revoke_acl_batch(tid: str, body: AclBatchDeleteRequest, request: Request):
    """
    Batch revoke permissions for multiple users/groups.
    Caller must be Owner of each object or admin.
    All deletions are applied in a single transaction; any failure rolls back the whole batch.
    Entries that do not exist are silently skipped (no 404).
    """
    caller_groups, caller_path = _caller_context(tid, request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            deleted = 0
            for entry in body.entries:
                _validate_object_tenant(tid, entry.object_path)
                await _ensure_can_manage_acl(conn, tid, caller_path, caller_groups, entry.object_path, "revoke")
                if await _delete_acl(conn, tid, entry.user_path, entry.object_path):
                    deleted += 1

    return {"status": "ok", "deleted": deleted}


@router.post("/AccessManager/Tenants/{tid}/Action/QueryACLs")
async def query_acls_batch(tid: str, body: QueryAclRequest):
    """
    Batch check ACLs with prefix-matching inheritance.
    Returns the matched role_path for each query item.
    """
    pool = await get_pool()
    results = []

    for item in body.queries:
        parts = item.object_path.split("/")
        candidates = ["/".join(parts[:i]) for i in range(len(parts), 0, -1)]

        row = await pool.fetchrow(
            """
            SELECT role_path, object_path FROM resource_acl
            WHERE tenant_id=$1 AND user_path=$2 AND object_path=ANY($3)
            ORDER BY LENGTH(object_path) DESC LIMIT 1
            """,
            tid, item.user_path, candidates,
        )

        if row is None:
            results.append({
                "allowed": False,
                "reason": "No ACL entry found",
                "user_path": item.user_path,
                "object_path": item.object_path,
            })
        else:
            results.append({
                "allowed": True,
                "matched_object": row["object_path"],
                "role_path": row["role_path"],
                "user_path": item.user_path,
                "object_path": item.object_path,
            })

    return results


@router.post("/AccessManager/Tenants/{tid}/Action/ListAllowedIds")
async def list_allowed_ids(tid: str, body: ListAllowedIdsRequest):
    """
    Return the resource IDs that a user may access under a type-level prefix.

    Designed for app_callback list_filter_mode: the application calls this
    endpoint on every collection GET to obtain the allowed ID set, then
    filters its own result set accordingly.

    type_prefix must be the runtime collection path without leading slash,
    e.g. "MemoryStore/Tenants/aidp/Instances".

    Returns paginated IDs (last path segment) and total count.
    """
    pool = await get_pool()
    page = max(1, body.page)
    size = min(max(1, body.page_size), 500)
    like_pattern = body.type_prefix.rstrip("/") + "/%"
    depth = body.type_prefix.rstrip("/").count("/") + 2

    total = await pool.fetchval(
        """
        SELECT COUNT(DISTINCT object_path)
        FROM resource_acl
        WHERE tenant_id = $1
          AND user_path = $2
          AND object_path LIKE $3
          AND array_length(string_to_array(object_path, '/'), 1) = $4
        """,
        tid, body.user_path, like_pattern, depth,
    ) or 0

    rows = await pool.fetch(
        """
        SELECT DISTINCT object_path
        FROM resource_acl
        WHERE tenant_id = $1
          AND user_path = $2
          AND object_path LIKE $3
          AND array_length(string_to_array(object_path, '/'), 1) = $4
        ORDER BY object_path
        LIMIT $5 OFFSET $6
        """,
        tid, body.user_path, like_pattern, depth,
        size, (page - 1) * size,
    )
    ids = [r["object_path"].split("/")[-1] for r in rows]
    return {"ids": ids, "total": total, "page": page, "page_size": size}


# ---------------------------------------------------------------------------
# AppObjects — enabled apps + resource object list for permission UI
# ---------------------------------------------------------------------------

def _build_resource_entry(res: Dict[str, Any], object_path: str) -> Dict[str, Any]:
    methods = [
        {"method": m, "display_name": METHOD_LABELS.get(m, m)}
        for m in res.get("methods", [])
    ]
    actions = [
        {
            "name": action.get("name", ""),
            "display_name": action.get("display_name", action.get("name", "")),
            "http_method": action.get("http_method", "POST"),
            "required_role": action.get("required_role", ""),
        }
        for action in res.get("actions", [])
    ]
    return {
        "resource_type": res.get("type", ""),
        "display_name": res.get("display_name", res.get("type", "")),
        "object_path": object_path,
        "methods": methods,
        "actions": actions,
    }


def _collect_objects(
    resources: List[Dict[str, Any]],
    tenant_id: str,
    out: List[Dict[str, Any]],
) -> None:
    """Recursively expand manifest resources into a flat list of type-level object entries."""
    for res in resources:
        pattern = res.get("path_pattern", "")
        m = re.search(r"/\{[^}]+\}$", pattern)
        collection_path = pattern[:m.start()] if m else pattern
        collection_path = re.sub(r"\{[Tt]enant[Ii]d\}", tenant_id, collection_path)
        object_path = collection_path.lstrip("/")

        if object_path and not re.search(r"\{[^}]+\}", object_path):
            out.append(_build_resource_entry(res, object_path))

        _collect_objects(res.get("children", []), tenant_id, out)


def _parse_manifest_json(raw_manifest: str) -> Optional[Dict[str, Any]]:
    try:
        parsed = json.loads(raw_manifest)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


@router.get("/AccessManager/Tenants/{tid}/AppObjects")
async def list_app_objects(tid: str):
    """
    Return all resource object types available to a tenant, grouped by app.

    Only apps with enabled=true in the apps table are included.
    Each object entry carries:
      - object_path  : type-level ACL path ready for resource_acl
      - methods      : HTTP methods with display labels (for UI checkboxes)
      - actions      : custom actions with required_role (for UI checkboxes)

    Frontend maps checked operations → role_path before calling
    PUT .../Groups/{group}/ObjectPermissions:
      GET only                     → Viewer
      GET + PUT/PATCH/POST         → Contributor
      GET + DELETE (or all)        → Owner
      action.required_role         → use that role directly
    """
    pool = await get_pool()

    # 1. Fetch enabled app namespaces
    enabled_rows = await pool.fetch(
        "SELECT app_name FROM apps WHERE enabled = true ORDER BY app_name"
    )
    enabled_namespaces = {r["app_name"] for r in enabled_rows}

    if not enabled_namespaces:
        return {"apps": []}

    # 2. Fetch manifests for enabled apps only
    manifest_rows = await pool.fetch(
        "SELECT namespace, manifest_json FROM app_manifests "
        "WHERE namespace = ANY($1) ORDER BY namespace",
        list(enabled_namespaces),
    )

    apps_out = []
    for row in manifest_rows:
        manifest = _parse_manifest_json(row["manifest_json"])
        if manifest is None:
            continue

        objects: List[Dict[str, Any]] = []
        _collect_objects(manifest.get("resources", []), tid, objects)

        apps_out.append({
            "namespace": row["namespace"],
            "display_name": manifest.get("display_name", row["namespace"]),
            "objects": objects,
        })

    return {"apps": apps_out}


# ---------------------------------------------------------------------------
# Group ObjectPermissions — batch set/clear a group's ACLs on resource objects
# ---------------------------------------------------------------------------

class ObjectPermissionItem(BaseModel):
    object_path: str
    role_path: Optional[str] = None  # null = revoke


class ObjectPermissionsRequest(BaseModel):
    permissions: List[ObjectPermissionItem]


async def _apply_group_object_permission(
    conn,
    tenant_id: str,
    group_path: str,
    caller_path: str,
    item: ObjectPermissionItem,
) -> tuple[int, int]:
    _validate_object_tenant(tenant_id, item.object_path)
    if item.role_path is None:
        return 0, int(await _delete_acl(conn, tenant_id, group_path, item.object_path))

    await _upsert_acl(
        conn,
        tenant_id,
        group_path,
        item.object_path,
        item.role_path,
        caller_path,
    )
    return 1, 0


@router.put("/AccessManager/Tenants/{tid}/Groups/{group_name}/ObjectPermissions")
async def set_group_object_permissions(
    tid: str,
    group_name: str,
    body: ObjectPermissionsRequest,
    request: Request,
):
    """
    Batch set or clear a group's resource-level ACLs.

    Caller must be tenant-admins or master-admins (enforced by pep-proxy path
    rules; this endpoint does a secondary group check for defence-in-depth).

    For each item in permissions:
      - role_path non-null → upsert resource_acl (group, object, role)
      - role_path null     → delete that ACL entry if it exists

    All changes are applied in a single transaction.
    The group's user_path is constructed from tid + group_name; the caller
    does not need to supply it.
    """
    caller_groups_raw = request.headers.get("x-auth-groups", "")
    caller_groups = [g.strip() for g in caller_groups_raw.split(",") if g.strip()]
    if not _is_admin(caller_groups, tid):
        raise HTTPException(status_code=403, detail="Only tenant-admins can manage group permissions")

    group_path = f"AccessManager/Tenants/{tid}/Groups/{group_name}"
    caller_id = request.headers.get("x-auth-user-id", "")
    caller_path = f"AccessManager/Tenants/{tid}/Users/{caller_id}" if caller_id else group_path

    pool = await get_pool()
    upserted = 0
    deleted = 0

    async with pool.acquire() as conn:
        async with conn.transaction():
            for item in body.permissions:
                item_upserted, item_deleted = await _apply_group_object_permission(
                    conn,
                    tid,
                    group_path,
                    caller_path,
                    item,
                )
                upserted += item_upserted
                deleted += item_deleted

    return {
        "status": "ok",
        "group_path": group_path,
        "upserted": upserted,
        "deleted": deleted,
    }
