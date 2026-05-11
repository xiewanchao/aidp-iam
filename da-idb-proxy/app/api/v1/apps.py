"""
Application Registration API + resource_patterns / resource_actions CRUD.

Manages apps / resource_patterns / resource_actions in the IAM database and
auto-creates {app_name}-admins groups in every tenant realm via Keycloak.

Resource patterns carry the flexible API adaptation fields
(id_source / id_field / id_query_param) and an optional nested `actions`
list that maps to resource_actions rows. Empty `actions` means the
downstream components (resource-sync, pep-proxy) use the built-in
DEFAULT_ACTIONS for standard RESTful routes.

Composite keys:
  - resource_patterns  PK(app_name, resource_prefix, method)  — method='' fallback
  - resource_actions   PK(id) — SERIAL; looked up by (app_name, resource_prefix,
                                method, path_suffix) tuple downstream.
"""

import os
from typing import List, Optional
from fastapi import APIRouter, status, HTTPException, Query

from app.core.keycloak import kc
from app.core.db import get_pool
from app.schemas.apps import (
    AppCreate, AppUpdate, AppResponse,
    ResourcePatternIn, ResourcePatternUpdate, ResourcePatternResponse,
    ResourceActionIn, ResourceActionUpdate, ResourceActionResponse,
)

router = APIRouter(prefix="/Apps", tags=["Applications"])

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
        SELECT app_name, resource_prefix, method, resource_type,
               id_source, id_field, id_query_param, response_id_field,
               share_to_admin_group_on_create, share_to_all_users_on_create
        FROM resource_patterns WHERE app_name = $1
        ORDER BY resource_prefix, method
        """,
        app_name,
    )
    action_rows = await conn.fetch(
        """
        SELECT id, resource_prefix, method, action, path_suffix,
               success_status, min_permission
        FROM resource_actions WHERE app_name = $1
        ORDER BY resource_prefix, id
        """,
        app_name,
    )

    # Bucket actions by (resource_prefix, method) — we attach them to the
    # matching pattern; if no pattern has method='X' but an action does, we
    # still surface the action under the fallback pattern (method='').
    actions_by_prefix_method: dict[tuple[str, str], list[ResourceActionResponse]] = {}
    for ar in action_rows:
        key = (ar["resource_prefix"], ar["method"])
        actions_by_prefix_method.setdefault(key, []).append(
            ResourceActionResponse(
                id=ar["id"],
                action=ar["action"],
                method=ar["method"],
                path_suffix=ar["path_suffix"],
                success_status=ar["success_status"],
                min_permission=ar["min_permission"],
            )
        )

    patterns: List[ResourcePatternResponse] = []
    for p in pattern_rows:
        # Attach actions where resource_prefix matches; method either matches
        # exactly or the pattern is the fallback (method='') which "catches"
        # any leftover actions.
        matched: list[ResourceActionResponse] = []
        for (ap, am), actions in actions_by_prefix_method.items():
            if ap != p["resource_prefix"]:
                continue
            if p["method"] == "" or am == p["method"]:
                matched.extend(actions)
        patterns.append(
            ResourcePatternResponse(
                app_name=p["app_name"],
                resource_prefix=p["resource_prefix"],
                method=p["method"],
                resource_type=p["resource_type"],
                id_source=p["id_source"],
                id_field=p["id_field"],
                id_query_param=p["id_query_param"],
                response_id_field=p["response_id_field"],
                share_to_admin_group_on_create=p["share_to_admin_group_on_create"],
                share_to_all_users_on_create=p["share_to_all_users_on_create"],
                actions=matched,
            )
        )
    return patterns


async def _ensure_app_exists(conn, app_name: str) -> None:
    exists = await conn.fetchval("SELECT 1 FROM apps WHERE app_name = $1", app_name)
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application '{app_name}' not found",
        )


# ---------------------------------------------------------------------------
# Apps CRUD
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
                        (app_name, resource_prefix, method, resource_type,
                         id_source, id_field, id_query_param, response_id_field,
                         share_to_admin_group_on_create, share_to_all_users_on_create)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    payload.app_name,
                    rp.resource_prefix,
                    rp.method,
                    rp.resource_type,
                    rp.id_source,
                    rp.id_field,
                    rp.id_query_param,
                    rp.response_id_field,
                    rp.share_to_admin_group_on_create,
                    rp.share_to_all_users_on_create,
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
            # resource_actions has no FK → delete first, then patterns, then app.
            await conn.execute("DELETE FROM resource_actions WHERE app_name = $1", app_name)
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


# ---------------------------------------------------------------------------
# Resource Patterns CRUD (nested under app)
# ---------------------------------------------------------------------------

@router.post(
    "/{app_name}/resource-patterns",
    status_code=status.HTTP_201_CREATED,
    response_model=ResourcePatternResponse,
)
async def create_resource_pattern(app_name: str, payload: ResourcePatternIn):
    """Add a resource_pattern (and its nested actions) to an existing app."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_app_exists(conn, app_name)

        async with conn.transaction():
            try:
                await conn.execute(
                    """
                    INSERT INTO resource_patterns
                        (app_name, resource_prefix, method, resource_type,
                         id_source, id_field, id_query_param, response_id_field,
                         share_to_admin_group_on_create, share_to_all_users_on_create)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    app_name,
                    payload.resource_prefix,
                    payload.method,
                    payload.resource_type,
                    payload.id_source,
                    payload.id_field,
                    payload.id_query_param,
                    payload.response_id_field,
                    payload.share_to_admin_group_on_create,
                    payload.share_to_all_users_on_create,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"resource_pattern conflict: {exc}",
                )

            for ra in payload.actions:
                await conn.execute(
                    """
                    INSERT INTO resource_actions
                        (app_name, resource_prefix, action, method,
                         path_suffix, success_status, min_permission)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    app_name,
                    payload.resource_prefix,
                    ra.action,
                    ra.method,
                    ra.path_suffix,
                    ra.success_status,
                    ra.min_permission,
                )

        # Return the freshly-inserted pattern with its actions
        patterns = await _fetch_patterns_with_actions(conn, app_name)
    for p in patterns:
        if p.resource_prefix == payload.resource_prefix and p.method == payload.method:
            return p
    # Shouldn't happen
    raise HTTPException(status_code=500, detail="pattern vanished after insert")


