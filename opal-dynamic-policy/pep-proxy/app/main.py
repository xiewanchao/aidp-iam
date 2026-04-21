# app/main.py
from fastapi import FastAPI, HTTPException, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any, List, Optional
from urllib.parse import parse_qs, urlsplit
import asyncio
import httpx
import json
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
_apps: Dict[str, str] = {}                       # path_prefix -> app_name
_resource_patterns: List[Dict[str, Any]] = []    # list of resource_patterns rows
_resource_actions: List[Dict[str, Any]] = []     # list of resource_actions rows

# Permission level mapping
# 'none' = 0 means no resource_acl check needed
PERMISSION_LEVELS = {"none": 0, "viewer": 1, "contributor": 2, "owner": 3}


# Default per-HTTP-method action rules used when no resource_actions row
# matches. Mirrors standard RESTful semantics.
#
# The special path_suffix "/{id}" means "one extra segment after the
# resource_prefix" when id_source == 'path'. For id_source != 'path' the
# "/{id}" matcher becomes "path equals resource_prefix" (ID lives elsewhere).
DEFAULT_ACTIONS: List[Dict[str, Any]] = [
    {"action": "create", "method": "POST",   "path_suffix": None,    "success_status": 201,  "min_permission": "none"},
    {"action": "list",   "method": "GET",    "path_suffix": None,    "success_status": None, "min_permission": "none"},
    {"action": "read",   "method": "GET",    "path_suffix": "/{id}", "success_status": None, "min_permission": "viewer"},
    {"action": "update", "method": "PUT",    "path_suffix": "/{id}", "success_status": None, "min_permission": "contributor"},
    {"action": "update", "method": "PATCH",  "path_suffix": "/{id}", "success_status": None, "min_permission": "contributor"},
    {"action": "delete", "method": "DELETE", "path_suffix": "/{id}", "success_status": 200,  "min_permission": "owner"},
]


# ---------------------------------------------------------------------------
# Startup / Shutdown
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup_event():
    global _grpc_task, _apps, _resource_patterns, _resource_actions

    # Initialise the database pool
    await db.init_pool()

    # Load apps, resource patterns and resource actions into memory
    try:
        _apps = await db.load_apps()
        _resource_patterns = await db.load_resource_patterns()
        _resource_actions = await db.load_resource_actions()
    except Exception as e:
        logger.warning(
            "Failed to load apps/resource_patterns/resource_actions at startup: %s", e
        )

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
) -> Optional[Dict[str, Any]]:
    """
    Match the remaining path (after stripping the app prefix) against
    resource_patterns for the given app.

    The match is prefix-based: a pattern matches when remaining_path either
    equals its resource_prefix or starts with "{resource_prefix}/". This
    prevents "/v1/kbfoo" from matching "/v1/kb".

    Returns the best-matching (longest resource_prefix) pattern dict or None.
    """
    best: Optional[Dict[str, Any]] = None
    best_len = 0
    for pat in _resource_patterns:
        if pat["app_name"] != app_name:
            continue
        rp = pat["resource_prefix"]
        if remaining_path == rp or remaining_path.startswith(rp + "/"):
            if len(rp) > best_len:
                best = pat
                best_len = len(rp)
    return best


