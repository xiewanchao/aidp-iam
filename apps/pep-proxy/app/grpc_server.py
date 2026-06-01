# app/grpc_server.py
#
# gRPC External Authorization server for Envoy Gateway integration.
#
# Implements the Envoy ext-authz v3 Authorization/Check RPC on port 9000.
#
# Request flow (v2.0 - groups-based + resource-level auth):
#   Envoy Gateway -> gRPC Check(CheckRequest) -> pep-proxy:9000
#       |
#       +-- decodes JWT directly from the raw Authorization: Bearer header
#       |      (Envoy Gateway's ext_authz filter forwards the Authorization
#       |       header unchanged; it does NOT inject pre-verified claims).
#       |      Legacy fallback: reads dev.agentgateway.jwt gRPC metadata
#       |      when present, for backward compatibility with older
#       |      AgentGateway deployments.
#       |
#       +-- extracts tenant_id from iss claim: {OIDC_BASE_URL}/realms/{tenant_id}
#       +-- extracts groups from JWT claims (with roles backward compat)
#       +-- reads x-authz-resource HTTP header (set by routing rules)
#       |      OR derives resource from the last segment of the request path
#       |
#       +-- calls OPA: POST /v1/data/authz/allow (path-level auth)
#       +-- performs resource-level auth check (Phase 4)
#       |
#       +-- returns CheckResponse

import json
import logging
import os
from typing import List

import grpc
import httpx

# ext_authz_pb2 and ext_authz_pb2_grpc are generated during Docker build.
from ext_authz_pb2 import (  # type: ignore[import]
    CheckResponse,
    DeniedHttpResponse,
    HeaderValue,
    HeaderValueOption,
    HttpStatus,
    OkHttpResponse,
    Status,
)
from ext_authz_pb2_grpc import (  # type: ignore[import]
    AuthorizationServicer,
    add_AuthorizationServicer_to_server,
)

logger = logging.getLogger(__name__)

OPA_URL = os.getenv("OPA_URL", "http://localhost:8181")
OIDC_BASE_URL = os.getenv("OIDC_BASE_URL", "")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_tenant_from_iss(iss: str) -> str:
    """
    Extract tenant_id from an issuer URL.

    Keycloak format: {base}/realms/{tenant_id}
    Example: https://auth.example.com/realms/tenant-001 -> tenant-001
    Returns empty string when iss does not contain /realms/.
    """
    if "/realms/" in iss:
        tail = iss.split("/realms/", 1)[1]
        return tail.split("/")[0]
    return ""


def _extract_groups(claims: dict) -> List[str]:
    """
    Extract groups from JWT claims with backward compatibility for roles.

    Priority:
    1. claims["groups"] (list of strings)
    2. claims["roles"] (list of {id, name} dicts -> extract names)
    3. claims["roles"] (list of strings)
    """
    groups = claims.get("groups", [])
    if isinstance(groups, list) and groups:
        # Ensure all items are strings
        return [str(g) for g in groups if g]

    # Backward compat: map roles to groups
    roles_list = claims.get("roles", [])
    if not roles_list or not isinstance(roles_list, list):
        return []

    if isinstance(roles_list[0], dict):
        return [
            role.get("name", "")
            for role in roles_list
            if isinstance(role, dict) and role.get("name")
        ]
    if isinstance(roles_list[0], str):
        return roles_list

    return []


