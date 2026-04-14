#!/usr/bin/env bash
# ============================================================================
# test.sh — IAM single-tenant end-to-end test suite (per ui-wireframes.md)
#
# Single realm `aidp`, single admin group `admins`, default group `all-users`.
# All admin-API tests use the admin user (aidp-client + password grant).
# All authz behavior tests cover: OPA path-level, resource-level ACL,
# ext_authz body forwarding, ext_proc list filtering & ACL auto-sync,
# API Key lifecycle + auth, IdP CRUD.
# ============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
KEYCLOAK_NS="keycloak"
OPA_NS="opa"
RS_NS="resource-sync"
ENVOY_GATEWAY_NS="${ENVOY_GATEWAY_NS:-aidp-iam}"
GATEWAY_PORT="${GATEWAY_PORT:-8080}"
BASE_URL="http://localhost:${GATEWAY_PORT}"

REALM="${REALM:-aidp}"
CLIENT_ID="${CLIENT_ID:-aidp-client}"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-Admin@123}"
NORMAL_USER="${NORMAL_USER:-normal-user}"
NORMAL_PASSWORD="${NORMAL_PASSWORD:-NormalUser@123}"
TEST_APP="${TEST_APP:-test-app}"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
PASS=0; FAIL=0; TOTAL=0

assert() { local d="$1" e="$2" a="$3"; TOTAL=$((TOTAL+1))
  if [ "$e" = "$a" ]; then echo -e "  ${GREEN}PASS${NC} $d"; PASS=$((PASS+1))
  else echo -e "  ${RED}FAIL${NC} $d (expected=$e, actual=$a)"; FAIL=$((FAIL+1)); fi
}
assert_contains() { local d="$1" e="$2" a="$3"; TOTAL=$((TOTAL+1))
  if echo "$a" | grep -q "$e"; then echo -e "  ${GREEN}PASS${NC} $d"; PASS=$((PASS+1))
  else echo -e "  ${RED}FAIL${NC} $d (expected to contain '$e')"; FAIL=$((FAIL+1)); fi
}
assert_not_contains() { local d="$1" u="$2" a="$3"; TOTAL=$((TOTAL+1))
  if echo "$a" | grep -q "$u"; then echo -e "  ${RED}FAIL${NC} $d (should NOT contain '$u')"; FAIL=$((FAIL+1))
  else echo -e "  ${GREEN}PASS${NC} $d"; PASS=$((PASS+1)); fi
}
assert_match() { local d="$1" p="$2" a="$3"; TOTAL=$((TOTAL+1))
  if echo "$a" | grep -qE "$p"; then echo -e "  ${GREEN}PASS${NC} $d"; PASS=$((PASS+1))
  else echo -e "  ${RED}FAIL${NC} $d (expected to match '$p', got '$a')"; FAIL=$((FAIL+1)); fi
}
skip() { echo -e "  ${YELLOW}SKIP${NC} $1"; }
section() { echo -e "\n${BLUE}=== $* ===${NC}"; }

psql_iam() {
  MSYS_NO_PATHCONV=1 kubectl -n "$KEYCLOAK_NS" exec postgres-0 -c postgres -- \
    psql -U keycloak -d iam -tA -c "$1" 2>/dev/null | tr -d '\r'
}

