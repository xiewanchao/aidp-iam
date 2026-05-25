"""
API Key Management API.

Provides CRUD operations for tenant-scoped API keys.
Keys are generated with an `ak_` prefix, SHA-256 hashed before storage,
and the plaintext is returned only on create / rotate.
"""

import hashlib
import secrets
import uuid
from typing import List, Optional

from fastapi import APIRouter, Header, status, HTTPException

from app.core.db import get_pool
from app.schemas.api_keys import (
    ApiKeyCreate,
    ApiKeyUpdate,
    ApiKeyResponse,
    ApiKeyCreateResponse,
)

router = APIRouter(prefix="/{tenant}/ApiKeys", tags=["API Keys"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_api_key() -> str:
    """Generate a plaintext API key: ak_ + 32 random bytes as hex (67 chars total)."""
    return "ak_" + secrets.token_hex(32)


def _hash_key(plaintext: str) -> str:
    """Return the SHA-256 hex digest of the plaintext key."""
    return hashlib.sha256(plaintext.encode()).hexdigest()


def _key_prefix(plaintext: str) -> str:
    """Return the first 8 characters of the key for display purposes."""
    return plaintext[:8]


def _key_suffix(plaintext: str) -> str:
    """Return the last 8 characters of the key for display purposes."""
    return plaintext[-8:]


def _key_display(prefix: str, suffix: str | None = None) -> str:
    """Return the redacted display form shown in API responses."""
    if suffix:
        return f"{prefix}...{suffix}"
    return f"{prefix}..."


def _normalize_name(name: str | None, fallback: str) -> str:
    """Normalize display name while keeping old callers compatible."""
    candidate = (name or "").strip()
    return candidate or fallback


def _generate_subject_id(app_name: str) -> str:
    """Auto-generate a subject_id: {app_name}-svc-{short_uuid}."""
    short = uuid.uuid4().hex[:8]
    return f"{app_name}-svc-{short}"


def _parse_groups(groups_header: Optional[str]) -> set[str]:
    """Parse the comma-separated groups injected by pep-proxy."""
    if not groups_header:
        return set()
    return {g.strip() for g in groups_header.split(",") if g.strip()}


def _is_admin_caller(groups: set[str], tenant: str) -> bool:
    """Return whether the caller may manage API keys across the tenant."""
    admin_names = {"master-admins", "tenant-admins", "admins"}
    admin_paths = {
        f"AccessManager/Tenants/{tenant}/Groups/master-admins",
        f"AccessManager/Tenants/{tenant}/Groups/tenant-admins",
        f"AccessManager/Tenants/{tenant}/Groups/admins",
    }
    return bool(groups & (admin_names | admin_paths))


def _caller_context(
    tenant: str,
    x_auth_user_id: Optional[str],
    x_auth_groups: Optional[str],
    x_auth_tenant: Optional[str],
) -> tuple[str, bool]:
    """Resolve the authenticated caller and whether it is tenant admin."""
    x_auth_user_id = x_auth_user_id if isinstance(x_auth_user_id, str) else None
    x_auth_groups = x_auth_groups if isinstance(x_auth_groups, str) else None
    x_auth_tenant = x_auth_tenant if isinstance(x_auth_tenant, str) else None
    caller_user_id = (x_auth_user_id or "").strip()
    if not caller_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Auth-User-Id header",
        )

    groups = _parse_groups(x_auth_groups)
    is_admin = _is_admin_caller(groups, tenant)
    if x_auth_tenant and x_auth_tenant != tenant and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-tenant API key management denied",
        )
    return caller_user_id, is_admin


_api_key_schema_ready = False


async def _ensure_api_key_schema(conn) -> None:
    """Add display metadata columns when upgrading an existing deployment."""
    global _api_key_schema_ready
    if _api_key_schema_ready:
        return

    await conn.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS name VARCHAR(128)")
    await conn.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS key_suffix VARCHAR(16)")
    await conn.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS owner_user_id VARCHAR(128)")
    await conn.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS created_by_user_id VARCHAR(128)")
    await conn.execute("UPDATE api_keys SET name = app_name WHERE name IS NULL")
    await conn.execute("UPDATE api_keys SET owner_user_id = created_by WHERE owner_user_id IS NULL")
    await conn.execute("UPDATE api_keys SET created_by_user_id = created_by WHERE created_by_user_id IS NULL")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_apikey_owner ON api_keys (tenant_id, owner_user_id)")
    _api_key_schema_ready = True


def _row_to_response(row) -> ApiKeyResponse:
    """Convert an asyncpg Record to an ApiKeyResponse."""
    data = dict(row)
    data["name"] = data.get("name") or data["app_name"]
    data["owner_user_id"] = data.get("owner_user_id") or data.get("created_by_user_id") or data["created_by"]
    data["created_by_user_id"] = data.get("created_by_user_id") or data["created_by"]
    data["key_display"] = _key_display(data["key_prefix"], data.get("key_suffix"))
    data["status"] = "enabled" if data["enabled"] else "disabled"
    data["operations"] = ["delete"]
    return ApiKeyResponse(**data)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("", status_code=status.HTTP_201_CREATED, response_model=ApiKeyCreateResponse)
