# app/main.py
from fastapi import FastAPI, HTTPException, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any, List, Optional
import asyncio
import httpx
import logging
import os
from datetime import datetime

_grpc_task: "asyncio.Task | None" = None

from .models import (
    AuthRequest, AuthResponse,
    PathRuleCreate, PathRuleUpdate, PathRuleResponse,
)
from .auth import verify_token, verify_api_key
from . import db
from . import grpc_server

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="PEP Proxy Service",
    description="Policy Enforcement Point with Path-level and Resource-level Authorization",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OPA_URL = os.getenv("OPA_URL", "http://localhost:8181")

# In-memory caches populated at startup and refreshable
_apps: Dict[str, str] = {}                  # path_prefix -> app_name
_resource_patterns: List[Dict[str, str]] = []  # [{app_name, resource_prefix, resource_type}]

# Permission level mapping
PERMISSION_LEVELS = {"viewer": 1, "contributor": 2, "owner": 3}


# ---------------------------------------------------------------------------
# Startup / Shutdown
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup_event():
    global _grpc_task, _apps, _resource_patterns

    # Initialise the database pool
    await db.init_pool()

    # Load apps and resource patterns into memory
    try:
        _apps = await db.load_apps()
        _resource_patterns = await db.load_resource_patterns()
    except Exception as e:
        logger.warning("Failed to load apps/resource_patterns at startup: %s", e)

    # Start the gRPC ext-authz server
    _grpc_task = asyncio.create_task(grpc_server.serve())
    _grpc_task.add_done_callback(_on_grpc_task_done)


@app.on_event("shutdown")
async def shutdown_event():
    await db.close_pool()


def _on_grpc_task_done(task: "asyncio.Task") -> None:
    if task.cancelled():
        logger.warning("gRPC ext-authz task was cancelled")
    elif task.exception():
        logger.error("gRPC ext-authz task failed: %s", task.exception(), exc_info=task.exception())
    else:
        logger.info("gRPC ext-authz task finished cleanly")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "pep-proxy",
        "timestamp": datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# Resource-level auth helper (Phase 4)
# ---------------------------------------------------------------------------

def _match_app(request_path: str) -> Optional[str]:
    """
    Match a request path against the apps table to find the app_name.

    Returns the app_name for the longest matching path_prefix, or None.
    """
    best_match: Optional[str] = None
    best_len = 0
    for prefix, app_name in _apps.items():
        if request_path.startswith(prefix) and len(prefix) > best_len:
            best_match = app_name
            best_len = len(prefix)
    return best_match


def _match_resource_pattern(
    app_name: str, remaining_path: str
) -> Optional[Dict[str, str]]:
    """
    Match the remaining path (after stripping the app prefix) against
    resource_patterns for the given app.

    Returns the best-matching pattern dict or None.
    """
    best: Optional[Dict[str, str]] = None
    best_len = 0
    for pat in _resource_patterns:
        if pat["app_name"] != app_name:
            continue
        rp = pat["resource_prefix"]
        if remaining_path.startswith(rp) and len(rp) > best_len:
            best = pat
            best_len = len(rp)
    return best


def _required_permission(method: str, segment_count: int) -> Optional[str]:
    """
    Determine the required permission level based on HTTP method and
    the number of path segments after the resource_prefix.

    segment_count:
        0 -> collection operation (GET list / POST create): skip ACL check
        1 -> direct resource operation
        2+ -> sub-resource operation on parent
    """
    method_upper = method.upper()

    if segment_count == 0:
        # Collection-level: no resource_acl check needed
        return None

    if segment_count == 1:
        # Direct resource operations
        if method_upper == "GET":
            return "viewer"
        if method_upper in ("PUT", "PATCH"):
            return "contributor"
        if method_upper == "DELETE":
            return "owner"
        # POST on a direct resource ID is unusual; treat as contributor
        return "contributor"

    # 2+ segments: sub-resource operations on parent
    if method_upper == "GET":
        return "viewer"
    # POST / PUT / PATCH / DELETE on sub-resource
    return "contributor"


