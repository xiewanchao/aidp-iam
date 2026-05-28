#!/usr/bin/env bash
# ============================================================================
# test.sh — MemoryStore end-to-end lifecycle test
#
# Covers the full MemoryStore API surface from manifest-template-memorystore.json:
#
#   PART A  Instance lifecycle      — CRUD + ACL auto-sync + ACL sharing
#   PART B  Memory lifecycle        — CRUD + Query action + sub-resource ACL
#   PART C  Template lifecycle      — CRUD + Filters/LLMExtraction actions
#   PART D  Instance cascade delete — delete instance cascades child ACL
#   PART E  Cleanup
#
# Cast:
#   alice = admin       (in master-admins + all-users; creates instances — is the owner)
#   bob   = normal-user (in all-users; default_acl gives Contributor on
#                        Instances type, so can create; used to verify
#                        resource-level isolation between owners)
#
# The aidp realm only has two users (admin + normal-user), created by
# init-keycloak.py. There is no "memory-admin" group or user — group-based
# path access is derived from default_acl user_template segments in the
# manifest (all-users / tenant-admins), not from a per-app admins group.
#
# Env overrides:
#   GATEWAY     (default http://localhost:30080)
#   NAMESPACE   (default mock-memory)
#   IAM_NS      (default aidp-iam)
#   KEYCLOAK_NS (default keycloak)
# ============================================================================
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/lib/common.sh"

POD_LABEL="${POD_LABEL:-app=mock-memory}"
TID="aidp"
BASE="$GATEWAY/MemoryStore/Tenants/$TID"
ACL_BASE="$GATEWAY/AccessManager/Tenants/$TID/ACLs"

banner "MemoryStore END-TO-END TEST"
init_tokens

# alice = admin (master-admins + all-users), bob = normal-user (all-users)
T_ALICE="$T_ADMIN"
T_BOB="$T_USER"

