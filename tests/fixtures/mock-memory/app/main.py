"""
Mock MemoryStore backend for AIDP IAM e2e tests.

Implements the MemoryStore API surface:
  /MemoryStore/Tenants/{tid}/Instances/{name}
  /MemoryStore/Tenants/{tid}/Instances/{name}/Memories/{memId}
  /MemoryStore/Tenants/{tid}/Instances/{name}/Memories/Query   (POST)
  /MemoryStore/Tenants/{tid}/Instances/{name}/Templates/{tplName}
  /MemoryStore/Tenants/{tid}/Instances/{name}/Templates/{tplName}/Filters      (POST)
  /MemoryStore/Tenants/{tid}/Instances/{name}/Templates/{tplName}/LLMExtraction (POST)

All state is in-memory (dict). The gateway injects X-Allowed-Ids on list
requests; this mock honours it for Instances list filtering.
"""
from flask import Flask, request, jsonify, Response
import json

app = Flask(__name__)

_instances = {}   # key: "{tid}/{name}"
_memories  = {}   # key: "{tid}/{name}/{memId}"
_templates = {}   # key: "{tid}/{name}/{tplName}"


def _ikey(tid, name):        return f"{tid}/{name}"
def _mkey(tid, name, mid):   return f"{tid}/{name}/{mid}"
def _tkey(tid, name, tpl):   return f"{tid}/{name}/{tpl}"

def _json(obj, status=200):
    return Response(json.dumps(obj), status=status, mimetype="application/json")

def _404(msg):
    return _json({"detail": msg}, 404)


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
        v for k, v in _memories.items()
        if k.startswith(prefix) and (allowed is None or v["id"] in allowed)
    ]
    return _json({"items": items, "total": len(items)})


@app.get("/MemoryStore/Tenants/<tid>/Instances/<name>/Memories/<mem_id>")
def get_memory(tid, name, mem_id):
    obj = _memories.get(_mkey(tid, name, mem_id))
    if not obj:
        return _404("memory not found")
    return _json(obj)


@app.put("/MemoryStore/Tenants/<tid>/Instances/<name>/Memories/<mem_id>")
def put_memory(tid, name, mem_id):
    if _ikey(tid, name) not in _instances:
        return _404("parent instance not found")
    key = _mkey(tid, name, mem_id)
    body = request.get_json(silent=True) or {}
    existed = key in _memories
    _memories[key] = {"tenant_id": tid, "instance_name": name, "id": mem_id, **body}
    return _json(_memories[key], 200 if existed else 201)


@app.delete("/MemoryStore/Tenants/<tid>/Instances/<name>/Memories/<mem_id>")
def delete_memory(tid, name, mem_id):
    key = _mkey(tid, name, mem_id)
    if key not in _memories:
        return _404("memory not found")
    del _memories[key]
    return Response(status=204)


@app.post("/MemoryStore/Tenants/<tid>/Instances/<name>/Memories/Query")
def query_memories(tid, name):
    if _ikey(tid, name) not in _instances:
        return _404("parent instance not found")
    body = request.get_json(silent=True) or {}
    query_text = body.get("query", "")
    prefix = f"{tid}/{name}/"
    results = [
        v for k, v in _memories.items()
        if k.startswith(prefix) and query_text.lower() in str(v).lower()
    ]
    return _json({"query": query_text, "results": results, "total": len(results)})


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
