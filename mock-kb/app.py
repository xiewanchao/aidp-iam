"""Mock KnowledgeBase backend — RESTful API aligned with manifest-based IAM.

URL convention: /KnowledgeBase/Tenants/{tenantId}/{ResourceType}/{resourceId}
tenantId is extracted from the URL path, not from request body.

Auth contract:
  - Reads X-Auth-User-Id / X-Auth-Tenant / X-Auth-Groups injected by pep-proxy.
  - List endpoints honor X-Allowed-Ids when present (admin bypass when absent).
  - Echoes auth headers back as X-Debug-* on every response.

Storage is in-memory and resets on pod restart.
"""
from flask import Flask, request, jsonify, Response
import uuid
import time
import json

app = Flask(__name__)

# ── In-memory stores ──────────────────────────────────────────────────────
KBS = {}          # {tenantId: {kbId: kb}}
MAPPINGS = {}     # {tenantId: {kbId: {mappingId: mapping}}}
FILES = {}        # {tenantId: {kbId: {fileId: file}}}
CONVERSATIONS = {}# {tenantId: {threadId: conv}}
MODELS = {}       # {modelId: model}  — system-level
PROMPTS = {}      # {promptId: prompt} — system-level
JARGON_LIBS = {}  # {tenantId: {libName: lib}}
JARGONS = {}      # {tenantId: {libName: {jargonName: jargon}}}


def _seed():
    for kid in ("KB1", "KB2", "KB3"):
        KBS.setdefault("system", {})[kid] = {
            "id": kid, "name": f"Seed {kid}",
            "description": f"pre-seeded {kid}",
            "state": 1, "created_by": "system",
            "created_at": "2026-01-01T00:00:00Z",
        }


_seed()


# ── Helpers ───────────────────────────────────────────────────────────────

def _auth():
    return {
        "user_id": request.headers.get("X-Auth-User-Id", ""),
        "tenant":  request.headers.get("X-Auth-Tenant", ""),
        "groups":  request.headers.get("X-Auth-Groups", ""),
    }


def _allowed_ids():
    header = request.headers.get("X-Allowed-Ids")
    if header is None:
        return None
    if header == "":
        return set()
    return {x.strip() for x in header.split(",") if x.strip()}


def _paginate(items):
    page = int(request.args.get("page", 1))
    size = int(request.args.get("size", 20))
    start = max((page - 1) * size, 0)
    return items[start:start + size], page, size, len(items)


def _filter_by_allowed(items, id_key="id"):
    allowed = _allowed_ids()
    if allowed is None:
        return items
    return [x for x in items if str(x.get(id_key)) in allowed]


def _debug_headers():
    return {
        "X-Debug-User-Id":     request.headers.get("X-Auth-User-Id", ""),
        "X-Debug-Tenant":      request.headers.get("X-Auth-Tenant", ""),
        "X-Debug-Groups":      request.headers.get("X-Auth-Groups", ""),
        "X-Debug-Allowed-Ids": request.headers.get("X-Allowed-Ids", ""),
    }


def _ok(data=None, code=200):
    resp = jsonify({"data": data, "code": 0, "message": "success"})
    resp.status_code = code
    for k, v in _debug_headers().items():
        resp.headers[k] = v
    return resp


def _created(data):
    return _ok(data, code=201)


def _not_found(msg):
    resp = jsonify({"code": 404, "message": msg, "data": None})
    resp.status_code = 404
    for k, v in _debug_headers().items():
        resp.headers[k] = v
    return resp


def _new_id():
    return str(uuid.uuid4())[:8]


@app.route("/health")
def health():
    return _ok({"service": "mock-kb"})


# ── KnowledgeBases ────────────────────────────────────────────────────────

@app.route("/KnowledgeBase/Tenants/<tenant_id>/KnowledgeBases", methods=["GET"])
def kb_list(tenant_id):
    items = _filter_by_allowed(list(KBS.get(tenant_id, {}).values()))
    paged, page, size, total = _paginate(items)
    return _ok({"items": paged, "total": total, "page": page, "size": size})


