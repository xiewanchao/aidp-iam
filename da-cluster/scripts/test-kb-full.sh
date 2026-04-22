#!/usr/bin/env bash
# ============================================================================
# test-kb-full.sh — Full KB auth matrix (mock-kb only, no real KB backend)
#
# Validates the KB接入 end-to-end:
#   - path-level auth (path_rules + path_rule_groups)
#   - resource-level auth (resource_patterns + resource_actions + resource_acl)
#   - ID extraction from path/body/query (method-aware pattern lookup)
#   - parent inheritance (files / mappings → parent KB)
#   - list filtering (X-Allowed-Ids injection, admins bypass)
#   - ext_proc 3-step ACL write (creator + admin_group + all-users)
#   - structured denial response {code, reason, path, method, rule}
#
# Runs against the live cluster — assumes `test.sh` baseline has been run
# (users admin/normal-user exist, Envoy Gateway is up). Spins its own
# port-forward on :8081 to avoid colliding with test.sh on :8080.
# ============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
KEYCLOAK_NS="keycloak"
ENVOY_GATEWAY_NS="${ENVOY_GATEWAY_NS:-aidp-iam}"
GATEWAY_PORT="${GATEWAY_PORT:-8081}"
BASE_URL="http://localhost:${GATEWAY_PORT}"

REALM="${REALM:-aidp}"
CLIENT_ID="${CLIENT_ID:-aidp-client}"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-Admin@123}"

# Dedicated test users (created by this script)
ALICE_USER="kbtest-alice"
ALICE_PASS="Alice@123"
BOB_USER="kbtest-bob"
BOB_PASS="Bob@123"
KBADMIN_USER="kbtest-kbadmin"
KBADMIN_PASS="KbAdmin@123"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
PASS=0; FAIL=0; TOTAL=0

