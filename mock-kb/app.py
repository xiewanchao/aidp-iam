"""Mock Knowledge Base backend for IAM end-to-end testing.

Implements all the /kb/* endpoints listed in the KB API spec. Every endpoint
returns a plausible 2xx response so the IAM chain (ext_authz, ext_proc,
resource_acl, path_rules) can be exercised end-to-end. No real business logic.

Creation endpoints return 201 so resource-sync's ext_proc handler writes an
entry into resource_acl for the created resource.

Storage is in-memory and resets on pod restart.
"""
from flask import Flask, request, jsonify, Response
import uuid
import time

app = Flask(__name__)

# In-memory stores
KBS = {}             # KDSID -> kb metadata
MAPPINGS = {}        # CHANNELID -> mapping
FILES = {}           # file_id -> metadata
FILESYSTEMS = {}     # fs_id -> fs metadata
MODELS = {}          # model_id -> model config
PROMPTS = {}         # prompt_id -> prompt
JARGON_LIBS = {}     # lib_name -> library
JARGONS = {}         # (lib_name, name) -> jargon
CONVERSATIONS = {}   # conv_id -> conversation


def _auth():
    return {
        "user_id": request.headers.get("X-Auth-User-Id", "unknown"),
        "tenant": request.headers.get("X-Auth-Tenant", "unknown"),
        "groups": request.headers.get("X-Auth-Groups", ""),
        "allowed_ids": request.headers.get("X-Allowed-Ids", ""),
    }


def _new_id():
    return str(uuid.uuid4())[:8]


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "mock-kb"})


# ── Knowledge Bases ────────────────────────────────────────────────────────

@app.route("/kb/knowledge_bases/page", methods=["GET", "POST"])
def kb_page():
    items = list(KBS.values())
    return jsonify({"items": items, "total": len(items), "page": 1, "size": 10})


@app.route("/kb/knowledge_bases/count", methods=["GET"])
def kb_count():
    return jsonify({"count": len(KBS)})


@app.route("/kb/knowledge_bases", methods=["GET"])
def kb_get_single():
    kdsid = request.args.get("KDSID") or (request.get_json(silent=True) or {}).get("KDSID")
    if kdsid and kdsid in KBS:
        return jsonify(KBS[kdsid])
    return jsonify({"items": list(KBS.values()), "total": len(KBS)})


@app.route("/kb/knowledge_bases/add", methods=["POST"])
def kb_add():
    body = request.get_json(force=True, silent=True) or {}
    kdsid = body.get("KDSID") or _new_id()
    kb = {
        "KDSID": kdsid,
        "NAME": body.get("NAME", f"kb-{kdsid}"),
        "DESCRIPTION": body.get("DESCRIPTION", ""),
        "CHUNKTOKENNUM": body.get("CHUNKTOKENNUM", 1024),
        "CHUNKOVERLAPNUM": body.get("CHUNKOVERLAPNUM", 128),
        "EMBEDDINGMODEL": body.get("EMBEDDINGMODEL", "default"),
        "created_by": _auth()["user_id"],
        "created_at": int(time.time()),
    }
    KBS[kdsid] = kb
    return jsonify(kb), 201


@app.route("/kb/knowledge_bases/modify", methods=["POST"])
def kb_modify():
    body = request.get_json(force=True, silent=True) or {}
    kdsid = body.get("KDSID")
    if not kdsid or kdsid not in KBS:
        return jsonify({"error": "not found"}), 404
    KBS[kdsid].update({k: v for k, v in body.items() if k != "KDSID"})
    return jsonify(KBS[kdsid])


@app.route("/kb/knowledge_bases/remove", methods=["POST"])
def kb_remove():
    body = request.get_json(force=True, silent=True) or {}
    kdsid = body.get("KDSID")
    KBS.pop(kdsid, None)
    return jsonify({"status": "deleted", "KDSID": kdsid})


# ── Directory Mappings ─────────────────────────────────────────────────────

@app.route("/kb/knowledge_bases/mappings", methods=["GET", "POST"])
def kb_mappings_list():
    return jsonify({"items": list(MAPPINGS.values()), "total": len(MAPPINGS)})


