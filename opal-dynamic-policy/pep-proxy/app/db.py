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

async def create_path_rule(
    path_prefix: str, required_group: str, description: str = "",
    method: str = None,
) -> Dict[str, Any]:
    """Insert a new path rule and return the created row."""
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO path_rules (path_prefix, method, required_group, description)
        VALUES ($1, $2, $3, $4)
        RETURNING id, path_prefix, method, required_group, description,
                  created_at::text AS created_at
        """,
        path_prefix,
        method,
        required_group,
        description,
    )
    return dict(row)


async def list_path_rules() -> List[Dict[str, Any]]:
    """Return all path rules ordered by id."""
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT id, path_prefix, method, required_group, description,
               created_at::text AS created_at
        FROM path_rules
        ORDER BY id
        """
    )
    return [dict(r) for r in rows]


async def get_path_rule(rule_id: int) -> Optional[Dict[str, Any]]:
    """Return a single path rule by id, or None if not found."""
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, path_prefix, method, required_group, description,
               created_at::text AS created_at
        FROM path_rules
        WHERE id = $1
        """,
        rule_id,
    )
    return dict(row) if row else None


async def update_path_rule(
    rule_id: int,
    path_prefix: Optional[str] = None,
    method: Optional[str] = "__unset__",
    required_group: Optional[str] = None,
    description: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Update a path rule by id. Only non-None fields are updated.
    method uses sentinel "__unset__" to distinguish "not provided" from "set to null".

    Returns the updated row, or None if the rule does not exist.
    """
    pool = get_pool()

    # Build SET clause dynamically for non-None fields
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
    if required_group is not None:
        updates.append(f"required_group = ${idx}")
        params.append(required_group)
        idx += 1
    if description is not None:
        updates.append(f"description = ${idx}")
        params.append(description)
        idx += 1

    if not updates:
        # Nothing to update; just return the existing row
        return await get_path_rule(rule_id)

    params.append(rule_id)
    set_clause = ", ".join(updates)
    query = f"""
        UPDATE path_rules
        SET {set_clause}
        WHERE id = ${idx}
        RETURNING id, path_prefix, method, required_group, description,
                  created_at::text AS created_at
    """
    row = await pool.fetchrow(query, *params)
    return dict(row) if row else None


async def delete_path_rule(rule_id: int) -> bool:
    """Delete a path rule by id. Returns True if a row was deleted."""
    pool = get_pool()
    result = await pool.execute("DELETE FROM path_rules WHERE id = $1", rule_id)
    # asyncpg returns "DELETE N" where N is the number of rows deleted
    return result == "DELETE 1"
