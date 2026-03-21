# app/auth.py
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

# Simple in-memory JWKS cache: iss -> (jwks_data, expiry_timestamp)
_jwks_cache: Dict[str, Tuple[dict, float]] = {}
JWKS_CACHE_TTL = 300  # seconds


def _extract_tenant_from_issuer(iss: str) -> str:
    """
    Extract tenant_id from an issuer URL.

    Expected format: {base}/realms/{tenant_id}[/...]
    Example: https://auth.example.com/realms/tenant-001
             → tenant-001
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

    # Use internal URL for discovery when OIDC_INTERNAL_URL is set (K8s environment)
    internal_iss = iss
    if OIDC_INTERNAL_URL and OIDC_BASE_URL and iss.startswith(OIDC_BASE_URL):
        internal_iss = iss.replace(OIDC_BASE_URL, OIDC_INTERNAL_URL, 1)

    discovery_url = f"{internal_iss}/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            disc_resp = await client.get(discovery_url)
            disc_resp.raise_for_status()
            jwks_uri: Optional[str] = disc_resp.json().get("jwks_uri")
            if not jwks_uri:
                raise ValueError("jwks_uri missing from OIDC discovery document")

            # Replace public URL with internal URL in jwks_uri too
            if OIDC_INTERNAL_URL and OIDC_BASE_URL:
                jwks_uri = jwks_uri.replace(OIDC_BASE_URL, OIDC_INTERNAL_URL, 1)

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
         iss = {OIDC_BASE_URL}/tenants/{tenant_id}
    3. Use iss as the OIDC discovery base to fetch the per-tenant JWKS.
    4. Verify the token signature using the RSA public key (RS256).
    5. Fall back to HS256 with JWT_SECRET when OIDC_BASE_URL is not set
       (development / testing). In fallback mode tenant_id is read from
       the tenant_id claim directly.
    """
    token = credentials.credentials

    # Step 1 – read header / claims without verifying signature
    try:
        unverified_header = jwt.get_unverified_header(token)
        unverified_claims = jwt.get_unverified_claims(token)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Malformed token: {e}")

    algorithm: str = unverified_header.get("alg", "RS256")

    try:
        if algorithm.startswith("RS") and OIDC_BASE_URL:
            # Step 2 – extract tenant_id from iss claim
            iss: str = unverified_claims.get("iss", "")
            if not iss:
                raise HTTPException(status_code=401, detail="Missing iss claim in token")

            if not iss.startswith(OIDC_BASE_URL):
                raise HTTPException(
                    status_code=401,
                    detail=f"Token issuer does not match OIDC_BASE_URL: {iss}",
                )

            tenant_id = _extract_tenant_from_issuer(iss)
            if not tenant_id:
                raise HTTPException(
                    status_code=401,
                    detail=f"Cannot extract tenant_id from issuer: {iss}",
                )

            # Step 3-4 – OIDC discovery and JWKS verification
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
            # Step 5 – HS256 fallback for dev / testing
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

    # Validate required claims
    for field in ("sub", "tenant_id"):
        if field not in payload:
            raise HTTPException(
                status_code=401, detail=f"Missing required field in token: {field}"
            )

    # roles: [{id, name}, ...] structure from Keycloak Script Mapper
    roles_list = payload.get("roles", [])
    roles    = [r.get("name", "") for r in roles_list if isinstance(r, dict) and r.get("name")]
    role_ids = [r.get("id",   "") for r in roles_list if isinstance(r, dict) and r.get("id")]

    return {
        "user_id":  payload["sub"],
        "tenant_id": payload["tenant_id"],
        "roles":    roles,     # 角色名列表，供 _require_admin / OPA _sys_roles 使用
        "role_ids": role_ids,  # 角色 UUID 列表，供 OPA Tier-3 policy 查询使用
        "email": payload.get("email", ""),
        "name":  payload.get("name",  ""),
        # Raw token forwarded to OPA so it can perform its own OIDC verification
        "token": token,
    }
