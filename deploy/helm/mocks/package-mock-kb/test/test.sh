#!/usr/bin/env bash
# ============================================================================
# test.sh — KB end-to-end lifecycle test, single entrypoint
#
# Covers all 7 sections of diagrams/api-specs/knowledgebase/apioption.md
# as one continuous user story. Each PART corresponds to one section and
# has its own ownership/permission lifecycle:
#
#   PART A  (1) 知识库管理        — KB CRUD + share via /acl/v1
#   PART B  (2) 模型配置          — kb-admins-only model lifecycle
#   PART C  (3) 提示词管理        — read all-users / edit kb-admins
#   PART D  (4) 术语管理          — kb-admins-only jargon library
#   PART E  (7) 知识库-术语库关联 — bind / unbind alice's lib to alice's KB
#   PART F  (5) 问答              — conversation per-thread ownership
#   PART G  (6) 检索              — stateless fusion search (all-users)
#   PART H  cleanup               — final teardown
#
# Cast:
#   alice = kb-admin    (in kb-admins + all-users; can manage models/prompts/
#                        jargons/bindings; is the KB owner across the test)
#   bob   = rubik-admin (in rubik-admins + all-users; for KB he is a clean
#                        "another user" — has KB path-level (kb_browse,
#                        kb_edit, kb_conv_*, kb_retrieval, kb_prompt_view)
#                        but NOT kb_admin_full / kb_model_* / kb_jargon_*
#                        / kb_jargon_bind. Lets us verify path-level vs.
#                        resource-level deny mechanics independently.)
#
# Env overrides:
#   GATEWAY     (default https://localhost:30080)
#   NAMESPACE   (default mock-kb)
#   IAM_NS      (default aidp-iam)
#   KEYCLOAK_NS (default keycloak)
#   POD_LABEL   (default app=mock-kb — change for real KB deployment)
# ============================================================================
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/lib/common.sh"

POD_LABEL="${POD_LABEL:-app=mock-kb}"

banner "KB END-TO-END TEST — apioption.md sections (1)–(7) lifecycle"
init_tokens

