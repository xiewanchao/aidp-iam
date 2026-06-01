"""
Mock MemoryStore backend for AIDP IAM e2e tests.

Implements the MemoryStore API surface:
  /MemoryStore/Tenants/{tid}/Instances/{name}
  /MemoryStore/Tenants/{tid}/Instances/{name}/Memories          (PUT create)
  /MemoryStore/Tenants/{tid}/Instances/{name}/Memories/{memId}  (PATCH update)
  /MemoryStore/Tenants/{tid}/Instances/{name}/Memories/Query    (POST)
  /MemoryStore/Tenants/{tid}/Instances/{name}/Memories/Delete   (POST)
  /MemoryStore/Tenants/{tid}/Instances/{name}/Templates/{tplName}
  /MemoryStore/Tenants/{tid}/Instances/{name}/Templates/{tplName}/Filters      (POST)
  /MemoryStore/Tenants/{tid}/Instances/{name}/Templates/{tplName}/LLMExtraction (POST)

app_managed_authz mode: IAM injects X-Auth-User-Id after verifying the caller
has Viewer on the parent Instance.  This mock uses that header to scope
Query/Delete results to the calling user's own memories.
"""
from flask import Flask, request, Response
import json
import uuid as _uuid

app = Flask(__name__)

_instances = {}   # key: "{tid}/{name}"
_memories  = {}   # key: "{tid}/{name}/{memId}"
_templates = {}   # key: "{tid}/{name}/{tplName}"


def _ikey(tid, name):       return f"{tid}/{name}"
def _mkey(tid, name, mid):  return f"{tid}/{name}/{mid}"
def _tkey(tid, name, tpl):  return f"{tid}/{name}/{tpl}"

def _json(obj, status=200):
    return Response(json.dumps(obj), status=status, mimetype="application/json")

def _404(msg):
    return _json({"detail": msg}, 404)

def _caller():
    """Return the user-id injected by the IAM gateway (X-Auth-User-Id)."""
    return request.headers.get("X-Auth-User-Id") or request.headers.get("x-auth-user-id")


# ── health ────────────────────────────────────────────────────────────────────
@app.get("/health")
@app.get("/MemoryStore/health")
def health():
    return _json({"status": "ok", "service": "mock-memory"})


# ── Instances ─────────────────────────────────────────────────────────────────
@app.get("/MemoryStore/Tenants/<tid>/Instances")
def list_instances(tid):
    allowed_raw = request.headers.get("x-allowed-ids", "")
    allowed = {x.strip() for x in allowed_raw.split(",") if x.strip()} if allowed_raw else None
    items = [
        v for k, v in _instances.items()
        if k.startswith(f"{tid}/") and (allowed is None or v["name"] in allowed)
    ]
    return _json({"items": items, "total": len(items)})


@app.get("/MemoryStore/Tenants/<tid>/Instances/<name>")
def get_instance(tid, name):
    obj = _instances.get(_ikey(tid, name))
    if not obj:
        return _404("instance not found")
    return _json(obj)


@app.put("/MemoryStore/Tenants/<tid>/Instances/<name>")
def put_instance(tid, name):
    key = _ikey(tid, name)
    body = request.get_json(silent=True) or {}
    existed = key in _instances
    _instances[key] = {"tenant_id": tid, "name": name, **body}
    return _json(_instances[key], 200 if existed else 201)


@app.delete("/MemoryStore/Tenants/<tid>/Instances/<name>")
def delete_instance(tid, name):
    key = _ikey(tid, name)
    if key not in _instances:
        return _404("instance not found")
    del _instances[key]
    for store in (_memories, _templates):
        for k in list(store.keys()):
            if k.startswith(f"{tid}/{name}/"):
                del store[k]
    return Response(status=204)


# ── Memories ──────────────────────────────────────────────────────────────────
@app.get("/MemoryStore/Tenants/<tid>/Instances/<name>/Memories")
def list_memories(tid, name):
    allowed_raw = request.headers.get("x-allowed-ids", "")
    allowed = {x.strip() for x in allowed_raw.split(",") if x.strip()} if allowed_raw else None
    prefix = f"{tid}/{name}/"
    items = [
        {k: v for k, v in mem.items() if k != "_owner"}
        for k, mem in _memories.items()
        if k.startswith(prefix) and (allowed is None or mem["id"] in allowed)
    ]
    return _json({"items": items, "total": len(items)})


@app.put("/MemoryStore/Tenants/<tid>/Instances/<name>/Memories")
def create_memory(tid, name):
    if _ikey(tid, name) not in _instances:
        return _404("parent instance not found")
    body = request.get_json(silent=True) or {}
    memory_id = str(_uuid.uuid4())
    key = _mkey(tid, name, memory_id)
    owner = _caller()
    _memories[key] = {"tenant_id": tid, "instance_name": name, "id": memory_id, "_owner": owner, **body}
    return _json({k: v for k, v in _memories[key].items() if k != "_owner"}, 201)


