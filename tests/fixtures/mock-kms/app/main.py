"""
Mock KMS backend for AIDP IAM e2e tests.

Implements the KMS API surface:
  /KnowledgeBase/Tenants/{tid}/KnowledgeBases/{kb_id}
  /KnowledgeBase/Tenants/{tid}/KnowledgeBases/Count            (type-level action)
  /KnowledgeBase/Tenants/{tid}/KnowledgeBases/GetFilesystem    (type-level action)
  /KnowledgeBase/Tenants/{tid}/KnowledgeBases/GetNfsshare      (type-level action)
  /KnowledgeBase/Tenants/{tid}/KnowledgeBases/{kb_id}/Channels/{ch_id}
  /KnowledgeBase/Tenants/{tid}/KnowledgeBases/{kb_id}/Channels/Count   (type-level action)
  /KnowledgeBase/Tenants/{tid}/KnowledgeBases/{kb_id}/KnowledgeFiles   (singleton)
  /KnowledgeBase/Tenants/{tid}/KnowledgeBases/{kb_id}/KnowledgeFiles/Upload
  /KnowledgeBase/Tenants/{tid}/KnowledgeBases/{kb_id}/KnowledgeFiles/History
  /KnowledgeBase/Tenants/{tid}/KnowledgeBases/{kb_id}/KnowledgeFiles/Count
  /KnowledgeBase/Tenants/{tid}/KnowledgeBases/{kb_id}/KnowledgeFiles/Remove
  /KnowledgeBase/Tenants/{tid}/KnowledgeBases/{kb_id}/KnowledgeFiles/GetFilesystem
  /KnowledgeBase/Tenants/{tid}/KnowledgeBases/{kb_id}/KnowledgeFiles/GetNfsshare
  /KnowledgeBase/Tenants/{tid}/Retrieval/FusionSearch
  /KnowledgeBase/Tenants/{tid}/JargonGroups
  /KnowledgeBase/Tenants/{tid}/JargonGroups/Version            (type-level action)

All state is in-memory. The gateway injects X-Allowed-Ids on KnowledgeBases
list requests; this mock honours it for filtering.
"""
from flask import Flask, request, Response
import json

app = Flask(__name__)

_kbs      = {}   # key: "{tid}/{kb_id}"
_channels = {}   # key: "{tid}/{kb_id}/{ch_id}"
_files    = {}   # key: "{tid}/{kb_id}" → list of file dicts
_jargons  = {}   # key: "{tid}" → list of jargon group dicts


def _j(obj, status=200):
    return Response(json.dumps(obj), status=status, mimetype="application/json")


def _404(msg):
    return _j({"detail": msg}, 404)


def _allowed(raw_header):
    if not raw_header:
        return None
    ids = {x.strip() for x in raw_header.split(",") if x.strip()}
    return ids if ids else None


_ADMIN_GROUPS = {"tenant-admins", "master-admins"}


def _is_admin():
    raw = request.headers.get("x-auth-groups", "")
    groups = {g.strip() for g in raw.split(",") if g.strip()}
    return bool(groups & _ADMIN_GROUPS)


def _admin_only():
    """Return 403 response if caller is not admin, else None."""
    if not _is_admin():
        return _j({"detail": "Channels 仅管理员可操作"}, 403)
    return None


# ── health ────────────────────────────────────────────────────────────────────
@app.get("/health")
@app.get("/KnowledgeBase/health")
def health():
    return _j({"status": "ok", "service": "mock-kms"})


# ── KnowledgeBases ────────────────────────────────────────────────────────────
@app.get("/KnowledgeBase/Tenants/<tid>/KnowledgeBases")
def list_kbs(tid):
    allowed = _allowed(request.headers.get("x-allowed-ids", ""))
    items = [
        v for k, v in _kbs.items()
        if k.startswith(f"{tid}/") and (allowed is None or v["id"] in allowed)
    ]
    return _j({"items": items, "total": len(items)})


@app.get("/KnowledgeBase/Tenants/<tid>/KnowledgeBases/<kb_id>")
def get_kb(tid, kb_id):
    obj = _kbs.get(f"{tid}/{kb_id}")
    if not obj:
        return _404("knowledge base not found")
    return _j(obj)


@app.put("/KnowledgeBase/Tenants/<tid>/KnowledgeBases/<kb_id>")
def put_kb(tid, kb_id):
    key = f"{tid}/{kb_id}"
    body = request.get_json(silent=True) or {}
    existed = key in _kbs
    _kbs[key] = {"tenant_id": tid, "id": kb_id, **body}
    return _j(_kbs[key], 200 if existed else 201)


@app.route("/KnowledgeBase/Tenants/<tid>/KnowledgeBases/<kb_id>", methods=["PATCH"])
def patch_kb(tid, kb_id):
    key = f"{tid}/{kb_id}"
    if key not in _kbs:
        return _404("knowledge base not found")
    _kbs[key].update(request.get_json(silent=True) or {})
    return _j(_kbs[key])


