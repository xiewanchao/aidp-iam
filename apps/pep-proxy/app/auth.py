# app/auth.py
import json
import hashlib
import os
import time
import logging
import httpx
import jwt
from jwt.algorithms import RSAAlgorithm
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Dict, Any, Tuple, Optional

security = HTTPBearer()
logger = logging.getLogger(__name__)

# OIDC base URL – used to validate that the token issuer belongs to this IdP.
# e.g. https://auth.example.com
# Expected issuer format: {OIDC_BASE_URL}/realms/{tenant_id}
# OIDC discovery per-tenant: {iss}/.well-known/openid-configuration
# JWKS URI: extracted from discovery doc (typically {iss}/protocol/certs)
OIDC_BASE_URL = os.getenv("OIDC_BASE_URL", "")

# Internal URL for OIDC discovery (used inside K8s when localhost doesn't resolve to Keycloak)
# e.g. http://keycloak.keycloak.svc.cluster.local:8080
OIDC_INTERNAL_URL = os.getenv("OIDC_INTERNAL_URL", "")

# Fallback shared secret for HS256 (dev / testing only)
JWT_SECRET = os.getenv("JWT_SECRET", "")

# IAM DB URL for API key verification (reuses the same PG as pep-proxy db module)
IAM_DB_URL = os.getenv(
    "IAM_DB_URL",
    "postgresql://keycloak:keycloak@iam-store.keycloak.svc.cluster.local:5432/iam",
)

# Simple in-memory JWKS cache: iss -> (jwks_data, expiry_timestamp)
_jwks_cache: Dict[str, Tuple[dict, float]] = {}
JWKS_CACHE_TTL = 300  # seconds


def _extract_tenant_from_issuer(iss: str) -> str:
    """
    Extract tenant_id from an issuer URL.

    Expected format: {base}/realms/{tenant_id}[/...]
    Example: https://auth.example.com/realms/tenant-001
             -> tenant-001
    Returns empty string if the iss does not contain /realms/.
    """
    if "/realms/" in iss:
        tail = iss.split("/realms/", 1)[1]
        return tail.split("/")[0]
    return ""


def _decode_unverified(token: str) -> Optional[Dict[str, Any]]:
    """
    Decode a JWT without verifying the signature.
    Returns the claims dict, or None on any parse error.
    Used by the gRPC ext-authz server to decode JWTs forwarded by
    Envoy Gateway via the Authorization header. (Legacy AgentGateway
    pre-verification metadata is no longer relied upon, but remains
    supported as a backward-compat fallback in grpc_server.py.)
    """
    try:
        return jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_aud": False,
                "verify_exp": False,
                "verify_nbf": False,
                "verify_iat": False,
            },
        )
    except (jwt.exceptions.PyJWTError, ValueError):
        # Any malformed token (bad base64, missing segments, etc.) returns None.
        return None


def _to_internal_url(url: str) -> str:
    """
    Convert a public-facing URL to an internal K8s URL for OIDC discovery.

    Extracts the path starting from /realms/ and prepends OIDC_INTERNAL_URL.
    This way KC_HOSTNAME can be any value (any port, any domain) and
    internal OIDC discovery still works.

    Example:
        http://localhost:8080/realms/data-agent
        -> http://keycloak.keycloak.svc.cluster.local:8080/realms/data-agent
    """
    if not OIDC_INTERNAL_URL or "/realms/" not in url:
        return url
    path = "/realms/" + url.split("/realms/", 1)[1]
    return OIDC_INTERNAL_URL.rstrip("/") + path