# ── prereq: pod, OIDC, IAM app, no-token rejects, 5 tokens ─────────────────
section "Prereq — environment ready"
ready=$(kubectl -n "$NAMESPACE" get pod -l "$POD_LABEL" \
  -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
assert_eq "mock-kb Pod Ready=True" "True" "$ready"
assert_eq "GET /realms/aidp/.well-known/openid-configuration → 200" "200" \
  "$(http_code "$GATEWAY/realms/aidp/.well-known/openid-configuration")"
APPS=$(NH "$T_ADMIN" "$GATEWAY/api/v1/apps")
assert_contains "GET /api/v1/apps contains 'knowledgebase'" '"knowledgebase"' "$APPS"
assert_eq "GET /kb/health (no token) → 401" "401" "$(http_code "$GATEWAY/kb/health")"
assert_eq "POST /kb/knowledge_bases/add (no token) → 401" "401" \
  "$(http_code -X POST "$GATEWAY/kb/knowledge_bases/add")"
for v in T_ADMIN T_KBADMIN T_RUBIKADMIN T_MEMADMIN T_USER; do
  assert_match "$v is JWT-shape" '^[A-Za-z0-9_.-]{800,}$' "${!v}"
done

# ── Resolve UUIDs (needed by /acl/v1 share calls in PART A) ─────────────────
ALICE_UID=$(kubectl -n "$KEYCLOAK_NS" exec iam-store-0 -c postgres -- \
  psql -U keycloak -d keycloak -tA -c \
  "SELECT id FROM user_entity WHERE username='kb-admin' AND realm_id=(SELECT id FROM realm WHERE name='aidp')" 2>/dev/null | tr -d ' \r\n')
BOB_UID=$(kubectl -n "$KEYCLOAK_NS" exec iam-store-0 -c postgres -- \
  psql -U keycloak -d keycloak -tA -c \
  "SELECT id FROM user_entity WHERE username='rubik-admin' AND realm_id=(SELECT id FROM realm WHERE name='aidp')" 2>/dev/null | tr -d ' \r\n')
[ -n "$ALICE_UID" ] || { echo "FATAL: cannot resolve kb-admin UUID"; exit 2; }
[ -n "$BOB_UID" ]   || { echo "FATAL: cannot resolve rubik-admin UUID"; exit 2; }

T_ALICE="$T_KBADMIN"
T_BOB="$T_RUBIKADMIN"

# ════════════════════════════════════════════════════════════════════════════
# PART A — apioption.md (1) 知识库管理
#   Lifecycle: alice creates KB → owner full access → no-share denial
#   → /acl/v1 share to bob viewer → promote contributor → revoke
# ════════════════════════════════════════════════════════════════════════════
banner "PART A — KB lifecycle (apioption.md 1) + /acl/v1 sharing"

KB_NAME="e2e-lifecycle-kb"

# A.1 alice creates the KB
section "A.1 alice POST /kb/knowledge_bases/add"
RESP_CREATE=$(NPOST "$T_ALICE" "$GATEWAY/kb/knowledge_bases/add" \
  "{\"name\":\"$KB_NAME\",\"description\":\"alice's KB\"}")
CODE_CREATE=$(NPOSTC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/add" \
  "{\"name\":\"${KB_NAME}-2\"}")
assert_in "alice creates KB → 201" "$CODE_CREATE" "200" "201"
KBID=$(echo "$RESP_CREATE" | json_get data.KDSID)
assert_match "response data.KDSID is real id" '^[A-Za-z0-9-]{4,}$' "$KBID"
sleep 2
assert_eq "ext_proc wrote owner ACL — subject_id == alice UUID" "$ALICE_UID" \
  "$(acl_owner knowledgebase "$KBID")"

# A.2 alice GETs every read endpoint
section "A.2 alice runs all GET endpoints (owner ⊇ viewer)"
assert_eq "GET /page"                            "200" "$(NHC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/page")"
assert_eq "GET /count"                            "200" "$(NHC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/count")"
assert_eq "GET /knowledge_bases?kbs_id=<own>"     "200" "$(NHC "$T_ALICE" "$GATEWAY/kb/knowledge_bases?kbs_id=$KBID")"
assert_eq "GET /mappings?kbs_id=<own>"            "200" "$(NHC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/mappings?kbs_id=$KBID")"
assert_eq "GET /mappings/count?kbs_id=<own>"      "200" "$(NHC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/mappings/count?kbs_id=$KBID")"
assert_eq "GET /files?kbs_id=<own>"               "200" "$(NHC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/files?kbs_id=$KBID")"
assert_eq "GET /files/count?kbs_id=<own>"         "200" "$(NHC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/files/count?kbs_id=$KBID")"
assert_eq "GET /files/history?kbs_id=<own>"       "200" "$(NHC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/files/history?kbs_id=$KBID")"
assert_eq "GET /files/filesystem"                 "200" "$(NHC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/files/filesystem")"

# A.3 alice does write operations
section "A.3 alice POSTs (modify / mappings+/- / files+/-)"
assert_eq "POST /modify"                          "200" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/modify" \
    "{\"kbs_id\":\"$KBID\",\"name\":\"${KB_NAME}-renamed\"}")"

RESP_M=$(NPOST "$T_ALICE" "$GATEWAY/kb/knowledge_bases/mappings/add" \
  "{\"kbs_id\":\"$KBID\",\"src_dir\":\"/d\",\"fs_id\":\"f1\",\"fs_name\":\"local\",\"channel_name\":\"c1\"}")
assert_in "POST /mappings/add"                    \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/mappings/add" \
    "{\"kbs_id\":\"$KBID\",\"src_dir\":\"/d2\",\"fs_id\":\"f2\",\"fs_name\":\"local\",\"channel_name\":\"c2\"}")" "200" "201"
DM_ID=$(echo "$RESP_M" | json_get data.CHANNELID)

UP_CODE=$(printf 'lifecycle\n' | curl -sS --max-time 10 -X POST \
  -H "Authorization: Bearer $T_ALICE" \
  -F "kbs_id=$KBID" -F "files=@-;filename=t.txt;type=text/plain" \
  -o /dev/null -w "%{http_code}" "$GATEWAY/kb/knowledge_bases/files/upload")
assert_in "POST /files/upload"                    "$UP_CODE" "200" "201"

