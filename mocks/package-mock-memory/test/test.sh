#!/usr/bin/env bash
# ============================================================================
# package-mock-memory/test/test.sh — Memory 业务端到端 + 角色权限测试
#
# 覆盖：
#   [基础] pod / 公开路由 / 鉴权拒绝 / 鉴权通过 / 应用注册
#   [角色] admin / memory-admin / normal-user 三个角色对每条端点的允许 / 拒绝
#          矩阵（按 sr-requirements.md + 业务期望矩阵）：
#            admin           : POST /tenants ✓  POST /system/recovery ✓
#            memory-admin    : 模板 ✓ tenants/{id}/instances ✓ 数据面 ✓
#                              POST /tenants ✗  POST /system/recovery ✗
#            normal-user     : GET /templates ✓ 数据面 ✓
#                              POST /templates ✗ POST /tenants ✗
#
# Memory 是**纯路径级鉴权**应用（不挂资源 ACL），「管自己的记忆」由业务后端
# 读 X-Auth-User-Id + body.user_id 比较强校验，IAM 不参与该层。
#
# 依赖：bash + curl + python3 + kubectl
# 不依赖 jq / base64 / lsof
# ============================================================================
set -uo pipefail

GATEWAY="${GATEWAY:-http://localhost:30080}"
NAMESPACE="${NAMESPACE:-mock-memory}"

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

get_token() {
  local username="$1" password="$2"
  curl -sS --max-time 10 -X POST "$GATEWAY/realms/aidp/protocol/openid-connect/token" \
    -d "grant_type=password" -d "client_id=aidp-client" -d "client_secret=$SECRET" \
    -d "username=$username" -d "password=$password" 2>/dev/null \
    | python -c "import sys,json
try: print(json.load(sys.stdin).get('access_token',''))
except: pass"
}

echo -e "${CYAN}=== mock-memory e2e + role-based test ===${NC}"
echo "Gateway: $GATEWAY"
echo

# ── Phase 0: tokens for all 3 roles ─────────────────────────────────────────
echo -e "${CYAN}[0] Prepare tokens (admin / memory-admin / normal-user)${NC}"
SECRET=$(kubectl -n keycloak get secret keycloak-aidp-client \
  -o go-template='{{index .data "client-secret" | base64decode}}' 2>/dev/null)
[ -z "$SECRET" ] && { echo -e "${RED}ERROR${NC} Cannot fetch client-secret. Is aidp-iam installed?"; exit 2; }

T_ADMIN=$(get_token admin Admin@123)
T_MEMADMIN=$(get_token memory-admin AppAdmin@123)
T_USER=$(get_token normal-user NormalUser@123)
[ -n "$T_ADMIN" ] && ok "admin token (len ${#T_ADMIN})"     || { fail "admin token"; exit 2; }
[ -n "$T_MEMADMIN" ] && ok "memory-admin token (len ${#T_MEMADMIN})" || { fail "memory-admin token"; exit 2; }
[ -n "$T_USER" ] && ok "normal-user token (len ${#T_USER})" || { fail "normal-user token"; exit 2; }
echo

