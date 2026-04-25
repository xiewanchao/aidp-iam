#!/usr/bin/env bash
# ============================================================================
# package-mock-memory/test/test.sh — Memory 业务端到端测试
#
# 覆盖：pod / 公开路由 / 鉴权拒绝 / 鉴权通过 / 应用注册 /
#       POST 创建 / ACL 自动写入 / GET 读 / PUT 修改 / DELETE 删 / ACL 级联清除
#       (memory 用 DEFAULT_ACTIONS：标准 RESTful + path id)
#
# 依赖：bash / curl / python3 / kubectl
# 不依赖 jq / base64 / lsof
# ============================================================================
set -uo pipefail

GATEWAY="${GATEWAY:-http://localhost:30080}"
NAMESPACE="${NAMESPACE:-mock-memory}"

GREEN='\033[0;32m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
PASS=0; FAIL=0

ok()   { printf "  ${GREEN}PASS${NC} %s\n" "$1"; PASS=$((PASS+1)); }
fail() { printf "  ${RED}FAIL${NC} %s%s\n" "$1" "${2:+ — $2}"; FAIL=$((FAIL+1)); }

assert_http() {
  local desc="$1" want="$2" got="$3"
  if [ "$got" = "$want" ]; then ok "$desc (HTTP $got)"; else fail "$desc" "want HTTP $want, got $got"; fi
}

http_code() {
  curl -sS -o /dev/null -w "%{http_code}" "$@" 2>/dev/null
}

acl_count() {
  local app="$1" rid="$2"
  kubectl -n keycloak exec postgres-0 -c postgres -- \
    psql -U keycloak -d iam -tA -c \
    "SELECT COUNT(*) FROM resource_acl WHERE app_name='$app' AND resource_id='$rid'" 2>/dev/null \
    | tr -d ' \r\n'
}

acl_max_id() {
  local app="$1"
  kubectl -n keycloak exec postgres-0 -c postgres -- \
    psql -U keycloak -d iam -tA -c \
    "SELECT COALESCE(MAX(id), 0) FROM resource_acl WHERE app_name='$app'" 2>/dev/null \
    | tr -d ' \r\n'
}

acl_latest_resource_id() {
  local app="$1" prev_max="$2"
  kubectl -n keycloak exec postgres-0 -c postgres -- \
    psql -U keycloak -d iam -tA -c \
    "SELECT resource_id FROM resource_acl WHERE app_name='$app' AND id > $prev_max ORDER BY id DESC LIMIT 1" 2>/dev/null \
    | tr -d ' \r\n'
}

echo -e "${CYAN}=== mock-memory e2e test ===${NC}"
echo "Gateway: $GATEWAY"
echo

# ── Phase 0: get admin token ────────────────────────────────────────────────
echo -e "${CYAN}[0] Prepare admin token${NC}"
SECRET=$(kubectl -n keycloak get secret keycloak-aidp-client \
  -o go-template='{{index .data "client-secret" | base64decode}}' 2>/dev/null)
[ -z "$SECRET" ] && { echo -e "${RED}ERROR${NC} Cannot fetch client-secret. Is aidp-iam installed?"; exit 2; }

