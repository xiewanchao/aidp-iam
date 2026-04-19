# bundle-server/app/main.py  --  IAM v2.0
from fastapi import FastAPI
from fastapi.responses import FileResponse
from typing import Dict, Any, List, Optional
import os
import json
import tarfile
import io
import asyncio
import hashlib
from datetime import datetime
import logging

import asyncpg
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Bundle Server")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BUNDLES_PATH = "/app/bundles"
OPA_URL = os.getenv("OPA_URL", "http://localhost:8181")
DB_URL = os.getenv("DB_URL", "postgresql://keycloak:keycloak@postgres.keycloak.svc.cluster.local:5432/iam")
REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL", "30"))

os.makedirs(BUNDLES_PATH, exist_ok=True)

db_pool: asyncpg.Pool = None
_refresh_task: Optional[asyncio.Task] = None
_last_data_hash: Optional[str] = None


# ---------------------------------------------------------------------------
# Rego policy (IAM v2.0)
# ---------------------------------------------------------------------------

REGO_POLICY = r"""package authz
import future.keywords.in
default allow = false

# App disabled check.
# path_prefix is stored with a trailing slash (e.g. "/anything/"), so we
# normalise the request path by appending a "/" before comparing — this lets
# `/anything` match the prefix `/anything/` (would otherwise fail startswith).
app_disabled {
    some app_name, app in data.apps
    startswith(concat("", [input.path, "/"]), app.path_prefix)
    app.enabled == false
}

# admins: single admin group (per diagrams/ui-wireframes.md single-tenant model).
# Bypass for management APIs and ACL APIs.
allow {
    not app_disabled
    "admins" in input.groups
    mgmt_or_acl_path
}

mgmt_or_acl_path {
    startswith(input.path, "/api/v1/")
}
mgmt_or_acl_path {
    startswith(input.path, "/acl/v1/")
}

# Path rule hit: check group membership (many-to-many, OR semantics)
allow {
    not app_disabled
    some rule in data.path_rules
    startswith(input.path, rule.path_prefix)
    some g in rule.required_groups
    g in input.groups
}

# Business path (NOT a management/ACL path) and not protected by a path_rule:
# all-users pass through. Management paths (/api/v1/* and /acl/v1/*) MUST go
# through the admin allow rule above; the all-users fallback never covers them.
allow {
    not app_disabled
    not is_management_path
    not path_is_protected
    "all-users" in input.groups
}

path_is_protected {
    some rule in data.path_rules
    startswith(input.path, rule.path_prefix)
}

is_management_path {
    startswith(input.path, "/api/v1/")
}
is_management_path {
    startswith(input.path, "/acl/v1/")
}
"""


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup_event():
    global db_pool, _refresh_task
    logger.info("Bundle Server v2.0 starting up, connecting to PostgreSQL...")
    db_pool = await asyncpg.create_pool(DB_URL, min_size=2, max_size=10)

    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS apps (
                name        VARCHAR PRIMARY KEY,
                path_prefix VARCHAR NOT NULL,
                enabled     BOOLEAN NOT NULL DEFAULT true
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS path_rules (
                id              SERIAL PRIMARY KEY,
                path_prefix     VARCHAR NOT NULL,
                required_group  VARCHAR NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS resource_patterns (
                id      SERIAL PRIMARY KEY,
                pattern VARCHAR NOT NULL,
                app     VARCHAR REFERENCES apps(name)
            )
        """)

    logger.info("PostgreSQL schema ready.")

    # Initial data load and push
    opa_data = await _load_opa_data()
    await _push_to_opa(opa_data)
    await _rebuild_bundle(opa_data)
    logger.info("Initial OPA data push complete.")

    # Start periodic refresh
    _refresh_task = asyncio.create_task(_periodic_refresh())


@app.on_event("shutdown")
async def shutdown_event():
    if _refresh_task:
        _refresh_task.cancel()
        try:
            await _refresh_task
        except asyncio.CancelledError:
            pass
    if db_pool:
        await db_pool.close()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "bundle-server",
        "version": "2.0",
        "timestamp": datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# OPA bundle endpoint
# ---------------------------------------------------------------------------

@app.get("/api/v1/opa-bundle")
async def get_opa_bundle():
    bundle_file = os.path.join(BUNDLES_PATH, "combined_bundle.tar.gz")
    if not os.path.exists(bundle_file):
        bundle_file = await _build_empty_bundle()
    return FileResponse(bundle_file, media_type="application/gzip", filename="bundle.tar.gz")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _load_opa_data() -> Dict[str, Any]:
    """Read apps and path_rules from DB and build the flat OPA data document."""
    async with db_pool.acquire() as conn:
        app_rows = await conn.fetch("SELECT app_name, path_prefix, enabled FROM apps ORDER BY app_name")
        rule_rows = await conn.fetch("""
            SELECT pr.id, pr.path_prefix, array_agg(prg.group_name) as groups
            FROM path_rules pr
            JOIN path_rule_groups prg ON pr.id = prg.rule_id
            GROUP BY pr.id, pr.path_prefix
            ORDER BY pr.id
        """)

    apps: Dict[str, Any] = {}
    for row in app_rows:
        apps[row["app_name"]] = {
            "path_prefix": row["path_prefix"],
            "enabled": row["enabled"],
        }

    path_rules: List[Dict[str, Any]] = []
    for row in rule_rows:
        path_rules.append({
            "path_prefix": row["path_prefix"],
            "required_groups": list(row["groups"]),
        })

    return {"apps": apps, "path_rules": path_rules}


def _hash_data(data: Dict) -> str:
    """Produce a deterministic hash of the OPA data document for change detection."""
    serialized = json.dumps(data, sort_keys=True)
    return hashlib.sha256(serialized.encode()).hexdigest()


async def _push_to_opa(opa_data: Dict):
    """Push Rego policy and data document to OPA via its REST API."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Push Rego policy
            resp = await client.put(
                f"{OPA_URL}/v1/policies/authz_main",
                content=REGO_POLICY.encode(),
                headers={"Content-Type": "text/plain"},
            )
            if resp.status_code not in (200, 204):
                logger.error("OPA rejected policy: %s - %s", resp.status_code, resp.text)

            # Push apps data
            apps_resp = await client.put(
                f"{OPA_URL}/v1/data/apps",
                json=opa_data["apps"],
            )
            if apps_resp.status_code not in (200, 204):
                logger.error("OPA rejected apps data: %s - %s", apps_resp.status_code, apps_resp.text)

            # Push path_rules data
            rules_resp = await client.put(
                f"{OPA_URL}/v1/data/path_rules",
                json=opa_data["path_rules"],
            )
            if rules_resp.status_code not in (200, 204):
                logger.error("OPA rejected path_rules data: %s - %s", rules_resp.status_code, rules_resp.text)

        logger.info("Pushed Rego + data to OPA (apps=%d, path_rules=%d)",
                     len(opa_data["apps"]), len(opa_data["path_rules"]))
    except Exception as e:
        logger.error("Failed to push to OPA: %s", e)


async def _rebuild_bundle(opa_data: Dict):
    """Rebuild the combined OPA bundle tar.gz served by GET /api/v1/opa-bundle."""
    combined_bundle = os.path.join(BUNDLES_PATH, "combined_bundle.tar.gz")

    with tarfile.open(combined_bundle, "w:gz") as tar:
        manifest = json.dumps({
            "revision": datetime.utcnow().isoformat(),
            "roots": ["authz"],
        }).encode()
        _tar_add(tar, ".manifest", manifest)

        data_bytes = json.dumps(opa_data).encode()
        _tar_add(tar, "data.json", data_bytes)

        rego_bytes = REGO_POLICY.encode()
        _tar_add(tar, "authz/policy.rego", rego_bytes)


def _tar_add(tar: tarfile.TarFile, name: str, data: bytes):
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


async def _build_empty_bundle() -> str:
    bundle_file = os.path.join(BUNDLES_PATH, "combined_bundle.tar.gz")
    with tarfile.open(bundle_file, "w:gz") as tar:
        manifest = json.dumps({"revision": "empty", "roots": ["authz"]}).encode()
        _tar_add(tar, ".manifest", manifest)
    return bundle_file


async def _periodic_refresh():
    """Every REFRESH_INTERVAL seconds, check if apps/path_rules changed and push to OPA."""
    global _last_data_hash
    logger.info("Periodic refresh started (interval=%ds)", REFRESH_INTERVAL)

    # Seed the hash from the initial load
    try:
        initial_data = await _load_opa_data()
        _last_data_hash = _hash_data(initial_data)
    except Exception as e:
        logger.error("Failed to seed initial data hash: %s", e)

    while True:
        await asyncio.sleep(REFRESH_INTERVAL)
        try:
            opa_data = await _load_opa_data()
            current_hash = _hash_data(opa_data)

            if current_hash != _last_data_hash:
                logger.info("Data change detected, pushing update to OPA...")
                await _push_to_opa(opa_data)
                await _rebuild_bundle(opa_data)
                _last_data_hash = current_hash
                logger.info("Periodic refresh: OPA updated.")
            else:
                logger.debug("Periodic refresh: no changes detected.")
        except Exception as e:
            logger.error("Periodic refresh failed: %s", e)