assert() { local d="$1" e="$2" a="$3"; TOTAL=$((TOTAL+1))
  if [ "$e" = "$a" ]; then echo -e "  ${GREEN}PASS${NC} $d"
    PASS=$((PASS+1))
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

# ── Port-forward Envoy Gateway ─────────────────────────────────────────────
echo -e "${YELLOW}Setting up port-forward on :${GATEWAY_PORT}...${NC}"
PF_PID=""
if curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/" 2>/dev/null | grep -qE '200|301|302|404'; then
  echo "  already forwarded"
else
  lsof -ti:${GATEWAY_PORT} 2>/dev/null | xargs kill -9 2>/dev/null || true
  GW_SVC=$(kubectl -n "$ENVOY_GATEWAY_NS" get svc -l gateway.envoyproxy.io/owning-gateway-name=eg -o name 2>/dev/null | head -1)
  [ -z "$GW_SVC" ] && GW_SVC="svc/envoy-eg"
  kubectl -n "$ENVOY_GATEWAY_NS" port-forward "$GW_SVC" "${GATEWAY_PORT}:80" >/dev/null 2>&1 &
  PF_PID=$!; sleep 3
fi

cleanup() {
  [ -n "$PF_PID" ] && kill "$PF_PID" 2>/dev/null || true
  # Remove test data so reruns are clean.
  psql_iam "DELETE FROM resource_acl WHERE app_name='knowledgebase' AND resource_id NOT IN ('KB1','KB2','KB3');" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# ── Admin token + Keycloak REST helpers ────────────────────────────────────
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
if [ -z "$ADMIN_TOKEN" ]; then
  echo -e "${RED}ERROR: could not obtain admin token${NC}"
  exit 1
fi

# We use the keycloak-proxy's /api/v1/{realm}/users endpoint with the aidp
# admin token (simpler than Keycloak's raw Admin API, which requires master realm).
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
KB_ADMINS_GID=$(gid_of_group kb-admins)
if [ -z "$ALL_USERS_GID" ] || [ -z "$KB_ADMINS_GID" ]; then
  echo -e "${RED}ERROR: could not look up preset group ids (all-users=$ALL_USERS_GID kb-admins=$KB_ADMINS_GID)${NC}"
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
  # temporary_password:false so password-grant works immediately (no
  # UPDATE_PASSWORD requiredAction). With the minimal user-profile
  # (username only), no master-realm finalization dance is needed.
  KP -X POST -H "Content-Type: application/json" \
    -d "{\"username\":\"$username\",\"password\":\"$password\",\"temporary_password\":false,\"groups\":$groups_json}" \
    "$IAM/users" >/dev/null
  uid_of_user "$username"
}

ALICE_UID=$(ensure_user "$ALICE_USER"     "$ALICE_PASS"     "[\"$ALL_USERS_GID\"]")
BOB_UID=$(ensure_user   "$BOB_USER"       "$BOB_PASS"       "[\"$ALL_USERS_GID\"]")
KBADMIN_UID=$(ensure_user "$KBADMIN_USER" "$KBADMIN_PASS"   "[\"$ALL_USERS_GID\",\"$KB_ADMINS_GID\"]")

ALICE_TOKEN=$(get_token "$ALICE_USER" "$ALICE_PASS")
BOB_TOKEN=$(get_token   "$BOB_USER"   "$BOB_PASS")
KBADMIN_TOKEN=$(get_token "$KBADMIN_USER" "$KBADMIN_PASS")
assert_match "alice token issued"   "^[A-Za-z0-9]+" "$ALICE_TOKEN"
assert_match "bob token issued"     "^[A-Za-z0-9]+" "$BOB_TOKEN"
assert_match "kbadmin token issued" "^[A-Za-z0-9]+" "$KBADMIN_TOKEN"

A()  { curl -s -H "Authorization: Bearer $ALICE_TOKEN"   "$@"; }
AH() { curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $ALICE_TOKEN"   "$@"; }
B()  { curl -s -H "Authorization: Bearer $BOB_TOKEN"     "$@"; }
BH() { curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $BOB_TOKEN"     "$@"; }
K()  { curl -s -H "Authorization: Bearer $KBADMIN_TOKEN" "$@"; }
KH() { curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $KBADMIN_TOKEN" "$@"; }
AD() { curl -s -H "Authorization: Bearer $ADMIN_TOKEN"   "$@"; }
ADH(){ curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $ADMIN_TOKEN"  "$@"; }

jbody() { echo "$1" | python -c "import sys; print(sys.stdin.read())"; }

# ════════════════════════════════════════════════════════════════════════════
section "Section 1: Path-level auth (5)"
# ════════════════════════════════════════════════════════════════════════════

# P1 alice GET list
P1=$(AH "$BASE_URL/kb/knowledge_bases/page")
assert "P1 alice GET /kb/knowledge_bases/page"       "200" "$P1"

# P2 alice POST admin path — path_rule denial
P2_BODY=$(A -X POST -H "Content-Type: application/json" -d '{"NAME":"x"}' "$BASE_URL/kb/models/config/add")
P2_CODE=$(AH -X POST -H "Content-Type: application/json" -d '{"NAME":"x"}' "$BASE_URL/kb/models/config/add")
assert "P2 alice POST /kb/models/config/add -> 403"  "403" "$P2_CODE"
assert_contains "P2 denial body has rule=path_rule"  '"rule": "path_rule"' "$P2_BODY"
assert_contains "P2 denial body has code=forbidden"  '"code": "forbidden"' "$P2_BODY"

# P3 no-token
P3=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/kb/knowledge_bases/page")
assert_match  "P3 no-token -> 401/403"               "^(401|403)$" "$P3"

# P4 kbadmin POST prompts
P4=$(KH -X POST -H "Content-Type: application/json" -d '{"name":"p1"}' "$BASE_URL/kb/prompts/add")
assert_match "P4 kbadmin POST /kb/prompts/add -> 2xx" "^2" "$P4"

# P5 app_disabled — toggle knowledgebase.enabled=false, then re-enable
psql_iam "UPDATE apps SET enabled=false WHERE app_name='knowledgebase';" >/dev/null
# wait for bundle-server to pick it up (30s refresh + OPA propagation)
sleep 35
P5_BODY=$(A "$BASE_URL/kb/knowledge_bases/page")
P5_CODE=$(AH "$BASE_URL/kb/knowledge_bases/page")
assert "P5 app_disabled -> 403"                       "403" "$P5_CODE"
assert_contains "P5 denial body has rule=app_disabled" '"rule": "app_disabled"' "$P5_BODY"
psql_iam "UPDATE apps SET enabled=true WHERE app_name='knowledgebase';" >/dev/null
sleep 35

# ════════════════════════════════════════════════════════════════════════════
section "Section 2: Resource-level single-resource (10)"
# ════════════════════════════════════════════════════════════════════════════

# R1 alice create KB
R1_BODY=$(A -X POST -H "Content-Type: application/json" \
  -d '{"NAME":"alice-kb","DESCRIPTION":"R1"}' "$BASE_URL/kb/knowledge_bases/add")
R1_KDSID=$(echo "$R1_BODY" | jget KDSID)
assert_match "R1 alice create KB -> KDSID present"   "^[A-Za-z0-9]+$" "$R1_KDSID"

# R2 ACL owner row exists (wait briefly for ext_proc write)
sleep 2
R2=$(psql_iam "SELECT permission FROM resource_acl WHERE resource_type='kb' AND resource_id='$R1_KDSID' AND subject_type='user' AND subject_id='$ALICE_UID';")
assert "R2 ACL owner row for alice"                  "owner" "$R2"

# R3 bob GET alice's KB
R3=$(BH "$BASE_URL/kb/knowledge_bases?KDSID=$R1_KDSID")
assert "R3 bob GET alice's KB -> 403"                "403" "$R3"

# R4 alice shares KB viewer to bob via IAM ACL API (owner-only operation, so alice must call)
SHARE_URL="$BASE_URL/acl/v1/resources/$R1_KDSID/permissions"
share_perm() {  # $1 subject_id $2 permission
  local sub="$1" perm="$2"
  A -X POST -H "Content-Type: application/json" \
    -d "{\"app_name\":\"knowledgebase\",\"resource_type\":\"kb\",\"subject_type\":\"user\",\"subject_id\":\"$sub\",\"permission\":\"$perm\"}" \
    "$SHARE_URL"
}
upsert_perm() {  # update existing row: find id, PUT to it
  local sub="$1" perm="$2"
  local acl_id
  acl_id=$(A "$SHARE_URL?app_name=knowledgebase&resource_type=kb" \
    | python -c "import sys,json; d=json.load(sys.stdin); print(next((p['id'] for p in d.get('permissions',[]) if p['subject_type']=='user' and p['subject_id']=='$sub'), ''))")
  if [ -n "$acl_id" ]; then
    A -X PUT -H "Content-Type: application/json" \
      -d "{\"app_name\":\"knowledgebase\",\"resource_type\":\"kb\",\"permission\":\"$perm\"}" \
      "$SHARE_URL/$acl_id" >/dev/null
  else
    share_perm "$sub" "$perm" >/dev/null
  fi
}

R4=$(share_perm "$BOB_UID" viewer)
assert_match "R4 share viewer to bob"                '"permission"[^,]*"viewer"' "$R4"

# R5 bob GET now succeeds
R5=$(BH "$BASE_URL/kb/knowledge_bases?KDSID=$R1_KDSID")
assert "R5 bob GET alice's KB (viewer) -> 200"       "200" "$R5"

# R6 bob modify -> 403
R6=$(BH -X POST -H "Content-Type: application/json" \
  -d "{\"KDSID\":\"$R1_KDSID\",\"NAME\":\"hacked\"}" "$BASE_URL/kb/knowledge_bases/modify")
assert "R6 bob POST /modify (viewer) -> 403"         "403" "$R6"

# R7 promote bob to contributor, modify succeeds
upsert_perm "$BOB_UID" contributor
sleep 1
R7=$(BH -X POST -H "Content-Type: application/json" \
  -d "{\"KDSID\":\"$R1_KDSID\",\"NAME\":\"renamed-by-bob\"}" "$BASE_URL/kb/knowledge_bases/modify")
assert "R7 bob POST /modify (contributor) -> 200"    "200" "$R7"

# R8 bob remove -> 403 (need owner)
R8=$(BH -X POST -H "Content-Type: application/json" \
  -d "{\"KDSID\":\"$R1_KDSID\"}" "$BASE_URL/kb/knowledge_bases/remove")
assert "R8 bob POST /remove (contributor) -> 403"    "403" "$R8"

# R9 alice remove
R9=$(AH -X POST -H "Content-Type: application/json" \
  -d "{\"KDSID\":\"$R1_KDSID\"}" "$BASE_URL/kb/knowledge_bases/remove")
assert "R9 alice POST /remove (owner) -> 200"        "200" "$R9"

# R10 ACL rows cascade-deleted
sleep 2
R10=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE resource_type='kb' AND resource_id='$R1_KDSID';")
assert "R10 ACL rows cascade-deleted"                "0" "$R10"

# ════════════════════════════════════════════════════════════════════════════
section "Section 3: ID extraction — query/body/path discriminate correctly (9)"
# ════════════════════════════════════════════════════════════════════════════
# Owner-only smoke tests (the previous shape of this section) can't tell
# "extraction worked + ACL matched" apart from "extraction failed silently +
# PEP skipped the resource check". We add bob-on-alice's-id probes: if
# extraction truly works, bob should see 403 with rule=resource_acl. If the
# PEP silently failed to pull the id, bob would sneak through as 200.

# ── Targets ──────────────────────────────────────────────────────────────
# KB is used for query + body extraction (all KB patterns are body/query).
I_KB_BODY=$(A -X POST -H "Content-Type: application/json" \
  -d '{"NAME":"extract-kb"}' "$BASE_URL/kb/knowledge_bases/add")
I_KDSID=$(echo "$I_KB_BODY" | jget KDSID)

# Rubik database is the only current resource with id_source='path'
# (KB has no path-based patterns). Alice's all-users path_rule lets her
# POST databases; creator becomes owner via ext_proc.
I_DB_BODY=$(A -X POST -H "Content-Type: application/json" \
  -d '{"name":"extract-db"}' "$BASE_URL/rubik/api/databases")
I_DBID=$(echo "$I_DB_BODY" | jget id)
sleep 2

# ── Q: id_source=query (GET /kb/knowledge_bases?KDSID=…) ────────────────
Q1=$(AH "$BASE_URL/kb/knowledge_bases?KDSID=$I_KDSID")
assert "Q1 alice query KDSID (owner) -> 200"         "200" "$Q1"

Q2_BODY=$(B "$BASE_URL/kb/knowledge_bases?KDSID=$I_KDSID")
Q2_CODE=$(BH "$BASE_URL/kb/knowledge_bases?KDSID=$I_KDSID")
assert "Q2 bob query alice's KDSID -> 403"           "403" "$Q2_CODE"
assert_contains "Q2 rule=resource_acl (proves query extraction fired)" \
    '"rule": "resource_acl"' "$Q2_BODY"

Q3=$(BH "$BASE_URL/kb/knowledge_bases?KDSID=does-not-exist-xyz")
assert "Q3 bob query bogus KDSID -> 403"             "403" "$Q3"

# ── B: id_source=body (POST /kb/knowledge_bases/modify {…KDSID:…}) ──────
BD1=$(AH -X POST -H "Content-Type: application/json" \
  -d "{\"KDSID\":\"$I_KDSID\",\"NAME\":\"via-body\"}" "$BASE_URL/kb/knowledge_bases/modify")
assert "BD1 alice body KDSID (owner) -> 200"         "200" "$BD1"

BD2_BODY=$(B -X POST -H "Content-Type: application/json" \
  -d "{\"KDSID\":\"$I_KDSID\",\"NAME\":\"hack\"}" "$BASE_URL/kb/knowledge_bases/modify")
BD2_CODE=$(BH -X POST -H "Content-Type: application/json" \
  -d "{\"KDSID\":\"$I_KDSID\",\"NAME\":\"hack\"}" "$BASE_URL/kb/knowledge_bases/modify")
assert "BD2 bob body alice's KDSID -> 403"           "403" "$BD2_CODE"
assert_contains "BD2 rule=resource_acl (proves body extraction fired)" \
    '"rule": "resource_acl"' "$BD2_BODY"

# ── P: id_source=path (GET /rubik/api/databases/<id>) ───────────────────
P1=$(AH "$BASE_URL/rubik/api/databases/$I_DBID")
assert "P1 alice path-id (owner) -> 200"             "200" "$P1"

P2_BODY=$(B "$BASE_URL/rubik/api/databases/$I_DBID")
P2_CODE=$(BH "$BASE_URL/rubik/api/databases/$I_DBID")
assert "P2 bob path-id on alice's db -> 403"         "403" "$P2_CODE"
assert_contains "P2 rule=resource_acl (proves path extraction fired)" \
    '"rule": "resource_acl"' "$P2_BODY"

# cleanup
A -X POST -H "Content-Type: application/json" \
  -d "{\"KDSID\":\"$I_KDSID\"}" "$BASE_URL/kb/knowledge_bases/remove" >/dev/null
A -X DELETE "$BASE_URL/rubik/api/databases/$I_DBID" >/dev/null 2>&1 || true
sleep 2

# ════════════════════════════════════════════════════════════════════════════
section "Section 4: Parent inheritance — files (5)"
# ════════════════════════════════════════════════════════════════════════════

# Create alice's KB
C_BODY=$(A -X POST -H "Content-Type: application/json" -d '{"NAME":"c-kb"}' \
  "$BASE_URL/kb/knowledge_bases/add")
C_KDSID=$(echo "$C_BODY" | jget KDSID)
sleep 1

# C1 alice (owner of KB) can list files
C1=$(AH "$BASE_URL/kb/knowledge_bases/files?kbs_id=$C_KDSID")
assert "C1 alice GET files (owner) -> 200"           "200" "$C1"

# C2 bob (no access) -> 403
C2=$(BH "$BASE_URL/kb/knowledge_bases/files?kbs_id=$C_KDSID")
assert "C2 bob GET files (no access) -> 403"        "403" "$C2"

# C3 share KB viewer to bob (alice is owner), upload still 403 (needs contributor)
A -X POST -H "Content-Type: application/json" \
  -d "{\"app_name\":\"knowledgebase\",\"resource_type\":\"kb\",\"subject_type\":\"user\",\"subject_id\":\"$BOB_UID\",\"permission\":\"viewer\"}" \
  "$BASE_URL/acl/v1/resources/$C_KDSID/permissions" >/dev/null
sleep 1
C3=$(BH -X POST -H "Content-Type: application/json" \
  -d "{\"kbs_id\":\"$C_KDSID\",\"name\":\"f.txt\"}" "$BASE_URL/kb/knowledge_bases/files/upload")
assert "C3 bob upload (viewer) -> 403"               "403" "$C3"

# C4 promote bob to contributor via PUT on existing ACL row
C_ACL_ID=$(A "$BASE_URL/acl/v1/resources/$C_KDSID/permissions?app_name=knowledgebase&resource_type=kb" \
  | python -c "import sys,json; d=json.load(sys.stdin); print(next((p['id'] for p in d.get('permissions',[]) if p['subject_type']=='user' and p['subject_id']=='$BOB_UID'), ''))")
A -X PUT -H "Content-Type: application/json" \
  -d '{"app_name":"knowledgebase","resource_type":"kb","permission":"contributor"}' \
  "$BASE_URL/acl/v1/resources/$C_KDSID/permissions/$C_ACL_ID" >/dev/null
sleep 1
C4=$(BH -X POST -H "Content-Type: application/json" \
  -d "{\"kbs_id\":\"$C_KDSID\",\"name\":\"f2.txt\"}" "$BASE_URL/kb/knowledge_bases/files/upload")
assert_match "C4 bob upload (contributor) -> 2xx"    "^2" "$C4"

# C5 bob can download (viewer-level access)
C5=$(BH "$BASE_URL/kb/knowledge_bases/files/download?kbs_id=$C_KDSID")
assert "C5 bob download file (contributor) -> 200"   "200" "$C5"

A -X POST -H "Content-Type: application/json" \
  -d "{\"KDSID\":\"$C_KDSID\"}" "$BASE_URL/kb/knowledge_bases/remove" >/dev/null
sleep 1

# ════════════════════════════════════════════════════════════════════════════
section "Section 5: List filter via X-Allowed-Ids (5)"
# ════════════════════════════════════════════════════════════════════════════

# Alice creates two KBs
L_A=$(A -X POST -H "Content-Type: application/json" -d '{"NAME":"L-a"}' \
  "$BASE_URL/kb/knowledge_bases/add")
LA_KDSID=$(echo "$L_A" | jget KDSID)
L_B=$(A -X POST -H "Content-Type: application/json" -d '{"NAME":"L-b"}' \
  "$BASE_URL/kb/knowledge_bases/add")
LB_KDSID=$(echo "$L_B" | jget KDSID)
sleep 2

# L1 bob lists → nothing of alice's (bob may still see the 3 seed KBs if shared, but those have no ACL)
L1_BODY=$(B "$BASE_URL/kb/knowledge_bases/page")
assert_not_contains "L1 bob does not see alice's KBs" "$LA_KDSID" "$L1_BODY"
assert_not_contains "L1 bob does not see alice's KBs (B)" "$LB_KDSID" "$L1_BODY"

# L2 share KB_A viewer to bob (alice is owner) → bob sees only KB_A
A -X POST -H "Content-Type: application/json" \
  -d "{\"app_name\":\"knowledgebase\",\"resource_type\":\"kb\",\"subject_type\":\"user\",\"subject_id\":\"$BOB_UID\",\"permission\":\"viewer\"}" \
  "$BASE_URL/acl/v1/resources/$LA_KDSID/permissions" >/dev/null
sleep 2
L2_BODY=$(B "$BASE_URL/kb/knowledge_bases/page")
assert_contains "L2 bob sees shared KB_A" "$LA_KDSID" "$L2_BODY"
assert_not_contains "L2 bob does NOT see KB_B" "$LB_KDSID" "$L2_BODY"

# L3 admin bypass — admin sees everything
L3_BODY=$(AD "$BASE_URL/kb/knowledge_bases/page")
assert_contains "L3 admin sees KB_A (bypass)" "$LA_KDSID" "$L3_BODY"
assert_contains "L3 admin sees KB_B (bypass)" "$LB_KDSID" "$L3_BODY"

# L4 debug headers — resource-sync injected X-Allowed-Ids for bob
L4_HEADERS=$(B -i "$BASE_URL/kb/knowledge_bases/page" | tr -d '\r' | grep -i "^X-Debug-Allowed-Ids:" | head -1)
assert_contains "L4 mock-kb echoes X-Debug-Allowed-Ids containing LA" "$LA_KDSID" "$L4_HEADERS"

# L5 admin's debug header shows NO X-Allowed-Ids (admin bypass)
L5_VALUE=$(AD -i "$BASE_URL/kb/knowledge_bases/page" | tr -d '\r' | grep -i "^x-debug-allowed-ids:" | head -1 | sed 's/^[^:]*: *//')
assert "L5 admin X-Debug-Allowed-Ids empty (bypass)" "" "$L5_VALUE"

# cleanup
A -X POST -H "Content-Type: application/json" -d "{\"KDSID\":\"$LA_KDSID\"}" "$BASE_URL/kb/knowledge_bases/remove" >/dev/null
A -X POST -H "Content-Type: application/json" -d "{\"KDSID\":\"$LB_KDSID\"}" "$BASE_URL/kb/knowledge_bases/remove" >/dev/null
sleep 1

# ════════════════════════════════════════════════════════════════════════════
section "Section 6: ext_proc 3-step ACL write — full resource_type matrix (14)"
# ════════════════════════════════════════════════════════════════════════════
# resource_sync.ext_proc_server:_write_acl() builds a 3-step plan per create:
#   1. (user, <creator>, owner)                  — always
#   2. (group, <app.admin_group>, owner)         — if pattern.share_to_admin_group_on_create
#   3. (group, 'all-users', viewer)              — if pattern.share_to_all_users_on_create
# The KB app currently wires these flags per resource_type as:
#   kb, conversation            → (F,F) → 1 row  (user owner only)
#   jargon_lib, model_config,
#   prompt_group                → (T,F) → 2 rows (user owner + kb-admins owner)
# No KB pattern currently sets share_to_all_users=T, so step 3 is dormant in
# this suite — we leave coverage for that to a dedicated test if/when enabled.

# E1 alice create KB → 1 owner row (no admin_group share for kb type)
E1_BODY=$(A -X POST -H "Content-Type: application/json" -d '{"NAME":"E1-kb"}' \
  "$BASE_URL/kb/knowledge_bases/add")
E1_KDSID=$(echo "$E1_BODY" | jget KDSID)
sleep 2
E1_ROWS=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE resource_type='kb' AND resource_id='$E1_KDSID';")
assert "E1 KB create writes exactly 1 ACL row"       "1" "$E1_ROWS"

# E2 kbadmin creates prompt → 2 rows (user owner + kb-admins group owner), NOT all-users
E2_BODY=$(K -X POST -H "Content-Type: application/json" -d '{"name":"E2-prompt"}' \
  "$BASE_URL/kb/prompts/add")
E2_PID=$(echo "$E2_BODY" | jget prompt_id)
sleep 2
E2_USER_ROW=$(psql_iam "SELECT permission FROM resource_acl WHERE resource_type='prompt_group' AND resource_id='$E2_PID' AND subject_type='user' AND subject_id='$KBADMIN_UID';")
E2_GROUP_ROW=$(psql_iam "SELECT permission FROM resource_acl WHERE resource_type='prompt_group' AND resource_id='$E2_PID' AND subject_type='group' AND subject_id='kb-admins';")
E2_ALLUSERS_ROW=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE resource_type='prompt_group' AND resource_id='$E2_PID' AND subject_id='all-users';")
assert "E2 prompt user owner ACL"                    "owner" "$E2_USER_ROW"
assert "E2 prompt kb-admins group owner ACL"         "owner" "$E2_GROUP_ROW"
assert "E2 prompt has NO all-users viewer row"       "0"     "$E2_ALLUSERS_ROW"

# E3 kbadmin (creator) can modify via user-owner ACL
E3=$(KH -X POST -H "Content-Type: application/json" \
  -d "{\"prompt_id\":\"$E2_PID\",\"name\":\"modified\"}" "$BASE_URL/kb/prompts/modify")
assert_match "E3 kbadmin modifies own prompt -> 2xx" "^2" "$E3"

# E4 alice (not kb-admins) is blocked at path_rule (POST /prompts → kb-admins only)
E4=$(AH -X POST -H "Content-Type: application/json" \
  -d "{\"prompt_id\":\"$E2_PID\",\"name\":\"evil\"}" "$BASE_URL/kb/prompts/modify")
assert "E4 alice POST /kb/prompts/modify (non kb-admin) -> 403" "403" "$E4"

# E5 delete prompt cascades ACL
psql_iam "DELETE FROM apps WHERE app_name='__nope__';" >/dev/null  # noop to anchor connection
K -X POST -H "Content-Type: application/json" \
  -d "{\"prompt_id\":\"$E2_PID\"}" "$BASE_URL/kb/prompts/remove" >/dev/null
sleep 2
E5=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE resource_type='prompt_group' AND resource_id='$E2_PID';")
assert "E5 prompt ACL rows cascade-deleted"          "0" "$E5"

# ── E6: jargon_lib (share_to_admin_group=T, share_to_all_users=F) ──────
# path_rule 26 restricts /kb/jargon_groups/** to kb-admins, so kbadmin creates.
E6_BODY=$(K -X POST -H "Content-Type: application/json" \
  -d '{"JARGON_LIB_NAME":"E6-jargon"}' "$BASE_URL/kb/jargon_groups/add")
E6_JLID=$(echo "$E6_BODY" | jget JARGON_LIB_NAME)
sleep 2
E6_USER_ROW=$(psql_iam "SELECT permission FROM resource_acl WHERE resource_type='jargon_lib' AND resource_id='$E6_JLID' AND subject_type='user' AND subject_id='$KBADMIN_UID';")
E6_GROUP_ROW=$(psql_iam "SELECT permission FROM resource_acl WHERE resource_type='jargon_lib' AND resource_id='$E6_JLID' AND subject_type='group' AND subject_id='kb-admins';")
E6_ALLUSERS_ROW=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE resource_type='jargon_lib' AND resource_id='$E6_JLID' AND subject_id='all-users';")
assert "E6 jargon_lib user owner ACL"                "owner" "$E6_USER_ROW"
assert "E6 jargon_lib kb-admins group owner ACL"     "owner" "$E6_GROUP_ROW"
assert "E6 jargon_lib NO all-users row"              "0"     "$E6_ALLUSERS_ROW"
# delete cascade check
K -X POST -H "Content-Type: application/json" \
  -d "{\"JARGON_LIB_NAME\":\"$E6_JLID\"}" "$BASE_URL/kb/jargon_groups/remove" >/dev/null
sleep 2
E6_AFTER=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE resource_type='jargon_lib' AND resource_id='$E6_JLID';")
assert "E6 jargon_lib ACL rows cascade-deleted"      "0" "$E6_AFTER"

# ── E7: model_config (share_to_admin_group=T, share_to_all_users=F) ────
# path_rule 24 restricts /kb/models/config/** to kb-admins.
E7_BODY=$(K -X POST -H "Content-Type: application/json" \
  -d '{"NAME":"E7-model"}' "$BASE_URL/kb/models/config/add")
E7_MID=$(echo "$E7_BODY" | jget ModelAPIID)
sleep 2
E7_USER_ROW=$(psql_iam "SELECT permission FROM resource_acl WHERE resource_type='model_config' AND resource_id='$E7_MID' AND subject_type='user' AND subject_id='$KBADMIN_UID';")
E7_GROUP_ROW=$(psql_iam "SELECT permission FROM resource_acl WHERE resource_type='model_config' AND resource_id='$E7_MID' AND subject_type='group' AND subject_id='kb-admins';")
E7_ALLUSERS_ROW=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE resource_type='model_config' AND resource_id='$E7_MID' AND subject_id='all-users';")
assert "E7 model_config user owner ACL"              "owner" "$E7_USER_ROW"
assert "E7 model_config kb-admins group owner ACL"   "owner" "$E7_GROUP_ROW"
assert "E7 model_config NO all-users row"            "0"     "$E7_ALLUSERS_ROW"
# delete cascade check
K -X POST -H "Content-Type: application/json" \
  -d "{\"ModelAPIID\":\"$E7_MID\"}" "$BASE_URL/kb/models/config/remove" >/dev/null
sleep 2
E7_AFTER=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE resource_type='model_config' AND resource_id='$E7_MID';")
assert "E7 model_config ACL rows cascade-deleted"    "0" "$E7_AFTER"

# ── E8: conversation (share_to_admin_group=F, share_to_all_users=F) ────
# path_rule 17 lets all-users POST /kb/conversations/start, so alice creates.
# mock-kb validates kbs_id against its KBS store, so we anchor to E1's KB
# (alice owns it, still alive at this point, cleaned up below).
E8_BODY=$(A -X POST -H "Content-Type: application/json" \
  -d "{\"kbs_id\":\"$E1_KDSID\",\"question\":\"hi\"}" "$BASE_URL/kb/conversations/start")
E8_CID=$(echo "$E8_BODY" | jget conv_id)
sleep 2
E8_ROWS=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE resource_type='conversation' AND resource_id='$E8_CID';")
E8_USER_ROW=$(psql_iam "SELECT permission FROM resource_acl WHERE resource_type='conversation' AND resource_id='$E8_CID' AND subject_type='user' AND subject_id='$ALICE_UID';")
E8_GROUP_ANY=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE resource_type='conversation' AND resource_id='$E8_CID' AND subject_type='group';")
assert "E8 conversation writes exactly 1 ACL row"    "1"     "$E8_ROWS"
assert "E8 conversation user owner ACL"              "owner" "$E8_USER_ROW"
assert "E8 conversation NO group rows (no admin_group share)" "0" "$E8_GROUP_ANY"
# delete cascade check
A -X POST -H "Content-Type: application/json" \
  -d "{\"conv_id\":\"$E8_CID\"}" "$BASE_URL/kb/conversations/remove" >/dev/null
sleep 2
E8_AFTER=$(psql_iam "SELECT COUNT(*) FROM resource_acl WHERE resource_type='conversation' AND resource_id='$E8_CID';")
assert "E8 conversation ACL rows cascade-deleted"    "0" "$E8_AFTER"

# cleanup E1's KB
A -X POST -H "Content-Type: application/json" -d "{\"KDSID\":\"$E1_KDSID\"}" "$BASE_URL/kb/knowledge_bases/remove" >/dev/null
sleep 1

# ════════════════════════════════════════════════════════════════════════════
section "Section 7: Structured denial response (3)"
# ════════════════════════════════════════════════════════════════════════════

# D1 no-token path has code=unauthorized, rule=authentication
D1=$(curl -s "$BASE_URL/kb/knowledge_bases/page")
assert_contains "D1 no-token body code=unauthorized" '"code": "unauthorized"' "$D1"
assert_contains "D1 no-token rule=authentication"    '"rule": "authentication"' "$D1"

# D2 path_rule denial (alice hitting kb-admins-only path)
D2=$(A -X POST -H "Content-Type: application/json" -d '{}' "$BASE_URL/kb/models/config/add")
assert_contains "D2 path_rule denial body"           '"rule": "path_rule"' "$D2"
assert_contains "D2 path_rule denial has path"       '"path": "/kb/models/config/add"' "$D2"
assert_contains "D2 path_rule denial has method"     '"method": "POST"' "$D2"

# D3 resource_acl denial (bob on unshared KB)
D3_KB=$(A -X POST -H "Content-Type: application/json" -d '{"NAME":"D3-kb"}' "$BASE_URL/kb/knowledge_bases/add")
D3_KDSID=$(echo "$D3_KB" | jget KDSID)
sleep 1
D3=$(B "$BASE_URL/kb/knowledge_bases?KDSID=$D3_KDSID")
assert_contains "D3 resource_acl denial body"        '"rule": "resource_acl"' "$D3"
A -X POST -H "Content-Type: application/json" -d "{\"KDSID\":\"$D3_KDSID\"}" "$BASE_URL/kb/knowledge_bases/remove" >/dev/null

# ════════════════════════════════════════════════════════════════════════════
section "Summary"
# ════════════════════════════════════════════════════════════════════════════
echo -e "  Total:  ${TOTAL}"
echo -e "  ${GREEN}PASS: ${PASS}${NC}"
echo -e "  ${RED}FAIL: ${FAIL}${NC}"
if [ "$FAIL" -eq 0 ]; then
  echo -e "\n${GREEN}ALL KB FULL TESTS PASSED${NC}"
  exit 0
else
  exit 1
fi
