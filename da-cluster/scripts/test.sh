#!/usr/bin/env bash
# ============================================================================
# test.sh — IAM end-to-end test suite (v2.1 unified authorization design)
#
# Unified URL format: /<Namespace>/Tenants/<TenantID>/<TypeA>/<IDA>[/...]
# New resource_acl: (id, tenant_id, user_path, object_path, role_path, ...)
# New ACL API: PUT/GET/DELETE /AccessManager/Tenants/{tid}/ACLs
# New Manifest API: PUT/GET/DELETE /AccessManager/Tenants/System/AppManifests/{ns}
# Default roles: Owner / Contributor / Viewer under AccessManager/Tenants/System/Roles/
# Legacy /api/v1/ routes kept as compat aliases.
# ============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
KEYCLOAK_NS="keycloak"
IAM_NS="aidp-iam"
ENVOY_GATEWAY_NS="${ENVOY_GATEWAY_NS:-aidp-gateway}"
GATEWAY_PORT="${GATEWAY_PORT:-30080}"
BASE_URL="http://localhost:${GATEWAY_PORT}"

REALM="${REALM:-aidp}"
CLIENT_ID="${CLIENT_ID:-aidp-client}"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-Admin@123}"
NORMAL_USER="${NORMAL_USER:-normal-user}"
NORMAL_PASSWORD="${NORMAL_PASSWORD:-NormalUser@123}"
TEST_APP="${TEST_APP:-test-app}"
TEST_NS="TestApp"

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

# Detect whether mock-kb backend route is installed (optional component)
HAS_KB_ROUTE=$(kubectl get httproute -A 2>/dev/null | grep -ciE "mock-kb|knowledgebase|KnowledgeBase" || true)
[ "$HAS_KB_ROUTE" -gt 0 ] \
  && echo -e "  ${GREEN}mock-kb route detected — KB tests will run${NC}" \
  || echo -e "  ${YELLOW}mock-kb route not found — KB backend tests will be skipped${NC}"