@app.route("/KnowledgeBase/Tenants/<tenant_id>/KnowledgeBases", methods=["POST"])
def kb_create(tenant_id):
    body = request.get_json(force=True, silent=True) or {}
    kb_id = _new_id()
    kb = {
        "id":          kb_id,
        "name":        body.get("name", f"kb-{kb_id}"),
        "description": body.get("description", ""),
        "state":       1,
        "created_by":  _auth()["user_id"],
        "created_at":  time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config": {
            "chunk_token_num":   body.get("chunk_token_num", 1024),
            "chunk_overlap_num": body.get("chunk_overlap_num", 128),
            "embedding_model":   body.get("embedding_model", "default"),
            "topk":              body.get("topk", 10),
            "similarity":        body.get("similarity", 0.7),
        },
    }
    KBS.setdefault(tenant_id, {})[kb_id] = kb
    return _created({"id": kb_id})


@app.route("/KnowledgeBase/Tenants/<tenant_id>/KnowledgeBases/<kb_id>", methods=["GET"])
def kb_get(tenant_id, kb_id):
    kb = KBS.get(tenant_id, {}).get(kb_id)
    if not kb:
        return _not_found(f"KnowledgeBase {kb_id} not found")
    return _ok(kb)


@app.route("/KnowledgeBase/Tenants/<tenant_id>/KnowledgeBases/<kb_id>", methods=["PUT"])
def kb_update(tenant_id, kb_id):
    kb = KBS.get(tenant_id, {}).get(kb_id)
    if not kb:
        return _not_found(f"KnowledgeBase {kb_id} not found")
    body = request.get_json(force=True, silent=True) or {}
    for field in ("name", "description"):
        if field in body:
            kb[field] = body[field]
    if "config" in body:
        kb.setdefault("config", {}).update(body["config"])
    return _ok(kb)


@app.route("/KnowledgeBase/Tenants/<tenant_id>/KnowledgeBases/<kb_id>", methods=["DELETE"])
def kb_delete(tenant_id, kb_id):
    tenant_kbs = KBS.get(tenant_id, {})
    if kb_id not in tenant_kbs:
        return _not_found(f"KnowledgeBase {kb_id} not found")
    tenant_kbs.pop(kb_id)
    MAPPINGS.get(tenant_id, {}).pop(kb_id, None)
    FILES.get(tenant_id, {}).pop(kb_id, None)
    return _ok(None)


# ── Mappings ──────────────────────────────────────────────────────────────

@app.route("/KnowledgeBase/Tenants/<tenant_id>/KnowledgeBases/<kb_id>/Mappings", methods=["GET"])
def mapping_list(tenant_id, kb_id):
    items = list(MAPPINGS.get(tenant_id, {}).get(kb_id, {}).values())
    paged, page, size, total = _paginate(items)
    return _ok({"items": paged, "total": total, "page": page, "size": size})


@app.route("/KnowledgeBase/Tenants/<tenant_id>/KnowledgeBases/<kb_id>/Mappings", methods=["POST"])
def mapping_create(tenant_id, kb_id):
    if kb_id not in KBS.get(tenant_id, {}):
        return _not_found(f"KnowledgeBase {kb_id} not found")
    body = request.get_json(force=True, silent=True) or {}
    mid = _new_id()
    m = {
        "id":       mid,
        "kb_id":    kb_id,
        "src_dir":  body.get("src_dir", ""),
        "fs_name":  body.get("fs_name", ""),
        "fs_id":    body.get("fs_id", ""),
        "state":    1,
    }
    MAPPINGS.setdefault(tenant_id, {}).setdefault(kb_id, {})[mid] = m
    return _created({"id": mid})


@app.route("/KnowledgeBase/Tenants/<tenant_id>/KnowledgeBases/<kb_id>/Mappings/<mapping_id>", methods=["DELETE"])
def mapping_delete(tenant_id, kb_id, mapping_id):
    kb_mappings = MAPPINGS.get(tenant_id, {}).get(kb_id, {})
    if mapping_id not in kb_mappings:
        return _not_found(f"Mapping {mapping_id} not found")
    kb_mappings.pop(mapping_id)
    return _ok(None)


# ── Files ─────────────────────────────────────────────────────────────────

