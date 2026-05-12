# bundle-server/app/main.py  --  IAM v2.0
from contextlib import asynccontextmanager
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

# ---------------------------------------------------------------------------
# 策略：Default Deny + 纯 path_rules
#   - 所有授权都通过 path_rules 显式预置
#   - 没有任何代码层面的"旁路"逻辑
#   - 每个组能访问哪些路径+Method，在 DB 里一目了然
# ---------------------------------------------------------------------------

# 1) 应用禁用：app.enabled=false 时，该 app 所有路径一律拒绝
app_disabled {
    some app_name, app in data.apps
    startswith(concat("", [input.path, "/"]), app.path_prefix)
    app.enabled == false
}

# 2) path_rule 命中 + 用户组在允许列表（多对多 OR 语义）+ Method 匹配
allow {
    not app_disabled
    some rule in data.path_rules
    startswith(input.path, rule.path_prefix)
    method_matches(rule)
    some g in rule.required_groups
    g in input.groups
}

method_matches(rule) { rule.method == null }
method_matches(rule) { rule.method == input.method }
"""


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(application: FastAPI):
    global db_pool, _refresh_task, _last_data_hash
    logger.info("Bundle Server v2.0 starting up, connecting to PostgreSQL...")
    db_pool = await asyncpg.create_pool(DB_URL, min_size=2, max_size=10)

    # NOTE: schema is owned by postgres-init-configmap + init-keycloak.py.
    # Bundle-server is a read-only consumer; it waits for those to create
    # the canonical tables rather than trying to CREATE IF NOT EXISTS with
    # stale column definitions that would silently diverge.
    logger.info("PostgreSQL pool ready (schema owned by init-keycloak).")

    # Initial data load and push. Advance _last_data_hash only on success so a
    # startup race with OPA (OPA not yet ready) leaves the hash as None and the
    # periodic refresh will keep retrying until the push lands.
    opa_data = await _load_opa_data()
    if await _push_to_opa(opa_data):
        _last_data_hash = _hash_data(opa_data)
        logger.info("Initial OPA data push complete.")
    else:
        logger.warning("Initial OPA data push failed; periodic refresh will retry.")
    await _rebuild_bundle(opa_data)

    _refresh_task = asyncio.create_task(_periodic_refresh())

    yield

    if _refresh_task:
        _refresh_task.cancel()
        try:
            await _refresh_task
        except asyncio.CancelledError:
            pass
    if db_pool:
        await db_pool.close()


app = FastAPI(title="Bundle Server", lifespan=lifespan)


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
    """Read apps, permission_groups, and app_manifests from DB; flatten into OPA data.

    path_rules come from two sources merged by (path_prefix, method):
      1. permission_group_paths × permission_group_bindings  — system paths (/api/v1/, /AccessManager/)
      2. app_manifests.manifest_json resources[]             — application paths (manifest-registered apps)
    """
    async with db_pool.acquire() as conn:
        app_rows = await conn.fetch(
            "SELECT app_name, path_prefix, admin_group, enabled FROM apps ORDER BY app_name"
        )
        rule_rows = await conn.fetch("""
            SELECT pgp.path_prefix,
                   pgp.method,
                   array_agg(DISTINCT pgb.kc_group_name ORDER BY pgb.kc_group_name) AS groups
            FROM permission_group_paths pgp
            JOIN permission_group_bindings pgb ON pgp.group_id = pgb.group_id
            GROUP BY pgp.path_prefix, pgp.method
            ORDER BY pgp.path_prefix, pgp.method
        """)
        manifest_rows = await conn.fetch(
            "SELECT namespace, manifest_json FROM app_manifests"
        )

    apps: Dict[str, Any] = {}
    for row in app_rows:
        apps[row["app_name"]] = {
            "path_prefix": row["path_prefix"],
            "admin_group": row["admin_group"],
            "enabled": row["enabled"],
        }

    # Merge path_rules from both sources into a dict keyed by (path_prefix, method)
    rules_map: Dict[tuple, set] = {}

    for row in rule_rows:
        key = (row["path_prefix"], row["method"])
        rules_map.setdefault(key, set()).update(row["groups"])

    for row in manifest_rows:
        try:
            manifest = json.loads(row["manifest_json"])
        except Exception:
            continue
        _extract_manifest_path_rules(manifest.get("resources", []), rules_map)

    path_rules: List[Dict[str, Any]] = [
        {
            "path_prefix": prefix,
            "method": method,
            "required_groups": sorted(groups),
        }
        for (prefix, method), groups in sorted(
            rules_map.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")
        )
    ]

    return {"apps": apps, "path_rules": path_rules}


def _extract_manifest_path_rules(resources: List[Dict], out: Dict[tuple, set]) -> None:
    """Recursively derive (path_prefix, method) → groups from manifest resources.

    path_pattern like /KnowledgeBase/Tenants/{tenantId}/KnowledgeBases/{kbId}
    becomes path_prefix /KnowledgeBase/Tenants/ (strip from first /{param}).

    required_groups is derived from default_acl[].user_template — the last path
    segment of each user_template is the group name (e.g. "all-users" from
    "AccessManager/Tenants/{tenantId}/Groups/all-users"). Falls back to
    ["all-users"] when default_acl is absent or contains no group entries.
    """
    import re
    for resource in resources:
        pattern = resource.get("path_pattern", "")
        # Strip from the first path variable onwards to get the stable prefix
        match = re.search(r"/\{[^}]+\}", pattern)
        if match:
            prefix = pattern[:match.start() + 1]  # keep trailing slash
        else:
            prefix = pattern + "/"

        # Derive groups from default_acl user_template last segment
        groups: set = set()
        for acl in resource.get("default_acl", []):
            user_template = acl.get("user_template", "")
            if user_template:
                group_name = user_template.rstrip("/").split("/")[-1]
                # Skip template placeholders like {tenantId}
                if group_name and not (group_name.startswith("{") and group_name.endswith("}")):
                    groups.add(group_name)
        if not groups:
            groups = {"all-users"}

        for method in resource.get("methods", []):
            out.setdefault((prefix, method), set()).update(groups)

        for action in resource.get("actions", []):
            action_prefix = prefix.rstrip("/") + action.get("path_suffix", "")
            http_method = action.get("http_method", "POST")
            out.setdefault((action_prefix, http_method), set()).update(groups)

        _extract_manifest_path_rules(resource.get("children", []), out)


def _hash_data(data: Dict) -> str:
    """Produce a deterministic hash of the OPA data document for change detection."""
    serialized = json.dumps(data, sort_keys=True)
    return hashlib.sha256(serialized.encode()).hexdigest()


async def _push_to_opa(opa_data: Dict) -> bool:
    """Push Rego policy and data document to OPA via its REST API.

    Returns True iff all three PUTs (policy, apps, path_rules) succeeded.
    Callers use the return value to decide whether to advance _last_data_hash,
    so a failed push stays retryable on the next refresh cycle.
    """
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
                return False

            # Push apps data
            apps_resp = await client.put(
                f"{OPA_URL}/v1/data/apps",
                json=opa_data["apps"],
            )
            if apps_resp.status_code not in (200, 204):
                logger.error("OPA rejected apps data: %s - %s", apps_resp.status_code, apps_resp.text)
                return False

            # Push path_rules data
            rules_resp = await client.put(
                f"{OPA_URL}/v1/data/path_rules",
                json=opa_data["path_rules"],
            )
            if rules_resp.status_code not in (200, 204):
                logger.error("OPA rejected path_rules data: %s - %s", rules_resp.status_code, rules_resp.text)
                return False

        logger.info("Pushed Rego + data to OPA (apps=%d, path_rules=%d)",
                     len(opa_data["apps"]), len(opa_data["path_rules"]))
        return True
    except Exception as e:
        logger.error("Failed to push to OPA: %s", e)
        return False


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
    """Every REFRESH_INTERVAL seconds, check if apps/path_rules changed and push to OPA.

    _last_data_hash is seeded by the startup event only on a successful push, so
    if it is still None we know the initial push never landed and we must keep
    retrying regardless of whether the data has changed.
    """
    global _last_data_hash
    logger.info("Periodic refresh started (interval=%ds)", REFRESH_INTERVAL)

    while True:
        await asyncio.sleep(REFRESH_INTERVAL)
        try:
            opa_data = await _load_opa_data()
            current_hash = _hash_data(opa_data)

            needs_push = _last_data_hash is None or current_hash != _last_data_hash
            if not needs_push:
                logger.debug("Periodic refresh: no changes detected.")
                continue

            if _last_data_hash is None:
                logger.info("Retrying initial OPA push...")
            else:
                logger.info("Data change detected, pushing update to OPA...")

            if await _push_to_opa(opa_data):
                await _rebuild_bundle(opa_data)
                _last_data_hash = current_hash
                logger.info("Periodic refresh: OPA updated.")
            else:
                logger.warning("Periodic refresh: push failed, will retry next cycle.")
        except Exception as e:
            logger.error("Periodic refresh failed: %s", e)
