"""Mock RubikSQL backend for IAM end-to-end testing.

Implements all the /api/* endpoints from the RubikSQL API spec.
Gateway rewrites /rubik/* -> /*, so this backend sees raw /api/... paths.

All endpoints return plausible 2xx responses. Storage is in-memory.
"""
from flask import Flask, request, jsonify, Response
import uuid
import time

app = Flask(__name__)

DBS = {}
SESSIONS = {}
KNOWLEDGE = {}
SKILLS = {}
METADATA = {}
CONFIG = {
    "models": {},
    "database-providers": {},
    "app": {},
    "llm-providers": {},
    "app_lang": "zh",
    "query_lang": "zh",
}


def _auth():
    return {
        "user_id": request.headers.get("X-Auth-User-Id", "unknown"),
        "tenant": request.headers.get("X-Auth-Tenant", "unknown"),
        "groups": request.headers.get("X-Auth-Groups", ""),
        "allowed_ids": request.headers.get("X-Allowed-Ids", ""),
    }


@app.after_request
def _echo_debug_headers(resp):
    """Echo the auth / X-Allowed-Ids request headers as X-Debug-* response
    headers so tests can assert Envoy+ext_proc injection without scraping logs."""
    resp.headers["X-Debug-User-Id"]       = request.headers.get("X-Auth-User-Id", "")
    resp.headers["X-Debug-Tenant"]        = request.headers.get("X-Auth-Tenant", "")
    resp.headers["X-Debug-Groups"]        = request.headers.get("X-Auth-Groups", "")
    resp.headers["X-Debug-Allowed-Ids"]   = request.headers.get("X-Allowed-Ids", "")
    resp.headers["X-Debug-Allowed-Total"] = request.headers.get("X-Allowed-Total", "")
    return resp


def _new_id():
    return str(uuid.uuid4())[:8]


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "mock-rubik"})


# ══════════════════════════════════════════════════════════════════════════
# 1. 数据库管理
# ══════════════════════════════════════════════════════════════════════════

def _allowed_ids_set():
    """Return None when X-Allowed-Ids header is absent (admin bypass → no filter).
    Return a set (possibly empty) otherwise. Empty set = user has no ACLs → empty list."""
    header = request.headers.get("X-Allowed-Ids")
    if header is None:
        return None
    if header == "":
        return set()
    return {x.strip() for x in header.split(",") if x.strip()}


@app.route("/api/databases", methods=["GET"])
def list_dbs():
    allowed = _allowed_ids_set()
    if allowed is None:
        items = list(DBS.values())
    else:
        items = [v for k, v in DBS.items() if k in allowed]
    return jsonify(items)


@app.route("/api/databases", methods=["POST"])
def create_db():
    body = request.get_json(force=True, silent=True) or {}
    db_id = _new_id()
    db = {
        "id": db_id,
        "name": body.get("name", f"db-{db_id}"),
        "type": body.get("type", "sqlite"),
        "path": body.get("path"),
        "host": body.get("host"),
        "port": body.get("port"),
        "user": body.get("user"),
        "password": body.get("password"),
        "database": body.get("database"),
        "kb_path": None,
        "created_at": _now(),
        "created_by": _auth()["user_id"],
        "auths": [],
        "kb_built": False,
    }
    DBS[db_id] = db
    return jsonify(db), 201


@app.route("/api/databases/config/data-dir", methods=["GET"])
def db_config_data_dir():
    return jsonify({"data_dir": "/var/lib/rubik/data"})


@app.route("/api/databases/<db_id>", methods=["GET"])
def get_db(db_id):
    if db_id not in DBS:
        return jsonify({"error": "not found"}), 404
    return jsonify(DBS[db_id])


@app.route("/api/databases/<db_id>", methods=["DELETE"])
def delete_db(db_id):
    DBS.pop(db_id, None)
    return jsonify({"status": "deleted", "id": db_id})


@app.route("/api/databases/<db_id>/check", methods=["GET"])
def db_check(db_id):
    return jsonify({"exists": db_id in DBS, "connected": True, "table_count": 3})


@app.route("/api/databases/<db_id>/schema", methods=["GET"])
def db_schema(db_id):
    return jsonify([
        {"name": "public", "type": "schema", "children": [
            {"name": "users", "type": "table", "children": [
                {"name": "id", "type": "column", "dataType": "int"},
                {"name": "name", "type": "column", "dataType": "varchar"},
            ]}
        ]}
    ])