# ── Phase 1: pod health ─────────────────────────────────────────────────────
echo -e "${CYAN}[1] Pod health${NC}"
ready=$(kubectl -n "$NAMESPACE" get pod -l app=mock-memory \
  -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
[ "$ready" = "True" ] && ok "mock-memory pod Ready" || fail "mock-memory pod" "Ready=$ready"
echo

# ── Phase 2: public routes ──────────────────────────────────────────────────
echo -e "${CYAN}[2] Public routes (no auth required)${NC}"
assert_http "GET /realms/aidp/.well-known/openid-configuration" 200 \
  "$(http_code "$GATEWAY/realms/aidp/.well-known/openid-configuration")"
echo

# ── Phase 3: protected without token ────────────────────────────────────────
echo -e "${CYAN}[3] Protected /memory/* rejects no-token${NC}"
assert_http "GET /memory/api/v1/templates (no token)" 401 \
  "$(http_code "$GATEWAY/memory/api/v1/templates")"
assert_http "POST /memory/api/v1/memory/add (no token)" 401 \
  "$(http_code -X POST "$GATEWAY/memory/api/v1/memory/add")"
assert_http "POST /memory/api/v1/tenants (no token)" 401 \
  "$(http_code -X POST "$GATEWAY/memory/api/v1/tenants")"
echo

# ── Phase 4: app registered ─────────────────────────────────────────────────
echo -e "${CYAN}[4] IAM has memory app registered${NC}"
APPS=$(curl -sS --max-time 10 -H "Authorization: Bearer $T_ADMIN" "$GATEWAY/api/v1/apps" 2>/dev/null)
echo "$APPS" | grep -q '"memory"' && ok "GET /api/v1/apps contains memory" \
  || fail "GET /api/v1/apps lacks memory" "$(echo "$APPS" | head -c 200)"
echo

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║                 ROLE-BASED PERMISSION MATRIX                             ║
# ╚══════════════════════════════════════════════════════════════════════════╝

# ── Phase 5: admin role (super admin) ───────────────────────────────────────
echo -e "${CYAN}[5] ${YELLOW}admin${NC}${CYAN} role: tenant + system management${NC}"
TENANT_CODE=$(http_code -X POST -H "Authorization: Bearer $T_ADMIN" \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":"e2e-admin-tenant"}' \
  "$GATEWAY/memory/api/v1/tenants")
assert_http_in "admin POST /tenants (create tenant, admin-only)" "$TENANT_CODE" 200 201
assert_http "admin POST /system/recovery (system action, admin-only)" 200 \
  "$(http_code -X POST -H "Authorization: Bearer $T_ADMIN" "$GATEWAY/memory/api/v1/system/recovery")"
echo

# ── Phase 6: memory-admin role ──────────────────────────────────────────────
echo -e "${CYAN}[6] ${YELLOW}memory-admin${NC}${CYAN} role: app management${NC}"
echo "  --- DENY (admin-only paths must reject memory-admin) ---"
assert_http "memory-admin POST /tenants → 403" 403 \
  "$(http_code -X POST -H "Authorization: Bearer $T_MEMADMIN" \
    -H "Content-Type: application/json" -d '{"tenant_id":"x"}' \
    "$GATEWAY/memory/api/v1/tenants")"
assert_http "memory-admin POST /system/recovery → 403" 403 \
  "$(http_code -X POST -H "Authorization: Bearer $T_MEMADMIN" \
    "$GATEWAY/memory/api/v1/system/recovery")"
echo "  --- ALLOW (app-admin paths) ---"
INSTANCE_CODE=$(http_code -X POST -H "Authorization: Bearer $T_MEMADMIN" \
  -H "Content-Type: application/json" -d '{"instance_name":"e2e-instance"}' \
  "$GATEWAY/memory/api/v1/tenants/e2e-admin-tenant/instances")
assert_http_in "memory-admin POST /tenants/{id}/instances" "$INSTANCE_CODE" 200 201
TEMPLATE_CODE=$(http_code -X POST -H "Authorization: Bearer $T_MEMADMIN" \
  -H "Content-Type: application/json" \
  -d '{"template_id":"e2e-tmpl","name":"e2e","memory_type":"fact"}' \
  "$GATEWAY/memory/api/v1/templates")
assert_http_in "memory-admin POST /templates" "$TEMPLATE_CODE" 200 201
assert_http "memory-admin GET /templates" 200 \
  "$(http_code -H "Authorization: Bearer $T_MEMADMIN" "$GATEWAY/memory/api/v1/templates")"
ADD_CODE=$(http_code -X POST -H "Authorization: Bearer $T_MEMADMIN" \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":"t1","instance_name":"i1","user_id":"u1","memory":"hello"}' \
  "$GATEWAY/memory/api/v1/memory/add")
assert_http_in "memory-admin POST /memory/add (data plane)" "$ADD_CODE" 200 201
echo

# ── Phase 7: normal-user role (end user) ────────────────────────────────────
echo -e "${CYAN}[7] ${YELLOW}normal-user${NC}${CYAN} role: own data only${NC}"
echo "  --- DENY (admin / app-admin only paths must reject end user) ---"
assert_http "normal-user POST /tenants → 403" 403 \
  "$(http_code -X POST -H "Authorization: Bearer $T_USER" \
    -H "Content-Type: application/json" -d '{"tenant_id":"x"}' \
    "$GATEWAY/memory/api/v1/tenants")"
assert_http "normal-user POST /system/recovery → 403" 403 \
  "$(http_code -X POST -H "Authorization: Bearer $T_USER" \
    "$GATEWAY/memory/api/v1/system/recovery")"
assert_http "normal-user POST /templates → 403" 403 \
  "$(http_code -X POST -H "Authorization: Bearer $T_USER" \
    -H "Content-Type: application/json" -d '{"template_id":"x"}' \
    "$GATEWAY/memory/api/v1/templates")"
assert_http "normal-user POST /tenants/{id}/instances → 403" 403 \
  "$(http_code -X POST -H "Authorization: Bearer $T_USER" \
    -H "Content-Type: application/json" -d '{"instance_name":"x"}' \
    "$GATEWAY/memory/api/v1/tenants/e2e-admin-tenant/instances")"
echo "  --- ALLOW (end-user paths) ---"
assert_http "normal-user GET /templates (read for use)" 200 \
  "$(http_code -H "Authorization: Bearer $T_USER" "$GATEWAY/memory/api/v1/templates")"
USR_ADD_CODE=$(http_code -X POST -H "Authorization: Bearer $T_USER" \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":"t1","instance_name":"i1","user_id":"u1","memory":"my own"}' \
  "$GATEWAY/memory/api/v1/memory/add")
assert_http_in "normal-user POST /memory/add (own data)" "$USR_ADD_CODE" 200 201
echo

# ── Phase 8: cleanup ────────────────────────────────────────────────────────
echo -e "${CYAN}[8] Cleanup${NC}"
DEL_T=$(http_code -X DELETE -H "Authorization: Bearer $T_MEMADMIN" \
  "$GATEWAY/memory/api/v1/templates/e2e-tmpl")
assert_http_in "memory-admin DELETE /templates/{id}" "$DEL_T" 200 204
DEL_TT=$(http_code -X DELETE -H "Authorization: Bearer $T_ADMIN" \
  "$GATEWAY/memory/api/v1/tenants/e2e-admin-tenant")
assert_http_in "admin DELETE /tenants/{id}" "$DEL_TT" 200 204
echo

# ── Summary ─────────────────────────────────────────────────────────────────
TOTAL=$((PASS+FAIL))
echo "================================================"
printf "  Passed: ${GREEN}%d${NC} / %d\n" "$PASS" "$TOTAL"
[ "$FAIL" -gt 0 ] && printf "  Failed: ${RED}%d${NC} / %d\n" "$FAIL" "$TOTAL"
echo "================================================"
exit "$FAIL"