assert_eq "POST /mappings/remove (no parent ACL cascade)" "200" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/mappings/remove" \
    "{\"kbs_id\":\"$KBID\",\"kbs_dm_id\":\"$DM_ID\"}")"
assert_in "POST /files/remove (no parent ACL cascade)" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/files/remove" \
    "{\"kbs_id\":\"$KBID\",\"file_ids\":[]}")" "200" "201"
sleep 1
assert_match "parent KB ACL intact after child removes" '^[1-9][0-9]*$' \
  "$(acl_count knowledgebase "$KBID")"

# A.4 bob without share — denied on resource ops
section "A.4 bob (no share) denied on resource-level KB ops"
assert_eq "bob GET /knowledge_bases?kbs_id=alice's → 403" "403" \
  "$(NHC "$T_BOB" "$GATEWAY/kb/knowledge_bases?kbs_id=$KBID")"
assert_eq "bob POST /modify on alice's → 403"     "403" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/knowledge_bases/modify" "{\"kbs_id\":\"$KBID\",\"name\":\"x\"}")"
assert_eq "bob POST /mappings/add on alice's → 403" "403" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/knowledge_bases/mappings/add" \
    "{\"kbs_id\":\"$KBID\",\"src_dir\":\"/x\",\"fs_id\":\"f\",\"fs_name\":\"l\",\"channel_name\":\"c\"}")"
assert_eq "bob POST /remove on alice's → 403"     "403" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/knowledge_bases/remove" "{\"kbs_id\":\"$KBID\"}")"

# A.5 alice shares to bob as VIEWER
section "A.5 alice POST /acl/v1/.../permissions {viewer}"
assert_eq "bob (not owner) cannot share → 403"    "403" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/acl/v1/resources/$KBID/permissions" \
    "{\"subject_type\":\"user\",\"subject_id\":\"$BOB_UID\",\"permission\":\"viewer\",\"app_name\":\"knowledgebase\",\"resource_type\":\"kb\"}")"
SHARE_RESP=$(NPOST "$T_ALICE" "$GATEWAY/acl/v1/resources/$KBID/permissions" \
  "{\"subject_type\":\"user\",\"subject_id\":\"$BOB_UID\",\"permission\":\"viewer\",\"app_name\":\"knowledgebase\",\"resource_type\":\"kb\"}")
ACL_ID=$(echo "$SHARE_RESP" | json_get id)
assert_match "share returns acl id"               '^[0-9]+$' "$ACL_ID"
LIST_PERM=$(NH "$T_ALICE" "$GATEWAY/acl/v1/resources/$KBID/permissions?app_name=knowledgebase&resource_type=kb")
assert_contains "list shows bob"                  "\"subject_id\":\"$BOB_UID\"" "$LIST_PERM"
assert_contains "list shows 'viewer'"             '"permission":"viewer"' "$LIST_PERM"

# A.6 bob with viewer ✓ GET, ✗ writes
section "A.6 bob has viewer"
assert_eq "bob GET single → 200"                  "200" "$(NHC "$T_BOB" "$GATEWAY/kb/knowledge_bases?kbs_id=$KBID")"
assert_eq "bob GET /mappings → 200"               "200" "$(NHC "$T_BOB" "$GATEWAY/kb/knowledge_bases/mappings?kbs_id=$KBID")"
assert_eq "bob POST /modify (viewer<contributor) → 403" "403" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/knowledge_bases/modify" "{\"kbs_id\":\"$KBID\",\"name\":\"x\"}")"
assert_eq "bob POST /remove (viewer<owner) → 403" "403" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/knowledge_bases/remove" "{\"kbs_id\":\"$KBID\"}")"

# A.7 alice promotes bob to CONTRIBUTOR
section "A.7 alice PUT promotion to contributor"
assert_eq "bob cannot self-promote → 403"         "403" \
  "$(curl -sS --max-time 10 -X PUT -H "Authorization: Bearer $T_BOB" \
    -H "Content-Type: application/json" -d '{"permission":"contributor"}' \
    -o /dev/null -w "%{http_code}" "$GATEWAY/acl/v1/resources/$KBID/permissions/$ACL_ID")"