@app.route("/api/databases/<db_id>/tables-columns", methods=["GET"])
def db_tables_cols(db_id):
    return jsonify([{"table": "users", "columns": ["id", "name"]}])


@app.route("/api/databases/<db_id>/tables/<table_name>", methods=["GET"])
def db_table_data(db_id, table_name):
    return jsonify({"columns": ["id", "name"], "rows": [], "total": 0})


@app.route("/api/databases/<db_id>/prettify-sql", methods=["POST"])
def db_prettify_sql(db_id):
    body = request.get_json(force=True, silent=True) or {}
    return jsonify({"success": True, "sql": body.get("sql", "").upper()})


@app.route("/api/databases/<db_id>/execute-sql", methods=["POST"])
def db_execute_sql(db_id):
    return jsonify({"columns": ["result"], "rows": [{"result": "ok"}], "total": 1})


# ══════════════════════════════════════════════════════════════════════════
# 2. 知识库构建
# ══════════════════════════════════════════════════════════════════════════

@app.route("/api/databases/<db_id>/build", methods=["POST"])
def db_build(db_id):
    return jsonify({"database_id": db_id, "status": "completed", "progress": 1.0, "message": "done",
                    "step": "final", "step_progress": 1.0, "elapsed": 10.0, "estimated": 10.0})


@app.route("/api/databases/<db_id>/build/status", methods=["GET"])
def db_build_status(db_id):
    return jsonify({"database_id": db_id, "status": "idle", "progress": None, "message": None,
                    "step": None, "step_progress": None, "elapsed": None, "estimated": None})


@app.route("/api/databases/<db_id>/build/stream", methods=["POST"])
def db_build_stream(db_id):
    def gen():
        yield 'data: {"database_id":"' + db_id + '","status":"building","progress":0.5}\n\n'
        yield 'data: {"database_id":"' + db_id + '","status":"completed","progress":1.0}\n\n'
    return Response(gen(), mimetype="text/event-stream")


@app.route("/api/databases/<db_id>/build/cancel", methods=["POST"])
def db_build_cancel(db_id):
    return jsonify({"success": True})


# ══════════════════════════════════════════════════════════════════════════
# 3. 知识管理
# ══════════════════════════════════════════════════════════════════════════

@app.route("/api/databases/<db_id>/knowledge/types", methods=["GET"])
def knowledge_types(db_id):
    return jsonify([{"type": "custom", "count": 0}, {"type": "skill", "count": 0}])


@app.route("/api/databases/<db_id>/knowledge/list", methods=["GET"])
def knowledge_list(db_id):
    items = [v for k, v in KNOWLEDGE.items() if k[0] == db_id]
    return jsonify({"items": items, "total": len(items), "page": 1, "limit": 50})


@app.route("/api/databases/<db_id>/knowledge/<type_>", methods=["GET"])
def knowledge_by_type(db_id, type_):
    items = [v for k, v in KNOWLEDGE.items() if k[0] == db_id and k[1] == type_]
    return jsonify({"items": items, "total": len(items), "page": 1, "limit": 50})


@app.route("/api/databases/<db_id>/knowledge/<type_>/<item_id>", methods=["GET"])
def knowledge_get(db_id, type_, item_id):
    k = (db_id, type_, item_id)
    if k in KNOWLEDGE:
        return jsonify(KNOWLEDGE[k])
    return jsonify({"error": "not found"}), 404


@app.route("/api/databases/<db_id>/knowledge/<type_>/<item_id>/dict", methods=["GET"])
def knowledge_get_dict(db_id, type_, item_id):
    k = (db_id, type_, item_id)
    return jsonify(KNOWLEDGE.get(k, {}))


@app.route("/api/databases/<db_id>/knowledge/<type_>/<item_id>", methods=["PUT"])
def knowledge_update(db_id, type_, item_id):
    body = request.get_json(force=True, silent=True) or {}
    k = (db_id, type_, item_id)
    if k not in KNOWLEDGE:
        KNOWLEDGE[k] = {"id_str": item_id, "type": type_, "name": f"item-{item_id}"}
    KNOWLEDGE[k].update(body)
    return jsonify(KNOWLEDGE[k])


