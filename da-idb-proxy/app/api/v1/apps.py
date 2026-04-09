"""
Application Registration API.

Manages the apps / resource_patterns tables in the IAM database and
auto-creates {app_name}-admins groups in every tenant realm via Keycloak.
"""

import os
from typing import List
from fastapi import APIRouter, status, HTTPException

from app.core.keycloak import kc
from app.core.db import get_pool
from app.schemas.apps import AppCreate, AppUpdate, AppResponse, ResourcePatternResponse

router = APIRouter(prefix="/apps", tags=["Applications"])

PROTECTED_REALM = os.getenv("KC_REALM", "master")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tenant_realms() -> List[dict]:
    """Return all non-master realms from Keycloak."""
    realms = kc.request("GET", "/realms").json()
    return [r for r in realms if r["realm"].lower() != PROTECTED_REALM.lower()]


def _create_group_in_realms(group_name: str) -> None:
    """Create a top-level group in every tenant realm (best-effort)."""
    for realm in _tenant_realms():
        realm_name = realm["realm"]
        try:
            kc.request("POST", f"/realms/{realm_name}/groups", json={"name": group_name})
        except Exception as exc:
            # 409 Conflict = group already exists, which is fine
            print(f"Warning: could not create group '{group_name}' in realm '{realm_name}': {exc}")


def _delete_group_in_realms(group_name: str) -> None:
    """Delete the named top-level group from every tenant realm (best-effort)."""
    for realm in _tenant_realms():
        realm_name = realm["realm"]
        try:
            groups = kc.request(
                "GET", f"/realms/{realm_name}/groups", params={"search": group_name}
            ).json()
            for g in groups:
                if g["name"] == group_name:
                    kc.request("DELETE", f"/realms/{realm_name}/groups/{g['id']}")
        except Exception as exc:
            print(f"Warning: could not delete group '{group_name}' in realm '{realm_name}': {exc}")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("", status_code=status.HTTP_201_CREATED, response_model=AppResponse)
async def register_app(payload: AppCreate):
    """Register a new application, persist to IAM DB, and create admins group."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Check for duplicate
        exists = await conn.fetchval(
            "SELECT 1 FROM apps WHERE app_name = $1", payload.app_name
        )
        if exists:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Application '{payload.app_name}' already exists",
            )

        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO apps (app_name, path_prefix, display_name, description)
                VALUES ($1, $2, $3, $4)
                RETURNING app_name, path_prefix, display_name, description,
                          enabled, created_at, updated_at
                """,
                payload.app_name,
                payload.path_prefix,
                payload.display_name,
                payload.description,
            )

            patterns = []
            for rp in payload.resource_patterns:
                await conn.execute(
                    """
                    INSERT INTO resource_patterns (app_name, resource_prefix, resource_type)
                    VALUES ($1, $2, $3)
                    """,
                    payload.app_name,
                    rp.resource_prefix,
                    rp.resource_type,
                )
                patterns.append(
                    ResourcePatternResponse(
                        app_name=payload.app_name,
                        resource_prefix=rp.resource_prefix,
                        resource_type=rp.resource_type,
                    )
                )

    # Create {app_name}-admins group in all tenant realms (best-effort, outside txn)
    _create_group_in_realms(f"{payload.app_name}-admins")

    return AppResponse(**dict(row), resource_patterns=patterns)


@router.get("", response_model=List[AppResponse])
async def list_apps():
    """List all registered applications with their resource patterns."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        app_rows = await conn.fetch(
            "SELECT app_name, path_prefix, display_name, description, "
            "enabled, created_at, updated_at FROM apps ORDER BY app_name"
        )

        results: List[AppResponse] = []
        for app_row in app_rows:
            pattern_rows = await conn.fetch(
                "SELECT app_name, resource_prefix, resource_type "
                "FROM resource_patterns WHERE app_name = $1",
                app_row["app_name"],
            )
            results.append(
                AppResponse(
                    **dict(app_row),
                    resource_patterns=[ResourcePatternResponse(**dict(p)) for p in pattern_rows],
                )
            )
    return results


@router.get("/{app_name}", response_model=AppResponse)
async def get_app(app_name: str):
    """Get a single application with its resource patterns."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        app_row = await conn.fetchrow(
            "SELECT app_name, path_prefix, display_name, description, "
            "enabled, created_at, updated_at FROM apps WHERE app_name = $1",
            app_name,
        )
        if app_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Application '{app_name}' not found",
            )

        pattern_rows = await conn.fetch(
            "SELECT app_name, resource_prefix, resource_type "
            "FROM resource_patterns WHERE app_name = $1",
            app_name,
        )

    return AppResponse(
        **dict(app_row),
        resource_patterns=[ResourcePatternResponse(**dict(p)) for p in pattern_rows],
    )


@router.put("/{app_name}", response_model=AppResponse)
async def update_app(app_name: str, payload: AppUpdate):
    """Update application metadata (display_name, description, enabled)."""
    pool = await get_pool()

    # Build dynamic SET clause from non-None fields
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

    # app_name is the last positional parameter
    values.append(app_name)
    set_clause = ", ".join(set_parts)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE apps SET {set_clause}, updated_at = now() "
            f"WHERE app_name = ${len(values)} "
            f"RETURNING app_name, path_prefix, display_name, description, "
            f"enabled, created_at, updated_at",
            *values,
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Application '{app_name}' not found",
            )

        pattern_rows = await conn.fetch(
            "SELECT app_name, resource_prefix, resource_type "
            "FROM resource_patterns WHERE app_name = $1",
            app_name,
        )

    return AppResponse(
        **dict(row),
        resource_patterns=[ResourcePatternResponse(**dict(p)) for p in pattern_rows],
    )


@router.delete("/{app_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_app(app_name: str):
    """Delete an application and its resource patterns. Removes admins group from realms."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM apps WHERE app_name = $1 RETURNING app_name",
            app_name,
        )
        if deleted is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Application '{app_name}' not found",
            )
        # resource_patterns cascade-deleted via FK ON DELETE CASCADE

    # Clean up Keycloak groups (best-effort)
    _delete_group_in_realms(f"{app_name}-admins")

    return None
