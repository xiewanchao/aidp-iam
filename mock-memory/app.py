"""Mock UnifiedMem backend for IAM end-to-end testing.

Implements all /api/v1/* endpoints from diagrams/api-specs/memory/api.md.
Gateway rewrites /memory/* -> /*, so this backend sees raw /api/... paths.

Two planes (see api.md):
  - 管理面: /api/v1/tenants, /api/v1/templates — memory-admins only (authz)
  - 数据面: /api/v1/memory, /api/v1/health, /api/v1/system — all-users (authz)

All endpoints return plausible 2xx responses. In-memory storage, resets on
pod restart. @app.after_request echoes X-Debug-* headers to mirror mock-kb
/ mock-rubik so tests can assert ext_proc injection end-to-end.
"""
from flask import Flask, request, jsonify
import uuid
import time

app = Flask(__name__)

TENANTS = {}        # tenant_id -> {"tenant_id": ..., "instances": {...}, "created_at": ...}
TEMPLATES = {}      # template_id -> template
MEMORIES = {}       # ack_id -> memory


def _new_id():
    return str(uuid.uuid4())[:8]


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ")


@app.after_request
def _echo_debug_headers(resp):
    """Echo auth + X-Allowed-Ids as X-Debug-* response headers."""
    resp.headers["X-Debug-User-Id"]       = request.headers.get("X-Auth-User-Id", "")
    resp.headers["X-Debug-Tenant"]        = request.headers.get("X-Auth-Tenant", "")
    resp.headers["X-Debug-Groups"]        = request.headers.get("X-Auth-Groups", "")
    resp.headers["X-Debug-Allowed-Ids"]   = request.headers.get("X-Allowed-Ids", "")
    resp.headers["X-Debug-Allowed-Total"] = request.headers.get("X-Allowed-Total", "")
    return resp


def _auth_user():
    return request.headers.get("X-Auth-User-Id", "anonymous")


# ══════════════════════════════════════════════════════════════════════════
# 数据面 · 1. 系统管理
# ══════════════════════════════════════════════════════════════════════════

@app.route("/health")
def health():
    """Kubernetes readiness/liveness probe (outside /api/v1 namespace)."""
    return jsonify({"status": "healthy", "service": "mock-memory", "version": "1.0.0"})


@app.route("/api/v1/health", methods=["GET"])
def api_health():
    return jsonify({"status": "healthy", "service": "UnifiedMem", "version": "1.0.0"})


@app.route("/api/v1/system/recovery", methods=["POST"])
def system_recovery():
    body = request.get_json(silent=True) or {}
    return jsonify({
        "recovered": 0, "failed": 0, "stale": 0,
        "details": [], "completed_at": _now(),
        "recovery_point": body.get("recovery_point", "latest"),
        "scope": body.get("scope", "all"),
    })


# ══════════════════════════════════════════════════════════════════════════
# 数据面 · 2. 记忆管理
# ══════════════════════════════════════════════════════════════════════════

@app.route("/api/v1/memory/add", methods=["POST"])
def memory_add():
    body = request.get_json(force=True, silent=True) or {}
    ack_id = str(uuid.uuid4())
    MEMORIES[ack_id] = {
        "ack_id": ack_id,
        "tenant_id":   body.get("tenant_id", "default"),
        "instance_id": body.get("instance_id", "default"),
        "user_id":     body.get("user_id", "default"),
        "content":     body.get("content", ""),
        "metadata":    body.get("metadata", {}),
        "created_by":  _auth_user(),
        "created_at":  _now(),
    }
    return jsonify({
        "ack_id": ack_id,
        "status": "SUCCESS",
        "message": f"Memory queued. Window: {uuid.uuid4()}, offset: {len(MEMORIES)}",
    }), 201


