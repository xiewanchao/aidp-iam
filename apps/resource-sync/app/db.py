"""
Async connection pool and query functions for the IAM PostgreSQL database.
Uses asyncpg for high-performance async queries against resource_acl and
pending_acl tables.

New schema (v2.0): resource_acl stores (user_path, object_path, role_path)
as full path strings. Queries use prefix matching for ACL inheritance.
"""

import logging
import os
from datetime import datetime, timedelta

import asyncpg

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

IAM_DB_URL = os.getenv(
    "IAM_DB_URL",
    "postgresql://keycloak:keycloak@iam-store.keycloak.svc.cluster.local:5432/iam",
)


# ---------------------------------------------------------------------------
# Pool lifecycle
# ---------------------------------------------------------------------------

async def init_pool() -> None:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=IAM_DB_URL,
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
        logger.info("asyncpg pool initialised (min=2, max=10)")


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("asyncpg pool closed")


def _get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialised — call init_pool() first")
    return _pool


# ---------------------------------------------------------------------------
# resource_acl CRUD (v2.0 path-based)
# ---------------------------------------------------------------------------

async def write_acl_entry(
    tenant_id: str,
    user_path: str,
    object_path: str,
    role_path: str,
    created_by: str | None = None,
) -> bool:
    """
    Insert a new ACL entry. Uses ON CONFLICT DO NOTHING to avoid duplicates.
    Returns True if a row was inserted, False if it already existed.
    """
    pool = _get_pool()
    result = await pool.execute(
        """
        INSERT INTO resource_acl (tenant_id, user_path, object_path, role_path, created_by)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (tenant_id, user_path, object_path) DO NOTHING
        """,
        tenant_id, user_path, object_path, role_path, created_by,
    )
    return result.endswith("1")


async def delete_acl_by_prefix(object_path_prefix: str) -> int:
    """
    Delete ALL ACL entries whose object_path equals the prefix or starts
    with prefix + '/'. This cascades to all sub-resources.

    Returns the number of rows deleted.
    """
    pool = _get_pool()
    result = await pool.execute(
        """
        DELETE FROM resource_acl
        WHERE object_path = $1
           OR object_path LIKE $2
        """,
        object_path_prefix,
        object_path_prefix + "/%",
    )
    count = int(result.split()[-1])
    if count:
        logger.info("Deleted %d ACL entries for prefix %s", count, object_path_prefix)
    return count