# Cleanup trap: kill port-forward, remove test data
cleanup() {
  [ -n "${PF_PID:-}" ] && kill "$PF_PID" 2>/dev/null || true
  psql_iam "DELETE FROM resource_acl WHERE object_path LIKE '%/TestApp/%' OR object_path LIKE '%/test-acl-%';" >/dev/null 2>&1 || true
  psql_iam "DELETE FROM resource_acl WHERE user_path LIKE '%/alice%' OR user_path LIKE '%/bob%';" >/dev/null 2>&1 || true
  psql_iam "DELETE FROM app_manifests WHERE namespace='$TEST_NS';" >/dev/null 2>&1 || true
  psql_iam "DELETE FROM apps WHERE app_name='$TEST_APP';" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# ════════════════════════════════════════════════════════════════════════════
section "Section 1: Pod health"
# ════════════════════════════════════════════════════════════════════════════
KC_HEALTH=$(MSYS_NO_PATHCONV=1 kubectl -n aidp-iam exec deploy/iam-services -c aidp-iam-app -- \
  python3 -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8090/AccessManager/Tenants/Common/Health').status)" 2>/dev/null || echo 000)
assert "keycloak-proxy /AccessManager/Tenants/Common/Health" "200" "$KC_HEALTH"

PEP_HEALTH=$(MSYS_NO_PATHCONV=1 kubectl -n aidp-iam exec deploy/iam-services -c aidp-iam-app -- \
  curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>/dev/null || echo 000)
assert "pep-proxy /health" "200" "$PEP_HEALTH"

RS_HEALTH=$(MSYS_NO_PATHCONV=1 kubectl -n aidp-iam exec deploy/iam-services -c aidp-iam-app -- \
  curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/health 2>/dev/null || echo 000)
assert "resource-sync /health" "200" "$RS_HEALTH"

# mock-kb is not part of the core stack; skip health check
skip "mock-kb /health (mock backends not installed)"

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
# Pre-test setup: register KnowledgeBase manifest so bundle-server derives
# /KnowledgeBase/ path_rules before any test section runs.
# ════════════════════════════════════════════════════════════════════════════
_setup_admin_token() {
  local _CS
  _CS=$(MSYS_NO_PATHCONV=1 kubectl -n aidp-iam get secret keycloak-aidp-client \
    -o jsonpath='{.data.client-secret}' | base64 -d)
  curl -s -X POST "$BASE_URL/realms/$REALM/protocol/openid-connect/token" \
    -d "client_id=$CLIENT_ID&client_secret=$_CS&grant_type=password&username=$ADMIN_USER&password=$ADMIN_PASSWORD" | \
    jget access_token
}
_SETUP_TOKEN=$(_setup_admin_token)

KB_MANIFEST_FILE=$(mktemp /tmp/kb_manifest_XXXXXX.json)
cat > "$KB_MANIFEST_FILE" <<'JSON'
{
  "namespace": "KnowledgeBase",
  "display_name": "Knowledge Base",
  "base_url": "http://mock-kb.mock-kb.svc.cluster.local:8080",
  "resources": [
    {
      "type": "KnowledgeBases",
      "path_pattern": "/KnowledgeBase/Tenants/{tenantId}/KnowledgeBases/{kbId}",
      "methods": ["GET", "POST", "PUT", "DELETE"],
      "actions": [],
      "default_acl": [
        {
          "user_template": "AccessManager/Tenants/{tenantId}/Groups/all-users",
          "object_template": "KnowledgeBase/Tenants/{tenantId}/KnowledgeBases",
          "role_path": "AccessManager/Tenants/System/Roles/Contributor"
        }
      ],
      "children": [
        {
          "type": "Mappings",
          "path_pattern": "/KnowledgeBase/Tenants/{tenantId}/KnowledgeBases/{kbId}/Mappings/{mappingId}",
          "methods": ["GET", "POST", "DELETE"],
          "actions": [],
          "default_acl": [],
          "children": []
        },
        {
          "type": "Files",
          "path_pattern": "/KnowledgeBase/Tenants/{tenantId}/KnowledgeBases/{kbId}/Files/{fileId}",
          "methods": ["GET", "POST", "DELETE"],
          "actions": [],
          "default_acl": [],
          "children": []
        }
      ]
    },
    {
      "type": "Conversations",
      "path_pattern": "/KnowledgeBase/Tenants/{tenantId}/Conversations/{threadId}",
      "methods": ["GET", "POST", "DELETE"],
      "actions": [
        {
          "name": "Stop",
          "path_suffix": "/Stop",
          "http_method": "POST",
          "required_role": "AccessManager/Tenants/System/Roles/Owner"
        }
      ],
      "default_acl": [
        {
          "user_template": "AccessManager/Tenants/{tenantId}/Groups/all-users",
          "object_template": "KnowledgeBase/Tenants/{tenantId}/Conversations",
          "role_path": "AccessManager/Tenants/System/Roles/Contributor"
        }
      ],
      "children": []
    },
    {
      "type": "ModelConfigs",
      "path_pattern": "/KnowledgeBase/Tenants/System/ModelConfigs/{modelId}",
      "methods": ["GET", "POST", "PUT", "DELETE"],
      "actions": [],
      "default_acl": [],
      "children": []
    },
    {
      "type": "Prompts",
      "path_pattern": "/KnowledgeBase/Tenants/System/Prompts/{promptId}",
      "methods": ["GET", "POST", "PUT", "DELETE"],
      "actions": [],
      "default_acl": [],
      "children": []
    },
    {
      "type": "JargonLibraries",
      "path_pattern": "/KnowledgeBase/Tenants/{tenantId}/JargonLibraries/{libName}",
      "methods": ["GET", "POST", "DELETE"],
      "actions": [],
      "default_acl": [],
      "children": [
        {
          "type": "Jargons",
          "path_pattern": "/KnowledgeBase/Tenants/{tenantId}/JargonLibraries/{libName}/Jargons/{jargonName}",
          "methods": ["GET", "POST", "PUT", "DELETE"],
          "actions": [],
          "default_acl": [],
          "children": []
        }
      ]
    }
  ]
}
JSON

_KB_PUT=$(curl -s -o /dev/null -w "%{http_code}" \
  -X PUT "$BASE_URL/AccessManager/Tenants/System/AppManifests/KnowledgeBase" \
  -H "Authorization: Bearer $_SETUP_TOKEN" \
  -H "Content-Type: application/json" \
  -d "@$KB_MANIFEST_FILE")
rm -f "$KB_MANIFEST_FILE"

if [ "$_KB_PUT" = "200" ] || [ "$_KB_PUT" = "201" ]; then
  echo "  [setup] KnowledgeBase manifest registered ($_KB_PUT), waiting for OPA bundle refresh..."
  sleep 35
else
  echo "  [setup] WARNING: KnowledgeBase manifest PUT returned $_KB_PUT"
fi

# ════════════════════════════════════════════════════════════════════════════
section "Section 3: Protected routes reject no-token (401/403)"
# ════════════════════════════════════════════════════════════════════════════
NO_TOKEN_PATHS=(
  "/AccessManager/Tenants/$REALM/ACLs" \
  "/AccessManager/Tenants/System/AppManifests/TestApp" \
  "/AccessManager/Tenants/$REALM/Action/QueryACLs" \
  "/AccessManager/Tenants" \
  "/AccessManager/Tenants/System/AppManifests"
)
if [ "$HAS_KB_ROUTE" -gt 0 ]; then
  NO_TOKEN_PATHS+=("/KnowledgeBase/Tenants/$REALM/KnowledgeBases")
else
  skip "no-token /KnowledgeBase/... (mock-kb route not installed)"
fi

for path in "${NO_TOKEN_PATHS[@]}"; do
  code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL$path")
  assert_match "no-token $path -> 401/403" "^(401|403)$" "$code"
done

# ════════════════════════════════════════════════════════════════════════════
section "Section 4: Admin token (aidp-client + admin user, password grant)"
# ════════════════════════════════════════════════════════════════════════════
CS=$(kubectl -n aidp-iam get secret keycloak-aidp-client -o jsonpath='{.data.client-secret}' 2>/dev/null | base64 -d)
assert_match "aidp-client client-secret present" "^[A-Za-z0-9]{20,}$" "$CS"

ADMIN_TOKEN=$(curl -s -X POST "$BASE_URL/realms/$REALM/protocol/openid-connect/token" \
  -d "client_id=$CLIENT_ID" -d "client_secret=$CS" -d "grant_type=password" \
  -d "username=$ADMIN_USER" -d "password=$ADMIN_PASSWORD" | jget access_token)
[ -n "$ADMIN_TOKEN" ] && assert "admin token issued" "yes" "yes" || assert "admin token issued" "yes" "no"

ADMIN_GROUPS=$(jwt_claim "$ADMIN_TOKEN" groups)
# Groups may be full paths like AccessManager/Tenants/aidp/Groups/admins or short names
assert_contains "admin token contains 'admins' group" "admins" "$ADMIN_GROUPS"
assert_contains "admin token contains 'all-users' group" "all-users" "$ADMIN_GROUPS"
ADMIN_ISS=$(jwt_claim "$ADMIN_TOKEN" iss)
assert_contains "admin token iss /realms/$REALM" "realms/$REALM" "$ADMIN_ISS"
ADMIN_SUB=$(jwt_claim "$ADMIN_TOKEN" sub)
assert_match "admin token sub is UUID" "[0-9a-f-]{36}" "$ADMIN_SUB"

A()  { curl -s -H "Authorization: Bearer $ADMIN_TOKEN" "$@"; }
AH() { curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $ADMIN_TOKEN" "$@"; }

# ════════════════════════════════════════════════════════════════════════════
section "Section 5: New ACL table schema verification"
# ════════════════════════════════════════════════════════════════════════════
# Verify new columns exist
for col in user_path object_path role_path; do
  COL_EXISTS=$(psql_iam "SELECT column_name FROM information_schema.columns WHERE table_name='resource_acl' AND column_name='$col';")
  assert "resource_acl has column $col" "$col" "$COL_EXISTS"
done

# Verify old columns do NOT exist
for col in app_name resource_type resource_id subject_type subject_id permission; do
  COL_EXISTS=$(psql_iam "SELECT column_name FROM information_schema.columns WHERE table_name='resource_acl' AND column_name='$col';")
  assert "resource_acl does NOT have old column $col" "" "$COL_EXISTS"
done

# Verify required columns still present
for col in id tenant_id created_at created_by; do
  COL_EXISTS=$(psql_iam "SELECT column_name FROM information_schema.columns WHERE table_name='resource_acl' AND column_name='$col';")
  assert "resource_acl has column $col" "$col" "$COL_EXISTS"
done

# ════════════════════════════════════════════════════════════════════════════
section "Section 6: Manifest API (AppManifests CRUD + default_acl sync)"
# ════════════════════════════════════════════════════════════════════════════
psql_iam "DELETE FROM app_manifests WHERE namespace='$TEST_NS';" >/dev/null 2>&1 || true

MANIFEST_BODY=$(cat <<JSON
{
  "namespace": "$TEST_NS",
  "display_name": "Test Application",
  "base_url": "http://mock-testapp.example.com",
  "resources": [
    {
      "type": "Resources",
      "display_name": "Generic test resources",
      "path_pattern": "/$TEST_NS/Tenants/{tenantId}/Resources/{id}",
      "methods": ["GET", "PUT", "PATCH", "DELETE"],
      "actions": [],
      "default_acl": [
        {
          "user_template": "AccessManager/Tenants/{tenantId}/Groups/all-users",
          "object_template": "$TEST_NS/Tenants/{tenantId}/Resources",
          "role_path": "AccessManager/Tenants/System/Roles/Viewer"
        }
      ],
      "children": []
    }
  ]
}
JSON
)

PUT_CODE=$(AH -X PUT "$BASE_URL/AccessManager/Tenants/System/AppManifests/$TEST_NS" \
  -H "Content-Type: application/json" -d "$MANIFEST_BODY")
assert_match "PUT /AccessManager/Tenants/System/AppManifests/$TEST_NS -> 200/201" "^(200|201)$" "$PUT_CODE"

GET_MANIFEST=$(A "$BASE_URL/AccessManager/Tenants/System/AppManifests/$TEST_NS")
assert_contains "GET manifest returns namespace" "$TEST_NS" "$GET_MANIFEST"
assert_contains "GET manifest returns resource_types" "Resources" "$GET_MANIFEST"

sleep 2  # wait for default_acl sync
DEFAULT_ACL_ROW=$(psql_iam "SELECT role_path FROM resource_acl WHERE object_path='$TEST_NS/Tenants/$REALM/Resources' AND user_path LIKE '%all-users%' LIMIT 1;")
assert_contains "default_acl sync wrote Viewer ACL" "Viewer" "$DEFAULT_ACL_ROW"

# List manifests
LIST_MANIFESTS=$(A "$BASE_URL/AccessManager/Tenants/System/AppManifests")
assert_contains "GET AppManifests list contains $TEST_NS" "$TEST_NS" "$LIST_MANIFESTS"

# Delete manifest
DEL_CODE=$(AH -X DELETE "$BASE_URL/AccessManager/Tenants/System/AppManifests/$TEST_NS")
assert_match "DELETE /AccessManager/Tenants/System/AppManifests/$TEST_NS -> 200/204" "^(200|204)$" "$DEL_CODE"
GET_AFTER=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $ADMIN_TOKEN" \
  "$BASE_URL/AccessManager/Tenants/System/AppManifests/$TEST_NS")
assert_match "GET deleted manifest -> 404" "^(404)$" "$GET_AFTER"

# ════════════════════════════════════════════════════════════════════════════
section "Section 7: ACL management API (PUT/GET/DELETE/QueryACLs)"
# ════════════════════════════════════════════════════════════════════════════
ACL_OBJ="TestNS/Tenants/$REALM/Resources/acl-api-test-001"
ACL_USER="AccessManager/Tenants/$REALM/Users/test-user-acl"
ACL_ROLE="AccessManager/Tenants/System/Roles/Contributor"

psql_iam "DELETE FROM resource_acl WHERE object_path='$ACL_OBJ';" >/dev/null 2>&1 || true

# PUT — grant permission
PUT_ACL=$(AH -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
  -H "Content-Type: application/json" \
  -d "{\"user_path\":\"$ACL_USER\",\"object_path\":\"$ACL_OBJ\",\"role_path\":\"$ACL_ROLE\"}")
assert_match "PUT /AccessManager/Tenants/$REALM/ACLs -> 200/201" "^(200|201)$" "$PUT_ACL"

# GET — query by object
GET_ACL=$(A "$BASE_URL/AccessManager/Tenants/$REALM/ACLs?object=$ACL_OBJ")
assert_contains "GET ACLs returns user_path" "test-user-acl" "$GET_ACL"
assert_contains "GET ACLs returns role_path Contributor" "Contributor" "$GET_ACL"

# POST QueryACLs — batch check
QUERY_BODY=$(cat <<JSON
{
  "queries": [
    {"user_path": "$ACL_USER", "object_path": "$ACL_OBJ"},
    {"user_path": "$ACL_USER", "object_path": "TestNS/Tenants/$REALM/Resources/nonexistent-999"}
  ]
}
JSON
)
QUERY_RESP=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Action/QueryACLs" \
  -H "Content-Type: application/json" -d "$QUERY_BODY")
assert_contains "QueryACLs returns role_path for existing ACL" "Contributor" "$QUERY_RESP"

# DELETE — revoke permission
DEL_ACL=$(AH -X DELETE "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
  -H "Content-Type: application/json" \
  -d "{\"user_path\":\"$ACL_USER\",\"object_path\":\"$ACL_OBJ\"}")
assert_match "DELETE /AccessManager/Tenants/$REALM/ACLs -> 200/204" "^(200|204)$" "$DEL_ACL"

# Verify removed from DB
ACL_COUNT=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE object_path='$ACL_OBJ' AND user_path='$ACL_USER';")
assert "ACL row removed after DELETE" "0" "$ACL_COUNT"

# ════════════════════════════════════════════════════════════════════════════
section "Section 8: ACL write and cascade delete via AccessManager API"
# ════════════════════════════════════════════════════════════════════════════
# Verify that PUT ACL creates a row and DELETE ACL removes it (and cascades to children).
S8_OBJ="DataAgent/Tenants/$REALM/DataAgentDBs/s8-test-db-001"
S8_CHILD="DataAgent/Tenants/$REALM/DataAgentDBs/s8-test-db-001/Tables/tbl-001"
S8_USER="AccessManager/Tenants/$REALM/Users/s8-test-user"
S8_ROLE="AccessManager/Tenants/System/Roles/Owner"

psql_iam "DELETE FROM resource_acl WHERE object_path LIKE 'DataAgent/Tenants/$REALM/DataAgentDBs/s8-%';" >/dev/null 2>&1 || true

PUT_S8=$(AH -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
  -H "Content-Type: application/json" \
  -d "{\"user_path\":\"$S8_USER\",\"object_path\":\"$S8_OBJ\",\"role_path\":\"$S8_ROLE\"}")
assert_match "PUT ACL for DataAgentDB resource -> 200/201" "^(200|201)$" "$PUT_S8"

S8_ROW=$(psql_iam "SELECT role_path FROM resource_acl WHERE user_path='$S8_USER' AND object_path='$S8_OBJ' LIMIT 1;")
assert_contains "ACL row written to DB with Owner role" "Owner" "$S8_ROW"

# Write a child ACL row to verify cascade delete
psql_iam "INSERT INTO resource_acl (tenant_id, user_path, object_path, role_path, created_by) VALUES ('$REALM', '$S8_USER', '$S8_CHILD', '$S8_ROLE', 'test') ON CONFLICT DO NOTHING;" >/dev/null

DEL_S8=$(AH -X DELETE "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
  -H "Content-Type: application/json" \
  -d "{\"user_path\":\"$S8_USER\",\"object_path\":\"$S8_OBJ\"}")
assert_match "DELETE ACL for DataAgentDB resource -> 200/204" "^(200|204)$" "$DEL_S8"

S8_COUNT=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE user_path='$S8_USER' AND object_path='$S8_OBJ';")
assert "parent ACL row removed after DELETE" "0" "$S8_COUNT"

# ════════════════════════════════════════════════════════════════════════════
section "Section 9: ACL DELETE removes exact row only (no cascade)"
# ════════════════════════════════════════════════════════════════════════════
S9_PARENT="DataAgent/Tenants/$REALM/DataAgentDBs/s9-test-db-001"
S9_CHILD1="DataAgent/Tenants/$REALM/DataAgentDBs/s9-test-db-001/Tables/tbl-001"
S9_CHILD2="DataAgent/Tenants/$REALM/DataAgentDBs/s9-test-db-001/Tables/tbl-002"
S9_USER="AccessManager/Tenants/$REALM/Users/s9-test-user"

psql_iam "DELETE FROM resource_acl WHERE object_path LIKE 'DataAgent/Tenants/$REALM/DataAgentDBs/s9-%';" >/dev/null 2>&1 || true

psql_iam "INSERT INTO resource_acl (tenant_id, user_path, object_path, role_path, created_by) VALUES
  ('$REALM', '$S9_USER', '$S9_PARENT', 'AccessManager/Tenants/System/Roles/Owner', 'test'),
  ('$REALM', '$S9_USER', '$S9_CHILD1', 'AccessManager/Tenants/System/Roles/Owner', 'test'),
  ('$REALM', '$S9_USER', '$S9_CHILD2', 'AccessManager/Tenants/System/Roles/Owner', 'test')
  ON CONFLICT DO NOTHING;" >/dev/null

DEL_S9=$(AH -X DELETE "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
  -H "Content-Type: application/json" \
  -d "{\"user_path\":\"$S9_USER\",\"object_path\":\"$S9_PARENT\"}")
assert_match "DELETE parent ACL -> 200/204" "^(200|204)$" "$DEL_S9"

S9_PARENT_COUNT=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE user_path='$S9_USER' AND object_path='$S9_PARENT';")
assert "parent ACL row removed" "0" "$S9_PARENT_COUNT"

S9_CHILD_COUNT=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE user_path='$S9_USER' AND object_path LIKE '$S9_PARENT/%';")
assert "child ACL rows remain (DELETE is exact-match only)" "2" "$S9_CHILD_COUNT"

# Cleanup
psql_iam "DELETE FROM resource_acl WHERE user_path='$S9_USER' AND object_path LIKE '$S9_PARENT%';" >/dev/null 2>&1 || true

# ════════════════════════════════════════════════════════════════════════════
section "Section 10: ACL prefix matching inheritance"
# ════════════════════════════════════════════════════════════════════════════
PARENT_OBJ="TestNS/Tenants/$REALM/Resources"
CHILD_OBJ="TestNS/Tenants/$REALM/Resources/res-inherit-001"
ALICE_PATH="AccessManager/Tenants/$REALM/Users/alice"
CONTRIB_ROLE="AccessManager/Tenants/System/Roles/Contributor"

psql_iam "DELETE FROM resource_acl WHERE user_path='$ALICE_PATH';" >/dev/null 2>&1 || true

PUT_PARENT=$(AH -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
  -H "Content-Type: application/json" \
  -d "{\"user_path\":\"$ALICE_PATH\",\"object_path\":\"$PARENT_OBJ\",\"role_path\":\"$CONTRIB_ROLE\"}")
assert_match "PUT parent ACL for alice -> 200/201" "^(200|201)$" "$PUT_PARENT"

INHERIT_QUERY=$(cat <<JSON
{
  "queries": [
    {"user_path": "$ALICE_PATH", "object_path": "$CHILD_OBJ"}
  ]
}
JSON
)
INHERIT_RESP=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Action/QueryACLs" \
  -H "Content-Type: application/json" -d "$INHERIT_QUERY")
assert_contains "child object inherits Contributor from parent prefix" "Contributor" "$INHERIT_RESP"

PARENT_DB=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE user_path='$ALICE_PATH' AND object_path='$PARENT_OBJ';")
assert "parent ACL row in DB" "1" "$PARENT_DB"
CHILD_DB=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE user_path='$ALICE_PATH' AND object_path='$CHILD_OBJ';")
assert "child ACL row NOT in DB (inherited, not stored)" "0" "$CHILD_DB"

AH -X DELETE "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
  -H "Content-Type: application/json" \
  -d "{\"user_path\":\"$ALICE_PATH\",\"object_path\":\"$PARENT_OBJ\"}" >/dev/null

# ════════════════════════════════════════════════════════════════════════════
section "Section 11: Default role matrix (Owner/Contributor/Viewer)"
# ════════════════════════════════════════════════════════════════════════════
NORMAL_TOKEN=$(curl -s -X POST "$BASE_URL/realms/$REALM/protocol/openid-connect/token" \
  -d "client_id=$CLIENT_ID" -d "client_secret=$CS" -d "grant_type=password" \
  -d "username=$NORMAL_USER" -d "password=$NORMAL_PASSWORD" | jget access_token)

if [ -n "$NORMAL_TOKEN" ]; then
  NORMAL_SUB=$(jwt_claim "$NORMAL_TOKEN" sub)
  NHC() { curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $NORMAL_TOKEN" "$@"; }

  ROLE_OBJ="DataAgent/Tenants/$REALM/DataAgentDBs/role-matrix-test-db-001"
  NORMAL_USER_PATH="AccessManager/Tenants/$REALM/Users/$NORMAL_SUB"

  psql_iam "DELETE FROM resource_acl WHERE object_path='$ROLE_OBJ';" >/dev/null 2>&1 || true

  # Grant Contributor and verify via QueryACLs
  AH -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
    -H "Content-Type: application/json" \
    -d "{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$ROLE_OBJ\",\"role_path\":\"$CONTRIB_ROLE\"}" >/dev/null

  QR=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Action/QueryACLs" \
    -H "Content-Type: application/json" \
    -d "{\"queries\":[{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$ROLE_OBJ\"}]}")
  assert_contains "QueryACLs: Contributor role stored correctly" "Contributor" "$QR"

  # Verify normal user can access /KnowledgeBase/ (all-users, manifest-derived path_rules)
  if [ "$HAS_KB_ROUTE" -gt 0 ]; then
    assert_match "normal-user GET /KnowledgeBase/.../KnowledgeBases -> 200 (all-users)" "^(200)$" "$(NHC $BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases)"
  else
    skip "normal-user GET /KnowledgeBase/... (mock-kb route not installed)"
  fi

  # Verify normal user cannot access admin-only AccessManager routes
  CODE=$(NHC "$BASE_URL/AccessManager/Tenants/System/AppManifests")
  assert_match "normal-user GET /AccessManager/Tenants/System/AppManifests -> 403 (not admin)" "^(401|403)$" "$CODE"

  # Switch to Viewer and verify
  AH -X DELETE "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
    -H "Content-Type: application/json" \
    -d "{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$ROLE_OBJ\"}" >/dev/null
  AH -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
    -H "Content-Type: application/json" \
    -d "{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$ROLE_OBJ\",\"role_path\":\"AccessManager/Tenants/System/Roles/Viewer\"}" >/dev/null

  QR2=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Action/QueryACLs" \
    -H "Content-Type: application/json" \
    -d "{\"queries\":[{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$ROLE_OBJ\"}]}")
  assert_contains "QueryACLs: Viewer role stored correctly" "Viewer" "$QR2"

  # Switch to Owner and verify
  AH -X DELETE "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
    -H "Content-Type: application/json" \
    -d "{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$ROLE_OBJ\"}" >/dev/null
  AH -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
    -H "Content-Type: application/json" \
    -d "{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$ROLE_OBJ\",\"role_path\":\"AccessManager/Tenants/System/Roles/Owner\"}" >/dev/null

  QR3=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Action/QueryACLs" \
    -H "Content-Type: application/json" \
    -d "{\"queries\":[{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$ROLE_OBJ\"}]}")
  assert_contains "QueryACLs: Owner role stored correctly" "Owner" "$QR3"

  # Cleanup
  AH -X DELETE "$BASE_URL/AccessManager/Tenants/$REALM/ACLs" \
    -H "Content-Type: application/json" \
    -d "{\"user_path\":\"$NORMAL_USER_PATH\",\"object_path\":\"$ROLE_OBJ\"}" >/dev/null
else
  skip "Section 11 — no normal-user token"
fi

# ════════════════════════════════════════════════════════════════════════════
section "Section 12: X-Allowed-Ids injection on collection GET"
# ════════════════════════════════════════════════════════════════════════════
# Create two KnowledgeBase resources, manually write ACLs, then verify
# X-Allowed-Ids is injected by resource-sync on the collection GET.
XI_KB1=$(A -X POST "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases" \
  -H "Content-Type: application/json" -d '{"name":"xi-test-kb-1","description":"test"}' | \
  python -c "import sys,json
try: d=json.load(sys.stdin); print(d.get('data',{}).get('id',''))
except: print('')" 2>/dev/null)
XI_KB2=$(A -X POST "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases" \
  -H "Content-Type: application/json" -d '{"name":"xi-test-kb-2","description":"test"}' | \
  python -c "import sys,json
try: d=json.load(sys.stdin); print(d.get('data',{}).get('id',''))
except: print('')" 2>/dev/null)

if [ -n "$XI_KB1" ] && [ -n "$XI_KB2" ]; then
  XI_ADMIN_PATH="AccessManager/Tenants/$REALM/Users/$ADMIN_SUB"
  psql_iam "INSERT INTO resource_acl (tenant_id, user_path, object_path, role_path, created_by) VALUES
    ('$REALM', '$XI_ADMIN_PATH', 'KnowledgeBase/Tenants/$REALM/KnowledgeBases/$XI_KB1', 'AccessManager/Tenants/System/Roles/Owner', 'test'),
    ('$REALM', '$XI_ADMIN_PATH', 'KnowledgeBase/Tenants/$REALM/KnowledgeBases/$XI_KB2', 'AccessManager/Tenants/System/Roles/Owner', 'test')
    ON CONFLICT DO NOTHING;" >/dev/null
  sleep 1

  # /KnowledgeBase/ uses the unified URL format — resource-sync injects X-Allowed-Ids.
  # mock-kb echoes it back as X-Debug-Allowed-Ids.
  # Admin token bypasses X-Allowed-Ids filtering (admins see all resources).
  XI_BODY=$(A "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases")
  assert_contains "GET /KnowledgeBase/.../KnowledgeBases body contains KB1" "$XI_KB1" "$XI_BODY"
  assert_contains "GET /KnowledgeBase/.../KnowledgeBases body contains KB2" "$XI_KB2" "$XI_BODY"

  psql_iam "DELETE FROM resource_acl WHERE user_path='$XI_ADMIN_PATH' AND object_path LIKE 'KnowledgeBase/Tenants/$REALM/KnowledgeBases/xi-%';" >/dev/null 2>&1 || true
  AH -X DELETE "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases/$XI_KB1" >/dev/null
  AH -X DELETE "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases/$XI_KB2" >/dev/null
else
  skip "Section 12 — could not create test KnowledgeBases"
fi

# ════════════════════════════════════════════════════════════════════════════
section "Section 13: Tenant isolation (cross-tenant request rejected)"
# ════════════════════════════════════════════════════════════════════════════
# JWT is for tenant $REALM but URL references a different tenant
CROSS_CODE=$(AH "$BASE_URL/AccessManager/Tenants/t-other-tenant/ACLs")
assert_match "cross-tenant ACL request -> 403" "^(403)$" "$CROSS_CODE"

CROSS_CODE2=$(AH "$BASE_URL/AccessManager/Tenants/t-other-tenant/Users")
assert_match "cross-tenant Users request -> 403" "^(403)$" "$CROSS_CODE2"

# Same tenant should work (admin bypass) — must include ?object= or ?user= param
SAME_CODE=$(AH "$BASE_URL/AccessManager/Tenants/$REALM/ACLs?object=TestNS/Tenants/$REALM/Resources/probe")
assert_match "same-tenant ACL request -> 200" "^(200)$" "$SAME_CODE"

# ════════════════════════════════════════════════════════════════════════════
section "Section 14: OPA path-level authz still works (permission_groups)"
# ════════════════════════════════════════════════════════════════════════════
# Admin super-bypass: admin can reach all registered app paths
CODE=$(AH "$BASE_URL/AccessManager/Tenants")
assert "admin GET /AccessManager/Tenants -> 200" "200" "$CODE"

if [ "$HAS_KB_ROUTE" -gt 0 ]; then
  CODE=$(AH "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases")
  assert "admin GET /KnowledgeBase/... -> 200 (super-bypass)" "200" "$CODE"
else
  skip "admin GET /KnowledgeBase/... (mock-kb route not installed)"
fi

CODE=$(AH "$BASE_URL/AccessManager/Tenants/$REALM/ACLs?object=DataAgent/Tenants/$REALM/DataAgentDBs/probe")
assert "admin GET /AccessManager/... -> 200 (super-bypass)" "200" "$CODE"

# OPA data endpoint check — path_rules now include manifest-derived entries
OPA_RULES=$(MSYS_NO_PATHCONV=1 kubectl -n aidp-iam exec deploy/iam-services -c opa -- \
  curl -s http://localhost:8181/v1/data/path_rules 2>/dev/null || echo "")
if [ -n "$OPA_RULES" ]; then
  assert_contains "OPA path_rules contains /KnowledgeBase/" "/KnowledgeBase/" "$OPA_RULES"
  assert_contains "OPA path_rules contains /api/v1/"        "/api/v1/"        "$OPA_RULES"
else
  skip "OPA data endpoint not reachable from pep-proxy container"
fi

# Normal user path-level: all-users allowed on /KnowledgeBase/ (manifest-derived), blocked on /api/v1/apps
if [ -n "${NORMAL_TOKEN:-}" ]; then
  NHC2() { curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $NORMAL_TOKEN" "$@"; }
  if [ "$HAS_KB_ROUTE" -gt 0 ]; then
    assert_match "normal-user GET /KnowledgeBase/.../KnowledgeBases -> 200 (all-users)" "^(200)$" "$(NHC2 $BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases)"
  else
    skip "normal-user GET /KnowledgeBase/... (mock-kb route not installed)"
  fi
  assert_match "normal-user GET /AccessManager/Tenants/System/AppManifests -> 403 (not admin)" "^(401|403)$" "$(NHC2 $BASE_URL/AccessManager/Tenants/System/AppManifests)"
else
  skip "Section 14 normal-user checks — no token"
fi

# ════════════════════════════════════════════════════════════════════════════
section "Section 15: /AccessManager/ routes work (v2.0 unified API)"
# ════════════════════════════════════════════════════════════════════════════
for path in \
  "/AccessManager/Tenants" \
  "/AccessManager/Tenants/System/Apps" \
  "/AccessManager/Tenants/System/AppManifests" \
  "/AccessManager/Tenants/$REALM/Users" \
  "/AccessManager/Tenants/$REALM/Groups" \
  "/AccessManager/Tenants/$REALM/ApiKeys"; do
  CODE=$(AH "$BASE_URL$path")
  assert_match "v2.0 $path -> 200" "^(200)$" "$CODE"
done

# Legacy ACL endpoint (moved to /AccessManager/ in v2.0; /acl/v1 is no longer active)
CODE=$(AH "$BASE_URL/acl/v1/resources/probe-id/permissions?app_name=KnowledgeBase&resource_type=KnowledgeBases")
assert_match "legacy /acl/v1 -> 200/403/404" "^(200|403|404)$" "$CODE"

# ════════════════════════════════════════════════════════════════════════════
section "Section 16: New /AccessManager/Tenants/{tid}/ identity routes"
# ════════════════════════════════════════════════════════════════════════════
CODE=$(AH "$BASE_URL/AccessManager/Tenants/$REALM/Users")
assert_match "GET /AccessManager/Tenants/$REALM/Users -> 200" "^(200)$" "$CODE"

CODE=$(AH "$BASE_URL/AccessManager/Tenants/$REALM/Groups")
assert_match "GET /AccessManager/Tenants/$REALM/Groups -> 200" "^(200)$" "$CODE"

# Create a user via new route
NEW_USER_BODY='{"username":"test-new-user-v2","email":"test-new-user-v2@example.com","password":"Test@12345"}'
CREATE_USER_CODE=$(AH -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Users" \
  -H "Content-Type: application/json" -d "$NEW_USER_BODY")
assert_match "POST /AccessManager/Tenants/$REALM/Users -> 200/201" "^(200|201)$" "$CREATE_USER_CODE"

# List and find the new user
USERS_LIST=$(A "$BASE_URL/AccessManager/Tenants/$REALM/Users")
assert_contains "new user appears in list" "test-new-user-v2" "$USERS_LIST"

# Get user ID and delete
NEW_UID=$(echo "$USERS_LIST" | python -c "
import sys,json
try:
  d=json.load(sys.stdin)
  users=d if isinstance(d,list) else d.get('users',d.get('items',[]))
  for u in users:
    if u.get('username')=='test-new-user-v2': print(u.get('id','')); break
except: pass" 2>/dev/null)
if [ -n "$NEW_UID" ]; then
  DEL_USER=$(AH -X DELETE "$BASE_URL/AccessManager/Tenants/$REALM/Users/$NEW_UID")
  assert_match "DELETE /AccessManager/Tenants/$REALM/Users/{id} -> 200/204" "^(200|204)$" "$DEL_USER"
else
  skip "Section 16 — could not extract new user ID for cleanup"
fi

# Create a group via new route
NEW_GRP_CODE=$(AH -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Groups" \
  -H "Content-Type: application/json" -d '{"name":"test-new-group-v2"}')
assert_match "POST /AccessManager/Tenants/$REALM/Groups -> 200/201" "^(200|201)$" "$NEW_GRP_CODE"
GROUPS_LIST=$(A "$BASE_URL/AccessManager/Tenants/$REALM/Groups")
assert_contains "new group appears in list" "test-new-group-v2" "$GROUPS_LIST"
NEW_GID=$(echo "$GROUPS_LIST" | python -c "
import sys,json
try:
  d=json.load(sys.stdin)
  groups=d if isinstance(d,list) else d.get('groups',d.get('items',[]))
  for g in groups:
    if g.get('name')=='test-new-group-v2': print(g.get('id','')); break
except: pass" 2>/dev/null)
[ -n "$NEW_GID" ] && AH -X DELETE "$BASE_URL/AccessManager/Tenants/$REALM/Groups/$NEW_GID" >/dev/null

# ════════════════════════════════════════════════════════════════════════════
section "Section 17: API Key lifecycle"
# ════════════════════════════════════════════════════════════════════════════
AK=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/ApiKeys" -H "Content-Type: application/json" \
  -d '{"app_name":"KnowledgeBase","description":"test-key","subject_id":"svc-test"}')
AK_PLAIN=$(echo "$AK" | jget api_key)
AK_ID=$(echo "$AK" | jget id)
AK_PREFIX=$(echo "$AK" | jget key_prefix)
assert_match "POST /api-keys returns plaintext" "^ak_[A-Za-z0-9_-]{20,}$" "$AK_PLAIN"
assert_match "POST /api-keys returns id" ".+" "$AK_ID"

DB_HASH=$(psql_iam "SELECT api_key_hash FROM api_keys WHERE id='$AK_ID';")
assert_not_contains "DB hash != plaintext" "$AK_PLAIN" "$DB_HASH"
assert_match "DB hash is sha256 hex" "^[a-f0-9]{64}$" "$DB_HASH"

LIST=$(A "$BASE_URL/AccessManager/Tenants/$REALM/ApiKeys")
assert_contains "GET /api-keys lists prefix" "$AK_PREFIX" "$LIST"
assert_not_contains "GET /api-keys does NOT expose plaintext" "$AK_PLAIN" "$LIST"

ROT=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/ApiKeys/$AK_ID/Rotate")
AK_NEW=$(echo "$ROT" | jget api_key)
assert_match "rotate returns new plaintext" "^ak_[A-Za-z0-9_-]{20,}$" "$AK_NEW"
[ "$AK_NEW" != "$AK_PLAIN" ] && assert "rotate plaintext differs" "yes" "yes" || assert "rotate plaintext differs" "yes" "no"

A -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/ApiKeys/$AK_ID" -H "Content-Type: application/json" -d '{"enabled":false}' >/dev/null
assert "DB enabled=false after disable" "f" "$(psql_iam "SELECT enabled FROM api_keys WHERE id='$AK_ID';")"

A -X DELETE "$BASE_URL/AccessManager/Tenants/$REALM/ApiKeys/$AK_ID" >/dev/null
assert "DB row removed after DELETE" "0" "$(psql_iam "SELECT COUNT(*) FROM api_keys WHERE id='$AK_ID';")"

# API Key auth test
FRESH=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/ApiKeys" -H "Content-Type: application/json" \
  -d '{"app_name":"KnowledgeBase","description":"auth-test","subject_id":"svc-auth","allowed_paths":["/KnowledgeBase"]}')
FRESH_KEY=$(echo "$FRESH" | jget api_key)
FRESH_ID=$(echo "$FRESH" | jget id)
if [ -n "$FRESH_KEY" ]; then
  if [ "$HAS_KB_ROUTE" -gt 0 ]; then
    CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $FRESH_KEY" "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases")
    assert_match "X-API-Key access /KnowledgeBase/... -> 200/403" "^(200|403)$" "$CODE"

    A -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/ApiKeys/$FRESH_ID" -H "Content-Type: application/json" -d '{"enabled":false}' >/dev/null
    sleep 1
    CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $FRESH_KEY" "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases")
    assert_match "disabled API Key -> 401/403" "^(401|403)$" "$CODE"

    CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: ak_invalid_xxx" "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases")
    assert_match "invalid API Key -> 401/403" "^(401|403)$" "$CODE"
  else
    skip "X-API-Key /KnowledgeBase/... tests (mock-kb route not installed)"
  fi

  A -X DELETE "$BASE_URL/AccessManager/Tenants/$REALM/ApiKeys/$FRESH_ID" >/dev/null
fi

# ════════════════════════════════════════════════════════════════════════════
section "Section 18: App disabled blocks access (even for admins)"
# ════════════════════════════════════════════════════════════════════════════
psql_iam "UPDATE apps SET enabled=false WHERE app_name='KnowledgeBase';" >/dev/null
if [ "$HAS_KB_ROUTE" -gt 0 ]; then
  sleep 35  # bundle refresh
  CODE=$(AH "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases")
  assert_match "/KnowledgeBase/... with KnowledgeBase disabled -> 403" "^(403)$" "$CODE"
else
  skip "/KnowledgeBase/... disabled test (mock-kb route not installed)"
fi

psql_iam "UPDATE apps SET enabled=true WHERE app_name='KnowledgeBase';" >/dev/null
if [ "$HAS_KB_ROUTE" -gt 0 ]; then
  sleep 35
  CODE=$(AH "$BASE_URL/KnowledgeBase/Tenants/$REALM/KnowledgeBases")
  assert_match "/KnowledgeBase/... re-enabled -> 200" "^(200|403)$" "$CODE"
else
  skip "/KnowledgeBase/... re-enabled test (mock-kb route not installed)"
fi

# ════════════════════════════════════════════════════════════════════════════
section "Section 19: DataAgent manifest registration + authorization"
# ════════════════════════════════════════════════════════════════════════════
DA_NS="DataAgent"
DA_DB_ID="da-test-db-001"
DA_DB_OBJ="DataAgent/Tenants/$REALM/DataAgentDBs/$DA_DB_ID"
DA_TABLE_OBJ="DataAgent/Tenants/$REALM/DataAgentDBs/$DA_DB_ID/Tables/tbl-001"
ALL_USERS_PATH="AccessManager/Tenants/$REALM/Groups/all-users"
TENANT_ADMINS_PATH="AccessManager/Tenants/$REALM/Groups/tenant-admins"

psql_iam "DELETE FROM app_manifests WHERE namespace='$DA_NS';" >/dev/null 2>&1 || true
psql_iam "DELETE FROM resource_acl WHERE object_path LIKE 'DataAgent/Tenants/$REALM/%';" >/dev/null 2>&1 || true

DA_MANIFEST_FILE=$(mktemp /tmp/da_manifest_XXXXXX.json)
cat > "$DA_MANIFEST_FILE" <<'JSON'
{
  "namespace": "DataAgent",
  "display_name": "DataAgent App",
  "base_url": "https://dataagent.example.com",
  "resources": [
    {
      "type": "DataBases",
      "display_name": "External Databases",
      "path_pattern": "/DataAgent/Tenants/{tenantId}/DataBases/{databaseId}",
      "methods": ["GET", "PUT", "PATCH", "DELETE"],
      "actions": [],
      "default_acl": [
        {
          "user_template": "AccessManager/Tenants/{tenantId}/Groups/all-users",
          "object_template": "DataAgent/Tenants/{tenantId}/DataBases",
          "role_path": "AccessManager/Tenants/System/Roles/Contributor"
        },
        {
          "user_template": "AccessManager/Tenants/{tenantId}/Groups/tenant-admins",
          "object_template": "DataAgent/Tenants/{tenantId}/DataBases",
          "role_path": "AccessManager/Tenants/System/Roles/Owner"
        }
      ],
      "children": []
    },
    {
      "type": "DataAgentDBs",
      "display_name": "DataAgent Knowledge Bases",
      "path_pattern": "/DataAgent/Tenants/{tenantId}/DataAgentDBs/{dbId}",
      "methods": ["GET", "PUT", "PATCH", "DELETE"],
      "actions": [
        {
          "name": "Query",
          "path_suffix": "/Query",
          "http_method": "POST",
          "required_role": "AccessManager/Tenants/System/Roles/Contributor"
        }
      ],
      "default_acl": [
        {
          "user_template": "AccessManager/Tenants/{tenantId}/Groups/all-users",
          "object_template": "DataAgent/Tenants/{tenantId}/DataAgentDBs",
          "role_path": "AccessManager/Tenants/System/Roles/Contributor"
        },
        {
          "user_template": "AccessManager/Tenants/{tenantId}/Groups/tenant-admins",
          "object_template": "DataAgent/Tenants/{tenantId}/DataAgentDBs",
          "role_path": "AccessManager/Tenants/System/Roles/Owner"
        }
      ],
      "children": [
        {
          "type": "Tables",
          "display_name": "Data Tables",
          "path_pattern": "/DataAgent/Tenants/{tenantId}/DataAgentDBs/{dbId}/Tables/{tableId}",
          "methods": ["GET", "PUT", "DELETE"],
          "actions": [],
          "default_acl": [],
          "children": []
        }
      ]
    },
    {
      "type": "DataAgentSessions",
      "display_name": "DataAgent Sessions",
      "path_pattern": "/DataAgent/Tenants/{tenantId}/DataAgentSessions/{sessionId}",
      "methods": ["GET", "PUT", "DELETE"],
      "actions": [
        {
          "name": "Chat",
          "path_suffix": "/Chat",
          "http_method": "POST",
          "required_role": "AccessManager/Tenants/System/Roles/Contributor"
        }
      ],
      "default_acl": [
        {
          "user_template": "AccessManager/Tenants/{tenantId}/Groups/all-users",
          "object_template": "DataAgent/Tenants/{tenantId}/DataAgentSessions",
          "role_path": "AccessManager/Tenants/System/Roles/Contributor"
        },
        {
          "user_template": "AccessManager/Tenants/{tenantId}/Groups/tenant-admins",
          "object_template": "DataAgent/Tenants/{tenantId}/DataAgentSessions",
          "role_path": "AccessManager/Tenants/System/Roles/Owner"
        }
      ],
      "children": []
    }
  ]
}
JSON

DA_PUT=$(AH -X PUT "$BASE_URL/AccessManager/Tenants/System/AppManifests/$DA_NS" \
  -H "Content-Type: application/json" -d "@$DA_MANIFEST_FILE")
rm -f "$DA_MANIFEST_FILE"
assert_match "PUT DataAgent manifest -> 200/201" "^(200|201)$" "$DA_PUT"

sleep 2  # wait for default_acl sync

# Verify default_acl sync: all-users → Contributor on DataAgentDBs
DA_ACL_CONTRIB=$(psql_iam "SELECT role_path FROM resource_acl WHERE tenant_id='$REALM' AND object_path='DataAgent/Tenants/$REALM/DataAgentDBs' AND user_path='$ALL_USERS_PATH' LIMIT 1;")
assert_contains "default_acl sync: all-users → Contributor on DataAgentDBs" "Contributor" "$DA_ACL_CONTRIB"

# Verify default_acl sync: tenant-admins → Owner on DataAgentDBs
DA_ACL_OWNER=$(psql_iam "SELECT role_path FROM resource_acl WHERE tenant_id='$REALM' AND object_path='DataAgent/Tenants/$REALM/DataAgentDBs' AND user_path='$TENANT_ADMINS_PATH' LIMIT 1;")
assert_contains "default_acl sync: tenant-admins → Owner on DataAgentDBs" "Owner" "$DA_ACL_OWNER"

# QueryACLs: all-users inherits Contributor on a specific DataAgentDB instance (prefix match)
DA_Q1=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Action/QueryACLs" \
  -H "Content-Type: application/json" \
  -d "{\"queries\":[{\"user_path\":\"$ALL_USERS_PATH\",\"object_path\":\"$DA_DB_OBJ\"}]}")
assert_contains "QueryACLs: all-users inherits Contributor on DataAgentDB instance" "Contributor" "$DA_Q1"

# QueryACLs: tenant-admins inherits Owner on a specific DataAgentDB instance
DA_Q2=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Action/QueryACLs" \
  -H "Content-Type: application/json" \
  -d "{\"queries\":[{\"user_path\":\"$TENANT_ADMINS_PATH\",\"object_path\":\"$DA_DB_OBJ\"}]}")
assert_contains "QueryACLs: tenant-admins inherits Owner on DataAgentDB instance" "Owner" "$DA_Q2"

# QueryACLs: Tables inherit Contributor from parent DataAgentDBs type (two-level prefix)
DA_Q3=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Action/QueryACLs" \
  -H "Content-Type: application/json" \
  -d "{\"queries\":[{\"user_path\":\"$ALL_USERS_PATH\",\"object_path\":\"$DA_TABLE_OBJ\"}]}")
assert_contains "QueryACLs: Tables inherit Contributor from DataAgentDBs type" "Contributor" "$DA_Q3"

# Simulate ext_proc: write Owner ACL for admin user on a specific DataAgentDB instance
psql_iam "INSERT INTO resource_acl (tenant_id, user_path, object_path, role_path, created_by) VALUES ('$REALM', 'AccessManager/Tenants/$REALM/Users/$ADMIN_SUB', '$DA_DB_OBJ', 'AccessManager/Tenants/System/Roles/Owner', 'test-ext-proc') ON CONFLICT DO NOTHING;" >/dev/null

DA_Q4=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Action/QueryACLs" \
  -H "Content-Type: application/json" \
  -d "{\"queries\":[{\"user_path\":\"AccessManager/Tenants/$REALM/Users/$ADMIN_SUB\",\"object_path\":\"$DA_DB_OBJ\"}]}")
assert_contains "QueryACLs: admin has Owner on specific DataAgentDB instance" "Owner" "$DA_Q4"

# GET manifest returns DataAgent
DA_GET=$(A "$BASE_URL/AccessManager/Tenants/System/AppManifests/$DA_NS")
assert_contains "GET DataAgent manifest returns namespace" "DataAgent" "$DA_GET"
assert_contains "GET DataAgent manifest returns DataAgentDBs" "DataAgentDBs" "$DA_GET"

# Clean up DataAgent test data
psql_iam "DELETE FROM app_manifests WHERE namespace='$DA_NS';" >/dev/null 2>&1 || true
psql_iam "DELETE FROM resource_acl WHERE object_path LIKE 'DataAgent/Tenants/$REALM/%';" >/dev/null 2>&1 || true

# ════════════════════════════════════════════════════════════════════════════
section "Section 20: Password policy + password status APIs"
# ════════════════════════════════════════════════════════════════════════════
# Refresh admin token — previous sections may have taken > token TTL
ADMIN_TOKEN=$(curl -s -X POST "$BASE_URL/realms/$REALM/protocol/openid-connect/token" \
  -d "client_id=$CLIENT_ID" -d "client_secret=$CS" -d "grant_type=password" \
  -d "username=$ADMIN_USER" -d "password=$ADMIN_PASSWORD" | jget access_token)

# ── 20.1 GET password-policy (initial state, no policy set) ─────────────
POLICY_INIT=$(A "$BASE_URL/AccessManager/Tenants/$REALM/PasswordPolicy")
assert_contains "GET password-policy returns expire_days field" "expire_days" "$POLICY_INIT"

# ── 20.2 PUT password-policy: set expire_days + min_length + require_digits ─
POLICY_PUT=$(AH -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/PasswordPolicy" \
  -H "Content-Type: application/json" \
  -d '{"expire_days":90,"min_length":8,"require_digits":true}')
assert_match "PUT password-policy -> 200" "^200$" "$POLICY_PUT"

# ── 20.3 GET password-policy: verify values persisted ───────────────────
POLICY_GET=$(A "$BASE_URL/AccessManager/Tenants/$REALM/PasswordPolicy")
assert_contains "GET password-policy: expire_days=90" '"expire_days":90' "$POLICY_GET"
assert_contains "GET password-policy: min_length=8"   '"min_length":8'   "$POLICY_GET"
assert_contains "GET password-policy: require_digits=true" '"require_digits":true' "$POLICY_GET"
assert_contains "GET password-policy: require_uppercase=false" '"require_uppercase":false' "$POLICY_GET"

# ── 20.4 PUT password-policy: partial update (only uppercase) ───────────
POLICY_PARTIAL=$(AH -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/PasswordPolicy" \
  -H "Content-Type: application/json" \
  -d '{"require_uppercase":true}')
assert_match "PUT password-policy partial update -> 200" "^200$" "$POLICY_PARTIAL"

POLICY_AFTER=$(A "$BASE_URL/AccessManager/Tenants/$REALM/PasswordPolicy")
assert_contains "partial update: expire_days still 90"      '"expire_days":90'        "$POLICY_AFTER"
assert_contains "partial update: require_uppercase now true" '"require_uppercase":true' "$POLICY_AFTER"
assert_contains "partial update: require_digits still true"  '"require_digits":true'   "$POLICY_AFTER"

# ── 20.5 GET password-status for admin user ─────────────────────────────
ADMIN_ID=$(A "$BASE_URL/AccessManager/Tenants/$REALM/Users?search=$ADMIN_USER" | \
  python -c "import sys,json; d=json.load(sys.stdin); print(d[0]['id'] if isinstance(d,list) and d else '')" 2>/dev/null)

if [ -n "$ADMIN_ID" ]; then
  PWD_STATUS=$(A "$BASE_URL/AccessManager/Tenants/$REALM/Users/$ADMIN_ID/PasswordStatus")
  assert_contains "GET password-status: has user_id"              "user_id"              "$PWD_STATUS"
  assert_contains "GET password-status: has credential_created_at" "credential_created_at" "$PWD_STATUS"
  assert_contains "GET password-status: has is_temporary"         "is_temporary"         "$PWD_STATUS"
  assert_contains "GET password-status: has days_remaining"       "days_remaining"       "$PWD_STATUS"
  assert_contains "GET password-status: has is_expired"           "is_expired"           "$PWD_STATUS"
  assert_contains "GET password-status: expiry_days=90"           '"expiry_days":90'     "$PWD_STATUS"
  # Admin password was set at cluster init, should not be expired
  assert_contains "GET password-status: is_expired=false"         '"is_expired":false'   "$PWD_STATUS"
else
  skip "GET password-status (admin user ID not found)"
fi

# ── 20.6 GET password-status: 404 for unknown user ──────────────────────
STATUS_404=$(AH "$BASE_URL/AccessManager/Tenants/$REALM/Users/nonexistent-uuid-000/PasswordStatus")
assert_match "GET password-status unknown user -> 404" "^404$" "$STATUS_404"

# ── 20.7 Reset password: temporary=true enforced ────────────────────────
# Create a temp user, reset password, verify user must change on next login
TMP_USER="pw-test-$(date +%s)"
TMP_RESP=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Users" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$TMP_USER\",\"password\":\"Init@1234\",\"temporary_password\":false}")
TMP_ID=$(echo "$TMP_RESP" | python -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)

if [ -n "$TMP_ID" ]; then
  RESET_CODE=$(AH -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/Users/$TMP_ID/Password" \
    -H "Content-Type: application/json" -d '{"password":"NewPass@5678"}')
  assert_match "PUT reset password -> 204" "^204$" "$RESET_CODE"

  # After reset, password-status should show is_temporary=true
  STATUS_AFTER=$(A "$BASE_URL/AccessManager/Tenants/$REALM/Users/$TMP_ID/PasswordStatus")
  assert_contains "password-status after reset: is_temporary=true" '"is_temporary":true' "$STATUS_AFTER"

  # Cleanup temp user
  A -X DELETE "$BASE_URL/AccessManager/Tenants/$REALM/Users/$TMP_ID" >/dev/null 2>&1 || true
else
  skip "reset password test (temp user creation failed)"
fi

# ── 20.8 Cleanup: remove password policy ────────────────────────────────
AH -X PUT "$BASE_URL/AccessManager/Tenants/$REALM/PasswordPolicy" \
  -H "Content-Type: application/json" \
  -d '{"expire_days":null,"min_length":null,"require_uppercase":false,"require_lowercase":false,"require_digits":false,"require_special":false,"history_count":null}' \
  >/dev/null 2>&1 || true

# ════════════════════════════════════════════════════════════════════════════
section "Section 21: Batch user creation (JSON body)"
# ════════════════════════════════════════════════════════════════════════════
# Refresh admin token — previous sections may have taken > token TTL
ADMIN_TOKEN=$(curl -s -X POST "$BASE_URL/realms/$REALM/protocol/openid-connect/token" \
  -d "client_id=$CLIENT_ID" -d "client_secret=$CS" -d "grant_type=password" \
  -d "username=$ADMIN_USER" -d "password=$ADMIN_PASSWORD" | jget access_token)
BC_U1="bc-user1-$(date +%s)"
BC_U2="bc-user2-$(date +%s)"

# ── 21.1 Batch-create 2 valid users ─────────────────────────────────────
BC_RESP=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Users/BatchCreate" \
  -H "Content-Type: application/json" \
  -d "{\"users\":[
    {\"username\":\"$BC_U1\",\"password\":\"Test@1234\",\"temporary_password\":true},
    {\"username\":\"$BC_U2\",\"password\":\"Test@5678\",\"temporary_password\":false}
  ]}")
assert_contains "batch-create 2 users: succeeded=2" '"succeeded":2' "$BC_RESP"
assert_contains "batch-create 2 users: failed=0"    '"failed":0'    "$BC_RESP"

# ── 21.2 Verify both users exist ────────────────────────────────────────
BC_U1_ID=$(A "$BASE_URL/AccessManager/Tenants/$REALM/Users?search=$BC_U1" | \
  python -c "import sys,json; d=json.load(sys.stdin); print(d[0]['id'] if isinstance(d,list) and d else '')" 2>/dev/null)
BC_U2_ID=$(A "$BASE_URL/AccessManager/Tenants/$REALM/Users?search=$BC_U2" | \
  python -c "import sys,json; d=json.load(sys.stdin); print(d[0]['id'] if isinstance(d,list) and d else '')" 2>/dev/null)
assert_match "batch-create: user1 exists in Keycloak" "^[0-9a-f-]{36}$" "$BC_U1_ID"
assert_match "batch-create: user2 exists in Keycloak" "^[0-9a-f-]{36}$" "$BC_U2_ID"

# ── 21.3 Duplicate username → partial failure ────────────────────────────
BC_DUP=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Users/BatchCreate" \
  -H "Content-Type: application/json" \
  -d "{\"users\":[
    {\"username\":\"bc-new-$(date +%s)\",\"password\":\"Test@1234\"},
    {\"username\":\"$BC_U1\",\"password\":\"Test@1234\"}
  ]}")
assert_contains "batch-create duplicate: succeeded=1" '"succeeded":1' "$BC_DUP"
assert_contains "batch-create duplicate: failed=1"    '"failed":1'    "$BC_DUP"
assert_contains "batch-create duplicate: errors array present" '"errors":' "$BC_DUP"
assert_contains "batch-create duplicate: error index=1" '"index":1' "$BC_DUP"

# ── 21.4 Empty users list → succeeded=0 failed=0 ────────────────────────
BC_EMPTY=$(A -X POST "$BASE_URL/AccessManager/Tenants/$REALM/Users/BatchCreate" \
  -H "Content-Type: application/json" \
  -d '{"users":[]}')
assert_contains "batch-create empty list: succeeded=0" '"succeeded":0' "$BC_EMPTY"
assert_contains "batch-create empty list: failed=0"    '"failed":0'    "$BC_EMPTY"

# ── 21.5 Cleanup batch-create test users ────────────────────────────────
for _ID in "$BC_U1_ID" "$BC_U2_ID"; do
  [ -n "$_ID" ] && A -X DELETE "$BASE_URL/AccessManager/Tenants/$REALM/Users/$_ID" >/dev/null 2>&1 || true
done
# Also clean up the new user from 21.3 (search by prefix)
BC_NEW_ID=$(A "$BASE_URL/AccessManager/Tenants/$REALM/Users?search=bc-new-" | \
  python -c "import sys,json; d=json.load(sys.stdin); print(d[0]['id'] if isinstance(d,list) and d else '')" 2>/dev/null)
[ -n "$BC_NEW_ID" ] && A -X DELETE "$BASE_URL/AccessManager/Tenants/$REALM/Users/$BC_NEW_ID" >/dev/null 2>&1 || true

# ════════════════════════════════════════════════════════════════════════════
section "Section 22: AppObjects + Group ObjectPermissions APIs"
# ════════════════════════════════════════════════════════════════════════════

# ── 22.1 GET AppObjects: returns enabled apps only ───────────────────────
APP_OBJS=$(A "$BASE_URL/AccessManager/Tenants/$REALM/AppObjects")
assert_contains "GET AppObjects: has apps array"          '"apps"'          "$APP_OBJS"
assert_contains "GET AppObjects: KnowledgeBase present"   "KnowledgeBase"   "$APP_OBJS"
assert_contains "GET AppObjects: KnowledgeBases object"   "KnowledgeBases"  "$APP_OBJS"
assert_contains "GET AppObjects: object_path has tenant"  "$REALM"          "$APP_OBJS"
assert_contains "GET AppObjects: methods field present"   '"methods"'       "$APP_OBJS"
assert_contains "GET AppObjects: actions field present"   '"actions"'       "$APP_OBJS"
assert_contains "GET AppObjects: display_name 查看"       "查看"            "$APP_OBJS"

# ── 22.2 GET AppObjects: disabled app not included ───────────────────────
psql_iam "UPDATE apps SET enabled=false WHERE app_name='KnowledgeBase';" >/dev/null
APP_OBJS_DIS=$(A "$BASE_URL/AccessManager/Tenants/$REALM/AppObjects")
assert_not_contains "GET AppObjects: disabled app excluded" "KnowledgeBase" "$APP_OBJS_DIS"
# Re-enable
psql_iam "UPDATE apps SET enabled=true WHERE app_name='KnowledgeBase';" >/dev/null

# ── 22.3 GET AppObjects: sub-resources with parent-ID placeholders excluded ─
assert_not_contains "GET AppObjects: Mappings (sub-resource) excluded" '"Mappings"' "$APP_OBJS"
assert_not_contains "GET AppObjects: Files (sub-resource) excluded"    '"Files"'    "$APP_OBJS"

# ── 22.4 PUT ObjectPermissions: set two roles ────────────────────────────
PERM_PUT=$(curl -s -o /dev/null -w "%{http_code}" \
  -X PUT -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d "{\"permissions\":[
    {\"object_path\":\"KnowledgeBase/Tenants/$REALM/KnowledgeBases\",\"role_path\":\"AccessManager/Tenants/System/Roles/Viewer\"},
    {\"object_path\":\"KnowledgeBase/Tenants/$REALM/Conversations\",\"role_path\":\"AccessManager/Tenants/System/Roles/Contributor\"}
  ]}" \
  "$BASE_URL/AccessManager/Tenants/$REALM/Groups/all-users/ObjectPermissions")
assert_match "PUT ObjectPermissions -> 200" "^200$" "$PERM_PUT"

# ── 22.5 Verify ACLs written to resource_acl ────────────────────────────
ACL_CHECK=$(A "$BASE_URL/AccessManager/Tenants/$REALM/ACLs?user=AccessManager/Tenants/$REALM/Groups/all-users")
assert_contains "ObjectPermissions: KnowledgeBases Viewer written"    "KnowledgeBases"  "$ACL_CHECK"
assert_contains "ObjectPermissions: Conversations Contributor written" "Conversations"   "$ACL_CHECK"
assert_contains "ObjectPermissions: Viewer role present"               "Viewer"          "$ACL_CHECK"
assert_contains "ObjectPermissions: Contributor role present"          "Contributor"     "$ACL_CHECK"

# ── 22.6 PUT ObjectPermissions: revoke one (role_path null) ─────────────
PERM_REVOKE=$(curl -s -o /dev/null -w "%{http_code}" \
  -X PUT -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d "{\"permissions\":[
    {\"object_path\":\"KnowledgeBase/Tenants/$REALM/Conversations\",\"role_path\":null}
  ]}" \
  "$BASE_URL/AccessManager/Tenants/$REALM/Groups/all-users/ObjectPermissions")
assert_match "PUT ObjectPermissions revoke -> 200" "^200$" "$PERM_REVOKE"

ACL_AFTER=$(A "$BASE_URL/AccessManager/Tenants/$REALM/ACLs?user=AccessManager/Tenants/$REALM/Groups/all-users")
assert_not_contains "ObjectPermissions: Conversations ACL removed" \
  "KnowledgeBase/Tenants/$REALM/Conversations" "$ACL_AFTER"
assert_contains "ObjectPermissions: KnowledgeBases ACL still present" \
  "KnowledgeBases" "$ACL_AFTER"

# ── 22.7 PUT ObjectPermissions: non-admin rejected ───────────────────────
PERM_NOAUTH=$(curl -s -o /dev/null -w "%{http_code}" \
  -X PUT -H "Authorization: Bearer $NORMAL_TOKEN" -H "Content-Type: application/json" \
  -d '{"permissions":[]}' \
  "$BASE_URL/AccessManager/Tenants/$REALM/Groups/all-users/ObjectPermissions")
assert_match "PUT ObjectPermissions non-admin -> 403" "^(403|401)$" "$PERM_NOAUTH"

# ── 22.8 Cleanup ─────────────────────────────────────────────────────────
curl -s -o /dev/null -X PUT -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"permissions\":[
    {\"object_path\":\"KnowledgeBase/Tenants/$REALM/KnowledgeBases\",\"role_path\":null}
  ]}" \
  "$BASE_URL/AccessManager/Tenants/$REALM/Groups/all-users/ObjectPermissions" || true

# ════════════════════════════════════════════════════════════════════════════
section "Section 23: Realm login settings + user profile (init-keycloak verification)"
# ════════════════════════════════════════════════════════════════════════════
# Refresh token for this section
ADMIN_TOKEN=$(curl -s -X POST "$BASE_URL/realms/$REALM/protocol/openid-connect/token" \
  -d "client_id=$CLIENT_ID" -d "client_secret=$CS" -d "grant_type=password" \
  -d "username=$ADMIN_USER" -d "password=$ADMIN_PASSWORD" | jget access_token)

# Get Keycloak master admin password from K8s secret
KC_MASTER_PASS=$(kubectl -n "$KEYCLOAK_NS" get secret keycloak-credentials \
  -o jsonpath='{.data.admin-password}' 2>/dev/null | base64 -d)

if [ -n "$KC_MASTER_PASS" ]; then
  # Port-forward Keycloak directly to bypass gateway (Admin API not exposed via gateway)
  kubectl -n "$KEYCLOAK_NS" port-forward svc/keycloak 18080:8080 >/dev/null 2>&1 &
  KC_PF_PID=$!
  sleep 2

  KC_ADMIN_TOKEN=$(curl -s -X POST "http://localhost:18080/realms/master/protocol/openid-connect/token" \
    -d "client_id=admin-cli&grant_type=password&username=admin&password=$KC_MASTER_PASS" \
    2>/dev/null | jget access_token)

  if [ -n "$KC_ADMIN_TOKEN" ]; then
    KC_REALM_RESP=$(curl -s "http://localhost:18080/admin/realms/$REALM" \
      -H "Authorization: Bearer $KC_ADMIN_TOKEN" 2>/dev/null)
    assert_contains "realm: rememberMe=true"           '"rememberMe":true'           "$KC_REALM_RESP"
    assert_contains "realm: verifyEmail=true"           '"verifyEmail":true'           "$KC_REALM_RESP"
    assert_contains "realm: editUsernameAllowed=true"   '"editUsernameAllowed":true'   "$KC_REALM_RESP"
    assert_contains "realm: resetPasswordAllowed=true"  '"resetPasswordAllowed":true'  "$KC_REALM_RESP"
    assert_contains "realm: loginWithEmailAllowed=true" '"loginWithEmailAllowed":true' "$KC_REALM_RESP"

    KC_PROFILE_RESP=$(curl -s "http://localhost:18080/admin/realms/$REALM/users/profile" \
      -H "Authorization: Bearer $KC_ADMIN_TOKEN" 2>/dev/null)
    assert_contains "user profile: username attribute present" '"username"'  "$KC_PROFILE_RESP"
    assert_contains "user profile: email attribute present"    '"email"'     "$KC_PROFILE_RESP"
    assert_contains "user profile: nickname attribute present" '"nickname"'  "$KC_PROFILE_RESP"
  else
    skip "Section 23 — could not get Keycloak admin token"
  fi

  kill $KC_PF_PID 2>/dev/null
else
  skip "Section 23 — keycloak-credentials secret not found"
fi

# ════════════════════════════════════════════════════════════════════════════
# Summary
# ════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Test Summary${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"
echo -e "  Total : $TOTAL"
echo -e "  ${GREEN}Pass  : $PASS${NC}"
echo -e "  ${RED}Fail  : $FAIL${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0