assert_eq "alice promotes bob → 200"              "200" \
  "$(curl -sS --max-time 10 -X PUT -H "Authorization: Bearer $T_ALICE" \
    -H "Content-Type: application/json" -d '{"permission":"contributor"}' \
    -o /dev/null -w "%{http_code}" "$GATEWAY/acl/v1/resources/$KBID/permissions/$ACL_ID")"

# A.8 bob with contributor ✓ modify, ✗ remove
section "A.8 bob has contributor"
assert_eq "bob POST /modify → 200"                "200" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/knowledge_bases/modify" \
    "{\"kbs_id\":\"$KBID\",\"name\":\"bob-modified\"}")"
RESP_BM=$(NPOST "$T_BOB" "$GATEWAY/kb/knowledge_bases/mappings/add" \
  "{\"kbs_id\":\"$KBID\",\"src_dir\":\"/b\",\"fs_id\":\"fb\",\"fs_name\":\"l\",\"channel_name\":\"cb\"}")
DM_BOB=$(echo "$RESP_BM" | json_get data.CHANNELID)
assert_match "bob mapping/add returned CHANNELID" '^[A-Za-z0-9-]{4,}$' "$DM_BOB"
assert_eq "bob POST /mappings/remove → 200"       "200" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/knowledge_bases/mappings/remove" \
    "{\"kbs_id\":\"$KBID\",\"kbs_dm_id\":\"$DM_BOB\"}")"
assert_eq "bob POST /remove (contributor<owner) → 403" "403" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/knowledge_bases/remove" "{\"kbs_id\":\"$KBID\"}")"

# A.9 alice revokes bob's permission
section "A.9 alice DELETE revokes bob"
assert_eq "bob cannot self-revoke → 403"          "403" \
  "$(curl -sS --max-time 10 -X DELETE -H "Authorization: Bearer $T_BOB" \
    -o /dev/null -w "%{http_code}" "$GATEWAY/acl/v1/resources/$KBID/permissions/$ACL_ID")"
assert_eq "alice DELETE → 200"                    "200" \
  "$(curl -sS --max-time 10 -X DELETE -H "Authorization: Bearer $T_ALICE" \
    -o /dev/null -w "%{http_code}" "$GATEWAY/acl/v1/resources/$KBID/permissions/$ACL_ID")"
assert_eq "bob GET (revoked) → 403"               "403" \
  "$(NHC "$T_BOB" "$GATEWAY/kb/knowledge_bases?kbs_id=$KBID")"
assert_eq "bob POST /modify (revoked) → 403"      "403" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/knowledge_bases/modify" "{\"kbs_id\":\"$KBID\",\"name\":\"x\"}")"

# (KB stays — used by PART E binding)


# ════════════════════════════════════════════════════════════════════════════
# PART B — apioption.md (2) 模型配置 (kb-admins-only)
# ════════════════════════════════════════════════════════════════════════════
banner "PART B — Model config lifecycle (apioption.md 2, kb-admins-only)"

# B.1 path-level deny for non-kb-admins
section "B.1 bob (not in kb-admins) denied at path-level"
assert_eq "no-token GET /models/config → 401"     "401" "$(http_code "$GATEWAY/kb/models/config")"
assert_eq "bob GET /models/config → 403"          "403" "$(NHC "$T_BOB" "$GATEWAY/kb/models/config")"
assert_eq "bob POST /models/config/add → 403"     "403" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/models/config/add" \
    '{"model_type":"LLM","model_name":"x","model_id":"x","api_url":"x","api_key":"x"}')"
assert_eq "bob POST /models/config/modify → 403"  "403" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/models/config/modify" '{"id":"x","model_api":{}}')"
assert_eq "bob POST /models/config/remove → 403"  "403" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/models/config/remove" '{"id":"x"}')"

# B.2 alice lifecycle
section "B.2 alice creates a model config"
RESP_MC=$(NPOST "$T_ALICE" "$GATEWAY/kb/models/config/add" \
  '{"model_type":"LLM","model_name":"qwen-test","model_id":"qt-1","api_url":"http://q","api_key":"k","provider":"prov"}')
