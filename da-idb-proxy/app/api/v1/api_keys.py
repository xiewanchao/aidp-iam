"""
API Key Management API.

Provides CRUD operations for tenant-scoped API keys.
Keys are generated with an `ak_` prefix, SHA-256 hashed before storage,
and the plaintext is returned only on create / rotate.
"""

import hashlib
import secrets
import uuid
from typing import List

from fastapi import APIRouter, status, HTTPException

from app.core.db import get_pool
from app.schemas.api_keys import (
    ApiKeyCreate,
    ApiKeyUpdate,
    ApiKeyResponse,
    ApiKeyCreateResponse,
)

router = APIRouter(prefix="/{tenant}/api-keys", tags=["API Keys"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_api_key() -> str:
    """Generate a plaintext API key: ak_ + 32 random bytes as hex (68 chars total)."""
    return "ak_" + secrets.token_hex(32)


def _hash_key(plaintext: str) -> str:
    """Return the SHA-256 hex digest of the plaintext key."""
    return hashlib.sha256(plaintext.encode()).hexdigest()


def _key_prefix(plaintext: str) -> str:
    """Return the first 8 characters of the key for display purposes."""
    return plaintext[:8]


def _generate_subject_id(app_name: str) -> str:
    """Auto-generate a subject_id: {app_name}-svc-{short_uuid}."""
    short = uuid.uuid4().hex[:8]
    return f"{app_name}-svc-{short}"


def _row_to_response(row) -> ApiKeyResponse:
    """Convert an asyncpg Record to an ApiKeyResponse."""
    data = dict(row)
    return ApiKeyResponse(**data)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("", status_code=status.HTTP_201_CREATED, response_model=ApiKeyCreateResponse)
async def create_api_key(tenant: str, payload: ApiKeyCreate):
    """
    Create a new API key for the tenant.

    The plaintext key is returned **only once** in the response.
    """
    pool = await get_pool()

    key_id = str(uuid.uuid4())
    plaintext = _generate_api_key()
    key_hash = _hash_key(plaintext)
    prefix = _key_prefix(plaintext)
    subject_id = _generate_subject_id(payload.app_name)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO api_keys
                (id, api_key_hash, key_prefix, tenant_id, app_name, description,
                 subject_id, subject_type, allowed_paths, rate_limit, expires_at,
                 enabled, created_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7, 'service', $8, $9, $10, true, $11)
            RETURNING id, key_prefix, tenant_id, app_name, description,
                      subject_id, subject_type, allowed_paths, rate_limit,
                      expires_at, enabled, created_by, created_at, updated_at,
                      last_used_at
            """,
            key_id,
            key_hash,
            prefix,
            tenant,
            payload.app_name,
            payload.description,
            subject_id,
            payload.allowed_paths,
            payload.rate_limit or 100,
            payload.expires_at,
            subject_id,  # created_by = subject_id for service keys
        )

    return ApiKeyCreateResponse(**dict(row), api_key=plaintext)


@router.get("", response_model=List[ApiKeyResponse])
async def list_api_keys(tenant: str):
    """List all API keys for a tenant (prefix only, no plaintext or hash)."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, key_prefix, tenant_id, app_name, description,
                   subject_id, subject_type, allowed_paths, rate_limit,
                   expires_at, enabled, created_by, created_at, updated_at,
                   last_used_at
            FROM api_keys
            WHERE tenant_id = $1
            ORDER BY created_at DESC
            """,
            tenant,
        )

    return [_row_to_response(r) for r in rows]


@router.get("/{key_id}", response_model=ApiKeyResponse)
async def get_api_key(tenant: str, key_id: str):
    """Get details of a single API key (no plaintext or hash)."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, key_prefix, tenant_id, app_name, description,
                   subject_id, subject_type, allowed_paths, rate_limit,
                   expires_at, enabled, created_by, created_at, updated_at,
                   last_used_at
            FROM api_keys
            WHERE id = $1 AND tenant_id = $2
            """,
            key_id,
            tenant,
        )

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key '{key_id}' not found",
        )

    return _row_to_response(row)


@router.put("/{key_id}", response_model=ApiKeyResponse)
async def update_api_key(tenant: str, key_id: str, payload: ApiKeyUpdate):
    """Update API key metadata (description, enabled, allowed_paths, rate_limit)."""
    pool = await get_pool()

    fields = payload.model_dump(exclude_none=True)
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

    # key_id and tenant_id are the last two positional params
    values.append(key_id)
    values.append(tenant)
    set_clause = ", ".join(set_parts)
    n = len(values)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE api_keys
            SET {set_clause}, updated_at = NOW()
            WHERE id = ${n - 1} AND tenant_id = ${n}
            RETURNING id, key_prefix, tenant_id, app_name, description,
                      subject_id, subject_type, allowed_paths, rate_limit,
                      expires_at, enabled, created_by, created_at, updated_at,
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
async def delete_api_key(tenant: str, key_id: str):
    """Delete (revoke) an API key permanently."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM api_keys WHERE id = $1 AND tenant_id = $2 RETURNING id",
            key_id,
            tenant,
        )

    if deleted is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key '{key_id}' not found",
        )

    return None


@router.post("/{key_id}/rotate", response_model=ApiKeyCreateResponse)
async def rotate_api_key(tenant: str, key_id: str):
    """
    Rotate an API key: generate a new key, invalidate the old one.

    The subject_id is preserved so downstream permissions remain intact.
    Returns the new plaintext key (shown only once).
    """
    pool = await get_pool()

    new_plaintext = _generate_api_key()
    new_hash = _hash_key(new_plaintext)
    new_prefix = _key_prefix(new_plaintext)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE api_keys
            SET api_key_hash = $1, key_prefix = $2, updated_at = NOW()
            WHERE id = $3 AND tenant_id = $4
            RETURNING id, key_prefix, tenant_id, app_name, description,
                      subject_id, subject_type, allowed_paths, rate_limit,
                      expires_at, enabled, created_by, created_at, updated_at,
                      last_used_at
            """,
            new_hash,
            new_prefix,
            key_id,
            tenant,
        )

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key '{key_id}' not found",
        )

    return ApiKeyCreateResponse(**dict(row), api_key=new_plaintext)
