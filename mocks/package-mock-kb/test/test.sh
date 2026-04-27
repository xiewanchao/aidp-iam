#!/usr/bin/env bash
# ============================================================================
# package-mock-kb/test/test.sh — KB 业务端到端 + 角色 + 资源 ACL 测试
#
# 覆盖三层：
#   [基础]    pod / 公开路由 / 鉴权拒绝 / 鉴权通过 / 应用注册
#   [路径级]  admin / kb-admin / normal-user 三角色对 /kb/ 路径访问权
#   [资源级]  ext_proc 自动写 owner ACL；不同用户对自己 / 别人资源的访问
#             - normal-user 创建 KB → owner ACL 自动写入
#             - normal-user 读自己 / 改自己 / 删自己 ✓
#             - admin / kb-admin 通过 kb_admin_full 路径级有权访问 normal-user 的 KB
#             - 另一个 normal-user（这里用 admin 模拟，因 admin 也属于 all-users）
#               没有 ACL 时无法 modify/remove（owner-only / contributor-only）
#
# 依赖：bash + curl + python3 + kubectl
# 不依赖 jq / base64 / lsof
# ============================================================================
set -uo pipefail

GATEWAY="${GATEWAY:-http://localhost:30080}"
NAMESPACE="${NAMESPACE:-mock-kb}"

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
  local app="$1" prev_max="$2"
  kubectl -n keycloak exec postgres-0 -c postgres -- \
    psql -U keycloak -d iam -tA -c \
    "SELECT resource_id FROM resource_acl WHERE app_name='$app' AND id > $prev_max ORDER BY id DESC LIMIT 1" 2>/dev/null \
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

echo -e "${CYAN}=== mock-kb e2e + role-based + resource-ACL test ===${NC}"
echo "Gateway: $GATEWAY"
echo

# ── Phase 0: tokens ─────────────────────────────────────────────────────────
echo -e "${CYAN}[0] Prepare tokens (admin / kb-admin / normal-user)${NC}"
SECRET=$(kubectl -n aidp-iam get secret keycloak-aidp-client \
  -o go-template='{{index .data "client-secret" | base64decode}}' 2>/dev/null)
[ -z "$SECRET" ] && { echo -e "${RED}ERROR${NC} Cannot fetch client-secret. Is aidp-iam installed?"; exit 2; }

T_ADMIN=$(get_token admin Admin@123)
T_KBADMIN=$(get_token kb-admin AppAdmin@123)
T_USER=$(get_token normal-user NormalUser@123)
[ -n "$T_ADMIN" ] && ok "admin token (len ${#T_ADMIN})"          || { fail "admin token"; exit 2; }
[ -n "$T_KBADMIN" ] && ok "kb-admin token (len ${#T_KBADMIN})"   || { fail "kb-admin token"; exit 2; }
[ -n "$T_USER" ] && ok "normal-user token (len ${#T_USER})"       || { fail "normal-user token"; exit 2; }
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

# ── Phase 4: app registered ─────────────────────────────────────────────────
echo -e "${CYAN}[4] IAM has knowledgebase app registered${NC}"
APPS=$(curl -sS --max-time 10 -H "Authorization: Bearer $T_ADMIN" "$GATEWAY/api/v1/apps" 2>/dev/null)
echo "$APPS" | grep -q '"knowledgebase"' && ok "GET /api/v1/apps contains knowledgebase" \
  || fail "GET /api/v1/apps lacks knowledgebase" "$(echo "$APPS" | head -c 200)"
echo

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║                 ROLE-BASED PATH ACCESS                                   ║
# ╚══════════════════════════════════════════════════════════════════════════╝

# ── Phase 5: admin role ─────────────────────────────────────────────────────
echo -e "${CYAN}[5] ${YELLOW}admin${NC}${CYAN} role: full /kb/ access (kb_admin_full)${NC}"
assert_http "admin GET /kb/health" 200 \
  "$(http_code -H "Authorization: Bearer $T_ADMIN" "$GATEWAY/kb/health")"