async def get_allowed_ids(
    tenant_id: str,
    user_path: str,
    groups: list[str],
    type_prefix: str,
    page: int = 1,
    size: int = 200,
) -> tuple[list[str], int]:
    """
    Reverse ACL query: given a user + groups, return paginated list of
    resource IDs (last path segment) that the subject may access under
    type_prefix (e.g. 'MemoryStore/Tenants/t-001/MemoryStores').

    Only returns direct children (depth = type_prefix depth + 1), not
    deeper sub-resources.
    """
    pool = _get_pool()
    subjects = [user_path] + groups
    like_pattern = type_prefix + "/%"
    depth = type_prefix.count("/") + 2

    total: int = await pool.fetchval(
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


async def list_acl_by_object(tenant_id: str, object_path: str) -> list[dict]:
    """Return all ACL entries for a specific object path (exact match)."""
    pool = _get_pool()
    rows = await pool.fetch(
        """
        SELECT id, tenant_id, user_path, object_path, role_path, created_at, created_by
        FROM resource_acl
        WHERE tenant_id = $1 AND object_path = $2
        ORDER BY created_at
        """,
        tenant_id, object_path,
    )
    return [dict(r) for r in rows]


async def list_acl_by_user(tenant_id: str, user_path: str) -> list[dict]:
    """Return all ACL entries for a specific user/group path."""
    pool = _get_pool()
    rows = await pool.fetch(
        """
        SELECT id, tenant_id, user_path, object_path, role_path, created_at, created_by
        FROM resource_acl
        WHERE tenant_id = $1 AND user_path = $2
        ORDER BY object_path
        """,
        tenant_id, user_path,
    )
    return [dict(r) for r in rows]


async def delete_acl_entry(tenant_id: str, user_path: str, object_path: str) -> bool:
    """Delete a single ACL entry by (tenant_id, user_path, object_path)."""
    pool = _get_pool()
    result = await pool.execute(
        "DELETE FROM resource_acl WHERE tenant_id=$1 AND user_path=$2 AND object_path=$3",
        tenant_id, user_path, object_path,
    )
    return result.endswith("1")


async def query_acl(
    tenant_id: str,
    user_path: str,
    group_paths: list[str],
    object_path: str,
) -> str | None:
    """
    Prefix-matching ACL query. Builds all ancestor prefixes of object_path
    and returns the role_path from the longest matching ACL entry.

    Used by pep-proxy for resource-level authorization checks.
    """
    pool = _get_pool()
    subjects = [user_path] + group_paths
    parts = object_path.split("/")
    candidates = ["/".join(parts[:i]) for i in range(len(parts), 0, -1)]
    row = await pool.fetchrow(
        """
        SELECT role_path FROM resource_acl
        WHERE tenant_id = $1
          AND user_path = ANY($2)
          AND object_path = ANY($3)
        ORDER BY LENGTH(object_path) DESC
        LIMIT 1
        """,
        tenant_id, subjects, candidates,
    )
    return row["role_path"] if row else None


# ---------------------------------------------------------------------------
# pending_acl retry queue
# ---------------------------------------------------------------------------

async def write_pending(
    action: str,
    object_path: str,
    tenant_id: str | None = None,
    user_path: str | None = None,
    role_path: str | None = None,
    created_by: str | None = None,
    error: str = "",
) -> None:
    """
    Insert a row into pending_acl for later retry.

    action: 'write' | 'delete_prefix'
    """
    pool = _get_pool()
    await pool.execute(
        """
        INSERT INTO pending_acl
            (action, tenant_id, user_path, object_path, role_path, created_by, last_error)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        action, tenant_id, user_path, object_path, role_path, created_by, error,
    )
    logger.warning(
        "Queued pending_acl: action=%s object=%s error=%s",
        action, object_path, error,
    )


async def get_list_filter_mode(resource_prefix: str) -> str:
    """Return list_filter_mode for a resource_prefix, defaulting to gateway_inject."""
    pool = _get_pool()
    row = await pool.fetchrow(
        "SELECT list_filter_mode FROM resource_patterns WHERE resource_prefix = $1 LIMIT 1",
        resource_prefix,
    )
    return row["list_filter_mode"] if row else "gateway_inject"


async def get_resource_pattern(resource_prefix: str) -> dict | None:
    """
    Look up a resource_patterns row by resource_prefix.

    resource_prefix uses the manifest template form, e.g.
    '/DataAgent/Tenants/{tenantId}/DataAgentDBs'.

    Returns a dict with id_source, id_field, response_id_field (may be None),
    or None when no matching pattern is registered.
    """
    pool = _get_pool()
    row = await pool.fetchrow(
        """
        SELECT id_source, id_field, response_id_field, on_create_acl
        FROM resource_patterns
        WHERE resource_prefix = $1
        ORDER BY method DESC
        LIMIT 1
        """,
        resource_prefix,
    )
    if row is None:
        return None
    result = dict(row)
    # asyncpg may return JSONB as a string on older drivers; normalise to list
    raw = result.get("on_create_acl")
    if isinstance(raw, str):
        import json as _json
        result["on_create_acl"] = _json.loads(raw)
    elif raw is None:
        result["on_create_acl"] = []
    return result


async def get_and_process_pending_acls() -> int:
    """
    Fetch retryable pending_acl rows (next_retry <= now AND retry_count < max_retries),
    attempt the action, and either delete on success or apply exponential backoff on failure.

    Returns the number of items processed.
    """
    pool = _get_pool()
    rows = await pool.fetch(
        """
        SELECT id, action, tenant_id, user_path, object_path, role_path,
               created_by, retry_count
        FROM pending_acl
        WHERE next_retry <= NOW()
          AND retry_count < max_retries
        ORDER BY next_retry
        LIMIT 50
        """
    )

    processed = 0
    for row in rows:
        pid = row["id"]
        action = row["action"]
        try:
            if action == "write":
                await write_acl_entry(
                    row["tenant_id"], row["user_path"], row["object_path"],
                    row["role_path"], row["created_by"],
                )
            elif action == "delete_prefix":
                await delete_acl_by_prefix(row["object_path"])

            await pool.execute("DELETE FROM pending_acl WHERE id = $1", pid)
            logger.info("pending_acl id=%s action=%s succeeded", pid, action)

        except Exception as exc:
            new_count = row["retry_count"] + 1
            # Exponential backoff: 5s → 10s → 20s → ... → 2560s
            backoff = min(5 * (2 ** (new_count - 1)), 2560)
            next_retry = datetime.utcnow() + timedelta(seconds=backoff)
            await pool.execute(
                """
                UPDATE pending_acl
                SET retry_count=$1, last_error=$2, next_retry=$3
                WHERE id=$4
                """,
                new_count, str(exc), next_retry, pid,
            )
            logger.warning(
                "pending_acl id=%s action=%s failed (attempt %d): %s",
                pid, action, new_count, exc,
            )

        processed += 1

    return processed