@app.route("/api/v1/memory/query", methods=["POST"])
def memory_query():
    body = request.get_json(force=True, silent=True) or {}
    query = body.get("query", "")
    user_id = body.get("user_id", "")
    # Return any memory for this user containing the query substring (mock)
    memories = []
    for m in MEMORIES.values():
        if user_id and m.get("user_id") != user_id:
            continue
        if query and query not in m.get("content", ""):
            continue
        memories.append({
            "memory_id": f"col_{m['tenant_id']}_{m['instance_id']}_user_{m['ack_id'][:6]}",
            "content": m["content"],
            "memory_type": "fact",
            "created_at": m["created_at"],
            "relevance_score": 0.95,
        })
    memories = memories[:int(body.get("top_k", 10))]
    return jsonify({"memories": memories, "total": len(memories)})


@app.route("/api/v1/memory/update", methods=["POST"])
def memory_update():
    body = request.get_json(force=True, silent=True) or {}
    memory_id = body.get("memory_id", "")
    return jsonify({"status": "SUCCESS", "message": "更新成功", "memory_id": memory_id})


@app.route("/api/v1/memory/delete", methods=["POST"])
def memory_delete():
    body = request.get_json(force=True, silent=True) or {}
    memory_id = body.get("memory_id", "")
    return jsonify({"status": "SUCCESS", "message": "删除成功", "memory_id": memory_id})


# ══════════════════════════════════════════════════════════════════════════
# 管理面 · 3. 租户和实例管理
# ══════════════════════════════════════════════════════════════════════════

@app.route("/api/v1/tenants", methods=["POST"])
def create_tenant():
    body = request.get_json(force=True, silent=True) or {}
    tid = body.get("tenant_id") or _new_id()
    TENANTS[tid] = {
        "tenant_id": tid, "instances": {}, "created_at": _now(),
        "created_by": _auth_user(),
    }
    return jsonify({"status": "success", "message": "租户创建成功", "tenant_id": tid}), 201


@app.route("/api/v1/tenants/<tenant_id>", methods=["DELETE"])
def delete_tenant(tenant_id):
    TENANTS.pop(tenant_id, None)
    return jsonify({"status": "success", "message": "租户删除成功", "tenant_id": tenant_id})


@app.route("/api/v1/tenants/<tenant_id>/instances", methods=["POST"])
def create_instance(tenant_id):
    body = request.get_json(force=True, silent=True) or {}
    name = body.get("instance_name") or _new_id()
    TENANTS.setdefault(tenant_id, {"tenant_id": tenant_id, "instances": {}, "created_at": _now()})
    TENANTS[tenant_id]["instances"][name] = {"name": name, "created_at": _now()}
    return jsonify({
        "status": "success", "message": "实例创建成功",
        "tenant_id": tenant_id, "instance_name": name,
    }), 201


@app.route("/api/v1/tenants/<tenant_id>/instances/<instance_name>", methods=["DELETE"])
def delete_instance(tenant_id, instance_name):
    if tenant_id in TENANTS:
        TENANTS[tenant_id]["instances"].pop(instance_name, None)
    return jsonify({
        "status": "success", "message": "实例删除成功",
        "tenant_id": tenant_id, "instance_name": instance_name,
    })


@app.route(
    "/api/v1/tenants/<tenant_id>/instances/<instance_name>/users/<user_id>/memories",
    methods=["DELETE"],
)
def delete_user_memories(tenant_id, instance_name, user_id):
    # Pretend to remove; in the mock we just count user's memories
    removed = [k for k, v in MEMORIES.items() if v.get("user_id") == user_id]
    for k in removed:
        MEMORIES.pop(k, None)
    return jsonify({
        "status": "success", "message": "用户记忆删除成功",
        "tenant_id": tenant_id, "instance_name": instance_name,
        "user_id": user_id, "removed_count": len(removed),
    })


# ══════════════════════════════════════════════════════════════════════════
# 管理面 · 4. 模板管理
# ══════════════════════════════════════════════════════════════════════════

