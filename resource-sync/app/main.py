"""
resource-sync FastAPI application — ACL Management API on port 8080.

Startup sequence:
  1. Initialise asyncpg connection pool.
  2. Load apps and resource_patterns into memory.
  3. Start the pending_acl retry worker as a background task.
  4. Start the gRPC ext_proc server on port 8082 as a background task.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from . import db
from . import ext_proc_server
from . import retry_worker
from .models import PermissionCreate, PermissionResponse, PermissionUpdate

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_grpc_task: asyncio.Task | None = None
_retry_task: asyncio.Task | None = None

app = FastAPI(
    title="Resource Sync Service",
    description="ACL Management API + Envoy ext_proc gRPC for automatic resource ACL synchronisation",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Startup / Shutdown
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup_event():
    global _grpc_task, _retry_task

    # 1. Database pool
    await db.init_pool()

    # 2. Load reference data into memory (shared with ext_proc_server)
    ext_proc_server.apps = await db.load_apps()
    ext_proc_server.resource_patterns = await db.load_resource_patterns()
    ext_proc_server.resource_actions = await db.load_resource_actions()
    logger.info(
        "Loaded %d apps, %d resource_patterns, %d resource_actions",
        len(ext_proc_server.apps),
        len(ext_proc_server.resource_patterns),
        len(ext_proc_server.resource_actions),
    )

    # 3. Start retry worker
    _retry_task = asyncio.create_task(retry_worker.run_retry_loop())
    _retry_task.add_done_callback(_on_task_done("retry_worker"))

    # 4. Start gRPC ext_proc server
    _grpc_task = asyncio.create_task(ext_proc_server.serve())
    _grpc_task.add_done_callback(_on_task_done("grpc_ext_proc"))


@app.on_event("shutdown")
async def shutdown_event():
    if _retry_task and not _retry_task.done():
        _retry_task.cancel()
    if _grpc_task and not _grpc_task.done():
        _grpc_task.cancel()
    await db.close_pool()


def _on_task_done(name: str):
    """Return a done-callback that logs task completion / failure."""
    def _callback(task: asyncio.Task) -> None:
        if task.cancelled():
            logger.warning("%s task was cancelled", name)
        elif task.exception():
            logger.error(
                "%s task failed: %s", name, task.exception(),
                exc_info=task.exception(),
            )
        else:
            logger.info("%s task finished cleanly", name)
    return _callback


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_resource(resource_id: str, app_name: str, resource_type: str) -> tuple[str, str]:
    """
    Resolve app_name and resource_type.

    If the caller provides them in the request body (POST), use those.
    Otherwise, attempt to look them up from the in-memory resource_patterns
    — but this is best-effort since we may not have path context in the
    REST API call.
    """
    if app_name and resource_type:
        return app_name, resource_type
    # Fallback: cannot determine without path context
    raise HTTPException(
        status_code=400,
        detail="app_name and resource_type are required",
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "resource-sync",
        "timestamp": datetime.utcnow().isoformat(),
        "apps_loaded": len(ext_proc_server.apps),
        "resource_patterns_loaded": len(ext_proc_server.resource_patterns),
        "resource_actions_loaded": len(ext_proc_server.resource_actions),
    }


# ---------------------------------------------------------------------------
# ACL Management API
# ---------------------------------------------------------------------------

@app.post("/acl/v1/resources/{resource_id}/permissions", response_model=PermissionResponse)
async def share_resource(resource_id: str, body: PermissionCreate, request: Request):
    """
    Share a resource with a user or group.

    Reads the authenticated user's identity from headers injected by
    pep-proxy (X-Auth-User-Id, X-Auth-Tenant, X-Auth-Groups).
    Owner check (per diagrams/story-breakdown.md SR05): the caller must own
    the resource (resource_acl row with permission=owner) before sharing.
    """
    tenant_id = request.headers.get("x-auth-tenant", "")
    user_id = request.headers.get("x-auth-user-id", "")
    if not tenant_id or not user_id:
        raise HTTPException(status_code=401, detail="Missing X-Auth-* headers")

    app_name, resource_type = _resolve_resource(
        resource_id, body.app_name, body.resource_type,
    )

    # Owner check — caller must be owner of the resource
    caller_perm = await db.query_permission(
        tenant_id, app_name, resource_type, resource_id, "user", user_id,
    )
    if caller_perm != "owner":
        raise HTTPException(
            status_code=403,
            detail="Only the resource owner can manage permissions",
        )

    try:
        row = await db.add_permission(
            tenant_id, app_name, resource_type, resource_id,
            body.subject_type, body.subject_id, body.permission,
        )
        return PermissionResponse(**row)
    except Exception as exc:
        if "unique" in str(exc).lower():
            raise HTTPException(
                status_code=409,
                detail="Permission already exists for this subject on this resource",
            )
        logger.error("Failed to add permission: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/acl/v1/resources/{resource_id}/permissions")
async def list_resource_permissions(
    resource_id: str,
    request: Request,
    app_name: str = Query(..., description="Application name"),
    resource_type: str = Query(..., description="Resource type"),
):
    """List all permissions for a specific resource."""
    tenant_id = request.headers.get("x-auth-tenant", "")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Missing X-Auth-Tenant header")

    rows = await db.list_permissions(tenant_id, app_name, resource_type, resource_id)
    return {
        "permissions": [PermissionResponse(**r).model_dump() for r in rows],
        "count": len(rows),
    }


async def _require_owner(request: Request, resource_id: str, acl_id: int) -> tuple[str, str]:
    """Resolve (tenant_id, user_id) and verify caller is owner of resource_id."""
    tenant_id = request.headers.get("x-auth-tenant", "")
    user_id = request.headers.get("x-auth-user-id", "")
    if not tenant_id or not user_id:
        raise HTTPException(status_code=401, detail="Missing X-Auth-* headers")
    row = await db.get_permission_row(acl_id)
    if not row or row["resource_id"] != resource_id or row["tenant_id"] != tenant_id:
        raise HTTPException(status_code=404, detail="Permission not found")
    caller_perm = await db.query_permission(
        tenant_id, row["app_name"], row["resource_type"], resource_id, "user", user_id,
    )
    if caller_perm != "owner":
        raise HTTPException(status_code=403, detail="Only the resource owner can manage permissions")
    return tenant_id, user_id


@app.put("/acl/v1/resources/{resource_id}/permissions/{acl_id}")
async def update_resource_permission(
    resource_id: str,
    acl_id: int,
    body: PermissionUpdate,
    request: Request,
):
    """Update the permission level of an existing ACL entry. Requires caller to be owner."""
    await _require_owner(request, resource_id, acl_id)
    updated = await db.update_permission(acl_id, body.permission)
    if not updated:
        raise HTTPException(status_code=404, detail="Permission not found")
    return {"status": "updated", "id": acl_id, "permission": body.permission}


@app.delete("/acl/v1/resources/{resource_id}/permissions/{acl_id}")
async def delete_resource_permission(resource_id: str, acl_id: int, request: Request):
    """Remove a specific permission entry (unshare). Requires caller to be owner."""
    await _require_owner(request, resource_id, acl_id)
    deleted = await db.delete_permission(acl_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Permission not found")
    return {"status": "deleted", "id": acl_id}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8080, log_level="info")
