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
#       +-- returns CheckResponse:
#               ALLOW -> code=0 + OkHttpResponse with x-auth-{user,tenant,groups} headers
#               DENY  -> code=7 + DeniedHttpResponse with HTTP 403

import json
import logging
import os
from typing import List

import grpc
import httpx

# ext_authz_pb2 and ext_authz_pb2_grpc are generated during Docker build:
#   python -m grpc_tools.protoc -I/app/proto --python_out=/app --grpc_python_out=/app \
#          /app/proto/ext_authz.proto
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
        return [r.get("name", "") for r in roles_list if isinstance(r, dict) and r.get("name")]
    elif isinstance(roles_list[0], str):
        return roles_list

    return []


def _ok(claims: dict, tenant_id: str, groups: List[str]) -> CheckResponse:
    """
    Build an ALLOW CheckResponse with identity headers.

    Header names follow diagrams/story-breakdown.md SR02:
    X-Auth-User-Id, X-Auth-Tenant, X-Auth-Groups.
    HTTP headers are case-insensitive, but the canonical-case names are emitted
    so backends that string-match a specific case work uniformly.
    """
    # append_action=2 → OVERWRITE_IF_EXISTS_OR_ADD
    # Needed for Envoy >=1.37 which stopped auto-applying headers without a
    # concrete append_action; see ext_authz.proto HeaderAppendAction enum.
    return CheckResponse(
        status=Status(code=0, message="OK"),
        ok_response=OkHttpResponse(
            headers=[
                HeaderValueOption(
                    header=HeaderValue(key="X-Auth-User-Id", value=claims.get("sub", "")),
                    append_action=2,
                ),
                HeaderValueOption(
                    header=HeaderValue(key="X-Auth-Tenant", value=tenant_id),
                    append_action=2,
                ),
                HeaderValueOption(
                    header=HeaderValue(key="X-Auth-Groups", value=",".join(groups)),
                    append_action=2,
                ),
            ]
        ),
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
          "code":   "forbidden",
          "reason": "...",
          "path":   "/kb/...",
          "method": "POST",
          "rule":   "path_rule" | "resource_acl" | "app_disabled" | "authentication" | "id_extraction_failed" | "upstream_error"
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
            "code":   _HTTP_CODE_LABEL.get(http_code, "internal"),
            "reason": reason,
            "path":   path,
            "method": method,
            "rule":   rule,
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

class AuthorizationService(AuthorizationServicer):
    """
    Implements the Envoy ext-authz v3 Authorization.Check RPC.

    Envoy Gateway forwards the raw Authorization header in the CheckRequest
    HTTP headers; JWT decoding happens here (and signature verification in
    OPA via io.jwt.decode_verify when OIDC is configured).

    Legacy/backward-compat: older AgentGateway deployments injected the
    pre-verified JWT payload as JSON in the gRPC metadata key
    ``dev.agentgateway.jwt``. That code path is still honoured below but is
    never exercised by Envoy Gateway.
    """

    async def Check(
        self,
        request,          # CheckRequest
        context,          # grpc.aio.ServicerContext
    ) -> CheckResponse:
        http = request.attributes.request.http
        headers: dict = dict(http.headers)

        # Pre-extract path/method so error bodies can include them even if we
        # short-circuit before Step 3.
        _early_path = (
            http.path
            or headers.get(":path", "")
            or headers.get("x-forwarded-path", "")
            or headers.get("x-original-path", "")
            or "/"
        )
        _early_path = _early_path.split("?")[0] if _early_path else "/"
        _early_method = http.method or headers.get(":method", "")

        # -- Step 0: API Key authentication branch ----------------------------
        # If x-api-key header is present, authenticate via API key instead of JWT.
        api_key_value = headers.get("x-api-key", "")
        if api_key_value:
            try:
                from .auth import verify_api_key  # lazy import
                raw_path = (
                    http.path
                    or headers.get(":path", "")
                    or headers.get("x-forwarded-path", "")
                    or headers.get("x-original-path", "")
                    or "/"
                )
                request_path_for_key = raw_path.split("?")[0] if raw_path else "/"
                user_info = await verify_api_key(api_key_value, request_path=request_path_for_key)
            except Exception as e:
                logger.warning("ext-authz gRPC: API key verification failed: %s", e)
                return _denied(401, f"Unauthorized: {e}",
                               rule="authentication",
                               path=_early_path, method=_early_method)

            tenant_id = user_info["tenant_id"]
            groups = user_info["groups"]
            token = ""

            # Build synthetic claims for downstream use
            claims = {
                "sub": user_info["user_id"],
                "tenant_id": tenant_id,
                "groups": groups,
            }

            # Skip JWT decoding steps; jump to Step 3 (resource/path/method)
        else:
            # -- Step 1: obtain JWT claims ----------------------------------------
            claims: dict | None = None
            token: str = ""

            # Priority 1 (legacy AgentGateway only): pre-verified claims
            # injected as JSON in the ``dev.agentgateway.jwt`` gRPC metadata
            # key. Envoy Gateway does NOT set this metadata; kept only as a
            # harmless backward-compat fallback for legacy deployments.
            for key, value in context.invocation_metadata():
                if key == "dev.agentgateway.jwt":
                    try:
                        claims = json.loads(value)
                        logger.debug("ext-authz gRPC: using pre-verified legacy AgentGateway claims")
                    except Exception as e:
                        logger.warning("Failed to parse dev.agentgateway.jwt metadata: %s", e)
                    break

            # Extract raw Bearer token (forwarded to OPA for its own verification)
            auth_header = headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]

            # Priority 2: decode the bearer token ourselves. This is the
            # normal path under Envoy Gateway (no pre-verified metadata).
            if claims is None and token:
                from .auth import _decode_unverified  # lazy import to avoid circular dep
                claims = _decode_unverified(token)
                if claims is None:
                    logger.warning("ext-authz gRPC: unable to decode token")
                    return _denied(401, "Unauthorized: malformed token",
                                   rule="authentication",
                                   path=_early_path, method=_early_method)

            if not claims:
                logger.warning("ext-authz gRPC: no claims available, denying request")
                return _denied(401, "Unauthorized: missing token",
                               rule="authentication",
                               path=_early_path, method=_early_method)

            # -- Step 2: derive tenant_id from iss --------------------------------
            iss: str = claims.get("iss", "")
            tenant_id = _extract_tenant_from_iss(iss) or claims.get("tenant_id", "")

            # Extract groups (with backward compat for roles)
            groups = _extract_groups(claims)

            # admins have no tenant restriction; let OPA decide.
            # Only reject when tenant is absent AND the user is NOT in admins.
            if not tenant_id and "admins" not in groups:
                logger.warning("ext-authz gRPC: cannot determine tenant_id from claims")
                return _denied(401, "Unauthorized: missing tenant_id",
                               rule="authentication",
                               path=_early_path, method=_early_method)

        # -- Step 3: resolve resource / path / method -------------------------
        raw_path: str = (
            http.path
            or headers.get(":path", "")
            or headers.get("x-forwarded-path", "")
            or headers.get("x-original-path", "")
            or "/"
        )
        # Strip query string for the OPA input / logging path. The full
        # raw_path (with query string) is forwarded to check_resource_auth
        # so that id_source='query' extraction can still see it.
        request_path = raw_path.split("?")[0] if raw_path else "/"
        method: str = http.method or headers.get(":method", "")
        resource: str = headers.get("x-authz-resource", "")

        # Extract the forwarded request body (when envoy body buffering is
        # enabled). http.body is `bytes` in the generated Python proto code
        # when declared as `bytes`, but older generators emit `str`; handle
        # both shapes defensively.
        raw_body = getattr(http, "body", b"") or b""
        if isinstance(raw_body, str):
            try:
                body_bytes = raw_body.encode("utf-8")
            except Exception:
                body_bytes = b""
        else:
            body_bytes = bytes(raw_body)

        if not resource:
            # Derive from the last non-empty path segment
            segments = [s for s in request_path.strip("/").split("/") if s]
            resource = segments[-1] if segments else "unknown"

        logger.info(
            "ext-authz gRPC: user=%s tenant=%s resource=%s path=%s (raw http.path=%r)",
            claims.get("sub"), tenant_id, resource, request_path, http.path,
        )

        # -- Step 4: query OPA (path-level auth) ------------------------------
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

        # Query the whole /v1/data/authz package so we can distinguish
        # "app_disabled" from "no path_rule matched" in the denial body.
        app_disabled = False
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{OPA_URL}/v1/data/authz",
                    json=opa_input,
                )
                if resp.status_code != 200:
                    logger.error("OPA returned %s: %s", resp.status_code, resp.text)
                    return _denied(503, "Authorization service error",
                                   rule="upstream_error",
                                   path=request_path, method=method)
                data = resp.json().get("result", {}) or {}
                allowed: bool = bool(data.get("allow", False))
                app_disabled = bool(data.get("app_disabled", False))

        except httpx.RequestError as e:
            logger.error("OPA connection error in ext-authz gRPC: %s", e)
            return _denied(503, "Authorization service unavailable",
                           rule="upstream_error",
                           path=request_path, method=method)
        except Exception as e:
            logger.error("Unexpected error in ext-authz gRPC: %s", e)
            return _denied(500, "Internal error",
                           rule="upstream_error",
                           path=request_path, method=method)

        if not allowed:
            logger.info(
                "ext-authz gRPC: DENIED (OPA) user=%s tenant=%s resource=%s app_disabled=%s",
                claims.get("sub"), tenant_id, resource, app_disabled,
            )
            if app_disabled:
                return _denied(403, "App is disabled",
                               rule="app_disabled",
                               path=request_path, method=method)
            return _denied(403, f"No path_rule matches groups {groups}",
                           rule="path_rule",
                           path=request_path, method=method)

        # -- Step 5: resource-level auth check (Phase 4) ----------------------
        try:
            from .main import check_resource_auth
            # Forward the full raw_path (may include query string) so that
            # id_source='query' extraction can read parameters, and the raw
            # request body so that id_source='body' extraction works.
            denial = await check_resource_auth(
                request_path=raw_path,
                method=method,
                tenant_id=tenant_id,
                user_id=claims.get("sub", ""),
                groups=groups,
                body_bytes=body_bytes,
            )
            if denial:
                logger.info(
                    "ext-authz gRPC: DENIED (resource) user=%s tenant=%s reason=%s",
                    claims.get("sub"), tenant_id, denial,
                )
                # Distinguish "can't extract ID" from "ACL doesn't permit"
                rule_kind = (
                    "id_extraction_failed"
                    if denial.startswith("Unable to extract resource_id")
                    else "resource_acl"
                )
                return _denied(403, denial,
                               rule=rule_kind,
                               path=request_path, method=method)
        except Exception as e:
            logger.error("Resource-level auth check failed in gRPC: %s", e)
            # Fail open for resource-level auth errors to avoid blocking all requests
            # when the DB is temporarily unavailable. OPA path-level auth already passed.

        logger.info(
            "ext-authz gRPC: ALLOWED user=%s tenant=%s resource=%s",
            claims.get("sub"), tenant_id, resource,
        )
        return _ok(claims, tenant_id, groups)


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