@app.route("/api/databases/<db_id>/knowledge/<type_>/<item_id>", methods=["DELETE"])
def knowledge_delete(db_id, type_, item_id):
    KNOWLEDGE.pop((db_id, type_, item_id), None)
    return jsonify({"success": True})


def _knowledge_response(db_id, type_, body):
    item_id = _new_id()
    entry = {
        "id_str": item_id,
        "type": type_,
        "name": body.get("name", f"{type_}-{item_id}"),
        "short_description": body.get("short_description"),
        "description": body.get("description"),
        "skill_body": body.get("skill_body"),
        "synonyms": body.get("synonyms", []),
        "tags": body.get("tags", []),
        "content_resources": body.get("content_resources"),
        "source": "user",
        "creator": _auth()["user_id"],
        "owner": _auth()["user_id"],
        "workspace": body.get("workspace", "db"),
        "processing_status": "completed",
        "metadata": body.get("metadata"),
        "created_at": _now(),
        "updated_at": _now(),
    }
    KNOWLEDGE[(db_id, type_, item_id)] = entry
    return entry


@app.route("/api/databases/<db_id>/knowledge/taxonomy", methods=["POST"])
def knowledge_taxonomy(db_id):
    body = request.get_json(force=True, silent=True) or {}
    return jsonify(_knowledge_response(db_id, "taxonomy", body)), 201


@app.route("/api/databases/<db_id>/knowledge/custom", methods=["POST"])
def knowledge_custom(db_id):
    body = request.get_json(force=True, silent=True) or {}
    return jsonify(_knowledge_response(db_id, "custom", body)), 201


@app.route("/api/databases/knowledge/special", methods=["POST"])
def knowledge_special():
    body = request.get_json(force=True, silent=True) or {}
    db_id = body.get("db_id") or "global"
    return jsonify(_knowledge_response(db_id, "special", body)), 201


@app.route("/api/databases/<db_id>/knowledge/experience", methods=["POST"])
def knowledge_experience(db_id):
    body = request.get_json(force=True, silent=True) or {}
    return jsonify(_knowledge_response(db_id, "experience", body)), 201


@app.route("/api/databases/<db_id>/knowledge/import", methods=["POST"])
def knowledge_import(db_id):
    body = request.get_json(force=True, silent=True) or {}
    return jsonify(_knowledge_response(db_id, body.get("type", "custom"), body)), 201


@app.route("/api/databases/<db_id>/knowledge/export/stream", methods=["POST"])
def knowledge_export_stream(db_id):
    def gen():
        yield 'data: {"event":"start"}\n\n'
        yield 'data: {"event":"complete"}\n\n'
    return Response(gen(), mimetype="text/event-stream")


# ══════════════════════════════════════════════════════════════════════════
# 4. 技能管理
# ══════════════════════════════════════════════════════════════════════════

@app.route("/api/databases/<db_id>/skill/custom", methods=["POST"])
def skill_custom(db_id):
    body = request.get_json(force=True, silent=True) or {}
    item_id = _new_id()
    skill = {
        "id_str": item_id,
        "type": "skill",
        "name": body.get("name", f"skill-{item_id}"),
        "description": body.get("description", ""),
        "skill_body": body.get("content", ""),
        "creator": _auth()["user_id"],
        "owner": _auth()["user_id"],
        "workspace": "db",
        "processing_status": "completed",
        "created_at": _now(),
        "updated_at": _now(),
    }
    SKILLS[(db_id, item_id)] = skill
    return jsonify(skill), 201


@app.route("/api/databases/<db_id>/skill/<item_id>", methods=["PUT"])
def skill_update(db_id, item_id):
    body = request.get_json(force=True, silent=True) or {}
    k = (db_id, item_id)
    if k not in SKILLS:
        SKILLS[k] = {"id_str": item_id, "type": "skill", "name": "existing"}
    SKILLS[k].update({
        "name": body.get("name", SKILLS[k].get("name")),
        "description": body.get("description", SKILLS[k].get("description")),
        "skill_body": body.get("content", SKILLS[k].get("skill_body")),
        "updated_at": _now(),
    })
    return jsonify(SKILLS[k])