def _ok(claims: dict, tenant_id: str, groups: List[str], extra_headers: list | None = None) -> CheckResponse:
    """
    Build an ALLOW CheckResponse with identity headers.

    Header names follow diagrams/story-breakdown.md SR02:
    X-Auth-User-Id, X-Auth-Tenant, X-Auth-Groups.
    HTTP headers are case-insensitive, but the canonical-case names are emitted
    so backends that string-match a specific case work uniformly.
    """
    headers = [
        HeaderValueOption(
            header=HeaderValue(key="X-Auth-User-Id", value=claims.get("sub", "")),
        ),
        HeaderValueOption(
            header=HeaderValue(key="X-Auth-Tenant", value=tenant_id),
        ),
        HeaderValueOption(
            header=HeaderValue(key="X-Auth-Groups", value=",".join(groups)),
        ),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    return CheckResponse(
        status=Status(code=0, message="OK"),
        ok_response=OkHttpResponse(headers=headers),
    )


_HTTP_CODE_LABEL = {
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    503: "unavailable",
}


def _denied(
    http_code: int,
    reason: str,
    *,
    rule: str = "authentication",
    path: str = "",
    method: str = "",
) -> CheckResponse:
    """
    Build a DENY CheckResponse with a structured JSON body.

    Body shape:
        {
          "code": "forbidden",
          "reason": "...",
          "path": "/kb/...",
          "method": "POST",
          "rule": "path_rule" | "resource_acl" | "app_disabled"
        }

    gRPC code mapping:
      401 -> 16 (UNAUTHENTICATED)
      403 -> 7  (PERMISSION_DENIED)
      404 -> 5  (NOT_FOUND)
      503 -> 14 (UNAVAILABLE)
      other -> 13 (INTERNAL)
    """
    grpc_code_map = {401: 16, 403: 7, 404: 5, 503: 14}
    grpc_code = grpc_code_map.get(http_code, 13)

    body = json.dumps(
        {
            "code": _HTTP_CODE_LABEL.get(http_code, "internal"),
            "reason": reason,
            "path": path,
            "method": method,
            "rule": rule,
        },
        ensure_ascii=False,
    )

    return CheckResponse(
        status=Status(code=grpc_code, message=reason),
        denied_response=DeniedHttpResponse(
            status=HttpStatus(code=http_code),
            body=body,
            headers=[
                HeaderValueOption(
                    header=HeaderValue(key="content-type", value="application/json"),
                ),
            ],
        ),
    )


# ---------------------------------------------------------------------------
# AuthorizationServicer
# ---------------------------------------------------------------------------

def _extract_request_path(http, headers: dict) -> str:
    raw = (
        http.path
        or headers.get(":path", "")
        or headers.get("x-forwarded-path", "")
        or headers.get("x-original-path", "")
        or "/"
    )
    return raw.split("?")[0] if raw else "/"


def _decode_body_bytes(http) -> bytes:
    raw = getattr(http, "body", b"") or b""
    if isinstance(raw, str):
        try:
            return raw.encode("utf-8")
        except UnicodeEncodeError:
            return b""
    return bytes(raw)


async def _authenticate_api_key(
    api_key_value: str,
    request_path: str,
    early_path: str,
    early_method: str,
) -> tuple[dict, str, list] | CheckResponse:
    """Verify API key and return (claims, token, groups) or a denial response."""
    try:
        from .auth import verify_api_key
        user_info = await verify_api_key(api_key_value, request_path=request_path)
    except Exception as exc:
        logger.warning("ext-authz gRPC: API key verification failed: %s", exc)
        return _denied(
            401, f"Unauthorized: {exc}",
            rule="authentication", path=early_path, method=early_method,
        )
    claims = {
        "sub": user_info["user_id"],
        "tenant_id": user_info["tenant_id"],
        "groups": user_info["groups"],
    }
    return claims, "", user_info["groups"]


async def _authenticate_jwt(
    headers: dict,
    context,
    early_path: str,
    early_method: str,
) -> tuple[dict, str, str, list] | CheckResponse:
    """Decode JWT and return (claims, token, tenant_id, groups) or a denial response."""
    claims: dict | None = None
    token: str = ""

    # Legacy AgentGateway: pre-verified claims in gRPC metadata (never set by Envoy Gateway).
    for key, value in context.invocation_metadata():
        if key == "dev.agentgateway.jwt":
            try:
                claims = json.loads(value)
            except Exception as exc:
                logger.warning("Failed to parse dev.agentgateway.jwt metadata: %s", exc)
            break

    auth_header = headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]

    if claims is None and token:
        from .auth import _decode_unverified
        claims = _decode_unverified(token)
        if claims is None:
            logger.warning("ext-authz gRPC: unable to decode token")
            return _denied(
                401, "Unauthorized: malformed token",
                rule="authentication", path=early_path, method=early_method,
            )

    if not claims:
        logger.warning("ext-authz gRPC: no claims available, denying request")
        return _denied(
            401, "Unauthorized: missing token",
            rule="authentication", path=early_path, method=early_method,
        )

    iss: str = claims.get("iss", "")
    tenant_id = _extract_tenant_from_iss(iss) or claims.get("tenant_id", "")
    groups = _extract_groups(claims)

    if not tenant_id and "master-admins" not in groups:
        logger.warning("ext-authz gRPC: cannot determine tenant_id from claims")
        return _denied(
            401, "Unauthorized: missing tenant_id",
            rule="authentication", path=early_path, method=early_method,
        )

    return claims, token, tenant_id, groups