@app.route("/KnowledgeBase/Tenants/<tenant_id>/KnowledgeBases/<kb_id>/Files", methods=["GET"])
def file_list(tenant_id, kb_id):
    items = list(FILES.get(tenant_id, {}).get(kb_id, {}).values())
    paged, page, size, total = _paginate(items)
    return _ok({"items": paged, "total": total, "page": page, "size": size})


@app.route("/KnowledgeBase/Tenants/<tenant_id>/KnowledgeBases/<kb_id>/Files", methods=["POST"])
def file_upload(tenant_id, kb_id):
    if kb_id not in KBS.get(tenant_id, {}):
        return _not_found(f"KnowledgeBase {kb_id} not found")
    now = int(time.time())
    if request.content_type and request.content_type.startswith("multipart/"):
        names = [f.filename for f in request.files.getlist("files")]
    else:
        body = request.get_json(silent=True) or {}
        names = body.get("file_names") or [body.get("name", "mock.txt")]
    uploaded = []
    for name in names:
        fid = _new_id()
        FILES.setdefault(tenant_id, {}).setdefault(kb_id, {})[fid] = {
            "id": fid, "kb_id": kb_id, "name": name,
            "size": 100, "uploaded_at": now,
        }
        uploaded.append({"id": fid, "name": name})
    return _created({"uploaded": uploaded, "total": len(uploaded)})


@app.route("/KnowledgeBase/Tenants/<tenant_id>/KnowledgeBases/<kb_id>/Files/<file_id>", methods=["DELETE"])
def file_delete(tenant_id, kb_id, file_id):
    kb_files = FILES.get(tenant_id, {}).get(kb_id, {})
    if file_id not in kb_files:
        return _not_found(f"File {file_id} not found")
    kb_files.pop(file_id)
    return _ok(None)


# ── Conversations ─────────────────────────────────────────────────────────

@app.route("/KnowledgeBase/Tenants/<tenant_id>/Conversations", methods=["GET"])
def conv_list(tenant_id):
    items = list(CONVERSATIONS.get(tenant_id, {}).values())
    paged, page, size, total = _paginate(items)
    return _ok({"items": paged, "total": total, "page": page, "size": size})