@app.delete("/KnowledgeBase/Tenants/<tid>/KnowledgeBases/<kb_id>")
def delete_kb(tid, kb_id):
    key = f"{tid}/{kb_id}"
    if key not in _kbs:
        return _404("knowledge base not found")
    del _kbs[key]
    for k in list(_channels.keys()):
        if k.startswith(f"{tid}/{kb_id}/"):
            del _channels[k]
    _files.pop(f"{tid}/{kb_id}", None)
    return _j({"deleted": kb_id})


# KnowledgeBases type-level actions (no kb_id)
@app.post("/KnowledgeBase/Tenants/<tid>/KnowledgeBases/Count")
def kb_count(tid):
    count = sum(1 for k in _kbs if k.startswith(f"{tid}/"))
    return _j({"count": count})


@app.post("/KnowledgeBase/Tenants/<tid>/KnowledgeBases/GetFilesystem")
def kb_get_filesystem(tid):
    return _j({"filesystem": "nfs", "tenant_id": tid})


@app.post("/KnowledgeBase/Tenants/<tid>/KnowledgeBases/GetNfsshare")
def kb_get_nfsshare(tid):
    return _j({"nfs_share": f"/exports/{tid}", "tenant_id": tid})


# ── Channels ──────────────────────────────────────────────────────────────────
@app.get("/KnowledgeBase/Tenants/<tid>/KnowledgeBases/<kb_id>/Channels")
def list_channels(tid, kb_id):
    deny = _admin_only()
    if deny:
        return deny
    if f"{tid}/{kb_id}" not in _kbs:
        return _404("knowledge base not found")
    prefix = f"{tid}/{kb_id}/"
    items = [v for k, v in _channels.items() if k.startswith(prefix)]
    return _j({"items": items, "total": len(items)})


@app.get("/KnowledgeBase/Tenants/<tid>/KnowledgeBases/<kb_id>/Channels/<ch_id>")
def get_channel(tid, kb_id, ch_id):
    deny = _admin_only()
    if deny:
        return deny
    obj = _channels.get(f"{tid}/{kb_id}/{ch_id}")
    if not obj:
        return _404("channel not found")
    return _j(obj)


@app.put("/KnowledgeBase/Tenants/<tid>/KnowledgeBases/<kb_id>/Channels/<ch_id>")
def put_channel(tid, kb_id, ch_id):
    deny = _admin_only()
    if deny:
        return deny
    if f"{tid}/{kb_id}" not in _kbs:
        return _404("parent knowledge base not found")
    key = f"{tid}/{kb_id}/{ch_id}"
    body = request.get_json(silent=True) or {}
    existed = key in _channels
    _channels[key] = {"tenant_id": tid, "kb_id": kb_id, "id": ch_id, **body}
    return _j(_channels[key], 200 if existed else 201)


@app.delete("/KnowledgeBase/Tenants/<tid>/KnowledgeBases/<kb_id>/Channels/<ch_id>")
def delete_channel(tid, kb_id, ch_id):
    deny = _admin_only()
    if deny:
        return deny
    key = f"{tid}/{kb_id}/{ch_id}"
    if key not in _channels:
        return _404("channel not found")
    del _channels[key]
    return _j({"deleted": ch_id})


@app.post("/KnowledgeBase/Tenants/<tid>/KnowledgeBases/<kb_id>/Channels/Count")
def channel_count(tid, kb_id):
    deny = _admin_only()
    if deny:
        return deny
    count = sum(1 for k in _channels if k.startswith(f"{tid}/{kb_id}/"))
    return _j({"count": count})


# ── KnowledgeFiles (singleton under each KB) ──────────────────────────────────
@app.get("/KnowledgeBase/Tenants/<tid>/KnowledgeBases/<kb_id>/KnowledgeFiles")
def list_files(tid, kb_id):
    if f"{tid}/{kb_id}" not in _kbs:
        return _404("knowledge base not found")
    items = _files.get(f"{tid}/{kb_id}", [])
    return _j({"items": items, "total": len(items)})


@app.delete("/KnowledgeBase/Tenants/<tid>/KnowledgeBases/<kb_id>/KnowledgeFiles")
def delete_file(tid, kb_id):
    key = f"{tid}/{kb_id}"
    if key not in _kbs:
        return _404("knowledge base not found")
    file_id = (request.get_json(silent=True) or {}).get("file_id")
    store = _files.get(key, [])
    before = len(store)
    _files[key] = [f for f in store if f.get("file_id") != file_id]
    return _j({"deleted": before - len(_files[key])})