async def _query_opa(
    token: str,
    claims: dict,
    groups: list,
    tenant_id: str,
    resource: str,
    request_path: str,
    method: str,
) -> tuple[bool, bool] | CheckResponse:
    """Query OPA for path-level auth. Returns (allowed, app_disabled) or a denial."""
    opa_input = {
        "input": {
            "token": token,
            "user": claims.get("sub", ""),
            "groups": groups,
            "tenant_id": tenant_id,
            "resource": resource,
            "path": request_path,
            "method": method,
            "context": {},
        }
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{OPA_URL}/v1/data/authz", json=opa_input)
        if resp.status_code != 200:
            logger.error("OPA returned %s: %s", resp.status_code, resp.text)
            return _denied(
                503, "Authorization service error",
                rule="upstream_error", path=request_path, method=method,
            )
        opa_result = resp.json().get("result", {}) or {}
        return (
            bool(opa_result.get("allow", False)),
            bool(opa_result.get("app_disabled", False)),
        )
    except httpx.RequestError as exc:
        logger.error("OPA connection error in ext-authz gRPC: %s", exc)
        return _denied(
            503, "Authorization service unavailable",
            rule="upstream_error", path=request_path, method=method,
        )
    except Exception as exc:
        logger.error("Unexpected error in ext-authz gRPC: %s", exc)
        return _denied(
            500, "Internal error",
            rule="upstream_error", path=request_path, method=method,
        )


async def _check_resource_auth_denial(
    raw_path: str,
    method: str,
    tenant_id: str,
    user_id: str,
    full_groups: list,
    request_path: str,
) -> CheckResponse | None:
    """Run resource-level ACL check. Returns a denial or None if allowed."""
    user_path = f"AccessManager/Tenants/{tenant_id}/Users/{user_id}" if user_id else ""
    try:
        from .main import check_resource_auth
        denial = await check_resource_auth(
            request_path=raw_path,
            method=method,
            tenant_id=tenant_id,
            user_path=user_path,
            groups=full_groups,
        )
        if not denial:
            return None
        logger.info(
            "ext-authz gRPC: DENIED (resource) user=%s tenant=%s reason=%s",
            user_id, tenant_id, denial,
        )
        http_code = 404 if denial.startswith("404:") else 403
        reason = denial[4:] if denial.startswith("404:") else denial
        return _denied(
            http_code,
            reason,
            rule="resource_acl",
            path=request_path,
            method=method,
        )
    except Exception as exc:
        logger.error("Resource-level auth check failed in gRPC: %s", exc)
        # Fail open: OPA path-level auth already passed; DB unavailability
        # should not block all requests.
        return None


async def _build_allowed_ids_headers(
    method: str,
    request_path: str,
    tenant_id: str,
    user_path: str,
    full_groups: list,
) -> list:
    """Build X-Allowed-Ids/X-Allowed-Total headers for collection GETs, or []."""
    if method.upper() != "GET" or not request_path.rstrip("/"):
        return []
    from .main import parse_unified_url, _is_admin_group
    parsed = parse_unified_url(request_path.split("?")[0])
    if not parsed or not parsed["is_collection"]:
        return []
    if _is_admin_group(full_groups, tenant_id, parsed["namespace"]):
        return []
    try:
        from . import db as _db
        allowed_ids, total = await _db.get_allowed_object_ids(
            tenant_id, user_path, full_groups, parsed["object_path"],
        )
        logger.info(
            "ext-authz gRPC: injecting X-Allowed-Ids count=%d for %s",
            len(allowed_ids), parsed["object_path"],
        )
        return [
            HeaderValueOption(
                header=HeaderValue(key="X-Allowed-Ids", value=",".join(allowed_ids)),
            ),
            HeaderValueOption(
                header=HeaderValue(key="X-Allowed-Total", value=str(total)),
            ),
        ]
    except Exception as exc:
        logger.error("ext-authz gRPC: X-Allowed-Ids injection failed: %s", exc)
        return []


def _request_target(http, headers: dict) -> tuple[str, str, str, str]:
    raw_path = (
        http.path
        or headers.get(":path", "")
        or headers.get("x-forwarded-path", "")
        or headers.get("x-original-path", "")
        or "/"
    )
    request_path = raw_path.split("?")[0] if raw_path else "/"
    method = http.method or headers.get(":method", "")
    resource = headers.get("x-authz-resource", "")
    if not resource:
        segments = [segment for segment in request_path.strip("/").split("/") if segment]
        resource = segments[-1] if segments else "unknown"
    return raw_path, request_path, method, resource


async def _authenticate_request(
    headers: dict,
    context,
    early_path: str,
    early_method: str,
) -> tuple[dict, str, str, list] | CheckResponse:
    api_key_value = headers.get("x-api-key", "")
    if api_key_value:
        auth_result = await _authenticate_api_key(
            api_key_value,
            early_path,
            early_path,
            early_method,
        )
        if isinstance(auth_result, CheckResponse):
            return auth_result
        claims, token, groups = auth_result
        return claims, token, claims["tenant_id"], groups
    return await _authenticate_jwt(headers, context, early_path, early_method)


def _full_group_paths(groups: list, tenant_id: str) -> list:
    full_groups = []
    for group in groups:
        if group.startswith("AccessManager/"):
            full_groups.append(group)
        else:
            full_groups.append(f"AccessManager/Tenants/{tenant_id}/Groups/{group}")
    return full_groups


def _opa_denial_response(
    app_disabled: bool,
    groups: list,
    request_path: str,
    method: str,
) -> CheckResponse:
    if app_disabled:
        return _denied(
            403,
            "App is disabled",
            rule="app_disabled",
            path=request_path,
            method=method,
        )
    return _denied(
        403,
        f"No path_rule matches groups {groups}",
        rule="path_rule",
        path=request_path,
        method=method,
    )


async def _allowed_response(
    claims: dict,
    tenant_id: str,
    groups: list,
    raw_path: str,
    request_path: str,
    method: str,
    resource: str,
) -> CheckResponse:
    user_id = claims.get("sub", "")
    full_groups = _full_group_paths(groups, tenant_id)
    user_path = f"AccessManager/Tenants/{tenant_id}/Users/{user_id}" if user_id else ""
    denial = await _check_resource_auth_denial(
        raw_path,
        method,
        tenant_id,
        user_id,
        full_groups,
        request_path,
    )
    if denial:
        return denial

    logger.info(
        "ext-authz gRPC: ALLOWED user=%s tenant=%s resource=%s",
        user_id,
        tenant_id,
        resource,
    )
    extra_headers = await _build_allowed_ids_headers(
        method,
        request_path,
        tenant_id,
        user_path,
        full_groups,
    )
    return _ok(claims, tenant_id, groups, extra_headers)


class AuthorizationService(AuthorizationServicer):
    """Envoy ext-authz v3 Authorization.Check RPC implementation."""

    async def check(self, request, context) -> CheckResponse:
        http = request.attributes.request.http
        headers: dict = dict(http.headers)
        early_path = _extract_request_path(http, headers)
        early_method = http.method or headers.get(":method", "")
        auth_result = await _authenticate_request(headers, context, early_path, early_method)
        if isinstance(auth_result, CheckResponse):
            return auth_result
        claims, token, tenant_id, groups = auth_result
        raw_path, request_path, method, resource = _request_target(http, headers)

        logger.info(
            "ext-authz gRPC: user=%s tenant=%s resource=%s path=%s",
            claims.get("sub"), tenant_id, resource, request_path,
        )

        opa_result = await _query_opa(
            token, claims, groups, tenant_id, resource, request_path, method,
        )
        if isinstance(opa_result, CheckResponse):
            return opa_result
        allowed, app_disabled = opa_result

        if not allowed:
            logger.info(
                "ext-authz gRPC: DENIED (OPA) user=%s tenant=%s resource=%s app_disabled=%s",
                claims.get("sub"), tenant_id, resource, app_disabled,
            )
            return _opa_denial_response(app_disabled, groups, request_path, method)
        return await _allowed_response(
            claims,
            tenant_id,
            groups,
            raw_path,
            request_path,
            method,
            resource,
        )

    Check = check


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

async def serve() -> None:
    """
    Start the gRPC ext-authz server on port 9000.

    Call this once from the FastAPI startup event:
        asyncio.create_task(grpc_server.serve())
    """
    try:
        server = grpc.aio.server()
        add_AuthorizationServicer_to_server(AuthorizationService(), server)
        listen_addr = "[::]:9000"
        server.add_insecure_port(listen_addr)
        await server.start()
        logger.info("gRPC ext-authz server listening on %s", listen_addr)
        await server.wait_for_termination()
    except Exception as exc:
        logger.error("gRPC ext-authz server failed: %s", exc, exc_info=True)
        raise
