#!/usr/bin/env bash
# ============================================================================
# package-mock-rubik/test/test.sh — Rubik 业务端到端 + 角色 + 资源 ACL 测试
#
# 覆盖三层：
#   [基础]    pod / 公开路由 / 鉴权拒绝 / 鉴权通过 / 应用注册
#   [路径级]  admin / rubik-admin / normal-user 三角色对 /rubik/ 路径访问权
#   [资源级]  database / session 两类资源的 owner ACL 自动同步：
#             - normal-user 创建 database → owner ACL 写入
#             - normal-user 创建 session → owner ACL 写入
#             - normal-user 删除自己的 → ACL 级联清
#             - normal-user 不能 DELETE rubik-admin 的 database（无 ACL）
#
# 依赖：bash + curl + python3 + kubectl
# 不依赖 jq / base64 / lsof
# ============================================================================
set -uo pipefail

GATEWAY="${GATEWAY:-http://localhost:30080}"
NAMESPACE="${NAMESPACE:-mock-rubik}"

GREEN='\033[0;32m'; RED='\033[0;31m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
PASS=0; FAIL=0

ok()   { printf "  ${GREEN}PASS${NC} %s\n" "$1"; PASS=$((PASS+1)); }
fail() { printf "  ${RED}FAIL${NC} %s%s\n" "$1" "${2:+ — $2}"; FAIL=$((FAIL+1)); }

assert_http() {
  local desc="$1" want="$2" got="$3"
  if [ "$got" = "$want" ]; then ok "$desc (HTTP $got)"; else fail "$desc" "want HTTP $want, got $got"; fi
}

assert_http_in() {
  local desc="$1" got="$2"; shift; shift
  for w in "$@"; do
    if [ "$got" = "$w" ]; then ok "$desc (HTTP $got)"; return; fi
  done
  fail "$desc" "want one of [$*], got $got"
}