async def create_api_key(
    tenant: str,
    payload: ApiKeyCreate,
    x_auth_user_id: Optional[str] = Header(None, alias="X-Auth-User-Id"),
    x_auth_groups: Optional[str] = Header(None, alias="X-Auth-Groups"),
    x_auth_tenant: Optional[str] = Header(None, alias="X-Auth-Tenant"),
):
    """
    Create a new API key for the tenant.

    The plaintext key is returned **only once** in the response.
    """
    pool = await get_pool()
    caller_user_id, _ = _caller_context(tenant, x_auth_user_id, x_auth_groups, x_auth_tenant)

    key_id = str(uuid.uuid4())
    plaintext = _generate_api_key()
    key_hash = _hash_key(plaintext)
    prefix = _key_prefix(plaintext)
    suffix = _key_suffix(plaintext)
    subject_id = _generate_subject_id(payload.app_name)
    name = _normalize_name(payload.name, payload.app_name)

    async with pool.acquire() as conn:
        await _ensure_api_key_schema(conn)
        row = await conn.fetchrow(
            """
            INSERT INTO api_keys
                (id, api_key_hash, key_prefix, key_suffix, tenant_id, name, app_name, description,
                 subject_id, subject_type, allowed_paths, rate_limit, expires_at,
                 enabled, owner_user_id, created_by_user_id, created_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'service', $10, $11, $12, true, $13, $14, $15)
            RETURNING id, name, key_prefix, key_suffix, tenant_id, owner_user_id, app_name, description,
                      subject_id, subject_type, allowed_paths, rate_limit,
                      expires_at, enabled, created_by_user_id, created_by, created_at, updated_at,
                      last_used_at
            """,
            key_id,
            key_hash,
            prefix,
            suffix,
            tenant,
            name,
            payload.app_name,
            payload.description,
            subject_id,
            payload.allowed_paths,
            payload.rate_limit or 100,
            payload.expires_at,
            caller_user_id,
            caller_user_id,
            caller_user_id,
        )

    return ApiKeyCreateResponse(**_row_to_response(row).model_dump(), api_key=plaintext)


@router.get("", response_model=List[ApiKeyResponse])
async def list_api_keys(
    tenant: str,
    x_auth_user_id: Optional[str] = Header(None, alias="X-Auth-User-Id"),
    x_auth_groups: Optional[str] = Header(None, alias="X-Auth-Groups"),
    x_auth_tenant: Optional[str] = Header(None, alias="X-Auth-Tenant"),
):
    """List all API keys for a tenant (prefix only, no plaintext or hash)."""
    pool = await get_pool()
    caller_user_id, is_admin = _caller_context(tenant, x_auth_user_id, x_auth_groups, x_auth_tenant)
    owner_clause = "" if is_admin else "AND owner_user_id = $2"
    args = (tenant,) if is_admin else (tenant, caller_user_id)

    async with pool.acquire() as conn:
        await _ensure_api_key_schema(conn)
        rows = await conn.fetch(
            f"""
            SELECT id, COALESCE(name, app_name) AS name, key_prefix, key_suffix,
                   tenant_id, owner_user_id, app_name, description,
                   subject_id, subject_type, allowed_paths, rate_limit,
                   expires_at, enabled, created_by_user_id, created_by, created_at, updated_at,
                   last_used_at
            FROM api_keys
            WHERE tenant_id = $1
              {owner_clause}
            ORDER BY created_at DESC
            """,
            *args,
        )

    return [_row_to_response(r) for r in rows]


@router.get("/{key_id}", response_model=ApiKeyResponse)
async def get_api_key(
    tenant: str,
    key_id: str,
    x_auth_user_id: Optional[str] = Header(None, alias="X-Auth-User-Id"),
    x_auth_groups: Optional[str] = Header(None, alias="X-Auth-Groups"),
    x_auth_tenant: Optional[str] = Header(None, alias="X-Auth-Tenant"),
):
    """Get details of a single API key (no plaintext or hash)."""
    pool = await get_pool()
    caller_user_id, is_admin = _caller_context(tenant, x_auth_user_id, x_auth_groups, x_auth_tenant)
    owner_clause = "" if is_admin else "AND owner_user_id = $3"
    args = (key_id, tenant) if is_admin else (key_id, tenant, caller_user_id)

    async with pool.acquire() as conn:
        await _ensure_api_key_schema(conn)
        row = await conn.fetchrow(
            f"""
            SELECT id, COALESCE(name, app_name) AS name, key_prefix, key_suffix,
                   tenant_id, owner_user_id, app_name, description,
                   subject_id, subject_type, allowed_paths, rate_limit,
                   expires_at, enabled, created_by_user_id, created_by, created_at, updated_at,
                   last_used_at
            FROM api_keys
            WHERE id = $1 AND tenant_id = $2
              {owner_clause}
            """,
            *args,
        )

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key '{key_id}' not found",
        )

    return _row_to_response(row)


