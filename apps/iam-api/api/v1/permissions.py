"""
Permission Groups CRUD — full lifecycle for permission_groups +
permission_group_paths + permission_group_bindings.

These three tables together define the PATH-level authorization surface for
every app in the IAM system:

  * permission_groups          — 一个"业务功能点" (e.g. kb_create, rubik_query)
  * permission_group_paths     — 该功能点匹配的 (path_prefix, method) 集合
  * permission_group_bindings  — 该功能点开放给哪些 Keycloak 组

bundle-server JOIN-reads these three tables on each OPA bundle tick, so any
change here reaches OPA within ~10 s without restart.

The aggregated VIEW (`GET /api/v1/{realm}/permissions`) lives in identity.py
and groups permission_groups by app. This module is the low-level CRUD the
UI and offline debugging flows drive against individual rows.
"""

from typing import List, Optional
from fastapi import APIRouter, status, HTTPException, Query

from app.core.db import get_pool
from app.schemas.permissions import (
    PermissionGroupCreate, PermissionGroupUpdate, PermissionGroupResponse,
    PermissionGroupPathIn, PermissionGroupPathResponse,
)

router = APIRouter(prefix="/permission-groups", tags=["PermissionGroups"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _load_paths(conn, group_id: int) -> List[PermissionGroupPathResponse]:
    rows = await conn.fetch(
        "SELECT id, path_prefix, method FROM permission_group_paths "
        "WHERE group_id = $1 ORDER BY path_prefix, method NULLS FIRST",
        group_id,
    )
    return [
        PermissionGroupPathResponse(
            id=r["id"], path_prefix=r["path_prefix"], method=r["method"]
        )
        for r in rows
    ]


async def _load_bindings(conn, group_id: int) -> List[str]:
    rows = await conn.fetch(
        "SELECT kc_group_name FROM permission_group_bindings "
        "WHERE group_id = $1 ORDER BY kc_group_name",
        group_id,
    )
    return [r["kc_group_name"] for r in rows]


async def _insert_paths(conn, group_id: int, paths: List[PermissionGroupPathIn]) -> None:
    for p in paths:
        await conn.execute(
            "INSERT INTO permission_group_paths (group_id, path_prefix, method) "
            "VALUES ($1, $2, $3) ON CONFLICT (group_id, path_prefix, method) DO NOTHING",
            group_id, p.path_prefix, p.method,
        )


async def _insert_bindings(conn, group_id: int, kc_groups: List[str]) -> None:
    for g in kc_groups:
        await conn.execute(
            "INSERT INTO permission_group_bindings (group_id, kc_group_name) "
            "VALUES ($1, $2) ON CONFLICT DO NOTHING",
            group_id, g,
        )


async def _to_response(conn, row) -> PermissionGroupResponse:
    return PermissionGroupResponse(
        id=row["id"],
        app_name=row["app_name"],
        name=row["name"],
        description=row["description"],
        created_at=row["created_at"],
        paths=await _load_paths(conn, row["id"]),
        bindings=await _load_bindings(conn, row["id"]),
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=PermissionGroupResponse,
)
async def create_permission_group(payload: PermissionGroupCreate):
    """Create a permission_group with nested paths + bindings in one transaction."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                row = await conn.fetchrow(
                    "INSERT INTO permission_groups (app_name, name, description) "
                    "VALUES ($1, $2, $3) "
                    "RETURNING id, app_name, name, description, created_at",
                    payload.app_name, payload.name, payload.description,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"permission_group conflict: {exc}",
                )
            await _insert_paths(conn, row["id"], payload.paths)
            await _insert_bindings(conn, row["id"], payload.bindings)
        return await _to_response(conn, row)


@router.get("", response_model=List[PermissionGroupResponse])
async def list_permission_groups(
    app_name: Optional[str] = Query(
        None,
        description="Filter by app_name. Use empty string '' to filter for cross-app rules.",
    ),
):
    """List all permission_groups with expanded paths + bindings.

    Different from `GET /api/v1/{realm}/permissions` — that one groups by
    app for UI display. This one is flat and app_name-filterable for tooling.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if app_name is None:
            rows = await conn.fetch(
                "SELECT id, app_name, name, description, created_at "
                "FROM permission_groups ORDER BY app_name NULLS FIRST, name"
            )
        else:
            rows = await conn.fetch(
                "SELECT id, app_name, name, description, created_at "
                "FROM permission_groups WHERE app_name = $1 ORDER BY name",
                app_name,
            )
        return [await _to_response(conn, r) for r in rows]


@router.get("/{group_id}", response_model=PermissionGroupResponse)
async def get_permission_group(group_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, app_name, name, description, created_at "
            "FROM permission_groups WHERE id = $1",
            group_id,
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"permission_group id={group_id} not found",
            )
        return await _to_response(conn, row)


@router.put("/{group_id}", response_model=PermissionGroupResponse)
async def update_permission_group(group_id: int, payload: PermissionGroupUpdate):
    """Update a permission_group.

    Semantics:
      - Top-level fields (`app_name`, `name`, `description`) are patched
        individually (None = no change).
      - `paths` and `bindings` are REPLACE-ALL when set: the new list wipes
        and re-writes the corresponding rows atomically. `[]` clears.
        `None` leaves existing rows untouched.
    """
    core_fields = {
        k: v for k, v in {
            "app_name": payload.app_name,
            "name": payload.name,
            "description": payload.description,
        }.items() if v is not None
    }

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            exists = await conn.fetchval(
                "SELECT 1 FROM permission_groups WHERE id = $1", group_id
            )
            if not exists:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"permission_group id={group_id} not found",
                )

            if core_fields:
                set_parts, values = [], []
                for idx, (col, val) in enumerate(core_fields.items(), start=1):
                    set_parts.append(f"{col} = ${idx}")
                    values.append(val)
                values.append(group_id)
                try:
                    await conn.execute(
                        f"UPDATE permission_groups SET {', '.join(set_parts)} "
                        f"WHERE id = ${len(values)}",
                        *values,
                    )
                except Exception as exc:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"permission_group update conflict: {exc}",
                    )

            if payload.paths is not None:
                await conn.execute(
                    "DELETE FROM permission_group_paths WHERE group_id = $1",
                    group_id,
                )
                await _insert_paths(conn, group_id, payload.paths)

            if payload.bindings is not None:
                await conn.execute(
                    "DELETE FROM permission_group_bindings WHERE group_id = $1",
                    group_id,
                )
                await _insert_bindings(conn, group_id, payload.bindings)

            row = await conn.fetchrow(
                "SELECT id, app_name, name, description, created_at "
                "FROM permission_groups WHERE id = $1",
                group_id,
            )
        return await _to_response(conn, row)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_permission_group(group_id: int):
    """Delete a permission_group. Cascades to paths + bindings (FK ON DELETE CASCADE)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM permission_groups WHERE id = $1 RETURNING id", group_id
        )
        if deleted is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"permission_group id={group_id} not found",
            )
    return None


# ---------------------------------------------------------------------------
# Sub-resource CRUD: paths & bindings as individual rows
# (cheaper for UI "add one" / "remove one" flows than full PUT replace)
# ---------------------------------------------------------------------------

@router.post(
    "/{group_id}/paths",
    status_code=status.HTTP_201_CREATED,
    response_model=PermissionGroupPathResponse,
)
async def add_permission_group_path(group_id: int, payload: PermissionGroupPathIn):
    """Add a single (path_prefix, method) row to a permission_group."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM permission_groups WHERE id = $1", group_id
        )
        if not exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"permission_group id={group_id} not found",
            )
        try:
            row = await conn.fetchrow(
                "INSERT INTO permission_group_paths (group_id, path_prefix, method) "
                "VALUES ($1, $2, $3) RETURNING id, path_prefix, method",
                group_id, payload.path_prefix, payload.method,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"path conflict: {exc}",
            )
    return PermissionGroupPathResponse(
        id=row["id"], path_prefix=row["path_prefix"], method=row["method"]
    )