# ══════════════════════════════════════════════════════════════════════════
# 5. 知识同步
# ══════════════════════════════════════════════════════════════════════════

@app.route("/api/databases/<db_id>/sync", methods=["POST"])
def db_sync(db_id):
    return jsonify({"success": True, "message": "synced", "result": {}})


@app.route("/api/databases/<db_id>/sync/stream", methods=["POST"])
def db_sync_stream(db_id):
    def gen():
        yield 'data: {"event":"start"}\n\n'
        yield 'data: {"event":"complete"}\n\n'
    return Response(gen(), mimetype="text/event-stream")


# ══════════════════════════════════════════════════════════════════════════
# 6. 元数据管理
# ══════════════════════════════════════════════════════════════════════════

@app.route("/api/metadata/<db_id>/init", methods=["GET"])
def metadata_init(db_id):
    METADATA[db_id] = {"tables": 3, "columns": 10, "initialized": True}
    return jsonify({"database_id": db_id, **METADATA[db_id]})


@app.route("/api/metadata/<db_id>", methods=["GET"])
def metadata_get(db_id):
    return jsonify(METADATA.get(db_id, {"database_id": db_id, "initialized": False}))


@app.route("/api/metadata/<db_id>/description", methods=["PUT"])
def metadata_description(db_id):
    return jsonify({"status": "updated"})


# ══════════════════════════════════════════════════════════════════════════
# 7. 查询
# ══════════════════════════════════════════════════════════════════════════

@app.route("/api/query", methods=["POST"])
def query():
    def gen():
        yield 'data: {"event":"plan","data":{"text":"planning..."},"hidden":false}\n\n'
        yield 'data: {"event":"sql","data":{"sql":"SELECT 1"},"hidden":false}\n\n'
        yield 'data: {"event":"complete","data":{"rows":[]},"hidden":false}\n\n'
    return Response(gen(), mimetype="text/event-stream")


# ══════════════════════════════════════════════════════════════════════════
# 8. 会话管理
# ══════════════════════════════════════════════════════════════════════════

@app.route("/api/sessions", methods=["GET"])
def list_sessions():
    allowed = _allowed_ids_set()
    if allowed is None:
        items = list(SESSIONS.values())
    else:
        items = [v for k, v in SESSIONS.items() if k in allowed]
    return jsonify({"today": items})


@app.route("/api/sessions", methods=["POST"])
def create_session():
    body = request.get_json(force=True, silent=True) or {}
    sid = _new_id()
    s = {
        "session_id": sid,
        "database_id": body.get("database_id", ""),
        "title": body.get("title") or "New Session",
        "dialect": "postgresql",
        "created_at": _now(),
        "updated_at": _now(),
        "preview": "",
        "message_count": 0,
    }
    SESSIONS[sid] = s
    # Return `id` so resource-sync (id_field='id') picks it up
    return jsonify({"id": sid, **s}), 201


@app.route("/api/sessions/<session_id>", methods=["DELETE"])
def delete_session(session_id):
    SESSIONS.pop(session_id, None)
    return "", 204


@app.route("/api/sessions/replay", methods=["POST"])
def session_replay():
    return jsonify({"next_offset": None, "has_more": False, "events": []})


@app.route("/api/sessions/<session_id>/replay", methods=["GET"])
def session_replay_stream(session_id):
    def gen():
        yield 'data: {"event":"start"}\n\n'
        yield 'data: {"event":"complete"}\n\n'
    return Response(gen(), mimetype="text/event-stream")


@app.route("/api/sessions/<session_id>/turns", methods=["GET"])
def session_turns(session_id):
    return jsonify([])


@app.route("/api/sessions/<session_id>/turns/<turn_id>/feedback", methods=["POST"])
def session_feedback(session_id, turn_id):
    body = request.get_json(force=True, silent=True) or {}
    return jsonify({
        "turn_info": {
            "turn_idx": int(turn_id) if turn_id.isdigit() else 0,
            "user_query": {"question": "", "context": None, "schema_": None, "hints": None, "user_id": _auth()["user_id"]},
            "total_elapsed_seconds": 1.0,
            "feedback": {"type": body.get("feedback_type"), "timestamp": _now(), "comment": body.get("comment")},
            "status": "completed",
            "error": None,
            "created_at": _now(),
            "completed_at": _now(),
            "mode": "auto",
        },
        "agent_runs": [],
    })