async def check_resource_auth(
    request_path: str,
    method: str,
    tenant_id: str,
    user_id: str,
    groups: List[str],
) -> Optional[str]:
    """
    Perform resource-level authorization check (Phase 4).

    Returns None if the request is allowed (or resource auth is not applicable).
    Returns a denial reason string if the request should be denied.
    """
    # Step 1: match request path against apps table
    app_name = _match_app(request_path)
    if not app_name:
        # No matching app - resource auth does not apply; allow
        return None

    # Find the matching prefix to strip it
    app_prefix = ""
    for prefix, aname in _apps.items():
        if aname == app_name and request_path.startswith(prefix):
            if len(prefix) > len(app_prefix):
                app_prefix = prefix

    # Strip app prefix to get the remaining path
    remaining = request_path[len(app_prefix):]
    if remaining and not remaining.startswith("/"):
        remaining = "/" + remaining
    remaining = remaining.lstrip("/")

    # Step 2: match remaining path against resource_patterns
    # We need to match with leading slash for consistency
    remaining_with_slash = "/" + remaining if remaining else "/"
    pattern = _match_resource_pattern(app_name, remaining_with_slash)
    if not pattern:
        # No matching resource pattern - resource auth does not apply; allow
        return None

    # Step 3: count path segments after resource_prefix
    resource_prefix = pattern["resource_prefix"]
    resource_type = pattern["resource_type"]
    after_prefix = remaining_with_slash[len(resource_prefix):]
    after_prefix = after_prefix.strip("/")
    segments = [s for s in after_prefix.split("/") if s] if after_prefix else []
    segment_count = len(segments)

    # Step 4: determine required permission
    required = _required_permission(method, segment_count)
    if required is None:
        # Collection-level operation - skip resource_acl check
        return None

    # Extract resource_id:
    #   1 segment  -> segments[0] is the resource_id
    #   2+ segments -> segments[0] is the parent resource_id
    resource_id = segments[0]

    # Step 5: query resource_acl
    try:
        permission = await db.query_resource_acl(
            tenant_id=tenant_id,
            app_name=app_name,
            resource_type=resource_type,
            resource_id=resource_id,
            user_id=user_id,
            groups=groups,
        )
    except Exception as e:
        logger.error("resource_acl query failed: %s", e)
        return "Resource authorization check failed"

    if permission is None:
        return f"No permission on {resource_type}/{resource_id}"

    # Step 6: compare permission levels
    user_level = PERMISSION_LEVELS.get(permission, 0)
    required_level = PERMISSION_LEVELS.get(required, 0)

    if user_level >= required_level:
        return None  # Allowed

    return (
        f"Insufficient permission on {resource_type}/{resource_id}: "
        f"has {permission}, needs {required}"
    )


# ---------------------------------------------------------------------------
# Auth check (delegates to OPA + resource-level)
# ---------------------------------------------------------------------------

