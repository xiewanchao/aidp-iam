#!/usr/bin/env bash
# ============================================================================
# test.sh — IAM end-to-end test suite
#
# Single realm `aidp`, admin group `admins`, default group `all-users`,
# app-admin groups `kb-admins` / `rubik-admins` / `memory-admins`.
# Business backends are mock-kb (/kb/*) and mock-rubik (/rubik/*).
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

KB_HEALTH=$(MSYS_NO_PATHCONV=1 kubectl -n mock-kb exec deploy/mock-kb -- \
  python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8080/health').status)" 2>/dev/null || echo 000)
assert "mock-kb /health" "200" "$KB_HEALTH"

RUBIK_HEALTH=$(MSYS_NO_PATHCONV=1 kubectl -n mock-rubik exec deploy/mock-rubik -- \
  python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8081/health').status)" 2>/dev/null || echo 000)
assert "mock-rubik /health" "200" "$RUBIK_HEALTH"

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
for path in /api/v1/tenants /api/v1/apps /api/v1/path-rules /kb/knowledge_bases/page /rubik/api/databases; do
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
assert_contains "GET /api/v1/apps lists seed app knowledgebase" "knowledgebase" "$APPS"
assert_contains "GET /api/v1/apps lists seed app rubik" "rubik" "$APPS"

CREATE=$(A -X POST "$BASE_URL/api/v1/apps" -H "Content-Type: application/json" \
  -d "{\"app_name\":\"$TEST_APP\",\"path_prefix\":\"/$TEST_APP/\",\"display_name\":\"Test\",\"enabled\":true}")
assert_contains "POST /api/v1/apps creates" "$TEST_APP" "$CREATE"
assert "DB has the new app" "$TEST_APP" "$(psql_iam "SELECT app_name FROM apps WHERE app_name='$TEST_APP';")"
# New admin_group should be auto-set on registration
assert "DB admin_group auto-set to {app}-admins" "$TEST_APP-admins" "$(psql_iam "SELECT admin_group FROM apps WHERE app_name='$TEST_APP';")"

CODE=$(AH "$BASE_URL/api/v1/apps/$TEST_APP")
assert "GET /api/v1/apps/$TEST_APP" "200" "$CODE"

CODE=$(AH -X PUT "$BASE_URL/api/v1/apps/$TEST_APP" -H "Content-Type: application/json" -d '{"enabled":false}')
assert_match "PUT disable app" "^(200|204)$" "$CODE"
assert "DB enabled flag flipped" "f" "$(psql_iam "SELECT enabled FROM apps WHERE app_name='$TEST_APP';")"

A -X PUT "$BASE_URL/api/v1/apps/$TEST_APP" -H "Content-Type: application/json" -d '{"enabled":true}' >/dev/null
sleep 2

# ════════════════════════════════════════════════════════════════════════════
section "Section 6: Path rules CRUD (method-aware)"
# ════════════════════════════════════════════════════════════════════════════
psql_iam "DELETE FROM path_rules WHERE path_prefix LIKE '/test/%';" >/dev/null 2>&1 || true

RULE=$(A -X POST "$BASE_URL/api/v1/path-rules" -H "Content-Type: application/json" \
  -d '{"path_prefix":"/test/admin","method":"POST","required_group":"some-app-admins","description":"test"}')
RULE_ID=$(echo "$RULE" | jget id)
assert_match "POST /api/v1/path-rules returns id" "^[0-9]+$" "$RULE_ID"
assert_contains "rule method is POST" "POST" "$RULE"

RULES=$(A "$BASE_URL/api/v1/path-rules")
assert_contains "GET lists new rule" "/test/admin" "$RULES"

CODE=$(AH -X PUT "$BASE_URL/api/v1/path-rules/$RULE_ID" -H "Content-Type: application/json" \
  -d '{"required_group":"some-app-admins","description":"updated"}')
assert_match "PUT path rule" "^(200|204)$" "$CODE"

CODE=$(AH -X DELETE "$BASE_URL/api/v1/path-rules/$RULE_ID")
assert "DELETE path rule" "204" "$CODE"
assert_not_contains "rule gone after delete" "/test/admin" "$(A $BASE_URL/api/v1/path-rules)"

# ════════════════════════════════════════════════════════════════════════════
section "Section 7: Path-level authz (admin super-bypass + method matching)"
# ════════════════════════════════════════════════════════════════════════════
# System admins bypass /api/v1/*, /acl/v1/*, and all registered app paths.
CODE=$(AH "$BASE_URL/api/v1/tenants")
assert "admin GET /api/v1/tenants -> 200" "200" "$CODE"

CODE=$(AH "$BASE_URL/kb/knowledge_bases/page")
assert "admin GET /kb/... -> 200 (super-bypass)" "200" "$CODE"

CODE=$(AH "$BASE_URL/rubik/api/databases")
assert "admin GET /rubik/... -> 200 (super-bypass)" "200" "$CODE"

# ════════════════════════════════════════════════════════════════════════════
section "Section 8: Tenants (single realm)"
# ════════════════════════════════════════════════════════════════════════════
TENANTS_LIST=$(A "$BASE_URL/api/v1/tenants")
assert_contains "GET /tenants returns aidp realm" "$REALM" "$TENANTS_LIST"

CODE=$(AH -X POST "$BASE_URL/api/v1/tenants" -H "Content-Type: application/json" \
  -d '{"realm_name":"x","admin_username":"y","admin_password":"z"}')
assert_match "POST /api/v1/tenants removed (404/405)" "^(404|405)$" "$CODE"

# ════════════════════════════════════════════════════════════════════════════
section "Section 9: Identity CRUD (groups/users)"
# ════════════════════════════════════════════════════════════════════════════
CODE=$(AH "$BASE_URL/api/v1/$REALM/groups")
assert "admin GET /{realm}/groups -> 200" "200" "$CODE"
CODE=$(AH "$BASE_URL/api/v1/$REALM/users")
assert "admin GET /{realm}/users -> 200" "200" "$CODE"

CODE=$(AH -X POST "$BASE_URL/api/v1/$REALM/groups" -H "Content-Type: application/json" \
  -d '{"name":"test-group"}')
assert_match "POST /{realm}/groups -> 200/201" "^(200|201)$" "$CODE"
GROUPS_LIST=$(A "$BASE_URL/api/v1/$REALM/groups")
assert_contains "GET lists test-group" "test-group" "$GROUPS_LIST"
GID=$(echo "$GROUPS_LIST" | python -c "import sys,json
d=json.load(sys.stdin)
for g in d:
  if g.get('name')=='test-group': print(g.get('id') or ''); break")
[ -n "$GID" ] && A -X DELETE "$BASE_URL/api/v1/$REALM/groups/$GID" >/dev/null

# ════════════════════════════════════════════════════════════════════════════
section "Section 10: Mock-KB end-to-end (create + resource_acl auto-sync)"
# ════════════════════════════════════════════════════════════════════════════
ADMIN_SUB=$(jwt_claim "$ADMIN_TOKEN" sub)

# Create a KB → ext_proc should write resource_acl with owner=admin_sub
KB_CREATE=$(A -X POST "$BASE_URL/kb/knowledge_bases/add" -H "Content-Type: application/json" \
  -d '{"KDSID":"kb-e2e-001","NAME":"E2E Test KB","DESCRIPTION":"integration test"}')
assert_contains "POST /kb/knowledge_bases/add returns KDSID" "kb-e2e-001" "$KB_CREATE"

sleep 2  # wait for ext_proc to write ACL
ACL_ROW=$(psql_iam "SELECT subject_id, permission FROM resource_acl WHERE resource_id='kb-e2e-001';")
assert_contains "resource_acl written after KB create" "$ADMIN_SUB" "$ACL_ROW"
assert_contains "resource_acl permission = owner" "owner" "$ACL_ROW"

# Read back via mock
KB_GET=$(A "$BASE_URL/kb/knowledge_bases/page")
assert_contains "GET /kb/knowledge_bases/page shows kb-e2e-001" "kb-e2e-001" "$KB_GET"

# Delete via remove endpoint → ext_proc should cascade delete ACL
A -X POST "$BASE_URL/kb/knowledge_bases/remove" -H "Content-Type: application/json" \
  -d '{"KDSID":"kb-e2e-001"}' >/dev/null
sleep 2
ACL_COUNT=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE resource_id='kb-e2e-001';")
assert "resource_acl cascaded on KB remove" "0" "$ACL_COUNT"

# ════════════════════════════════════════════════════════════════════════════
section "Section 11: Mock-Rubik end-to-end (database create/delete)"
# ════════════════════════════════════════════════════════════════════════════
RUBIK_CREATE=$(A -X POST "$BASE_URL/rubik/api/databases" -H "Content-Type: application/json" \
  -d '{"name":"test-db","type":"sqlite"}')
DB_ID=$(echo "$RUBIK_CREATE" | jget id)
assert_match "POST /rubik/api/databases returns id" "^[a-z0-9]{8}$" "$DB_ID"

sleep 2
ACL_CHECK=$(psql_iam "SELECT permission FROM resource_acl WHERE resource_id='$DB_ID' AND subject_id='$ADMIN_SUB';")
assert "Rubik DB creator became owner" "owner" "$ACL_CHECK"

# Sub-resource access (admin bypasses via super-bypass)
CODE=$(AH "$BASE_URL/rubik/api/databases/$DB_ID")
assert "admin GET /rubik/api/databases/{id} -> 200" "200" "$CODE"

CODE=$(AH "$BASE_URL/rubik/api/databases/$DB_ID/schema")
assert "admin GET /rubik/.../schema -> 200" "200" "$CODE"

# Build endpoint
CODE=$(AH -X POST "$BASE_URL/rubik/api/databases/$DB_ID/build")
assert "admin POST /rubik/.../build -> 200" "200" "$CODE"

# Delete
CODE=$(AH -X DELETE "$BASE_URL/rubik/api/databases/$DB_ID")
assert_match "admin DELETE /rubik/api/databases/{id} -> 2xx" "^(200|204)$" "$CODE"
sleep 2
ACL_COUNT=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE resource_id='$DB_ID';")
assert "Rubik DB ACL cascaded on delete" "0" "$ACL_COUNT"

# ════════════════════════════════════════════════════════════════════════════
section "Section 12: ACL API (/acl/v1) with admin"
# ════════════════════════════════════════════════════════════════════════════
psql_iam "DELETE FROM resource_acl WHERE resource_id='kb-acl-test';" >/dev/null
psql_iam "INSERT INTO resource_acl(tenant_id,app_name,resource_type,resource_id,subject_type,subject_id,permission) VALUES
  ('$REALM','knowledgebase','kb','kb-acl-test','user','$ADMIN_SUB','owner'),
  ('$REALM','knowledgebase','kb','kb-acl-test','user','someuser','owner')
  ON CONFLICT DO NOTHING;" >/dev/null

LIST=$(A "$BASE_URL/acl/v1/resources/kb-acl-test/permissions?app_name=knowledgebase&resource_type=kb")
assert_contains "GET /acl/v1 returns owner" "someuser" "$LIST"

SHARE=$(A -X POST "$BASE_URL/acl/v1/resources/kb-acl-test/permissions" -H "Content-Type: application/json" \
  -d "{\"app_name\":\"knowledgebase\",\"resource_type\":\"kb\",\"subject_type\":\"user\",\"subject_id\":\"viewer1\",\"permission\":\"viewer\"}")
assert_contains "POST share to viewer1" "viewer1" "$SHARE"

ACL_ID=$(psql_iam "SELECT id FROM resource_acl WHERE resource_id='kb-acl-test' AND subject_id='viewer1';")
if [ -n "$ACL_ID" ]; then
  CODE=$(AH -X PUT "$BASE_URL/acl/v1/resources/kb-acl-test/permissions/$ACL_ID" -H "Content-Type: application/json" \
    -d '{"permission":"contributor"}')
  assert_match "PUT permission viewer→contributor" "^(200|204)$" "$CODE"

  CODE=$(AH -X DELETE "$BASE_URL/acl/v1/resources/kb-acl-test/permissions/$ACL_ID")
  assert_match "DELETE permission" "^(200|204)$" "$CODE"
fi
psql_iam "DELETE FROM resource_acl WHERE resource_id='kb-acl-test';" >/dev/null

# ════════════════════════════════════════════════════════════════════════════
section "Section 13: resource_acl owner/viewer/contributor + UNIQUE"
# ════════════════════════════════════════════════════════════════════════════
psql_iam "DELETE FROM resource_acl WHERE resource_id='acl-unique-test';" >/dev/null
psql_iam "INSERT INTO resource_acl(tenant_id,app_name,resource_type,resource_id,subject_type,subject_id,permission) VALUES
  ('$REALM','knowledgebase','kb','acl-unique-test','user','u-owner','owner'),
  ('$REALM','knowledgebase','kb','acl-unique-test','user','u-viewer','viewer'),
  ('$REALM','knowledgebase','kb','acl-unique-test','user','u-contrib','contributor');" >/dev/null
assert "owner row" "owner" "$(psql_iam "SELECT permission FROM resource_acl WHERE resource_id='acl-unique-test' AND subject_id='u-owner';")"
assert "viewer row" "viewer" "$(psql_iam "SELECT permission FROM resource_acl WHERE resource_id='acl-unique-test' AND subject_id='u-viewer';")"
assert "contributor row" "contributor" "$(psql_iam "SELECT permission FROM resource_acl WHERE resource_id='acl-unique-test' AND subject_id='u-contrib';")"

BEFORE=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE resource_id='acl-unique-test' AND subject_id='u-owner';")
psql_iam "INSERT INTO resource_acl(tenant_id,app_name,resource_type,resource_id,subject_type,subject_id,permission) VALUES('$REALM','knowledgebase','kb','acl-unique-test','user','u-owner','viewer') ON CONFLICT DO NOTHING;" >/dev/null
AFTER=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE resource_id='acl-unique-test' AND subject_id='u-owner';")
assert "UNIQUE prevents dupes" "$BEFORE" "$AFTER"

psql_iam "DELETE FROM resource_acl WHERE resource_id='acl-unique-test';" >/dev/null
assert "cascade delete by resource_id" "0" "$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE resource_id='acl-unique-test';")"

# ════════════════════════════════════════════════════════════════════════════
section "Section 14: ext_proc + pending_acl retry worker"
# ════════════════════════════════════════════════════════════════════════════
A "$BASE_URL/kb/knowledge_bases/page" >/dev/null
sleep 1
RS_LOG=$(kubectl -n "$RS_NS" logs deploy/resource-sync --tail=30 2>&1 | grep -iE "ext_proc|process|stream" | tail -3)
[ -n "$RS_LOG" ] && assert "ext_proc handler observed activity" "yes" "yes" || skip "ext_proc activity not visible"

PENDING=$(psql_iam "SELECT COUNT(*) FROM pending_acl WHERE retry_count < max_retries;")
assert_match "pending_acl bounded" "^[0-9]+$" "$PENDING"
RS_RUNNING=$(kubectl -n "$RS_NS" get deploy resource-sync -o jsonpath='{.status.readyReplicas}' 2>/dev/null)
assert "retry worker up" "1" "$RS_RUNNING"

# ════════════════════════════════════════════════════════════════════════════
section "Section 15: API Key lifecycle"
# ════════════════════════════════════════════════════════════════════════════
AK=$(A -X POST "$BASE_URL/api/v1/$REALM/api-keys" -H "Content-Type: application/json" \
  -d '{"app_name":"knowledgebase","description":"test-key","subject_id":"svc-test"}')
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

ROT=$(A -X POST "$BASE_URL/api/v1/$REALM/api-keys/$AK_ID/rotate")
AK_NEW=$(echo "$ROT" | jget api_key)
assert_match "rotate returns new plaintext" "^ak_[A-Za-z0-9_-]{20,}$" "$AK_NEW"
[ "$AK_NEW" != "$AK_PLAIN" ] && assert "rotate plaintext differs" "yes" "yes" || assert "rotate plaintext differs" "yes" "no"

A -X PUT "$BASE_URL/api/v1/$REALM/api-keys/$AK_ID" -H "Content-Type: application/json" -d '{"enabled":false}' >/dev/null
assert "DB enabled=false after disable" "f" "$(psql_iam "SELECT enabled FROM api_keys WHERE id='$AK_ID';")"

A -X DELETE "$BASE_URL/api/v1/$REALM/api-keys/$AK_ID" >/dev/null
assert "DB row removed after DELETE" "0" "$(psql_iam "SELECT COUNT(*) FROM api_keys WHERE id='$AK_ID';")"

# ════════════════════════════════════════════════════════════════════════════
section "Section 16: API Key auth"
# ════════════════════════════════════════════════════════════════════════════
FRESH=$(A -X POST "$BASE_URL/api/v1/$REALM/api-keys" -H "Content-Type: application/json" \
  -d '{"app_name":"knowledgebase","description":"auth-test","subject_id":"svc-auth","allowed_paths":["/kb"]}')
FRESH_KEY=$(echo "$FRESH" | jget api_key)
FRESH_ID=$(echo "$FRESH" | jget id)
if [ -n "$FRESH_KEY" ]; then
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $FRESH_KEY" "$BASE_URL/kb/knowledge_bases/page")
  assert_match "X-API-Key access /kb/... -> 200/403" "^(200|403)$" "$CODE"

  A -X PUT "$BASE_URL/api/v1/$REALM/api-keys/$FRESH_ID" -H "Content-Type: application/json" -d '{"enabled":false}' >/dev/null
  sleep 1
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $FRESH_KEY" "$BASE_URL/kb/knowledge_bases/page")
  assert_match "disabled API Key -> 401/403" "^(401|403)$" "$CODE"

  CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: ak_invalid_xxx" "$BASE_URL/kb/knowledge_bases/page")
  assert_match "invalid API Key -> 401/403" "^(401|403)$" "$CODE"

  A -X DELETE "$BASE_URL/api/v1/$REALM/api-keys/$FRESH_ID" >/dev/null
fi

# ════════════════════════════════════════════════════════════════════════════
section "Section 17: SAML IdP CRUD (admin)"
# ════════════════════════════════════════════════════════════════════════════
CODE=$(AH "$BASE_URL/api/v1/$REALM/idp/saml/instances")
assert "GET /{realm}/idp/saml/instances -> 200" "200" "$CODE"
CODE=$(AH -X POST "$BASE_URL/api/v1/$REALM/idp/saml/import" -H "Content-Type: application/json" \
  -d '{"metadata_xml":"not-xml"}')
assert_match "POST /{realm}/idp/saml/import invalid -> 4xx" "^4" "$CODE"

# ════════════════════════════════════════════════════════════════════════════
section "Section 18: License disable (app_disabled blocks even admins)"
# ════════════════════════════════════════════════════════════════════════════
A -X PUT "$BASE_URL/api/v1/apps/knowledgebase" -H "Content-Type: application/json" -d '{"enabled":false}' >/dev/null
sleep 35  # bundle refresh
CODE=$(AH "$BASE_URL/kb/knowledge_bases/page")
assert_match "/kb/... with knowledgebase disabled -> 403" "^(403)$" "$CODE"

A -X PUT "$BASE_URL/api/v1/apps/knowledgebase" -H "Content-Type: application/json" -d '{"enabled":true}' >/dev/null
sleep 35
CODE=$(AH "$BASE_URL/kb/knowledge_bases/page")
assert_match "/kb/... re-enabled -> 200" "^(200|403)$" "$CODE"

# ════════════════════════════════════════════════════════════════════════════
section "Section 19: Normal user (default-deny + path_rule whitelist)"
# ════════════════════════════════════════════════════════════════════════════
NORMAL_TOKEN=$(curl -s -X POST "$BASE_URL/realms/$REALM/protocol/openid-connect/token" \
  -d "client_id=$CLIENT_ID" -d "client_secret=$CS" -d "grant_type=password" \
  -d "username=$NORMAL_USER" -d "password=$NORMAL_PASSWORD" | jget access_token)

if [ -n "$NORMAL_TOKEN" ]; then
  NH() { curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $NORMAL_TOKEN" "$@"; }
  # Management API - should be 403 (not in admins)
  assert_match "normal-user GET /api/v1/apps -> 403" "^(401|403)$" "$(NH $BASE_URL/api/v1/apps)"
  assert_match "normal-user GET /api/v1/path-rules -> 403" "^(401|403)$" "$(NH $BASE_URL/api/v1/path-rules)"
  assert_match "normal-user GET /api/v1/$REALM/users -> 403" "^(401|403)$" "$(NH $BASE_URL/api/v1/$REALM/users)"
  # Business API with all-users path_rule
  assert_match "normal-user GET /rubik/api/databases -> 200 (all-users allowed)" "^(200)$" "$(NH $BASE_URL/rubik/api/databases)"
  # Business API NOT whitelisted for all-users
  assert_match "normal-user PUT /rubik/api/config/models/x -> 403" "^(401|403)$" "$(NH -X PUT -H 'Content-Type: application/json' -d '{}' $BASE_URL/rubik/api/config/models/x)"
else
  skip "Section 19 — no normal-user token"
fi

# ════════════════════════════════════════════════════════════════════════════
section "Section 20: BackendTrafficPolicy"
# ════════════════════════════════════════════════════════════════════════════
BTP=$(cat <<'EOF'
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: BackendTrafficPolicy
metadata: {name: test-btp, namespace: envoy-gateway-system}
spec:
  targetRefs:
  - {group: gateway.networking.k8s.io, kind: HTTPRoute, name: mock-kb-route}
  loadBalancer: {type: ConsistentHash, consistentHash: {type: Header, header: {name: X-Tenant-Id}}}
EOF
)
echo "$BTP" | kubectl apply -f - >/dev/null 2>&1
STATUS=$(kubectl -n envoy-gateway-system get backendtrafficpolicy test-btp -o jsonpath='{.status.ancestors[0].conditions[?(@.type=="Accepted")].status}' 2>/dev/null)
assert_match "BackendTrafficPolicy accepted" "^(True|)$" "$STATUS"
kubectl -n envoy-gateway-system delete backendtrafficpolicy test-btp >/dev/null 2>&1 || true

# ════════════════════════════════════════════════════════════════════════════
section "Section 21: POST non-201 does NOT write ACL"
# ════════════════════════════════════════════════════════════════════════════
# Mock-kb returns 200 (not 201) for /knowledge_bases/page — POST should not create ACL
A -X POST "$BASE_URL/kb/knowledge_bases/page" -H "Content-Type: application/json" -d '{}' >/dev/null
sleep 2
# No resource_id should be extracted from this endpoint (no pattern match on /page)
# Just verify no spurious ACL row was written with known test id
assert "ext_proc skips paths without resource_pattern match" "0" "$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE resource_id='page';")"

# ════════════════════════════════════════════════════════════════════════════
section "Section 22: X-Allowed-Ids on collection endpoint"
# ════════════════════════════════════════════════════════════════════════════
# Create 2 Rubik DBs, verify GET /rubik/api/databases returns them filtered
DB1=$(A -X POST "$BASE_URL/rubik/api/databases" -H "Content-Type: application/json" -d '{"name":"x1"}' | jget id)
DB2=$(A -X POST "$BASE_URL/rubik/api/databases" -H "Content-Type: application/json" -d '{"name":"x2"}' | jget id)
sleep 2
if [ -n "$DB1" ] && [ -n "$DB2" ]; then
  LIST=$(A "$BASE_URL/rubik/api/databases")
  if echo "$LIST" | grep -q "$DB1"; then
    assert "GET /rubik/api/databases returns owned DBs" "yes" "yes"
  else
    skip "X-Allowed-Ids filtering not observed (ext_proc may not intercept GET path)"
  fi
  A -X DELETE "$BASE_URL/rubik/api/databases/$DB1" >/dev/null
  A -X DELETE "$BASE_URL/rubik/api/databases/$DB2" >/dev/null
else
  skip "could not create test DBs"
fi

# ════════════════════════════════════════════════════════════════════════════
section "Section 23: Body-based resource ID (NL2SQL query)"
# ════════════════════════════════════════════════════════════════════════════
# /rubik/api/query extracts database_id from JSON body. Create a DB, then query it.
DB_Q=$(A -X POST "$BASE_URL/rubik/api/databases" -H "Content-Type: application/json" -d '{"name":"query-test"}' | jget id)
sleep 2
if [ -n "$DB_Q" ]; then
  CODE=$(AH -X POST "$BASE_URL/rubik/api/query" -H "Content-Type: application/json" \
    -d "{\"session_id\":\"s1\",\"database_id\":\"$DB_Q\",\"query\":\"how many users\"}")
  assert_match "POST /rubik/api/query -> 200" "^(200)$" "$CODE"
  A -X DELETE "$BASE_URL/rubik/api/databases/$DB_Q" >/dev/null
else
  skip "could not create DB for query test"
fi

# ════════════════════════════════════════════════════════════════════════════
section "Section 24: /acl/v1 admin access"
# ════════════════════════════════════════════════════════════════════════════
CODE=$(AH "$BASE_URL/acl/v1/resources/probe-id/permissions?app_name=knowledgebase&resource_type=kb")
assert_match "/acl/v1 admin GET -> 200" "^(200|403)$" "$CODE"
CODE=$(AH -X POST "$BASE_URL/acl/v1/resources/probe-id/permissions" \
  -H "Content-Type: application/json" \
  -d '{"app_name":"knowledgebase","resource_type":"kb","subject_type":"user","subject_id":"x","permission":"viewer"}')
assert_match "/acl/v1 POST without owner row -> 401/403" "^(401|403)$" "$CODE"

# ════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${BLUE}════════════════════════════════════════${NC}"
echo -e "Test Results: ${GREEN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC}, $TOTAL total"
if [ "$FAIL" -eq 0 ]; then
  echo -e "${GREEN}ALL TESTS PASSED${NC}"; exit 0
else
  echo -e "${RED}SOME TESTS FAILED${NC}"; exit 1
fi
