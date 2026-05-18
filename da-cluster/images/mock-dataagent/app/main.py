"""
Mock DataAgent backend for AIDP IAM e2e tests.

Implements the DataAgent API surface:
  /DataAgent/Tenants/{tid}/Databases/{db_id}
  /DataAgent/Tenants/{tid}/Databases/{db_id}/Metadata
  /DataAgent/Tenants/{tid}/Databases/{db_id}/Schema
  /DataAgent/Tenants/{tid}/Databases/{db_id}/Columns
  /DataAgent/Tenants/{tid}/Databases/{db_id}/Tables
  /DataAgent/Tenants/{tid}/Databases/{db_id}/Knowledge/{item_id}
  /DataAgent/Tenants/{tid}/Databases/{db_id}/TaxonomyKL/{item_id}
  /DataAgent/Tenants/{tid}/Databases/{db_id}/CustomKL/{item_id}
  /DataAgent/Tenants/{tid}/Databases/{db_id}/ExperienceKL/{item_id}
  /DataAgent/Tenants/{tid}/Databases/{db_id}/Skill/{item_id}
  /DataAgent/Tenants/{tid}/Databases/{db_id}/LogicalColumnKL/{item_id}
  /DataAgent/Tenants/{tid}/Databases/SpecialKL/{item_id}
  /DataAgent/Tenants/{tid}/Sessions/{session_id}
  /DataAgent/Tenants/{tid}/Sessions/{session_id}/Turns

All state is in-memory. The gateway injects X-Allowed-Ids on list requests;
this mock honours it for Databases and Sessions list filtering.
"""
from flask import Flask, request, jsonify, Response
import json

app = Flask(__name__)

_databases  = {}   # key: "{tid}/{db_id}"
_knowledge  = {}   # key: "{tid}/{db_id}/{item_id}"
_specialkl  = {}   # key: "{tid}/{item_id}"
_sessions   = {}   # key: "{tid}/{session_id}"
_turns      = {}   # key: "{tid}/{session_id}" → list of turn dicts

def _j(obj, status=200):
    return Response(json.dumps(obj), status=status, mimetype="application/json")

def _404(msg):
    return _j({"detail": msg}, 404)

def _allowed(raw_header):
    """Parse X-Allowed-Ids header into a set, or None if absent."""
    if not raw_header:
        return None
    ids = {x.strip() for x in raw_header.split(",") if x.strip()}
    return ids if ids else None


# ── health ────────────────────────────────────────────────────────────────────
@app.get("/health")
@app.get("/DataAgent/health")
def health():
    return _j({"status": "ok", "service": "mock-dataagent"})


# ── Databases ─────────────────────────────────────────────────────────────────
@app.get("/DataAgent/Tenants/<tid>/Databases")
def list_databases(tid):
    allowed = _allowed(request.headers.get("x-allowed-ids", ""))
    items = [
        v for k, v in _databases.items()
        if k.startswith(f"{tid}/") and (allowed is None or v["db_id"] in allowed)
    ]
    return _j({"items": items, "total": len(items)})


@app.get("/DataAgent/Tenants/<tid>/Databases/<db_id>")
def get_database(tid, db_id):
    obj = _databases.get(f"{tid}/{db_id}")
    if not obj:
        return _404("database not found")
    return _j(obj)


@app.put("/DataAgent/Tenants/<tid>/Databases/<db_id>")
def put_database(tid, db_id):
    key = f"{tid}/{db_id}"
    body = request.get_json(silent=True) or {}
    existed = key in _databases
    _databases[key] = {"tenant_id": tid, "db_id": db_id, **body}
    return _j(_databases[key], 200 if existed else 201)


@app.delete("/DataAgent/Tenants/<tid>/Databases/<db_id>")
def delete_database(tid, db_id):
    key = f"{tid}/{db_id}"
    if key not in _databases:
        return _404("database not found")
    del _databases[key]
    # cascade: remove all knowledge under this db
    for store in (_knowledge,):
        for k in list(store.keys()):
            if k.startswith(f"{tid}/{db_id}/"):
                del store[k]
    return Response(status=204)


# Database type-level actions (no db_id)
@app.post("/DataAgent/Tenants/<tid>/Databases/Test")
def db_test(tid):
    return _j({"status": "ok", "action": "Test"})

@app.post("/DataAgent/Tenants/<tid>/Databases/Check")
def db_check(tid):
    return _j({"status": "ok", "action": "Check"})

# Database instance-level actions (have db_id)
for _action in ("PrettifySql", "ExecuteSql", "Build", "StreamBuild",
                "Cancel", "CheckRefresh", "StreamRefresh"):
    def _make_db_action(action_name):
        def _handler(tid, db_id):
            if f"{tid}/{db_id}" not in _databases:
                return _404("database not found")
            return _j({"status": "ok", "action": action_name})
        _handler.__name__ = f"db_action_{action_name.lower()}"
        return _handler
    app.post(f"/DataAgent/Tenants/<tid>/Databases/<db_id>/{_action}")(_make_db_action(_action))


