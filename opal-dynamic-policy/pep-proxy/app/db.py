# app/db.py
#
# Database access module for pep-proxy.
#
# Manages an asyncpg connection pool to the IAM PostgreSQL database and
# provides helpers for:
#   - Loading the apps table (path_prefix -> app_name) into memory
#   - Loading resource_patterns for resource-level auth
#   - Querying resource_acl for permission checks
#   - Path-rules CRUD operations
#
import os
import logging
from typing import Dict, List, Optional, Any

import asyncpg

logger = logging.getLogger(__name__)

IAM_DB_URL = os.getenv(
    "IAM_DB_URL",
    "postgresql://keycloak:keycloak@postgres.keycloak.svc.cluster.local:5432/iam",
)

_pool: Optional[asyncpg.Pool] = None


# ---------------------------------------------------------------------------
# Pool lifecycle
# ---------------------------------------------------------------------------

async def init_pool() -> asyncpg.Pool:
    """Create and return the asyncpg connection pool."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(IAM_DB_URL, min_size=2, max_size=10)
        logger.info("asyncpg pool created: %s", IAM_DB_URL.split("@")[-1])
    return _pool


async def close_pool() -> None:
    """Gracefully close the connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("asyncpg pool closed")


def get_pool() -> asyncpg.Pool:
    """Return the current pool (must call init_pool first)."""
    if _pool is None:
        raise RuntimeError("Database pool not initialised; call init_pool() first")
    return _pool


# ---------------------------------------------------------------------------
# Startup data loaders
# ---------------------------------------------------------------------------

async def load_apps() -> Dict[str, str]:
    """
    Load the apps table into a dict: path_prefix -> app_name.

    Used at startup and can be refreshed periodically.
    """
    pool = get_pool()
    rows = await pool.fetch("SELECT app_name, path_prefix FROM apps")
    result: Dict[str, str] = {}
    for row in rows:
        result[row["path_prefix"]] = row["app_name"]
    logger.info("Loaded %d apps from database", len(result))
    return result


async def load_resource_patterns() -> List[Dict[str, Any]]:
    """
    Load resource_patterns table into a list of dicts.

    Each dict contains:
        app_name, resource_prefix, resource_type,
        id_source       ('path' / 'query' / 'body'; default 'path'),
        id_field        (e.g. 'id', 'kb_id', 'data.kb_id'; default 'id'),
        id_query_param  (query parameter name; default None).
    """
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT app_name, resource_prefix, resource_type,
               id_source, id_field, id_query_param
        FROM resource_patterns
        """
    )
    result = [
        {
            "app_name": row["app_name"],
            "resource_prefix": row["resource_prefix"],
            "resource_type": row["resource_type"],
            "id_source": row["id_source"] or "path",
            "id_field": row["id_field"] or "id",
            "id_query_param": row["id_query_param"],
        }
        for row in rows
    ]
    logger.info("Loaded %d resource patterns from database", len(result))
    return result


async def load_resource_actions() -> List[Dict[str, Any]]:
    """
    Load resource_actions table into a list of dicts.

    Each dict contains:
        id, app_name, resource_prefix, action, method,
        path_suffix, success_status, min_permission.

    These rows describe per-API overrides for the default RESTful mapping
    (POST=create, GET=list/read, PUT/PATCH=update, DELETE=delete). When no
    row matches a request, pep-proxy falls back to DEFAULT_ACTIONS in
    main.py.
    """
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT id, app_name, resource_prefix, action, method,
               path_suffix, success_status, min_permission
        FROM resource_actions
        """
    )
    result = [
        {
            "id": row["id"],
            "app_name": row["app_name"],
            "resource_prefix": row["resource_prefix"],
            "action": row["action"],
            "method": (row["method"] or "").upper(),
            "path_suffix": row["path_suffix"],
            "success_status": row["success_status"],
            "min_permission": row["min_permission"] or "none",
        }
        for row in rows
    ]
    logger.info("Loaded %d resource actions from database", len(result))
    return result


# ---------------------------------------------------------------------------
# Resource ACL query
# ---------------------------------------------------------------------------