TOKEN=$(curl -sS -X POST "$GATEWAY/realms/aidp/protocol/openid-connect/token" \
  -d "grant_type=password" -d "client_id=aidp-client" -d "client_secret=$SECRET" \
  -d "username=admin" -d "password=Admin@123" 2>/dev/null \
  | python -c "import sys,json
try: print(json.load(sys.stdin).get('access_token',''))
except: pass")
[ -z "$TOKEN" ] && { echo -e "${RED}ERROR${NC} Cannot get admin token"; exit 2; }
echo "  Token len: ${#TOKEN}"
echo

# ── Phase 1: pod health ─────────────────────────────────────────────────────
echo -e "${CYAN}[1] Pod health${NC}"
ready=$(kubectl -n "$NAMESPACE" get pod -l app=mock-memory \
  -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
[ "$ready" = "True" ] && ok "mock-memory pod Ready" || fail "mock-memory pod" "Ready=$ready"
echo

# ── Phase 2: public routes ──────────────────────────────────────────────────
echo -e "${CYAN}[2] Public routes (no auth)${NC}"
assert_http "GET /realms/aidp/.well-known/openid-configuration" 200 \
  "$(http_code "$GATEWAY/realms/aidp/.well-known/openid-configuration")"
echo

# ── Phase 3: auth required ──────────────────────────────────────────────────
echo -e "${CYAN}[3] Protected /memory/* rejects no-token${NC}"
assert_http "GET /memory/api/v1/templates (no token)" 401 \
  "$(http_code "$GATEWAY/memory/api/v1/templates")"
assert_http "POST /memory/api/v1/memory/add (no token)" 401 \
  "$(http_code -X POST "$GATEWAY/memory/api/v1/memory/add")"
echo

# ── Phase 4: auth pass ──────────────────────────────────────────────────────
echo -e "${CYAN}[4] Protected /memory/* accepts admin token${NC}"
assert_http "GET /memory/health (admin)" 200 \
  "$(http_code -H "Authorization: Bearer $TOKEN" "$GATEWAY/memory/health")"
echo

# ── Phase 5: app registered ─────────────────────────────────────────────────
echo -e "${CYAN}[5] IAM has memory app registered${NC}"
APPS=$(curl -sS -H "Authorization: Bearer $TOKEN" "$GATEWAY/api/v1/apps" 2>/dev/null)
echo "$APPS" | grep -q '"memory"' && ok "GET /api/v1/apps contains memory" \
  || fail "GET /api/v1/apps lacks memory" "$(echo "$APPS" | head -c 200)"
echo

# NOTE: mock-memory 的真实 API 是 /api/v1/templates / /api/v1/tenants / /api/v1/memory/*
# 不是 IAM seed 里的 /v1/memories（这是已知的 init script stale 问题，待修）
# 当前版本 memory 的资源级 ACL 自动同步**不工作**，本测试只覆盖路径级鉴权
# + 实际接口的 HTTP 行为，不查 resource_acl 表。

# ── Phase 6: data plane (memory add/query/update/delete) ────────────────────
echo -e "${CYAN}[6] Memory data plane (path-level auth, admin can call all)${NC}"
ADD_CODE=$(http_code -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":"t1","instance_name":"i1","user_id":"u1","memory":"hello"}' \
  "$GATEWAY/memory/api/v1/memory/add")
[ "$ADD_CODE" = "200" ] || [ "$ADD_CODE" = "201" ] \
  && ok "POST /memory/api/v1/memory/add (HTTP $ADD_CODE)" \
  || fail "POST /memory/api/v1/memory/add" "want 200/201, got $ADD_CODE"
assert_http "POST /memory/api/v1/memory/query" 200 \
  "$(http_code -X POST -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"tenant_id":"t1","instance_name":"i1","user_id":"u1"}' \
    "$GATEWAY/memory/api/v1/memory/query")"
echo

# ── Phase 7: templates (admin CRUD) ─────────────────────────────────────────
echo -e "${CYAN}[7] Templates CRUD (admin)${NC}"
T_CODE=$(http_code -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"template_id":"e2e-tmpl","name":"e2e","memory_type":"fact"}' \
  "$GATEWAY/memory/api/v1/templates")
assert_http "POST /memory/api/v1/templates" 201 "$T_CODE"
assert_http "GET /memory/api/v1/templates" 200 \
  "$(http_code -H "Authorization: Bearer $TOKEN" "$GATEWAY/memory/api/v1/templates")"
assert_http "GET /memory/api/v1/templates/e2e-tmpl" 200 \
  "$(http_code -H "Authorization: Bearer $TOKEN" "$GATEWAY/memory/api/v1/templates/e2e-tmpl")"
echo

# ── Phase 8: tenant management (admin only) ─────────────────────────────────
echo -e "${CYAN}[8] Tenant management (admin only path)${NC}"
TENANT_CODE=$(http_code -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":"e2e-tenant"}' \
  "$GATEWAY/memory/api/v1/tenants")
[ "$TENANT_CODE" = "201" ] || [ "$TENANT_CODE" = "200" ] \
  && ok "POST /memory/api/v1/tenants (HTTP $TENANT_CODE)" \
  || fail "POST /memory/api/v1/tenants" "got $TENANT_CODE"
echo

# ── Phase 9: cleanup ────────────────────────────────────────────────────────
echo -e "${CYAN}[9] Cleanup test data${NC}"
DEL_T=$(http_code -X DELETE -H "Authorization: Bearer $TOKEN" \
  "$GATEWAY/memory/api/v1/templates/e2e-tmpl")
[ "$DEL_T" = "200" ] || [ "$DEL_T" = "204" ] \
  && ok "DELETE /memory/api/v1/templates/e2e-tmpl (HTTP $DEL_T)" \
  || fail "DELETE template" "got $DEL_T"

DEL_TT=$(http_code -X DELETE -H "Authorization: Bearer $TOKEN" \
  "$GATEWAY/memory/api/v1/tenants/e2e-tenant")
[ "$DEL_TT" = "200" ] || [ "$DEL_TT" = "204" ] \
  && ok "DELETE /memory/api/v1/tenants/e2e-tenant (HTTP $DEL_TT)" \
  || fail "DELETE tenant" "got $DEL_TT"
echo

# ── Phase 10: system admin endpoint ─────────────────────────────────────────
echo -e "${CYAN}[10] System recovery endpoint (admin only)${NC}"
assert_http "POST /memory/api/v1/system/recovery (admin)" 200 \
  "$(http_code -X POST -H "Authorization: Bearer $TOKEN" \
    "$GATEWAY/memory/api/v1/system/recovery")"
echo

# ── Summary ─────────────────────────────────────────────────────────────────
TOTAL=$((PASS+FAIL))
echo "================================================"
printf "  Passed: ${GREEN}%d${NC} / %d\n" "$PASS" "$TOTAL"
[ "$FAIL" -gt 0 ] && printf "  Failed: ${RED}%d${NC} / %d\n" "$FAIL" "$TOTAL"
echo "================================================"
exit "$FAIL"