@router.put(
    "/{app_name}/resource-patterns",
    response_model=ResourcePatternResponse,
)
async def update_resource_pattern(
    app_name: str,
    payload: ResourcePatternUpdate,
    resource_prefix: str = Query(..., description="Pattern resource_prefix (path param alternative)"),
    method: str = Query("", description="HTTP method; '' means fallback pattern"),
):
    """Update a resource_pattern identified by (app_name, resource_prefix, method).

    Because method (and / inside resource_prefix) makes path-based identification
    awkward, we expose a PUT with query parameters for the composite key.
    """
    fields = payload.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    set_parts, values = [], []
    for idx, (col, val) in enumerate(fields.items(), start=1):
        set_parts.append(f"{col} = ${idx}")
        values.append(val)
    values.extend([app_name, resource_prefix, method])

    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_app_exists(conn, app_name)
        updated = await conn.fetchval(
            f"UPDATE resource_patterns SET {', '.join(set_parts)} "
            f"WHERE app_name = ${len(values)-2} AND resource_prefix = ${len(values)-1} "
            f"AND method = ${len(values)} RETURNING resource_prefix",
            *values,
        )
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"resource_pattern ({app_name}, {resource_prefix}, {method!r}) not found",
            )
        patterns = await _fetch_patterns_with_actions(conn, app_name)
    for p in patterns:
        if p.resource_prefix == resource_prefix and p.method == method:
            return p
    raise HTTPException(status_code=500, detail="pattern vanished after update")


@router.delete(
    "/{app_name}/resource-patterns",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_resource_pattern(
    app_name: str,
    resource_prefix: str = Query(...),
    method: str = Query(""),
    cascade_actions: bool = Query(
        True,
        description="Also delete matching resource_actions (same app+prefix+method)",
    ),
):
    """Delete a single resource_pattern and optionally its matching actions."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_app_exists(conn, app_name)
        async with conn.transaction():
            if cascade_actions:
                await conn.execute(
                    "DELETE FROM resource_actions "
                    "WHERE app_name = $1 AND resource_prefix = $2 AND method = $3",
                    app_name, resource_prefix, method,
                )
            deleted = await conn.fetchval(
                "DELETE FROM resource_patterns "
                "WHERE app_name = $1 AND resource_prefix = $2 AND method = $3 "
                "RETURNING resource_prefix",
                app_name, resource_prefix, method,
            )
            if deleted is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"resource_pattern ({app_name}, {resource_prefix}, {method!r}) not found",
                )
    return None


# ---------------------------------------------------------------------------
# Resource Actions CRUD (nested under app, id-based)
# ---------------------------------------------------------------------------

@router.post(
    "/{app_name}/resource-actions",
    status_code=status.HTTP_201_CREATED,
    response_model=ResourceActionResponse,
)
async def create_resource_action(
    app_name: str,
    payload: ResourceActionIn,
    resource_prefix: str = Query(..., description="Which pattern this action attaches to"),
):
    """Add a standalone resource_action row for an existing app+prefix."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_app_exists(conn, app_name)
        row = await conn.fetchrow(
            """
            INSERT INTO resource_actions
                (app_name, resource_prefix, action, method, path_suffix,
                 success_status, min_permission)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id, action, method, path_suffix, success_status, min_permission
            """,
            app_name,
            resource_prefix,
            payload.action,
            payload.method,
            payload.path_suffix,
            payload.success_status,
            payload.min_permission,
        )
    return ResourceActionResponse(**dict(row))


@router.put(
    "/{app_name}/resource-actions/{action_id}",
    response_model=ResourceActionResponse,
)
async def update_resource_action(app_name: str, action_id: int, payload: ResourceActionUpdate):
    """Update a resource_action row by id (scoped to the app)."""
    fields = payload.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    set_parts, values = [], []
    for idx, (col, val) in enumerate(fields.items(), start=1):
        set_parts.append(f"{col} = ${idx}")
        values.append(val)
    values.extend([app_name, action_id])

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE resource_actions SET {', '.join(set_parts)} "
            f"WHERE app_name = ${len(values)-1} AND id = ${len(values)} "
            f"RETURNING id, action, method, path_suffix, success_status, min_permission",
            *values,
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"resource_action id={action_id} (app={app_name}) not found",
            )
    return ResourceActionResponse(**dict(row))


@router.delete(
    "/{app_name}/resource-actions/{action_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_resource_action(app_name: str, action_id: int):
    """Delete a resource_action row by id (scoped to the app)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM resource_actions WHERE app_name = $1 AND id = $2 RETURNING id",
            app_name, action_id,
        )
        if deleted is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"resource_action id={action_id} (app={app_name}) not found",
            )
    return None