assert_in "alice POST /add → 201" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/models/config/add" \
    '{"model_type":"LLM","model_name":"qwen-2","model_id":"qt-2","api_url":"http://q","api_key":"k"}')" "200" "201"
MID=$(echo "$RESP_MC" | json_get data)
assert_match "response.data carries model id"     '^[A-Za-z0-9-]+$' "$MID"
sleep 2
assert_match "ACL written (creator + kb-admins)"  '^[12]$' "$(acl_count knowledgebase "$MID")"

section "B.3 alice GET /models/config (list + shape)"
LIST=$(NH "$T_ALICE" "$GATEWAY/kb/models/config")
assert_contains "list has MODELAPI"               '"MODELAPI"' "$LIST"
assert_contains "MODELAPI is JSON-encoded string" '"MODELAPI":"{\"' "$LIST"

section "B.4 alice modifies + removes the model"
assert_eq "alice POST /modify → 200"              "200" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/models/config/modify" "{\"id\":\"$MID\",\"model_api\":{\"model_name\":\"renamed\"}}")"
assert_eq "alice POST /remove → 200"              "200" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/models/config/remove" "{\"id\":\"$MID\"}")"
sleep 2
assert_eq "ACL row gone after remove (owner cascade)" "0" "$(acl_count knowledgebase "$MID")"


# ════════════════════════════════════════════════════════════════════════════
# PART C — apioption.md (3) 提示词管理 (read all-users / edit kb-admins)
# ════════════════════════════════════════════════════════════════════════════
banner "PART C — Prompt lifecycle (apioption.md 3)"

section "C.1 no-token / negative axes"
assert_eq "no-token GET /prompts/page → 401"      "401" "$(http_code "$GATEWAY/kb/prompts/page")"
assert_eq "no-token POST /prompts/add → 401"      "401" "$(http_code -X POST "$GATEWAY/kb/prompts/add")"

section "C.2 alice creates a prompt group"
RESP_P=$(NPOST "$T_ALICE" "$GATEWAY/kb/prompts/add" \
  '{"title":"e2e-prompt","description":"by alice","mode":"NAIVE","prompts":{"system":"hi"}}')
assert_in "alice POST /prompts/add → 201" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/prompts/add" \
    '{"title":"e2e-prompt-2","description":"d2","mode":"DEEP_QA","prompts":{}}')" "200" "201"
PID=$(echo "$RESP_P" | json_get data.id)
assert_match "data.id is real id"                 '^[A-Za-z0-9-]+$' "$PID"
sleep 2
assert_match "ACL row written (creator + kb-admins)" '^[12]$' "$(acl_count knowledgebase "$PID")"

section "C.3 alice + bob can READ (kb_prompt_view = all-users)"
assert_eq "alice GET /detail → 200"               "200" "$(NHC "$T_ALICE" "$GATEWAY/kb/prompts/detail?id=$PID")"
DET=$(NH "$T_ALICE" "$GATEWAY/kb/prompts/detail?id=$PID")
assert_contains "detail has 'kb_ids'"             '"kb_ids"' "$DET"
assert_contains "detail has 'prompts'"            '"prompts"' "$DET"
assert_eq "alice GET /options → 200"              "200" "$(NHC "$T_ALICE" "$GATEWAY/kb/prompts/options")"
assert_eq "alice GET /page → 200"                 "200" "$(NHC "$T_ALICE" "$GATEWAY/kb/prompts/page")"
assert_eq "bob (all-users) GET /detail → 200"     "200" "$(NHC "$T_BOB" "$GATEWAY/kb/prompts/detail?id=$PID")"
assert_eq "bob GET /options → 200"                "200" "$(NHC "$T_BOB" "$GATEWAY/kb/prompts/options")"
assert_eq "bob GET /page → 200"                   "200" "$(NHC "$T_BOB" "$GATEWAY/kb/prompts/page")"

section "C.4 only kb-admins can EDIT"
assert_eq "bob POST /modify (not kb-admin) → 403" "403" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/prompts/modify" "{\"id\":\"$PID\",\"title\":\"hack\"}")"
assert_eq "bob POST /remove (not kb-admin) → 403" "403" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/prompts/remove" "{\"id\":\"$PID\"}")"
assert_eq "alice POST /modify → 200"              "200" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/prompts/modify" "{\"id\":\"$PID\",\"title\":\"renamed\"}")"
assert_eq "alice POST /remove → 200"              "200" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/prompts/remove" "{\"id\":\"$PID\"}")"
sleep 2
assert_eq "ACL row gone after remove"             "0" "$(acl_count knowledgebase "$PID")"


