"""Mock Knowledge Base backend for IAM end-to-end testing.

Contract:
  - Creation endpoints (/add, /upload, /start, ...) return 201 with a JSON
    body whose ID field matches the paired resource_patterns.id_field.
    resource-sync's ext_proc handler extracts this ID to write resource_acl.
  - Modify / remove endpoints return 200 with {"status": "ok", "<id>": "..."}.
  - List endpoints honor X-Allowed-Ids (comma-separated) injected by
    resource-sync: present but empty → empty list; present and populated →
    filter to those IDs; absent → no filter (admin bypass).
  - Every response carries X-Debug-* headers echoing the X-Auth-* /
    X-Allowed-Ids values seen, so tests can assert the injection without
    scraping pod logs.
  - Pre-seeded with 3 knowledge bases (KB1, KB2, KB3) for baseline tests.

Storage is in-memory and resets on pod restart.
"""
from flask import Flask, request, jsonify, Response
import uuid
import time

app = Flask(__name__)

# ── In-memory stores ──────────────────────────────────────────────────────
KBS = {}             # KDSID -> kb metadata
MAPPINGS = {}        # CHANNELID -> mapping (has KDSID)
FILES = {}           # file_id -> metadata (has kbs_id)
FILESYSTEMS = {}     # FSID -> fs metadata
MODELS = {}          # ModelAPIID -> model config
PROMPTS = {}         # prompt_id -> prompt
JARGON_LIBS = {}     # JARGON_LIB_NAME -> library
JARGONS = {}         # (lib, name) -> jargon
CONVERSATIONS = {}   # conv_id -> conversation (has kbs_id)