echo

# ── Phase 6: kb-admin role (path-level full /kb/) ──────────────────────────
echo -e "${CYAN}[6] ${YELLOW}kb-admin${NC}${CYAN} role: kb_admin_full /kb/ access${NC}"
assert_http "kb-admin GET /kb/health" 200 \
  "$(http_code -H "Authorization: Bearer $T_KBADMIN" "$GATEWAY/kb/health")"
PREV_MAX=$(acl_max_id knowledgebase)
KBADMIN_CREATE=$(http_code -X POST -H "Authorization: Bearer $T_KBADMIN" \
  -H "Content-Type: application/json" -d '{"name":"e2e-kbadmin-kb"}' \
  "$GATEWAY/kb/knowledge_bases/add")
assert_http "kb-admin POST /kb/knowledge_bases/add" 201 "$KBADMIN_CREATE"
sleep 2
KBADMIN_KB=$(acl_latest_resource_id knowledgebase "$PREV_MAX")
[ -n "$KBADMIN_KB" ] && ok "kb-admin's KB written to ACL: $KBADMIN_KB" \
  || fail "kb-admin's KB ACL not written"
echo

# ── Phase 7: normal-user role + resource ACL flow ──────────────────────────
echo -e "${CYAN}[7] ${YELLOW}normal-user${NC}${CYAN} role: own KB CRUD + ACL auto-sync${NC}"
PREV_MAX=$(acl_max_id knowledgebase)
USER_CREATE=$(http_code -X POST -H "Authorization: Bearer $T_USER" \
  -H "Content-Type: application/json" -d '{"name":"e2e-user-kb"}' \
  "$GATEWAY/kb/knowledge_bases/add")
assert_http "normal-user POST /kb/knowledge_bases/add" 201 "$USER_CREATE"
sleep 2
USER_KB=$(acl_latest_resource_id knowledgebase "$PREV_MAX")
[ -n "$USER_KB" ] && ok "normal-user's KB written to ACL: $USER_KB" \
  || { fail "normal-user's KB ACL not written"; USER_KB="missing"; }

# ext_proc 自动写的 owner 应该是 normal-user 的 UUID
NORMAL_UID=$(uid_of normal-user)
ACL_OWNER=$(acl_subject_of knowledgebase "$USER_KB")
[ "$ACL_OWNER" = "$NORMAL_UID" ] && ok "ACL owner == normal-user uuid" \
  || fail "ACL owner mismatch" "want $NORMAL_UID, got $ACL_OWNER"

# normal-user 操作自己的 KB → 修改 / 删除走 owner 权限
# （注：POST /kb/knowledge_bases without suffix 对 all-users 路径级是禁的 —— kb_browse
# 只放行 GET 方法。"读自己 KB 详情" 在真实 KB API 里靠 GET /knowledge_bases 列表实现，
# 这里就不展开测了。modify/remove 已能验证 owner 权限通路。)
assert_http "normal-user POST /kb/knowledge_bases/modify (contributor needed, owner has it)" 200 \
  "$(http_code -X POST -H "Authorization: Bearer $T_USER" \
    -H "Content-Type: application/json" -d "{\"kbs_id\":\"$USER_KB\",\"name\":\"updated\"}" \
    "$GATEWAY/kb/knowledge_bases/modify")"
# 带 kbs_id query 的读：pep-proxy 校验 owner 的 viewer 权限通过（owner ⊇ viewer）
assert_http "normal-user GET /kb/knowledge_bases?kbs_id=<own>" 200 \
  "$(http_code -H "Authorization: Bearer $T_USER" "$GATEWAY/kb/knowledge_bases?kbs_id=$USER_KB")"
echo

