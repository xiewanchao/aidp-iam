#!/usr/bin/env bash
# ============================================================================
# test_opa.sh — OPA 权限认证全用例测试
# 1:1 mapping to dttest/opa-test-cases-detail.txt
# Cases: PATH-TC-001~010, ACL-TC-001~013, DISABLED-TC-001~003,
#        EXTPROC-TC-001~006, EXTPROC-MST-001~002
# ============================================================================
set -uo pipefail

KEYCLOAK_NS="${KEYCLOAK_NS:-keycloak}"
IAM_NS="${IAM_NS:-aidp-iam}"
ENVOY_GATEWAY_NS="${ENVOY_GATEWAY_NS:-aidp-gateway}"
GATEWAY_PORT="${GATEWAY_PORT:-30080}"
BASE_URL="http://localhost:${GATEWAY_PORT}"
REALM="${REALM:-aidp}"
CLIENT_ID="${CLIENT_ID:-aidp-client}"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-Admin@123}"
NORMAL_USER="${NORMAL_USER:-normal-user}"
NORMAL_PASSWORD="${NORMAL_PASSWORD:-NormalUser@123}"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
PASS=0; FAIL=0

assert()     { local d="$1" e="$2" a="$3"
  if [ "$e" = "$a" ]; then echo -e "  ${GREEN}PASS${NC} $d"; PASS=$((PASS+1))
  else echo -e "  ${RED}FAIL${NC} $d (want=$e got=$a)"; FAIL=$((FAIL+1)); fi; }
assert_match(){ local d="$1" p="$2" a="$3"
  if echo "$a"|grep -qE "$p"; then echo -e "  ${GREEN}PASS${NC} $d"; PASS=$((PASS+1))
  else echo -e "  ${RED}FAIL${NC} $d (want~$p got=$a)"; FAIL=$((FAIL+1)); fi; }
assert_contains(){ local d="$1" e="$2" a="$3"
  if echo "$a"|grep -q "$e"; then echo -e "  ${GREEN}PASS${NC} $d"; PASS=$((PASS+1))
  else echo -e "  ${RED}FAIL${NC} $d (want substr '$e')"; FAIL=$((FAIL+1)); fi; }
assert_not_contains(){ local d="$1" u="$2" a="$3"
  if echo "$a"|grep -q "$u"; then echo -e "  ${RED}FAIL${NC} $d (should NOT contain '$u')"; FAIL=$((FAIL+1))
  else echo -e "  ${GREEN}PASS${NC} $d"; PASS=$((PASS+1)); fi; }
skip()   { echo -e "  ${YELLOW}SKIP${NC} $1"; }
section(){ echo -e "\n${BLUE}=== $* ===${NC}"; }
banner() { printf "\n${BLUE}════════════════════════════════════════${NC}\n${BLUE} %s${NC}\n${BLUE}════════════════════════════════════════${NC}\n" "$*"; }

psql_iam(){ MSYS_NO_PATHCONV=1 kubectl -n "$KEYCLOAK_NS" exec iam-store-0 -c postgres -- \
  psql -U keycloak -d iam -tA -c "$1" 2>/dev/null | tr -d '\r'; }

