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
import re
from typing import Any, Dict, List

from app.core.db import get_pool
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["AppManifests"])


def _resource_pattern_row(namespace: str, resource: Dict, parent_mode: str) -> Dict:
    pattern = resource.get("path_pattern", "")
    mode = resource.get("list_filter_mode", parent_mode)
    match = re.search(r"/\{([^}]+)\}$", pattern)
    if match:
        resource_prefix = pattern[:match.start()]
        id_field = match.group(1)
    else:
        resource_prefix = pattern
        id_field = "id"
    return {
        "app_name": namespace,
        "resource_prefix": resource_prefix,
        "resource_type": resource.get("type", ""),
        "id_source": "path",
        "id_field": id_field,
        "list_filter_mode": mode,
        "admin_bypass": resource.get("admin_bypass", True),
        "on_create_acl": resource.get("on_create_acl", []),
        "allow_create_without_acl": resource.get("allow_create_without_acl", False),
        "app_managed_authz": resource.get("app_managed_authz", False),
    }


def _collect_resource_patterns(
    namespace: str,
    resources: List[Dict],
    out: List[Dict],
    parent_mode: str = "gateway_inject",
) -> None:
    for resource in resources:
        row = _resource_pattern_row(namespace, resource, parent_mode)
        out.append(row)
        _collect_resource_patterns(namespace, resource.get("children", []), out, row["list_filter_mode"])


async def _ensure_app_exists(pool, namespace: str, manifest: Dict[str, Any]) -> None:
    await pool.execute(
        """
        INSERT INTO apps (app_name, path_prefix, display_name, enabled)
        VALUES ($1, $2, $3, true)
        ON CONFLICT (app_name) DO UPDATE SET
            path_prefix = EXCLUDED.path_prefix,
            display_name = EXCLUDED.display_name
        """,
        namespace,
        f"/{namespace}/",
        manifest.get("display_name", namespace),
    )


async def _upsert_resource_pattern(pool, row: Dict) -> bool:
    result = await pool.execute(
        """
        INSERT INTO resource_patterns
            (
                app_name, resource_prefix, method, resource_type,
                id_source, id_field, list_filter_mode, admin_bypass,
                on_create_acl, allow_create_without_acl, app_managed_authz
            )
        VALUES ($1, $2, '', $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (app_name, resource_prefix, method) DO UPDATE
          SET resource_type = EXCLUDED.resource_type,
              id_source = EXCLUDED.id_source,
              id_field = EXCLUDED.id_field,
              list_filter_mode = EXCLUDED.list_filter_mode,
              admin_bypass = EXCLUDED.admin_bypass,
              on_create_acl = EXCLUDED.on_create_acl,
              allow_create_without_acl = EXCLUDED.allow_create_without_acl,
              app_managed_authz = EXCLUDED.app_managed_authz
        """,
        row["app_name"],
        row["resource_prefix"],
        row["resource_type"],
        row["id_source"],
        row["id_field"],
        row["list_filter_mode"],
        row["admin_bypass"],
        json.dumps(row["on_create_acl"]),
        row["allow_create_without_acl"],
        row["app_managed_authz"],
    )
    return result.endswith("1")


async def _sync_resource_patterns(pool, namespace: str, manifest: Dict[str, Any]) -> int:
    """
    Derive resource_patterns rows from manifest resources[] and upsert into DB.

    Children are processed recursively. Actions are not written to resource_patterns
    because they are non-standard verbs handled by resource_actions.
    """
    rows: List[Dict] = []
    _collect_resource_patterns(namespace, manifest.get("resources", []), rows)

    inserted = 0
    await _ensure_app_exists(pool, namespace, manifest)
    for row in rows:
        if await _upsert_resource_pattern(pool, row):
            inserted += 1
    return inserted


def _collect_acl_templates(resource_list: List[Dict]) -> List[Dict]:
    templates = []
    for resource in resource_list:
        templates.extend(resource.get("default_acl", []))
        templates.extend(_collect_acl_templates(resource.get("children", [])))
    return templates


async def _insert_default_acl(pool, tenant_id: str, template: Dict) -> bool:
    user_path = template.get("user_template", "").replace("{tenantId}", tenant_id)
    object_path = template.get("object_template", "").replace("{tenantId}", tenant_id)
    role_path = template.get("role_path", "")
    if not (user_path and object_path and role_path):
        return False
    result = await pool.execute(
        """
        INSERT INTO resource_acl (tenant_id, user_path, object_path, role_path, created_by)
        VALUES ($1, $2, $3, $4, 'manifest-sync')
        ON CONFLICT (tenant_id, user_path, object_path) DO NOTHING
        """,
        tenant_id,
        user_path,
        object_path,
        role_path,
    )
    return result.endswith("1")


async def _sync_default_acls(pool, namespace: str, manifest: Dict[str, Any], extra_tenant: str = "") -> int:
    """
    Expand default_acl templates in manifest for all existing tenants and
    write them to resource_acl. Returns the number of rows inserted.
    """
    tenant_rows = await pool.fetch(
        "SELECT DISTINCT tenant_id FROM api_keys UNION SELECT DISTINCT tenant_id FROM resource_acl"
    )
    tenant_ids = {row["tenant_id"] for row in tenant_rows}
    if extra_tenant:
        tenant_ids.add(extra_tenant)

    templates = _collect_acl_templates(manifest.get("resources", []))
    inserted = 0
    for tenant_id in tenant_ids:
        for template in templates:
            if await _insert_default_acl(pool, tenant_id, template):
                inserted += 1
    return inserted


@router.put("/AccessManager/Tenants/System/AppManifests/{namespace}")
async def upsert_manifest(namespace: str, request: Request):
    """
    Register or update an application manifest. Request body is the full
    manifest JSON (must include base_url). Triggers initial ACL sync.
    """
    try:
        body: Dict[str, Any] = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON") from exc

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
        namespace,
        base_url,
        callback_url,
        json.dumps(body, ensure_ascii=False),
    )

    caller_tenant = request.headers.get("x-auth-tenant", "") if request else ""
    synced_acls = await _sync_default_acls(pool, namespace, body, extra_tenant=caller_tenant)
    synced_patterns = await _sync_resource_patterns(pool, namespace, body)
    return {
        "status": "ok",
        "namespace": namespace,
        "acls_synced": synced_acls,
        "patterns_synced": synced_patterns,
    }


@router.get("/AccessManager/Tenants/System/AppManifests")
async def list_manifests():
    """List all registered application manifests."""
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT namespace, base_url, callback_url, registered_at FROM app_manifests ORDER BY namespace"
    )
    return {"manifests": [dict(row) for row in rows], "count": len(rows)}


@router.get("/AccessManager/Tenants/System/AppManifests/{namespace}")
async def get_manifest(namespace: str):
    """Get a single application manifest."""
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT namespace, base_url, callback_url, manifest_json, registered_at "
        "FROM app_manifests WHERE namespace=$1",
        namespace,
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Manifest not found: {namespace}")
    return dict(row)


@router.delete("/AccessManager/Tenants/System/AppManifests/{namespace}")
async def delete_manifest(namespace: str):
    """Delete an application manifest."""
    pool = await get_pool()
    result = await pool.execute("DELETE FROM app_manifests WHERE namespace=$1", namespace)
    if result.endswith("0"):
        raise HTTPException(status_code=404, detail=f"Manifest not found: {namespace}")
    return {"status": "deleted", "namespace": namespace}
