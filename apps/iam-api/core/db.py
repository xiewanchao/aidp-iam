"""
Async connection pool for the IAM PostgreSQL database.
Uses asyncpg for high-performance async queries.
"""

import os
import asyncpg

_pool: asyncpg.Pool | None = None

IAM_DB_URL = os.getenv(
    "IAM_DB_URL",
    "postgresql://keycloak:keycloak@iam-store.keycloak.svc.cluster.local:5432/iam",
)


async def get_pool() -> asyncpg.Pool:
    """Return the shared connection pool, creating it lazily on first call."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=IAM_DB_URL,
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
    return _pool


async def close_pool() -> None:
    """Gracefully close the connection pool on application shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