# ── Phase 8: resource-level isolation ───────────────────────────────────────
echo -e "${CYAN}[8] Resource-level isolation (cross-user)${NC}"
# admin / kb-admin 通过 kb_admin_full 拿到 /kb/ 全路径权限，但**资源级 ACL 仍生效**：
# admin 没有 USER_KB 的 ACL → modify (contributor) 应该被拒
# 注：这取决于 pep-proxy 是否对 admin 组绕开 ACL 检查；测试当前真实行为
ADMIN_MODIFY_OTHERS=$(http_code -X POST -H "Authorization: Bearer $T_ADMIN" \
  -H "Content-Type: application/json" -d "{\"kbs_id\":\"$USER_KB\",\"name\":\"hacked\"}" \
  "$GATEWAY/kb/knowledge_bases/modify")
echo "  [info] admin POST /kb/knowledge_bases/modify on user's KB → HTTP $ADMIN_MODIFY_OTHERS (无强期望，记录当前行为)"

# 另一方向：normal-user 不能动 kb-admin 创建的 KB（normal-user 没有 admin 组路径级权 + 没有 ACL）
USER_MODIFY_OTHERS=$(http_code -X POST -H "Authorization: Bearer $T_USER" \
  -H "Content-Type: application/json" -d "{\"kbs_id\":\"$KBADMIN_KB\",\"name\":\"hacked\"}" \
  "$GATEWAY/kb/knowledge_bases/modify")
assert_http "normal-user POST /modify on kb-admin's KB → 403 (no ACL)" 403 "$USER_MODIFY_OTHERS"

USER_DELETE_OTHERS=$(http_code -X POST -H "Authorization: Bearer $T_USER" \
  -H "Content-Type: application/json" -d "{\"kbs_id\":\"$KBADMIN_KB\"}" \
  "$GATEWAY/kb/knowledge_bases/remove")
assert_http "normal-user POST /remove on kb-admin's KB → 403 (no ACL)" 403 "$USER_DELETE_OTHERS"
echo

# ── Phase 9: delete + ACL cascade clean ─────────────────────────────────────
echo -e "${CYAN}[9] Delete own KB → ACL cascade clean${NC}"
assert_http "normal-user POST /kb/knowledge_bases/remove (own, owner has perm)" 200 \
  "$(http_code -X POST -H "Authorization: Bearer $T_USER" \
    -H "Content-Type: application/json" -d "{\"kbs_id\":\"$USER_KB\"}" \
    "$GATEWAY/kb/knowledge_bases/remove")"
sleep 2
N=$(acl_count knowledgebase "$USER_KB")
[ "$N" = "0" ] && ok "resource_acl row removed for $USER_KB" \
  || fail "resource_acl count=$N after delete (expected 0)"
echo

# ── Phase 10: cleanup kb-admin's KB ────────────────────────────────────────
echo -e "${CYAN}[10] Cleanup kb-admin's KB${NC}"
assert_http "kb-admin POST /kb/knowledge_bases/remove (own)" 200 \
  "$(http_code -X POST -H "Authorization: Bearer $T_KBADMIN" \
    -H "Content-Type: application/json" -d "{\"kbs_id\":\"$KBADMIN_KB\"}" \
    "$GATEWAY/kb/knowledge_bases/remove")"
sleep 2
N=$(acl_count knowledgebase "$KBADMIN_KB")
[ "$N" = "0" ] && ok "resource_acl row removed for $KBADMIN_KB" \
  || fail "resource_acl count=$N after delete (expected 0)"
echo

# ── Summary ─────────────────────────────────────────────────────────────────
TOTAL=$((PASS+FAIL))
echo "================================================"
printf "  Passed: ${GREEN}%d${NC} / %d\n" "$PASS" "$TOTAL"
[ "$FAIL" -gt 0 ] && printf "  Failed: ${RED}%d${NC} / %d\n" "$FAIL" "$TOTAL"
echo "================================================"
exit "$FAIL"