# ── Metadata (singleton) ──────────────────────────────────────────────────────
@app.get("/DataAgent/Tenants/<tid>/Databases/<db_id>/Metadata")
def get_metadata(tid, db_id):
    if f"{tid}/{db_id}" not in _databases:
        return _404("database not found")
    return _j({"tenant_id": tid, "db_id": db_id, "type": "Metadata"})

@app.post("/DataAgent/Tenants/<tid>/Databases/<db_id>/Metadata/Init")
def metadata_init(tid, db_id):
    if f"{tid}/{db_id}" not in _databases:
        return _404("database not found")
    return _j({"status": "ok", "action": "Init"})

@app.post("/DataAgent/Tenants/<tid>/Databases/<db_id>/Metadata/Process")
def metadata_process(tid, db_id):
    if f"{tid}/{db_id}" not in _databases:
        return _404("database not found")
    return _j({"status": "ok", "action": "Process"})


# ── Schema / Columns / Tables (singletons) ────────────────────────────────────
for _singleton in ("Schema", "Columns", "Tables"):
    def _make_singleton(name):
        def _handler(tid, db_id):
            if f"{tid}/{db_id}" not in _databases:
                return _404("database not found")
            return _j({"tenant_id": tid, "db_id": db_id, "type": name})
        _handler.__name__ = f"get_{name.lower()}"
        return _handler
    app.get(f"/DataAgent/Tenants/<tid>/Databases/<db_id>/{_singleton}")(_make_singleton(_singleton))


# ── Knowledge ─────────────────────────────────────────────────────────────────
@app.get("/DataAgent/Tenants/<tid>/Databases/<db_id>/Knowledge")
def list_knowledge(tid, db_id):
    prefix = f"{tid}/{db_id}/"
    items = [v for k, v in _knowledge.items() if k.startswith(prefix)]
    return _j({"items": items, "total": len(items)})

@app.get("/DataAgent/Tenants/<tid>/Databases/<db_id>/Knowledge/<item_id>")
def get_knowledge(tid, db_id, item_id):
    obj = _knowledge.get(f"{tid}/{db_id}/{item_id}")
    if not obj:
        return _404("knowledge not found")
    return _j(obj)

@app.put("/DataAgent/Tenants/<tid>/Databases/<db_id>/Knowledge/<item_id>")
def put_knowledge(tid, db_id, item_id):
    if f"{tid}/{db_id}" not in _databases:
        return _404("parent database not found")
    key = f"{tid}/{db_id}/{item_id}"
    body = request.get_json(silent=True) or {}
    existed = key in _knowledge
    _knowledge[key] = {"tenant_id": tid, "db_id": db_id, "item_id": item_id, "item_id_str": item_id, **body}
    return _j(_knowledge[key], 200 if existed else 201)

@app.patch("/DataAgent/Tenants/<tid>/Databases/<db_id>/Knowledge/<item_id>")
def patch_knowledge(tid, db_id, item_id):
    key = f"{tid}/{db_id}/{item_id}"
    if key not in _knowledge:
        return _404("knowledge not found")
    _knowledge[key].update(request.get_json(silent=True) or {})
    return _j(_knowledge[key])

@app.delete("/DataAgent/Tenants/<tid>/Databases/<db_id>/Knowledge/<item_id>")
def delete_knowledge(tid, db_id, item_id):
    key = f"{tid}/{db_id}/{item_id}"
    if key not in _knowledge:
        return _404("knowledge not found")
    del _knowledge[key]
    return Response(status=204)

# Knowledge type-level actions
@app.post("/DataAgent/Tenants/<tid>/Databases/<db_id>/Knowledge/GetTypes")
def knowledge_gettypes(tid, db_id):
    return _j({"status": "ok", "action": "GetTypes", "types": ["taxonomy", "custom", "experience", "skill"]})

@app.post("/DataAgent/Tenants/<tid>/Databases/<db_id>/Knowledge/Import")
def knowledge_import(tid, db_id):
    return _j({"status": "ok", "action": "Import"})

@app.post("/DataAgent/Tenants/<tid>/Databases/<db_id>/Knowledge/Export")
def knowledge_export(tid, db_id):
    return _j({"status": "ok", "action": "Export"})


# ── Knowledge sub-types (TaxonomyKL, CustomKL, ExperienceKL, Skill, LogicalColumnKL) ──
_kl_stores = {}  # key: "{type}/{tid}/{db_id}/{item_id}"

