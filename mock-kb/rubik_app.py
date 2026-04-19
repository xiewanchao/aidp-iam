"""Mock RubikSQL backend for IAM end-to-end testing."""
from flask import Flask, request, jsonify
import uuid

app = Flask(__name__)
DB_STORE = {}
SESSION_STORE = {}

def _auth():
    return {
        "user_id": request.headers.get("X-Auth-User-Id", "unknown"),
        "tenant": request.headers.get("X-Auth-Tenant", "unknown"),
        "groups": request.headers.get("X-Auth-Groups", ""),
    }

# --- Databases ---
@app.route("/api/databases", methods=["GET"])
def list_databases():
    return jsonify(list(DB_STORE.values()))

@app.route("/api/databases", methods=["POST"])
def create_database():
    data = request.get_json(force=True, silent=True) or {}
    db_id = str(uuid.uuid4())[:8]
    db = {"id": db_id, "name": data.get("name", "unnamed"), "type": data.get("type", "postgresql"),
          "created_by": _auth()["user_id"], "kb_built": False}
    DB_STORE[db_id] = db
    return jsonify(db), 201

@app.route("/api/databases/<db_id>", methods=["GET"])
def get_database(db_id):
    if db_id not in DB_STORE:
        return jsonify({"error": "not found"}), 404
    return jsonify(DB_STORE[db_id])

@app.route("/api/databases/<db_id>", methods=["DELETE"])
def delete_database(db_id):
    DB_STORE.pop(db_id, None)
    return jsonify({"status": "deleted", "id": db_id}), 200

@app.route("/api/databases/<db_id>/schema", methods=["GET"])
def get_schema(db_id):
    return jsonify([{"name": "public", "type": "schema", "children": []}])

@app.route("/api/databases/<db_id>/build", methods=["POST"])
def build_kb(db_id):
    return jsonify({"database_id": db_id, "status": "completed"}), 200

@app.route("/api/databases/<db_id>/knowledge/<path:sub>", methods=["GET", "POST", "PUT", "DELETE"])
def knowledge(db_id, sub):
    if request.method == "POST":
        return jsonify({"id_str": str(uuid.uuid4())[:8], "type": "custom", "name": "test"}), 201
    return jsonify({"items": [], "total": 0})

@app.route("/api/databases/<db_id>/skill/<path:sub>", methods=["POST", "PUT"])
def skill(db_id, sub):
    return jsonify({"id_str": str(uuid.uuid4())[:8], "type": "skill"}), 201

@app.route("/api/databases/<db_id>/sync", methods=["POST"])
def sync(db_id):
    return jsonify({"success": True, "message": "synced"})

# --- Query ---
@app.route("/api/query", methods=["POST"])
def query():
    return jsonify({"event": "complete", "data": {"sql": "SELECT 1"}}), 200

# --- Sessions ---
@app.route("/api/sessions", methods=["GET"])
def list_sessions():
    return jsonify({"today": list(SESSION_STORE.values())})

@app.route("/api/sessions", methods=["POST"])
def create_session():
    data = request.get_json(force=True, silent=True) or {}
    sid = str(uuid.uuid4())[:8]
    s = {"session_id": sid, "database_id": data.get("database_id", ""), "title": data.get("title", "New")}
    SESSION_STORE[sid] = s
    return jsonify(s), 201

@app.route("/api/sessions/<sid>", methods=["DELETE"])
def delete_session(sid):
    SESSION_STORE.pop(sid, None)
    return "", 204

# --- Config ---
@app.route("/api/config", methods=["GET"])
def get_config():
    return jsonify({"language": "zh"})

@app.route("/api/config/<path:sub>", methods=["GET", "PUT", "POST", "DELETE"])
def config_sub(sub):
    if request.method == "GET":
        return jsonify({})
    return jsonify({"status": "updated"}), 200

# --- Metadata ---
@app.route("/api/metadata/<path:sub>", methods=["GET", "PUT"])
def metadata(sub):
    return jsonify({"status": "ok"})

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

# Catch-all
@app.route("/<path:path>", methods=["GET", "POST", "PUT", "DELETE"])
def catch_all(path):
    return jsonify({"path": f"/{path}", "method": request.method, "_auth": _auth()})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081)
