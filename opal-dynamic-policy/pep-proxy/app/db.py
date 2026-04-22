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
        app_name, resource_prefix, method ('' for fallback / specific verb),
        resource_type,
        id_source       ('path' / 'query' / 'body'; default 'path'),
        id_field        (e.g. 'id', 'kb_id', 'data.kb_id'; default 'id'),
        id_query_param  (query parameter name; default None),
        share_to_admin_group_on_create  (bool, default false),
        share_to_all_users_on_create    (bool, default false).
    """
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT app_name, resource_prefix, method, resource_type,
               id_source, id_field, id_query_param,
               share_to_admin_group_on_create, share_to_all_users_on_create
        FROM resource_patterns
        """
    )
    result = [
        {
            "app_name": row["app_name"],
            "resource_prefix": row["resource_prefix"],
            "method": (row["method"] or "").upper(),
            "resource_type": row["resource_type"],
            "id_source": row["id_source"] or "path",
            "id_field": row["id_field"] or "id",
            "id_query_param": row["id_query_param"],
            "share_to_admin_group_on_create": bool(row["share_to_admin_group_on_create"]),
            "share_to_all_users_on_create": bool(row["share_to_all_users_on_create"]),
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
# Path-rules CRUD removed: tables path_rules/path_rule_groups superseded by
# permission_groups + permission_group_paths + permission_group_bindings.
# bundle-server is the read path into OPA; admin CRUD (if/when needed) lives
# on keycloak-proxy under /api/v1/{realm}/permission-groups.
# ---------------------------------------------------------------------------