@app.route("/kb/knowledge_bases/mappings/count", methods=["GET", "POST"])
def kb_mappings_count():
    return jsonify({"count": len(MAPPINGS)})


@app.route("/kb/knowledge_bases/mappings/add", methods=["POST"])
def kb_mappings_add():
    body = request.get_json(force=True, silent=True) or {}
    kdsid = body.get("KDSID") or "unknown"
    cid = _new_id()
    m = {
        "CHANNELID": cid,
        "KDSID": kdsid,
        "SRCDIR": body.get("SRCDIR", ""),
        "FSNAME": body.get("FSNAME", ""),
        "FSID": body.get("FSID", ""),
        "CHANNELNAME": body.get("CHANNELNAME", f"ch-{cid}"),
    }
    MAPPINGS[cid] = m
    return jsonify({"KDSID": kdsid, **m}), 201


@app.route("/kb/knowledge_bases/mappings/remove", methods=["POST"])
def kb_mappings_remove():
    body = request.get_json(force=True, silent=True) or {}
    cid = body.get("CHANNELID")
    MAPPINGS.pop(cid, None)
    return jsonify({"status": "deleted", "CHANNELID": cid, "KDSID": body.get("KDSID", "")})


# ── Files & Filesystem ────────────────────────────────────────────────────

@app.route("/kb/knowledge_bases/files/history", methods=["GET"])
def kb_files_history():
    return jsonify({"items": list(FILES.values()), "total": len(FILES)})


@app.route("/kb/knowledge_bases/files", methods=["GET"])
def kb_files_list():
    return jsonify({"items": list(FILES.values()), "total": len(FILES)})


@app.route("/kb/knowledge_bases/files/count", methods=["GET"])
def kb_files_count():
    return jsonify({"count": len(FILES)})


@app.route("/kb/knowledge_bases/files/filesystem/add", methods=["POST"])
def fs_add():
    body = request.get_json(force=True, silent=True) or {}
    fs_id = _new_id()
    fs = {"FSID": fs_id, "FSNAME": body.get("FSNAME", f"fs-{fs_id}"), "KDSID": body.get("KDSID", "")}
    FILESYSTEMS[fs_id] = fs
    return jsonify({"KDSID": body.get("KDSID", ""), **fs}), 201


@app.route("/kb/knowledge_bases/files/filesystem/remove", methods=["POST"])
def fs_remove():
    body = request.get_json(force=True, silent=True) or {}
    FILESYSTEMS.pop(body.get("FSID"), None)
    return jsonify({"status": "deleted", "KDSID": body.get("KDSID", "")})


@app.route("/kb/knowledge_bases/files/filesystem", methods=["GET"])
def fs_list():
    return jsonify({"items": list(FILESYSTEMS.values()), "total": len(FILESYSTEMS)})


@app.route("/kb/knowledge_bases/files/upload", methods=["POST"])
def file_upload():
    body = request.get_json(silent=True) or {}
    fid = _new_id()
    FILES[fid] = {"file_id": fid, "KDSID": body.get("KDSID", ""), "name": body.get("name", f"file-{fid}")}
    return jsonify({"KDSID": body.get("KDSID", ""), "file_id": fid, "status": "uploaded"}), 201


@app.route("/kb/knowledge_bases/files/remove", methods=["POST"])
def file_remove():
    body = request.get_json(force=True, silent=True) or {}
    FILES.pop(body.get("file_id"), None)
    return jsonify({"status": "deleted", "KDSID": body.get("KDSID", "")})


@app.route("/kb/knowledge_bases/files/download", methods=["GET"])
def file_download():
    return Response(b"mock file content", mimetype="application/octet-stream")


# ── Models ─────────────────────────────────────────────────────────────────

@app.route("/kb/models/config", methods=["GET", "POST"])
def models_config():
    return jsonify({"items": list(MODELS.values()), "total": len(MODELS)})


@app.route("/kb/models/config/add", methods=["POST"])
def models_add():
    body = request.get_json(force=True, silent=True) or {}
    mid = _new_id()
    m = {"ModelAPIID": mid, **body}
    MODELS[mid] = m
    return jsonify(m), 201


