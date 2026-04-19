"""
Mock Knowledge Base backend for end-to-end IAM testing.

Simulates KB APIs: creates/queries/deletes knowledge bases.
Returns proper status codes (201 for create, 200 for query, 204 for delete)
so that ext_proc (resource-sync) can capture resource lifecycle events.
"""
from flask import Flask, request, jsonify
import uuid

app = Flask(__name__)

# In-memory store
KB_STORE = {}
COUNTER = 100


def _auth_headers():
    return {
        "user_id": request.headers.get("X-Auth-User-Id", "unknown"),
        "tenant": request.headers.get("X-Auth-Tenant", "unknown"),
        "groups": request.headers.get("X-Auth-Groups", ""),
    }


@app.route("/knowledge_bases", methods=["GET"])
def list_kb():
    auth = _auth_headers()
    allowed = request.headers.get("X-Allowed-Ids", "")
    allowed_ids = [x.strip() for x in allowed.split(",") if x.strip()] if allowed else None

    if allowed_ids is not None:
        result = [v for k, v in KB_STORE.items() if k in allowed_ids]
    else:
        result = list(KB_STORE.values())

    return jsonify({
        "_auth": auth,
        "_allowed_ids_header": allowed,
        "items": result,
        "total": len(result),
    })


@app.route("/knowledge_bases/page", methods=["GET"])
def list_kb_page():
    return list_kb()


@app.route("/knowledge_bases/count", methods=["GET"])
def count_kb():
    return jsonify({"count": len(KB_STORE)})


@app.route("/knowledge_bases/add", methods=["POST"])
def create_kb():
    global COUNTER
    auth = _auth_headers()
    data = request.get_json(force=True, silent=True) or {}
    COUNTER += 1
    kb_id = str(COUNTER)
    kb = {
        "KDSID": kb_id,
        "NAME": data.get("NAME", "unnamed"),
        "DESCRIPTION": data.get("DESCRIPTION", ""),
        "owner": auth["user_id"],
    }
    KB_STORE[kb_id] = kb
    return jsonify(kb), 201


@app.route("/knowledge_bases/modify", methods=["POST"])
def modify_kb():
    data = request.get_json(force=True, silent=True) or {}
    kb_id = data.get("KDSID")
    if not kb_id or kb_id not in KB_STORE:
        return jsonify({"error": "not found"}), 404
    KB_STORE[kb_id].update({k: v for k, v in data.items() if k != "KDSID"})
    return jsonify(KB_STORE[kb_id])


@app.route("/knowledge_bases/remove", methods=["POST"])
def delete_kb():
    data = request.get_json(force=True, silent=True) or {}
    kb_id = data.get("KDSID")
    if kb_id and kb_id in KB_STORE:
        del KB_STORE[kb_id]
    return "", 204


@app.route("/models/config", methods=["GET"])
def list_models():
    return jsonify({"models": [], "_auth": _auth_headers()})


@app.route("/models/config/add", methods=["POST"])
def add_model():
    return jsonify({"status": "ok", "_auth": _auth_headers()}), 201


@app.route("/conversations/start", methods=["POST"])
def chat():
    return jsonify({"answer": "mock response", "_auth": _auth_headers()})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


# Catch-all for other /kb/* paths
@app.route("/<path:path>", methods=["GET", "POST", "PUT", "DELETE"])
def catch_all(path):
    return jsonify({
        "path": f"/{path}",
        "method": request.method,
        "_auth": _auth_headers(),
        "_headers": {k: v for k, v in request.headers if k.startswith("X-")},
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