@app.route("/MemoryStore/Tenants/<tid>/Instances/<name>/Memories/<mem_id>", methods=["PATCH"])
def patch_memory(tid, name, mem_id):
    key = _mkey(tid, name, mem_id)
    if key not in _memories:
        return _404("memory not found")
    body = request.get_json(silent=True) or {}
    _memories[key].update({k: v for k, v in body.items() if k != "_owner"})
    return _json({k: v for k, v in _memories[key].items() if k != "_owner"})


@app.post("/MemoryStore/Tenants/<tid>/Instances/<name>/Memories/Query")
def query_memories(tid, name):
    if _ikey(tid, name) not in _instances:
        return _404("parent instance not found")
    caller = _caller()
    body = request.get_json(silent=True) or {}
    memory_id = body.get("memory_id")
    query_text = body.get("query", "")
    prefix = f"{tid}/{name}/"
    results = [
        {k: v for k, v in mem.items() if k != "_owner"}
        for key, mem in _memories.items()
        if key.startswith(prefix)
        and (caller is None or mem.get("_owner") == caller)
        and (memory_id is None or mem["id"] == memory_id)
        and (not query_text or query_text.lower() in str(mem).lower())
    ]
    return _json({"query": query_text, "results": results, "total": len(results)})


@app.post("/MemoryStore/Tenants/<tid>/Instances/<name>/Memories/Delete")
def delete_memories(tid, name):
    if _ikey(tid, name) not in _instances:
        return _404("parent instance not found")
    caller = _caller()
    body = request.get_json(silent=True) or {}
    memory_id = body.get("memory_id")
    filters = body.get("filters", {})
    deleted = []
    if memory_id:
        key = _mkey(tid, name, memory_id)
        mem = _memories.get(key)
        if mem and (caller is None or mem.get("_owner") == caller):
            del _memories[key]
            deleted.append(memory_id)
    elif filters:
        prefix = f"{tid}/{name}/"
        for k in list(_memories.keys()):
            if not k.startswith(prefix):
                continue
            v = _memories[k]
            if (caller is None or v.get("_owner") == caller) and \
               all(str(v.get(fk)) == str(fv) for fk, fv in filters.items()):
                deleted.append(v["id"])
                del _memories[k]
    return _json({"deleted": deleted, "count": len(deleted)})


# ── Templates ─────────────────────────────────────────────────────────────────
@app.get("/MemoryStore/Tenants/<tid>/Instances/<name>/Templates")
def list_templates(tid, name):
    prefix = f"{tid}/{name}/"
    items = [v for k, v in _templates.items() if k.startswith(prefix)]
    return _json({"items": items, "total": len(items)})


@app.get("/MemoryStore/Tenants/<tid>/Instances/<name>/Templates/<tpl_name>")
def get_template(tid, name, tpl_name):
    obj = _templates.get(_tkey(tid, name, tpl_name))
    if not obj:
        return _404("template not found")
    return _json(obj)


@app.put("/MemoryStore/Tenants/<tid>/Instances/<name>/Templates/<tpl_name>")
def put_template(tid, name, tpl_name):
    if _ikey(tid, name) not in _instances:
        return _404("parent instance not found")
    key = _tkey(tid, name, tpl_name)
    body = request.get_json(silent=True) or {}
    existed = key in _templates
    _templates[key] = {"tenant_id": tid, "instance_name": name, "name": tpl_name, **body}
    return _json(_templates[key], 200 if existed else 201)


@app.route("/MemoryStore/Tenants/<tid>/Instances/<name>/Templates/<tpl_name>", methods=["PATCH"])
def patch_template(tid, name, tpl_name):
    key = _tkey(tid, name, tpl_name)
    if key not in _templates:
        return _404("template not found")
    body = request.get_json(silent=True) or {}
    _templates[key].update(body)
    return _json(_templates[key])


@app.delete("/MemoryStore/Tenants/<tid>/Instances/<name>/Templates/<tpl_name>")
def delete_template(tid, name, tpl_name):
    key = _tkey(tid, name, tpl_name)
    if key not in _templates:
        return _404("template not found")
    del _templates[key]
    return Response(status=204)


@app.post("/MemoryStore/Tenants/<tid>/Instances/<name>/Templates/<tpl_name>/Filters")
def set_filters(tid, name, tpl_name):
    key = _tkey(tid, name, tpl_name)
    if key not in _templates:
        return _404("template not found")
    body = request.get_json(silent=True) or {}
    _templates[key]["filters"] = body
    return _json({"status": "ok", "filters": body})


@app.post("/MemoryStore/Tenants/<tid>/Instances/<name>/Templates/<tpl_name>/LLMExtraction")
def set_llm_extraction(tid, name, tpl_name):
    key = _tkey(tid, name, tpl_name)
    if key not in _templates:
        return _404("template not found")
    body = request.get_json(silent=True) or {}
    _templates[key]["llm_extraction"] = body
    return _json({"status": "ok", "llm_extraction": body})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