async def _fetch_jwks(iss: str) -> dict:
    """
    Fetch JWKS for a specific token issuer via OIDC discovery.

    1. GET {iss}/.well-known/openid-configuration
    2. Extract jwks_uri from the discovery document.
    3. GET {jwks_uri} to obtain the JWKS.
    Results are cached per issuer for JWKS_CACHE_TTL seconds.
    """
    cached = _jwks_cache.get(iss)
    if cached and time.monotonic() < cached[1]:
        return cached[0]

    internal_iss = _to_internal_url(iss)
    discovery_url = f"{internal_iss}/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            disc_resp = await client.get(discovery_url)
            disc_resp.raise_for_status()
            jwks_uri: Optional[str] = disc_resp.json().get("jwks_uri")
            if not jwks_uri:
                raise ValueError("jwks_uri missing from OIDC discovery document")

            # Convert jwks_uri to internal URL too
            jwks_uri = _to_internal_url(jwks_uri)

            jwks_resp = await client.get(jwks_uri)
            jwks_resp.raise_for_status()
            jwks = jwks_resp.json()

    except Exception as e:
        logger.error("Failed to fetch JWKS for issuer %s: %s", iss, e)
        raise HTTPException(status_code=401, detail="Unable to fetch signing keys")

    _jwks_cache[iss] = (jwks, time.monotonic() + JWKS_CACHE_TTL)
    return jwks


def _decode_token_metadata(token: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    try:
        header = jwt.get_unverified_header(token)
        claims = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_aud": False,
                "verify_exp": False,
                "verify_nbf": False,
                "verify_iat": False,
            },
        )
    except (jwt.exceptions.PyJWTError, ValueError) as exc:
        raise HTTPException(status_code=401, detail=f"Malformed token: {exc}") from exc
    return header, claims


async def _verify_rs_token(
    token: str,
    algorithm: str,
    header: Dict[str, Any],
    claims: Dict[str, Any],
) -> Tuple[Dict[str, Any], str]:
    iss = claims.get("iss", "")
    if not iss:
        raise HTTPException(status_code=401, detail="Missing iss claim in token")

    tenant_id = _extract_tenant_from_issuer(iss)
    jwks = await _fetch_jwks(iss)
    kid = header.get("kid")
    key_data = next(
        (key for key in jwks.get("keys", []) if not kid or key.get("kid") == kid),
        None,
    )
    if key_data is None:
        raise HTTPException(status_code=401, detail="Signing key not found in JWKS")

    public_key = RSAAlgorithm.from_jwk(json.dumps(key_data))
    payload = jwt.decode(
        token,
        public_key,
        algorithms=[algorithm],
        options={"verify_aud": False},
    )
    if tenant_id:
        payload["tenant_id"] = tenant_id
    return payload, tenant_id


def _verify_hs_token(token: str) -> Tuple[Dict[str, Any], str]:
    payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    iss = payload.get("iss", "")
    tenant_id = _extract_tenant_from_issuer(iss) or payload.get("tenant_id", "")
    if tenant_id:
        payload["tenant_id"] = tenant_id
    return payload, tenant_id


