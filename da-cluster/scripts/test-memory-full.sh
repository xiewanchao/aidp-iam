#!/usr/bin/env bash
# ============================================================================
# test-memory-full.sh — Full UnifiedMem (memory) auth matrix (mock-memory only)
#
# 三层 RBAC 覆盖：
#   - 超级管理员 admins：memory_admin_full /memory/* 全权（POST /tenants、
#     /system/recovery 等超管动作仅此层可达）
#   - 租户管理员 memory-admins：memory_tenant_admin：/tenants/* 子路径、
#     /templates* 全部方法、/memory/* 数据；但 POST /tenants 不允许（trailing-/
#     排除）
#   - 最终用户 all-users：memory_user_data（/memory/* 数据）、
#     memory_user_templates_read（GET /templates*）、memory_health
#     （GET /health）；/system/recovery 与 POST /tenants 等超管动作 ✗
#
# 额外覆盖：app_disabled 切换、结构化拒绝响应、permission_groups 种子核查。
#
# 注：memory 无资源级鉴权（无 resource_type），测试聚焦路径级 + RBAC 分层。
#
# Port-forward 用 :8084（test.sh=8080, test-kb=8081, test-rubik=8082）；
# Keycloak 直连复用 :8180。
# ============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
KEYCLOAK_NS="keycloak"
ENVOY_GATEWAY_NS="${ENVOY_GATEWAY_NS:-aidp-iam}"
GATEWAY_PORT="${GATEWAY_PORT:-8084}"
KEYCLOAK_PORT="${KEYCLOAK_PORT:-8180}"
BASE_URL="http://localhost:${GATEWAY_PORT}"
KC_DIRECT_URL="http://localhost:${KEYCLOAK_PORT}"

REALM="${REALM:-aidp}"
CLIENT_ID="${CLIENT_ID:-aidp-client}"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-Admin@123}"

ALICE_USER="memtest-alice"
ALICE_PASS="Alice@123"
BOB_USER="memtest-bob"
BOB_PASS="Bob@123"
MEMADMIN_USER="memtest-memadmin"
MEMADMIN_PASS="MemAdmin@123"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
PASS=0; FAIL=0; TOTAL=0

assert() { local d="$1" e="$2" a="$3"; TOTAL=$((TOTAL+1))
  if [ "$e" = "$a" ]; then echo -e "  ${GREEN}PASS${NC} $d"; PASS=$((PASS+1))
  else echo -e "  ${RED}FAIL${NC} $d (expected=$e, actual=$a)"; FAIL=$((FAIL+1)); fi
}
assert_match() { local d="$1" p="$2" a="$3"; TOTAL=$((TOTAL+1))
  if echo "$a" | grep -qE "$p"; then echo -e "  ${GREEN}PASS${NC} $d"; PASS=$((PASS+1))
  else echo -e "  ${RED}FAIL${NC} $d (pattern='$p', got='$a')"; FAIL=$((FAIL+1)); fi
}
assert_contains() { local d="$1" e="$2" a="$3"; TOTAL=$((TOTAL+1))
  if echo "$a" | grep -q "$e"; then echo -e "  ${GREEN}PASS${NC} $d"; PASS=$((PASS+1))
  else echo -e "  ${RED}FAIL${NC} $d (expected to contain '$e', got '$a')"; FAIL=$((FAIL+1)); fi
}
assert_not_contains() { local d="$1" u="$2" a="$3"; TOTAL=$((TOTAL+1))
  if echo "$a" | grep -q "$u"; then echo -e "  ${RED}FAIL${NC} $d (should NOT contain '$u')"; FAIL=$((FAIL+1))
  else echo -e "  ${GREEN}PASS${NC} $d"; PASS=$((PASS+1)); fi
}
section() { echo -e "\n${BLUE}=== $* ===${NC}"; }

psql_iam() {
  MSYS_NO_PATHCONV=1 kubectl -n "$KEYCLOAK_NS" exec postgres-0 -c postgres -- \
    psql -U keycloak -d iam -tA -c "$1" 2>/dev/null | tr -d '\r'
}