@app.post("/KnowledgeBase/Tenants/<tid>/KnowledgeBases/<kb_id>/KnowledgeFiles/Upload")
def files_upload(tid, kb_id):
    key = f"{tid}/{kb_id}"
    if key not in _kbs:
        return _404("knowledge base not found")
    body = request.get_json(silent=True) or {}
    file_id = body.get("file_id", f"file-{len(_files.get(key, []))}")
    _files.setdefault(key, []).append({"file_id": file_id, **body})
    return _j({"file_id": file_id, "status": "uploaded"}, 201)


@app.post("/KnowledgeBase/Tenants/<tid>/KnowledgeBases/<kb_id>/KnowledgeFiles/History")
def files_history(tid, kb_id):
    if f"{tid}/{kb_id}" not in _kbs:
        return _404("knowledge base not found")
    items = _files.get(f"{tid}/{kb_id}", [])
    return _j({"items": [{"file_id": f["file_id"], "status": "done"} for f in items]})


@app.post("/KnowledgeBase/Tenants/<tid>/KnowledgeBases/<kb_id>/KnowledgeFiles/Count")
def files_count(tid, kb_id):
    return _j({"count": len(_files.get(f"{tid}/{kb_id}", []))})


@app.post("/KnowledgeBase/Tenants/<tid>/KnowledgeBases/<kb_id>/KnowledgeFiles/Remove")
def files_remove(tid, kb_id):
    key = f"{tid}/{kb_id}"
    if key not in _kbs:
        return _404("knowledge base not found")
    file_ids = set((request.get_json(silent=True) or {}).get("file_ids", []))
    before = len(_files.get(key, []))
    _files[key] = [f for f in _files.get(key, []) if f.get("file_id") not in file_ids]
    return _j({"deleted": before - len(_files[key])})


@app.post("/KnowledgeBase/Tenants/<tid>/KnowledgeBases/<kb_id>/KnowledgeFiles/GetFilesystem")
def files_get_filesystem(tid, kb_id):
    if f"{tid}/{kb_id}" not in _kbs:
        return _404("knowledge base not found")
    return _j({"filesystem": "nfs", "kb_id": kb_id})


@app.post("/KnowledgeBase/Tenants/<tid>/KnowledgeBases/<kb_id>/KnowledgeFiles/GetNfsshare")
def files_get_nfsshare(tid, kb_id):
    if f"{tid}/{kb_id}" not in _kbs:
        return _404("knowledge base not found")
    return _j({"nfs_share": f"/exports/{tid}/{kb_id}", "kb_id": kb_id})


# ── Retrieval ─────────────────────────────────────────────────────────────────
@app.post("/KnowledgeBase/Tenants/<tid>/Retrieval/FusionSearch")
def fusion_search(tid):
    body = request.get_json(silent=True) or {}
    query = body.get("query", "")
    kb_ids = body.get("kb_ids", [])
    results = [
        {"kb_id": kb_id, "score": 0.9, "content": f"mock result for '{query}'"}
        for kb_id in kb_ids
        if f"{tid}/{kb_id}" in _kbs
    ]
    return _j({"query": query, "results": results, "total": len(results)})


# ── JargonGroups ──────────────────────────────────────────────────────────────
@app.get("/KnowledgeBase/Tenants/<tid>/JargonGroups")
def list_jargons(tid):
    items = _jargons.get(tid, [])
    return _j({"items": items, "total": len(items)})


@app.put("/KnowledgeBase/Tenants/<tid>/JargonGroups")
def put_jargon(tid):
    body = request.get_json(silent=True) or {}
    name = body.get("name")
    if not name:
        return _j({"detail": "name is required"}, 400)
    store = _jargons.setdefault(tid, [])
    existing = next((i for i, g in enumerate(store) if g["name"] == name), None)
    if existing is not None:
        store[existing].update(body)
        return _j(store[existing])
    entry = {"name": name, "tenant_id": tid, **body}
    store.append(entry)
    return _j(entry, 201)


@app.route("/KnowledgeBase/Tenants/<tid>/JargonGroups", methods=["PATCH"])
def patch_jargon(tid):
    body = request.get_json(silent=True) or {}
    name = body.get("name")
    store = _jargons.get(tid, [])
    entry = next((g for g in store if g["name"] == name), None)
    if not entry:
        return _404("jargon group not found")
    entry.update(body)
    return _j(entry)


@app.delete("/KnowledgeBase/Tenants/<tid>/JargonGroups")
def delete_jargon(tid):
    body = request.get_json(silent=True) or {}
    name = body.get("name")
    store = _jargons.get(tid, [])
    before = len(store)
    _jargons[tid] = [g for g in store if g["name"] != name]
    return _j({"deleted": before - len(_jargons[tid])})


@app.post("/KnowledgeBase/Tenants/<tid>/JargonGroups/Version")
def jargon_version(tid):
    return _j({"version": len(_jargons.get(tid, [])), "tenant_id": tid})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)

