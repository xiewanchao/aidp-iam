#!/usr/bin/env bash
# ============================================================================
# test-memory-full.sh — Full UnifiedMem (memory) auth matrix (mock-memory only)
#
# 覆盖：
#   - permission_groups 模型驱动的路径级鉴权（管理面 vs 数据面）
#   - 管理面（/tenants, /templates）只对 memory-admins / admins 开放
#   - 数据面（/memory, /health, /system）对 all-users 开放
#   - memory_admin_full 覆盖所有路径
#   - app_disabled 切换
#   - 结构化拒绝响应
#   - permission_groups 种子核查
#
# 注：memory 目前没有资源级鉴权 pattern（api 结构 body 里有 tenant_id/
# instance_id/memory_id 但没有统一的 resource_type 语义），因此测试聚焦
# 路径级 + 管理面隔离。
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
  local uid="$1" pw="$2" uname="$3"
  curl -s -X PUT -H "Authorization: Bearer $KC_MASTER_TOKEN" -H "Content-Type: application/json" \
    -d "{\"requiredActions\":[],\"enabled\":true,\"emailVerified\":true,\"email\":\"$uname@test.local\",\"firstName\":\"$uname\",\"lastName\":\"Test\"}" \
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

finalize_user "$ALICE_UID"    "$ALICE_PASS"    "$ALICE_USER"
finalize_user "$BOB_UID"      "$BOB_PASS"      "$BOB_USER"
finalize_user "$MEMADMIN_UID" "$MEMADMIN_PASS" "$MEMADMIN_USER"

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
section "Section 1: 数据面 — all-users 可访问 (6)"
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

# D6: alice POST /memory/api/v1/system/recovery → 200
assert "D6 alice POST /system/recovery -> 200" "200" \
  "$(AH -X POST -H 'Content-Type: application/json' -d '{"recovery_point":"latest","scope":"all"}' $BASE_URL/memory/api/v1/system/recovery)"

# ════════════════════════════════════════════════════════════════════════════
section "Section 2: 管理面 — alice (all-users) 不可访问 (5)"
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

# M4: alice POST /memory/api/v1/templates → 403
assert "M4 alice POST /templates -> 403" "403" \
  "$(AH -X POST -H 'Content-Type: application/json' -d '{"template_data":{"tenant_id":"t","template_name":"n","memory_type":"fact"}}' $BASE_URL/memory/api/v1/templates)"

# M5: alice GET /memory/api/v1/templates → 403
assert "M5 alice GET /templates -> 403" "403" "$(AH $BASE_URL/memory/api/v1/templates)"

# ════════════════════════════════════════════════════════════════════════════
section "Section 3: 管理面 — memadmin (memory-admins) 可访问 (6)"
# ════════════════════════════════════════════════════════════════════════════

# A1: memadmin POST /tenants → 201
A1_BODY=$(M -X POST -H "Content-Type: application/json" -d '{"tenant_id":"mem_test_tenant"}' "$BASE_URL/memory/api/v1/tenants")
A1_CODE=$(MH -X POST -H "Content-Type: application/json" -d '{"tenant_id":"mem_test_tenant_2"}' "$BASE_URL/memory/api/v1/tenants")
assert "A1 memadmin POST /tenants -> 201" "201" "$A1_CODE"
assert_match "A1 tenant response has status=success" '"status"[[:space:]]*:[[:space:]]*"success"' "$A1_BODY"

# A2: memadmin POST /tenants/{id}/instances → 201
assert "A2 memadmin POST /tenants/{id}/instances -> 201" "201" \
  "$(MH -X POST -H 'Content-Type: application/json' -d '{"instance_name":"inst1"}' $BASE_URL/memory/api/v1/tenants/mem_test_tenant/instances)"

# A3: memadmin POST /templates → 201
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

# cleanup: 删除创建的 tenant
M -X DELETE "$BASE_URL/memory/api/v1/tenants/mem_test_tenant" >/dev/null
M -X DELETE "$BASE_URL/memory/api/v1/tenants/mem_test_tenant_2" >/dev/null

# ════════════════════════════════════════════════════════════════════════════
section "Section 4: admin 全覆盖 via memory_admin_full (3)"
# ════════════════════════════════════════════════════════════════════════════

# E1: admin 也能访问管理面（admins in memory_admin_full）
assert "E1 admin POST /tenants -> 201" "201" \
  "$(ADH -X POST -H 'Content-Type: application/json' -d '{"tenant_id":"admin_test"}' $BASE_URL/memory/api/v1/tenants)"

# E2: admin 也能访问数据面
assert "E2 admin GET /health -> 200" "200" "$(ADH $BASE_URL/memory/api/v1/health)"

# E3: admin 能访问 memory 的任意路径
assert "E3 admin POST /memory/add -> 201" "201" \
  "$(ADH -X POST -H 'Content-Type: application/json' -d '{"tenant_id":"t","instance_id":"i","user_id":"u","content":"x"}' $BASE_URL/memory/api/v1/memory/add)"

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
section "Section 6: permission_groups 种子核查 (6)"
# ════════════════════════════════════════════════════════════════════════════

# memory app 下 5 个 + memory_admin_full (app_name='memory') = 6
PG_COUNT=$(psql_iam "SELECT COUNT(*) FROM permission_groups WHERE app_name='memory';")
assert "Memory permission_groups seeded (6 个含 admin_full)" "6" "$PG_COUNT"

# memory_admin_full 含 admins + memory-admins
MAF=$(psql_iam "SELECT kc_group_name FROM permission_group_bindings WHERE group_id=(SELECT id FROM permission_groups WHERE name='memory_admin_full') ORDER BY kc_group_name;")
assert_contains "memory_admin_full 含 admins"        "admins"        "$MAF"
assert_contains "memory_admin_full 含 memory-admins" "memory-admins" "$MAF"

# memory_tenant_manage 只绑 memory-admins
TM=$(psql_iam "SELECT kc_group_name FROM permission_group_bindings WHERE group_id=(SELECT id FROM permission_groups WHERE name='memory_tenant_manage') ORDER BY kc_group_name;")
assert_contains "memory_tenant_manage 含 memory-admins" "memory-admins" "$TM"
assert_not_contains "memory_tenant_manage 不绑 all-users" "all-users" "$TM"

# memory_data_rw 绑 all-users
DRW=$(psql_iam "SELECT kc_group_name FROM permission_group_bindings WHERE group_id=(SELECT id FROM permission_groups WHERE name='memory_data_rw') ORDER BY kc_group_name;")
assert_contains "memory_data_rw 含 all-users" "all-users" "$DRW"

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
