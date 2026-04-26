#!/usr/bin/env bash
# ============================================================================
# package-mock-rubik/test/test.sh — Rubik 业务端到端测试
#
# 覆盖：pod / 公开路由 / 鉴权拒绝 / 鉴权通过 / 应用注册 / 创建 database / ACL /
#       创建 session / ACL / 删除 + ACL 级联清除 (DELETE /id 路径模式)
#
# 依赖：bash / curl / python3 / kubectl
# 不依赖 jq / base64 / lsof
# ============================================================================
set -uo pipefail

GATEWAY="${GATEWAY:-http://localhost:30080}"
NAMESPACE="${NAMESPACE:-mock-rubik}"

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
  local app="$1" rtype="$2" prev_max="$3"
  kubectl -n keycloak exec postgres-0 -c postgres -- \
    psql -U keycloak -d iam -tA -c \
    "SELECT resource_id FROM resource_acl WHERE app_name='$app' AND resource_type='$rtype' AND id > $prev_max ORDER BY id DESC LIMIT 1" 2>/dev/null \
    | tr -d ' \r\n'
}

echo -e "${CYAN}=== mock-rubik e2e test ===${NC}"
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
ready=$(kubectl -n "$NAMESPACE" get pod -l app=mock-rubik \
  -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
[ "$ready" = "True" ] && ok "mock-rubik pod Ready" || fail "mock-rubik pod" "Ready=$ready"
echo

# ── Phase 2: public routes ──────────────────────────────────────────────────
echo -e "${CYAN}[2] Public routes (no auth)${NC}"
assert_http "GET /realms/aidp/.well-known/openid-configuration" 200 \
  "$(http_code "$GATEWAY/realms/aidp/.well-known/openid-configuration")"
echo

# ── Phase 3: auth required ──────────────────────────────────────────────────
echo -e "${CYAN}[3] Protected /rubik/* rejects no-token${NC}"
assert_http "GET /rubik/api/databases (no token)" 401 \
  "$(http_code "$GATEWAY/rubik/api/databases")"
assert_http "POST /rubik/api/databases (no token)" 401 \
  "$(http_code -X POST "$GATEWAY/rubik/api/databases")"
echo

# ── Phase 4: auth pass ──────────────────────────────────────────────────────
echo -e "${CYAN}[4] Protected /rubik/* accepts admin token${NC}"
assert_http "GET /rubik/health (admin)" 200 \
  "$(http_code -H "Authorization: Bearer $TOKEN" "$GATEWAY/rubik/health")"
echo

# ── Phase 5: app registered ─────────────────────────────────────────────────
echo -e "${CYAN}[5] IAM has rubik app registered${NC}"
APPS=$(curl -sS -H "Authorization: Bearer $TOKEN" "$GATEWAY/api/v1/apps" 2>/dev/null)
echo "$APPS" | grep -q '"rubik"' && ok "GET /api/v1/apps contains rubik" \
  || fail "GET /api/v1/apps lacks rubik" "$(echo "$APPS" | head -c 200)"
echo

# ── Phase 6: create database ────────────────────────────────────────────────
echo -e "${CYAN}[6] POST /rubik/api/databases (create database → 201)${NC}"
PREV_MAX=$(acl_max_id rubik)
DB_CODE=$(http_code -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"name":"e2e-test-db"}' \
  "$GATEWAY/rubik/api/databases")
assert_http "POST /rubik/api/databases" 201 "$DB_CODE"
echo

# ── Phase 7: ACL auto-write (database) ──────────────────────────────────────
echo -e "${CYAN}[7] ext_proc auto-writes owner ACL for database${NC}"
sleep 2
DB_ID=$(acl_latest_resource_id rubik database "$PREV_MAX")
[ -n "$DB_ID" ] && ok "Got database resource_id from new ACL row: $DB_ID" \
  || { fail "No new ACL row written for database"; DB_ID="missing"; }
N=$(acl_count rubik "$DB_ID")
[ "$N" = "1" ] && ok "resource_acl row count=1 for database $DB_ID" \
  || fail "resource_acl count=$N for $DB_ID (expected 1)"
echo

# ── Phase 8: create session ─────────────────────────────────────────────────
echo -e "${CYAN}[8] POST /rubik/api/sessions (create session → 201)${NC}"
PREV_MAX=$(acl_max_id rubik)
SESS_CODE=$(http_code -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{}' \
  "$GATEWAY/rubik/api/sessions")
assert_http "POST /rubik/api/sessions" 201 "$SESS_CODE"
sleep 2
SESS_ID=$(acl_latest_resource_id rubik session "$PREV_MAX")
[ -n "$SESS_ID" ] && ok "Got session resource_id from new ACL row: $SESS_ID" \
  || { fail "No new ACL row written for session"; SESS_ID="missing"; }
N=$(acl_count rubik "$SESS_ID")
[ "$N" = "1" ] && ok "resource_acl row count=1 for session $SESS_ID" \
  || fail "resource_acl count=$N for session (expected 1)"
echo

# ── Phase 9: delete database + ACL cascade ──────────────────────────────────
echo -e "${CYAN}[9] DELETE /rubik/api/databases/{id} → cascade ACL clean${NC}"
DEL_CODE=$(http_code -X DELETE -H "Authorization: Bearer $TOKEN" \
  "$GATEWAY/rubik/api/databases/$DB_ID")
[ "$DEL_CODE" = "200" ] || [ "$DEL_CODE" = "204" ] \
  && ok "DELETE /rubik/api/databases/$DB_ID (HTTP $DEL_CODE)" \
  || fail "DELETE /rubik/api/databases" "want 200/204, got $DEL_CODE"
sleep 2
N=$(acl_count rubik "$DB_ID")
[ "$N" = "0" ] && ok "resource_acl row removed for database $DB_ID" \
  || fail "resource_acl count=$N after delete (expected 0)"
echo

# ── Phase 10: delete session + ACL cascade ──────────────────────────────────
echo -e "${CYAN}[10] DELETE /rubik/api/sessions/{id} → cascade ACL clean${NC}"
DEL_CODE=$(http_code -X DELETE -H "Authorization: Bearer $TOKEN" \
  "$GATEWAY/rubik/api/sessions/$SESS_ID")
[ "$DEL_CODE" = "200" ] || [ "$DEL_CODE" = "204" ] \
  && ok "DELETE /rubik/api/sessions/$SESS_ID (HTTP $DEL_CODE)" \
  || fail "DELETE /rubik/api/sessions" "want 200/204, got $DEL_CODE"
sleep 2
N=$(acl_count rubik "$SESS_ID")
[ "$N" = "0" ] && ok "resource_acl row removed for session $SESS_ID" \
  || fail "resource_acl count=$N after delete (expected 0)"
echo

# ── Summary ─────────────────────────────────────────────────────────────────
TOTAL=$((PASS+FAIL))
echo "================================================"
printf "  Passed: ${GREEN}%d${NC} / %d\n" "$PASS" "$TOTAL"
[ "$FAIL" -gt 0 ] && printf "  Failed: ${RED}%d${NC} / %d\n" "$FAIL" "$TOTAL"
echo "================================================"
exit "$FAIL"