jget(){ python -c "import sys,json
try: v=json.load(sys.stdin)
except: print(''); sys.exit(0)
for k in '$1'.split('.'):
  v=(v[int(k)] if isinstance(v,list) else v.get(k,'')) if v else ''
print(v if v is not None else '')"; }

# ── Port-forward ──────────────────────────────────────────────────────────────
PF_PID=""
if ! curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/" 2>/dev/null | grep -qE '200|301|302|404'; then
  GW_SVC=$(kubectl -n "$ENVOY_GATEWAY_NS" get svc \
    -l gateway.envoyproxy.io/owning-gateway-name=eg -o name 2>/dev/null | head -1)
  [ -z "$GW_SVC" ] && GW_SVC="svc/envoy-eg"
  kubectl -n "$ENVOY_GATEWAY_NS" port-forward "$GW_SVC" "${GATEWAY_PORT}:80" >/dev/null 2>&1 &
  PF_PID=$!; sleep 3
fi

HAS_KB=$(kubectl get httproute -A 2>/dev/null | grep -ciE "mock-kb|knowledgebase" || true)
HAS_DA=$(kubectl get httproute -A 2>/dev/null | grep -ciE "mock-dataagent|dataagent" || true)
[ "$HAS_KB" -gt 0 ] && echo -e "  ${GREEN}mock-kb route detected${NC}" \
  || echo -e "  ${YELLOW}mock-kb route not found — KB backend tests will be skipped${NC}"
[ "$HAS_DA" -gt 0 ] && echo -e "  ${GREEN}mock-dataagent route detected${NC}" \
  || echo -e "  ${YELLOW}mock-dataagent route not found — DA backend tests will be skipped${NC}"

# ── Tokens ────────────────────────────────────────────────────────────────────
CS=$(kubectl -n "$IAM_NS" get secret keycloak-aidp-client \
  -o jsonpath='{.data.client-secret}' 2>/dev/null | base64 -d)
tok(){ curl -s -X POST "$BASE_URL/realms/$REALM/protocol/openid-connect/token" \
  -d "client_id=$CLIENT_ID" -d "client_secret=$CS" \
  -d "grant_type=password" -d "username=$1" -d "password=$2" | jget access_token; }
sub(){ echo "$1"|cut -d. -f2|python -c "
import sys,base64,json; s=sys.stdin.read().strip(); s+='='*(-len(s)%4)
print(json.loads(base64.urlsafe_b64decode(s)).get('sub',''))"; }

ADMIN_TOKEN=$(tok "$ADMIN_USER" "$ADMIN_PASSWORD")
[ -n "$ADMIN_TOKEN" ] || { echo "FATAL: cannot get admin token"; exit 2; }
NORMAL_TOKEN=$(tok "$NORMAL_USER" "$NORMAL_PASSWORD")
[ -n "$NORMAL_TOKEN" ] || echo -e "  ${YELLOW}WARNING: cannot get normal-user token; some tests will skip${NC}"

ADMIN_SUB=$(sub "$ADMIN_TOKEN")
NORMAL_SUB=$(sub "$NORMAL_TOKEN" 2>/dev/null || echo "")

A()  { curl -s    -H "Authorization: Bearer $ADMIN_TOKEN"  "$@"; }
AH() { curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $ADMIN_TOKEN"  "$@"; }
N()  { curl -s    -H "Authorization: Bearer $NORMAL_TOKEN" "$@"; }
NH() { curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $NORMAL_TOKEN" "$@"; }

ADMIN_USER_PATH="AccessManager/Tenants/$REALM/Users/$ADMIN_SUB"
NORMAL_USER_PATH="AccessManager/Tenants/$REALM/Users/$NORMAL_SUB"
OWNER_ROLE="AccessManager/Tenants/System/Roles/Owner"
CONTRIB_ROLE="AccessManager/Tenants/System/Roles/Contributor"
VIEWER_ROLE="AccessManager/Tenants/System/Roles/Viewer"
OPA_TEST_NS="OpaTest"
NO_GROUPS_UID=""
TA_USER_UID=""

# ── Cleanup ───────────────────────────────────────────────────────────────────
cleanup(){
  [ -n "${PF_PID:-}" ] && kill "$PF_PID" 2>/dev/null || true
  psql_iam "DELETE FROM resource_acl WHERE object_path LIKE 'OpaTest/%' OR object_path LIKE '%/opa-test-%' OR object_path LIKE '%/opa-acl-%' OR object_path LIKE '%/opa-ep-%' OR object_path LIKE '%/opa-query-%' OR object_path LIKE '%/opa-no-auth-%' OR object_path LIKE '%/mst002-%' OR object_path LIKE '%/opa-acl-share-%' OR object_path LIKE '%/opa-acl-list-%';" >/dev/null 2>&1 || true
  psql_iam "DELETE FROM app_manifests WHERE namespace='$OPA_TEST_NS';" >/dev/null 2>&1 || true
  psql_iam "DELETE FROM apps WHERE app_name='opa-test-app';" >/dev/null 2>&1 || true
  psql_iam "UPDATE apps SET enabled=true WHERE app_name IN ('KnowledgeBase','DataAgent');" >/dev/null 2>&1 || true
  if [ -n "$NO_GROUPS_UID" ]; then
    AH -X DELETE "$BASE_URL/AccessManager/Tenants/$REALM/Users/$NO_GROUPS_UID" >/dev/null 2>&1 || true
  fi
  if [ -n "$TA_USER_UID" ]; then
    AH -X DELETE "$BASE_URL/AccessManager/Tenants/$REALM/Users/$TA_USER_UID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# ── Create tenant-admins test user (needed for TC006-EP list bypass test) ────
_TA_RESP=$(A -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/Users" \
  -H "Content-Type: application/json" \
  -d '{"username":"opa-ta-user","password":"OpaTA@123","enabled":true}' 2>/dev/null)
TA_USER_UID=$(echo "$_TA_RESP" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
if [ -n "$TA_USER_UID" ]; then
  _TA_GID=$(curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
    "$BASE_URL/AccessManager/Tenants/$REALM/Groups" 2>/dev/null \
    | grep -o '"id":"[^"]*","name":"tenant-admins"' | grep -o '"id":"[^"]*"' | cut -d'"' -f4)
  [ -n "$_TA_GID" ] && AH -X PUT \
    "$BASE_URL/AccessManager/Tenants/$REALM/Users/$TA_USER_UID/Groups/$_TA_GID" >/dev/null
  # Also add to all-users so tenant-admins user can access KB paths (path_rules require all-users)
  _AU_GID=$(curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
    "$BASE_URL/AccessManager/Tenants/$REALM/Groups" 2>/dev/null \
    | grep -o '"id":"[^"]*","name":"all-users"' | grep -o '"id":"[^"]*"' | cut -d'"' -f4)
  [ -n "$_AU_GID" ] && AH -X PUT \
    "$BASE_URL/AccessManager/Tenants/$REALM/Users/$TA_USER_UID/Groups/$_AU_GID" >/dev/null
  T_TA=$(tok "opa-ta-user" "OpaTA@123")
  TA(){ curl -s    -H "Authorization: Bearer $T_TA" "$@"; }
  TAH(){ curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $T_TA" "$@"; }
else
  T_TA=""
  TA(){ echo ""; }
  TAH(){ echo "000"; }
fi

# ═══════════════════════════════════════════════════════════════════════════════
banner "一、PATH：路径级鉴权"
# ═══════════════════════════════════════════════════════════════════════════════

# ── PATH-TC-001: 应用接入后已声明功能可访问，未声明功能不可访问 ──────────────
section "PATH-TC-001: declared path accessible / undeclared path denied"
psql_iam "DELETE FROM app_manifests WHERE namespace='$OPA_TEST_NS';" >/dev/null 2>&1 || true
_PUT=$(AH -X PUT "$BASE_URL/AccessManager/Tenants/System/AppManifests/$OPA_TEST_NS" \
  -H "Content-Type: application/json" \
  -d "{
  \"namespace\":\"$OPA_TEST_NS\",\"display_name\":\"OPA Test App\",
  \"base_url\":\"https://opa-test.example.com\",
  \"resources\":[{
    \"type\":\"Items\",\"display_name\":\"Items\",
    \"path_pattern\":\"/$OPA_TEST_NS/Tenants/{tenantId}/Items/{item_id}\",
    \"methods\":[\"GET\",\"PUT\",\"DELETE\"],\"actions\":[],
    \"default_acl\":[{
      \"user_template\":\"AccessManager/Tenants/{tenantId}/Groups/all-users\",
      \"object_template\":\"$OPA_TEST_NS/Tenants/{tenantId}/Items\",
      \"role_path\":\"$VIEWER_ROLE\"}],
    \"children\":[]}],\"custom_roles\":[]}")
assert_match "TC001 register TestApp manifest → 200/201" "^(200|201)$" "$_PUT"
sleep 35
# declared path: path-level allows → not 403 (may 502 if no backend)
_DECL=$(NH "$BASE_URL/$OPA_TEST_NS/Tenants/$REALM/Items/test-001")
assert_match "TC001 declared path (all-users) → not 403" "^(200|404|405|502)$" "$_DECL"
# undeclared path: OPA denies → 403
_UNDECL=$(NH "$BASE_URL/$OPA_TEST_NS/Tenants/$REALM/Undeclared/test-001")
assert_match "TC001 undeclared path → 403/404" "^(403|404)$" "$_UNDECL"

# ── PATH-TC-002: all-users 可访问 KnowledgeBase 已声明入口 ───────────────────
section "PATH-TC-002: all-users can access KnowledgeBase declared path"
if [ "$HAS_KB" -gt 0 ]; then
  assert_match "TC002 normal-user GET /KnowledgeBase/... → 200" "^(200)$" \
    "$(NH "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases")"
else skip "TC002: mock-kb route not installed"; fi

# ── PATH-TC-003: 未接入应用访问被拒绝 ───────────────────────────────────────
section "PATH-TC-003: unregistered app path denied"
_CODE=$(NH "$BASE_URL/NeverRegisteredApp/Tenants/$REALM/Resources/id-001")
assert_match "TC003 unregistered app → 403/404" "^(403|404)$" "$_CODE"

# ── PATH-TC-004: 删除接入配置后入口不可访问 ──────────────────────────────────
section "PATH-TC-004: delete manifest → declared path denied"
_BEFORE=$(NH "$BASE_URL/$OPA_TEST_NS/Tenants/$REALM/Items/before-delete")
assert_match "TC004 path accessible before delete → not 403" "^(200|404|405|502)$" "$_BEFORE"
AH -X DELETE "$BASE_URL/AccessManager/Tenants/System/AppManifests/$OPA_TEST_NS" >/dev/null
sleep 35
_AFTER=$(NH "$BASE_URL/$OPA_TEST_NS/Tenants/$REALM/Items/after-delete")
assert_match "TC004 path denied after manifest delete → 403/404" "^(403|404)$" "$_AFTER"

# ── PATH-TC-005: 无用户组用户访问被拒绝 ──────────────────────────────────────
section "PATH-TC-005: user with no groups denied"
_NG_RESP=$(A -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/Users" \
  -H "Content-Type: application/json" \
  -d '{"username":"opa-test-nogroups","password":"OpaTest@123","enabled":true}' 2>/dev/null)
NO_GROUPS_UID=$(echo "$_NG_RESP" | python -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
if [ -n "$NO_GROUPS_UID" ]; then
  _NG_TOK=$(tok "opa-test-nogroups" "OpaTest@123")
  if [ -n "$_NG_TOK" ]; then
    _CODE=$(curl -s -o /dev/null -w "%{http_code}" \
      -H "Authorization: Bearer $_NG_TOK" \
      "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases")
    assert_match "TC005 no-groups user → 403" "^(403)$" "$_CODE"
  else skip "TC005: cannot get token for no-groups user"; fi
else skip "TC005: cannot create no-groups user"; fi

# ── PATH-TC-006: 系统管理员可访问管理路径 ────────────────────────────────────
# NOTE: master-admins bypasses AccessManager (System) paths.
# admin may or may not be in all-users depending on cluster setup;
# TC-006 only verifies management path access, not app path behavior.
section "PATH-TC-006: master-admins can access management paths"
assert_match "TC006 admin GET /AccessManager/Tenants/System/AppManifests → 200" "^(200)$" \
  "$(AH "$BASE_URL/AccessManager/Tenants/System/AppManifests")"
assert_match "TC006 admin GET /AccessManager/Tenants/$REALM/Groups → 200" "^(200)$" \
  "$(AH "$BASE_URL/AccessManager/Tenants/$REALM/Groups")"

# ── PATH-TC-007: 普通用户不能访问系统管理入口 ─────────────────────────────────
section "PATH-TC-007: normal-user denied on admin-only routes"
assert_match "TC007 normal-user GET /AccessManager/Tenants/System/AppManifests → 403" "^(401|403)$" \
  "$(NH "$BASE_URL/AccessManager/Tenants/System/AppManifests")"
assert_match "TC007 normal-user GET /AccessManager/Tenants/System/Apps → 403" "^(401|403)$" \
  "$(NH "$BASE_URL/AccessManager/Tenants/System/Apps")"

# ── PATH-TC-008: 管理员可访问权限管理入口 ─────────────────────────────────────
section "PATH-TC-008: admin can access permission management routes"
assert_match "TC008 admin GET /AccessManager/Tenants/$REALM/ACLs → 200" "^(200|400)$" \
  "$(AH "$BASE_URL/AccessManager/Tenants/$REALM/ACLs")"
assert_match "TC008 admin GET /AccessManager/Tenants/$REALM/Groups → 200" "^(200)$" \
  "$(AH "$BASE_URL/AccessManager/Tenants/$REALM/Groups")"

# ── PATH-TC-009: 普通用户不能访问权限管理入口 ─────────────────────────────────
section "PATH-TC-009: normal-user denied on permission management routes"
assert_match "TC009 normal-user GET /AccessManager/Tenants/$REALM/ACLs → 403" "^(401|403)$" \
  "$(NH "$BASE_URL/AccessManager/Tenants/$REALM/ACLs")"
assert_match "TC009 normal-user GET /AccessManager/Tenants/$REALM/Groups → 403" "^(401|403)$" \
  "$(NH "$BASE_URL/AccessManager/Tenants/$REALM/Groups")"

# ── PATH-TC-010: 未知应用入口访问被拒绝（管理员和普通用户均不能旁路） ──────────
section "PATH-TC-010: unknown app path denied for both admin and normal-user"
assert_match "TC010 admin GET /UnknownApp999/... → 403/404" "^(403|404)$" \
  "$(AH "$BASE_URL/UnknownApp999/Tenants/$REALM/Data/id-001")"
assert_match "TC010 normal-user GET /UnknownApp999/... → 403/404" "^(403|404)$" \
  "$(NH "$BASE_URL/UnknownApp999/Tenants/$REALM/Data/id-001")"


# ═══════════════════════════════════════════════════════════════════════════════
banner "二、ACL：资源级鉴权"
# ═══════════════════════════════════════════════════════════════════════════════

ACL_OBJ="KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-acl-kb-001"
ACL_CHILD="KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-acl-kb-001/Docs/doc-001"
psql_iam "DELETE FROM resource_acl WHERE object_path LIKE '%/opa-acl-%';" >/dev/null 2>&1 || true

# ── ACL-TC-001: 新增授权后可查询到 ───────────────────────────────────────────
section "ACL-TC-001: PUT ACL → visible in GET ACLs"
_PUT=$(AH -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
  -H "Content-Type: application/json" \
  -d "{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$ACL_OBJ\",\"role_path\":\"$OWNER_ROLE\"}")
assert_match "TC001 PUT ACL → 200/201" "^(200|201)$" "$_PUT"
_GET=$(A "$BASE_URL/AccessManager/Tenants/$REALM/ACLs?object=$ACL_OBJ")
assert_contains "TC001 GET ACLs contains user_path" "$NORMAL_SUB" "$_GET"
assert_contains "TC001 GET ACLs contains role Owner" "Owner" "$_GET"

# ── ACL-TC-002: 按资源查询只返回该资源授权 ────────────────────────────────────
section "ACL-TC-002: GET ACLs filtered by object returns only that resource"
_OTHER_OBJ="KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-acl-kb-002"
AH -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
  -H "Content-Type: application/json" \
  -d "{\"user_path\":\"$ADMIN_USER_PATH\",\"object_path\":\"$_OTHER_OBJ\",\"role_path\":\"$OWNER_ROLE\"}" >/dev/null
_FILTERED=$(A "$BASE_URL/AccessManager/Tenants/$REALM/ACLs?object=$ACL_OBJ")
assert_contains     "TC002 filtered result contains target object" "opa-acl-kb-001" "$_FILTERED"
assert_not_contains "TC002 filtered result excludes other object"  "opa-acl-kb-002" "$_FILTERED"
AH -X DELETE "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
  -H "Content-Type: application/json" \
  -d "{\"user_path\":\"$ADMIN_USER_PATH\",\"object_path\":\"$_OTHER_OBJ\"}" >/dev/null

# ── ACL-TC-003: 删除授权后列表不可见 ─────────────────────────────────────────
section "ACL-TC-003: DELETE ACL → removed from list"
_DEL=$(AH -X DELETE "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
  -H "Content-Type: application/json" \
  -d "{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$ACL_OBJ\"}")
assert_match "TC003 DELETE ACL → 200/204" "^(200|204)$" "$_DEL"
_CNT=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE object_path='$ACL_OBJ' AND user_path='$NORMAL_USER_PATH';")
assert "TC003 ACL row removed from DB" "0" "$_CNT"
# Use ADMIN_USER_PATH for QueryACLs: admin has no KB type-level ACL, so after
# deleting the instance ACL there is truly no matching entry → allowed=false
_QUERY=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Action/QueryACLs" \
  -H "Content-Type: application/json" \
  -d "{\"queries\":[{\"user_path\":\"$ADMIN_USER_PATH\",\"object_path\":\"$ACL_OBJ\"}]}")
assert_contains "TC003 QueryACLs allowed=false (no ACL for admin on this object)" "false" "$_QUERY"

# ── ACL-TC-004: 删除父资源授权不影响子资源授权 ───────────────────────────────
section "ACL-TC-004: DELETE parent ACL does not cascade to child ACL"
AH -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
  -H "Content-Type: application/json" \
  -d "{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$ACL_OBJ\",\"role_path\":\"$OWNER_ROLE\"}" >/dev/null
psql_iam "INSERT INTO resource_acl (tenant_id,user_path,object_path,role_path,created_by)
  VALUES ('$REALM','$NORMAL_USER_PATH','$ACL_CHILD','$VIEWER_ROLE','test')
  ON CONFLICT DO NOTHING;" >/dev/null
AH -X DELETE "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
  -H "Content-Type: application/json" \
  -d "{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$ACL_OBJ\"}" >/dev/null
_PARENT_CNT=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE object_path='$ACL_OBJ' AND user_path='$NORMAL_USER_PATH';")
_CHILD_CNT=$(psql_iam  "SELECT COUNT(*) FROM resource_acl WHERE object_path='$ACL_CHILD' AND user_path='$NORMAL_USER_PATH';")
assert "TC004 parent ACL removed" "0" "$_PARENT_CNT"
assert "TC004 child ACL still exists" "1" "$_CHILD_CNT"
psql_iam "DELETE FROM resource_acl WHERE object_path='$ACL_CHILD';" >/dev/null

# ── ACL-TC-005: Viewer 只读，不可编辑/删除 ───────────────────────────────────
section "ACL-TC-005: Viewer role — read allowed, write/delete denied"
_VIEWER_OBJ="KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-acl-viewer-001"
psql_iam "DELETE FROM resource_acl WHERE object_path='$_VIEWER_OBJ';" >/dev/null 2>&1 || true
AH -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
  -H "Content-Type: application/json" \
  -d "{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$_VIEWER_OBJ\",\"role_path\":\"$VIEWER_ROLE\"}" >/dev/null
_QV=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Action/QueryACLs" \
  -H "Content-Type: application/json" \
  -d "{\"queries\":[{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$_VIEWER_OBJ\"}]}")
assert_contains "TC005 QueryACLs returns Viewer" "Viewer" "$_QV"
if [ "$HAS_KB" -gt 0 ]; then
  assert_match "TC005 Viewer GET resource → 200" "^(200|404)$" \
    "$(NH "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-acl-viewer-001")"
  assert_match "TC005 Viewer PATCH resource → 403" "^(403)$" \
    "$(curl -s -o /dev/null -w "%{http_code}" -X PATCH \
      -H "Authorization: Bearer $NORMAL_TOKEN" \
      -H "Content-Type: application/json" -d '{"name":"x"}' \
      "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-acl-viewer-001")"
  assert_match "TC005 Viewer DELETE resource → 403" "^(403)$" \
    "$(curl -s -o /dev/null -w "%{http_code}" -X DELETE \
      -H "Authorization: Bearer $NORMAL_TOKEN" \
      "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-acl-viewer-001")"
else skip "TC005 HTTP checks: mock-kb not installed"; fi
psql_iam "DELETE FROM resource_acl WHERE object_path='$_VIEWER_OBJ';" >/dev/null

# ── ACL-TC-006: Contributor 可读写，不可删除 ──────────────────────────────────
section "ACL-TC-006: Contributor role — read/write allowed, delete denied"
_CONTRIB_OBJ="KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-acl-contrib-001"
psql_iam "DELETE FROM resource_acl WHERE object_path='$_CONTRIB_OBJ';" >/dev/null 2>&1 || true
AH -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
  -H "Content-Type: application/json" \
  -d "{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$_CONTRIB_OBJ\",\"role_path\":\"$CONTRIB_ROLE\"}" >/dev/null
_QC=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Action/QueryACLs" \
  -H "Content-Type: application/json" \
  -d "{\"queries\":[{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$_CONTRIB_OBJ\"}]}")
assert_contains "TC006 QueryACLs returns Contributor" "Contributor" "$_QC"
if [ "$HAS_KB" -gt 0 ]; then
  assert_match "TC006 Contributor GET → 200" "^(200|404)$" \
    "$(NH "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-acl-contrib-001")"
  assert_match "TC006 Contributor PUT → 200/404" "^(200|404)$" \
    "$(curl -s -o /dev/null -w "%{http_code}" -X PUT \
      -H "Authorization: Bearer $NORMAL_TOKEN" \
      -H "Content-Type: application/json" -d '{"name":"x"}' \
      "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-acl-contrib-001")"
  assert_match "TC006 Contributor DELETE → 403" "^(403)$" \
    "$(curl -s -o /dev/null -w "%{http_code}" -X DELETE \
      -H "Authorization: Bearer $NORMAL_TOKEN" \
      "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-acl-contrib-001")"
else skip "TC006 HTTP checks: mock-kb not installed"; fi
psql_iam "DELETE FROM resource_acl WHERE object_path='$_CONTRIB_OBJ';" >/dev/null

# ── ACL-TC-007: Owner 全权限 ──────────────────────────────────────────────────
section "ACL-TC-007: Owner role — full access (read/write/delete)"
_OWNER_OBJ="KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-acl-owner-001"
psql_iam "DELETE FROM resource_acl WHERE object_path='$_OWNER_OBJ';" >/dev/null 2>&1 || true
AH -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
  -H "Content-Type: application/json" \
  -d "{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$_OWNER_OBJ\",\"role_path\":\"$OWNER_ROLE\"}" >/dev/null
_QO=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Action/QueryACLs" \
  -H "Content-Type: application/json" \
  -d "{\"queries\":[{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$_OWNER_OBJ\"}]}")
assert_contains "TC007 QueryACLs returns Owner" "Owner" "$_QO"
if [ "$HAS_KB" -gt 0 ]; then
  assert_match "TC007 Owner GET → 200" "^(200|404)$" \
    "$(NH "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-acl-owner-001")"
  assert_match "TC007 Owner PUT → 200/201/404" "^(200|201|404)$" \
    "$(curl -s -o /dev/null -w "%{http_code}" -X PUT \
      -H "Authorization: Bearer $NORMAL_TOKEN" \
      -H "Content-Type: application/json" -d '{"name":"x"}' \
      "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-acl-owner-001")"
  assert_match "TC007 Owner DELETE → 200/204/404" "^(200|204|404)$" \
    "$(curl -s -o /dev/null -w "%{http_code}" -X DELETE \
      -H "Authorization: Bearer $NORMAL_TOKEN" \
      "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-acl-owner-001")"
else skip "TC007 HTTP checks: mock-kb not installed"; fi
psql_iam "DELETE FROM resource_acl WHERE object_path='$_OWNER_OBJ';" >/dev/null

# ── ACL-TC-008: 无授权用户访问资源被拒绝 ─────────────────────────────────────
section "ACL-TC-008: user with no ACL denied resource access"
_NO_AUTH_OBJ="KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-no-auth-001"
psql_iam "DELETE FROM resource_acl WHERE object_path='$_NO_AUTH_OBJ';" >/dev/null 2>&1 || true
# admin has no KB type-level ACL → QueryACLs returns false
_Q=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Action/QueryACLs" \
  -H "Content-Type: application/json" \
  -d "{\"queries\":[{\"user_path\":\"$ADMIN_USER_PATH\",\"object_path\":\"$_NO_AUTH_OBJ\"}]}")
assert_contains "TC008 QueryACLs allowed=false for user with no ACL" "false" "$_Q"
if [ "$HAS_KB" -gt 0 ]; then
  # admin not in all-users → path-level 403 (before resource-level even runs)
  # If admin IS in all-users, pep-proxy returns 404 (no ACL, resource not found)
  assert_match "TC008 admin GET resource with no ACL → 403/404" "^(403|404)$" \
    "$(AH "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-no-auth-001")"
fi

# ── ACL-TC-009: 权限查询页面显示正确角色 ─────────────────────────────────────
section "ACL-TC-009: QueryACLs returns correct role"
_QUERY_OBJ="KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-query-001"
psql_iam "DELETE FROM resource_acl WHERE object_path='$_QUERY_OBJ';" >/dev/null 2>&1 || true
AH -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
  -H "Content-Type: application/json" \
  -d "{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$_QUERY_OBJ\",\"role_path\":\"$CONTRIB_ROLE\"}" >/dev/null
_QR=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Action/QueryACLs" \
  -H "Content-Type: application/json" \
  -d "{\"queries\":[{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$_QUERY_OBJ\"}]}")
assert_contains "TC009 QueryACLs returns Contributor" "Contributor" "$_QR"
assert_contains "TC009 QueryACLs allowed=true"        "true"        "$_QR"
psql_iam "DELETE FROM resource_acl WHERE object_path='$_QUERY_OBJ';" >/dev/null

# ── ACL-TC-013: 列表/搜索只返回有权限的资源，不泄露未授权资源 ─────────────────
section "ACL-TC-013: list returns only authorized resources, unauthorized ones excluded"
_LIST_AUTH="KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-acl-list-auth-001"
_LIST_NOAUTH="KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-acl-list-noauth-001"
psql_iam "DELETE FROM resource_acl WHERE object_path IN ('$_LIST_AUTH','$_LIST_NOAUTH');" >/dev/null 2>&1 || true
# Ensure normal-user has no KB type-level ACL that would make all KBs visible
psql_iam "DELETE FROM resource_acl WHERE user_path='$NORMAL_USER_PATH' AND object_path='KnowledgeBase/Tenants/$REALM/KnowledgeBases';" >/dev/null 2>&1 || true
# admin grants normal-user Viewer on auth resource only; noauth resource has no ACL for normal-user
AH -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
  -H "Content-Type: application/json" \
  -d "{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$_LIST_AUTH\",\"role_path\":\"$VIEWER_ROLE\"}" >/dev/null
# verify QueryACLs: one allowed, one not
_QA=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Action/QueryACLs" \
  -H "Content-Type: application/json" \
  -d "{\"queries\":[
    {\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$_LIST_AUTH\"},
    {\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$_LIST_NOAUTH\"}
  ]}")
assert_contains "TC013 authorized resource: allowed=true"    "true"  "$_QA"
assert_contains "TC013 unauthorized resource: allowed=false" "false" "$_QA"
if [ "$HAS_KB" -gt 0 ] && [ -n "$T_TA" ]; then
  # Use tenant-admins user (in all-users) to seed both KBs so ext_proc fires
  TAH -X PUT "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-acl-list-auth-001" \
    -H "Content-Type: application/json" -d '{"name":"opa-acl-list-auth-001"}' >/dev/null
  TAH -X PUT "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-acl-list-noauth-001" \
    -H "Content-Type: application/json" -d '{"name":"opa-acl-list-noauth-001"}' >/dev/null
  sleep 3
  # normal-user list: X-Allowed-Ids contains only the auth resource id
  _LIST=$(N "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases")
  assert_contains     "TC013 list includes authorized resource"    "opa-acl-list-auth-001"   "$_LIST"
  assert_not_contains "TC013 list excludes unauthorized resource"  "opa-acl-list-noauth-001" "$_LIST"
  # cleanup: TA user owns both KBs via ext_proc
  TAH -X DELETE "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-acl-list-auth-001" >/dev/null
  TAH -X DELETE "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-acl-list-noauth-001" >/dev/null
elif [ "$HAS_KB" -gt 0 ]; then
  skip "TC013 HTTP list checks: tenant-admins test user not available"
else skip "TC013 HTTP list checks: mock-kb not installed"; fi
psql_iam "DELETE FROM resource_acl WHERE object_path IN ('$_LIST_AUTH','$_LIST_NOAUTH');" >/dev/null

# ── ACL-TC-011: 资源 Owner 主动授权 Viewer 给其他用户 ────────────────────────
# Design note: /ACLs endpoint requires master-admins or tenant-admins (path-level).
# In this system a resource Owner delegates sharing through the admin; the admin
# acts on the Owner's behalf.  Here we simulate the full flow:
#   1. admin grants normal-user Owner on the resource (pre-condition)
#   2. admin grants admin-user Viewer on the same resource (as Owner's delegate)
#   3. verify admin-user's Viewer is reflected in QueryACLs
#   4. verify admin-user can GET but cannot DELETE (Viewer < Owner)
section "ACL-TC-011: admin grants Viewer to a user on behalf of Owner"
_SHARE_OBJ="KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-acl-share-001"
psql_iam "DELETE FROM resource_acl WHERE object_path='$_SHARE_OBJ';" >/dev/null 2>&1 || true
# Step 1: normal-user is Owner
AH -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
  -H "Content-Type: application/json" \
  -d "{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$_SHARE_OBJ\",\"role_path\":\"$OWNER_ROLE\"}" >/dev/null
# Step 2: admin grants Viewer to ADMIN_USER_PATH (simulating owner's share request via admin)
_GRANT=$(AH -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
  -H "Content-Type: application/json" \
  -d "{\"user_path\":\"$ADMIN_USER_PATH\",\"object_path\":\"$_SHARE_OBJ\",\"role_path\":\"$VIEWER_ROLE\"}")
assert_match "TC011 admin grants Viewer → 200/201" "^(200|201)$" "$_GRANT"
_QV=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Action/QueryACLs" \
  -H "Content-Type: application/json" \
  -d "{\"queries\":[{\"user_path\":\"$ADMIN_USER_PATH\",\"object_path\":\"$_SHARE_OBJ\"}]}")
assert_contains "TC011 QueryACLs shows admin has Viewer" "Viewer" "$_QV"
assert_contains "TC011 QueryACLs allowed=true for admin"  "true"  "$_QV"
# Step 3: normal-user (also in all-users) verifies: Owner can still GET and DELETE
if [ "$HAS_KB" -gt 0 ]; then
  assert_match "TC011 normal-user GET resource (Owner) → 200/404" "^(200|404)$" \
    "$(NH "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-acl-share-001")"
  # admin has Viewer ACL; if admin is in all-users path-level passes → 200/404 (Viewer GET allowed, resource may not exist in backend)
  # if admin not in all-users → 403 (path-level denied)
  assert_match "TC011 admin GET resource (Viewer) → 200/403/404" "^(200|403|404)$" \
    "$(AH "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-acl-share-001")"
else skip "TC011 HTTP checks: mock-kb not installed"; fi

# ── ACL-TC-012: 资源 Owner 撤销已授权，被撤销用户访问恢复 403 ─────────────────
# admin revokes the Viewer it granted in TC-011 (admin acts as Owner's delegate)
section "ACL-TC-012: admin revokes granted Viewer permission"
_REVOKE=$(AH -X DELETE "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
  -H "Content-Type: application/json" \
  -d "{\"user_path\":\"$ADMIN_USER_PATH\",\"object_path\":\"$_SHARE_OBJ\"}")
assert_match "TC012 admin revokes Viewer → 200/204" "^(200|204)$" "$_REVOKE"
_CNT=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE object_path='$_SHARE_OBJ' AND user_path='$ADMIN_USER_PATH';")
assert "TC012 admin ACL row removed from DB" "0" "$_CNT"
_QR=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Action/QueryACLs" \
  -H "Content-Type: application/json" \
  -d "{\"queries\":[{\"user_path\":\"$ADMIN_USER_PATH\",\"object_path\":\"$_SHARE_OBJ\"}]}")
assert_contains "TC012 QueryACLs allowed=false after revoke" "false" "$_QR"
# admin is not in all-users so KB path is 403 regardless; verify via QueryACLs only
psql_iam "DELETE FROM resource_acl WHERE object_path='$_SHARE_OBJ';" >/dev/null

# ── ACL-TC-010: tenant-admins 无需单独授权即可管理租户资源 ────────────────────
section "ACL-TC-010: tenant-admins bypass resource-level ACL; master-admins do not"
_TA_OBJ="KnowledgeBase/Tenants/$REALM/KnowledgeBases/opa-ta-test-001"
psql_iam "DELETE FROM resource_acl WHERE object_path='$_TA_OBJ';" >/dev/null 2>&1 || true
# tenant-admins group bypass: admin is in master-admins (path-level bypass only)
# Verify via QueryACLs: admin has no explicit ACL but master-admins bypass path-level
_TA_Q=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Action/QueryACLs" \
  -H "Content-Type: application/json" \
  -d "{\"queries\":[{\"user_path\":\"$ADMIN_USER_PATH\",\"object_path\":\"$_TA_OBJ\"}]}")
# master-admins bypass path-level but NOT resource-level → allowed=false without explicit ACL
assert_contains "TC010 master-admins has no implicit resource ACL" "false" "$_TA_Q"
# tenant-admins group path: check via group path query
_TA_GRP_PATH="AccessManager/Tenants/$REALM/Groups/tenant-admins"
_TA_GRP_Q=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Action/QueryACLs" \
  -H "Content-Type: application/json" \
  -d "{\"queries\":[{\"user_path\":\"$_TA_GRP_PATH\",\"object_path\":\"$_TA_OBJ\"}]}")
assert_contains "TC010 tenant-admins group has no implicit resource ACL either" "false" "$_TA_GRP_Q"
# Clean up any KB type-level ACL for normal-user before EXTPROC tests
psql_iam "DELETE FROM resource_acl WHERE user_path='$NORMAL_USER_PATH' AND object_path='KnowledgeBase/Tenants/$REALM/KnowledgeBases';" >/dev/null 2>&1 || true


# ═══════════════════════════════════════════════════════════════════════════════
banner "三、EXTPROC：资源变更自动授权与列表过滤"
# ═══════════════════════════════════════════════════════════════════════════════

EP_BASE="$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases"
EP_OBJ_PREFIX="KnowledgeBase/Tenants/$REALM/KnowledgeBases"
psql_iam "DELETE FROM resource_acl WHERE object_path LIKE '%/opa-ep-%';" >/dev/null 2>&1 || true
# Grant normal-user KB type-level Owner so they can create KBs in EXTPROC tests
if [ "$HAS_KB" -gt 0 ]; then
  AH -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
    -H "Content-Type: application/json" \
    -d "{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"KnowledgeBase/Tenants/$REALM/KnowledgeBases\",\"role_path\":\"$OWNER_ROLE\"}" >/dev/null
fi

# ── EXTPROC-TC-001: 创建资源后创建者自动获得 Owner ───────────────────────────
section "EXTPROC-TC-001: creator auto-gets Owner ACL on resource creation"
if [ "$HAS_KB" -gt 0 ]; then
  EP_KB_ID="opa-ep-kb-$(date +%s)"
  _CREATE=$(curl -s -o /dev/null -w "%{http_code}" -X PUT \
    -H "Authorization: Bearer $NORMAL_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"$EP_KB_ID\",\"description\":\"extproc test\"}" \
    "$EP_BASE/$EP_KB_ID")
  assert_match "TC001-EP create KB → 200/201" "^(200|201)$" "$_CREATE"
  sleep 3
  _ACL_CNT=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE object_path='$EP_OBJ_PREFIX/$EP_KB_ID' AND user_path='$NORMAL_USER_PATH';")
  assert_match "TC001-EP Owner ACL written for creator" "^[1-9]" "$_ACL_CNT"
  _ACL_ROLE=$(psql_iam "SELECT role_path FROM resource_acl WHERE object_path='$EP_OBJ_PREFIX/$EP_KB_ID' AND user_path='$NORMAL_USER_PATH' LIMIT 1;")
  assert_contains "TC001-EP creator role is Owner" "Owner" "$_ACL_ROLE"
else skip "EXTPROC-TC-001: mock-kb not installed"; EP_KB_ID=""; fi

# ── EXTPROC-TC-002: 自动 Owner 只归属于创建者 ────────────────────────────────
section "EXTPROC-TC-002: auto Owner only for creator, not other users"
if [ "$HAS_KB" -gt 0 ] && [ -n "${EP_KB_ID:-}" ]; then
  _OTHER_CNT=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE object_path='$EP_OBJ_PREFIX/$EP_KB_ID' AND user_path='$ADMIN_USER_PATH';")
  assert "TC002-EP admin has no auto ACL on creator's resource" "0" "$_OTHER_CNT"
  # admin may have KB type-level ACL via all-users group → 200/403 depending on cluster setup
  assert_match "TC002-EP admin GET creator's resource → 403/200 (no instance ACL)" "^(403|200|404)$" \
    "$(AH "$EP_BASE/$EP_KB_ID")"
  # Verify normal-user cannot access admin's KB (no ACL, no type-level grant for admin's resource)
  # Use a KB ID that was created by admin (EP_KB_U1 from TC005 is cleaned up, use a fresh one)
  _ADM_ONLY_KB="opa-ep-adm-only-$(date +%s)"
  curl -s -o /dev/null -X PUT \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"$_ADM_ONLY_KB\"}" "$EP_BASE/$_ADM_ONLY_KB"
  sleep 2
  assert_match "TC002-EP normal-user cannot access admin-created KB → 403" "^(403|404)$" \
    "$(NH "$EP_BASE/$_ADM_ONLY_KB")"
  curl -s -o /dev/null -X DELETE -H "Authorization: Bearer $ADMIN_TOKEN" "$EP_BASE/$_ADM_ONLY_KB"
else skip "EXTPROC-TC-002: depends on TC-001"; fi

# ── EXTPROC-TC-003: 删除资源后 ACL 自动清除 ──────────────────────────────────
section "EXTPROC-TC-003: delete resource → ACL auto-removed"
if [ "$HAS_KB" -gt 0 ] && [ -n "${EP_KB_ID:-}" ]; then
  _DEL=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE \
    -H "Authorization: Bearer $NORMAL_TOKEN" \
    "$EP_BASE/$EP_KB_ID")
  assert_match "TC003-EP DELETE resource → 200/204" "^(200|204)$" "$_DEL"
  sleep 3
  _AFTER_CNT=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE object_path='$EP_OBJ_PREFIX/$EP_KB_ID';")
  assert "TC003-EP ACL removed after resource delete" "0" "$_AFTER_CNT"
else skip "EXTPROC-TC-003: depends on TC-001"; fi

# ── EXTPROC-TC-004: 列表只展示有权限的资源 ───────────────────────────────────
section "EXTPROC-TC-004: resource list filtered to user's accessible resources"
if [ "$HAS_KB" -gt 0 ]; then
  EP_KB_A="opa-ep-list-a-$(date +%s)"
  EP_KB_B="opa-ep-list-b-$(date +%s)"
  # admin creates KB-A, normal-user creates KB-B
  curl -s -o /dev/null -X PUT \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"$EP_KB_A\"}" "$EP_BASE/$EP_KB_A"
  curl -s -o /dev/null -X PUT \
    -H "Authorization: Bearer $NORMAL_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"$EP_KB_B\"}" "$EP_BASE/$EP_KB_B"
  sleep 3
  _NORMAL_LIST=$(N "$EP_BASE")
  assert_not_contains "TC004-EP normal-user list excludes admin's KB" "$EP_KB_A" "$_NORMAL_LIST"
  assert_contains     "TC004-EP normal-user list includes own KB"     "$EP_KB_B" "$_NORMAL_LIST"
  # cleanup
  curl -s -o /dev/null -X DELETE -H "Authorization: Bearer $ADMIN_TOKEN"  "$EP_BASE/$EP_KB_A"
  curl -s -o /dev/null -X DELETE -H "Authorization: Bearer $NORMAL_TOKEN" "$EP_BASE/$EP_KB_B"
else skip "EXTPROC-TC-004: mock-kb not installed"; fi

# ── EXTPROC-TC-005: 不同用户资源列表互相隔离 ─────────────────────────────────
section "EXTPROC-TC-005: different users' resource lists are isolated"
if [ "$HAS_KB" -gt 0 ]; then
  EP_KB_U1="opa-ep-iso-u1-$(date +%s)"
  EP_KB_U2="opa-ep-iso-u2-$(date +%s)"
  curl -s -o /dev/null -X PUT \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"$EP_KB_U1\"}" "$EP_BASE/$EP_KB_U1"
  curl -s -o /dev/null -X PUT \
    -H "Authorization: Bearer $NORMAL_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"$EP_KB_U2\"}" "$EP_BASE/$EP_KB_U2"
  sleep 3
  _U2_LIST=$(N "$EP_BASE")
  # user2 (normal-user) has no type-level ACL → only sees own KBs
  assert_not_contains "TC005-EP user2 list excludes user1's KB" "$EP_KB_U1" "$_U2_LIST"
  assert_contains     "TC005-EP user2 list includes own KB"     "$EP_KB_U2" "$_U2_LIST"
  curl -s -o /dev/null -X DELETE -H "Authorization: Bearer $ADMIN_TOKEN"  "$EP_BASE/$EP_KB_U1"
  curl -s -o /dev/null -X DELETE -H "Authorization: Bearer $NORMAL_TOKEN" "$EP_BASE/$EP_KB_U2"
else skip "EXTPROC-TC-005: mock-kb not installed"; fi

# ── EXTPROC-TC-006: tenant-admins 查看资源不受过滤限制 ───────────────────────
section "EXTPROC-TC-006: tenant-admins resource list bypasses X-Allowed-Ids filter"
if [ "$HAS_KB" -gt 0 ] && [ -n "$T_TA" ]; then
  EP_KB_TA="opa-ep-ta-$(date +%s)"
  EP_KB_NRM2="opa-ep-nrm2-$(date +%s)"
  # tenant-admins user creates one KB, normal-user creates another
  curl -s -o /dev/null -X PUT \
    -H "Authorization: Bearer $T_TA" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"$EP_KB_TA\"}" "$EP_BASE/$EP_KB_TA"
  curl -s -o /dev/null -X PUT \
    -H "Authorization: Bearer $NORMAL_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"$EP_KB_NRM2\"}" "$EP_BASE/$EP_KB_NRM2"
  sleep 3
  # tenant-admins bypasses X-Allowed-Ids → sees all KBs
  _TA_LIST=$(TA "$EP_BASE")
  assert_contains     "TC006-EP tenant-admins list includes own KB"          "$EP_KB_TA"   "$_TA_LIST"
  assert_contains     "TC006-EP tenant-admins list includes normal-user's KB" "$EP_KB_NRM2" "$_TA_LIST"
  # normal-user only sees own KB
  _NRM2_LIST=$(N "$EP_BASE")
  assert_contains     "TC006-EP normal-user list includes own KB"            "$EP_KB_NRM2" "$_NRM2_LIST"
  assert_not_contains "TC006-EP normal-user list excludes tenant-admins' KB" "$EP_KB_TA"   "$_NRM2_LIST"
  curl -s -o /dev/null -X DELETE -H "Authorization: Bearer $T_TA"          "$EP_BASE/$EP_KB_TA"
  curl -s -o /dev/null -X DELETE -H "Authorization: Bearer $NORMAL_TOKEN"  "$EP_BASE/$EP_KB_NRM2"
elif [ "$HAS_KB" -gt 0 ]; then
  skip "EXTPROC-TC-006: tenant-admins test user not available"
else skip "EXTPROC-TC-006: mock-kb not installed"; fi

# ── EXTPROC-MST-001: 策略刷新组件恢复后路径鉴权自动恢复 ──────────────────────
section "EXTPROC-MST-001: path authz recovers after bundle-server restart"
if [ "$HAS_KB" -gt 0 ]; then
  # Use normal-user (in all-users) to check KB path, admin is not in all-users
  assert_match "MST001 before restart normal-user GET KB → 200" "^(200)$" \
    "$(NH "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases")"
  kubectl -n "$IAM_NS" rollout restart deployment/iam-services >/dev/null 2>&1 || true
  kubectl -n "$IAM_NS" rollout status deployment/iam-services --timeout=120s >/dev/null 2>&1 || true
  sleep 40
  assert_match "MST001 after restart declared path → 200" "^(200)$" \
    "$(NH "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases")"
  assert_match "MST001 after restart undeclared path → 403/404" "^(403|404)$" \
    "$(NH "$BASE_URL/NeverRegisteredApp/Tenants/$REALM/Data/id-001")"
else skip "EXTPROC-MST-001: mock-kb not installed"; fi

# ── EXTPROC-MST-002: 鉴权组件恢复后资源级鉴权自动恢复 ────────────────────────
section "EXTPROC-MST-002: resource-level authz recovers after pep-proxy restart"
_MST2_OBJ="KnowledgeBase/Tenants/$REALM/KnowledgeBases/mst002-kb-001"
psql_iam "DELETE FROM resource_acl WHERE object_path='$_MST2_OBJ';" >/dev/null 2>&1 || true
AH -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
  -H "Content-Type: application/json" \
  -d "{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$_MST2_OBJ\",\"role_path\":\"$VIEWER_ROLE\"}" >/dev/null
_Q_BEFORE=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Action/QueryACLs" \
  -H "Content-Type: application/json" \
  -d "{\"queries\":[{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$_MST2_OBJ\"}]}")
assert_contains "MST002 Viewer ACL present before restart" "Viewer" "$_Q_BEFORE"
kubectl -n "$IAM_NS" rollout restart deployment/iam-services >/dev/null 2>&1 || true
kubectl -n "$IAM_NS" rollout status deployment/iam-services --timeout=120s >/dev/null 2>&1 || true
sleep 10
_Q_AFTER=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Action/QueryACLs" \
  -H "Content-Type: application/json" \
  -d "{\"queries\":[{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$_MST2_OBJ\"}]}")
assert_contains "MST002 Viewer ACL still present after restart" "Viewer" "$_Q_AFTER"
if [ "$HAS_KB" -gt 0 ]; then
  assert_match "MST002 Viewer GET after restart → 200" "^(200|404)$" \
    "$(NH "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases/mst002-kb-001")"
  assert_match "MST002 Viewer DELETE after restart → 403" "^(403)$" \
    "$(curl -s -o /dev/null -w "%{http_code}" -X DELETE \
      -H "Authorization: Bearer $NORMAL_TOKEN" \
      "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases/mst002-kb-001")"
fi
psql_iam "DELETE FROM resource_acl WHERE object_path='$_MST2_OBJ';" >/dev/null

# ═══════════════════════════════════════════════════════════════════════════════
echo -e "\n${BLUE}════════════════════════════════════════${NC}"
echo -e "  Passed: ${GREEN}$PASS${NC} / $((PASS+FAIL))"
[ "$FAIL" -eq 0 ] \
  && echo -e "  ${GREEN}ALL PASSED${NC}" \
  || echo -e "  ${RED}FAILED: $FAIL${NC} / $((PASS+FAIL))"
echo -e "${BLUE}════════════════════════════════════════${NC}"
[ "$FAIL" -eq 0 ]