for _kl_type in ("TaxonomyKL", "CustomKL", "ExperienceKL", "Skill", "LogicalColumnKL"):
    def _make_kl_put(ktype):
        def _handler(tid, db_id, item_id):
            if f"{tid}/{db_id}" not in _databases:
                return _404("parent database not found")
            key = f"{ktype}/{tid}/{db_id}/{item_id}"
            body = request.get_json(silent=True) or {}
            existed = key in _kl_stores
            _kl_stores[key] = {"tenant_id": tid, "db_id": db_id, "item_id": item_id, "type": ktype, **body}
            return _j(_kl_stores[key], 200 if existed else 201)
        _handler.__name__ = f"put_{ktype.lower()}"
        return _handler

    def _make_kl_patch(ktype):
        def _handler(tid, db_id, item_id):
            key = f"{ktype}/{tid}/{db_id}/{item_id}"
            if key not in _kl_stores:
                return _404(f"{ktype} not found")
            _kl_stores[key].update(request.get_json(silent=True) or {})
            return _j(_kl_stores[key])
        _handler.__name__ = f"patch_{ktype.lower()}"
        return _handler

    app.put(f"/DataAgent/Tenants/<tid>/Databases/<db_id>/{_kl_type}/<item_id>")(_make_kl_put(_kl_type))
    if _kl_type in ("Skill", "LogicalColumnKL"):
        app.patch(f"/DataAgent/Tenants/<tid>/Databases/<db_id>/{_kl_type}/<item_id>")(_make_kl_patch(_kl_type))


# ── SpecialKL (collection-level sub-resource, no db_id) ──────────────────────
_specialkl = {}  # key: "{tid}/{item_id}"

@app.put("/DataAgent/Tenants/<tid>/Databases/SpecialKL/<item_id>")
def put_specialkl(tid, item_id):
    key = f"{tid}/{item_id}"
    body = request.get_json(silent=True) or {}
    existed = key in _specialkl
    _specialkl[key] = {"tenant_id": tid, "item_id": item_id, "item_id_str": item_id, **body}
    return _j(_specialkl[key], 200 if existed else 201)

@app.patch("/DataAgent/Tenants/<tid>/Databases/SpecialKL/<item_id>")
def patch_specialkl(tid, item_id):
    key = f"{tid}/{item_id}"
    if key not in _specialkl:
        return _404("SpecialKL not found")
    _specialkl[key].update(request.get_json(silent=True) or {})
    return _j(_specialkl[key])

@app.delete("/DataAgent/Tenants/<tid>/Databases/SpecialKL/<item_id>")
def delete_specialkl(tid, item_id):
    key = f"{tid}/{item_id}"
    if key not in _specialkl:
        return _404("SpecialKL not found")
    del _specialkl[key]
    return Response(status=204)


# ── Sessions ──────────────────────────────────────────────────────────────────
@app.get("/DataAgent/Tenants/<tid>/Sessions")
def list_sessions(tid):
    allowed = _allowed(request.headers.get("x-allowed-ids", ""))
    items = [
        v for k, v in _sessions.items()
        if k.startswith(f"{tid}/") and (allowed is None or v["session_id"] in allowed)
    ]
    return _j({"items": items, "total": len(items)})

@app.get("/DataAgent/Tenants/<tid>/Sessions/<session_id>")
def get_session(tid, session_id):
    obj = _sessions.get(f"{tid}/{session_id}")
    if not obj:
        return _404("session not found")
    return _j(obj)

@app.put("/DataAgent/Tenants/<tid>/Sessions/<session_id>")
def put_session(tid, session_id):
    key = f"{tid}/{session_id}"
    body = request.get_json(silent=True) or {}
    existed = key in _sessions
    _sessions[key] = {"tenant_id": tid, "session_id": session_id, **body}
    if key not in _turns:
        _turns[key] = []
    return _j(_sessions[key], 200 if existed else 201)

@app.delete("/DataAgent/Tenants/<tid>/Sessions/<session_id>")
def delete_session(tid, session_id):
    key = f"{tid}/{session_id}"
    if key not in _sessions:
        return _404("session not found")
    del _sessions[key]
    _turns.pop(key, None)
    return Response(status=204)

@app.post("/DataAgent/Tenants/<tid>/Sessions/<session_id>/Replay")
def session_replay(tid, session_id):
    if f"{tid}/{session_id}" not in _sessions:
        return _404("session not found")
    return _j({"status": "ok", "action": "Replay"})


# ── Turns (singleton list under Session) ─────────────────────────────────────
@app.get("/DataAgent/Tenants/<tid>/Sessions/<session_id>/Turns")
def get_turns(tid, session_id):
    key = f"{tid}/{session_id}"
    if key not in _sessions:
        return _404("session not found")
    turns = _turns.get(key, [])
    return _j({"items": turns, "total": len(turns)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
