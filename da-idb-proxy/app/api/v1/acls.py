"""
ACL Management API for AccessManager.

Endpoints:
  PUT    /AccessManager/Tenants/{tid}/ACLs          Grant/update a permission
  GET    /AccessManager/Tenants/{tid}/ACLs          Query ACLs by object or user
  DELETE /AccessManager/Tenants/{tid}/ACLs          Revoke a permission
  POST   /AccessManager/Tenants/{tid}/Action/QueryACLs  Batch check permissions
"""

from fastapi import APIRouter, HTTPException, Request, Query as QParam
from pydantic import BaseModel
from typing import List, Optional
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