@app.route("/KnowledgeBase/Tenants/<tenant_id>/Conversations", methods=["POST"])
def conv_create(tenant_id):
    body = request.get_json(force=True, silent=True) or {}
    thread_id = body.get("thread_id") or _new_id()
    conv = {
        "id":         thread_id,
        "thread_id":  thread_id,
        "query":      body.get("query", ""),
        "answer":     "mock answer",
        "created_by": _auth()["user_id"],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    CONVERSATIONS.setdefault(tenant_id, {})[thread_id] = conv
    return _created({"id": thread_id, "answer": "mock answer"})


@app.route("/KnowledgeBase/Tenants/<tenant_id>/Conversations/<thread_id>", methods=["GET"])
def conv_get(tenant_id, thread_id):
    conv = CONVERSATIONS.get(tenant_id, {}).get(thread_id)
    if not conv:
        return _not_found(f"Conversation {thread_id} not found")
    return _ok(conv)


@app.route("/KnowledgeBase/Tenants/<tenant_id>/Conversations/<thread_id>", methods=["DELETE"])
def conv_delete(tenant_id, thread_id):
    tenant_convs = CONVERSATIONS.get(tenant_id, {})
    if thread_id not in tenant_convs:
        return _not_found(f"Conversation {thread_id} not found")
    tenant_convs.pop(thread_id)
    return _ok(None)


@app.route("/KnowledgeBase/Tenants/<tenant_id>/Conversations/<thread_id>/Stop", methods=["POST"])
def conv_stop(tenant_id, thread_id):
    return _ok({"stopped": thread_id})


# ── ModelConfigs (system-level) ───────────────────────────────────────────

@app.route("/KnowledgeBase/Tenants/System/ModelConfigs", methods=["GET"])
def model_list():
    items = list(MODELS.values())
    paged, page, size, total = _paginate(items)
    return _ok({"items": paged, "total": total, "page": page, "size": size})


@app.route("/KnowledgeBase/Tenants/System/ModelConfigs", methods=["POST"])
def model_create():
    body = request.get_json(force=True, silent=True) or {}
    mid = _new_id()
    MODELS[mid] = {
        "id":         mid,
        "model_type": body.get("model_type", "LLM"),
        "model_name": body.get("model_name", ""),
        "model_id":   body.get("model_id", ""),
        "api_url":    body.get("api_url", ""),
        "provider":   body.get("provider"),
    }
    return _created({"id": mid})


@app.route("/KnowledgeBase/Tenants/System/ModelConfigs/<model_id>", methods=["GET"])
def model_get(model_id):
    m = MODELS.get(model_id)
    if not m:
        return _not_found(f"ModelConfig {model_id} not found")
    return _ok(m)


@app.route("/KnowledgeBase/Tenants/System/ModelConfigs/<model_id>", methods=["PUT"])
def model_update(model_id):
    m = MODELS.get(model_id)
    if not m:
        return _not_found(f"ModelConfig {model_id} not found")
    body = request.get_json(force=True, silent=True) or {}
    for field in ("model_type", "model_name", "model_id", "api_url", "provider"):
        if field in body:
            m[field] = body[field]
    return _ok(m)


@app.route("/KnowledgeBase/Tenants/System/ModelConfigs/<model_id>", methods=["DELETE"])
def model_delete(model_id):
    if model_id not in MODELS:
        return _not_found(f"ModelConfig {model_id} not found")
    MODELS.pop(model_id)
    return _ok(None)


# ── Prompts (system-level) ────────────────────────────────────────────────

@app.route("/KnowledgeBase/Tenants/System/Prompts", methods=["GET"])
def prompt_list():
    items = list(PROMPTS.values())
    paged, page, size, total = _paginate(items)
    return _ok({"items": paged, "total": total, "page": page, "size": size})


@app.route("/KnowledgeBase/Tenants/System/Prompts", methods=["POST"])
def prompt_create():
    body = request.get_json(force=True, silent=True) or {}
    pid = _new_id()
    PROMPTS[pid] = {
        "id":          pid,
        "title":       body.get("title", ""),
        "description": body.get("description", ""),
        "mode":        body.get("mode", "NAIVE"),
        "prompts":     body.get("prompts", {}),
        "created_at":  time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return _created({"id": pid})


@app.route("/KnowledgeBase/Tenants/System/Prompts/<prompt_id>", methods=["GET"])
def prompt_get(prompt_id):
    p = PROMPTS.get(prompt_id)
    if not p:
        return _not_found(f"Prompt {prompt_id} not found")
    return _ok(p)


@app.route("/KnowledgeBase/Tenants/System/Prompts/<prompt_id>", methods=["PUT"])
def prompt_update(prompt_id):
    p = PROMPTS.get(prompt_id)
    if not p:
        return _not_found(f"Prompt {prompt_id} not found")
    body = request.get_json(force=True, silent=True) or {}
    for field in ("title", "description", "prompts", "mode"):
        if field in body:
            p[field] = body[field]
    return _ok(p)


@app.route("/KnowledgeBase/Tenants/System/Prompts/<prompt_id>", methods=["DELETE"])
def prompt_delete(prompt_id):
    if prompt_id not in PROMPTS:
        return _not_found(f"Prompt {prompt_id} not found")
    PROMPTS.pop(prompt_id)
    return _ok(None)


# ── JargonLibraries ───────────────────────────────────────────────────────

@app.route("/KnowledgeBase/Tenants/<tenant_id>/JargonLibraries", methods=["GET"])
def jargon_lib_list(tenant_id):
    items = list(JARGON_LIBS.get(tenant_id, {}).values())
    paged, page, size, total = _paginate(items)
    return _ok({"items": paged, "total": total, "page": page, "size": size})


@app.route("/KnowledgeBase/Tenants/<tenant_id>/JargonLibraries", methods=["POST"])
def jargon_lib_create(tenant_id):
    body = request.get_json(force=True, silent=True) or {}
    name = body.get("name") or f"lib-{_new_id()}"
    lib = {
        "id":          _new_id(),
        "name":        name,
        "description": body.get("description", ""),
        "created_at":  time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    JARGON_LIBS.setdefault(tenant_id, {})[name] = lib
    return _created({"id": lib["id"], "name": name})


@app.route("/KnowledgeBase/Tenants/<tenant_id>/JargonLibraries/<lib_name>", methods=["GET"])
def jargon_lib_get(tenant_id, lib_name):
    lib = JARGON_LIBS.get(tenant_id, {}).get(lib_name)
    if not lib:
        return _not_found(f"JargonLibrary {lib_name} not found")
    return _ok(lib)


@app.route("/KnowledgeBase/Tenants/<tenant_id>/JargonLibraries/<lib_name>", methods=["DELETE"])
def jargon_lib_delete(tenant_id, lib_name):
    tenant_libs = JARGON_LIBS.get(tenant_id, {})
    if lib_name not in tenant_libs:
        return _not_found(f"JargonLibrary {lib_name} not found")
    tenant_libs.pop(lib_name)
    JARGONS.get(tenant_id, {}).pop(lib_name, None)
    return _ok(None)


# ── Jargons ───────────────────────────────────────────────────────────────

@app.route("/KnowledgeBase/Tenants/<tenant_id>/JargonLibraries/<lib_name>/Jargons", methods=["GET"])
def jargon_list(tenant_id, lib_name):
    items = list(JARGONS.get(tenant_id, {}).get(lib_name, {}).values())
    paged, page, size, total = _paginate(items)
    return _ok({"items": paged, "total": total, "page": page, "size": size})


@app.route("/KnowledgeBase/Tenants/<tenant_id>/JargonLibraries/<lib_name>/Jargons", methods=["POST"])
def jargon_create(tenant_id, lib_name):
    body = request.get_json(force=True, silent=True) or {}
    name = body.get("name") or f"j-{_new_id()}"
    jargon = {
        "id":          _new_id(),
        "name":        name,
        "lib_name":    lib_name,
        "description": body.get("description", ""),
        "target_term": body.get("target_term", []),
        "created_at":  int(time.time()),
    }
    JARGONS.setdefault(tenant_id, {}).setdefault(lib_name, {})[name] = jargon
    return _created({"id": jargon["id"], "name": name})


@app.route("/KnowledgeBase/Tenants/<tenant_id>/JargonLibraries/<lib_name>/Jargons/<jargon_name>", methods=["GET"])
def jargon_get(tenant_id, lib_name, jargon_name):
    j = JARGONS.get(tenant_id, {}).get(lib_name, {}).get(jargon_name)
    if not j:
        return _not_found(f"Jargon {jargon_name} not found")
    return _ok(j)


@app.route("/KnowledgeBase/Tenants/<tenant_id>/JargonLibraries/<lib_name>/Jargons/<jargon_name>", methods=["PUT"])
def jargon_update(tenant_id, lib_name, jargon_name):
    j = JARGONS.get(tenant_id, {}).get(lib_name, {}).get(jargon_name)
    if not j:
        return _not_found(f"Jargon {jargon_name} not found")
    body = request.get_json(force=True, silent=True) or {}
    for field in ("description", "target_term"):
        if field in body:
            j[field] = body[field]
    return _ok(j)


@app.route("/KnowledgeBase/Tenants/<tenant_id>/JargonLibraries/<lib_name>/Jargons/<jargon_name>", methods=["DELETE"])
def jargon_delete(tenant_id, lib_name, jargon_name):
    lib_jargons = JARGONS.get(tenant_id, {}).get(lib_name, {})
    if jargon_name not in lib_jargons:
        return _not_found(f"Jargon {jargon_name} not found")
    lib_jargons.pop(jargon_name)
    return _ok(None)


# ── FusionSearch ──────────────────────────────────────────────────────────

@app.route("/KnowledgeBase/Tenants/<tenant_id>/Action/FusionSearch", methods=["POST"])
def fusion_search(tenant_id):
    return _ok({
        "items": [
            {"id": 0, "score": 1.0, "text": "mock result 1"},
            {"id": 1, "score": 0.9, "text": "mock result 2"},
        ],
        "total": 2,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
