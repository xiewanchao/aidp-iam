"""
App Manifest Management API.

Endpoints:
  PUT    /AccessManager/Tenants/System/AppManifests/{namespace}  Register/update manifest
  GET    /AccessManager/Tenants/System/AppManifests              List all manifests
  GET    /AccessManager/Tenants/System/AppManifests/{namespace}  Get single manifest
  DELETE /AccessManager/Tenants/System/AppManifests/{namespace}  Delete manifest

On PUT, triggers initial ACL sync: expands default_acl templates for all
existing tenants and writes them to resource_acl.

The request body is the full manifest JSON (same format as manifest-template.json).
base_url must be present in the manifest body.
"""

import json
from fastapi import APIRouter, HTTPException, Request
from typing import Any, Dict, List
from app.core.db import get_pool

router = APIRouter(tags=["AppManifests"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _sync_default_acls(pool, namespace: str, manifest: Dict[str, Any], extra_tenant: str = "") -> int:
    """
    Expand default_acl templates in manifest for all existing tenants and
    write them to resource_acl. Returns the number of rows inserted.
    """
    tenant_rows = await pool.fetch(
        "SELECT DISTINCT tenant_id FROM api_keys UNION SELECT DISTINCT tenant_id FROM resource_acl"
    )
    tenant_ids = {r["tenant_id"] for r in tenant_rows}
    if extra_tenant:
        tenant_ids.add(extra_tenant)

    def _collect_acl_templates(resource_list: List[Dict]) -> List[Dict]:
        templates = []
        for res in resource_list:
            for acl in res.get("default_acl", []):
                templates.append(acl)
            templates.extend(_collect_acl_templates(res.get("children", [])))
        return templates

    templates = _collect_acl_templates(manifest.get("resources", []))
    inserted = 0

    for tid in tenant_ids:
        for tmpl in templates:
            user_path   = tmpl.get("user_template", "").replace("{tenantId}", tid)
            object_path = tmpl.get("object_template", "").replace("{tenantId}", tid)
            role_path   = tmpl.get("role_path", "")
            if not (user_path and object_path and role_path):
                continue
            try:
                result = await pool.execute(
                    """
                    INSERT INTO resource_acl (tenant_id, user_path, object_path, role_path, created_by)
                    VALUES ($1, $2, $3, $4, 'manifest-sync')
                    ON CONFLICT (tenant_id, user_path, object_path) DO NOTHING
                    """,
                    tid, user_path, object_path, role_path,
                )
                if result.endswith("1"):
                    inserted += 1
            except Exception:
                pass

    return inserted


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.put("/AccessManager/Tenants/System/AppManifests/{namespace}")
async def upsert_manifest(namespace: str, request: Request):
    """
    Register or update an application manifest. Request body is the full
    manifest JSON (must include base_url). Triggers initial ACL sync.
    """
    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON")

    base_url = body.get("base_url", "")
    if not base_url:
        raise HTTPException(status_code=400, detail="base_url is required in manifest body")
    callback_url = body.get("callback_url") or None

    pool = await get_pool()

    await pool.execute(
        """
        INSERT INTO app_manifests (namespace, base_url, callback_url, manifest_json)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (namespace) DO UPDATE
          SET base_url=$2, callback_url=$3, manifest_json=$4, registered_at=NOW()
        """,
        namespace, base_url, callback_url, json.dumps(body, ensure_ascii=False),
    )

    # Include the caller's tenant so default_acl is synced even on a fresh cluster
    caller_tenant = request.headers.get("x-auth-tenant", "") if request else ""
    synced = await _sync_default_acls(pool, namespace, body, extra_tenant=caller_tenant)

    return {"status": "ok", "namespace": namespace, "acls_synced": synced}


@router.get("/AccessManager/Tenants/System/AppManifests")
async def list_manifests():
    """List all registered application manifests."""
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT namespace, base_url, callback_url, registered_at FROM app_manifests ORDER BY namespace"
    )
    return {"manifests": [dict(r) for r in rows], "count": len(rows)}


@router.get("/AccessManager/Tenants/System/AppManifests/{namespace}")
async def get_manifest(namespace: str):
    """Get a single application manifest."""
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT namespace, base_url, callback_url, manifest_json, registered_at FROM app_manifests WHERE namespace=$1",
        namespace,
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Manifest not found: {namespace}")
    return dict(row)


@router.delete("/AccessManager/Tenants/System/AppManifests/{namespace}")
async def delete_manifest(namespace: str):
    """Delete an application manifest."""
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM app_manifests WHERE namespace=$1", namespace,
    )
    if result.endswith("0"):
        raise HTTPException(status_code=404, detail=f"Manifest not found: {namespace}")
    return {"status": "deleted", "namespace": namespace}
