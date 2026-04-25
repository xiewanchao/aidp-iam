#!/usr/bin/env bash
# ============================================================================
# package-mock-kb/test/test.sh — KB 业务端到端测试
#
# 覆盖：pod / 公开路由 / 鉴权拒绝 / 鉴权通过 / 应用注册 / 创建 / ACL 自动写入 /
#       读 / 修改 / 删除 / ACL 级联清除
#
# 依赖：bash / curl / python3 / kubectl
# 不依赖 jq / base64 / lsof
# ============================================================================
set -uo pipefail

GATEWAY="${GATEWAY:-http://localhost:30080}"
NAMESPACE="${NAMESPACE:-mock-kb}"

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

echo -e "${CYAN}=== mock-kb e2e test ===${NC}"
echo "Gateway: $GATEWAY"
echo

# ── Phase 0: get admin token ────────────────────────────────────────────────
echo -e "${CYAN}[0] Prepare admin token${NC}"
SECRET=$(kubectl -n keycloak get secret keycloak-aidp-client \
  -o go-template='{{index .data "client-secret" | base64decode}}' 2>/dev/null)
if [ -z "$SECRET" ]; then
  echo -e "${RED}ERROR${NC} Cannot fetch keycloak-aidp-client secret. Is aidp-iam installed?"
  exit 2
fi

TOKEN_RESP=$(curl -sS -X POST "$GATEWAY/realms/aidp/protocol/openid-connect/token" \
  -d "grant_type=password" -d "client_id=aidp-client" -d "client_secret=$SECRET" \
  -d "username=admin" -d "password=Admin@123" 2>/dev/null)
TOKEN=$(echo "$TOKEN_RESP" | python -c "import sys,json
try: print(json.load(sys.stdin).get('access_token',''))
except: pass")
if [ -z "$TOKEN" ]; then
  echo -e "${RED}ERROR${NC} Cannot get admin token: $TOKEN_RESP"
  exit 2
fi
echo "  Token len: ${#TOKEN}"
echo

# ── Phase 1: pod health ─────────────────────────────────────────────────────
echo -e "${CYAN}[1] Pod health${NC}"
ready=$(kubectl -n "$NAMESPACE" get pod -l app=mock-kb \
  -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
[ "$ready" = "True" ] && ok "mock-kb pod Ready" || fail "mock-kb pod" "Ready=$ready"
echo

# ── Phase 2: public routes ──────────────────────────────────────────────────
echo -e "${CYAN}[2] Public routes (no auth required)${NC}"
assert_http "GET /realms/master/.well-known/openid-configuration" 200 \
  "$(http_code "$GATEWAY/realms/master/.well-known/openid-configuration")"
assert_http "GET /realms/aidp/.well-known/openid-configuration" 200 \
  "$(http_code "$GATEWAY/realms/aidp/.well-known/openid-configuration")"
echo

# ── Phase 3: auth required ──────────────────────────────────────────────────
echo -e "${CYAN}[3] Protected /kb/* rejects no-token${NC}"
assert_http "GET /kb/health (no token)" 401 \
  "$(http_code "$GATEWAY/kb/health")"
assert_http "POST /kb/knowledge_bases/add (no token)" 401 \
  "$(http_code -X POST "$GATEWAY/kb/knowledge_bases/add")"
echo

# ── Phase 4: auth pass ──────────────────────────────────────────────────────
echo -e "${CYAN}[4] Protected /kb/* accepts admin token${NC}"
assert_http "GET /kb/health (admin)" 200 \
  "$(http_code -H "Authorization: Bearer $TOKEN" "$GATEWAY/kb/health")"
echo

# ── Phase 5: app registered ─────────────────────────────────────────────────
echo -e "${CYAN}[5] IAM has knowledgebase app registered${NC}"
APPS=$(curl -sS -H "Authorization: Bearer $TOKEN" "$GATEWAY/api/v1/apps" 2>/dev/null)
echo "$APPS" | grep -q '"knowledgebase"' && ok "GET /api/v1/apps contains knowledgebase" \
  || fail "GET /api/v1/apps lacks knowledgebase" "$(echo "$APPS" | head -c 200)"
echo

# ── Phase 6: create resource ────────────────────────────────────────────────
echo -e "${CYAN}[6] POST /kb/knowledge_bases/add (create → 201)${NC}"
PREV_MAX=$(acl_max_id knowledgebase)
CREATE_CODE=$(http_code -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"name":"e2e-test-kb"}' \
  "$GATEWAY/kb/knowledge_bases/add")
assert_http "POST /kb/knowledge_bases/add" 201 "$CREATE_CODE"
echo

# ── Phase 7: ACL auto-write ─────────────────────────────────────────────────
echo -e "${CYAN}[7] ext_proc auto-writes owner ACL${NC}"
sleep 2
KB_ID=$(acl_latest_resource_id knowledgebase "$PREV_MAX")
[ -n "$KB_ID" ] && ok "Got resource_id from new ACL row: $KB_ID" \
  || { fail "No new ACL row written after create"; KB_ID="missing"; }
N=$(acl_count knowledgebase "$KB_ID")
[ "$N" = "1" ] && ok "resource_acl row count=1 for $KB_ID" \
  || fail "resource_acl count=$N for $KB_ID (expected 1)"
echo

# ── Phase 8: read ───────────────────────────────────────────────────────────
echo -e "${CYAN}[8] POST /kb/knowledge_bases (read by body kbs_id)${NC}"
assert_http "POST /kb/knowledge_bases body={kbs_id}" 200 \
  "$(http_code -X POST -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" -d "{\"kbs_id\":\"$KB_ID\"}" \
    "$GATEWAY/kb/knowledge_bases")"
echo

# ── Phase 9: modify ─────────────────────────────────────────────────────────
echo -e "${CYAN}[9] POST /kb/knowledge_bases/modify (contributor)${NC}"
assert_http "POST /kb/knowledge_bases/modify" 200 \
  "$(http_code -X POST -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" -d "{\"kbs_id\":\"$KB_ID\",\"name\":\"updated\"}" \
    "$GATEWAY/kb/knowledge_bases/modify")"
echo

# ── Phase 10: delete + ACL cascade clean ────────────────────────────────────
echo -e "${CYAN}[10] POST /kb/knowledge_bases/remove (delete → cascade ACL)${NC}"
assert_http "POST /kb/knowledge_bases/remove" 200 \
  "$(http_code -X POST -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" -d "{\"kbs_id\":\"$KB_ID\"}" \
    "$GATEWAY/kb/knowledge_bases/remove")"
sleep 2
N=$(acl_count knowledgebase "$KB_ID")
[ "$N" = "0" ] && ok "resource_acl row removed for $KB_ID" \
  || fail "resource_acl count=$N after delete (expected 0)"
echo

# ── Summary ─────────────────────────────────────────────────────────────────
TOTAL=$((PASS+FAIL))
echo "================================================"
printf "  Passed: ${GREEN}%d${NC} / %d\n" "$PASS" "$TOTAL"
[ "$FAIL" -gt 0 ] && printf "  Failed: ${RED}%d${NC} / %d\n" "$FAIL" "$TOTAL"
echo "================================================"
exit "$FAIL"
