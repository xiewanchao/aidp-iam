# app/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any, List, Optional
import asyncio
import httpx
import logging
import os
import re
from datetime import datetime

_grpc_task: "asyncio.Task | None" = None

from pep_proxy.models import AuthRequest, AuthResponse
from pep_proxy.auth import verify_token, verify_api_key
from pep_proxy import db
from pep_proxy import grpc_server

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

_raw_bypass = os.getenv("AUTHZ_BYPASS_PATTERNS", "")
AUTHZ_BYPASS_PATTERNS: list[re.Pattern] = [
    re.compile(p.strip())
    for p in _raw_bypass.split(",")
    if p.strip()
]
DEFAULT_ROLE_MATRIX: Dict[str, set] = {
    "AccessManager/Tenants/System/Roles/Owner": {"GET", "PUT", "PATCH", "DELETE", "POST"},
    "AccessManager/Tenants/System/Roles/Contributor": {"GET", "PUT", "PATCH", "POST"},
    "AccessManager/Tenants/System/Roles/Viewer": {"GET"},
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


def _is_admin_group(groups: List[str], tenant_id: str, namespace: str = "") -> bool:
    # tenant-admins bypass resource-level ACL checks (full access within tenant).
    # {namespace}-admins bypass resource-level ACL for that specific application.
    # master-admins is a cross-tenant IAM role; it does NOT bypass per-resource
    # ACL enforcement on tenant resources — only on System-level paths.
    bypass_paths = {
        f"AccessManager/Tenants/{tenant_id}/Groups/tenant-admins",
    }
    if namespace:
        bypass_paths.add(f"AccessManager/Tenants/{tenant_id}/Groups/{namespace}-admins")
    return bool(bypass_paths & set(groups))


def _is_master_admin(groups: List[str], tenant_id: str) -> bool:
    # master-admins can access System-level paths (manifests, roles, apps config)
    # but cannot bypass resource-level ACL on tenant-owned resources.
    master_paths = {
        f"AccessManager/Tenants/{tenant_id}/Groups/master-admins",
    }
    return bool(master_paths & set(groups))


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
    except httpx.TimeoutException:
        return f"Callback timeout for namespace {namespace}"
    except Exception as exc:
        logger.error("Callback error for %s: %s", namespace, exc)
        return f"Callback error: {exc}"
    return _callback_response_denial(resp)


def _callback_response_denial(resp: httpx.Response) -> Optional[str]:
    if resp.status_code != 200:
        return f"Callback returned HTTP {resp.status_code}"
    try:
        data = resp.json()
    except ValueError:
        return "Callback returned invalid JSON"
    if data.get("allowed"):
        return None
    return data.get("reason", "Denied by application callback")


def _tenant_access_denial(url_tenant: str, tenant_id: str, groups: List[str]) -> Optional[str]:
    if url_tenant == tenant_id:
        return None
    if url_tenant == "System" and _is_master_admin(groups, tenant_id):
        return None
    return "Cross-tenant access denied"


async def _access_manager_denial(
    method: str,
    object_path: str,
    user_path: str,
    groups: List[str],
    tenant_id: str,
    is_admin: bool,
) -> Optional[str]:
    if is_admin:
        return None
    parts = object_path.split("/")
    my_user_id = user_path.split("/")[-1]
    is_self_detail = (
        method.upper() == "GET"
        and len(parts) == 6
        and parts[3] == "Users"
        and parts[4] == my_user_id
        and parts[5] == "Details"
    )
    if is_self_detail:
        return None
    result = await db.query_acl(tenant_id, user_path, groups, object_path)
    if result is None:
        return "AccessManager resource access requires admin privileges"
    role, _ = result
    allowed_methods = DEFAULT_ROLE_MATRIX.get(role, set())
    if method.upper() not in allowed_methods:
        return f"Role {role} does not permit {method} on AccessManager resource"
    return None


async def _load_resource_pattern(resource_prefix: str) -> tuple[Optional[dict], Optional[str]]:
    try:
        return await db.get_resource_pattern(resource_prefix), None
    except Exception as exc:
        logger.error("check_resource_auth: resource_patterns lookup failed %s: %s", resource_prefix, exc)
        return None, f"Resource pattern lookup failed for {resource_prefix}"


def _is_fixed_post_action(method: str, object_path: str, pattern: dict) -> bool:
    stripped_segment = object_path.rsplit("/", 1)[-1]
    id_field = pattern.get("id_field", "id")
    return method.upper() == "POST" and stripped_segment != id_field and not stripped_segment.startswith("{")


async def _resolve_resource_pattern(
    resource_prefix: str,
    method: str,
    object_path: str,
    is_collection: bool,
) -> tuple[Optional[dict], bool, Optional[str]]:
    pattern, error = await _load_resource_pattern(resource_prefix)
    if error:
        return None, False, error
    if pattern is not None:
        return pattern, (not is_collection and _is_fixed_post_action(method, object_path, pattern)), None

    parent_prefix = resource_prefix
    while "/" in parent_prefix:
        parent_prefix = parent_prefix.rsplit("/", 1)[0]
        pattern, error = await _load_resource_pattern(parent_prefix)
        if error:
            return None, False, error
        if pattern is not None:
            return pattern, True, None
    return None, False, None


async def _role_access_denial(
    namespace: str,
    tenant_id: str,
    user_path: str,
    object_path: str,
    role: str,
    method: str,
) -> Optional[str]:
    role_ns = role.split("/")[0] if "/" in role else ""
    if role_ns != DEFAULT_ROLE_NAMESPACE:
        return await _callback_check(namespace, tenant_id, user_path, object_path, role, method)

    allowed_methods = DEFAULT_ROLE_MATRIX.get(role, set())
    return None if method.upper() in allowed_methods else f"Role {role} does not permit {method}"


async def _isolated_resource_denial(
    namespace: str,
    tenant_id: str,
    user_path: str,
    groups: List[str],
    object_path: str,
    method: str,
) -> Optional[str]:
    result = await db.query_acl(tenant_id, user_path, groups, object_path)
    if result is None:
        return f"No ACL entry for {object_path}"

    role, matched_path = result
    collection_path = object_path.rsplit("/", 1)[0]
    if not (matched_path.startswith(collection_path) and len(matched_path) > len(collection_path)):
        return f"No instance-level ACL for {object_path}"
    return await _role_access_denial(namespace, tenant_id, user_path, object_path, role, method)


async def _allow_create_without_acl_denial(
    pattern: dict,
    parsed: Dict[str, Any],
    namespace: str,
    tenant_id: str,
    user_path: str,
    groups: List[str],
    method: str,
    is_action_path: bool = False,
) -> tuple[bool, Optional[str]]:
    if not pattern.get("allow_create_without_acl", False):
        return False, None
    if method.upper() == "PUT":
        return True, None
    if method.upper() == "GET" and parsed["is_collection"] and not is_action_path:
        return True, None

    object_path = parsed["object_path"]

    if method.upper() == "GET" and not is_action_path:
        # Instance GET: enforce per-user isolation (must have instance-level ACL).
        denial = await _isolated_resource_denial(namespace, tenant_id, user_path, groups, object_path, method)
        return True, denial

    # POST action (e.g. DraftSession, Replay): the last segment is the action name.
    # Check ACL on the instance path (strip action name) and enforce the role matrix.
    if method.upper() == "POST" or is_action_path:
        instance_path = object_path.rsplit("/", 1)[0]
        result = await db.query_acl(tenant_id, user_path, groups, instance_path)
        if result is None:
            return True, f"No ACL entry for {instance_path}"
        role, _ = result
        denial = await _role_access_denial(namespace, tenant_id, user_path, object_path, role, method)
        return True, denial

    # DELETE/PATCH on a specific instance: require instance-level ACL.
    denial = await _isolated_resource_denial(namespace, tenant_id, user_path, groups, object_path, method)
    return True, denial


def _is_type_level_match(matched_path: str) -> bool:
    matched_parts = matched_path.split("/")
    resource_parts_count = len(matched_parts) - 3
    return resource_parts_count % 2 == 1


async def _resource_exists_or_fail_safe(tenant_id: str, object_path: str) -> bool:
    try:
        return await db.resource_acl_exists(tenant_id, object_path)
    except Exception as exc:
        logger.error("check_resource_auth: resource existence lookup failed %s: %s", object_path, exc)
        return True


async def _type_level_acl_denial(
    tenant_id: str,
    object_path: str,
    method: str,
    is_action_path: bool,
    is_collection: bool,
    matched_path: str,
) -> Optional[str]:
    if not (_is_type_level_match(matched_path) and (not is_collection or is_action_path)):
        return None
    if is_action_path or method.upper() == "PUT":
        return None
    if method.upper() == "GET":
        if not await _resource_exists_or_fail_safe(tenant_id, object_path):
            return "404:Resource not found or no access"
        return f"No instance-level ACL for {object_path}"
    return f"No instance-level ACL for {object_path} (type-level ACL only permits PUT/create)"


async def _pattern_resource_auth_denial(
    pattern: dict,
    parsed: Dict[str, Any],
    namespace: str,
    tenant_id: str,
    user_path: str,
    groups: List[str],
    method: str,
    is_action_path: bool,
) -> Optional[str]:
    object_path = parsed["object_path"]

    if pattern.get("app_managed_authz", False):
        # Only verify the caller has any ACL on the parent resource (Instance).
        # query_acl uses prefix-matching, so it walks up to the Instance-level entry.
        check_path = object_path.rsplit("/", 1)[0] if "/" in object_path else object_path
        result = await db.query_acl(tenant_id, user_path, groups, check_path)
        if result is None:
            return f"No ACL entry for parent resource {check_path}"
        return None

    handled, denial = await _allow_create_without_acl_denial(
        pattern,
        parsed,
        namespace,
        tenant_id,
        user_path,
        groups,
        method,
        is_action_path,
    )
    if handled:
        return denial

    result = await db.query_acl(tenant_id, user_path, groups, object_path)
    if result is None:
        return f"No ACL entry for {object_path}"
    role, matched_path = result

    denial = await _type_level_acl_denial(
        tenant_id,
        object_path,
        method,
        is_action_path,
        parsed["is_collection"],
        matched_path,
    )
    if denial:
        return denial
    return await _role_access_denial(namespace, tenant_id, user_path, object_path, role, method)


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
    """Perform resource-level authorization check using unified URL format."""
    bare_path = request_path.split("?", 1)[0] if "?" in request_path else request_path
    parsed = parse_unified_url(bare_path)
    if parsed is None:
        return None

    url_tenant = parsed["tenant_id"]
    object_path = parsed["object_path"]
    namespace = parsed["namespace"]
    denial = _tenant_access_denial(url_tenant, tenant_id, groups)
    if denial:
        return denial

    is_admin = _is_admin_group(groups, tenant_id, namespace)
    if namespace == "AccessManager":
        return await _access_manager_denial(method, object_path, user_path, groups, tenant_id, is_admin)

    resource_prefix = _collection_prefix(object_path, url_tenant, parsed["is_collection"])
    pattern, is_action_path, error = await _resolve_resource_pattern(
        resource_prefix,
        method,
        object_path,
        parsed["is_collection"],
    )
    if error:
        return error
    if pattern is None:
        logger.debug(
            "check_resource_auth: no resource_pattern for %s - skipping resource-level check",
            resource_prefix,
        )
        return None
    if is_admin and pattern.get("admin_bypass", True):
        return None
    return await _pattern_resource_auth_denial(
        pattern,
        parsed,
        namespace,
        tenant_id,
        user_path,
        groups,
        method,
        is_action_path,
    )


# ---------------------------------------------------------------------------
# Auth check endpoint (HTTP)
# ---------------------------------------------------------------------------

def _tenant_user_context(user_info: Dict[str, Any], tenant_id: str) -> tuple[str, List[str]]:
    user_path = f"AccessManager/Tenants/{tenant_id}/Users/{user_info['user_id']}"
    groups = []
    for group in user_info.get("groups", []):
        if group.startswith("AccessManager/"):
            groups.append(group)
        else:
            groups.append(f"AccessManager/Tenants/{tenant_id}/Groups/{group}")
    return user_path, groups


def _build_opa_input(
    token: str,
    user_id: str,
    groups: List[str],
    tenant_id: str,
    resource: str,
    path: str,
    method: str,
    context: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "input": {
            "token": token,
            "user": user_id,
            "groups": groups,
            "tenant_id": tenant_id,
            "resource": resource,
            "path": path,
            "method": method,
            "context": context,
        }
    }


async def _query_opa_allow(opa_input: Dict[str, Any], failure_detail: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(f"{OPA_URL}/v1/data/authz/allow", json=opa_input)
    except httpx.RequestError as exc:
        logger.error("OPA connection error: %s", exc)
        raise HTTPException(status_code=503, detail="Authorization service unavailable") from exc
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail=failure_detail)
    return bool(response.json().get("result", False))


def _auth_response(
    allowed: bool,
    user_info: Dict[str, Any],
    tenant_id: str,
    resource: str,
    reason: str,
) -> AuthResponse:
    return AuthResponse(
        allowed=allowed,
        user=user_info["user_id"],
        tenant_id=tenant_id,
        resource=resource,
        reason=reason,
    )


async def _ext_authz_user_info(headers, original_path: str) -> Dict[str, Any]:
    api_key_header = headers.get("x-api-key")
    if api_key_header:
        return await verify_api_key(api_key_header, request_path=original_path)

    from fastapi.security import HTTPAuthorizationCredentials
    auth_header = headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authentication")
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=auth_header[7:],
    )
    return await verify_token(credentials)