jget() { python -c "
import sys,json
try: v=json.load(sys.stdin)
except: print(''); sys.exit(0)
for k in '$1'.split('.'):
  if isinstance(v, list):
    try: v=v[int(k)]
    except: v=''; break
  else:
    v=v.get(k,'') if isinstance(v,dict) else ''
print(v if v is not None else '')"; }

# ── Port-forwards ─────────────────────────────────────────────────────────
echo -e "${YELLOW}Setting up port-forward on :${GATEWAY_PORT}...${NC}"
PF_PID=""
if curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/" 2>/dev/null | grep -qE '200|301|302|404'; then
  echo "  gateway already forwarded"
else
  lsof -ti:${GATEWAY_PORT} 2>/dev/null | xargs kill -9 2>/dev/null || true
  GW_SVC=$(kubectl -n "$ENVOY_GATEWAY_NS" get svc -l gateway.envoyproxy.io/owning-gateway-name=eg -o name 2>/dev/null | head -1)
  [ -z "$GW_SVC" ] && GW_SVC="svc/envoy-eg"
  kubectl -n "$ENVOY_GATEWAY_NS" port-forward "$GW_SVC" "${GATEWAY_PORT}:80" >/dev/null 2>&1 &
  PF_PID=$!; sleep 3
fi

KC_PF_PID=""
if curl -s -o /dev/null -w "%{http_code}" "$KC_DIRECT_URL/realms/master/.well-known/openid-configuration" 2>/dev/null | grep -q 200; then
  :
else
  lsof -ti:${KEYCLOAK_PORT} 2>/dev/null | xargs kill -9 2>/dev/null || true
  kubectl -n "$KEYCLOAK_NS" port-forward svc/keycloak "${KEYCLOAK_PORT}:8080" >/dev/null 2>&1 &
  KC_PF_PID=$!; sleep 3
fi

cleanup() {
  [ -n "$PF_PID" ] && kill "$PF_PID" 2>/dev/null || true
  [ -n "$KC_PF_PID" ] && kill "$KC_PF_PID" 2>/dev/null || true
}
trap cleanup EXIT

# ── KC master token to finalize test users ────────────────────────────────
KC_ADMIN_PW=$(kubectl -n "$KEYCLOAK_NS" get secret keycloak-credentials -o jsonpath='{.data.admin-password}' 2>/dev/null | base64 -d)
KC_MASTER_TOKEN=$(curl -s -X POST "$KC_DIRECT_URL/realms/master/protocol/openid-connect/token" \
  -d "client_id=admin-cli" -d "grant_type=password" \
  -d "username=admin" -d "password=$KC_ADMIN_PW" | jget access_token)
if [ -z "$KC_MASTER_TOKEN" ]; then
  echo -e "${RED}ERROR: could not obtain Keycloak master admin token${NC}"; exit 1
fi

finalize_user() {
  local uid="$1" pw="$2"
  curl -s -X PUT -H "Authorization: Bearer $KC_MASTER_TOKEN" -H "Content-Type: application/json" \
    -d "{\"requiredActions\":[],\"enabled\":true}" \
    "$KC_DIRECT_URL/admin/realms/$REALM/users/$uid" >/dev/null
  curl -s -X PUT -H "Authorization: Bearer $KC_MASTER_TOKEN" -H "Content-Type: application/json" \
    -d "{\"type\":\"password\",\"value\":\"$pw\",\"temporary\":false}" \
    "$KC_DIRECT_URL/admin/realms/$REALM/users/$uid/reset-password" >/dev/null
}

# ── Admin token + user provisioning ───────────────────────────────────────
section "Setup: admin token"
CS=$(kubectl -n "$KEYCLOAK_NS" get secret keycloak-aidp-client -o jsonpath='{.data.client-secret}' 2>/dev/null | base64 -d)
if [ -z "$CS" ]; then
  echo -e "${RED}ERROR: aidp-client client-secret not found — run scripts/setup.sh first${NC}"
  exit 1
fi

get_token() {
  local user="$1" pass="$2"
  curl -s -X POST "$BASE_URL/realms/$REALM/protocol/openid-connect/token" \
    -d "client_id=$CLIENT_ID" -d "client_secret=$CS" -d "grant_type=password" \
    -d "username=$user" -d "password=$pass" | jget access_token
}

ADMIN_TOKEN=$(get_token "$ADMIN_USER" "$ADMIN_PASSWORD")
[ -z "$ADMIN_TOKEN" ] && { echo -e "${RED}ERROR: could not obtain admin token${NC}"; exit 1; }

IAM="$BASE_URL/api/v1/$REALM"
KP() { curl -s -H "Authorization: Bearer $ADMIN_TOKEN" "$@"; }

gid_of_group() {
  KP "$IAM/groups?search=$1" | python -c "
import sys,json
try:
  data=json.load(sys.stdin)
except:
  data=[]
for g in data if isinstance(data, list) else []:
  if g.get('name') == '$1':
    print(g['id']); break"
}

ALL_USERS_GID=$(gid_of_group all-users)
MEMORY_ADMINS_GID=$(gid_of_group memory-admins)
if [ -z "$ALL_USERS_GID" ] || [ -z "$MEMORY_ADMINS_GID" ]; then
  echo -e "${RED}ERROR: preset group ids missing (all-users=$ALL_USERS_GID memory-admins=$MEMORY_ADMINS_GID)${NC}"
  exit 1
fi

uid_of_user() {
  KP "$IAM/users?search=$1" | python -c "
import sys,json
try:
  d=json.load(sys.stdin)
except:
  d=[]
for u in d if isinstance(d, list) else []:
  if u.get('username') == '$1':
    print(u['id']); break"
}

ensure_user() {
  local username="$1" password="$2" groups_json="$3"
  local existing
  existing=$(uid_of_user "$username")
  if [ -n "$existing" ]; then
    KP -X DELETE "$IAM/users/$existing" >/dev/null 2>&1 || true
  fi
  KP -X POST -H "Content-Type: application/json" \
    -d "{\"username\":\"$username\",\"password\":\"$password\",\"groups\":$groups_json}" \
    "$IAM/users" >/dev/null
  uid_of_user "$username"
}

ALICE_UID=$(ensure_user "$ALICE_USER"     "$ALICE_PASS"     "[\"$ALL_USERS_GID\"]")
BOB_UID=$(ensure_user   "$BOB_USER"       "$BOB_PASS"       "[\"$ALL_USERS_GID\"]")
MEMADMIN_UID=$(ensure_user "$MEMADMIN_USER" "$MEMADMIN_PASS" "[\"$ALL_USERS_GID\",\"$MEMORY_ADMINS_GID\"]")

finalize_user "$ALICE_UID"    "$ALICE_PASS"
finalize_user "$BOB_UID"      "$BOB_PASS"
finalize_user "$MEMADMIN_UID" "$MEMADMIN_PASS"

ALICE_TOKEN=$(get_token "$ALICE_USER"    "$ALICE_PASS")
BOB_TOKEN=$(get_token   "$BOB_USER"      "$BOB_PASS")
MEMADMIN_TOKEN=$(get_token "$MEMADMIN_USER" "$MEMADMIN_PASS")
assert_match "alice token issued"   "^[A-Za-z0-9]+" "$ALICE_TOKEN"
assert_match "bob token issued"     "^[A-Za-z0-9]+" "$BOB_TOKEN"
assert_match "memadmin token issued" "^[A-Za-z0-9]+" "$MEMADMIN_TOKEN"

A()   { curl -s -H "Authorization: Bearer $ALICE_TOKEN"    "$@"; }
AH()  { curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $ALICE_TOKEN"    "$@"; }
B()   { curl -s -H "Authorization: Bearer $BOB_TOKEN"      "$@"; }
BH()  { curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $BOB_TOKEN"      "$@"; }
M()   { curl -s -H "Authorization: Bearer $MEMADMIN_TOKEN" "$@"; }
MH()  { curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $MEMADMIN_TOKEN" "$@"; }
AD()  { curl -s -H "Authorization: Bearer $ADMIN_TOKEN"    "$@"; }
ADH() { curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $ADMIN_TOKEN"   "$@"; }

# ════════════════════════════════════════════════════════════════════════════
section "Section 1: 最终用户数据面 — alice (all-users) 可访问 (7)"
# ════════════════════════════════════════════════════════════════════════════

# D1: alice GET /memory/api/v1/health → 200
assert "D1 alice GET /api/v1/health -> 200" "200" "$(AH $BASE_URL/memory/api/v1/health)"

# D2: alice POST /memory/api/v1/memory/add → 201
D2_BODY=$(A -X POST -H "Content-Type: application/json" \
  -d '{"tenant_id":"seed_tenant","instance_id":"seed_instance","user_id":"u1","content":"hello"}' \
  "$BASE_URL/memory/api/v1/memory/add")
D2_CODE=$(AH -X POST -H "Content-Type: application/json" \
  -d '{"tenant_id":"seed_tenant","instance_id":"seed_instance","user_id":"u1","content":"hello"}' \
  "$BASE_URL/memory/api/v1/memory/add")
assert "D2 alice POST /memory/add -> 201" "201" "$D2_CODE"
assert_contains "D2 response has ack_id" "ack_id" "$D2_BODY"

# D3: alice POST /memory/api/v1/memory/query → 200
assert "D3 alice POST /memory/query -> 200" "200" \
  "$(AH -X POST -H 'Content-Type: application/json' -d '{"tenant_id":"seed_tenant","instance_id":"seed_instance","user_id":"u1","query":"hello","top_k":5}' $BASE_URL/memory/api/v1/memory/query)"

# D4: alice POST /memory/api/v1/memory/update → 200
assert "D4 alice POST /memory/update -> 200" "200" \
  "$(AH -X POST -H 'Content-Type: application/json' -d '{"memory_id":"m1","content":"new"}' $BASE_URL/memory/api/v1/memory/update)"

# D5: alice POST /memory/api/v1/memory/delete → 200
assert "D5 alice POST /memory/delete -> 200" "200" \
  "$(AH -X POST -H 'Content-Type: application/json' -d '{"memory_id":"m1"}' $BASE_URL/memory/api/v1/memory/delete)"

# D6: alice GET /memory/api/v1/templates → 200 (all-users 可只读模板)
D6_BODY=$(A "$BASE_URL/memory/api/v1/templates")
D6_CODE=$(AH "$BASE_URL/memory/api/v1/templates")
assert "D6 alice GET /templates -> 200" "200" "$D6_CODE"
assert_contains "D6 alice sees seeded user_profile template" "user_profile" "$D6_BODY"

# D7: alice GET /memory/api/v1/templates/user_profile → 200 (子路径也 GET-可见)
assert "D7 alice GET /templates/user_profile -> 200" "200" \
  "$(AH $BASE_URL/memory/api/v1/templates/user_profile)"

# ════════════════════════════════════════════════════════════════════════════
section "Section 2: 管理面写 — alice (all-users) 不可访问 (6)"
# ════════════════════════════════════════════════════════════════════════════

# M1: alice POST /memory/api/v1/tenants → 403 rule=path_rule
M1_BODY=$(A -X POST -H "Content-Type: application/json" -d '{"tenant_id":"t1"}' "$BASE_URL/memory/api/v1/tenants")
M1_CODE=$(AH -X POST -H "Content-Type: application/json" -d '{"tenant_id":"t1"}' "$BASE_URL/memory/api/v1/tenants")
assert "M1 alice POST /tenants -> 403" "403" "$M1_CODE"
assert_contains "M1 denial body rule=path_rule" '"rule": "path_rule"' "$M1_BODY"

# M2: alice DELETE /memory/api/v1/tenants/x → 403
assert "M2 alice DELETE /tenants/x -> 403" "403" "$(AH -X DELETE $BASE_URL/memory/api/v1/tenants/x)"

# M3: alice POST /memory/api/v1/tenants/x/instances → 403
assert "M3 alice POST /tenants/x/instances -> 403" "403" \
  "$(AH -X POST -H 'Content-Type: application/json' -d '{"instance_name":"i1"}' $BASE_URL/memory/api/v1/tenants/x/instances)"

# M4: alice POST /memory/api/v1/templates → 403 (写模板仅 memory-admins+)
assert "M4 alice POST /templates -> 403" "403" \
  "$(AH -X POST -H 'Content-Type: application/json' -d '{"template_data":{"tenant_id":"t","template_name":"n","memory_type":"fact"}}' $BASE_URL/memory/api/v1/templates)"

# M5: alice PUT /memory/api/v1/templates/user_profile → 403 (写模板仅 memory-admins+)
assert "M5 alice PUT /templates/user_profile -> 403" "403" \
  "$(AH -X PUT -H 'Content-Type: application/json' -d '{"template_data":{"tenant_id":"t","template_name":"n","memory_type":"fact"}}' $BASE_URL/memory/api/v1/templates/user_profile)"

# M6: alice POST /memory/api/v1/system/recovery → 403 (超管动作，memory_admin_full admins-only)
M6_BODY=$(A -X POST -H "Content-Type: application/json" -d '{"recovery_point":"latest","scope":"all"}' "$BASE_URL/memory/api/v1/system/recovery")
M6_CODE=$(AH -X POST -H "Content-Type: application/json" -d '{"recovery_point":"latest","scope":"all"}' "$BASE_URL/memory/api/v1/system/recovery")
assert "M6 alice POST /system/recovery -> 403" "403" "$M6_CODE"
assert_contains "M6 denial body rule=path_rule" '"rule": "path_rule"' "$M6_BODY"

# ════════════════════════════════════════════════════════════════════════════
section "Section 3: 租户管理员 — memadmin (memory-admins) 管理面能力 (8)"
# ════════════════════════════════════════════════════════════════════════════

# A1: memadmin POST /tenants → 403 (trailing-/ 排除，tenant 创建仅 admins)
A1_BODY=$(M -X POST -H "Content-Type: application/json" -d '{"tenant_id":"memadmin_try"}' "$BASE_URL/memory/api/v1/tenants")
A1_CODE=$(MH -X POST -H "Content-Type: application/json" -d '{"tenant_id":"memadmin_try"}' "$BASE_URL/memory/api/v1/tenants")
assert "A1 memadmin POST /tenants -> 403 (超管动作)" "403" "$A1_CODE"
assert_contains "A1 denial rule=path_rule" '"rule": "path_rule"' "$A1_BODY"

# setup: admin 预创建 tenant（via memory_admin_full）供后续 memadmin 子路径操作
AD -X POST -H "Content-Type: application/json" \
  -d '{"tenant_id":"mem_test_tenant"}' "$BASE_URL/memory/api/v1/tenants" >/dev/null

# A2: memadmin POST /tenants/{id}/instances → 201 (memory_tenant_admin 匹配 /tenants/ 子路径)
assert "A2 memadmin POST /tenants/{id}/instances -> 201" "201" \
  "$(MH -X POST -H 'Content-Type: application/json' -d '{"instance_name":"inst1"}' $BASE_URL/memory/api/v1/tenants/mem_test_tenant/instances)"

# A3: memadmin POST /templates → 201 (写模板 ✓)
A3_BODY=$(M -X POST -H "Content-Type: application/json" \
  -d '{"instance_id":"i1","template_data":{"tenant_id":"t","template_name":"test_tmpl","memory_type":"fact","description":"test"}}' \
  "$BASE_URL/memory/api/v1/templates")
assert_contains "A3 memadmin POST /templates -> success" "success" "$A3_BODY"

# A4: memadmin GET /templates → 200 (lists pre-seeded user_profile + newly created)
A4_BODY=$(M "$BASE_URL/memory/api/v1/templates")
assert_contains "A4 memadmin GET /templates lists user_profile (seed)" "user_profile" "$A4_BODY"

# A5: memadmin GET /templates/user_profile → 200
assert "A5 memadmin GET /templates/user_profile -> 200" "200" \
  "$(MH $BASE_URL/memory/api/v1/templates/user_profile)"

# A6: memadmin POST /templates/user_profile/filters → 200
A6_BODY=$(M -X POST -H "Content-Type: application/json" \
  -d '{"tenant_id":"t","instance_id":"i","whitelist":["agent_001"],"blacklist":["agent_999"]}' \
  "$BASE_URL/memory/api/v1/templates/user_profile/filters")
assert_contains "A6 memadmin filters updated" "agent_001" "$A6_BODY"

# A7: memadmin POST /memory/add → 201 (memory_tenant_admin 也覆盖 /memory/ 数据面)
assert "A7 memadmin POST /memory/add -> 201" "201" \
  "$(MH -X POST -H 'Content-Type: application/json' -d '{"tenant_id":"t","instance_id":"i","user_id":"u","content":"x"}' $BASE_URL/memory/api/v1/memory/add)"

# A8: memadmin POST /system/recovery → 403 (系统级超管动作 ✗)
assert "A8 memadmin POST /system/recovery -> 403" "403" \
  "$(MH -X POST -H 'Content-Type: application/json' -d '{"recovery_point":"latest","scope":"all"}' $BASE_URL/memory/api/v1/system/recovery)"

# cleanup: 删除创建的 tenant
AD -X DELETE "$BASE_URL/memory/api/v1/tenants/mem_test_tenant" >/dev/null

# ════════════════════════════════════════════════════════════════════════════
section "Section 4: 超级管理员 — admin 全覆盖 via memory_admin_full (4)"
# ════════════════════════════════════════════════════════════════════════════

# E1: admin 可创建 tenant（超管动作，仅 admins）
assert "E1 admin POST /tenants -> 201" "201" \
  "$(ADH -X POST -H 'Content-Type: application/json' -d '{"tenant_id":"admin_test"}' $BASE_URL/memory/api/v1/tenants)"

# E2: admin 可访问数据面
assert "E2 admin GET /health -> 200" "200" "$(ADH $BASE_URL/memory/api/v1/health)"

# E3: admin 可访问 memory 的任意路径
assert "E3 admin POST /memory/add -> 201" "201" \
  "$(ADH -X POST -H 'Content-Type: application/json' -d '{"tenant_id":"t","instance_id":"i","user_id":"u","content":"x"}' $BASE_URL/memory/api/v1/memory/add)"

# E4: admin 可执行系统恢复（memadmin/alice 均不能）
assert "E4 admin POST /system/recovery -> 200" "200" \
  "$(ADH -X POST -H 'Content-Type: application/json' -d '{"recovery_point":"latest","scope":"all"}' $BASE_URL/memory/api/v1/system/recovery)"

AD -X DELETE "$BASE_URL/memory/api/v1/tenants/admin_test" >/dev/null

# ════════════════════════════════════════════════════════════════════════════
section "Section 5: app_disabled + 结构化拒绝 (5)"
# ════════════════════════════════════════════════════════════════════════════

# S1: no-token 任何 memory 路径 → 401
S1=$(curl -s "$BASE_URL/memory/api/v1/health")
assert_match "S1 no-token -> 401" "^(401|403)" \
  "$(curl -s -o /dev/null -w "%{http_code}" $BASE_URL/memory/api/v1/health)"
assert_contains "S1 no-token body code=unauthorized" '"code": "unauthorized"' "$S1"
assert_contains "S1 no-token rule=authentication"   '"rule": "authentication"' "$S1"

# S2: app_disabled 切换
psql_iam "UPDATE apps SET enabled=false WHERE app_name='memory';" >/dev/null
sleep 35
S2_BODY=$(A "$BASE_URL/memory/api/v1/health")
S2_CODE=$(AH "$BASE_URL/memory/api/v1/health")
assert "S2 app_disabled -> 403" "403" "$S2_CODE"
assert_contains "S2 denial body rule=app_disabled" '"rule": "app_disabled"' "$S2_BODY"
psql_iam "UPDATE apps SET enabled=true WHERE app_name='memory';" >/dev/null
sleep 35

# ════════════════════════════════════════════════════════════════════════════
section "Section 6: permission_groups 种子核查 (7)"
# ════════════════════════════════════════════════════════════════════════════

# memory app 下 4 个功能点 + memory_admin_full (app_name='memory') = 5
PG_COUNT=$(psql_iam "SELECT COUNT(*) FROM permission_groups WHERE app_name='memory';")
assert "Memory permission_groups seeded (5 = admin_full + 4 功能点)" "5" "$PG_COUNT"

# memory_admin_full 仅绑 admins（新设计：memory-admins 移除）
MAF=$(psql_iam "SELECT kc_group_name FROM permission_group_bindings WHERE group_id=(SELECT id FROM permission_groups WHERE name='memory_admin_full') ORDER BY kc_group_name;")
assert_contains "memory_admin_full 含 admins"             "admins"        "$MAF"
assert_not_contains "memory_admin_full 已移除 memory-admins" "memory-admins" "$MAF"

# memory_tenant_admin 只绑 memory-admins
TA=$(psql_iam "SELECT kc_group_name FROM permission_group_bindings WHERE group_id=(SELECT id FROM permission_groups WHERE name='memory_tenant_admin') ORDER BY kc_group_name;")
assert_contains     "memory_tenant_admin 含 memory-admins" "memory-admins" "$TA"
assert_not_contains "memory_tenant_admin 不绑 all-users"   "all-users"     "$TA"

# memory_user_data 绑 all-users
UD=$(psql_iam "SELECT kc_group_name FROM permission_group_bindings WHERE group_id=(SELECT id FROM permission_groups WHERE name='memory_user_data') ORDER BY kc_group_name;")
assert_contains "memory_user_data 含 all-users" "all-users" "$UD"

# memory_user_templates_read 绑 all-users（新增，提供末端用户模板只读）
UTR=$(psql_iam "SELECT kc_group_name FROM permission_group_bindings WHERE group_id=(SELECT id FROM permission_groups WHERE name='memory_user_templates_read') ORDER BY kc_group_name;")
assert_contains "memory_user_templates_read 含 all-users" "all-users" "$UTR"

# ════════════════════════════════════════════════════════════════════════════
section "Summary"
# ════════════════════════════════════════════════════════════════════════════
echo -e "  Total:  ${TOTAL}"
echo -e "  ${GREEN}PASS: ${PASS}${NC}"
echo -e "  ${RED}FAIL: ${FAIL}${NC}"
if [ "$FAIL" -eq 0 ]; then
  echo -e "\n${GREEN}ALL MEMORY FULL TESTS PASSED${NC}"
  exit 0
else
  exit 1
fi
