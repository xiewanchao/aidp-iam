"""
Async connection pool and query functions for the IAM PostgreSQL database.
Uses asyncpg for high-performance async queries against resource_acl and
pending_acl tables.
"""

import logging
import math
import os
from datetime import datetime, timedelta

import asyncpg

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

IAM_DB_URL = os.getenv(
    "IAM_DB_URL",
    "postgresql://keycloak:keycloak@postgres.keycloak.svc.cluster.local:5432/iam",
)


# ---------------------------------------------------------------------------
# Pool lifecycle
# ---------------------------------------------------------------------------

async def init_pool() -> None:
    """Create the shared asyncpg connection pool."""
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
    """Gracefully close the connection pool on application shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("asyncpg pool closed")


def _get_pool() -> asyncpg.Pool:
    """Return the pool or raise if not initialised."""
    if _pool is None:
        raise RuntimeError("Database pool not initialised — call init_pool() first")
    return _pool


# ---------------------------------------------------------------------------
# Startup loaders (cached in memory by main.py / ext_proc_server.py)
# ---------------------------------------------------------------------------

async def load_resource_patterns() -> list[dict]:
    """
    Load all rows from resource_patterns.
    Returns list of {app_name, resource_prefix, resource_type}.
    """
    pool = _get_pool()
    rows = await pool.fetch(
        "SELECT app_name, resource_prefix, resource_type FROM resource_patterns"
    )
    return [dict(r) for r in rows]


async def load_apps() -> dict[str, dict]:
    """
    Load all rows from apps.
    Returns dict keyed by app_name -> {path_prefix, enabled}.
    """
    pool = _get_pool()
    rows = await pool.fetch("SELECT app_name, path_prefix, enabled FROM apps")
    return {
        r["app_name"]: {"path_prefix": r["path_prefix"], "enabled": r["enabled"]}
        for r in rows
    }


# ---------------------------------------------------------------------------
# resource_acl CRUD
# ---------------------------------------------------------------------------

async def write_acl(
    tenant_id: str,
    app_name: str,
    resource_type: str,
    resource_id: str,
    subject_type: str,
    subject_id: str,
    permission: str,
) -> bool:
    """
    Insert a new ACL entry.  Uses ON CONFLICT to avoid duplicates.
    Returns True if a row was inserted, False if it already existed.
    """
    pool = _get_pool()
    result = await pool.execute(
        """
        INSERT INTO resource_acl
            (tenant_id, app_name, resource_type, resource_id,
             subject_type, subject_id, permission)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (tenant_id, app_name, resource_type, resource_id,
                     subject_type, subject_id)
        DO NOTHING
        """,
        tenant_id, app_name, resource_type, resource_id,
        subject_type, subject_id, permission,
    )
    # asyncpg returns e.g. "INSERT 0 1" or "INSERT 0 0"
    return result.endswith("1")


async def delete_acl_for_resource(
    app_name: str,
    resource_type: str,
    resource_id: str,
) -> bool:
    """
    Delete ALL ACL entries for a given resource (cross-tenant cleanup).
    Uses idx_acl_cleanup index.
    Returns True if any rows were deleted.
    """
    pool = _get_pool()
    result = await pool.execute(
        """
        DELETE FROM resource_acl
        WHERE app_name = $1
          AND resource_type = $2
          AND resource_id = $3
        """,
        app_name, resource_type, resource_id,
    )
    # e.g. "DELETE 3"
    count = int(result.split()[-1])
    return count > 0


async def get_allowed_resource_ids(
    tenant_id: str,
    app_name: str,
    resource_type: str,
    subject_id: str,
    groups: list[str],
    page: int = 1,
    size: int = 20,
) -> tuple[list[str], int]:
    """
    Return paginated list of resource_ids that the subject (user or any of
    their groups) may access, plus the total count.

    Uses idx_acl_subject index.
    """
    pool = _get_pool()

    # Build subject list: the user themselves + all their groups
    subject_clauses = [f"(subject_type = 'user' AND subject_id = $4)"]
    params: list = [tenant_id, app_name, resource_type, subject_id]
    if groups:
        idx = len(params) + 1
        subject_clauses.append(
            f"(subject_type = 'group' AND subject_id = ANY(${idx}::text[]))"
        )
        params.append(groups)

    subject_filter = " OR ".join(subject_clauses)

    count_sql = f"""
        SELECT COUNT(DISTINCT resource_id)
        FROM resource_acl
        WHERE tenant_id = $1
          AND app_name = $2
          AND resource_type = $3
          AND ({subject_filter})
    """
    total = await pool.fetchval(count_sql, *params)

    offset = (page - 1) * size
    # Append LIMIT and OFFSET params
    limit_idx = len(params) + 1
    offset_idx = len(params) + 2
    params.extend([size, offset])

    data_sql = f"""
        SELECT DISTINCT resource_id
        FROM resource_acl
        WHERE tenant_id = $1
          AND app_name = $2
          AND resource_type = $3
          AND ({subject_filter})
        ORDER BY resource_id
        LIMIT ${limit_idx} OFFSET ${offset_idx}
    """
    rows = await pool.fetch(data_sql, *params)
    ids = [r["resource_id"] for r in rows]
    return ids, total or 0


async def list_permissions(
    tenant_id: str,
    app_name: str,
    resource_type: str,
    resource_id: str,
) -> list[dict]:
    """Return all ACL entries for a specific resource."""
    pool = _get_pool()
    rows = await pool.fetch(
        """
        SELECT id, tenant_id, app_name, resource_type, resource_id,
               subject_type, subject_id, permission, created_at
        FROM resource_acl
        WHERE tenant_id = $1
          AND app_name = $2
          AND resource_type = $3
          AND resource_id = $4
        ORDER BY created_at
        """,
        tenant_id, app_name, resource_type, resource_id,
    )
    return [dict(r) for r in rows]


async def add_permission(
    tenant_id: str,
    app_name: str,
    resource_type: str,
    resource_id: str,
    subject_type: str,
    subject_id: str,
    permission: str,
) -> dict:
    """
    Insert a new ACL entry and return the created row.
    Raises asyncpg.UniqueViolationError on duplicate.
    """
    pool = _get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO resource_acl
            (tenant_id, app_name, resource_type, resource_id,
             subject_type, subject_id, permission)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id, tenant_id, app_name, resource_type, resource_id,
                  subject_type, subject_id, permission, created_at
        """,
        tenant_id, app_name, resource_type, resource_id,
        subject_type, subject_id, permission,
    )
    return dict(row)