http_code() {
  curl -sS --max-time 10 -o /dev/null -w "%{http_code}" "$@" 2>/dev/null
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

acl_subject_of() {
  local app="$1" rid="$2"
  kubectl -n keycloak exec postgres-0 -c postgres -- \
    psql -U keycloak -d iam -tA -c \
    "SELECT subject_id FROM resource_acl WHERE app_name='$app' AND resource_id='$rid' LIMIT 1" 2>/dev/null \
    | tr -d ' \r\n'
}

uid_of() {
  local username="$1"
  kubectl -n keycloak exec postgres-0 -c postgres -- \
    psql -U keycloak -d keycloak -tA -c \
    "SELECT id FROM user_entity WHERE username='$username' AND realm_id=(SELECT id FROM realm WHERE name='aidp')" 2>/dev/null \
    | tr -d ' \r\n'
}

get_token() {
  local username="$1" password="$2"
  curl -sS --max-time 10 -X POST "$GATEWAY/realms/aidp/protocol/openid-connect/token" \
    -d "grant_type=password" -d "client_id=aidp-client" -d "client_secret=$SECRET" \
    -d "username=$username" -d "password=$password" 2>/dev/null \
    | python -c "import sys,json
try: print(json.load(sys.stdin).get('access_token',''))
except: pass"
}

echo -e "${CYAN}=== mock-rubik e2e + role-based + resource-ACL test ===${NC}"
echo "Gateway: $GATEWAY"
echo

# ── Phase 0: tokens ─────────────────────────────────────────────────────────
echo -e "${CYAN}[0] Prepare tokens (admin / rubik-admin / normal-user)${NC}"
SECRET=$(kubectl -n aidp-iam get secret keycloak-aidp-client \
  -o go-template='{{index .data "client-secret" | base64decode}}' 2>/dev/null)
[ -z "$SECRET" ] && { echo -e "${RED}ERROR${NC} Cannot fetch client-secret. Is aidp-iam installed?"; exit 2; }

T_ADMIN=$(get_token admin Admin@123)
T_RBADMIN=$(get_token rubik-admin AppAdmin@123)
T_USER=$(get_token normal-user NormalUser@123)
[ -n "$T_ADMIN" ] && ok "admin token (len ${#T_ADMIN})"          || { fail "admin token"; exit 2; }
[ -n "$T_RBADMIN" ] && ok "rubik-admin token (len ${#T_RBADMIN})" || { fail "rubik-admin token"; exit 2; }
[ -n "$T_USER" ] && ok "normal-user token (len ${#T_USER})"       || { fail "normal-user token"; exit 2; }
echo

# ── Phase 1: pod health ─────────────────────────────────────────────────────
echo -e "${CYAN}[1] Pod health${NC}"
ready=$(kubectl -n "$NAMESPACE" get pod -l app=mock-rubik \
  -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
[ "$ready" = "True" ] && ok "mock-rubik pod Ready" || fail "mock-rubik pod" "Ready=$ready"
echo

# ── Phase 2: public routes ──────────────────────────────────────────────────
echo -e "${CYAN}[2] Public routes (no auth required)${NC}"
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

# ── Phase 4: app registered ─────────────────────────────────────────────────
echo -e "${CYAN}[4] IAM has rubik app registered${NC}"
APPS=$(curl -sS --max-time 10 -H "Authorization: Bearer $T_ADMIN" "$GATEWAY/api/v1/apps" 2>/dev/null)
echo "$APPS" | grep -q '"rubik"' && ok "GET /api/v1/apps contains rubik" \
  || fail "GET /api/v1/apps lacks rubik" "$(echo "$APPS" | head -c 200)"
echo

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║                 ROLE-BASED PATH + RESOURCE ACCESS                        ║
# ╚══════════════════════════════════════════════════════════════════════════╝

# ── Phase 5: admin role ─────────────────────────────────────────────────────
echo -e "${CYAN}[5] ${YELLOW}admin${NC}${CYAN} role: full /rubik/ access (rubik_admin_full)${NC}"
assert_http "admin GET /rubik/health" 200 \
  "$(http_code -H "Authorization: Bearer $T_ADMIN" "$GATEWAY/rubik/health")"
echo

# ── Phase 6: rubik-admin role + resource ACL ───────────────────────────────
echo -e "${CYAN}[6] ${YELLOW}rubik-admin${NC}${CYAN} role: rubik_admin_full + create database${NC}"
assert_http "rubik-admin GET /rubik/health" 200 \
  "$(http_code -H "Authorization: Bearer $T_RBADMIN" "$GATEWAY/rubik/health")"
PREV_MAX=$(acl_max_id rubik)
RBADMIN_DB=$(http_code -X POST -H "Authorization: Bearer $T_RBADMIN" \
  -H "Content-Type: application/json" -d '{"name":"e2e-rbadmin-db"}' \
  "$GATEWAY/rubik/api/databases")
assert_http "rubik-admin POST /api/databases" 201 "$RBADMIN_DB"
sleep 2
RBADMIN_DB_ID=$(acl_latest_resource_id rubik database "$PREV_MAX")
[ -n "$RBADMIN_DB_ID" ] && ok "rubik-admin's database written to ACL: $RBADMIN_DB_ID" \
  || fail "rubik-admin's database ACL not written"
echo

# ── Phase 7: normal-user role + own resource ACL ───────────────────────────
echo -e "${CYAN}[7] ${YELLOW}normal-user${NC}${CYAN} role: own database + session CRUD${NC}"
PREV_MAX=$(acl_max_id rubik)
USER_DB=$(http_code -X POST -H "Authorization: Bearer $T_USER" \
  -H "Content-Type: application/json" -d '{"name":"e2e-user-db"}' \
  "$GATEWAY/rubik/api/databases")
assert_http "normal-user POST /api/databases" 201 "$USER_DB"
sleep 2
USER_DB_ID=$(acl_latest_resource_id rubik database "$PREV_MAX")
[ -n "$USER_DB_ID" ] && ok "normal-user's database written to ACL: $USER_DB_ID" \
  || { fail "normal-user's database ACL not written"; USER_DB_ID="missing"; }

# ext_proc 自动写的 owner 应该是 normal-user 的 UUID
NORMAL_UID=$(uid_of normal-user)
ACL_OWNER=$(acl_subject_of rubik "$USER_DB_ID")
[ "$ACL_OWNER" = "$NORMAL_UID" ] && ok "ACL owner == normal-user uuid" \
  || fail "ACL owner mismatch" "want $NORMAL_UID, got $ACL_OWNER"

# Session 资源类型 — 也走 ACL 自动同步
PREV_MAX=$(acl_max_id rubik)
USER_SESS=$(http_code -X POST -H "Authorization: Bearer $T_USER" \
  -H "Content-Type: application/json" -d '{}' \
  "$GATEWAY/rubik/api/sessions")
assert_http "normal-user POST /api/sessions" 201 "$USER_SESS"
sleep 2
USER_SESS_ID=$(acl_latest_resource_id rubik session "$PREV_MAX")
[ -n "$USER_SESS_ID" ] && ok "normal-user's session written to ACL: $USER_SESS_ID" \
  || { fail "normal-user's session ACL not written"; USER_SESS_ID="missing"; }
echo

# ── Phase 8: resource-level isolation (cross-user) ─────────────────────────
echo -e "${CYAN}[8] Resource-level isolation (cross-user)${NC}"
# normal-user 不能 DELETE rubik-admin 的 database（资源 owner 是 rubik-admin，
# normal-user 没有 ACL）
USER_DEL_OTHERS=$(http_code -X DELETE -H "Authorization: Bearer $T_USER" \
  "$GATEWAY/rubik/api/databases/$RBADMIN_DB_ID")
assert_http "normal-user DELETE rubik-admin's database → 403 (no ACL)" 403 "$USER_DEL_OTHERS"

# 反向 admin/rubik-admin 通过 rubik_admin_full 路径级有权访问 normal-user 的 database
ADMIN_DEL_OTHERS=$(http_code -X DELETE -H "Authorization: Bearer $T_ADMIN" \
  "$GATEWAY/rubik/api/databases/nonexistent-id")
echo "  [info] admin DELETE non-existent database → HTTP $ADMIN_DEL_OTHERS (路径级允许，资源不存在)"
echo

# ── Phase 9: delete own + ACL cascade clean ────────────────────────────────
echo -e "${CYAN}[9] Delete own + ACL cascade clean${NC}"
USER_DEL_OWN_DB=$(http_code -X DELETE -H "Authorization: Bearer $T_USER" \
  "$GATEWAY/rubik/api/databases/$USER_DB_ID")
assert_http_in "normal-user DELETE own database" "$USER_DEL_OWN_DB" 200 204
sleep 2
N=$(acl_count rubik "$USER_DB_ID")
[ "$N" = "0" ] && ok "resource_acl row removed for user db $USER_DB_ID" \
  || fail "resource_acl count=$N after delete (expected 0)"

USER_DEL_OWN_SESS=$(http_code -X DELETE -H "Authorization: Bearer $T_USER" \
  "$GATEWAY/rubik/api/sessions/$USER_SESS_ID")
assert_http_in "normal-user DELETE own session" "$USER_DEL_OWN_SESS" 200 204
sleep 2
N=$(acl_count rubik "$USER_SESS_ID")
[ "$N" = "0" ] && ok "resource_acl row removed for user session $USER_SESS_ID" \
  || fail "resource_acl count=$N after delete (expected 0)"
echo

# ── Phase 10: cleanup rubik-admin's database ───────────────────────────────
echo -e "${CYAN}[10] Cleanup rubik-admin's database${NC}"
RBADMIN_DEL=$(http_code -X DELETE -H "Authorization: Bearer $T_RBADMIN" \
  "$GATEWAY/rubik/api/databases/$RBADMIN_DB_ID")
assert_http_in "rubik-admin DELETE own database" "$RBADMIN_DEL" 200 204
sleep 2
N=$(acl_count rubik "$RBADMIN_DB_ID")
[ "$N" = "0" ] && ok "resource_acl row removed for $RBADMIN_DB_ID" \
  || fail "resource_acl count=$N after delete (expected 0)"
echo

# ── Summary ─────────────────────────────────────────────────────────────────
TOTAL=$((PASS+FAIL))
echo "================================================"
printf "  Passed: ${GREEN}%d${NC} / %d\n" "$PASS" "$TOTAL"
[ "$FAIL" -gt 0 ] && printf "  Failed: ${RED}%d${NC} / %d\n" "$FAIL" "$TOTAL"
echo "================================================"
exit "$FAIL"
