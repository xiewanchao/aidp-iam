"""
resource-sync FastAPI application on port 8080.

Startup sequence:
  1. Initialise asyncpg connection pool.
  2. Start the pending_acl retry worker as a background task.
  3. Start the gRPC ext_proc server on port 8082 as a background task.

ACL management API has moved to da-idb-proxy (AccessManager namespace).
This service only exposes /health and the gRPC ext_proc endpoint.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import db
from . import ext_proc_server
from . import retry_worker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_grpc_task: asyncio.Task | None = None
_retry_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(application: FastAPI):
    global _grpc_task, _retry_task

    await db.init_pool()

    _retry_task = asyncio.create_task(retry_worker.run_retry_loop())
    _retry_task.add_done_callback(_on_task_done("retry_worker"))

    _grpc_task = asyncio.create_task(ext_proc_server.serve())
    _grpc_task.add_done_callback(_on_task_done("grpc_ext_proc"))

    yield

    if _retry_task and not _retry_task.done():
        _retry_task.cancel()
    if _grpc_task and not _grpc_task.done():
        _grpc_task.cancel()
    await db.close_pool()


app = FastAPI(
    title="Resource Sync Service",
    description="Envoy ext_proc gRPC for automatic resource ACL synchronisation",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _on_task_done(name: str):
    def _callback(task: asyncio.Task) -> None:
        if task.cancelled():
            logger.warning("%s task was cancelled", name)
        elif task.exception():
            logger.error("%s task failed: %s", name, task.exception(), exc_info=task.exception())
        else:
            logger.info("%s task finished cleanly", name)
    return _callback


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "resource-sync",
        "timestamp": datetime.utcnow().isoformat(),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8080, log_level="info")