# ════════════════════════════════════════════════════════════════════════════
# PART D — apioption.md (4) 术语管理 (kb-admins-only across the section)
#   The library created here stays alive into PART E (binding).
# ════════════════════════════════════════════════════════════════════════════
banner "PART D — Jargon library lifecycle (apioption.md 4)"

LIB="e2e-jargon-lib"
JNAME="freeride"

section "D.1 bob (not kb-admin) denied at path-level"
assert_eq "no-token GET /jargon_groups → 401"     "401" "$(http_code "$GATEWAY/kb/jargon_groups")"
assert_eq "bob GET /jargon_groups → 403"          "403" "$(NHC "$T_BOB" "$GATEWAY/kb/jargon_groups")"
assert_eq "bob POST /jargon_groups/add → 403"     "403" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/jargon_groups/add" "{\"jargon_lib_name\":\"hack\"}")"

section "D.2 alice creates jargon library"
assert_in "alice POST /jargon_groups/add → 201" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/jargon_groups/add" \
    "{\"jargon_lib_name\":\"$LIB\",\"description\":\"e2e\"}")" "200" "201"
sleep 2
assert_match "ACL row(s) written for lib"         '^[12]$' "$(acl_count knowledgebase "$LIB")"
LIST=$(NH "$T_ALICE" "$GATEWAY/kb/jargon_groups")
assert_contains "list contains the lib"           "$LIB" "$LIST"
assert_eq "GET /jargon_groups/version → 200"      "200" \
  "$(NHC "$T_ALICE" "$GATEWAY/kb/jargon_groups/version?jargon_lib_name=$LIB")"

section "D.3 alice manages jargon entries"
assert_in "alice POST /jargons/add (batch) → 201" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/jargons/add" \
    "{\"jargon_info_list\":[{\"jargon_name\":\"$JNAME\",\"description\":\"d\",\"target_term\":[\"free\"],\"jargon_lib_name\":\"$LIB\",\"creator_name\":\"alice\",\"creator_id\":\"$ALICE_UID\"}]}")" "200" "201"
assert_eq "alice GET /jargon_groups/jargons → 200" "200" \
  "$(NHC "$T_ALICE" "$GATEWAY/kb/jargon_groups/jargons?jargon_lib_name=$LIB")"
assert_eq "alice GET /jargons_groups/jargon → 200" "200" \
  "$(NHC "$T_ALICE" "$GATEWAY/kb/jargons_groups/jargon?jargon_lib_name=$LIB")"
assert_eq "alice POST /jargons/modify → 200"      "200" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/jargons/modify" \
    "{\"jargon_lib_name\":\"$LIB\",\"jargon_name\":\"$JNAME\",\"description\":\"updated\"}")"

# (lib stays — used by PART E)


# ════════════════════════════════════════════════════════════════════════════
# PART E — apioption.md (7) 知识库 ↔ 术语库关联 (kb-admins, needs KB+lib)
#   Uses alice's KB ($KBID) from PART A and alice's jargon lib ($LIB) from D.
# ════════════════════════════════════════════════════════════════════════════
banner "PART E — KB ↔ jargon binding (apioption.md 7)"

section "E.1 before bind: GET /knowledge_bases/jargon_groups returns empty"
PRE_BIND=$(NH "$T_ALICE" "$GATEWAY/kb/knowledge_bases/jargon_groups?kb_name=${KB_NAME}-renamed")
assert_contains "before bind: jargon_lib_name is empty" '"jargon_lib_name":""' "$PRE_BIND"

section "E.2 bob (not kb-admin) denied; alice binds"
assert_eq "bob POST /jargon_groups/knowledge_bases/add → 403" "403" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/jargon_groups/knowledge_bases/add" \
    "{\"kb_name\":\"${KB_NAME}-renamed\",\"jargon_lib_name\":\"$LIB\"}")"