@app.route("/kb/models/config/modify", methods=["POST"])
def models_modify():
    body = request.get_json(force=True, silent=True) or {}
    mid = body.get("ModelAPIID")
    if mid in MODELS:
        MODELS[mid].update(body)
    return jsonify(MODELS.get(mid, body))


@app.route("/kb/models/config/remove", methods=["POST"])
def models_remove():
    body = request.get_json(force=True, silent=True) or {}
    MODELS.pop(body.get("ModelAPIID"), None)
    return jsonify({"status": "deleted"})


@app.route("/kb/models/config/set", methods=["GET", "POST"])
def models_set():
    return jsonify({"status": "ok"})


# ── Prompts ────────────────────────────────────────────────────────────────

@app.route("/kb/prompts", methods=["GET", "POST"])
def prompts_get():
    return jsonify({"items": list(PROMPTS.values()), "total": len(PROMPTS)})


@app.route("/kb/prompts/add", methods=["POST"])
def prompts_add():
    body = request.get_json(force=True, silent=True) or {}
    pid = _new_id()
    p = {"prompt_id": pid, **body}
    PROMPTS[pid] = p
    return jsonify(p), 201


@app.route("/kb/prompts/modify", methods=["POST"])
def prompts_modify():
    body = request.get_json(force=True, silent=True) or {}
    pid = body.get("prompt_id")
    if pid in PROMPTS:
        PROMPTS[pid].update(body)
    return jsonify(PROMPTS.get(pid, body))


@app.route("/kb/prompts/remove", methods=["POST"])
def prompts_remove():
    body = request.get_json(force=True, silent=True) or {}
    PROMPTS.pop(body.get("prompt_id"), None)
    return jsonify({"status": "deleted"})


@app.route("/kb/prompts/menu", methods=["GET"])
def prompts_menu():
    return jsonify({"items": [{"id": k, "name": v.get("name", "")} for k, v in PROMPTS.items()]})


@app.route("/kb/prompts/page", methods=["POST"])
def prompts_page():
    return jsonify({"items": list(PROMPTS.values()), "total": len(PROMPTS), "page": 1, "size": 10})


# ── Jargon Groups & Jargons ───────────────────────────────────────────────

@app.route("/kb/jargon_groups", methods=["GET", "POST"])
def jargon_groups_list():
    return jsonify({"items": list(JARGON_LIBS.values()), "total": len(JARGON_LIBS)})


@app.route("/kb/jargon_groups/add", methods=["POST"])
def jargon_groups_add():
    body = request.get_json(force=True, silent=True) or {}
    name = body.get("JARGON_LIB_NAME", f"lib-{_new_id()}")
    lib = {"JARGON_LIB_NAME": name, "DESCRIPTION": body.get("DESCRIPTION", "")}
    JARGON_LIBS[name] = lib
    return jsonify(lib), 201


@app.route("/kb/jargon_groups/remove", methods=["POST"])
def jargon_groups_remove():
    body = request.get_json(force=True, silent=True) or {}
    JARGON_LIBS.pop(body.get("JARGON_LIB_NAME"), None)
    return jsonify({"status": "deleted"})


@app.route("/kb/jargon_groups/jargons", methods=["GET", "POST"])
def jargon_groups_jargons():
    lib = request.args.get("JARGON_LIB_NAME") or (request.get_json(silent=True) or {}).get("JARGON_LIB_NAME")
    items = [v for k, v in JARGONS.items() if k[0] == lib]
    return jsonify({"items": items, "total": len(items)})


@app.route("/kb/jargon_groups/knowledge_bases/add", methods=["POST"])
def jargon_kb_bind():
    return jsonify({"status": "bound"}), 201


@app.route("/kb/jargon_groups/knowledge_bases/remove", methods=["POST"])
def jargon_kb_unbind():
    return jsonify({"status": "unbound"})