async def query_resource_acl(
    tenant_id: str,
    app_name: str,
    resource_type: str,
    resource_id: str,
    user_id: str,
    groups: List[str],
) -> Optional[str]:
    """
    Query resource_acl for the highest permission granted to a user or their groups.

    Returns the permission string ('owner', 'contributor', 'viewer') or None
    if no matching ACL entry exists.

    Permission priority: owner (3) > contributor (2) > viewer (1).
    """
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT permission FROM resource_acl
        WHERE tenant_id=$1 AND app_name=$2 AND resource_type=$3 AND resource_id=$4
          AND ((subject_type='user' AND subject_id=$5)
               OR (subject_type='group' AND subject_id = ANY($6)))
        ORDER BY CASE permission
            WHEN 'owner' THEN 3
            WHEN 'contributor' THEN 2
            WHEN 'viewer' THEN 1
        END DESC
        LIMIT 1
        """,
        tenant_id,
        app_name,
        resource_type,
        resource_id,
        user_id,
        groups,
    )
    return row["permission"] if row else None


# ---------------------------------------------------------------------------
# Path-rules CRUD
# ---------------------------------------------------------------------------

# path_rules 主表只描述 (path_prefix, method, description)；绑定的 groups
# 都在 path_rule_groups 关联表里。下列 CRUD 把两张表当成一个聚合对象处理。
# required_group 主表字段保留仅为兼容 NOT NULL 约束（写入 required_groups[0]），
# 所有读路径都从 path_rule_groups JOIN + array_agg 聚合。

_RULE_SELECT = """
    SELECT pr.id, pr.path_prefix, pr.method, pr.description,
           pr.created_at::text AS created_at,
           COALESCE(
               array_agg(prg.group_name ORDER BY prg.group_name)
               FILTER (WHERE prg.group_name IS NOT NULL),
               ARRAY[]::VARCHAR[]
           ) AS required_groups
    FROM path_rules pr
    LEFT JOIN path_rule_groups prg ON pr.id = prg.rule_id
"""


async def create_path_rule(
    path_prefix: str,
    required_groups: List[str],
    description: str = "",
    method: str = None,
) -> Dict[str, Any]:
    """Insert a new path rule + its group bindings atomically and return the created row."""
    if not required_groups:
        raise ValueError("required_groups must be non-empty")

    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO path_rules (path_prefix, method, required_group, description)
                VALUES ($1, $2, $3, $4)
                RETURNING id
                """,
                path_prefix,
                method,
                required_groups[0],
                description,
            )
            rule_id = row["id"]
            await conn.executemany(
                """
                INSERT INTO path_rule_groups (rule_id, group_name)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                """,
                [(rule_id, g) for g in required_groups],
            )
    return await get_path_rule(rule_id)


async def list_path_rules() -> List[Dict[str, Any]]:
    """Return all path rules ordered by id, each with its bound groups."""
    pool = get_pool()
    rows = await pool.fetch(
        _RULE_SELECT + """
        GROUP BY pr.id, pr.path_prefix, pr.method, pr.description, pr.created_at
        ORDER BY pr.id
        """
    )
    return [dict(r) for r in rows]


async def get_path_rule(rule_id: int) -> Optional[Dict[str, Any]]:
    """Return a single path rule (with bound groups), or None if not found."""
    pool = get_pool()
    row = await pool.fetchrow(
        _RULE_SELECT + """
        WHERE pr.id = $1
        GROUP BY pr.id, pr.path_prefix, pr.method, pr.description, pr.created_at
        """,
        rule_id,
    )
    return dict(row) if row else None


async def update_path_rule(
    rule_id: int,
    path_prefix: Optional[str] = None,
    method: Optional[str] = "__unset__",
    required_groups: Optional[List[str]] = None,
    description: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Update a path rule by id. Only non-None fields are updated.
    method uses sentinel "__unset__" to distinguish "not provided" from "set to null".
    required_groups, if provided, fully replaces the bound groups.

    Returns the updated row (with bound groups), or None if the rule does not exist.
    """
    pool = get_pool()

    updates: List[str] = []
    params: List[Any] = []
    idx = 1

    if path_prefix is not None:
        updates.append(f"path_prefix = ${idx}")
        params.append(path_prefix)
        idx += 1
    if method != "__unset__":
        updates.append(f"method = ${idx}")
        params.append(method)
        idx += 1
    if required_groups is not None:
        # Keep the vestigial required_group column in sync with groups[0] to
        # satisfy NOT NULL and for any legacy readers.
        updates.append(f"required_group = ${idx}")
        params.append(required_groups[0])
        idx += 1
    if description is not None:
        updates.append(f"description = ${idx}")
        params.append(description)
        idx += 1

    async with pool.acquire() as conn:
        async with conn.transaction():
            if updates:
                params.append(rule_id)
                set_clause = ", ".join(updates)
                exists = await conn.fetchrow(
                    f"UPDATE path_rules SET {set_clause} WHERE id = ${idx} RETURNING id",
                    *params,
                )
                if exists is None:
                    return None
            else:
                exists = await conn.fetchrow("SELECT id FROM path_rules WHERE id = $1", rule_id)
                if exists is None:
                    return None

            if required_groups is not None:
                await conn.execute("DELETE FROM path_rule_groups WHERE rule_id = $1", rule_id)
                await conn.executemany(
                    """
                    INSERT INTO path_rule_groups (rule_id, group_name)
                    VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                    """,
                    [(rule_id, g) for g in required_groups],
                )

    return await get_path_rule(rule_id)


async def delete_path_rule(rule_id: int) -> bool:
    """Delete a path rule by id. path_rule_groups rows cascade via FK."""
    pool = get_pool()
    result = await pool.execute("DELETE FROM path_rules WHERE id = $1", rule_id)
    return result == "DELETE 1"
