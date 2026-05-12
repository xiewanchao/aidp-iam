# app/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any, List, Optional
import asyncio
import httpx
import logging
import os
from datetime import datetime

_grpc_task: "asyncio.Task | None" = None

from .models import AuthRequest, AuthResponse
from .auth import verify_token, verify_api_key
from . import db
from . import grpc_server

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    global _grpc_task
    await db.init_pool()
    _grpc_task = asyncio.create_task(grpc_server.serve())
    _grpc_task.add_done_callback(_on_grpc_task_done)
    yield
    await db.close_pool()


app = FastAPI(
    title="PEP Proxy Service",
    description="Policy Enforcement Point — unified path-based ACL authorization",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OPA_URL = os.getenv("OPA_URL", "http://localhost:8181")
DEFAULT_ROLE_MATRIX: Dict[str, set] = {
    "AccessManager/Tenants/System/Roles/Owner":       {"GET", "PUT", "PATCH", "DELETE", "POST"},
    "AccessManager/Tenants/System/Roles/Contributor": {"GET", "PUT", "PATCH", "POST"},
    "AccessManager/Tenants/System/Roles/Viewer":      {"GET"},
}

DEFAULT_ROLE_NAMESPACE = "AccessManager"


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
# Unified URL parsing
# ---------------------------------------------------------------------------

def parse_unified_url(path: str) -> Optional[Dict[str, Any]]:
    """
    Parse a unified URL path into components.

    Format: /<NS>/Tenants/<tid>/<TypeA>/<IDA>[/<TypeB>/<IDB>...][/<Action>]

    Returns dict with namespace, tenant_id, object_path, is_collection.
    Returns None when path does not match the unified format.
    """
    parts = path.lstrip("/").split("/")
    # Minimum: NS/Tenants/tid/Type (4 parts)
    if len(parts) < 4 or parts[1] != "Tenants":
        return None
    namespace = parts[0]
    tenant_id = parts[2]
    object_path = "/".join(parts)
    # Even number of resource parts (after NS/Tenants/tid) → collection
    resource_parts = parts[3:]
    is_collection = (len(resource_parts) % 2 == 1)
    return {
        "namespace": namespace,
        "tenant_id": tenant_id,
        "object_path": object_path,
        "is_collection": is_collection,
    }


def _is_admin_group(groups: List[str], tenant_id: str) -> bool:
    # Only tenant-admins and admins bypass resource-level ACL checks.
    # master-admins is a cross-tenant IAM role (creates tenant-admins etc.)
    # and should NOT bypass per-resource ACL enforcement.
    admin_paths = {
        f"AccessManager/Tenants/{tenant_id}/Groups/tenant-admins",
        f"AccessManager/Tenants/{tenant_id}/Groups/admins",
    }
    return bool(admin_paths & set(groups))


def _collection_prefix(object_path: str, tenant_id: str, is_collection: bool) -> str:
    """
    Convert a runtime object_path to the resource_prefix key stored in
    resource_patterns (manifest template form with leading slash and
    {tenantId} placeholder).

    Examples:
      "DataAgent/Tenants/t-001/DataAgentDBs"        (collection) →
        "/DataAgent/Tenants/{tenantId}/DataAgentDBs"
      "DataAgent/Tenants/t-001/DataAgentDBs/db-001" (instance) →
        "/DataAgent/Tenants/{tenantId}/DataAgentDBs"
      "KnowledgeBase/Tenants/System/ModelConfigs"   (system) →
        "/KnowledgeBase/Tenants/System/ModelConfigs"
    """
    path = object_path if is_collection else object_path.rsplit("/", 1)[0]
    if tenant_id and tenant_id != "System":
        path = path.replace(f"/Tenants/{tenant_id}/", "/Tenants/{tenantId}/", 1)
    return "/" + path


async def _callback_check(
    namespace: str,
    tenant_id: str,
    user_path: str,
    object_path: str,
    role_path: str,
    method: str,
) -> Optional[str]:
    """
    Call the application's QueryACLs callback for custom roles.
    Returns None if allowed, denial reason string if denied.
    """
    callback_url = await db.get_callback_url(namespace)
    if not callback_url:
        return f"No callback URL registered for namespace {namespace}"

    payload = {
        "user": user_path,
        "object": object_path,
        "role": role_path,
        "action": method.upper(),
    }
    try:
        async with httpx.AsyncClient(timeout=0.5) as client:
            resp = await client.post(callback_url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("allowed"):
                    return None
                return data.get("reason", "Denied by application callback")
            return f"Callback returned HTTP {resp.status_code}"
    except httpx.TimeoutException:
        return f"Callback timeout for namespace {namespace}"
    except Exception as exc:
        logger.error("Callback error for %s: %s", namespace, exc)
        return f"Callback error: {exc}"


# ---------------------------------------------------------------------------
# Resource-level auth (v2.0 unified path-based)
# ---------------------------------------------------------------------------

async def check_resource_auth(
    request_path: str,
    method: str,
    tenant_id: str,
    user_path: str,
    groups: List[str],
) -> Optional[str]:
    """
    Perform resource-level authorization check using unified URL format.

    Returns None when allowed (or not applicable).
    Returns a denial reason string when denied.
    """
    # Strip query string
    bare_path = request_path.split("?", 1)[0] if "?" in request_path else request_path

    parsed = parse_unified_url(bare_path)
    if parsed is None:
        # Not a unified URL — skip resource-level check
        return None

    url_tenant = parsed["tenant_id"]
    object_path = parsed["object_path"]
    namespace = parsed["namespace"]

    # Tenant isolation: URL tenant must match JWT tenant.
    # "System" is a special tenant for system-level resources (manifests, roles);
    # any admin user in their own tenant may access it.
    if url_tenant != tenant_id:
        if url_tenant == "System" and _is_admin_group(groups, tenant_id):
            return None
        return "Cross-tenant access denied"

    # Admin groups bypass resource-level check
    if _is_admin_group(groups, tenant_id):
        return None

    # Existence check: only enforce resource-level auth for namespaces that
    # have a registered manifest (i.e. a resource_patterns row).  Unknown
    # namespaces pass through so legacy / non-manifest routes are unaffected.
    resource_prefix = _collection_prefix(object_path, url_tenant, parsed["is_collection"])
    try:
        pattern = await db.get_resource_pattern(resource_prefix)
    except Exception as exc:
        # DB unavailable — fail-closed: deny rather than silently skip the check.
        logger.error("check_resource_auth: resource_patterns lookup failed %s: %s", resource_prefix, exc)
        return f"Resource pattern lookup failed for {resource_prefix}"

    if pattern is None:
        # No manifest registered for this namespace — skip resource-level check.
        # This covers legacy /api/v1/ routes and any namespace not yet onboarded.
        # Note: OPA path-level check already blocks unknown namespaces, so this
        # branch is only reachable for routes explicitly allowed by path_rules
        # but not yet backed by a manifest (e.g. during the OPA refresh window).
        logger.debug(
            "check_resource_auth: no resource_pattern for %s — skipping resource-level check",
            resource_prefix,
        )
        return None

    # Query ACL with prefix matching
    role = await db.query_acl(tenant_id, user_path, groups, object_path)
    if role is None:
        return f"No ACL entry for {object_path}"

    # Determine role namespace
    role_ns = role.split("/")[0] if "/" in role else ""

    if role_ns == DEFAULT_ROLE_NAMESPACE:
        # Default Role: local matrix check
        allowed_methods = DEFAULT_ROLE_MATRIX.get(role, set())
        if method.upper() in allowed_methods:
            return None
        return f"Role {role} does not permit {method}"
    else:
        # Custom Role: callback to application
        return await _callback_check(
            namespace, tenant_id, user_path, object_path, role, method,
        )


# ---------------------------------------------------------------------------
# Auth check endpoint (HTTP)
# ---------------------------------------------------------------------------

@app.post("/api/v1/auth/check", response_model=AuthResponse)
async def check_permission(
    request: AuthRequest,
    user_info: Dict = Depends(verify_token),
):
    tenant_id = request.tenant_id or user_info["tenant_id"]
    user_path = f"AccessManager/Tenants/{tenant_id}/Users/{user_info['user_id']}"
    groups = [
        f"AccessManager/Tenants/{tenant_id}/Groups/{g}"
        if not g.startswith("AccessManager/") else g
        for g in user_info.get("groups", [])
    ]

    opa_input = {
        "input": {
            "token": user_info["token"],
            "user": user_info["user_id"],
            "groups": user_info["groups"],
            "tenant_id": tenant_id,
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
                allowed=False, user=user_info["user_id"],
                tenant_id=tenant_id, resource=request.resource,
                reason="Denied by policy",
            )

        denial = await check_resource_auth(
            request_path=request.path or "",
            method=request.method or "GET",
            tenant_id=tenant_id,
            user_path=user_path,
            groups=groups,
        )
        if denial:
            return AuthResponse(
                allowed=False, user=user_info["user_id"],
                tenant_id=tenant_id, resource=request.resource,
                reason=denial,
            )

        return AuthResponse(
            allowed=True, user=user_info["user_id"],
            tenant_id=tenant_id, resource=request.resource,
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

    api_key_header = headers.get("x-api-key")
    if api_key_header:
        user_info = await verify_api_key(api_key_header, request_path=original_path)
    else:
        from fastapi.security import HTTPAuthorizationCredentials
        auth_header = headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing authentication")
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=auth_header[7:]
        )
        user_info = await verify_token(credentials)

    tenant_id = user_info["tenant_id"]
    user_path = f"AccessManager/Tenants/{tenant_id}/Users/{user_info['user_id']}"
    groups = [
        f"AccessManager/Tenants/{tenant_id}/Groups/{g}"
        if not g.startswith("AccessManager/") else g
        for g in user_info.get("groups", [])
    ]

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

        denial = await check_resource_auth(
            request_path=original_path,
            method=method,
            tenant_id=tenant_id,
            user_path=user_path,
            groups=groups,
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
