"""
ACL Management API for AccessManager.

Endpoints:
  PUT    /AccessManager/Tenants/{tid}/ACLs                              Grant/update a permission
  GET    /AccessManager/Tenants/{tid}/ACLs                              Query ACLs by object or user
  DELETE /AccessManager/Tenants/{tid}/ACLs                              Revoke a permission
  POST   /AccessManager/Tenants/{tid}/Action/QueryACLs                  Batch check permissions
  GET    /AccessManager/Tenants/{tid}/AppObjects                        List enabled apps + their resource objects
  PUT    /AccessManager/Tenants/{tid}/Groups/{group_name}/ObjectPermissions  Batch set group ACLs
"""

import json
import re
from fastapi import APIRouter, HTTPException, Request, Query as QParam
from pydantic import BaseModel
from typing import Any, Dict, List, Optional
from app.core.db import get_pool

router = APIRouter(tags=["ACLs"])

OWNER_ROLE = "AccessManager/Tenants/System/Roles/Owner"
DEFAULT_ROLE_MATRIX = {
    "AccessManager/Tenants/System/Roles/Owner":       {"GET", "PUT", "PATCH", "DELETE", "POST"},
    "AccessManager/Tenants/System/Roles/Contributor": {"GET", "PUT", "PATCH", "POST"},
    "AccessManager/Tenants/System/Roles/Viewer":      {"GET"},
}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class AclEntry(BaseModel):
    user_path: str
    object_path: str
    role_path: str


class AclDeleteRequest(BaseModel):
    user_path: str
    object_path: str


class QueryAclItem(BaseModel):
    user_path: str
    object_path: str


class QueryAclRequest(BaseModel):
    queries: List[QueryAclItem]


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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.put("/AccessManager/Tenants/{tid}/ACLs")
async def grant_acl(tid: str, body: AclEntry, request: Request):
    """Grant or update a permission. Caller must be Owner of the object or admin."""
    caller_id = request.headers.get("x-auth-user-id", "")
    caller_groups_raw = request.headers.get("x-auth-groups", "")
    caller_groups = [g.strip() for g in caller_groups_raw.split(",") if g.strip()]
    caller_path = f"AccessManager/Tenants/{tid}/Users/{caller_id}" if caller_id else ""

    obj_parts = body.object_path.split("/")
    if len(obj_parts) >= 3 and obj_parts[1] == "Tenants" and obj_parts[2] != tid:
        raise HTTPException(status_code=400, detail="Cross-tenant ACL not allowed")

    pool = await get_pool()

    if not _is_admin(caller_groups, tid):
        caller_role = await _get_caller_role(pool, tid, caller_path, body.object_path)
        if caller_role != OWNER_ROLE:
            raise HTTPException(status_code=403, detail="Only Owner can grant permissions")
        if _role_level(body.role_path) > _role_level(caller_role):
            raise HTTPException(status_code=403, detail="Cannot grant a role higher than your own")

    await pool.execute(
        """
        INSERT INTO resource_acl (tenant_id, user_path, object_path, role_path, created_by)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (tenant_id, user_path, object_path)
        DO UPDATE SET role_path=$4, created_by=$5
        """,
        tid, body.user_path, body.object_path, body.role_path, caller_path,
    )
    return {"status": "ok", "user_path": body.user_path, "object_path": body.object_path, "role_path": body.role_path}


