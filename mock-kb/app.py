"""Mock Knowledge Base backend — fully aligned with diagrams/api-specs/knowledgebase/api.md.

Field name convention:
  - REQUEST body / query: snake_case per api.md (kbs_id, kbs_dm_id, src_dir, etc.)
  - RESPONSE body inside `data.{...}`: keeps legacy UPPERCASE keys per api.md
    (KDSID, KDSNAME, CHANNELID, ...) so existing IAM resource_pattern's
    response_id_field=data.KDSID still extracts the new resource ID for ext_proc.

Auth contract (still preserved):
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

# ── In-memory stores (keys are the spec-aligned ID strings) ──────────────
KBS = {}             # kbs_id   -> kb metadata
MAPPINGS = {}        # kbs_dm_id -> mapping (has kbs_id)
FILES = {}           # file_id  -> metadata (has kbs_id)
FILESYSTEMS = {}     # fs_id    -> fs metadata
MODELS = {}          # model id -> model config
PROMPTS = {}         # prompt id -> prompt
JARGON_LIBS = {}     # jargon_lib_name -> library (has jargon_lib_id)
JARGONS = {}         # (jargon_lib_name, jargon_name) -> jargon
CONVERSATIONS = {}   # thread_id -> conversation


def _seed():
    for kid in ("KB1", "KB2", "KB3"):
        KBS[kid] = {
            "KDSID":          kid,
            "KDSNAME":        f"Seed {kid}",
            "DESCRIPTION":    f"pre-seeded {kid}",
            "VECTORCOLLNAME": f"coll_{kid.lower()}",
            "KMSCONFIGSTR": {
                "chunk_token_num":   1024,
                "chunk_overlap_num": 128,
                "embedding_model":   "default",
            },
            "STATE":          1,
            "PERSONSPACE":    0,
            "CREATE_TIME":    "2026-01-01 00:00:00",
            "created_by":     "system",
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


def _paginate(items, default_size=20):
    body = request.get_json(silent=True) or {}
    page = int(request.args.get("page_index") or request.args.get("page")
               or body.get("page_index") or body.get("page") or 1)
    size = int(request.args.get("page_size") or request.args.get("size")
               or body.get("page_size") or body.get("size") or default_size)
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


def _result_ok(description="success"):
    return {"code": 0, "description": description, "suggestion": ""}


def _envelope(data, code=200, description="success"):
    """api.md envelope shape: {data, result}."""
    resp = jsonify({"data": data, "result": _result_ok(description)})
    resp.status_code = code
    for k, v in _debug_headers().items():
        resp.headers[k] = v
    return resp


def _msg_envelope(data, code_status=200, message="success", code=0):
    """Newer api.md envelope: {code, message, data}."""
    resp = jsonify({"code": code, "message": message, "data": data})
    resp.status_code = code_status
    for k, v in _debug_headers().items():
        resp.headers[k] = v
    return resp


def _new_id():
    return str(uuid.uuid4())[:8]


def _not_found(field, value):
    resp = jsonify({"code": 404, "message": f"{field}={value} not found", "data": None})
    resp.status_code = 404
    for k, v in _debug_headers().items():
        resp.headers[k] = v
    return resp


@app.route("/health")
def health():
    return _msg_envelope({"service": "mock-kb"}, message="ok")


# ── (1) Knowledge Base Management ────────────────────────────────────────

@app.route("/kb/knowledge_bases/page", methods=["GET", "POST"])
def kb_page():
    """Spec: paginated list. Body: {page_index, page_size}."""
    items = _filter_by_allowed(list(KBS.values()), "KDSID")
    paged, _, _ = _paginate(items)
    return _envelope(paged)


@app.route("/kb/knowledge_bases/count", methods=["GET"])
def kb_count():
    items = _filter_by_allowed(list(KBS.values()), "KDSID")
    return _envelope({"count": len(items)})


@app.route("/kb/knowledge_bases", methods=["GET"])
def kb_get_single():
    """Spec: query ?kbs_id=... → single KB. List mode (no kbs_id) is mock-only."""
    kbs_id = (request.args.get("kbs_id")
              or (request.get_json(silent=True) or {}).get("kbs_id"))
    if kbs_id:
        if kbs_id in KBS:
            return _envelope(KBS[kbs_id])
        return _not_found("kbs_id", kbs_id)
    items = _filter_by_allowed(list(KBS.values()), "KDSID")
    return _envelope(items)


@app.route("/kb/knowledge_bases/add", methods=["POST"])
def kb_add():
    """Spec: body has name/description/embedding_model/etc.
    Response: {data: {KDSID: str}, result: {...}}."""
    body = request.get_json(force=True, silent=True) or {}
    kbs_id = body.get("kbs_id") or _new_id()
    kb = {
        "KDSID":          kbs_id,
        "KDSNAME":        body.get("name", f"kb-{kbs_id}"),
        "DESCRIPTION":    body.get("description", ""),
        "VECTORCOLLNAME": f"coll_{kbs_id}",
        "KMSCONFIGSTR": {
            "chunk_token_num":   body.get("chunk_token_num", 1024),
            "chunk_overlap_num": body.get("chunk_overlap_num", 128),
            "embedding_model":   body.get("embedding_model", "default"),
        },
        "STATE":          1,
        "PERSONSPACE":    body.get("is_personal", 0),
        "CREATE_TIME":    time.strftime("%Y-%m-%d %H:%M:%S"),
        "created_by":     _auth()["user_id"],
        "topk":           body.get("topk", 10),
        "similarity":     body.get("similarity", 0.7),
        "smartsplit":     body.get("smartsplit", 1),
    }
    KBS[kbs_id] = kb
    return _envelope({"KDSID": kbs_id}, code=201)


@app.route("/kb/knowledge_bases/modify", methods=["POST"])
def kb_modify():
    body = request.get_json(force=True, silent=True) or {}
    kbs_id = body.get("kbs_id")
    if not kbs_id or kbs_id not in KBS:
        return _not_found("kbs_id", kbs_id)
    kb = KBS[kbs_id]
    if "name" in body:               kb["KDSNAME"] = body["name"]
    if "description" in body:        kb["DESCRIPTION"] = body["description"]
    if "embedding_model" in body:    kb["KMSCONFIGSTR"]["embedding_model"] = body["embedding_model"]
    if "chunk_token_num" in body:    kb["KMSCONFIGSTR"]["chunk_token_num"] = body["chunk_token_num"]
    if "chunk_overlap_num" in body:  kb["KMSCONFIGSTR"]["chunk_overlap_num"] = body["chunk_overlap_num"]
    if "is_personal" in body:        kb["PERSONSPACE"] = body["is_personal"]
    return _envelope(None)


@app.route("/kb/knowledge_bases/remove", methods=["POST"])
def kb_remove():
    body = request.get_json(force=True, silent=True) or {}
    kbs_id = body.get("kbs_id")
    if not kbs_id or kbs_id not in KBS:
        return _not_found("kbs_id", kbs_id)
    KBS.pop(kbs_id, None)
    return _envelope(None)


# ── Directory Mappings ────────────────────────────────────────────────────

@app.route("/kb/knowledge_bases/mappings", methods=["GET"])
def kb_mappings_list():
    """Spec body: kbs_id, page_index, page_size."""
    kbs_id = (request.args.get("kbs_id")
              or (request.get_json(silent=True) or {}).get("kbs_id"))
    items = list(MAPPINGS.values())
    if kbs_id:
        items = [m for m in items if str(m.get("DSTKDSID")) == str(kbs_id)]
    paged, _, _ = _paginate(items)
    return _envelope(paged)


@app.route("/kb/knowledge_bases/mappings/count", methods=["GET"])
def kb_mappings_count():
    kbs_id = (request.args.get("kbs_id")
              or (request.get_json(silent=True) or {}).get("kbs_id"))
    items = list(MAPPINGS.values())
    if kbs_id:
        items = [m for m in items if str(m.get("DSTKDSID")) == str(kbs_id)]
    return _envelope({"count": len(items)})


@app.route("/kb/knowledge_bases/mappings/add", methods=["POST"])
def kb_mappings_add():
    """Spec body: kbs_dm_id, kbs_id, src_dir, fs_name, fs_id, channel_name."""
    body = request.get_json(force=True, silent=True) or {}
    kbs_id = body.get("kbs_id")
    if not kbs_id or kbs_id not in KBS:
        return _not_found("kbs_id", kbs_id)
    kbs_dm_id = body.get("kbs_dm_id") or _new_id()
    m = {
        "CHANNELID":   kbs_dm_id,
        "FSID":        body.get("fs_id", ""),
        "FSNAME":      body.get("fs_name", ""),
        "SRCDIR":      body.get("src_dir", ""),
        "STATE":       1,
        "DSTKDSID":    kbs_id,
        "DEFAULT":     False,
        # internal — for /remove lookup by composite
        "channel_name": body.get("channel_name", f"ch-{kbs_dm_id}"),
    }
    MAPPINGS[kbs_dm_id] = m
    return _envelope({"CHANNELID": kbs_dm_id}, code=201)


@app.route("/kb/knowledge_bases/mappings/remove", methods=["POST"])
def kb_mappings_remove():
    """Spec body: kbs_id, kbs_dm_id."""
    body = request.get_json(force=True, silent=True) or {}
    kbs_dm_id = body.get("kbs_dm_id")
    if kbs_dm_id not in MAPPINGS:
        return _not_found("kbs_dm_id", kbs_dm_id)
    MAPPINGS.pop(kbs_dm_id, None)
    return _envelope(None)


# ── (2) Files ────────────────────────────────────────────────────────────

@app.route("/kb/knowledge_bases/files", methods=["POST"])
def kb_files_query():
    """Spec: POST with body {kbs_id, page_index, page_size}.
    Response items: {file_name, file_type, file_size, first_upload_time,
    update_time, import_source_dir}."""
    body = request.get_json(force=True, silent=True) or {}
    kbs_id = body.get("kbs_id")
    items = list(FILES.values())
    if kbs_id:
        items = [f for f in items if str(f.get("kbs_id")) == str(kbs_id)]
    paged, _, _ = _paginate(items)
    files_view = [
        {
            "file_name":         f.get("file_name", ""),
            "file_type":         f.get("file_type", "txt"),
            "file_size":         f.get("file_size", 0),
            "first_upload_time": f.get("first_upload_time", 0),
            "update_time":       f.get("update_time", 0),
            "import_source_dir": f.get("import_source_dir", ""),
        }
        for f in paged
    ]
    return _msg_envelope(
        {"files": files_view, "total": len(items)},
        message="query knowledge files success",
    )


@app.route("/kb/knowledge_bases/files/count", methods=["GET"])
def kb_files_count():
    kbs_id = request.args.get("kbs_id")
    items = list(FILES.values())
    if kbs_id:
        items = [f for f in items if str(f.get("kbs_id")) == str(kbs_id)]
    return _msg_envelope(
        {"total": len(items)},
        message="query knowledge files count success",
    )


@app.route("/kb/knowledge_bases/files/history", methods=["POST"])
def kb_files_history():
    """Spec: POST with body {kbs_id, page_index, page_size, dir_path?, fs_id?}."""
    body = request.get_json(force=True, silent=True) or {}
    kbs_id = body.get("kbs_id")
    items = list(FILES.values())
    if kbs_id:
        items = [f for f in items if str(f.get("kbs_id")) == str(kbs_id)]
    history_list = [
        {
            "file_name": f.get("file_name", ""),
            "file_type": f.get("file_type", "txt"),
            "file_size": f.get("file_size", 0),
            "status":    "COMPLETED",
        }
        for f in items
    ]
    return _msg_envelope(
        {
            "history_list": history_list,
            "summary": {
                "created_at":         int(time.time()),
                "last_pipeline_time": None,
                "total":              len(history_list),
                "success":            len(history_list),
            },
        },
        message="query knowledge files history success",
    )


@app.route("/kb/knowledge_bases/files/filesystem", methods=["GET"])
def fs_list():
    """Spec marks 暂未支持 — return empty list as placeholder."""
    return _msg_envelope([], message="filesystem list not implemented")


@app.route("/kb/knowledge_bases/files/upload", methods=["POST"])
def file_upload():
    """Spec: multipart form, kbs_id + files=@local. Mock accepts JSON too."""
    if request.content_type and request.content_type.startswith("multipart/"):
        kbs_id = request.form.get("kbs_id")
        names = [f.filename for f in request.files.getlist("files")]
    else:
        body = request.get_json(silent=True) or {}
        kbs_id = body.get("kbs_id")
        names = body.get("file_names") or [body.get("name", "mock.txt")]
    if not kbs_id or kbs_id not in KBS:
        return _not_found("kbs_id", kbs_id)
    now = int(time.time())
    success = []
    for n in names:
        fid = _new_id()
        FILES[fid] = {
            "file_id":           fid,
            "kbs_id":            kbs_id,
            "file_name":         n,
            "file_type":         (n.rsplit(".", 1) + ["txt"])[1],
            "file_size":         100,
            "first_upload_time": now,
            "update_time":       now,
            "import_source_dir": f"/{kbs_id}",
        }
        success.append({"filename": n, "path": f"/upload/{kbs_id}/{n}", "size": 100})
    return _msg_envelope(
        {
            "summary": {"total": len(names), "success": len(names), "failed": 0},
            "success_list": success,
            "failed_list":  [],
        },
        code_status=201,
        message="操作成功",
    )


@app.route("/kb/knowledge_bases/files/remove", methods=["POST"])
def file_remove():
    """Spec marks 暂未支持 — return success placeholder."""
    return _msg_envelope(None, message="not implemented")


@app.route("/kb/knowledge_bases/files/download", methods=["GET"])
def file_download():
    resp = Response(b"mock file content", mimetype="application/octet-stream")
    for k, v in _debug_headers().items():
        resp.headers[k] = v
    return resp


# ── (3) Models ───────────────────────────────────────────────────────────

@app.route("/kb/models/config", methods=["GET"])
def models_config():
    """Spec: optional ?model_api_id=... → single. Else all.
    MODELAPI is a JSON-encoded string per api.md."""
    mid = request.args.get("model_api_id")
    if mid:
        if mid in MODELS:
            return _msg_envelope([{"ID": mid, "MODELAPI": json.dumps(MODELS[mid])}],
                                  message="query model config success")
        return _not_found("model_api_id", mid)
    return _msg_envelope(
        [{"ID": k, "MODELAPI": json.dumps(v)} for k, v in MODELS.items()],
        message="query model config success",
    )


@app.route("/kb/models/config/add", methods=["POST"])
def models_add():
    """Spec body: model_type, model_name, model_id, api_url, api_key, provider."""
    body = request.get_json(force=True, silent=True) or {}
    mid = _new_id()
    MODELS[mid] = {
        "ModelType": body.get("model_type", "LLM"),
        "ModelName": body.get("model_name", ""),
        "ModelID":   body.get("model_id", ""),
        "APIUrl":    body.get("api_url", ""),
        "APIKey":    body.get("api_key", ""),
        "Provider":  body.get("provider"),
    }
    return _msg_envelope(mid, code_status=201, message="add model config success")


@app.route("/kb/models/config/modify", methods=["POST"])
def models_modify():
    """Spec body: id (top), model_api: {model_type, model_name, ...}."""
    body = request.get_json(force=True, silent=True) or {}
    mid = body.get("id")
    if mid not in MODELS:
        return _not_found("id", mid)
    api = body.get("model_api", {})
    if "model_type" in api: MODELS[mid]["ModelType"] = api["model_type"]
    if "model_name" in api: MODELS[mid]["ModelName"] = api["model_name"]
    if "model_id" in api:   MODELS[mid]["ModelID"]   = api["model_id"]
    if "api_url" in api:    MODELS[mid]["APIUrl"]    = api["api_url"]
    if "api_key" in api:    MODELS[mid]["APIKey"]    = api["api_key"]
    if "provider" in api:   MODELS[mid]["Provider"]  = api["provider"]
    return _msg_envelope(True, message="modify model config success")


@app.route("/kb/models/config/remove", methods=["POST"])
def models_remove():
    """Spec body: id."""
    body = request.get_json(force=True, silent=True) or {}
    mid = body.get("id")
    if mid not in MODELS:
        return _not_found("id", mid)
    MODELS.pop(mid, None)
    return _msg_envelope(True, message="remove model config success")


# ── (4) Prompts ──────────────────────────────────────────────────────────

@app.route("/kb/prompts/detail", methods=["GET"])
def prompts_detail():
    body = request.get_json(silent=True) or {}
    pid = request.args.get("id") or body.get("id")
    pid = str(pid) if pid is not None else None
    if pid not in PROMPTS:
        return _not_found("id", pid)
    return _msg_envelope(PROMPTS[pid])


@app.route("/kb/prompts/options", methods=["GET"])
def prompts_options():
    body = request.get_json(silent=True) or {}
    mode = request.args.get("mode") or body.get("mode")
    items = [{"id": p["id"], "title": p["title"], "description": p["description"],
              "mode": p["mode"]}
             for p in PROMPTS.values()
             if not mode or p.get("mode") == mode]
    return _msg_envelope(items)


@app.route("/kb/prompts/page", methods=["POST", "GET"])
def prompts_page():
    """Spec body: page, size, mode (optional), keyword (optional)."""
    body = request.get_json(silent=True) or {}
    mode    = request.args.get("mode")    or body.get("mode")
    keyword = request.args.get("keyword") or body.get("keyword")
    items = list(PROMPTS.values())
    if mode:
        items = [p for p in items if p.get("mode") == mode]
    if keyword:
        kw = str(keyword).lower()
        items = [p for p in items
                 if kw in str(p.get("title", "")).lower()
                 or kw in str(p.get("description", "")).lower()]
    paged, page, size = _paginate(items, default_size=20)
    items_view = [
        {k: p[k] for k in
         ("id", "title", "description", "mode", "source", "created_at", "updated_at")}
        for p in paged
    ]
    return _msg_envelope({
        "total": len(items),
        "pages": (len(items) + size - 1) // size if size else 1,
        "page":  page,
        "size":  size,
        "items": items_view,
    })


@app.route("/kb/prompts/add", methods=["POST"])
def prompts_add():
    """Spec body: title, description, mode, prompts."""
    body = request.get_json(force=True, silent=True) or {}
    pid = _new_id()
    PROMPTS[pid] = {
        "id":          pid,
        "title":       body.get("title", ""),
        "description": body.get("description", ""),
        "mode":        body.get("mode", "NAIVE"),
        "source":      "user",
        "prompts":     body.get("prompts", {}),
        "kb_ids":      [],
        "created_at":  time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at":  time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    return _msg_envelope({"id": pid}, code_status=201)


@app.route("/kb/prompts/modify", methods=["POST"])
def prompts_modify():
    """Spec body: id, optional title/description/prompts."""
    body = request.get_json(force=True, silent=True) or {}
    pid = body.get("id")
    pid = str(pid) if pid is not None else None
    if pid not in PROMPTS:
        return _not_found("id", pid)
    for k in ("title", "description", "prompts"):
        if k in body:
            PROMPTS[pid][k] = body[k]
    PROMPTS[pid]["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return _msg_envelope(None)


@app.route("/kb/prompts/remove", methods=["POST"])
def prompts_remove():
    """Spec body: id."""
    body = request.get_json(force=True, silent=True) or {}
    pid = body.get("id")
    pid = str(pid) if pid is not None else None
    if pid not in PROMPTS:
        return _not_found("id", pid)
    PROMPTS.pop(pid, None)
    return _msg_envelope(None)


# ── (5) Jargon Groups & Jargons ──────────────────────────────────────────

@app.route("/kb/jargon_groups", methods=["GET"])
def jargon_groups_list():
    items = list(JARGON_LIBS.values())
    paged, page, size = _paginate(items, default_size=10)
    return _msg_envelope({
        "total":       len(items),
        "page":        page,
        "size":        size,
        "total_pages": (len(items) + size - 1) // size,
        "list":        paged,
    })


@app.route("/kb/jargon_groups/jargons", methods=["GET"])
def jargon_groups_jargons():
    """Spec: query/body has jargon_lib_name."""
    body = request.get_json(silent=True) or {}
    lib = request.args.get("jargon_lib_name") or body.get("jargon_lib_name")
    items = [v for k, v in JARGONS.items() if k[0] == lib] if lib else list(JARGONS.values())
    paged, page, size = _paginate(items, default_size=20)
    return _msg_envelope({"total": len(items), "page": page, "size": size, "list": paged})


@app.route("/kb/knowledge_bases/jargon_groups", methods=["GET"])
def kb_jargon_groups():
    """Spec: query/body has kb_name → returns the bound jargon_lib_name."""
    body = request.get_json(silent=True) or {}
    kb_name = request.args.get("kb_name") or body.get("kb_name")
    bound = next(iter(JARGON_LIBS.keys()), "")
    resp = jsonify({"code": 0, "message": "ok", "jargon_lib_name": bound, "kb_name": kb_name})
    for k, v in _debug_headers().items():
        resp.headers[k] = v
    return resp


@app.route("/kb/jargons_groups/jargon", methods=["GET"])
def jargons_lookup():
    """Spec body: jargon_lib_name + jargon_name (list)."""
    body = request.get_json(silent=True) or {}
    lib = request.args.get("jargon_lib_name") or body.get("jargon_lib_name")
    names = body.get("jargon_name") or []
    if isinstance(names, str):
        names = [names]
    out = []
    for n in names:
        j = JARGONS.get((lib, n))
        out.append({
            "jargon_name": n,
            "target_term": (j or {}).get("target_term", []),
            "description": (j or {}).get("description", ""),
            "created_at":  (j or {}).get("created_at", 0),
            "exist":       j is not None,
        })
    return _msg_envelope(out)


@app.route("/kb/jargon_groups/version", methods=["GET"])
def jargon_lib_version():
    """Spec body: jargon_lib_name → returns sequence_id."""
    body = request.get_json(silent=True) or {}
    lib = request.args.get("jargon_lib_name") or body.get("jargon_lib_name")
    seq = JARGON_LIBS.get(lib, {}).get("sequence_id", 1) if lib else 0
    resp = jsonify({"code": 0, "message": "ok", "sequence_id": seq})
    for k, v in _debug_headers().items():
        resp.headers[k] = v
    return resp


@app.route("/kb/jargon_groups/add", methods=["POST"])
def jargon_groups_add():
    """Spec body: jargon_lib_name, description."""
    body = request.get_json(force=True, silent=True) or {}
    name = body.get("jargon_lib_name") or f"lib-{_new_id()}"
    lib_id = _new_id()
    JARGON_LIBS[name] = {
        "jargon_lib_id":   lib_id,
        "jargon_lib_name": name,
        "description":     body.get("description", ""),
        "entry_count":     0,
        "bound_kb_ids":    [],
        "created_at":      int(time.time()),
        "sequence_id":     1,
    }
    return _msg_envelope(
        {"jargon_lib_id": lib_id, "jargon_lib_name": name,
         "created_at": time.strftime("%Y-%m-%d %H:%M:%S")},
        code_status=201,
    )


@app.route("/kb/jargon_groups/remove", methods=["POST"])
def jargon_groups_remove():
    """Spec body: jargon_lib_name."""
    body = request.get_json(force=True, silent=True) or {}
    name = body.get("jargon_lib_name")
    if name not in JARGON_LIBS:
        return _not_found("jargon_lib_name", name)
    JARGON_LIBS.pop(name, None)
    return _msg_envelope(None)


@app.route("/kb/jargon_groups/knowledge_bases/add", methods=["POST"])
def jargon_kb_bind():
    """Spec body: kb_name, jargon_lib_name."""
    return _msg_envelope({"status": "bound"}, code_status=201)


@app.route("/kb/jargon_groups/knowledge_bases/remove", methods=["POST"])
def jargon_kb_unbind():
    """Spec body: kb_name, jargon_lib_name."""
    return _msg_envelope({"status": "unbound"})


@app.route("/kb/jargons/add", methods=["POST"])
def jargons_add():
    """Spec body: jargon_info_list = [{jargon_name, description, target_term, jargon_lib_name, ...}]."""
    body = request.get_json(force=True, silent=True) or {}
    items = body.get("jargon_info_list", [])
    for it in items:
        lib = it.get("jargon_lib_name", "default")
        name = it.get("jargon_name", f"j-{_new_id()}")
        JARGONS[(lib, name)] = {
            "jargon_id":       _new_id(),
            "jargon_name":     name,
            "jargon_lib_name": lib,
            "description":     it.get("description", ""),
            "target_term":     it.get("target_term", []),
            "creator_name":    it.get("creator_name", ""),
            "creator_id":      it.get("creator_id", ""),
            "created_at":      int(time.time()),
        }
    return _msg_envelope(None, code_status=201)


@app.route("/kb/jargons/modify", methods=["POST"])
def jargons_modify():
    """Spec body: jargon_lib_name, jargon_name, description."""
    body = request.get_json(force=True, silent=True) or {}
    key = (body.get("jargon_lib_name"), body.get("jargon_name"))
    if key not in JARGONS:
        return _not_found("jargon_name", key[1])
    if "description" in body:
        JARGONS[key]["description"] = body["description"]
    JARGONS[key]["update_at"] = int(time.time())
    return _msg_envelope({
        "jargon_id":   JARGONS[key]["jargon_id"],
        "jargon_name": JARGONS[key]["jargon_name"],
        "target_term": JARGONS[key]["target_term"],
        "description": JARGONS[key]["description"],
        "update_at":   JARGONS[key]["update_at"],
    })


@app.route("/kb/jargons/remove", methods=["POST"])
def jargons_remove():
    """Spec body: jargon_lib_name, jargon_name."""
    body = request.get_json(force=True, silent=True) or {}
    key = (body.get("jargon_lib_name"), body.get("jargon_name"))
    if key not in JARGONS:
        return _not_found("jargon_name", key[1])
    JARGONS.pop(key, None)
    return _msg_envelope(None)


# ── (6) Conversations ────────────────────────────────────────────────────

@app.route("/kb/conversations/start", methods=["POST"])
def conv_start():
    """Spec body: query, resources, thread_id, user_id, output_format, llm, embedding,
    reranker, rag_mode, enable_proxy. Streams SSE in real KB; mock returns JSON."""
    body = request.get_json(force=True, silent=True) or {}
    thread_id = body.get("thread_id") or _new_id()
    CONVERSATIONS[thread_id] = {
        "conv_id":    thread_id,
        "thread_id":  thread_id,
        "kbs_id":     (body.get("resources") or [{}])[0].get("collection_name", ""),
        "query":      body.get("query", ""),
        "created_by": _auth()["user_id"],
        "answer":     "mock answer",
    }
    resp = jsonify({"event": "answer", "data": {"thread_id": thread_id, "answer": "mock answer"}})
    resp.status_code = 201
    for k, v in _debug_headers().items():
        resp.headers[k] = v
    return resp


@app.route("/kb/conversations/stop", methods=["POST", "GET"])
def conv_stop():
    body = request.get_json(silent=True) or {}
    thread_id = body.get("thread_id") or request.args.get("thread_id")
    return _msg_envelope(None, message="stop chat success")


@app.route("/kb/conversations/images/generate", methods=["POST"])
def conv_images_generate():
    """Spec body: image_url → returns token."""
    body = request.get_json(silent=True) or {}
    return _msg_envelope({"token": f"mock-token-{_new_id()}-{body.get('image_url','')[:20]}"},
                          message="generate image link success")


@app.route("/kb/conversations/images/download", methods=["GET"])
def conv_images_download():
    resp = Response(b"\x89PNG\r\n\x1a\n", mimetype="image/png")
    for k, v in _debug_headers().items():
        resp.headers[k] = v
    return resp


# ── (7) Retrieval ────────────────────────────────────────────────────────

@app.route("/kb/retrieval/fusion_search", methods=["POST"])
def retrieval_fusion():
    """Spec body: query, kds_list, search_method, ..."""
    body = request.get_json(silent=True) or {}
    return _msg_envelope({
        "data": [
            {"chunk_type": "text", "file_url": "", "id": 0, "score": 1.0, "text": "mock result 1"},
            {"chunk_type": "text", "file_url": "", "id": 1, "score": 0.9, "text": "mock result 2"},
        ],
        "total_return_count": 2,
    }, message="retrieve success")


# ── Catch-all (debug, mock-only) ─────────────────────────────────────────

@app.route("/kb/<path:sub>", methods=["GET", "POST", "PUT", "DELETE"])
def kb_catch_all(sub):
    return _msg_envelope({
        "path":   f"/kb/{sub}",
        "method": request.method,
        "_auth":  _auth(),
        "body":   request.get_json(silent=True) or {},
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