@app.route("/api/v1/templates", methods=["POST"])
def create_template():
    body = request.get_json(force=True, silent=True) or {}
    tmpl = body.get("template_data", body)
    tid = tmpl.get("template_id") or _new_id()
    tmpl["template_id"] = tid
    tmpl.setdefault("template_name", f"tmpl-{tid}")
    tmpl.setdefault("memory_type", "fact")
    tmpl.setdefault("status", "SUCCESS")
    tmpl.setdefault("created_at", _now())
    tmpl.setdefault("filters", {"whitelist": [], "blacklist": []})
    tmpl.setdefault("enable_llm_extraction", False)
    TEMPLATES[tid] = tmpl
    return jsonify({"success": True, "template": tmpl}), 201


@app.route("/api/v1/templates", methods=["GET"])
def list_templates():
    return jsonify({"templates": list(TEMPLATES.values()), "total": len(TEMPLATES)})


@app.route("/api/v1/templates/<template_id>", methods=["GET"])
def get_template(template_id):
    if template_id not in TEMPLATES:
        return jsonify({"status": "error", "reason": "not found"}), 404
    return jsonify(TEMPLATES[template_id])


@app.route("/api/v1/templates/<template_id>", methods=["PUT"])
def update_template(template_id):
    if template_id not in TEMPLATES:
        return jsonify({"status": "error", "reason": "not found"}), 404
    body = request.get_json(force=True, silent=True) or {}
    updates = body.get("update_data", body)
    TEMPLATES[template_id].update({k: v for k, v in updates.items() if k != "template_id"})
    return jsonify(TEMPLATES[template_id])


@app.route("/api/v1/templates/<template_id>", methods=["DELETE"])
def delete_template(template_id):
    TEMPLATES.pop(template_id, None)
    return jsonify({"status": "success", "message": f"Template {template_id} deleted"})


@app.route("/api/v1/templates/<template_id>/filters", methods=["POST"])
def update_template_filters(template_id):
    body = request.get_json(force=True, silent=True) or {}
    filters = {"whitelist": body.get("whitelist", []), "blacklist": body.get("blacklist", [])}
    if template_id in TEMPLATES:
        TEMPLATES[template_id]["filters"] = filters
    return jsonify(filters)


@app.route("/api/v1/templates/<template_id>/filters", methods=["GET"])
def get_template_filters(template_id):
    if template_id in TEMPLATES:
        return jsonify(TEMPLATES[template_id].get("filters", {"whitelist": [], "blacklist": []}))
    return jsonify({"whitelist": [], "blacklist": []})


@app.route("/api/v1/templates/<template_id>/llm-extraction", methods=["POST"])
def set_template_llm_extraction(template_id):
    body = request.get_json(force=True, silent=True) or {}
    enable = bool(body.get("enable", True))
    if template_id in TEMPLATES:
        TEMPLATES[template_id]["enable_llm_extraction"] = enable
    return jsonify({
        "status": "success",
        "template_id": template_id,
        "enable_llm_extraction": enable,
    })


@app.route("/api/v1/templates/batch/llm-extraction", methods=["POST"])
def batch_set_llm_extraction():
    body = request.get_json(force=True, silent=True) or {}
    updates = body.get("update_map", {}) or {}
    count = 0
    for tid, enable in updates.items():
        if tid in TEMPLATES:
            TEMPLATES[tid]["enable_llm_extraction"] = bool(enable)
            count += 1
    return jsonify({"status": "success", "updated_count": count})


# ══════════════════════════════════════════════════════════════════════════
# Seed baseline data
# ══════════════════════════════════════════════════════════════════════════

def _seed():
    TENANTS["seed_tenant"] = {
        "tenant_id": "seed_tenant",
        "instances": {"seed_instance": {"name": "seed_instance", "created_at": _now()}},
        "created_at": _now(),
        "created_by": "system",
    }
    TEMPLATES["user_profile"] = {
        "template_id": "user_profile",
        "template_name": "User Profile",
        "memory_type": "fact",
        "description": "用户基本信息模板（预置）",
        "status": "SUCCESS",
        "enable_llm_extraction": True,
        "created_at": _now(),
        "filters": {"whitelist": [], "blacklist": []},
    }


_seed()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8082)