@app.post("/api/v1/auth/check", response_model=AuthResponse)
async def check_permission(
    request: AuthRequest,
    user_info: Dict = Depends(verify_token),
):
    opa_input = {
        "input": {
            "token": user_info["token"],
            "user": user_info["user_id"],
            "groups": user_info["groups"],
            "tenant_id": request.tenant_id or user_info["tenant_id"],
            "resource": request.resource,
            "path": request.path or "",
            "method": request.method or "",
            "context": request.context or {},
        }
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(f"{OPA_URL}/v1/data/authz/allow", json=opa_input)
            if response.status_code != 200:
                raise HTTPException(status_code=500, detail="Authorization service error")
            allowed = response.json().get("result", False)

        if not allowed:
            return AuthResponse(
                allowed=False,
                user=user_info["user_id"],
                tenant_id=request.tenant_id or user_info["tenant_id"],
                resource=request.resource,
                reason="Denied by policy",
            )

        # Resource-level auth check (Phase 4)
        denial = await check_resource_auth(
            request_path=request.path or "",
            method=request.method or "GET",
            tenant_id=request.tenant_id or user_info["tenant_id"],
            user_id=user_info["user_id"],
            groups=user_info["groups"],
        )
        if denial:
            return AuthResponse(
                allowed=False,
                user=user_info["user_id"],
                tenant_id=request.tenant_id or user_info["tenant_id"],
                resource=request.resource,
                reason=denial,
            )

        return AuthResponse(
            allowed=True,
            user=user_info["user_id"],
            tenant_id=request.tenant_id or user_info["tenant_id"],
            resource=request.resource,
            reason="Allowed by policy",
        )
    except httpx.RequestError as e:
        logger.error("OPA connection error: %s", e)
        raise HTTPException(status_code=503, detail="Authorization service unavailable")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Permission check error: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# External authz (HTTP - agentgateway)
# ---------------------------------------------------------------------------

@app.post("/api/v1/ext-authz")
async def ext_authz_check(request: Request):
    headers = request.headers
    original_path = headers.get("x-original-path", str(request.url.path))
    method = headers.get("x-original-method", request.method)

    # Authentication: API Key takes priority over Bearer token
    api_key_header = headers.get("x-api-key")
    if api_key_header:
        user_info = await verify_api_key(api_key_header, request_path=original_path)
    else:
        # Fall back to JWT Bearer token authentication
        from fastapi.security import HTTPAuthorizationCredentials
        auth_header = headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing authentication")
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=auth_header[7:]
        )
        user_info = await verify_token(credentials)

    tenant_id = user_info["tenant_id"]
    resource = headers.get("x-authz-resource", "")

    if not resource:
        segments = [s for s in original_path.strip("/").split("/") if s]
        resource = segments[-1] if segments else "unknown"

    opa_input = {
        "input": {
            "token": user_info["token"],
            "user": user_info["user_id"],
            "groups": user_info["groups"],
            "tenant_id": tenant_id,
            "resource": resource,
            "path": original_path,
            "method": method,
            "context": {},
        }
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{OPA_URL}/v1/data/authz/allow", json=opa_input)
            if resp.status_code != 200:
                raise HTTPException(status_code=500, detail="OPA query failed")
            allowed = resp.json().get("result", False)

        if not allowed:
            raise HTTPException(status_code=403, detail="Forbidden by policy")

        # Resource-level auth check (Phase 4)
        denial = await check_resource_auth(
            request_path=original_path,
            method=method,
            tenant_id=tenant_id,
            user_id=user_info["user_id"],
            groups=user_info["groups"],
        )
        if denial:
            raise HTTPException(status_code=403, detail=denial)

        return Response(
            status_code=200,
            headers={
                "x-auth-user": user_info["user_id"],
                "x-auth-tenant": tenant_id,
                "x-auth-groups": ",".join(user_info["groups"]),
            },
        )
    except HTTPException:
        raise
    except httpx.RequestError as e:
        logger.error("OPA connection error in ext-authz: %s", e)
        raise HTTPException(status_code=503, detail="Authorization service unavailable")
    except Exception as e:
        logger.error("ext-authz error: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Path-rules CRUD (Phase 2b)
# ---------------------------------------------------------------------------

@app.post("/api/v1/path-rules", response_model=PathRuleResponse, status_code=201)
async def create_path_rule(
    body: PathRuleCreate,
    user_info: Dict = Depends(verify_token),
):
    """Create a new path rule (admin only)."""
    _require_admin(user_info)
    try:
        row = await db.create_path_rule(
            path_prefix=body.path_prefix,
            required_group=body.required_group,
            description=body.description,
        )
        return PathRuleResponse(**row)
    except Exception as e:
        logger.error("Failed to create path rule: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to create path rule: {e}")


@app.get("/api/v1/path-rules", response_model=List[PathRuleResponse])
async def list_path_rules(
    user_info: Dict = Depends(verify_token),
):
    """List all path rules."""
    try:
        rows = await db.list_path_rules()
        return [PathRuleResponse(**r) for r in rows]
    except Exception as e:
        logger.error("Failed to list path rules: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to list path rules: {e}")


@app.get("/api/v1/path-rules/{rule_id}", response_model=PathRuleResponse)
async def get_path_rule(
    rule_id: int,
    user_info: Dict = Depends(verify_token),
):
    """Get a single path rule by id."""
    try:
        row = await db.get_path_rule(rule_id)
    except Exception as e:
        logger.error("Failed to get path rule: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to get path rule: {e}")

    if row is None:
        raise HTTPException(status_code=404, detail="Path rule not found")
    return PathRuleResponse(**row)


@app.put("/api/v1/path-rules/{rule_id}", response_model=PathRuleResponse)
async def update_path_rule(
    rule_id: int,
    body: PathRuleUpdate,
    user_info: Dict = Depends(verify_token),
):
    """Update a path rule (admin only)."""
    _require_admin(user_info)
    try:
        row = await db.update_path_rule(
            rule_id=rule_id,
            path_prefix=body.path_prefix,
            required_group=body.required_group,
            description=body.description,
        )
    except Exception as e:
        logger.error("Failed to update path rule: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to update path rule: {e}")

    if row is None:
        raise HTTPException(status_code=404, detail="Path rule not found")
    return PathRuleResponse(**row)


@app.delete("/api/v1/path-rules/{rule_id}", status_code=204)
async def delete_path_rule(
    rule_id: int,
    user_info: Dict = Depends(verify_token),
):
    """Delete a path rule (admin only)."""
    _require_admin(user_info)
    try:
        deleted = await db.delete_path_rule(rule_id)
    except Exception as e:
        logger.error("Failed to delete path rule: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to delete path rule: {e}")

    if not deleted:
        raise HTTPException(status_code=404, detail="Path rule not found")
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Refresh in-memory caches
# ---------------------------------------------------------------------------

@app.post("/api/v1/admin/refresh-cache", status_code=200)
async def refresh_cache(user_info: Dict = Depends(verify_token)):
    """Reload apps and resource_patterns from the database (admin only)."""
    _require_admin(user_info)
    global _apps, _resource_patterns
    _apps = await db.load_apps()
    _resource_patterns = await db.load_resource_patterns()
    return {
        "status": "ok",
        "apps_count": len(_apps),
        "resource_patterns_count": len(_resource_patterns),
    }


# ---------------------------------------------------------------------------
# Guard helpers
# ---------------------------------------------------------------------------

def _require_admin(user_info: Dict):
    groups = user_info.get("groups", [])
    if "master-admins" not in groups and "tenant-admins" not in groups:
        raise HTTPException(status_code=403, detail="Admin access required")


def _require_same_tenant(requested_tenant: str, user_info: Dict):
    groups = user_info.get("groups", [])
    if "master-admins" in groups:
        return
    if requested_tenant != user_info["tenant_id"]:
        raise HTTPException(status_code=403, detail="Cannot operate on other tenant")