assert_in "alice POST /jargon_groups/knowledge_bases/add → 201" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/jargon_groups/knowledge_bases/add" \
    "{\"kb_name\":\"${KB_NAME}-renamed\",\"jargon_lib_name\":\"$LIB\"}")" "200" "201"

section "E.3 after bind: GET reflects the binding"
POST_BIND=$(NH "$T_ALICE" "$GATEWAY/kb/knowledge_bases/jargon_groups?kb_name=${KB_NAME}-renamed")
assert_contains "after bind: jargon_lib_name = $LIB" "\"jargon_lib_name\":\"$LIB\"" "$POST_BIND"

section "E.4 alice unbinds; GET goes empty again"
assert_eq "alice POST /jargon_groups/knowledge_bases/remove → 200" "200" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/jargon_groups/knowledge_bases/remove" \
    "{\"kb_name\":\"${KB_NAME}-renamed\",\"jargon_lib_name\":\"$LIB\"}")"
POST_UNBIND=$(NH "$T_ALICE" "$GATEWAY/kb/knowledge_bases/jargon_groups?kb_name=${KB_NAME}-renamed")
assert_contains "after unbind: jargon_lib_name is empty" '"jargon_lib_name":""' "$POST_UNBIND"


# ════════════════════════════════════════════════════════════════════════════
# PART F — apioption.md (5) 问答 (per-thread ownership; all-users for path)
# ════════════════════════════════════════════════════════════════════════════
banner "PART F — Conversation lifecycle (apioption.md 5)"

section "F.1 no-token rejects + alice/bob each start own thread"
assert_eq "no-token POST /start → 401"            "401" "$(http_code -X POST "$GATEWAY/kb/conversations/start")"
RESP_FA=$(NPOST "$T_ALICE" "$GATEWAY/kb/conversations/start" \
  '{"query":"hi","resources":[{"collection_name":"kb1"}],"thread_id":"","user_id":"a","output_format":"qa","rag_mode":"naive_rag","enable_proxy":false}')
TID_ALICE=$(echo "$RESP_FA" | json_get data.thread_id)
assert_match "alice thread_id"                    '^[A-Za-z0-9-]+$' "$TID_ALICE"
RESP_FB=$(NPOST "$T_BOB" "$GATEWAY/kb/conversations/start" \
  '{"query":"hi","resources":[],"thread_id":"","user_id":"b","output_format":"qa","rag_mode":"naive_rag"}')
TID_BOB=$(echo "$RESP_FB" | json_get data.thread_id)
assert_match "bob thread_id"                      '^[A-Za-z0-9-]+$' "$TID_BOB"

section "F.2 conversation list (all-users path; per-user GET works)"
assert_eq "alice GET /list → 200"                 "200" "$(NHC "$T_ALICE" "$GATEWAY/kb/conversations/list")"
assert_eq "bob GET /list → 200"                   "200" "$(NHC "$T_BOB"   "$GATEWAY/kb/conversations/list")"

section "F.3 single-thread access is owner-only"
assert_eq "alice GET ?thread_id=alice's → 200"    "200" \
  "$(NHC "$T_ALICE" "$GATEWAY/kb/conversations?thread_id=$TID_ALICE")"
assert_eq "bob GET ?thread_id=alice's → 403"      "403" \
  "$(NHC "$T_BOB"   "$GATEWAY/kb/conversations?thread_id=$TID_ALICE")"
assert_eq "bob GET ?thread_id=bob's → 200"        "200" \
  "$(NHC "$T_BOB"   "$GATEWAY/kb/conversations?thread_id=$TID_BOB")"

section "F.4 alice/bob each stop their own thread; cross-stop denied"
assert_eq "alice POST /stop alice's → 200"        "200" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/conversations/stop" "{\"thread_id\":\"$TID_ALICE\"}")"
assert_eq "bob POST /stop alice's → 403"          "403" \
  "$(NPOSTC "$T_BOB"   "$GATEWAY/kb/conversations/stop" "{\"thread_id\":\"$TID_ALICE\"}")"
assert_eq "bob POST /stop bob's → 200"            "200" \
  "$(NPOSTC "$T_BOB"   "$GATEWAY/kb/conversations/stop" "{\"thread_id\":\"$TID_BOB\"}")"