def _seed():
    for kid in ("KB1", "KB2", "KB3"):
        KBS[kid] = {
            "KDSID": kid,
            "NAME": f"Seed {kid}",
            "DESCRIPTION": f"pre-seeded {kid}",
            "CHUNKTOKENNUM": 1024,
            "CHUNKOVERLAPNUM": 128,
            "EMBEDDINGMODEL": "default",
            "created_by": "system",
            "created_at": 0,
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
    """Return None when the header is absent (admin bypass / full pass-through).
    Return a set (possibly empty) otherwise."""
    header = request.headers.get("X-Allowed-Ids")
    if header is None:
        return None
    if header == "":
        return set()
    return {x.strip() for x in header.split(",") if x.strip()}


def _paginate(items, default_size=50):
    body = request.get_json(silent=True) or {}
    page = int(request.args.get("PAGEINDEX") or body.get("PAGEINDEX") or 1)
    size = int(request.args.get("PAGESIZE") or body.get("PAGESIZE") or default_size)
    start = max((page - 1) * size, 0)
    end = start + size
    return items[start:end], page, size


def _filter_by_allowed(items, id_key):
    allowed = _allowed_ids()
    if allowed is None:
        return items
    return [x for x in items if str(x.get(id_key)) in allowed]


def _debug_headers():
    return {
        "X-Debug-User-Id":       request.headers.get("X-Auth-User-Id", ""),
        "X-Debug-Tenant":        request.headers.get("X-Auth-Tenant", ""),
        "X-Debug-Groups":        request.headers.get("X-Auth-Groups", ""),
        "X-Debug-Allowed-Ids":   request.headers.get("X-Allowed-Ids", ""),
        "X-Debug-Allowed-Total": request.headers.get("X-Allowed-Total", ""),
    }


def _json(payload, status=200):
    resp = jsonify(payload)
    resp.status_code = status
    for k, v in _debug_headers().items():
        resp.headers[k] = v
    return resp


def _new_id():
    return str(uuid.uuid4())[:8]


def _not_found(id_field, value):
    return _json({"status": "error", "reason": f"{id_field}={value} not found"}, 404)


@app.route("/health")
def health():
    return _json({"status": "ok", "service": "mock-kb"})


# ── Knowledge Bases ───────────────────────────────────────────────────────

@app.route("/kb/knowledge_bases/page", methods=["GET", "POST"])
def kb_page():
    items = _filter_by_allowed(list(KBS.values()), "KDSID")
    paged, page, size = _paginate(items)
    return _json({"items": paged, "total": len(items), "page": page, "size": size})


@app.route("/kb/knowledge_bases/count", methods=["GET"])
def kb_count():
    items = _filter_by_allowed(list(KBS.values()), "KDSID")
    return _json({"count": len(items)})


@app.route("/kb/knowledge_bases", methods=["GET"])
def kb_get_single():
    """api.md: query ?kbs_id=... → returns {"data": {...}}; no id → list filtered by X-Allowed-Ids."""
    kdsid = request.args.get("kbs_id") or (request.get_json(silent=True) or {}).get("kbs_id")
    if kdsid:
        if kdsid in KBS:
            return _json({"data": KBS[kdsid]})
        return _not_found("kbs_id", kdsid)
    items = _filter_by_allowed(list(KBS.values()), "KDSID")
    return _json({"data": items, "total": len(items)})


@app.route("/kb/knowledge_bases/add", methods=["POST"])
def kb_add():
    """api.md: response is {"data": {"KDSID": str}}. resource-sync extracts data.KDSID."""
    body = request.get_json(force=True, silent=True) or {}
    # Accept legacy KDSID too so tests can pre-pick an id; prefer new name.
    kdsid = body.get("kbs_id") or body.get("KDSID") or _new_id()
    kb = {
        "KDSID":           kdsid,
        "NAME":            body.get("NAME", body.get("name", f"kb-{kdsid}")),
        "DESCRIPTION":     body.get("DESCRIPTION", body.get("description", "")),
        "CHUNKTOKENNUM":   body.get("CHUNKTOKENNUM", body.get("chunk_token_num", 1024)),
        "CHUNKOVERLAPNUM": body.get("CHUNKOVERLAPNUM", body.get("chunk_overlap_num", 128)),
        "EMBEDDINGMODEL":  body.get("EMBEDDINGMODEL", body.get("embedding_model", "default")),
        "created_by":      _auth()["user_id"],
        "created_at":      int(time.time()),
    }
    KBS[kdsid] = kb
    return _json({"data": kb}, 201)


@app.route("/kb/knowledge_bases/modify", methods=["POST"])
def kb_modify():
    body = request.get_json(force=True, silent=True) or {}
    kdsid = body.get("kbs_id")
    if not kdsid or kdsid not in KBS:
        return _not_found("kbs_id", kdsid)
    KBS[kdsid].update({k: v for k, v in body.items() if k != "kbs_id"})
    return _json({"status": "ok", "data": KBS[kdsid]})


@app.route("/kb/knowledge_bases/remove", methods=["POST"])
def kb_remove():
    body = request.get_json(force=True, silent=True) or {}
    kdsid = body.get("kbs_id")
    if not kdsid or kdsid not in KBS:
        return _not_found("kbs_id", kdsid)
    KBS.pop(kdsid, None)
    return _json({"status": "ok", "data": {"KDSID": kdsid}})


# ── Directory Mappings ────────────────────────────────────────────────────

@app.route("/kb/knowledge_bases/mappings", methods=["GET"])
def kb_mappings_list():
    kdsid = request.args.get("KDSID") or (request.get_json(silent=True) or {}).get("KDSID")
    items = list(MAPPINGS.values())
    if kdsid:
        items = [m for m in items if str(m.get("KDSID")) == str(kdsid)]
    return _json({"items": items, "total": len(items)})


@app.route("/kb/knowledge_bases/mappings/count", methods=["GET"])
def kb_mappings_count():
    kdsid = request.args.get("KDSID")
    items = list(MAPPINGS.values())
    if kdsid:
        items = [m for m in items if str(m.get("KDSID")) == str(kdsid)]
    return _json({"count": len(items)})


@app.route("/kb/knowledge_bases/mappings/add", methods=["POST"])
def kb_mappings_add():
    body = request.get_json(force=True, silent=True) or {}
    kdsid = body.get("KDSID")
    if not kdsid or kdsid not in KBS:
        return _not_found("KDSID", kdsid)
    cid = _new_id()
    m = {
        "CHANNELID":   cid,
        "KDSID":       kdsid,
        "SRCDIR":      body.get("SRCDIR", ""),
        "FSNAME":      body.get("FSNAME", ""),
        "FSID":        body.get("FSID", ""),
        "CHANNELNAME": body.get("CHANNELNAME", f"ch-{cid}"),
    }
    MAPPINGS[cid] = m
    return _json(m, 201)


@app.route("/kb/knowledge_bases/mappings/remove", methods=["POST"])
def kb_mappings_remove():
    body = request.get_json(force=True, silent=True) or {}
    cid = body.get("CHANNELID")
    kdsid = body.get("KDSID")
    if cid not in MAPPINGS:
        return _not_found("CHANNELID", cid)
    MAPPINGS.pop(cid, None)
    return _json({"status": "ok", "CHANNELID": cid, "KDSID": kdsid})


# ── Files & Filesystem ────────────────────────────────────────────────────

@app.route("/kb/knowledge_bases/files/history", methods=["GET"])
def kb_files_history():
    kbs_id = request.args.get("kbs_id") or request.args.get("KDSID")
    items = list(FILES.values())
    if kbs_id:
        items = [f for f in items if str(f.get("kbs_id")) == str(kbs_id)]
    return _json({"items": items, "total": len(items)})


@app.route("/kb/knowledge_bases/files", methods=["GET"])
def kb_files_list():
    kbs_id = request.args.get("kbs_id") or request.args.get("KDSID")
    items = list(FILES.values())
    if kbs_id:
        items = [f for f in items if str(f.get("kbs_id")) == str(kbs_id)]
    return _json({"items": items, "total": len(items)})


@app.route("/kb/knowledge_bases/files/count", methods=["GET"])
def kb_files_count():
    kbs_id = request.args.get("kbs_id")
    items = list(FILES.values())
    if kbs_id:
        items = [f for f in items if str(f.get("kbs_id")) == str(kbs_id)]
    return _json({"count": len(items)})


@app.route("/kb/knowledge_bases/files/filesystem/add", methods=["POST"])
def fs_add():
    body = request.get_json(force=True, silent=True) or {}
    fs_id = _new_id()
    fs = {"FSID": fs_id, "FSNAME": body.get("FSNAME", f"fs-{fs_id}")}
    FILESYSTEMS[fs_id] = fs
    return _json(fs, 201)


@app.route("/kb/knowledge_bases/files/filesystem/remove", methods=["POST"])
def fs_remove():
    body = request.get_json(force=True, silent=True) or {}
    fs_id = body.get("FSID")
    if fs_id not in FILESYSTEMS:
        return _not_found("FSID", fs_id)
    FILESYSTEMS.pop(fs_id, None)
    return _json({"status": "ok", "FSID": fs_id})


@app.route("/kb/knowledge_bases/files/filesystem", methods=["GET"])
def fs_list():
    return _json({"items": list(FILESYSTEMS.values()), "total": len(FILESYSTEMS)})


@app.route("/kb/knowledge_bases/files/upload", methods=["POST"])
def file_upload():
    body = request.get_json(silent=True) or {}
    kbs_id = body.get("kbs_id")
    if not kbs_id or kbs_id not in KBS:
        return _not_found("kbs_id", kbs_id)
    fid = _new_id()
    FILES[fid] = {"file_id": fid, "kbs_id": kbs_id, "name": body.get("name", f"file-{fid}")}
    return _json({"status": "ok", "kbs_id": kbs_id, "file_id": fid}, 201)


@app.route("/kb/knowledge_bases/files/remove", methods=["POST"])
def file_remove():
    body = request.get_json(force=True, silent=True) or {}
    kbs_id = body.get("kbs_id")
    file_id = body.get("file_id")
    FILES.pop(file_id, None)
    return _json({"status": "ok", "kbs_id": kbs_id, "file_id": file_id})


@app.route("/kb/knowledge_bases/files/download", methods=["GET"])
def file_download():
    resp = Response(b"mock file content", mimetype="application/octet-stream")
    for k, v in _debug_headers().items():
        resp.headers[k] = v
    return resp


# ── Models ────────────────────────────────────────────────────────────────

@app.route("/kb/models/config", methods=["GET"])
def models_config():
    items = _filter_by_allowed(list(MODELS.values()), "ModelAPIID")
    return _json({"items": items, "total": len(items)})


@app.route("/kb/models/config/add", methods=["POST"])
def models_add():
    body = request.get_json(force=True, silent=True) or {}
    mid = _new_id()
    m = {"ModelAPIID": mid, **{k: v for k, v in body.items() if k != "ModelAPIID"}}
    MODELS[mid] = m
    return _json(m, 201)


@app.route("/kb/models/config/modify", methods=["POST"])
def models_modify():
    body = request.get_json(force=True, silent=True) or {}
    mid = body.get("ModelAPIID")
    if mid not in MODELS:
        return _not_found("ModelAPIID", mid)
    MODELS[mid].update({k: v for k, v in body.items() if k != "ModelAPIID"})
    return _json({"status": "ok", **MODELS[mid]})


@app.route("/kb/models/config/remove", methods=["POST"])
def models_remove():
    body = request.get_json(force=True, silent=True) or {}
    mid = body.get("ModelAPIID")
    if mid not in MODELS:
        return _not_found("ModelAPIID", mid)
    MODELS.pop(mid, None)
    return _json({"status": "ok", "ModelAPIID": mid})


@app.route("/kb/models/config/set", methods=["GET"])
def models_set():
    return _json({"status": "ok"})


# ── Prompts ───────────────────────────────────────────────────────────────

@app.route("/kb/prompts", methods=["GET"])
def prompts_get():
    items = _filter_by_allowed(list(PROMPTS.values()), "prompt_id")
    return _json({"items": items, "total": len(items)})


@app.route("/kb/prompts/add", methods=["POST"])
def prompts_add():
    body = request.get_json(force=True, silent=True) or {}
    pid = _new_id()
    p = {"prompt_id": pid, **{k: v for k, v in body.items() if k != "prompt_id"}}
    PROMPTS[pid] = p
    return _json(p, 201)


@app.route("/kb/prompts/modify", methods=["POST"])
def prompts_modify():
    body = request.get_json(force=True, silent=True) or {}
    pid = body.get("prompt_id")
    if pid not in PROMPTS:
        return _not_found("prompt_id", pid)
    PROMPTS[pid].update({k: v for k, v in body.items() if k != "prompt_id"})
    return _json({"status": "ok", **PROMPTS[pid]})


@app.route("/kb/prompts/remove", methods=["POST"])
def prompts_remove():
    body = request.get_json(force=True, silent=True) or {}
    pid = body.get("prompt_id")
    if pid not in PROMPTS:
        return _not_found("prompt_id", pid)
    PROMPTS.pop(pid, None)
    return _json({"status": "ok", "prompt_id": pid})


@app.route("/kb/prompts/menu", methods=["GET"])
def prompts_menu():
    items = _filter_by_allowed(list(PROMPTS.values()), "prompt_id")
    return _json({"items": [{"id": p.get("prompt_id"), "name": p.get("name", "")} for p in items]})


@app.route("/kb/prompts/page", methods=["POST"])
def prompts_page():
    items = _filter_by_allowed(list(PROMPTS.values()), "prompt_id")
    paged, page, size = _paginate(items)
    return _json({"items": paged, "total": len(items), "page": page, "size": size})


# ── Jargon Groups & Jargons ──────────────────────────────────────────────

@app.route("/kb/jargon_groups", methods=["GET"])
def jargon_groups_list():
    items = _filter_by_allowed(list(JARGON_LIBS.values()), "JARGON_LIB_NAME")
    return _json({"items": items, "total": len(items)})


@app.route("/kb/jargon_groups/add", methods=["POST"])
def jargon_groups_add():
    body = request.get_json(force=True, silent=True) or {}
    name = body.get("JARGON_LIB_NAME", f"lib-{_new_id()}")
    lib = {"JARGON_LIB_NAME": name, "DESCRIPTION": body.get("DESCRIPTION", "")}
    JARGON_LIBS[name] = lib
    return _json(lib, 201)


@app.route("/kb/jargon_groups/remove", methods=["POST"])
def jargon_groups_remove():
    body = request.get_json(force=True, silent=True) or {}
    name = body.get("JARGON_LIB_NAME")
    if name not in JARGON_LIBS:
        return _not_found("JARGON_LIB_NAME", name)
    JARGON_LIBS.pop(name, None)
    return _json({"status": "ok", "JARGON_LIB_NAME": name})


@app.route("/kb/jargon_groups/jargons", methods=["GET"])
def jargon_groups_jargons():
    lib = request.args.get("JARGON_LIB_NAME")
    items = [v for k, v in JARGONS.items() if k[0] == lib] if lib else list(JARGONS.values())
    return _json({"items": items, "total": len(items)})


@app.route("/kb/jargon_groups/knowledge_bases/add", methods=["POST"])
def jargon_kb_bind():
    return _json({"status": "bound"}, 201)


@app.route("/kb/jargon_groups/knowledge_bases/remove", methods=["POST"])
def jargon_kb_unbind():
    return _json({"status": "unbound"})


@app.route("/kb/jargons_groups/<jargon_lib_name>", methods=["GET"])
@app.route("/kb/jargon_groups/<jargon_lib_name>", methods=["GET"])
def jargons_in_lib(jargon_lib_name):
    items = [v for k, v in JARGONS.items() if k[0] == jargon_lib_name]
    return _json({"items": items, "total": len(items)})


@app.route("/kb/jargons/add", methods=["POST"])
def jargons_add():
    body = request.get_json(force=True, silent=True) or {}
    lib = body.get("JARGON_LIB_NAME", "default")
    name = body.get("JARGON_NAME", f"j-{_new_id()}")
    j = {**body, "JARGON_NAME": name, "JARGON_LIB_NAME": lib}
    JARGONS[(lib, name)] = j
    return _json(j, 201)


@app.route("/kb/jargons/modify", methods=["POST"])
def jargons_modify():
    body = request.get_json(force=True, silent=True) or {}
    key = (body.get("JARGON_LIB_NAME"), body.get("JARGON_NAME"))
    if key not in JARGONS:
        return _not_found("JARGON_NAME", key[1])
    JARGONS[key].update(body)
    return _json({"status": "ok", **JARGONS[key]})


@app.route("/kb/jargons/remove", methods=["POST"])
def jargons_remove():
    body = request.get_json(force=True, silent=True) or {}
    key = (body.get("JARGON_LIB_NAME"), body.get("JARGON_NAME"))
    if key not in JARGONS:
        return _not_found("JARGON_NAME", key[1])
    JARGONS.pop(key, None)
    return _json({"status": "ok", "JARGON_LIB_NAME": key[0], "JARGON_NAME": key[1]})


@app.route("/kb/knowledge_bases/<kb_name>/jargon", methods=["GET"])
def kb_jargon_query(kb_name):
    return _json({"kb_name": kb_name, "jargon_libs": list(JARGON_LIBS.keys())})


@app.route("/kb/jargon_groups/version/<jargon_lib_name>", methods=["GET"])
def jargon_lib_version(jargon_lib_name):
    return _json({"JARGON_LIB_NAME": jargon_lib_name, "version": "v1.0"})


# ── Conversations (Q&A) ───────────────────────────────────────────────────

@app.route("/kb/conversations", methods=["GET"])
def conv_list():
    items = _filter_by_allowed(list(CONVERSATIONS.values()), "conv_id")
    return _json({"items": items, "total": len(items)})


@app.route("/kb/conversations/start", methods=["POST"])
def conv_start():
    body = request.get_json(force=True, silent=True) or {}
    kbs_id = body.get("kbs_id")
    if not kbs_id or kbs_id not in KBS:
        return _not_found("kbs_id", kbs_id)
    cid = _new_id()
    CONVERSATIONS[cid] = {
        "conv_id":    cid,
        "kbs_id":     kbs_id,
        "question":   body.get("question", ""),
        "created_by": _auth()["user_id"],
    }
    return _json({"status": "ok", "conv_id": cid, "kbs_id": kbs_id, "answer": "mock answer"}, 201)


@app.route("/kb/conversations/stop", methods=["GET"])
def conv_stop():
    conv_id = request.args.get("conv_id")
    return _json({"status": "stopped", "conv_id": conv_id})


@app.route("/kb/conversations/images/generate", methods=["POST"])
def conv_images_generate():
    body = request.get_json(silent=True) or {}
    return _json({
        "kbs_id":    body.get("kbs_id", ""),
        "image_url": f"/kb/conversations/images/download?kbs_id={body.get('kbs_id','')}&id=mock",
    })


@app.route("/kb/conversations/images/download", methods=["GET"])
def conv_images_download():
    resp = Response(b"\x89PNG\r\n\x1a\n", mimetype="image/png")
    for k, v in _debug_headers().items():
        resp.headers[k] = v
    return resp


@app.route("/kb/conversations/query/batch", methods=["GET"])
def conv_query_batch():
    items = _filter_by_allowed(list(CONVERSATIONS.values()), "conv_id")
    return _json({"items": items, "total": len(items)})


@app.route("/kb/conversations/query/single", methods=["GET"])
def conv_query_single():
    conv_id = request.args.get("conv_id")
    if conv_id not in CONVERSATIONS:
        return _not_found("conv_id", conv_id)
    return _json(CONVERSATIONS[conv_id])


@app.route("/kb/conversations/remove", methods=["POST"])
def conv_remove():
    body = request.get_json(force=True, silent=True) or {}
    conv_id = body.get("conv_id")
    if conv_id not in CONVERSATIONS:
        return _not_found("conv_id", conv_id)
    CONVERSATIONS.pop(conv_id, None)
    return _json({"status": "ok", "conv_id": conv_id})


# ── Retrieval ─────────────────────────────────────────────────────────────

@app.route("/kb/retrieval/fusion_search", methods=["POST"])
def retrieval_fusion():
    body = request.get_json(silent=True) or {}
    return _json({"results": [], "total": 0, "kbs_id": body.get("kbs_id", "")})


# ── Catch-all (debug) ─────────────────────────────────────────────────────

@app.route("/kb/<path:sub>", methods=["GET", "POST", "PUT", "DELETE"])
def kb_catch_all(sub):
    return _json({
        "path":   f"/kb/{sub}",
        "method": request.method,
        "_auth":  _auth(),
        "body":   request.get_json(silent=True) or {},
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