# ══════════════════════════════════════════════════════════════════════════
# 9. 配置管理
# ══════════════════════════════════════════════════════════════════════════

@app.route("/api/config", methods=["GET"])
def config_all():
    return jsonify(CONFIG)


@app.route("/api/config/models", methods=["GET"])
def config_models():
    return jsonify(CONFIG["models"])


@app.route("/api/config/models/<preset_name>", methods=["PUT"])
def config_models_update(preset_name):
    body = request.get_json(force=True, silent=True) or {}
    CONFIG["models"][preset_name] = body
    return jsonify(CONFIG["models"])


@app.route("/api/config/database-providers", methods=["GET"])
def config_db_providers():
    return jsonify(CONFIG["database-providers"])


@app.route("/api/config/database-providers/<provider>", methods=["PUT"])
def config_db_providers_update(provider):
    body = request.get_json(force=True, silent=True) or {}
    CONFIG["database-providers"][provider] = body
    return jsonify(CONFIG["database-providers"])


@app.route("/api/config/language", methods=["GET"])
def config_lang_get():
    return jsonify({"app_lang": CONFIG["app_lang"], "query_lang": CONFIG["query_lang"]})


@app.route("/api/config/language", methods=["PUT"])
def config_lang_set():
    body = request.get_json(force=True, silent=True) or {}
    lang = body.get("language", "zh")
    CONFIG["app_lang"] = lang
    CONFIG["query_lang"] = lang
    return jsonify({"app_lang": lang, "query_lang": lang})


@app.route("/api/config/languages", methods=["PUT"])
def config_langs_set():
    body = request.get_json(force=True, silent=True) or {}
    if body.get("app_lang"):
        CONFIG["app_lang"] = body["app_lang"]
    if body.get("query_lang"):
        CONFIG["query_lang"] = body["query_lang"]
    return jsonify({"app_lang": CONFIG["app_lang"], "query_lang": CONFIG["query_lang"]})


@app.route("/api/config/app", methods=["GET"])
def config_app_get():
    return jsonify(CONFIG["app"])


@app.route("/api/config/app/<key>", methods=["PUT"])
def config_app_set(key):
    body = request.get_json(force=True, silent=True) or {}
    CONFIG["app"][key] = body.get("value")
    return jsonify(CONFIG["app"])


@app.route("/api/config/reload", methods=["POST"])
def config_reload():
    return jsonify({"status": "reloaded"})


@app.route("/api/config/setup", methods=["POST"])
def config_setup():
    return jsonify({"status": "setup_completed"})


@app.route("/api/config/paths/logs", methods=["GET"])
def config_paths_logs():
    return jsonify({"path": "/var/log/rubik.log", "size": 0})


@app.route("/api/config/open-path", methods=["POST"])
def config_open_path():
    return jsonify({"status": "ok"})


@app.route("/api/config/llm-providers", methods=["GET"])
def config_llm_list():
    return jsonify(CONFIG["llm-providers"])


@app.route("/api/config/llm-providers/<name>", methods=["PUT"])
def config_llm_update(name):
    body = request.get_json(force=True, silent=True) or {}
    existing = CONFIG["llm-providers"].get(name, {})
    existing.update(body.get("updates", {}))
    CONFIG["llm-providers"][name] = existing
    return jsonify(CONFIG["llm-providers"])


@app.route("/api/config/llm-providers", methods=["POST"])
def config_llm_create():
    body = request.get_json(force=True, silent=True) or {}
    name = body.get("name", f"llm-{_new_id()}")
    CONFIG["llm-providers"][name] = body
    return jsonify(CONFIG["llm-providers"][name]), 201


@app.route("/api/config/llm-providers/<name>", methods=["DELETE"])
def config_llm_delete(name):
    CONFIG["llm-providers"].pop(name, None)
    return jsonify({"status": "deleted", "name": name})


# ── Catch-all ──────────────────────────────────────────────────────────────

@app.route("/<path:sub>", methods=["GET", "POST", "PUT", "DELETE"])
def catch_all(sub):
    return jsonify({
        "path": f"/{sub}",
        "method": request.method,
        "_auth": _auth(),
        "body": request.get_json(silent=True) or {},
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081)
