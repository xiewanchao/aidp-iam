"""
Database access module for pep-proxy (v2.0).

Manages an asyncpg connection pool and provides:
  - query_acl: prefix-matching ACL lookup for resource-level authorization
  - get_allowed_object_ids: reverse ACL query for List request filtering
  - get_callback_url: look up app callback URL from app_manifests
"""

import logging
import os
from typing import List, Optional

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
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(IAM_DB_URL, min_size=2, max_size=10)
        logger.info("asyncpg pool created: %s", IAM_DB_URL.split("@")[-1])
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("asyncpg pool closed")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialised; call init_pool() first")
    return _pool


# ---------------------------------------------------------------------------
# resource_acl queries (v2.0 path-based)
# ---------------------------------------------------------------------------

async def query_acl(
    tenant_id: str,
    user_path: str,
    group_paths: List[str],
    object_path: str,
) -> Optional[tuple]:
    """
    Prefix-matching ACL query.

    Builds all ancestor prefixes of object_path (from longest to shortest)
    and returns (role_path, matched_object_path) from the longest matching
    ACL entry for any of the subject paths (user + groups).

    Returns None when no ACL entry matches.
    """
    pool = get_pool()
    subjects = [user_path] + group_paths
    parts = object_path.split("/")
    candidates = ["/".join(parts[:i]) for i in range(len(parts), 0, -1)]
    row = await pool.fetchrow(
        """
        SELECT role_path, object_path AS matched_path FROM resource_acl
        WHERE tenant_id = $1
          AND user_path = ANY($2)
          AND object_path = ANY($3)
        ORDER BY LENGTH(object_path) DESC
        LIMIT 1
        """,
        tenant_id, subjects, candidates,
    )
    return (row["role_path"], row["matched_path"]) if row else None


async def get_allowed_object_ids(
    tenant_id: str,
    user_path: str,
    group_paths: List[str],
    type_prefix: str,
    page: int = 1,
    size: int = 200,
) -> tuple:
    """
    Reverse ACL query for List requests.

    Returns (ids, total) where ids is a paginated list of resource IDs
    (last path segment) that the subject may access under type_prefix.

    Only returns direct children (depth = type_prefix depth + 1).
    """
    pool = get_pool()
    subjects = [user_path] + group_paths
    like_pattern = type_prefix + "/%"
    depth = type_prefix.count("/") + 2

    total = await pool.fetchval(
        """
        SELECT COUNT(DISTINCT object_path)
        FROM resource_acl
        WHERE tenant_id = $1
          AND user_path = ANY($2)
          AND object_path LIKE $3
          AND array_length(string_to_array(object_path, '/'), 1) = $4
        """,
        tenant_id, subjects, like_pattern, depth,
    ) or 0

    rows = await pool.fetch(
        """
        SELECT DISTINCT object_path
        FROM resource_acl
        WHERE tenant_id = $1
          AND user_path = ANY($2)
          AND object_path LIKE $3
          AND array_length(string_to_array(object_path, '/'), 1) = $4
        ORDER BY object_path
        LIMIT $5 OFFSET $6
        """,
        tenant_id, subjects, like_pattern, depth,
        size, (page - 1) * size,
    )
    ids = [r["object_path"].split("/")[-1] for r in rows]
    return ids, total


async def resource_acl_exists(tenant_id: str, object_path: str) -> bool:
    """Return True if any ACL entry exists for the exact object_path (any subject)."""
    pool = get_pool()
    val = await pool.fetchval(
        "SELECT 1 FROM resource_acl WHERE tenant_id = $1 AND object_path = $2 LIMIT 1",
        tenant_id, object_path,
    )
    return val is not None


async def get_callback_url(namespace: str) -> Optional[str]:
    """Look up the callback_url for a namespace from app_manifests."""
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT callback_url FROM app_manifests WHERE namespace = $1",
        namespace,
    )
    return row["callback_url"] if row else None


async def get_resource_pattern(resource_prefix: str) -> Optional[dict]:
    """
    Look up a resource_patterns row by resource_prefix.

    resource_prefix is the _collection_prefix output: fixed keywords are
    literal, {tenantId} is already substituted, but deeper ID segments may
    still be actual values (e.g. '/MemoryStore/Tenants/{tenantId}/Instances/inst-001/Memories').

    DB rows store named placeholders like {tenantId}/{instanceName}.  We
    build a regex from each DB row by splitting on {param} tokens, escaping
    the fixed parts, and joining with [^/]+ wildcards.  Longest match wins.

    Returns a dict with id_source, id_field, response_id_field, or None.
    """
    import re as _re
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT resource_prefix, id_source, id_field, response_id_field
        FROM resource_patterns
        ORDER BY LENGTH(resource_prefix) DESC
        """,
    )
    for row in rows:
        parts = _re.split(r"\{[^}]+\}", row["resource_prefix"])
        regex = "^" + "[^/]+".join(_re.escape(p) for p in parts) + "$"
        if _re.match(regex, resource_prefix):
            return {
                "id_source":        row["id_source"],
                "id_field":         row["id_field"],
                "response_id_field": row["response_id_field"],
            }
    return None