@router.get("/AccessManager/Tenants/{tid}/ACLs")
async def query_acls(
    tid: str,
    object: Optional[str] = QParam(None, description="Filter by object path"),
    user: Optional[str] = QParam(None, description="Filter by user/group path"),
):
    """Query ACLs by object path or user path."""
    if not object and not user:
        raise HTTPException(status_code=400, detail="Provide ?object= or ?user=")

    pool = await get_pool()
    if object:
        rows = await pool.fetch(
            """
            SELECT user_path, object_path, role_path, created_at, created_by
            FROM resource_acl WHERE tenant_id=$1 AND object_path=$2
            ORDER BY created_at
            """,
            tid, object,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT user_path, object_path, role_path, created_at, created_by
            FROM resource_acl WHERE tenant_id=$1 AND user_path=$2
            ORDER BY object_path
            """,
            tid, user,
        )
    return {"acls": [dict(r) for r in rows], "count": len(rows)}


@router.delete("/AccessManager/Tenants/{tid}/ACLs")
async def revoke_acl(tid: str, body: AclDeleteRequest, request: Request):
    """Revoke a permission. Caller must be Owner of the object or admin."""
    caller_id = request.headers.get("x-auth-user-id", "")
    caller_groups_raw = request.headers.get("x-auth-groups", "")
    caller_groups = [g.strip() for g in caller_groups_raw.split(",") if g.strip()]
    caller_path = f"AccessManager/Tenants/{tid}/Users/{caller_id}" if caller_id else ""

    pool = await get_pool()

    if not _is_admin(caller_groups, tid):
        caller_role = await _get_caller_role(pool, tid, caller_path, body.object_path)
        if caller_role != OWNER_ROLE:
            raise HTTPException(status_code=403, detail="Only Owner can revoke permissions")

    result = await pool.execute(
        "DELETE FROM resource_acl WHERE tenant_id=$1 AND user_path=$2 AND object_path=$3",
        tid, body.user_path, body.object_path,
    )
    deleted = int(result.split()[-1])
    if not deleted:
        raise HTTPException(status_code=404, detail="ACL entry not found")
    return {"status": "deleted", "user_path": body.user_path, "object_path": body.object_path}


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


# ---------------------------------------------------------------------------
# AppObjects — enabled apps + resource object list for permission UI
# ---------------------------------------------------------------------------

def _collect_objects(
    resources: List[Dict[str, Any]],
    tenant_id: str,
    out: List[Dict[str, Any]],
) -> None:
    """
    Recursively expand manifest resources[] into a flat list of type-level
    object entries for the permission UI.

    Each entry includes:
      - object_path  : type-level ACL path, ready for resource_acl
      - methods      : HTTP methods declared in the manifest (GET/PUT/PATCH/DELETE)
      - actions      : custom actions (name, display_name, required_role)

    Sub-resources that still carry a parent-ID placeholder (e.g. {kbId}) are
    skipped — their permissions are inherited via prefix-matching from the
    parent type.

    Frontend mapping convention (methods → role_path):
      GET only                          → Viewer
      GET + any of PUT/PATCH/POST       → Contributor
      GET + DELETE (or all methods)     → Owner
      action.required_role              → use that role directly
    """
    # Method display names for the UI
    METHOD_LABELS: Dict[str, str] = {
        "GET":    "查看",
        "PUT":    "创建",
        "PATCH":  "编辑",
        "POST":   "创建",
        "DELETE": "删除",
    }

    for res in resources:
        pattern = res.get("path_pattern", "")
        # Strip the last /{param} to get the collection-level path
        m = re.search(r"/\{[^}]+\}$", pattern)
        collection_path = pattern[:m.start()] if m else pattern
        # Replace {tenantId} placeholder with actual tenant
        collection_path = re.sub(r"\{[Tt]enant[Ii]d\}", tenant_id, collection_path)
        # Remove leading slash — object_path convention has no leading slash
        object_path = collection_path.lstrip("/")

        # Skip if any unresolved {param} remains (sub-resource with parent ID in path)
        if object_path and not re.search(r"\{[^}]+\}", object_path):
            # Build method list with display labels
            methods = [
                {
                    "method":       method,
                    "display_name": METHOD_LABELS.get(method, method),
                }
                for method in res.get("methods", [])
            ]

            # Build action list — include required_role so frontend knows
            # which role to assign when this action is checked
            actions = [
                {
                    "name":          action.get("name", ""),
                    "display_name":  action.get("display_name", action.get("name", "")),
                    "http_method":   action.get("http_method", "POST"),
                    "required_role": action.get("required_role", ""),
                }
                for action in res.get("actions", [])
            ]

            out.append({
                "resource_type": res.get("type", ""),
                "display_name":  res.get("display_name", res.get("type", "")),
                "object_path":   object_path,
                "methods":       methods,
                "actions":       actions,
            })

        # Always recurse into children so deeply-nested top-level objects are found
        _collect_objects(res.get("children", []), tenant_id, out)


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
        try:
            manifest: Dict[str, Any] = json.loads(row["manifest_json"])
        except Exception:
            continue

        objects: List[Dict[str, Any]] = []
        _collect_objects(manifest.get("resources", []), tid, objects)

        apps_out.append({
            "namespace":    row["namespace"],
            "display_name": manifest.get("display_name", row["namespace"]),
            "objects":      objects,
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
                # Reject cross-tenant object_paths
                parts = item.object_path.split("/")
                if len(parts) >= 3 and parts[1] == "Tenants" and parts[2] != tid:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cross-tenant object_path not allowed: {item.object_path}",
                    )

                if item.role_path is not None:
                    await conn.execute(
                        """
                        INSERT INTO resource_acl
                            (tenant_id, user_path, object_path, role_path, created_by)
                        VALUES ($1, $2, $3, $4, $5)
                        ON CONFLICT (tenant_id, user_path, object_path)
                        DO UPDATE SET role_path = $4, created_by = $5
                        """,
                        tid, group_path, item.object_path, item.role_path, caller_path,
                    )
                    upserted += 1
                else:
                    result = await conn.execute(
                        "DELETE FROM resource_acl "
                        "WHERE tenant_id=$1 AND user_path=$2 AND object_path=$3",
                        tid, group_path, item.object_path,
                    )
                    if result.endswith("1"):
                        deleted += 1

    return {
        "status": "ok",
        "group_path": group_path,
        "upserted": upserted,
        "deleted": deleted,
    }

    return results
