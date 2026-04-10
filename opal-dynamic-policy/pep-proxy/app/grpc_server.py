# app/grpc_server.py
#
# gRPC External Authorization server for agentgateway integration.
#
# Implements the Envoy ext-authz v3 Authorization/Check RPC on port 9000.
#
# Request flow (v2.0 - groups-based + resource-level auth):
#   agentgateway -> gRPC Check(CheckRequest) -> pep-proxy:9000
#       |
#       +-- reads dev.agentgateway.jwt gRPC metadata (pre-verified by agentgateway)
#       |      OR decodes raw Authorization: Bearer token (dev / fallback mode)
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
    """Build an ALLOW CheckResponse with identity headers."""
    return CheckResponse(
        status=Status(code=0, message="OK"),
        ok_response=OkHttpResponse(
            headers=[
                HeaderValueOption(
                    header=HeaderValue(key="x-auth-user", value=claims.get("sub", ""))
                ),
                HeaderValueOption(
                    header=HeaderValue(key="x-auth-tenant", value=tenant_id)
                ),
                HeaderValueOption(
                    header=HeaderValue(key="x-auth-groups", value=",".join(groups))
                ),
            ]
        ),
    )


def _denied(http_code: int, message: str) -> CheckResponse:
    """
    Build a DENY CheckResponse.

    gRPC code mapping:
      401 -> 16 (UNAUTHENTICATED)
      403 -> 7  (PERMISSION_DENIED)
      503 -> 14 (UNAVAILABLE)
      other -> 13 (INTERNAL)
    """
    grpc_code_map = {401: 16, 403: 7, 503: 14}
    grpc_code = grpc_code_map.get(http_code, 13)
    return CheckResponse(
        status=Status(code=grpc_code, message=message),
        denied_response=DeniedHttpResponse(
            status=HttpStatus(code=http_code),
            body=message,
        ),
    )


# ---------------------------------------------------------------------------
# AuthorizationServicer
# ---------------------------------------------------------------------------

class AuthorizationService(AuthorizationServicer):
    """
    Implements the Envoy ext-authz v3 Authorization.Check RPC.

    agentgateway verifies the JWT before calling ext-authz and injects the
    verified payload as JSON in the gRPC metadata key ``dev.agentgateway.jwt``.
    The raw Authorization header is included in the CheckRequest HTTP headers
    for use by OPA's own io.jwt.decode_verify when OIDC is configured.
    """

    async def Check(
        self,
        request,          # CheckRequest
        context,          # grpc.aio.ServicerContext
    ) -> CheckResponse:
        http = request.attributes.request.http
        headers: dict = dict(http.headers)

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
                return _denied(401, f"Unauthorized: {e}")

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

            # Priority 1: pre-verified claims injected by agentgateway
            for key, value in context.invocation_metadata():
                if key == "dev.agentgateway.jwt":
                    try:
                        claims = json.loads(value)
                        logger.debug("ext-authz gRPC: using pre-verified agentgateway claims")
                    except Exception as e:
                        logger.warning("Failed to parse dev.agentgateway.jwt metadata: %s", e)
                    break

            # Extract raw Bearer token (forwarded to OPA for its own verification)
            auth_header = headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]

            # Priority 2: decode the bearer token ourselves (dev mode / no agentgateway metadata)
            if claims is None and token:
                from .auth import _decode_unverified  # lazy import to avoid circular dep
                claims = _decode_unverified(token)
                if claims is None:
                    logger.warning("ext-authz gRPC: unable to decode token")
                    return _denied(401, "Unauthorized: malformed token")

            if not claims:
                logger.warning("ext-authz gRPC: no claims available, denying request")
                return _denied(401, "Unauthorized: missing token")

            # -- Step 2: derive tenant_id from iss --------------------------------
            iss: str = claims.get("iss", "")
            tenant_id = _extract_tenant_from_iss(iss) or claims.get("tenant_id", "")

            # Extract groups (with backward compat for roles)
            groups = _extract_groups(claims)

            # master-admins have no tenant restriction; let OPA decide.
            # Only reject when tenant is absent AND the user is NOT in master-admins.
            if not tenant_id and "master-admins" not in groups:
                logger.warning("ext-authz gRPC: cannot determine tenant_id from claims")
                return _denied(401, "Unauthorized: missing tenant_id")

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

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{OPA_URL}/v1/data/authz/allow",
                    json=opa_input,
                )
                if resp.status_code != 200:
                    logger.error("OPA returned %s: %s", resp.status_code, resp.text)
                    return _denied(503, "Authorization service error")
                allowed: bool = resp.json().get("result", False)

        except httpx.RequestError as e:
            logger.error("OPA connection error in ext-authz gRPC: %s", e)
            return _denied(503, "Authorization service unavailable")
        except Exception as e:
            logger.error("Unexpected error in ext-authz gRPC: %s", e)
            return _denied(500, "Internal error")

        if not allowed:
            logger.info(
                "ext-authz gRPC: DENIED (OPA) user=%s tenant=%s resource=%s",
                claims.get("sub"), tenant_id, resource,
            )
            return _denied(403, "Forbidden by policy")

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
                return _denied(403, denial)
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
