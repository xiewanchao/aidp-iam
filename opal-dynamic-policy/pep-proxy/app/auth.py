# app/auth.py
import hashlib
import os
import time
import logging
import httpx
from jose import jwt, jwk
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
    "postgresql://keycloak:keycloak@postgres.keycloak.svc.cluster.local:5432/iam",
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
    Used by the gRPC ext-authz server when agentgateway pre-verification
    metadata is unavailable.
    """
    try:
        return jwt.get_unverified_claims(token)
    except Exception:
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

    Returns a dict with user_id, tenant_id, groups, email, name, token.
    """
    token = credentials.credentials

    # Step 1 - read header / claims without verifying signature
    try:
        unverified_header = jwt.get_unverified_header(token)
        unverified_claims = jwt.get_unverified_claims(token)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Malformed token: {e}")

    algorithm: str = unverified_header.get("alg", "RS256")
    tenant_id: str = ""

    try:
        if algorithm.startswith("RS") and (OIDC_BASE_URL or OIDC_INTERNAL_URL):
            # Step 2 - extract tenant_id from iss claim
            iss: str = unverified_claims.get("iss", "")
            if not iss:
                raise HTTPException(status_code=401, detail="Missing iss claim in token")

            tenant_id = _extract_tenant_from_issuer(iss)
            # tenant_id may be empty for master realm users - that is OK

            # Step 3-4 - OIDC discovery and JWKS verification
            jwks = await _fetch_jwks(iss)
            kid = unverified_header.get("kid")

            # Find the matching key; fall back to first key when no kid present
            key_data = next(
                (k for k in jwks.get("keys", []) if not kid or k.get("kid") == kid),
                None,
            )
            if key_data is None:
                raise HTTPException(
                    status_code=401, detail="Signing key not found in JWKS"
                )

            public_key = jwk.construct(key_data)
            payload = jwt.decode(
                token,
                public_key.to_pem().decode(),
                algorithms=[algorithm],
                options={"verify_aud": False},
            )
            # Override tenant_id with the iss-derived string name (realm slug),
            # NOT the UUID that Keycloak may embed directly in the tenant_id claim.
            # This ensures consistency with the Rego _user_tenant extraction.
            if tenant_id:
                payload["tenant_id"] = tenant_id

        elif JWT_SECRET:
            # Step 5 - HS256 fallback for dev / testing
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            # In dev mode derive tenant from iss if present, else from tenant_id claim
            iss = payload.get("iss", "")
            tenant_id = _extract_tenant_from_issuer(iss) or payload.get("tenant_id", "")
            if tenant_id:
                payload["tenant_id"] = tenant_id

        else:
            raise HTTPException(
                status_code=401,
                detail="No verification key configured (set OIDC_BASE_URL or JWT_SECRET)",
            )

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.JWTClaimsError as e:
        raise HTTPException(status_code=401, detail=f"Invalid claims: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Authentication failed: {e}")

    # Validate required claims - only "sub" is strictly required.
    # tenant_id may not exist for master realm users.
    if "sub" not in payload:
        raise HTTPException(
            status_code=401, detail="Missing required field in token: sub"
        )

    # Robust tenant_id extraction: prefer iss-derived, then payload claim
    if not tenant_id:
        iss = payload.get("iss", "")
        tenant_id = _extract_tenant_from_issuer(iss)
    if not tenant_id:
        tenant_id = payload.get("tenant_id", "")

    # Extract groups from JWT payload
    groups = payload.get("groups", [])
    if not isinstance(groups, list):
        groups = []

    # Backward compatibility: if groups is empty, check for roles and map to groups
    if not groups:
        roles_list = payload.get("roles", [])
        if roles_list and isinstance(roles_list, list):
            if roles_list and isinstance(roles_list[0], dict):
                # roles: [{id, name}, ...] structure from Keycloak Script Mapper
                groups = [r.get("name", "") for r in roles_list if isinstance(r, dict) and r.get("name")]
            elif roles_list and isinstance(roles_list[0], str):
                # roles: ["role-name", ...] plain string list
                groups = roles_list

    return {
        "user_id": payload["sub"],
        "tenant_id": tenant_id,
        "groups": groups,
        "email": payload.get("email", ""),
        "name": payload.get("name", ""),
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
               allowed_paths, rate_limit, expires_at, enabled
        FROM api_keys
        WHERE api_key_hash = $1
        """,
        key_hash,
    )

    if row is None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    if not row["enabled"]:
        raise HTTPException(status_code=401, detail="API key is disabled")

    # Check expiration
    if row["expires_at"] is not None:
        import datetime
        now = datetime.datetime.utcnow()
        if row["expires_at"] < now:
            raise HTTPException(status_code=401, detail="API key has expired")

    # Check allowed_paths (if set and request_path provided)
    allowed = row["allowed_paths"]
    if allowed and request_path:
        path_ok = any(request_path.startswith(p) for p in allowed)
        if not path_ok:
            raise HTTPException(
                status_code=403,
                detail="API key not authorized for this path",
            )

    # Update last_used_at (fire-and-forget, don't block the response)
    try:
        await pool.execute(
            "UPDATE api_keys SET last_used_at = NOW() WHERE id = $1",
            row["id"],
        )
    except Exception as e:
        logger.warning("Failed to update last_used_at for API key %s: %s", row["id"], e)

    return {
        "user_id": row["subject_id"],
        "tenant_id": row["tenant_id"],
        "groups": ["all-users"],
        "subject_type": row["subject_type"],
        "email": "",
        "name": row["app_name"],
        "token": "",
    }