async def update_permission(acl_id: int, permission: str) -> bool:
    """Update the permission level of an existing ACL entry."""
    pool = _get_pool()
    result = await pool.execute(
        "UPDATE resource_acl SET permission = $1 WHERE id = $2",
        permission, acl_id,
    )
    return result.endswith("1")


async def delete_permission(acl_id: int) -> bool:
    """Delete a single ACL entry by id."""
    pool = _get_pool()
    result = await pool.execute(
        "DELETE FROM resource_acl WHERE id = $1",
        acl_id,
    )
    return result.endswith("1")


# ---------------------------------------------------------------------------
# pending_acl
# ---------------------------------------------------------------------------

async def write_pending_acl(
    tenant_id: str,
    app_name: str,
    resource_type: str,
    resource_id: str,
    subject_type: str,
    subject_id: str,
    permission: str,
    action: str,
    error: str,
) -> None:
    """Insert a row into pending_acl for later retry."""
    pool = _get_pool()
    await pool.execute(
        """
        INSERT INTO pending_acl
            (tenant_id, app_name, resource_type, resource_id,
             subject_type, subject_id, permission, action, last_error)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
        tenant_id, app_name, resource_type, resource_id,
        subject_type, subject_id, permission, action, error,
    )
    logger.warning(
        "Queued pending_acl: action=%s app=%s type=%s id=%s error=%s",
        action, app_name, resource_type, resource_id, error,
    )


async def get_and_process_pending_acls() -> int:
    """
    Fetch all retryable pending_acl rows (next_retry <= now AND
    retry_count < max_retries), attempt the action, and either delete
    on success or increment retry_count + backoff on failure.

    Returns the number of items processed (success + failure).
    """
    pool = _get_pool()
    rows = await pool.fetch(
        """
        SELECT id, tenant_id, app_name, resource_type, resource_id,
               subject_type, subject_id, permission, action, retry_count
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
            if action == "create":
                await write_acl(
                    row["tenant_id"], row["app_name"], row["resource_type"],
                    row["resource_id"], row["subject_type"], row["subject_id"],
                    row["permission"],
                )
            elif action == "delete":
                await delete_acl_for_resource(
                    row["app_name"], row["resource_type"], row["resource_id"],
                )

            # Success — remove from queue
            await pool.execute("DELETE FROM pending_acl WHERE id = $1", pid)
            logger.info("pending_acl id=%s action=%s succeeded, removed", pid, action)

        except Exception as exc:
            # Failure — increment retry count with exponential backoff
            new_count = row["retry_count"] + 1
            backoff_seconds = min(60 * (2 ** new_count), 3600)  # cap at 1 hour
            next_retry = datetime.utcnow() + timedelta(seconds=backoff_seconds)
            await pool.execute(
                """
                UPDATE pending_acl
                SET retry_count = $1, last_error = $2, next_retry = $3
                WHERE id = $4
                """,
                new_count, str(exc), next_retry, pid,
            )
            logger.warning(
                "pending_acl id=%s action=%s failed (attempt %d): %s",
                pid, action, new_count, exc,
            )

        processed += 1

    return processed