def _ext_authz_resource(headers, original_path: str) -> str:
    resource = headers.get("x-authz-resource", "")
    if resource:
        return resource
    segments = [segment for segment in original_path.strip("/").split("/") if segment]
    return segments[-1] if segments else "unknown"


def _ext_authz_headers(user_info: Dict[str, Any], tenant_id: str) -> Dict[str, str]:
    return {
        "X-Auth-User-Id": user_info["user_id"],
        "X-Auth-Username": user_info.get("username", ""),
        "X-Auth-Nickname": user_info.get("nickname", ""),
        "X-Auth-Tenant": tenant_id,
        "X-Auth-Groups": ",".join(user_info["groups"]),
    }


@app.post("/api/v1/auth/check", response_model=AuthResponse)
async def check_permission(
    request: AuthRequest,
    user_info: Dict = Depends(verify_token),
):
    tenant_id = request.tenant_id or user_info["tenant_id"]
    user_path, groups = _tenant_user_context(user_info, tenant_id)
    opa_input = _build_opa_input(
        user_info["token"],
        user_info["user_id"],
        user_info["groups"],
        tenant_id,
        request.resource,
        request.path or "",
        request.method or "",
        request.context or {},
    )
    try:
        allowed = await _query_opa_allow(opa_input, "Authorization service error")
        if not allowed:
            return _auth_response(False, user_info, tenant_id, request.resource, "Denied by policy")

        denial = await check_resource_auth(
            request_path=request.path or "",
            method=request.method or "GET",
            tenant_id=tenant_id,
            user_path=user_path,
            groups=groups,
        )
        if denial:
            return _auth_response(False, user_info, tenant_id, request.resource, denial)
        return _auth_response(True, user_info, tenant_id, request.resource, "Allowed by policy")
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
    user_info = await _ext_authz_user_info(headers, original_path)
    tenant_id = user_info["tenant_id"]
    user_path, groups = _tenant_user_context(user_info, tenant_id)
    resource = _ext_authz_resource(headers, original_path)

    if any(p.fullmatch(original_path) for p in AUTHZ_BYPASS_PATTERNS):
        return Response(status_code=200, headers=_ext_authz_headers(user_info, tenant_id))

    opa_input = _build_opa_input(
        user_info["token"],
        user_info["user_id"],
        user_info["groups"],
        tenant_id,
        resource,
        original_path,
        method,
        {},
    )

    try:
        if not await _query_opa_allow(opa_input, "OPA query failed"):
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

        return Response(status_code=200, headers=_ext_authz_headers(user_info, tenant_id))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("ext-authz error: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")