# 注意：spec 中路径为 /kb/jargons_groups/{name}（拼写不一致），两个都支持
@app.route("/kb/jargons_groups/<jargon_lib_name>", methods=["GET", "POST"])
@app.route("/kb/jargon_groups/<jargon_lib_name>", methods=["GET", "POST"])
def jargons_in_lib(jargon_lib_name):
    items = [v for k, v in JARGONS.items() if k[0] == jargon_lib_name]
    return jsonify({"items": items, "total": len(items)})


@app.route("/kb/jargons/add", methods=["POST"])
def jargons_add():
    body = request.get_json(force=True, silent=True) or {}
    lib = body.get("JARGON_LIB_NAME", "default")
    name = body.get("JARGON_NAME", f"j-{_new_id()}")
    j = {**body, "JARGON_NAME": name}
    JARGONS[(lib, name)] = j
    return jsonify(j), 201


@app.route("/kb/jargons/modify", methods=["POST"])
def jargons_modify():
    body = request.get_json(force=True, silent=True) or {}
    key = (body.get("JARGON_LIB_NAME"), body.get("JARGON_NAME"))
    if key in JARGONS:
        JARGONS[key].update(body)
    return jsonify(JARGONS.get(key, body))


@app.route("/kb/jargons/remove", methods=["POST"])
def jargons_remove():
    body = request.get_json(force=True, silent=True) or {}
    JARGONS.pop((body.get("JARGON_LIB_NAME"), body.get("JARGON_NAME")), None)
    return jsonify({"status": "deleted"})


@app.route("/kb/knowledge_bases/<kb_name>/jargon", methods=["GET"])
def kb_jargon_query(kb_name):
    return jsonify({"kb_name": kb_name, "jargon_libs": list(JARGON_LIBS.keys())})


@app.route("/kb/jargon_groups/version/<jargon_lib_name>", methods=["GET"])
def jargon_lib_version(jargon_lib_name):
    return jsonify({"JARGON_LIB_NAME": jargon_lib_name, "version": "v1.0"})


# ── Conversations (Q&A) ────────────────────────────────────────────────────

@app.route("/kb/conversations/start", methods=["POST"])
def conv_start():
    body = request.get_json(force=True, silent=True) or {}
    cid = _new_id()
    CONVERSATIONS[cid] = {"id": cid, "question": body.get("question", ""), "created_by": _auth()["user_id"]}
    return jsonify({"conversation_id": cid, "answer": "mock answer"}), 201


@app.route("/kb/conversations/stop", methods=["GET", "POST"])
def conv_stop():
    return jsonify({"status": "stopped"})


@app.route("/kb/conversations/images/generate", methods=["POST"])
def conv_images_generate():
    return jsonify({"image_url": "/kb/conversations/images/download?id=mock"})


@app.route("/kb/conversations/images/download", methods=["GET"])
def conv_images_download():
    return Response(b"\x89PNG\r\n\x1a\n", mimetype="image/png")


@app.route("/kb/conversations/query/batch", methods=["GET", "POST"])
def conv_query_batch():
    return jsonify({"items": list(CONVERSATIONS.values()), "total": len(CONVERSATIONS)})


@app.route("/kb/conversations/query/single", methods=["GET", "POST"])
def conv_query_single():
    cid = request.args.get("id") or (request.get_json(silent=True) or {}).get("id")
    return jsonify(CONVERSATIONS.get(cid, {"error": "not found"}))


@app.route("/kb/conversations/remove", methods=["POST"])
def conv_remove():
    body = request.get_json(force=True, silent=True) or {}
    CONVERSATIONS.pop(body.get("id"), None)
    return jsonify({"status": "deleted"})


# ── Retrieval ──────────────────────────────────────────────────────────────

@app.route("/kb/retrieval/fusion_search", methods=["POST"])
def retrieval_fusion():
    return jsonify({"results": [], "total": 0})


# ── Catch-all ──────────────────────────────────────────────────────────────

@app.route("/kb/<path:sub>", methods=["GET", "POST", "PUT", "DELETE"])
def kb_catch_all(sub):
    return jsonify({
        "path": f"/kb/{sub}",
        "method": request.method,
        "_auth": _auth(),
        "body": request.get_json(silent=True) or {},
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
