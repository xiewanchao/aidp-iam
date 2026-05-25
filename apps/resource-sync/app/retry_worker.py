"""
Background asyncio task that periodically retries failed ACL writes
stored in the pending_acl table.
"""

import asyncio
import logging

from . import db

logger = logging.getLogger(__name__)

RETRY_INTERVAL_SECONDS = 10


async def run_retry_loop() -> None:
    """
    Run forever, processing pending_acl items every RETRY_INTERVAL_SECONDS.

    This coroutine is intended to be wrapped in asyncio.create_task() from
    the FastAPI startup event.
    """
    logger.info(
        "pending_acl retry worker started (interval=%ds)",
        RETRY_INTERVAL_SECONDS,
    )
    while True:
        try:
            processed = await db.get_and_process_pending_acls()
            if processed > 0:
                logger.info("retry worker processed %d pending_acl items", processed)
        except Exception as exc:
            logger.error("retry worker unexpected error: %s", exc, exc_info=True)

        await asyncio.sleep(RETRY_INTERVAL_SECONDS)