@router.delete(
    "/{group_id}/paths/{path_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_permission_group_path(group_id: int, path_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM permission_group_paths "
            "WHERE id = $1 AND group_id = $2 RETURNING id",
            path_id, group_id,
        )
        if deleted is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"path id={path_id} (in group {group_id}) not found",
            )
    return None


@router.post(
    "/{group_id}/bindings/{kc_group_name}",
    status_code=status.HTTP_201_CREATED,
)
async def add_permission_group_binding(group_id: int, kc_group_name: str):
    """Grant a permission_group to a Keycloak group (by name)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM permission_groups WHERE id = $1", group_id
        )
        if not exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"permission_group id={group_id} not found",
            )
        await conn.execute(
            "INSERT INTO permission_group_bindings (group_id, kc_group_name) "
            "VALUES ($1, $2) ON CONFLICT DO NOTHING",
            group_id, kc_group_name,
        )
    return {"group_id": group_id, "kc_group_name": kc_group_name}


@router.delete(
    "/{group_id}/bindings/{kc_group_name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_permission_group_binding(group_id: int, kc_group_name: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM permission_group_bindings "
            "WHERE group_id = $1 AND kc_group_name = $2 "
            "RETURNING kc_group_name",
            group_id, kc_group_name,
        )
        if deleted is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"binding (group {group_id} → {kc_group_name}) not found",
            )
    return None