# ── prereq ───────────────────────────────────────────────────────────────────
section "Prereq — environment ready"
ready=$(kubectl -n "$NAMESPACE" get pod -l "$POD_LABEL" \
  -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
assert_eq "mock-memory Pod Ready=True" "True" "$ready"
assert_eq "GET /health → 200 (no token needed)" "200" \
  "$(http_code "$GATEWAY/MemoryStore/health")"
assert_eq "GET /MemoryStore/Tenants/$TID/Instances (no token) → 401" "401" \
  "$(http_code "$BASE/Instances")"
assert_eq "PUT /MemoryStore/Tenants/$TID/Instances/x (no token) → 401" "401" \
  "$(http_code -X PUT "$BASE/Instances/x")"
for v in T_ADMIN T_USER; do
  assert_match "$v is JWT-shape" '^[A-Za-z0-9_.-]{800,}$' "${!v}"
done

# Resolve Keycloak UUIDs for ACL assertions
ALICE_UID=$(kubectl -n "$KEYCLOAK_NS" exec iam-store-0 -c postgres -- \
  psql -U keycloak -d keycloak -tA -c \
  "SELECT id FROM user_entity WHERE username='admin' AND realm_id=(SELECT id FROM realm WHERE name='aidp')" 2>/dev/null | tr -d ' \r\n')
BOB_UID=$(kubectl -n "$KEYCLOAK_NS" exec iam-store-0 -c postgres -- \
  psql -U keycloak -d keycloak -tA -c \
  "SELECT id FROM user_entity WHERE username='normal-user' AND realm_id=(SELECT id FROM realm WHERE name='aidp')" 2>/dev/null | tr -d ' \r\n')
[ -n "$ALICE_UID" ] || { echo "FATAL: cannot resolve admin UUID"; exit 2; }
[ -n "$BOB_UID" ]   || { echo "FATAL: cannot resolve normal-user UUID"; exit 2; }

ALICE_PATH="AccessManager/Tenants/$TID/Users/$ALICE_UID"
BOB_PATH="AccessManager/Tenants/$TID/Users/$BOB_UID"


# ════════════════════════════════════════════════════════════════════════════
# PART A — Instance lifecycle + ACL auto-sync + ACL sharing
# ════════════════════════════════════════════════════════════════════════════
banner "PART A — Instance lifecycle + ACL sharing"

INST="e2e-inst-alice"
INST_BOB="e2e-inst-bob"
INST_OBJ="MemoryStore/Tenants/$TID/Instances/$INST"
INST_BOB_OBJ="MemoryStore/Tenants/$TID/Instances/$INST_BOB"

# A.1 alice creates an instance
section "A.1 alice PUT Instance → 201 + ACL written"
RESP_A=$(NPUT "$T_ALICE" "$BASE/Instances/$INST" '{"description":"alice instance"}')
assert_in "alice PUT Instance → 201" \
  "$(NPUTC "$T_ALICE" "$BASE/Instances/$INST" '{"description":"alice instance"}')" "200" "201"
assert_contains "response has name" "\"name\": \"$INST\"" "$RESP_A"
sleep 2
assert_eq "ext_proc wrote owner ACL for alice" "$ALICE_UID" \
  "$(acl_owner MemoryStore "$INST")"

# A.2 bob creates his own instance (default_acl gives all-users Contributor)
section "A.2 bob PUT his own Instance → 201"
assert_in "bob PUT Instance → 201" \
  "$(NPUTC "$T_BOB" "$BASE/Instances/$INST_BOB" '{"description":"bob instance"}')" "200" "201"
sleep 2
assert_eq "ext_proc wrote owner ACL for bob" "$BOB_UID" \
  "$(acl_owner MemoryStore "$INST_BOB")"

# A.3 alice GETs her instance; bob denied on alice's
section "A.3 resource-level isolation"
assert_eq "alice GET own Instance → 200" "200" \
  "$(NHC "$T_ALICE" "$BASE/Instances/$INST")"
assert_eq "bob GET alice's Instance (no share) → 403" "403" \
  "$(NHC "$T_BOB" "$BASE/Instances/$INST")"
assert_eq "alice GET bob's Instance (no share) → 403" "403" \
  "$(NHC "$T_ALICE" "$BASE/Instances/$INST_BOB")"

# A.4 list — X-Allowed-Ids filters to owned instances
section "A.4 list Instances (gateway_inject filter)"
LIST_A=$(NH "$T_ALICE" "$BASE/Instances")
assert_contains "alice list contains own instance" "\"$INST\"" "$LIST_A"
assert_not_contains "alice list excludes bob's instance" "\"$INST_BOB\"" "$LIST_A"

# A.5 alice shares to bob as viewer via /AccessManager/Tenants/{tid}/ACLs
section "A.5 alice shares Instance to bob (viewer)"
VIEWER_ROLE="AccessManager/Tenants/System/Roles/Viewer"
CONTRIBUTOR_ROLE="AccessManager/Tenants/System/Roles/Contributor"
assert_eq "bob cannot share alice's instance → 403" "403" \
  "$(curl -sS --max-time 10 -X PUT -H "Authorization: Bearer $T_BOB" \
    -H "Content-Type: application/json" \
    -d "{\"user_path\":\"$BOB_PATH\",\"object_path\":\"$INST_OBJ\",\"role_path\":\"$VIEWER_ROLE\"}" \
    -o /dev/null -w "%{http_code}" "$ACL_BASE")"
SHARE_RESP=$(curl -sS --max-time 10 -X PUT -H "Authorization: Bearer $T_ALICE" \
  -H "Content-Type: application/json" \
  -d "{\"user_path\":\"$BOB_PATH\",\"object_path\":\"$INST_OBJ\",\"role_path\":\"$VIEWER_ROLE\"}" \
  "$ACL_BASE")
assert_contains "share returns status ok" '"status"' "$SHARE_RESP"

# A.6 bob with viewer: GET ok, PUT/DELETE denied
section "A.6 bob has viewer on alice's Instance"
assert_eq "bob GET alice's Instance (viewer) → 200" "200" \
  "$(NHC "$T_BOB" "$BASE/Instances/$INST")"
assert_eq "bob PUT alice's Instance (viewer<contributor) → 403" "403" \
  "$(NPUTC "$T_BOB" "$BASE/Instances/$INST" '{"description":"hack"}')"
assert_eq "bob DELETE alice's Instance (viewer<owner) → 403" "403" \
  "$(NDEL "$T_BOB" "$BASE/Instances/$INST")"

# A.7 alice promotes bob to contributor
section "A.7 alice promotes bob to contributor"
assert_eq "alice promotes → 200" "200" \
  "$(curl -sS --max-time 10 -X PUT -H "Authorization: Bearer $T_ALICE" \
    -H "Content-Type: application/json" \
    -d "{\"user_path\":\"$BOB_PATH\",\"object_path\":\"$INST_OBJ\",\"role_path\":\"$CONTRIBUTOR_ROLE\"}" \
    -o /dev/null -w "%{http_code}" "$ACL_BASE")"
assert_eq "bob PUT alice's Instance (contributor) → 200" "200" \
  "$(NPUTC "$T_BOB" "$BASE/Instances/$INST" '{"description":"bob updated"}')"
assert_eq "bob DELETE alice's Instance (contributor<owner) → 403" "403" \
  "$(NDEL "$T_BOB" "$BASE/Instances/$INST")"

# A.8 alice revokes bob
section "A.8 alice revokes bob"
assert_eq "alice DELETE permission → 200" "200" \
  "$(curl -sS --max-time 10 -X DELETE -H "Authorization: Bearer $T_ALICE" \
    -H "Content-Type: application/json" \
    -d "{\"user_path\":\"$BOB_PATH\",\"object_path\":\"$INST_OBJ\"}" \
    -o /dev/null -w "%{http_code}" "$ACL_BASE")"
assert_eq "bob GET alice's Instance (revoked) → 403" "403" \
  "$(NHC "$T_BOB" "$BASE/Instances/$INST")"


# ════════════════════════════════════════════════════════════════════════════
# PART B — Memory lifecycle + Query action
# ════════════════════════════════════════════════════════════════════════════
banner "PART B — Memory lifecycle + Query action"

MEM_ID="mem-001"
MEM_BASE="$BASE/Instances/$INST/Memories"

# B.1 alice creates a memory (sub-resource inherits instance ACL)
section "B.1 alice PUT Memory → 201"
RESP_M=$(NPUT "$T_ALICE" "$MEM_BASE/$MEM_ID" \
  '{"content":"The sky is blue","tags":["nature"]}')
assert_in "alice PUT Memory → 201" \
  "$(NPUTC "$T_ALICE" "$MEM_BASE/$MEM_ID" '{"content":"The sky is blue","tags":["nature"]}')" "200" "201"
assert_contains "response has id" "\"id\": \"$MEM_ID\"" "$RESP_M"
sleep 2
assert_match "ACL row written for memory" '^[1-9][0-9]*$' \
  "$(acl_count MemoryStore "$MEM_ID")"

# B.2 alice GETs the memory; bob denied (no share on parent)
section "B.2 resource-level isolation on Memory"
assert_eq "alice GET Memory → 200" "200" \
  "$(NHC "$T_ALICE" "$MEM_BASE/$MEM_ID")"
assert_eq "bob GET alice's Memory (no share) → 403" "403" \
  "$(NHC "$T_BOB" "$MEM_BASE/$MEM_ID")"

# B.3 alice lists memories
section "B.3 alice lists Memories"
LIST_M=$(NH "$T_ALICE" "$MEM_BASE")
assert_contains "list contains mem-001" "\"$MEM_ID\"" "$LIST_M"

# B.4 Query action (POST .../Memories/Query) — Viewer permission required
section "B.4 POST Memories/Query (Viewer)"
assert_eq "no-token Query → 401" "401" \
  "$(http_code -X POST "$MEM_BASE/Query")"
QRESP=$(NPOST "$T_ALICE" "$MEM_BASE/Query" '{"query":"sky"}')
assert_contains "Query returns results array" '"results"' "$QRESP"
assert_contains "Query found the memory" "\"$MEM_ID\"" "$QRESP"
QRESP_MISS=$(NPOST "$T_ALICE" "$MEM_BASE/Query" '{"query":"zzznomatch"}')
assert_contains "Query with no match returns empty results" '"results": []' "$QRESP_MISS"

# B.5 alice updates and deletes the memory
section "B.5 alice updates + deletes Memory"
assert_in "alice PUT Memory (update) → 200" \
  "$(NPUTC "$T_ALICE" "$MEM_BASE/$MEM_ID" '{"content":"The sky is very blue"}')" "200" "201"
assert_eq "alice DELETE Memory → 204" "204" \
  "$(NDEL "$T_ALICE" "$MEM_BASE/$MEM_ID")"
sleep 2
assert_eq "ACL row gone after Memory delete" "0" \
  "$(acl_count MemoryStore "$MEM_ID")"
assert_eq "alice GET deleted Memory → 404" "404" \
  "$(NHC "$T_ALICE" "$MEM_BASE/$MEM_ID")"


# ════════════════════════════════════════════════════════════════════════════
# PART C — Template lifecycle + Filters / LLMExtraction actions
# ════════════════════════════════════════════════════════════════════════════
banner "PART C — Template lifecycle + Filters/LLMExtraction actions"

TPL="tpl-default"
TPL_BASE="$BASE/Instances/$INST/Templates"

# C.1 alice creates a template
section "C.1 alice PUT Template → 201"
RESP_T=$(NPUT "$T_ALICE" "$TPL_BASE/$TPL" \
  '{"description":"default extraction template","enabled":true}')
assert_in "alice PUT Template → 201" \
  "$(NPUTC "$T_ALICE" "$TPL_BASE/$TPL" '{"description":"default extraction template","enabled":true}')" "200" "201"
assert_contains "response has name" "\"name\": \"$TPL\"" "$RESP_T"
sleep 2
assert_match "ACL row written for template" '^[1-9][0-9]*$' \
  "$(acl_count MemoryStore "$TPL")"

# C.2 alice GETs; bob denied
section "C.2 resource-level isolation on Template"
assert_eq "alice GET Template → 200" "200" \
  "$(NHC "$T_ALICE" "$TPL_BASE/$TPL")"
assert_eq "bob GET alice's Template (no share) → 403" "403" \
  "$(NHC "$T_BOB" "$TPL_BASE/$TPL")"

# C.3 alice lists templates
section "C.3 alice lists Templates"
LIST_T=$(NH "$T_ALICE" "$TPL_BASE")
assert_contains "list contains $TPL" "\"$TPL\"" "$LIST_T"

# C.4 PATCH template
section "C.4 alice PATCH Template"
assert_eq "alice PATCH Template → 200" "200" \
  "$(curl -sS --max-time 10 -X PATCH -H "Authorization: Bearer $T_ALICE" \
    -H "Content-Type: application/json" -d '{"description":"updated"}' \
    -o /dev/null -w "%{http_code}" "$TPL_BASE/$TPL")"
assert_eq "bob PATCH alice's Template (no share) → 403" "403" \
  "$(curl -sS --max-time 10 -X PATCH -H "Authorization: Bearer $T_BOB" \
    -H "Content-Type: application/json" -d '{"description":"hack"}' \
    -o /dev/null -w "%{http_code}" "$TPL_BASE/$TPL")"

# C.5 Filters action (POST .../Templates/{name}/Filters) — Contributor required
section "C.5 POST Templates/Filters (Contributor)"
assert_eq "no-token Filters → 401" "401" \
  "$(http_code -X POST "$TPL_BASE/$TPL/Filters")"
assert_eq "bob Filters (no share, <contributor) → 403" "403" \
  "$(NPOSTC "$T_BOB" "$TPL_BASE/$TPL/Filters" \
    '{"allow_agents":["agent-1"],"deny_agents":[]}')"
FRESP=$(NPOST "$T_ALICE" "$TPL_BASE/$TPL/Filters" \
  '{"allow_agents":["agent-1"],"deny_agents":["agent-bad"]}')
assert_contains "Filters response has status ok" '"status": "ok"' "$FRESP"
assert_contains "Filters response echoes allow_agents" '"allow_agents"' "$FRESP"

# C.6 LLMExtraction action (POST .../Templates/{name}/LLMExtraction) — Contributor required
section "C.6 POST Templates/LLMExtraction (Contributor)"
assert_eq "no-token LLMExtraction → 401" "401" \
  "$(http_code -X POST "$TPL_BASE/$TPL/LLMExtraction")"
assert_eq "bob LLMExtraction (no share, <contributor) → 403" "403" \
  "$(NPOSTC "$T_BOB" "$TPL_BASE/$TPL/LLMExtraction" '{"enabled":true}')"
LRESP=$(NPOST "$T_ALICE" "$TPL_BASE/$TPL/LLMExtraction" \
  '{"enabled":true,"model":"qwen-turbo","prompt":"extract memories"}')
assert_contains "LLMExtraction response has status ok" '"status": "ok"' "$LRESP"
assert_contains "LLMExtraction response echoes enabled" '"enabled"' "$LRESP"

# C.7 alice deletes the template
section "C.7 alice DELETE Template"
assert_eq "bob DELETE alice's Template (no share) → 403" "403" \
  "$(NDEL "$T_BOB" "$TPL_BASE/$TPL")"
assert_eq "alice DELETE Template → 204" "204" \
  "$(NDEL "$T_ALICE" "$TPL_BASE/$TPL")"
sleep 2
assert_eq "ACL row gone after Template delete" "0" \
  "$(acl_count MemoryStore "$TPL")"


# ════════════════════════════════════════════════════════════════════════════
# PART D — Instance cascade delete
# ════════════════════════════════════════════════════════════════════════════
banner "PART D — Instance cascade delete"

# Recreate a memory and template under alice's instance, then delete the instance
section "D.1 seed child resources under alice's Instance"
NPUTC "$T_ALICE" "$MEM_BASE/mem-cascade" '{"content":"cascade test"}' >/dev/null
NPUTC "$T_ALICE" "$TPL_BASE/tpl-cascade" '{"description":"cascade test"}' >/dev/null
sleep 2
assert_match "mem-cascade ACL exists" '^[1-9][0-9]*$' \
  "$(acl_count MemoryStore "mem-cascade")"
assert_match "tpl-cascade ACL exists" '^[1-9][0-9]*$' \
  "$(acl_count MemoryStore "tpl-cascade")"

section "D.2 alice DELETE Instance cascades children"
assert_eq "bob DELETE alice's Instance (no share) → 403" "403" \
  "$(NDEL "$T_BOB" "$BASE/Instances/$INST")"
assert_eq "alice DELETE Instance → 204" "204" \
  "$(NDEL "$T_ALICE" "$BASE/Instances/$INST")"
sleep 2
assert_eq "Instance ACL gone" "0" "$(acl_count MemoryStore "$INST")"
assert_eq "alice GET deleted Instance → 404" "404" \
  "$(NHC "$T_ALICE" "$BASE/Instances/$INST")"
assert_eq "alice GET Memory under deleted Instance → 404" "404" \
  "$(NHC "$T_ALICE" "$MEM_BASE/mem-cascade")"
assert_eq "alice GET Template under deleted Instance → 404" "404" \
  "$(NHC "$T_ALICE" "$TPL_BASE/tpl-cascade")"


# ════════════════════════════════════════════════════════════════════════════
# PART E — Cleanup
# ════════════════════════════════════════════════════════════════════════════
banner "PART E — Cleanup"

section "E.1 delete bob's instance"
assert_eq "bob DELETE own Instance → 204" "204" \
  "$(NDEL "$T_BOB" "$BASE/Instances/$INST_BOB")"
sleep 2
assert_eq "bob's Instance ACL gone" "0" "$(acl_count MemoryStore "$INST_BOB")"

print_summary
exit "$FAIL"