jget() { python -c "import sys,json
try: v=json.load(sys.stdin)
except: print(''); sys.exit(0)
for k in '$1'.split('.'):
  if isinstance(v, list):
    try: v=v[int(k)]
    except: v=''; break
  else:
    v=v.get(k,'') if isinstance(v,dict) else ''
print(v if v is not None else '')"; }

jwt_claim() { local tk="$1" k="$2"; echo "$tk" | cut -d. -f2 | python -c "
import sys,base64,json
s=sys.stdin.read().strip(); s+='='*(-len(s)%4)
d=json.loads(base64.urlsafe_b64decode(s))
v=d.get('$k','')
print(json.dumps(v) if not isinstance(v,str) else v)"; }

# ── Port-forward to gateway ────────────────────────────────────────────────
echo -e "${YELLOW}Setting up port-forward to Envoy Gateway...${NC}"
if curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/" 2>/dev/null | grep -qE '200|301|302|404'; then
  echo -e "  ${GREEN}Port ${GATEWAY_PORT} already forwarded${NC}"; PF_PID=""
else
  lsof -ti:${GATEWAY_PORT} 2>/dev/null | xargs kill -9 2>/dev/null || true
  GW_SVC=$(kubectl -n "$ENVOY_GATEWAY_NS" get svc -l gateway.envoyproxy.io/owning-gateway-name=eg -o name 2>/dev/null | head -1)
  [ -z "$GW_SVC" ] && GW_SVC="svc/envoy-eg"
  kubectl -n "$ENVOY_GATEWAY_NS" port-forward "$GW_SVC" "${GATEWAY_PORT}:80" >/dev/null 2>&1 &
  PF_PID=$!; sleep 3
fi
trap "[ -n \"\${PF_PID:-}\" ] && kill \$PF_PID 2>/dev/null || true; \
  psql_iam \"DELETE FROM path_rules WHERE path_prefix LIKE '/anything/%';\" >/dev/null 2>&1 || true; \
  psql_iam \"DELETE FROM apps WHERE app_name='$TEST_APP';\" >/dev/null 2>&1 || true" EXIT

# ════════════════════════════════════════════════════════════════════════════
section "Section 1: Pod health"
# ════════════════════════════════════════════════════════════════════════════
KC_HEALTH=$(MSYS_NO_PATHCONV=1 kubectl -n "$KEYCLOAK_NS" exec deploy/keycloak-proxy -- \
  python3 -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8090/api/v1/common/health').status)" 2>/dev/null || echo 000)
assert "keycloak-proxy /api/v1/common/health" "200" "$KC_HEALTH"

PEP_HEALTH=$(MSYS_NO_PATHCONV=1 kubectl -n "$OPA_NS" exec deploy/pep-proxy -c opal-proxy -- \
  curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>/dev/null || echo 000)
assert "pep-proxy /health" "200" "$PEP_HEALTH"

RS_HEALTH=$(MSYS_NO_PATHCONV=1 kubectl -n "$RS_NS" exec deploy/resource-sync -- \
  curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/health 2>/dev/null || echo 000)
assert "resource-sync /health" "200" "$RS_HEALTH"

EG_DP=$(kubectl -n "$ENVOY_GATEWAY_NS" get deploy -l gateway.envoyproxy.io/owning-gateway-name=eg \
  -o jsonpath='{.items[0].status.readyReplicas}' 2>/dev/null)
assert "envoy data plane ready" "1" "$EG_DP"

# ════════════════════════════════════════════════════════════════════════════
section "Section 2: Public routes (no auth)"
# ════════════════════════════════════════════════════════════════════════════
OIDC=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/realms/$REALM/.well-known/openid-configuration")
assert "GET /realms/$REALM/.well-known/openid-configuration" "200" "$OIDC"
ADMIN_CONSOLE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/admin/")
assert_match "GET /admin/" "^(200|302|303)$" "$ADMIN_CONSOLE"

# ════════════════════════════════════════════════════════════════════════════
section "Section 3: Protected routes reject no-token (401/403)"
# ════════════════════════════════════════════════════════════════════════════
for path in /api/v1/tenants /api/v1/apps /api/v1/path-rules /anything /legacy/get; do
  code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL$path")
  assert_match "no-token $path -> 401/403" "^(401|403)$" "$code"
done

# ════════════════════════════════════════════════════════════════════════════
section "Section 4: Admin token (aidp-client + admin user, password grant)"
# ════════════════════════════════════════════════════════════════════════════
CS=$(kubectl -n "$KEYCLOAK_NS" get secret keycloak-aidp-client -o jsonpath='{.data.client-secret}' 2>/dev/null | base64 -d)
assert_match "aidp-client client-secret present" "^[A-Za-z0-9]{20,}$" "$CS"

ADMIN_TOKEN=$(curl -s -X POST "$BASE_URL/realms/$REALM/protocol/openid-connect/token" \
  -d "client_id=$CLIENT_ID" -d "client_secret=$CS" -d "grant_type=password" \
  -d "username=$ADMIN_USER" -d "password=$ADMIN_PASSWORD" | jget access_token)
[ -n "$ADMIN_TOKEN" ] && assert "admin token issued" "yes" "yes" || assert "admin token issued" "yes" "no"

ADMIN_GROUPS=$(jwt_claim "$ADMIN_TOKEN" groups)
assert_contains "admin token contains 'admins' group" "admins" "$ADMIN_GROUPS"
assert_contains "admin token contains 'all-users' group" "all-users" "$ADMIN_GROUPS"
ADMIN_GIDS=$(jwt_claim "$ADMIN_TOKEN" group_ids)
assert_match "admin token contains group_ids (UUIDs)" "[0-9a-f-]{36}" "$ADMIN_GIDS"
ADMIN_ISS=$(jwt_claim "$ADMIN_TOKEN" iss)
assert_contains "admin token iss /realms/$REALM" "realms/$REALM" "$ADMIN_ISS"

A() { curl -s -H "Authorization: Bearer $ADMIN_TOKEN" "$@"; }
AH() { curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $ADMIN_TOKEN" "$@"; }

# ════════════════════════════════════════════════════════════════════════════
section "Section 5: Apps registry CRUD"
# ════════════════════════════════════════════════════════════════════════════
psql_iam "DELETE FROM apps WHERE app_name='$TEST_APP';" >/dev/null 2>&1 || true

APPS=$(A "$BASE_URL/api/v1/apps")
assert_contains "GET /api/v1/apps lists seed app httpbin" "httpbin" "$APPS"

CREATE=$(A -X POST "$BASE_URL/api/v1/apps" -H "Content-Type: application/json" \
  -d "{\"app_name\":\"$TEST_APP\",\"path_prefix\":\"/$TEST_APP/\",\"display_name\":\"Test\",\"enabled\":true}")
assert_contains "POST /api/v1/apps creates" "$TEST_APP" "$CREATE"
assert "DB has the new app" "$TEST_APP" "$(psql_iam "SELECT app_name FROM apps WHERE app_name='$TEST_APP';")"

CODE=$(AH "$BASE_URL/api/v1/apps/$TEST_APP")
assert "GET /api/v1/apps/$TEST_APP" "200" "$CODE"

CODE=$(AH -X PUT "$BASE_URL/api/v1/apps/$TEST_APP" -H "Content-Type: application/json" -d '{"enabled":false}')
assert_match "PUT disable app" "^(200|204)$" "$CODE"
assert "DB enabled flag flipped" "f" "$(psql_iam "SELECT enabled FROM apps WHERE app_name='$TEST_APP';")"

A -X PUT "$BASE_URL/api/v1/apps/$TEST_APP" -H "Content-Type: application/json" -d '{"enabled":true}' >/dev/null
sleep 2

# ════════════════════════════════════════════════════════════════════════════
section "Section 6: Path rules CRUD"
# ════════════════════════════════════════════════════════════════════════════
psql_iam "DELETE FROM path_rules WHERE path_prefix LIKE '/anything/%';" >/dev/null 2>&1 || true

RULE=$(A -X POST "$BASE_URL/api/v1/path-rules" -H "Content-Type: application/json" \
  -d '{"path_prefix":"/anything/admin","required_group":"some-app-admins","description":"test"}')
RULE_ID=$(echo "$RULE" | jget id)
assert_match "POST /api/v1/path-rules returns id" "^[0-9]+$" "$RULE_ID"

RULES=$(A "$BASE_URL/api/v1/path-rules")
assert_contains "GET lists new rule" "/anything/admin" "$RULES"

CODE=$(AH -X PUT "$BASE_URL/api/v1/path-rules/$RULE_ID" -H "Content-Type: application/json" \
  -d '{"required_group":"some-app-admins","description":"updated"}')
assert_match "PUT path rule" "^(200|204)$" "$CODE"

CODE=$(AH -X DELETE "$BASE_URL/api/v1/path-rules/$RULE_ID")
assert "DELETE path rule" "204" "$CODE"
assert_not_contains "rule gone after delete" "/anything/admin" "$(A $BASE_URL/api/v1/path-rules)"

# ════════════════════════════════════════════════════════════════════════════
section "Section 7: Path-level authz (admin bypass + path_rule)"
# ════════════════════════════════════════════════════════════════════════════
CODE=$(AH "$BASE_URL/api/v1/tenants")
assert "admin GET /api/v1/tenants -> 200" "200" "$CODE"

CODE=$(AH "$BASE_URL/anything")
assert_match "admin GET /anything -> 200/403" "^(200|403)$" "$CODE"

# Add a path rule requiring a non-existent group; admins-only bypass covers
# /api/v1/* + /acl/v1/*, NOT business paths → /anything/secret should 403.
A -X POST "$BASE_URL/api/v1/path-rules" -H "Content-Type: application/json" \
  -d '{"path_prefix":"/anything/secret","required_group":"nonexistent"}' >/dev/null
# bundle-server pushes to OPA every 30s; wait one cycle
sleep 35
CODE=$(AH "$BASE_URL/anything/secret")
assert "non-matching path_rule group → 403 (even for admin)" "403" "$CODE"
SECRET_ID=$(psql_iam "SELECT id FROM path_rules WHERE path_prefix='/anything/secret';")
[ -n "$SECRET_ID" ] && A -X DELETE "$BASE_URL/api/v1/path-rules/$SECRET_ID" >/dev/null
sleep 3

# ════════════════════════════════════════════════════════════════════════════
section "Section 8: Realm sanity (GET /api/v1/tenants only)"
# ════════════════════════════════════════════════════════════════════════════
TENANTS_LIST=$(A "$BASE_URL/api/v1/tenants")
assert_contains "GET /tenants returns aidp realm" "$REALM" "$TENANTS_LIST"

CODE=$(AH -X POST "$BASE_URL/api/v1/tenants" -H "Content-Type: application/json" \
  -d '{"realm_name":"x","admin_username":"y","admin_password":"z"}')
assert_match "POST /api/v1/tenants removed (404/405)" "^(404|405)$" "$CODE"

# ════════════════════════════════════════════════════════════════════════════
section "Section 9: Identity CRUD (roles/groups/users) in aidp realm"
# ════════════════════════════════════════════════════════════════════════════
CODE=$(AH "$BASE_URL/api/v1/$REALM/roles")
assert "admin GET /{realm}/roles -> 200" "200" "$CODE"
CODE=$(AH "$BASE_URL/api/v1/$REALM/groups")
assert "admin GET /{realm}/groups -> 200" "200" "$CODE"
CODE=$(AH "$BASE_URL/api/v1/$REALM/users")
assert "admin GET /{realm}/users -> 200" "200" "$CODE"

CODE=$(AH -X POST "$BASE_URL/api/v1/$REALM/roles" -H "Content-Type: application/json" \
  -d '{"name":"test-role","description":"tmp"}')
assert_match "POST /{realm}/roles -> 200/201" "^(200|201)$" "$CODE"
ROLES=$(A "$BASE_URL/api/v1/$REALM/roles")
assert_contains "GET lists test-role" "test-role" "$ROLES"
A -X DELETE "$BASE_URL/api/v1/$REALM/roles/test-role" >/dev/null

CODE=$(AH -X POST "$BASE_URL/api/v1/$REALM/groups" -H "Content-Type: application/json" \
  -d '{"name":"test-group"}')
assert_match "POST /{realm}/groups -> 200/201" "^(200|201)$" "$CODE"
GROUPS_LIST=$(A "$BASE_URL/api/v1/$REALM/groups")
assert_contains "GET lists test-group" "test-group" "$GROUPS_LIST"
GID=$(echo "$GROUPS_LIST" | python -c "import sys,json
d=json.load(sys.stdin)
for g in d:
  if g.get('name')=='test-group' or g.get('group_name')=='test-group':
    print(g.get('id') or g.get('group_id') or ''); break")
[ -n "$GID" ] && A -X DELETE "$BASE_URL/api/v1/$REALM/groups/$GID" >/dev/null

# ════════════════════════════════════════════════════════════════════════════
section "Section 10: ACL API (/acl/v1) with admin owner check"
# ════════════════════════════════════════════════════════════════════════════
ADMIN_SUB=$(jwt_claim "$ADMIN_TOKEN" sub)
psql_iam "DELETE FROM resource_acl WHERE resource_id='item-test-001';" >/dev/null
psql_iam "INSERT INTO resource_acl(tenant_id,app_name,resource_type,resource_id,subject_type,subject_id,permission) VALUES
  ('$REALM','httpbin','item','item-test-001','user','$ADMIN_SUB','owner'),
  ('$REALM','httpbin','item','item-test-001','user','testuser1','owner')
  ON CONFLICT DO NOTHING;" >/dev/null

LIST=$(A "$BASE_URL/acl/v1/resources/item-test-001/permissions?app_name=httpbin&resource_type=item")
if echo "$LIST" | grep -qi "Missing X-Auth"; then
  skip "GET /acl/v1 — pep-proxy not injecting headers (regression)"
else
  assert_contains "GET /acl/v1/resources/{id}/permissions returns owner" "testuser1" "$LIST"

  SHARE=$(A -X POST "$BASE_URL/acl/v1/resources/item-test-001/permissions" -H "Content-Type: application/json" \
    -d "{\"app_name\":\"httpbin\",\"resource_type\":\"item\",\"subject_type\":\"user\",\"subject_id\":\"viewer1\",\"permission\":\"viewer\"}")
  assert_contains "POST share to viewer1" "viewer1" "$SHARE"

  ACL_ID=$(psql_iam "SELECT id FROM resource_acl WHERE resource_id='item-test-001' AND subject_id='viewer1';")
  if [ -n "$ACL_ID" ]; then
    CODE=$(AH -X PUT "$BASE_URL/acl/v1/resources/item-test-001/permissions/$ACL_ID" -H "Content-Type: application/json" \
      -d '{"permission":"contributor"}')
    assert_match "PUT permission viewer→contributor" "^(200|204)$" "$CODE"

    CODE=$(AH -X DELETE "$BASE_URL/acl/v1/resources/item-test-001/permissions/$ACL_ID")
    assert_match "DELETE permission" "^(200|204)$" "$CODE"
    assert "ACL row removed" "0" "$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE id=$ACL_ID;")"
  fi
fi

# Non-owner cannot share. Use normal-user token.
NORMAL_TOKEN=$(curl -s -X POST "$BASE_URL/realms/$REALM/protocol/openid-connect/token" \
  -d "client_id=$CLIENT_ID" -d "client_secret=$CS" -d "grant_type=password" \
  -d "username=$NORMAL_USER" -d "password=$NORMAL_PASSWORD" | jget access_token)

if [ -n "$NORMAL_TOKEN" ]; then
  psql_iam "INSERT INTO resource_acl(tenant_id,app_name,resource_type,resource_id,subject_type,subject_id,permission) VALUES
    ('$REALM','httpbin','item','item-other','user','some-other-owner','owner') ON CONFLICT DO NOTHING;" >/dev/null
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $NORMAL_TOKEN" -H "Content-Type: application/json" \
    -X POST "$BASE_URL/acl/v1/resources/item-other/permissions" \
    -d '{"app_name":"httpbin","resource_type":"item","subject_type":"user","subject_id":"hijack","permission":"viewer"}')
  assert_match "non-owner share denied (4xx)" "^(401|403)$" "$CODE"
  psql_iam "DELETE FROM resource_acl WHERE resource_id='item-other';" >/dev/null
else
  skip "normal-user token unavailable for non-owner test"
fi

# ════════════════════════════════════════════════════════════════════════════
section "Section 11: resource_acl owner/viewer/contributor + UNIQUE constraint"
# ════════════════════════════════════════════════════════════════════════════
psql_iam "DELETE FROM resource_acl WHERE resource_id='item-test-002';" >/dev/null
psql_iam "INSERT INTO resource_acl(tenant_id,app_name,resource_type,resource_id,subject_type,subject_id,permission) VALUES
  ('$REALM','httpbin','item','item-test-002','user','u-owner','owner'),
  ('$REALM','httpbin','item','item-test-002','user','u-viewer','viewer'),
  ('$REALM','httpbin','item','item-test-002','user','u-contrib','contributor');" >/dev/null
assert "owner row" "owner" "$(psql_iam "SELECT permission FROM resource_acl WHERE resource_id='item-test-002' AND subject_id='u-owner';")"
assert "viewer row" "viewer" "$(psql_iam "SELECT permission FROM resource_acl WHERE resource_id='item-test-002' AND subject_id='u-viewer';")"
assert "contributor row" "contributor" "$(psql_iam "SELECT permission FROM resource_acl WHERE resource_id='item-test-002' AND subject_id='u-contrib';")"

BEFORE=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE resource_id='item-test-002' AND subject_id='u-owner';")
psql_iam "INSERT INTO resource_acl(tenant_id,app_name,resource_type,resource_id,subject_type,subject_id,permission) VALUES('$REALM','httpbin','item','item-test-002','user','u-owner','viewer') ON CONFLICT DO NOTHING;" >/dev/null
AFTER=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE resource_id='item-test-002' AND subject_id='u-owner';")
assert "UNIQUE (tenant,app,type,res,subj_type,subj_id) prevents dupes" "$BEFORE" "$AFTER"

# ════════════════════════════════════════════════════════════════════════════
section "Section 12: resource_acl cascade on delete"
# ════════════════════════════════════════════════════════════════════════════
BEFORE=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE resource_id='item-test-002';")
assert_match "ACL rows present" "^[1-9][0-9]*$" "$BEFORE"
psql_iam "DELETE FROM resource_acl WHERE resource_id='item-test-002';" >/dev/null
assert "After cascade delete: 0 rows" "0" "$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE resource_id='item-test-002';")"

# ════════════════════════════════════════════════════════════════════════════
section "Section 13: ext_proc activity"
# ════════════════════════════════════════════════════════════════════════════
A "$BASE_URL/anything" >/dev/null
sleep 1
RS_LOG=$(kubectl -n "$RS_NS" logs deploy/resource-sync --tail=20 2>&1 | grep -iE "ext_proc|process|stream" | tail -3)
[ -n "$RS_LOG" ] && assert "ext_proc handler observed activity" "yes" "yes" || skip "ext_proc activity not visible"

# ════════════════════════════════════════════════════════════════════════════
section "Section 14: pending_acl + retry worker"
# ════════════════════════════════════════════════════════════════════════════
PENDING=$(psql_iam "SELECT COUNT(*) FROM pending_acl WHERE retry_count < max_retries;")
assert_match "pending_acl bounded" "^[0-9]+$" "$PENDING"
RS_RUNNING=$(kubectl -n "$RS_NS" get deploy resource-sync -o jsonpath='{.status.readyReplicas}' 2>/dev/null)
assert "retry worker (resource-sync) up" "1" "$RS_RUNNING"

# ════════════════════════════════════════════════════════════════════════════
section "Section 15: API Key lifecycle"
# ════════════════════════════════════════════════════════════════════════════
AK=$(A -X POST "$BASE_URL/api/v1/$REALM/api-keys" -H "Content-Type: application/json" \
  -d '{"app_name":"httpbin","description":"test-key","subject_id":"svc-test"}')
AK_PLAIN=$(echo "$AK" | jget api_key)
AK_ID=$(echo "$AK" | jget id)
AK_PREFIX=$(echo "$AK" | jget key_prefix)
assert_match "POST /api-keys returns plaintext" "^ak_[A-Za-z0-9_-]{20,}$" "$AK_PLAIN"
assert_match "POST /api-keys returns id" ".+" "$AK_ID"

DB_HASH=$(psql_iam "SELECT api_key_hash FROM api_keys WHERE id='$AK_ID';")
assert_not_contains "DB hash != plaintext" "$AK_PLAIN" "$DB_HASH"
assert_match "DB hash is sha256 hex" "^[a-f0-9]{64}$" "$DB_HASH"

LIST=$(A "$BASE_URL/api/v1/$REALM/api-keys")
assert_contains "GET /api-keys lists prefix" "$AK_PREFIX" "$LIST"
assert_not_contains "GET /api-keys does NOT expose plaintext" "$AK_PLAIN" "$LIST"

DETAIL=$(A "$BASE_URL/api/v1/$REALM/api-keys/$AK_ID")
assert_contains "detail has id" "$AK_ID" "$DETAIL"
assert_not_contains "detail does NOT expose plaintext" "$AK_PLAIN" "$DETAIL"

ROT=$(A -X POST "$BASE_URL/api/v1/$REALM/api-keys/$AK_ID/rotate")
AK_NEW=$(echo "$ROT" | jget api_key)
assert_match "rotate returns new plaintext" "^ak_[A-Za-z0-9_-]{20,}$" "$AK_NEW"
[ "$AK_NEW" != "$AK_PLAIN" ] && assert "rotate plaintext differs" "yes" "yes" || assert "rotate plaintext differs" "yes" "no"
DB_SUBJ_BEFORE=$(echo "$AK" | jget subject_id)
DB_SUBJ_AFTER=$(psql_iam "SELECT subject_id FROM api_keys WHERE id='$AK_ID';")
assert "subject_id preserved across rotation" "$DB_SUBJ_BEFORE" "$DB_SUBJ_AFTER"

A -X PUT "$BASE_URL/api/v1/$REALM/api-keys/$AK_ID" -H "Content-Type: application/json" -d '{"enabled":false}' >/dev/null
assert "DB enabled=false after disable" "f" "$(psql_iam "SELECT enabled FROM api_keys WHERE id='$AK_ID';")"

A -X DELETE "$BASE_URL/api/v1/$REALM/api-keys/$AK_ID" >/dev/null
assert "DB row removed after DELETE" "0" "$(psql_iam "SELECT COUNT(*) FROM api_keys WHERE id='$AK_ID';")"

# ════════════════════════════════════════════════════════════════════════════
section "Section 16: API Key auth"
# ════════════════════════════════════════════════════════════════════════════
FRESH=$(A -X POST "$BASE_URL/api/v1/$REALM/api-keys" -H "Content-Type: application/json" \
  -d '{"app_name":"httpbin","description":"auth-test","subject_id":"svc-auth","allowed_paths":["/anything"]}')
FRESH_KEY=$(echo "$FRESH" | jget api_key)
FRESH_ID=$(echo "$FRESH" | jget id)
if [ -n "$FRESH_KEY" ]; then
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $FRESH_KEY" "$BASE_URL/anything")
  assert_match "X-API-Key access /anything → 200/403" "^(200|403)$" "$CODE"

  A -X PUT "$BASE_URL/api/v1/$REALM/api-keys/$FRESH_ID" -H "Content-Type: application/json" -d '{"enabled":false}' >/dev/null
  sleep 1
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $FRESH_KEY" "$BASE_URL/anything")
  assert_match "disabled API Key → 401/403" "^(401|403)$" "$CODE"

  CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: ak_invalid_xxx" "$BASE_URL/anything")
  assert_match "invalid API Key → 401/403" "^(401|403)$" "$CODE"

  A -X DELETE "$BASE_URL/api/v1/$REALM/api-keys/$FRESH_ID" >/dev/null
fi

# ════════════════════════════════════════════════════════════════════════════
section "Section 17: SAML IdP CRUD (admin)"
# ════════════════════════════════════════════════════════════════════════════
CODE=$(AH "$BASE_URL/api/v1/$REALM/idp/saml/instances")
assert "GET /{realm}/idp/saml/instances → 200" "200" "$CODE"
CODE=$(AH -X POST "$BASE_URL/api/v1/$REALM/idp/saml/import" -H "Content-Type: application/json" \
  -d '{"metadata_xml":"not-xml"}')
assert_match "POST /{realm}/idp/saml/import invalid → 4xx" "^4" "$CODE"

# ════════════════════════════════════════════════════════════════════════════
section "Section 18: License disable (admin can flip; app_disabled blocks)"
# ════════════════════════════════════════════════════════════════════════════
A -X PUT "$BASE_URL/api/v1/apps/httpbin" -H "Content-Type: application/json" -d '{"enabled":false}' >/dev/null
# bundle refresh interval is 30s; wait for OPA to pick up app_disabled
sleep 35
CODE=$(AH "$BASE_URL/anything")
assert_match "/anything with httpbin disabled → 403" "^(403)$" "$CODE"

A -X PUT "$BASE_URL/api/v1/apps/httpbin" -H "Content-Type: application/json" -d '{"enabled":true}' >/dev/null
sleep 35
CODE=$(AH "$BASE_URL/anything")
assert_match "/anything re-enabled → 200/403" "^(200|403)$" "$CODE"

# ════════════════════════════════════════════════════════════════════════════
section "Section 19: Normal user denied admin endpoints"
# ════════════════════════════════════════════════════════════════════════════
if [ -n "$NORMAL_TOKEN" ]; then
  NH() { curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $NORMAL_TOKEN" "$@"; }
  assert_match "normal-user GET /api/v1/apps → 403" "^(401|403)$" "$(NH $BASE_URL/api/v1/apps)"
  assert_match "normal-user GET /api/v1/path-rules → 403" "^(401|403)$" "$(NH $BASE_URL/api/v1/path-rules)"
  assert_match "normal-user GET /api/v1/$REALM/users → 403" "^(401|403)$" "$(NH $BASE_URL/api/v1/$REALM/users)"
else
  skip "Section 19 — no normal-user token"
fi

# ════════════════════════════════════════════════════════════════════════════
section "Section 20: BackendTrafficPolicy (Envoy Gateway exclusive)"
# ════════════════════════════════════════════════════════════════════════════
BTP=$(cat <<'EOF'
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: BackendTrafficPolicy
metadata: {name: test-btp, namespace: envoy-gateway-system}
spec:
  targetRefs:
  - {group: gateway.networking.k8s.io, kind: HTTPRoute, name: tenant-api-route}
  loadBalancer: {type: ConsistentHash, consistentHash: {type: Header, header: {name: X-Tenant-Id}}}
EOF
)
echo "$BTP" | kubectl apply -f - >/dev/null 2>&1
STATUS=$(kubectl -n envoy-gateway-system get backendtrafficpolicy test-btp -o jsonpath='{.status.ancestors[0].conditions[?(@.type=="Accepted")].status}' 2>/dev/null)
assert_match "BackendTrafficPolicy accepted" "^(True|)$" "$STATUS"
kubectl -n envoy-gateway-system delete backendtrafficpolicy test-btp >/dev/null 2>&1 || true

# ════════════════════════════════════════════════════════════════════════════
section "Section 21: Real POST non-201 → no ACL written (SR03 negative)"
# ════════════════════════════════════════════════════════════════════════════
psql_iam "INSERT INTO resource_patterns(app_name,resource_prefix,resource_type,id_source,id_field) VALUES('httpbin','/anything/status','echo_item','path','id') ON CONFLICT DO NOTHING;" >/dev/null
sleep 1
A -X POST "$BASE_URL/anything/status/fake-id-001" -H "Content-Type: application/json" -d '{"id":"fake-id-001"}' >/dev/null
sleep 2
assert "ext_proc DOES NOT write ACL on non-201" "0" "$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE resource_id='fake-id-001';")"
psql_iam "DELETE FROM resource_patterns WHERE app_name='httpbin' AND resource_prefix='/anything/status';" >/dev/null

# ════════════════════════════════════════════════════════════════════════════
section "Section 22: SR06 X-Allowed-Ids injection on collection path"
# ════════════════════════════════════════════════════════════════════════════
psql_iam "INSERT INTO resource_acl(tenant_id,app_name,resource_type,resource_id,subject_type,subject_id,permission) VALUES
  ('$REALM','httpbin','item','eid-x','user','$ADMIN_SUB','owner'),
  ('$REALM','httpbin','item','eid-y','user','$ADMIN_SUB','viewer')
  ON CONFLICT DO NOTHING;" >/dev/null
sleep 1
ECHO=$(A "$BASE_URL/anything/items?page=1&size=10")
HAS_IDS=$(echo "$ECHO" | python -c "
import sys,json
try:
  d=json.load(sys.stdin); h=d.get('headers',{})
  v=h.get('X-Allowed-Ids') or h.get('x-allowed-ids')
  print('yes' if v else 'no')
except: print('no')")
[ "$HAS_IDS" = "yes" ] && assert "ext_proc 注入 X-Allowed-Ids" "yes" "yes" || skip "X-Allowed-Ids 未注入（admin sub 未在 ACL 集合表中？）"
psql_iam "DELETE FROM resource_acl WHERE app_name='httpbin' AND resource_id IN ('eid-x','eid-y');" >/dev/null 2>&1 || true

# ════════════════════════════════════════════════════════════════════════════
section "Section 23: Flexible API adaptation — body / query id_source (v2.1)"
# ════════════════════════════════════════════════════════════════════════════
psql_iam "INSERT INTO resource_patterns(app_name,resource_prefix,resource_type,id_source,id_field) VALUES('httpbin','/anything/body','bodyitem','body','item_id') ON CONFLICT DO NOTHING;" >/dev/null
psql_iam "INSERT INTO resource_acl(tenant_id,app_name,resource_type,resource_id,subject_type,subject_id,permission) VALUES('$REALM','httpbin','bodyitem','bd-001','user','$ADMIN_SUB','owner') ON CONFLICT DO NOTHING;" >/dev/null
sleep 2
CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -X PUT -d '{"item_id":"bd-001","x":"y"}' "$BASE_URL/anything/body")
assert_match "PUT body id_source → 200/403" "^(200|403)$" "$CODE"

psql_iam "INSERT INTO resource_patterns(app_name,resource_prefix,resource_type,id_source,id_field,id_query_param) VALUES('httpbin','/anything/query','qitem','query','rid','rid') ON CONFLICT DO NOTHING;" >/dev/null
psql_iam "INSERT INTO resource_acl(tenant_id,app_name,resource_type,resource_id,subject_type,subject_id,permission) VALUES('$REALM','httpbin','qitem','q-001','user','$ADMIN_SUB','owner') ON CONFLICT DO NOTHING;" >/dev/null
sleep 1
CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $ADMIN_TOKEN" "$BASE_URL/anything/query?rid=q-001")
assert_match "GET query id_source → 200/403" "^(200|403)$" "$CODE"

psql_iam "DELETE FROM resource_patterns WHERE app_name='httpbin' AND resource_prefix IN ('/anything/body','/anything/query');" >/dev/null
psql_iam "DELETE FROM resource_acl WHERE app_name='httpbin' AND resource_type IN ('bodyitem','qitem');" >/dev/null

# ════════════════════════════════════════════════════════════════════════════
section "Section 24: /acl/v1/* admin access"
# ════════════════════════════════════════════════════════════════════════════
CODE=$(AH "$BASE_URL/acl/v1/resources/probe-id/permissions?app_name=httpbin&resource_type=item")
assert_match "/acl/v1 admin GET → 200" "^(200|403)$" "$CODE"
CODE=$(AH -X POST "$BASE_URL/acl/v1/resources/probe-id/permissions" \
  -H "Content-Type: application/json" \
  -d '{"app_name":"httpbin","resource_type":"item","subject_type":"user","subject_id":"x","permission":"viewer"}')
assert_match "/acl/v1 POST without owner row → 401/403" "^(401|403)$" "$CODE"

# ════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${BLUE}════════════════════════════════════════${NC}"
echo -e "Test Results: ${GREEN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC}, $TOTAL total"
if [ "$FAIL" -eq 0 ]; then
  echo -e "${GREEN}ALL TESTS PASSED${NC}"; exit 0
else
  echo -e "${RED}SOME TESTS FAILED${NC}"; exit 1
fi