async def _verify_payload(
    token: str,
    header: Dict[str, Any],
    claims: Dict[str, Any],
) -> Tuple[Dict[str, Any], str]:
    algorithm = header.get("alg", "RS256")
    try:
        if algorithm.startswith("RS") and (OIDC_BASE_URL or OIDC_INTERNAL_URL):
            return await _verify_rs_token(token, algorithm, header, claims)
        if JWT_SECRET:
            return _verify_hs_token(token)
        raise HTTPException(
            status_code=401,
            detail="No verification key configured (set OIDC_BASE_URL or JWT_SECRET)",
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Authentication failed: {exc}") from exc


def _normalise_tenant_id(payload: Dict[str, Any], tenant_id: str) -> str:
    if tenant_id:
        return tenant_id
    iss = payload.get("iss", "")
    return _extract_tenant_from_issuer(iss) or payload.get("tenant_id", "")


def _groups_from_roles(roles_list: Any) -> list:
    if not roles_list or not isinstance(roles_list, list):
        return []
    if isinstance(roles_list[0], dict):
        return [r.get("name", "") for r in roles_list if isinstance(r, dict) and r.get("name")]
    if isinstance(roles_list[0], str):
        return roles_list
    return []


def _groups_from_payload(payload: Dict[str, Any]) -> list:
    groups = payload.get("groups", [])
    if isinstance(groups, list) and groups:
        return groups
    return _groups_from_roles(payload.get("roles", []))


async def verify_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Dict[str, Any]:
    """
    Verify a JWT Bearer token.

    Flow:
    1. Decode header + claims WITHOUT signature verification.
    2. Extract tenant_id from the iss (issuer) claim:
         iss = {OIDC_BASE_URL}/realms/{tenant_id}
    3. Use iss as the OIDC discovery base to fetch the per-tenant JWKS.
    4. Verify the token signature using the RSA public key (RS256).
    5. Fall back to HS256 with JWT_SECRET when OIDC_BASE_URL is not set
       (development / testing). In fallback mode tenant_id is read from
       the tenant_id claim directly.

    Returns a dict with user_id, tenant_id, groups, token.
    """
    token = credentials.credentials
    unverified_header, unverified_claims = _decode_token_metadata(token)
    payload, tenant_id = await _verify_payload(token, unverified_header, unverified_claims)

    # Validate required claims - only "sub" is strictly required.
    # tenant_id may not exist for master realm users.
    if "sub" not in payload:
        raise HTTPException(
            status_code=401, detail="Missing required field in token: sub"
        )

    tenant_id = _normalise_tenant_id(payload, tenant_id)
    groups = _groups_from_payload(payload)

    return {
        "user_id": payload["sub"],
        "username": payload.get("preferred_username", ""),
        "nickname": payload.get("nickname", ""),
        "tenant_id": tenant_id,
        "groups": groups,
        "token": token,
    }


# ---------------------------------------------------------------------------
# API Key verification
# ---------------------------------------------------------------------------

async def verify_api_key(
    api_key: str,
    request_path: str = "",
) -> Dict[str, Any]:
    """
    Verify an API key (X-API-Key header) against the api_keys table.

    Steps:
      1. SHA-256 hash the key
      2. Look up api_keys by hash
      3. Check: exists, enabled, not expired
      4. Optionally check allowed_paths
      5. Update last_used_at
      6. Return identity dict compatible with verify_token output

    Raises HTTPException(401) on any failure.
    """
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    from . import db as pep_db  # lazy import to avoid circular dependency

    pool = pep_db.get_pool()

    row = await pool.fetchrow(
        """
        SELECT id, tenant_id, app_name, subject_id, subject_type,
               owner_user_id, allowed_paths, rate_limit, expires_at, enabled
        FROM api_keys
        WHERE api_key_hash = $1
        """,
        key_hash,
    )

    if row is None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    if not row["enabled"]:
        raise HTTPException(status_code=401, detail="API key is disabled")

    if row["expires_at"] is not None:
        import datetime
        now = datetime.datetime.utcnow()
        if row["expires_at"] < now:
            raise HTTPException(status_code=401, detail="API key has expired")

    allowed = row["allowed_paths"]
    if allowed and request_path:
        path_ok = any(request_path.startswith(p) for p in allowed)
        if not path_ok:
            raise HTTPException(
                status_code=403,
                detail="API key not authorized for this path",
            )

    # Fire-and-forget last_used_at update
    try:
        await pool.execute(
            "UPDATE api_keys SET last_used_at = NOW() WHERE id = $1",
            row["id"],
        )
    except Exception as e:
        logger.warning("Failed to update last_used_at for API key %s: %s", row["id"], e)

    # Use owner_user_id as the acting identity so downstream resource ACL
    # checks (e.g. KnowledgeBase ownership) resolve to the real user, not
    # the service account subject_id.
    acting_user_id = row["owner_user_id"] or row["subject_id"]

    return {
        "user_id": acting_user_id,
        "username": acting_user_id,
        "nickname": "",
        "tenant_id": row["tenant_id"],
        "groups": ["all-users"],
        "subject_type": row["subject_type"],
        "app_name": row["app_name"],
        "token": "",
    }
