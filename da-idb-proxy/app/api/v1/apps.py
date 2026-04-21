"""
Application Registration API.

Manages apps / resource_patterns / resource_actions in the IAM database and
auto-creates {app_name}-admins groups in every tenant realm via Keycloak.

Resource patterns carry the flexible API adaptation fields
(id_source / id_field / id_query_param) and an optional nested `actions`
list that maps to resource_actions rows.  Empty `actions` means the
downstream components (resource-sync, pep-proxy) use the built-in
DEFAULT_ACTIONS for standard RESTful routes.
"""

import os
from typing import List
from fastapi import APIRouter, status, HTTPException

from app.core.keycloak import kc
from app.core.db import get_pool
from app.schemas.apps import (
    AppCreate, AppUpdate, AppResponse,
    ResourcePatternResponse, ResourceActionResponse,
)

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


async def _fetch_patterns_with_actions(conn, app_name: str) -> List[ResourcePatternResponse]:
    """Load all resource_patterns for an app + their resource_actions."""
    pattern_rows = await conn.fetch(
        """
        SELECT app_name, resource_prefix, resource_type,
               id_source, id_field, id_query_param
        FROM resource_patterns WHERE app_name = $1
        ORDER BY resource_prefix
        """,
        app_name,
    )
    action_rows = await conn.fetch(
        """
        SELECT id, resource_prefix, action, method, path_suffix,
               success_status, min_permission
        FROM resource_actions WHERE app_name = $1
        ORDER BY resource_prefix, id
        """,
        app_name,
    )

    actions_by_prefix: dict[str, list[ResourceActionResponse]] = {}
    for ar in action_rows:
        actions_by_prefix.setdefault(ar["resource_prefix"], []).append(
            ResourceActionResponse(
                id=ar["id"],
                action=ar["action"],
                method=ar["method"],
                path_suffix=ar["path_suffix"],
                success_status=ar["success_status"],
                min_permission=ar["min_permission"],
            )
        )

    return [
        ResourcePatternResponse(
            app_name=p["app_name"],
            resource_prefix=p["resource_prefix"],
            resource_type=p["resource_type"],
            id_source=p["id_source"],
            id_field=p["id_field"],
            id_query_param=p["id_query_param"],
            actions=actions_by_prefix.get(p["resource_prefix"], []),
        )
        for p in pattern_rows
    ]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("", status_code=status.HTTP_201_CREATED, response_model=AppResponse)
async def register_app(payload: AppCreate):
    """Register a new application, persist patterns + actions, create admins group."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM apps WHERE app_name = $1", payload.app_name
        )
        if exists:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Application '{payload.app_name}' already exists",
            )

        async with conn.transaction():
            admin_group = f"{payload.app_name}-admins"
            row = await conn.fetchrow(
                """
                INSERT INTO apps (app_name, path_prefix, display_name, description, admin_group)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING app_name, path_prefix, display_name, description,
                          admin_group, enabled, created_at, updated_at
                """,
                payload.app_name,
                payload.path_prefix,
                payload.display_name,
                payload.description,
                admin_group,
            )

            for rp in payload.resource_patterns:
                await conn.execute(
                    """
                    INSERT INTO resource_patterns
                        (app_name, resource_prefix, resource_type,
                         id_source, id_field, id_query_param)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    payload.app_name,
                    rp.resource_prefix,
                    rp.resource_type,
                    rp.id_source,
                    rp.id_field,
                    rp.id_query_param,
                )

                for ra in rp.actions:
                    await conn.execute(
                        """
                        INSERT INTO resource_actions
                            (app_name, resource_prefix, action, method,
                             path_suffix, success_status, min_permission)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                        payload.app_name,
                        rp.resource_prefix,
                        ra.action,
                        ra.method,
                        ra.path_suffix,
                        ra.success_status,
                        ra.min_permission,
                    )

        patterns = await _fetch_patterns_with_actions(conn, payload.app_name)

    # Create {app_name}-admins group in every realm (best-effort, outside txn).
    # In single-realm deployments this just iterates over `aidp`.
    _create_group_in_realms(f"{payload.app_name}-admins")

    return AppResponse(**dict(row), resource_patterns=patterns)


@router.get("", response_model=List[AppResponse])
async def list_apps():
    """List all registered applications with their resource patterns + actions."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        app_rows = await conn.fetch(
            "SELECT app_name, path_prefix, display_name, description, "
            "admin_group, enabled, created_at, updated_at FROM apps ORDER BY app_name"
        )

        results: List[AppResponse] = []
        for app_row in app_rows:
            patterns = await _fetch_patterns_with_actions(conn, app_row["app_name"])
            results.append(AppResponse(**dict(app_row), resource_patterns=patterns))
    return results


@router.get("/{app_name}", response_model=AppResponse)
async def get_app(app_name: str):
    """Get a single application with its resource patterns + actions."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        app_row = await conn.fetchrow(
            "SELECT app_name, path_prefix, display_name, description, "
            "admin_group, enabled, created_at, updated_at FROM apps WHERE app_name = $1",
            app_name,
        )
        if app_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Application '{app_name}' not found",
            )

        patterns = await _fetch_patterns_with_actions(conn, app_name)

    return AppResponse(**dict(app_row), resource_patterns=patterns)


@router.put("/{app_name}", response_model=AppResponse)
async def update_app(app_name: str, payload: AppUpdate):
    """Update application metadata (display_name, description, enabled)."""
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

    values.append(app_name)
    set_clause = ", ".join(set_parts)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE apps SET {set_clause}, updated_at = now() "
            f"WHERE app_name = ${len(values)} "
            f"RETURNING app_name, path_prefix, display_name, description, "
            f"admin_group, enabled, created_at, updated_at",
            *values,
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Application '{app_name}' not found",
            )

        patterns = await _fetch_patterns_with_actions(conn, app_name)

    return AppResponse(**dict(row), resource_patterns=patterns)


@router.delete("/{app_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_app(app_name: str):
    """Delete an application and its resource patterns. Removes admins group from realms."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            # resource_patterns → apps FK does not cascade on the schema level,
            # and resource_actions cascades off resource_patterns — delete in
            # dependency order to keep things clean.
            await conn.execute("DELETE FROM resource_patterns WHERE app_name = $1", app_name)
            deleted = await conn.fetchval(
                "DELETE FROM apps WHERE app_name = $1 RETURNING app_name",
                app_name,
            )
            if deleted is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Application '{app_name}' not found",
                )

    # Clean up Keycloak groups (best-effort)
    _delete_group_in_realms(f"{app_name}-admins")

    return None