def find_action_rule(
    pattern: Dict[str, Any],
    method: str,
    path: str,
    actions: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Find the best matching action rule for a request.

    Matching order (most specific first):
      1. resource_actions rows with explicit (non-NULL) path_suffix that
         equals the actual sub-path after resource_prefix.
      2. resource_actions rows with NULL path_suffix (matches resource_prefix
         itself).
      3. DEFAULT_ACTIONS fallback (standard RESTful).

    The special DEFAULT_ACTIONS "/{id}" suffix means "exactly one extra path
    segment after resource_prefix" when id_source == 'path'. For id_source
    != 'path', "/{id}" collapses to "the path equals resource_prefix" because
    the ID lives in the query string or body, not the URL path.
    """
    method_upper = (method or "").upper()
    resource_prefix = pattern["resource_prefix"]
    id_source = pattern.get("id_source", "path")

    # Slice off the resource_prefix portion to get the sub-path.
    # sub_path == "" means the request path equals resource_prefix.
    if path == resource_prefix:
        sub_path = ""
    elif path.startswith(resource_prefix + "/"):
        sub_path = path[len(resource_prefix):]  # keeps the leading '/'
    else:
        # Shouldn't happen because _match_resource_pattern already filtered,
        # but be defensive.
        return None

    # --- Pass 1: DB-sourced rules with explicit path_suffix ---------------
    # Longest path_suffix wins when multiple rows match.
    best_explicit: Optional[Dict[str, Any]] = None
    best_explicit_len = -1
    for rule in actions:
        if rule.get("app_name") != pattern["app_name"]:
            continue
        if rule.get("resource_prefix") != resource_prefix:
            continue
        if (rule.get("method") or "").upper() != method_upper:
            continue
        suffix = rule.get("path_suffix")
        if suffix is None:
            continue
        if sub_path == suffix or sub_path.endswith(suffix):
            if len(suffix) > best_explicit_len:
                best_explicit = rule
                best_explicit_len = len(suffix)

    if best_explicit is not None:
        return best_explicit

    # --- Pass 2: DB-sourced rules with NULL path_suffix -------------------
    # A NULL path_suffix matches the resource_prefix itself (sub_path empty)
    # for path-based IDs, or any request for non-path IDs.
    for rule in actions:
        if rule.get("app_name") != pattern["app_name"]:
            continue
        if rule.get("resource_prefix") != resource_prefix:
            continue
        if (rule.get("method") or "").upper() != method_upper:
            continue
        if rule.get("path_suffix") is not None:
            continue
        if id_source == "path":
            if sub_path == "":
                return rule
        else:
            # For query/body ID extraction there's no per-ID segment in path;
            # a NULL-suffix rule is the canonical match.
            return rule

    # --- Pass 3: DEFAULT_ACTIONS fallback ---------------------------------
    # Evaluate rules whose method matches and whose suffix semantics fit.
    # Prefer "/{id}" (more specific) over NULL when applicable.
    default_explicit: Optional[Dict[str, Any]] = None
    default_null: Optional[Dict[str, Any]] = None

    # For path-based IDs, "/{id}" means exactly one extra segment.
    # For query/body IDs, "/{id}" collapses to sub_path == "".
    segments = [s for s in sub_path.strip("/").split("/") if s]
    segment_count = len(segments)

    for rule in DEFAULT_ACTIONS:
        if rule["method"] != method_upper:
            continue
        suffix = rule["path_suffix"]
        if suffix == "/{id}":
            if id_source == "path":
                # Exactly one path segment after resource_prefix.
                if segment_count == 1 and default_explicit is None:
                    default_explicit = rule
            else:
                # For non-path IDs, treat "/{id}" as "path == resource_prefix".
                if sub_path == "" and default_explicit is None:
                    default_explicit = rule
        elif suffix is None:
            # Collection-level: path == resource_prefix.
            if sub_path == "" and default_null is None:
                default_null = rule

    if default_explicit is not None:
        return default_explicit
    return default_null


def extract_resource_id(
    pattern: Dict[str, Any],
    path: str,
    query_params: Dict[str, str],
    body_bytes: bytes,
) -> Optional[str]:
    """
    Extract resource_id according to pattern.id_source.

    - 'path'  : first segment after resource_prefix in the URL path.
    - 'query' : query parameter named pattern['id_query_param'].
    - 'body'  : JSON body field (supports dotted nested keys, e.g. 'data.id').
    """
    id_source = pattern.get("id_source", "path")
    resource_prefix = pattern["resource_prefix"]

    if id_source == "path":
        if path == resource_prefix:
            return None
        if path.startswith(resource_prefix + "/"):
            sub = path[len(resource_prefix) + 1:]
        else:
            sub = ""
        sub = sub.strip("/")
        return sub.split("/")[0] if sub else None

    if id_source == "query":
        qp_name = pattern.get("id_query_param")
        if not qp_name:
            return None
        value = query_params.get(qp_name)
        return str(value) if value else None

    if id_source == "body":
        if not body_bytes:
            return None
        try:
            obj: Any = json.loads(body_bytes)
        except Exception:
            return None
        field = pattern.get("id_field") or "id"
        for key in field.split("."):
            if isinstance(obj, dict):
                obj = obj.get(key)
            else:
                return None
        if obj is None:
            return None
        return str(obj)

    return None


def _parse_query_params(raw_path: str) -> Dict[str, str]:
    """
    Parse query string out of a raw request path/URI.

    Returns a flat dict; repeated keys yield the first value.
    """
    if not raw_path:
        return {}
    try:
        parts = urlsplit(raw_path)
        qs = parts.query
    except Exception:
        # Fall back to naive split
        if "?" in raw_path:
            qs = raw_path.split("?", 1)[1]
        else:
            qs = ""
    if not qs:
        return {}
    parsed = parse_qs(qs, keep_blank_values=True)
    return {k: (v[0] if v else "") for k, v in parsed.items()}


async def check_resource_auth(
    request_path: str,
    method: str,
    tenant_id: str,
    user_id: str,
    groups: List[str],
    body_bytes: bytes = b"",
) -> Optional[str]:
    """
    Perform resource-level authorization check.

    Returns None when the request is allowed (or resource auth does not
    apply). Returns a denial reason string otherwise.

    request_path may include a query string; it is split here so that
    callers don't need to pre-parse it.
    body_bytes is optional raw request body used when an action extracts the
    resource_id from the body.
    """
    # Separate query string from the bare URL path.
    query_params: Dict[str, str] = {}
    if "?" in request_path:
        query_params = _parse_query_params(request_path)
        bare_path = request_path.split("?", 1)[0]
    else:
        bare_path = request_path

    # Step 1: match request path against apps table
    app_name = _match_app(bare_path)
    if not app_name:
        # No matching app - resource auth does not apply; allow
        return None

    # Find the matching prefix to strip it
    app_prefix = ""
    for prefix, aname in _apps.items():
        if aname == app_name and bare_path.startswith(prefix):
            if len(prefix) > len(app_prefix):
                app_prefix = prefix

    # Strip app prefix to get the remaining path (re-add leading slash)
    remaining = bare_path[len(app_prefix):]
    if remaining and not remaining.startswith("/"):
        remaining = "/" + remaining
    if not remaining:
        remaining = "/"

    # Step 2: match remaining path against resource_patterns
    pattern = _match_resource_pattern(app_name, remaining)
    if not pattern:
        # No matching resource pattern - resource auth does not apply; allow
        return None

    resource_type = pattern["resource_type"]

    # Step 3: pick the action rule that governs this (method, path) pair.
    rule = find_action_rule(pattern, method, remaining, _resource_actions)
    if rule is None:
        # No matching action rule and no default fallback - allow by default.
        return None

    required = (rule.get("min_permission") or "none").lower()

    # Step 4: 'none' means no ACL check is needed (create / list / public).
    if required == "none" or PERMISSION_LEVELS.get(required, 0) == 0:
        return None

    # Step 5: extract resource_id according to pattern.id_source.
    resource_id = extract_resource_id(pattern, remaining, query_params, body_bytes)
    if not resource_id:
        # Can't identify a resource to check - deny explicitly. This is
        # safer than allowing since min_permission is non-'none'.
        return (
            f"Unable to extract resource_id for {resource_type} "
            f"(id_source={pattern.get('id_source')})"
        )

    # Step 6: query resource_acl
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

    # Step 7: compare permission levels
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
# External authz (HTTP - Envoy Gateway)
# ---------------------------------------------------------------------------

@app.post("/api/v1/ext-authz")
async def ext_authz_check(request: Request):
    headers = request.headers
    original_path = headers.get("x-original-path", str(request.url.path))
    method = headers.get("x-original-method", request.method)

    # Read the forwarded body (if any) so body-based resource_id extraction
    # works for id_source='body'. It's safe to read it here because this
    # endpoint's own request body is the forwarded payload.
    try:
        forwarded_body = await request.body()
    except Exception:
        forwarded_body = b""

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
            body_bytes=forwarded_body,
        )
        if denial:
            raise HTTPException(status_code=403, detail=denial)

        return Response(
            status_code=200,
            headers={
                "X-Auth-User-Id": user_info["user_id"],
                "X-Auth-Tenant": tenant_id,
                "X-Auth-Groups": ",".join(user_info["groups"]),
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
            method=body.method,
            required_groups=body.required_groups,
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
            method=body.method if body.method is not None else "__unset__",
            required_groups=body.required_groups,
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
    """Reload apps, resource_patterns and resource_actions from the database (admin only)."""
    _require_admin(user_info)
    global _apps, _resource_patterns, _resource_actions
    _apps = await db.load_apps()
    _resource_patterns = await db.load_resource_patterns()
    _resource_actions = await db.load_resource_actions()
    return {
        "status": "ok",
        "apps_count": len(_apps),
        "resource_patterns_count": len(_resource_patterns),
        "resource_actions_count": len(_resource_actions),
    }


# ---------------------------------------------------------------------------
# Guard helpers
# ---------------------------------------------------------------------------

def _require_admin(user_info: Dict):
    # Single-tenant model: only the `admins` group is admin.
    groups = user_info.get("groups", [])
    if "admins" not in groups:
        raise HTTPException(status_code=403, detail="Admin access required")


def _require_same_tenant(requested_tenant: str, user_info: Dict):
    groups = user_info.get("groups", [])
    if "admins" in groups:
        return
    if requested_tenant != user_info["tenant_id"]:
        raise HTTPException(status_code=403, detail="Cannot operate on other tenant")