section "F.5 image generate/download (all-users, no per-image ACL)"
GBODY=$(NPOST "$T_ALICE" "$GATEWAY/kb/conversations/images/generate" '{"image_url":"/x.png"}')
assert_contains "alice generate has data.token"   '"token"' "$GBODY"
assert_eq "bob can also generate → 200"           "200" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/conversations/images/generate" '{"image_url":"/y.png"}')"
assert_eq "alice download → 200"                  "200" \
  "$(NHC "$T_ALICE" "$GATEWAY/kb/conversations/images/download?token=mock-tok")"

section "F.6 owners remove their own threads"
assert_eq "bob POST /remove alice's → 403"        "403" \
  "$(NPOSTC "$T_BOB"   "$GATEWAY/kb/conversations/remove" "{\"thread_id\":\"$TID_ALICE\"}")"
assert_eq "alice POST /remove alice's → 200"      "200" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/conversations/remove" "{\"thread_id\":\"$TID_ALICE\"}")"
assert_eq "bob POST /remove bob's → 200"          "200" \
  "$(NPOSTC "$T_BOB"   "$GATEWAY/kb/conversations/remove" "{\"thread_id\":\"$TID_BOB\"}")"


# ════════════════════════════════════════════════════════════════════════════
# PART G — apioption.md (6) 检索 (stateless fusion search, all-users)
# ════════════════════════════════════════════════════════════════════════════
banner "PART G — Retrieval (apioption.md 6)"

section "G fusion_search"
assert_eq "no-token → 401"                        "401" "$(http_code -X POST "$GATEWAY/kb/retrieval/fusion_search")"
assert_eq "alice minimal body → 200"              "200" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/retrieval/fusion_search" \
    '{"query":"q","kds_list":["k1"],"search_method":"hybrid_search"}')"
assert_eq "bob → 200"                             "200" \
  "$(NPOSTC "$T_BOB"   "$GATEWAY/kb/retrieval/fusion_search" \
    '{"query":"q","kds_list":[],"search_method":"vector_search"}')"
RBODY=$(NPOST "$T_ALICE" "$GATEWAY/kb/retrieval/fusion_search" \
  '{"query":"deep","kds_list":["k1","k2"],"search_method":"hybrid_search","reranking_enable":true,"reranking_mode":"high_accuracy","top_k":5}')
assert_contains "response has data array"         '"data"' "$RBODY"
assert_contains "response has total_return_count" '"total_return_count"' "$RBODY"


# ════════════════════════════════════════════════════════════════════════════
# PART H — Final cleanup
# ════════════════════════════════════════════════════════════════════════════
banner "PART H — Final cleanup"

# Remove jargon entry, then jargon lib
NPOSTC "$T_ALICE" "$GATEWAY/kb/jargons/remove" \
  "{\"jargon_lib_name\":\"$LIB\",\"jargon_name\":\"$JNAME\"}" >/dev/null
assert_eq "alice POST /jargon_groups/remove → 200" "200" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/jargon_groups/remove" "{\"jargon_lib_name\":\"$LIB\"}")"
sleep 1
assert_eq "ACL row gone for jargon lib"           "0" "$(acl_count knowledgebase "$LIB")"

# Owner-level KB delete cascades all KB ACL
assert_eq "alice POST /knowledge_bases/remove → 200" "200" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/remove" "{\"kbs_id\":\"$KBID\"}")"
sleep 2
assert_eq "ACL row(s) gone for KB (owner cascade)" "0" "$(acl_count knowledgebase "$KBID")"

# Tidy up the secondary KB created in A.1 (find by name; best-effort)
SECOND_KBID=$(NH "$T_ALICE" "$GATEWAY/kb/knowledge_bases/page?page_size=200" \
  | python -c "import sys,json
try:
  d=json.load(sys.stdin)
  for k in d.get('data',[]):
    if k.get('KDSNAME') == '${KB_NAME}-2': print(k.get('KDSID')); break
except: pass" 2>/dev/null)
[ -n "$SECOND_KBID" ] && NPOSTC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/remove" \
  "{\"kbs_id\":\"$SECOND_KBID\"}" >/dev/null

print_summary
exit "$FAIL"