@router.put("/{key_id}", response_model=ApiKeyResponse)
async def update_api_key(
    tenant: str,
    key_id: str,
    payload: ApiKeyUpdate,
    x_auth_user_id: Optional[str] = Header(None, alias="X-Auth-User-Id"),
    x_auth_groups: Optional[str] = Header(None, alias="X-Auth-Groups"),
    x_auth_tenant: Optional[str] = Header(None, alias="X-Auth-Tenant"),
):
    """Update API key metadata (description, enabled, allowed_paths, rate_limit)."""
    pool = await get_pool()
    caller_user_id, is_admin = _caller_context(tenant, x_auth_user_id, x_auth_groups, x_auth_tenant)

    fields = payload.model_dump(exclude_none=True)
    if "name" in fields:
        fields["name"] = _normalize_name(fields["name"], "")
        if not fields["name"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="API key name cannot be blank",
            )

    if not fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )

    set_parts = []
    values = []
    for idx, (col, val) in enumerate(fields.items(), start=1):
        set_parts.append(f"{col} = ${idx}")
        values.append(val)

    set_clause = ", ".join(set_parts)
    values.append(key_id)
    key_id_param = len(values)
    values.append(tenant)
    tenant_param = len(values)
    owner_clause = ""
    if not is_admin:
        values.append(caller_user_id)
        owner_clause = f"AND owner_user_id = ${len(values)}"

    async with pool.acquire() as conn:
        await _ensure_api_key_schema(conn)
        row = await conn.fetchrow(
            f"""
            UPDATE api_keys
            SET {set_clause}, updated_at = NOW()
            WHERE id = ${key_id_param} AND tenant_id = ${tenant_param}
              {owner_clause}
            RETURNING id, COALESCE(name, app_name) AS name, key_prefix, key_suffix,
                      tenant_id, owner_user_id, app_name, description,
                      subject_id, subject_type, allowed_paths, rate_limit,
                      expires_at, enabled, created_by_user_id, created_by, created_at, updated_at,
                      last_used_at
            """,
            *values,
        )

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key '{key_id}' not found",
        )

    return _row_to_response(row)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    tenant: str,
    key_id: str,
    x_auth_user_id: Optional[str] = Header(None, alias="X-Auth-User-Id"),
    x_auth_groups: Optional[str] = Header(None, alias="X-Auth-Groups"),
    x_auth_tenant: Optional[str] = Header(None, alias="X-Auth-Tenant"),
):
    """Delete (revoke) an API key permanently."""
    pool = await get_pool()
    caller_user_id, is_admin = _caller_context(tenant, x_auth_user_id, x_auth_groups, x_auth_tenant)
    owner_clause = "" if is_admin else "AND owner_user_id = $3"
    args = (key_id, tenant) if is_admin else (key_id, tenant, caller_user_id)

    async with pool.acquire() as conn:
        await _ensure_api_key_schema(conn)
        deleted = await conn.fetchval(
            f"""
            DELETE FROM api_keys
            WHERE id = $1 AND tenant_id = $2
              {owner_clause}
            RETURNING id
            """,
            *args,
        )

    if deleted is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key '{key_id}' not found",
        )

    return None


@router.post("/{key_id}/Rotate", response_model=ApiKeyCreateResponse)
async def rotate_api_key(
    tenant: str,
    key_id: str,
    x_auth_user_id: Optional[str] = Header(None, alias="X-Auth-User-Id"),
    x_auth_groups: Optional[str] = Header(None, alias="X-Auth-Groups"),
    x_auth_tenant: Optional[str] = Header(None, alias="X-Auth-Tenant"),
):
    """
    Rotate an API key: generate a new key, invalidate the old one.

    The subject_id is preserved so downstream permissions remain intact.
    Returns the new plaintext key (shown only once).
    """
    pool = await get_pool()
    caller_user_id, is_admin = _caller_context(tenant, x_auth_user_id, x_auth_groups, x_auth_tenant)

    new_plaintext = _generate_api_key()
    new_hash = _hash_key(new_plaintext)
    new_prefix = _key_prefix(new_plaintext)
    new_suffix = _key_suffix(new_plaintext)
    owner_clause = "" if is_admin else "AND owner_user_id = $6"
    args = (
        new_hash,
        new_prefix,
        new_suffix,
        key_id,
        tenant,
    ) if is_admin else (
        new_hash,
        new_prefix,
        new_suffix,
        key_id,
        tenant,
        caller_user_id,
    )

    async with pool.acquire() as conn:
        await _ensure_api_key_schema(conn)
        row = await conn.fetchrow(
            f"""
            UPDATE api_keys
            SET api_key_hash = $1, key_prefix = $2, key_suffix = $3, updated_at = NOW()
            WHERE id = $4 AND tenant_id = $5
              {owner_clause}
            RETURNING id, COALESCE(name, app_name) AS name, key_prefix, key_suffix,
                      tenant_id, owner_user_id, app_name, description,
                      subject_id, subject_type, allowed_paths, rate_limit,
                      expires_at, enabled, created_by_user_id, created_by, created_at, updated_at,
                      last_used_at
            """,
            *args,
        )

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key '{key_id}' not found",
        )

    return ApiKeyCreateResponse(**_row_to_response(row).model_dump(), api_key=new_plaintext)
