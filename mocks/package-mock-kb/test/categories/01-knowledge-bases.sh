#!/usr/bin/env bash
# ============================================================================
# 01-knowledge-bases.sh — KB ownership lifecycle
#
# Replaces the old endpoint-matrix style with a single coherent user story
# that exercises:
#   * apioption.md (1) 知识库管理   — KB / mappings / files lifecycle
#   * apioption.md (4) 术语管理     — jargon library + entries (reused here
#                                     to set up a binding the lifecycle needs)
#   * apioption.md (7) KB ↔ 术语库 — bind / unbind / verify GET shows binding
#   * IAM /acl/v1/* sharing API     — share / promote / revoke a KB
#
# Cast:
#   alice = kb-admin   (creator + owner of the KB; in kb-admins group, can
#                       create jargon libraries and bind them to KBs)
#   bob   = rubik-admin (in all-users group → has KB path-level access via
#                       kb_browse / kb_edit, but no kb-admins privileges and
#                       no resource-level ACL on alice's KB initially —
#                       a clean "another user" for share/permission tests)
#
# Why the test runs as a single story rather than per-endpoint matrix:
#   It mirrors how the system is actually used. Each phase is an assertion
#   about the IAM permission model:
#     - phase 1   : POST /add writes owner ACL
#     - phase 2-3 : owner has ALL permissions (viewer + contributor + owner)
#                   on every KB sub-resource (mappings, files, GET endpoints)
#     - phase 4-6 : kb-admins can create jargon libs and bind them to KBs;
#                   GET /knowledge_bases/jargon_groups reflects the binding
#     - phase 7   : non-shared user cannot access ANY KB resource operation
#     - phase 8-9 : viewer share lets them GET, but not modify
#     - phase 10-11: contributor share lets them modify, but not remove
#     - phase 12  : revoke restores 403
#     - phase 13  : owner-level /remove cascades all ACLs cleanly
# ============================================================================
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/../lib/common.sh"

banner "[01] KB OWNERSHIP LIFECYCLE — apioption.md (1)+(4)+(7) + /acl/v1"
init_tokens

# ── Resolve user UUIDs (needed for /acl/v1 share calls) ─────────────────────
ALICE_UID=$(kubectl -n "$KEYCLOAK_NS" exec postgres-0 -c postgres -- \
  psql -U keycloak -d keycloak -tA -c \
  "SELECT id FROM user_entity WHERE username='kb-admin' AND realm_id=(SELECT id FROM realm WHERE name='aidp')" 2>/dev/null | tr -d ' \r\n')
BOB_UID=$(kubectl -n "$KEYCLOAK_NS" exec postgres-0 -c postgres -- \
  psql -U keycloak -d keycloak -tA -c \
  "SELECT id FROM user_entity WHERE username='rubik-admin' AND realm_id=(SELECT id FROM realm WHERE name='aidp')" 2>/dev/null | tr -d ' \r\n')
[ -n "$ALICE_UID" ] || { echo "FATAL: cannot resolve kb-admin UUID"; exit 2; }
[ -n "$BOB_UID" ]   || { echo "FATAL: cannot resolve rubik-admin UUID"; exit 2; }

# Tokens: alice=kb-admin, bob=rubik-admin
T_ALICE="$T_KBADMIN"
T_BOB="$T_RUBIKADMIN"

# ════════════════════════════════════════════════════════════════════════════
# PHASE 1 — alice creates a KB (owner ACL auto-written by ext_proc)
# ════════════════════════════════════════════════════════════════════════════
section "Phase 1 — alice POST /kb/knowledge_bases/add"

assert_eq "no-token POST /add → 401" "401" \
  "$(http_code -X POST "$GATEWAY/kb/knowledge_bases/add")"

KB_NAME="e2e-lifecycle-kb"
RESP_CREATE=$(NPOST "$T_ALICE" "$GATEWAY/kb/knowledge_bases/add" \
  "{\"name\":\"$KB_NAME\",\"description\":\"alice's KB\"}")
CODE_CREATE=$(NPOSTC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/add" \
  "{\"name\":\"${KB_NAME}-2\"}")
assert_in "alice POST /add → 201" "$CODE_CREATE" "200" "201"
KBID=$(echo "$RESP_CREATE" | json_get data.KDSID)
assert_match "response data.KDSID is a real id" '^[A-Za-z0-9-]{4,}$' "$KBID"
sleep 2
assert_eq "ACL owner row written, subject_id == alice UUID" "$ALICE_UID" \
  "$(acl_owner knowledgebase "$KBID")"

# ════════════════════════════════════════════════════════════════════════════
# PHASE 2 — alice (owner) reads everything she should be able to read
# ════════════════════════════════════════════════════════════════════════════
section "Phase 2 — alice runs all GET endpoints (owner ⊇ viewer)"

assert_eq "GET /page (list)"                 "200" "$(NHC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/page")"
assert_eq "GET /count"                        "200" "$(NHC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/count")"
assert_eq "GET /knowledge_bases?kbs_id=<own>" "200" "$(NHC "$T_ALICE" "$GATEWAY/kb/knowledge_bases?kbs_id=$KBID")"
assert_eq "GET /mappings?kbs_id=<own>"        "200" "$(NHC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/mappings?kbs_id=$KBID")"
assert_eq "GET /mappings/count?kbs_id=<own>"  "200" "$(NHC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/mappings/count?kbs_id=$KBID")"
assert_eq "GET /files?kbs_id=<own>"           "200" "$(NHC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/files?kbs_id=$KBID")"
assert_eq "GET /files/count?kbs_id=<own>"     "200" "$(NHC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/files/count?kbs_id=$KBID")"
assert_eq "GET /files/history?kbs_id=<own>"   "200" "$(NHC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/files/history?kbs_id=$KBID")"
assert_eq "GET /files/filesystem"             "200" "$(NHC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/files/filesystem")"

# ════════════════════════════════════════════════════════════════════════════
# PHASE 3 — alice (owner) modifies the KB and its sub-resources
# ════════════════════════════════════════════════════════════════════════════
section "Phase 3 — alice POSTs (modify / mappings+/- / files+/-) on her KB"

assert_eq "POST /modify (owner ⊇ contributor)" "200" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/modify" \
    "{\"kbs_id\":\"$KBID\",\"name\":\"${KB_NAME}-renamed\",\"description\":\"updated\"}")"

# mappings/add → records DM_ID for the matching /mappings/remove later
RESP_M=$(NPOST "$T_ALICE" "$GATEWAY/kb/knowledge_bases/mappings/add" \
  "{\"kbs_id\":\"$KBID\",\"src_dir\":\"/data\",\"fs_id\":\"f1\",\"fs_name\":\"local\",\"channel_name\":\"c1\"}")
assert_in "POST /mappings/add → 201" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/mappings/add" \
    "{\"kbs_id\":\"$KBID\",\"src_dir\":\"/d2\",\"fs_id\":\"f2\",\"fs_name\":\"local\",\"channel_name\":\"c2\"}")" "200" "201"
DM_ID=$(echo "$RESP_M" | json_get data.CHANNELID)
assert_match "response data.CHANNELID is a real id" '^[A-Za-z0-9-]{4,}$' "$DM_ID"

# files/upload via stdin (avoids Windows path issues with @file)
UP_CODE=$(printf 'lifecycle test content\n' | curl -sS --max-time 10 \
  -X POST -H "Authorization: Bearer $T_ALICE" \
  -F "kbs_id=$KBID" -F "files=@-;filename=test.txt;type=text/plain" \
  -o /dev/null -w "%{http_code}" "$GATEWAY/kb/knowledge_bases/files/upload")
assert_in "POST /files/upload → 201" "$UP_CODE" "200" "201"

# mappings/remove and files/remove are contributor-level — must NOT cascade
# the parent KB's owner ACL (regression check on the v1.4.1 ext_proc fix)
assert_eq "POST /mappings/remove → 200" "200" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/mappings/remove" \
    "{\"kbs_id\":\"$KBID\",\"kbs_dm_id\":\"$DM_ID\"}")"
assert_in "POST /files/remove → 200" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/files/remove" \
    "{\"kbs_id\":\"$KBID\",\"file_ids\":[]}")" "200" "201"
sleep 1
assert_match "parent KB ACL intact after child removes (no cascade)" '^[1-9][0-9]*$' \
  "$(acl_count knowledgebase "$KBID")"

# ════════════════════════════════════════════════════════════════════════════
# PHASE 4 — alice creates a jargon library + entries (kb-admins privilege)
# ════════════════════════════════════════════════════════════════════════════
section "Phase 4 — alice creates jargon library (apioption.md sec 4)"

LIB="e2e-lifecycle-lib"
JNAME="freeride"

assert_in "POST /jargon_groups/add → 201" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/jargon_groups/add" \
    "{\"jargon_lib_name\":\"$LIB\",\"description\":\"e2e\"}")" "200" "201"

assert_eq "GET /jargon_groups (list contains it)" "200" \
  "$(NHC "$T_ALICE" "$GATEWAY/kb/jargon_groups")"
LIST_BODY=$(NH "$T_ALICE" "$GATEWAY/kb/jargon_groups")
assert_contains "list response carries '$LIB'" "$LIB" "$LIST_BODY"

assert_eq "GET /jargon_groups/version" "200" \
  "$(NHC "$T_ALICE" "$GATEWAY/kb/jargon_groups/version?jargon_lib_name=$LIB")"

assert_in "POST /jargons/add (batch entries) → 201" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/jargons/add" \
    "{\"jargon_info_list\":[{\"jargon_name\":\"$JNAME\",\"description\":\"d\",\"target_term\":[\"free\"],\"jargon_lib_name\":\"$LIB\",\"creator_name\":\"alice\",\"creator_id\":\"$ALICE_UID\"}]}")" "200" "201"

assert_eq "GET /jargon_groups/jargons" "200" \
  "$(NHC "$T_ALICE" "$GATEWAY/kb/jargon_groups/jargons?jargon_lib_name=$LIB")"

# ════════════════════════════════════════════════════════════════════════════
# PHASE 5 — alice binds the jargon library to her KB (apioption.md sec 7)
# ════════════════════════════════════════════════════════════════════════════
section "Phase 5 — alice binds jargon lib to KB"

# BEFORE binding: GET should return empty
PRE_BIND=$(NH "$T_ALICE" "$GATEWAY/kb/knowledge_bases/jargon_groups?kb_name=${KB_NAME}-renamed")
assert_contains "before bind: jargon_lib_name is empty" '"jargon_lib_name":""' "$PRE_BIND"

# Bind
assert_in "POST /jargon_groups/knowledge_bases/add → 201" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/jargon_groups/knowledge_bases/add" \
    "{\"kb_name\":\"${KB_NAME}-renamed\",\"jargon_lib_name\":\"$LIB\"}")" "200" "201"

# AFTER binding: GET should return the bound lib
POST_BIND=$(NH "$T_ALICE" "$GATEWAY/kb/knowledge_bases/jargon_groups?kb_name=${KB_NAME}-renamed")
assert_contains "after bind: jargon_lib_name = $LIB" "\"jargon_lib_name\":\"$LIB\"" "$POST_BIND"

# ════════════════════════════════════════════════════════════════════════════
# PHASE 6 — bob (rubik-admin, no share) cannot access alice's KB
# ════════════════════════════════════════════════════════════════════════════
section "Phase 6 — bob (no share) is denied on resource-level ops"

assert_eq "bob GET /knowledge_bases?kbs_id=alice's → 403" "403" \
  "$(NHC "$T_BOB" "$GATEWAY/kb/knowledge_bases?kbs_id=$KBID")"
assert_eq "bob GET /mappings?kbs_id=alice's → 403" "403" \
  "$(NHC "$T_BOB" "$GATEWAY/kb/knowledge_bases/mappings?kbs_id=$KBID")"
assert_eq "bob POST /modify on alice's → 403" "403" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/knowledge_bases/modify" \
    "{\"kbs_id\":\"$KBID\",\"name\":\"hack\"}")"
assert_eq "bob POST /mappings/add on alice's → 403" "403" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/knowledge_bases/mappings/add" \
    "{\"kbs_id\":\"$KBID\",\"src_dir\":\"/x\",\"fs_id\":\"f\",\"fs_name\":\"l\",\"channel_name\":\"c\"}")"
assert_eq "bob POST /remove on alice's → 403" "403" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/knowledge_bases/remove" "{\"kbs_id\":\"$KBID\"}")"

# ════════════════════════════════════════════════════════════════════════════
# PHASE 7 — alice shares the KB to bob as VIEWER (POST /acl/v1/...)
# ════════════════════════════════════════════════════════════════════════════
section "Phase 7 — alice shares KB to bob as viewer"

# Non-owner cannot share
assert_eq "bob POST /acl/v1/.../permissions (not owner) → 403" "403" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/acl/v1/resources/$KBID/permissions" \
    "{\"subject_type\":\"user\",\"subject_id\":\"$BOB_UID\",\"permission\":\"viewer\",\"app_name\":\"knowledgebase\",\"resource_type\":\"kb\"}")"

# Owner shares to bob with viewer
SHARE_RESP=$(NPOST "$T_ALICE" "$GATEWAY/acl/v1/resources/$KBID/permissions" \
  "{\"subject_type\":\"user\",\"subject_id\":\"$BOB_UID\",\"permission\":\"viewer\",\"app_name\":\"knowledgebase\",\"resource_type\":\"kb\"}")
SHARE_CODE=$(NPOSTC "$T_ALICE" "$GATEWAY/acl/v1/resources/$KBID/permissions" \
  "{\"subject_type\":\"user\",\"subject_id\":\"$BOB_UID\",\"permission\":\"viewer\",\"app_name\":\"knowledgebase\",\"resource_type\":\"kb\"}" 2>/dev/null)
# Second call may return 409 (UNIQUE on subject) — only assert the first
assert_in "alice shares to bob (viewer) → 201" "$(echo "$SHARE_RESP" | python -c "import sys,json
try: d=json.load(sys.stdin); print('201' if 'id' in d else '500')
except: print('500')")" "200" "201"

ACL_ID=$(echo "$SHARE_RESP" | json_get id)
assert_match "share response carries acl id" '^[0-9]+$' "$ACL_ID"

# Verify GET /acl/v1/.../permissions lists bob
LIST_PERM=$(NH "$T_ALICE" "$GATEWAY/acl/v1/resources/$KBID/permissions?app_name=knowledgebase&resource_type=kb")
assert_contains "GET /acl/v1/.../permissions includes bob" "\"subject_id\":\"$BOB_UID\"" "$LIST_PERM"
assert_contains "GET /acl/v1/.../permissions includes 'viewer'" '"permission":"viewer"' "$LIST_PERM"

# ════════════════════════════════════════════════════════════════════════════
# PHASE 8 — bob with viewer can read but not modify
# ════════════════════════════════════════════════════════════════════════════
section "Phase 8 — bob has viewer: GET ✓, POST modify/remove ✗"

assert_eq "bob GET /knowledge_bases?kbs_id=alice's → 200" "200" \
  "$(NHC "$T_BOB" "$GATEWAY/kb/knowledge_bases?kbs_id=$KBID")"
assert_eq "bob GET /mappings?kbs_id=alice's → 200" "200" \
  "$(NHC "$T_BOB" "$GATEWAY/kb/knowledge_bases/mappings?kbs_id=$KBID")"
assert_eq "bob POST /modify (viewer < contributor) → 403" "403" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/knowledge_bases/modify" \
    "{\"kbs_id\":\"$KBID\",\"name\":\"hack\"}")"
assert_eq "bob POST /remove (viewer < owner) → 403" "403" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/knowledge_bases/remove" "{\"kbs_id\":\"$KBID\"}")"

# ════════════════════════════════════════════════════════════════════════════
# PHASE 9 — alice promotes bob to CONTRIBUTOR (PUT /acl/v1/...)
# ════════════════════════════════════════════════════════════════════════════
section "Phase 9 — alice promotes bob viewer→contributor"

# bob cannot self-promote
assert_eq "bob PUT /acl/v1/.../permissions/{id} (not owner) → 403" "403" \
  "$(curl -sS --max-time 10 -X PUT -H "Authorization: Bearer $T_BOB" \
    -H "Content-Type: application/json" \
    -d '{"permission":"contributor"}' \
    -o /dev/null -w "%{http_code}" \
    "$GATEWAY/acl/v1/resources/$KBID/permissions/$ACL_ID")"

# alice promotes
PUT_CODE=$(curl -sS --max-time 10 -X PUT -H "Authorization: Bearer $T_ALICE" \
  -H "Content-Type: application/json" \
  -d '{"permission":"contributor"}' \
  -o /dev/null -w "%{http_code}" \
  "$GATEWAY/acl/v1/resources/$KBID/permissions/$ACL_ID")
assert_eq "alice PUT /acl/v1/.../{id} {permission:contributor} → 200" "200" "$PUT_CODE"

# ════════════════════════════════════════════════════════════════════════════
# PHASE 10 — bob with contributor can modify; cannot remove
# ════════════════════════════════════════════════════════════════════════════
section "Phase 10 — bob has contributor: modify ✓, remove ✗"

assert_eq "bob POST /modify (contributor) → 200" "200" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/knowledge_bases/modify" \
    "{\"kbs_id\":\"$KBID\",\"name\":\"bob-modified\",\"description\":\"by bob\"}")"

# bob can also add child mapping
RESP_BM=$(NPOST "$T_BOB" "$GATEWAY/kb/knowledge_bases/mappings/add" \
  "{\"kbs_id\":\"$KBID\",\"src_dir\":\"/bob\",\"fs_id\":\"fb\",\"fs_name\":\"local\",\"channel_name\":\"cb\"}")
assert_in "bob POST /mappings/add (contributor) → 201" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/knowledge_bases/mappings/add" \
    "{\"kbs_id\":\"$KBID\",\"src_dir\":\"/bob2\",\"fs_id\":\"fb2\",\"fs_name\":\"local\",\"channel_name\":\"cb2\"}")" "200" "201"
DM_BOB=$(echo "$RESP_BM" | json_get data.CHANNELID)
assert_eq "bob POST /mappings/remove (contributor) → 200" "200" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/knowledge_bases/mappings/remove" \
    "{\"kbs_id\":\"$KBID\",\"kbs_dm_id\":\"$DM_BOB\"}")"

# bob still cannot owner-level delete
assert_eq "bob POST /remove (contributor < owner) → 403" "403" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/knowledge_bases/remove" "{\"kbs_id\":\"$KBID\"}")"

# ════════════════════════════════════════════════════════════════════════════
# PHASE 11 — alice revokes bob's permission (DELETE /acl/v1/...)
# ════════════════════════════════════════════════════════════════════════════
section "Phase 11 — alice revokes bob's share"

# bob cannot self-revoke
assert_eq "bob DELETE /acl/v1/.../permissions/{id} (not owner) → 403" "403" \
  "$(curl -sS --max-time 10 -X DELETE -H "Authorization: Bearer $T_BOB" \
    -o /dev/null -w "%{http_code}" \
    "$GATEWAY/acl/v1/resources/$KBID/permissions/$ACL_ID")"

assert_eq "alice DELETE /acl/v1/.../{id} → 200" "200" \
  "$(curl -sS --max-time 10 -X DELETE -H "Authorization: Bearer $T_ALICE" \
    -o /dev/null -w "%{http_code}" \
    "$GATEWAY/acl/v1/resources/$KBID/permissions/$ACL_ID")"

# Verify bob no longer has access
assert_eq "bob GET /knowledge_bases?kbs_id=alice's (revoked) → 403" "403" \
  "$(NHC "$T_BOB" "$GATEWAY/kb/knowledge_bases?kbs_id=$KBID")"
assert_eq "bob POST /modify (revoked) → 403" "403" \
  "$(NPOSTC "$T_BOB" "$GATEWAY/kb/knowledge_bases/modify" \
    "{\"kbs_id\":\"$KBID\",\"name\":\"x\"}")"

# ════════════════════════════════════════════════════════════════════════════
# PHASE 12 — cleanup: unbind jargon, remove jargon entries, lib, KB
# ════════════════════════════════════════════════════════════════════════════
section "Phase 12 — cleanup"

# Unbind
assert_eq "POST /jargon_groups/knowledge_bases/remove (unbind) → 200" "200" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/jargon_groups/knowledge_bases/remove" \
    "{\"kb_name\":\"${KB_NAME}-renamed\",\"jargon_lib_name\":\"$LIB\"}")"
POST_UNBIND=$(NH "$T_ALICE" "$GATEWAY/kb/knowledge_bases/jargon_groups?kb_name=${KB_NAME}-renamed")
assert_contains "after unbind: jargon_lib_name is empty again" '"jargon_lib_name":""' "$POST_UNBIND"

# Remove jargon entry, then jargon lib
assert_eq "POST /jargons/remove → 200" "200" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/jargons/remove" \
    "{\"jargon_lib_name\":\"$LIB\",\"jargon_name\":\"$JNAME\"}")"
assert_eq "POST /jargon_groups/remove → 200" "200" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/jargon_groups/remove" \
    "{\"jargon_lib_name\":\"$LIB\"}")"
sleep 1
assert_eq "ACL row gone for jargon lib" "0" \
  "$(acl_count knowledgebase "$LIB")"

# Owner-level KB delete cascades all KB ACL rows
assert_eq "alice POST /knowledge_bases/remove (owner cascade) → 200" "200" \
  "$(NPOSTC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/remove" "{\"kbs_id\":\"$KBID\"}")"
sleep 2
assert_eq "ACL rows gone for KB" "0" "$(acl_count knowledgebase "$KBID")"

# Cleanup the secondary KB created in phase 1 ${KB_NAME}-2 (if still around)
# Find it by name (mock returns full list to alice via /page)
SECOND_KBID=$(NH "$T_ALICE" "$GATEWAY/kb/knowledge_bases/page?page_size=200" \
  | python -c "import sys,json
try:
  d=json.load(sys.stdin)
  for k in d.get('data',[]):
    if k.get('KDSNAME') == '${KB_NAME}-2': print(k.get('KDSID')); break
except: pass")
[ -n "$SECOND_KBID" ] && NPOSTC "$T_ALICE" "$GATEWAY/kb/knowledge_bases/remove" \
  "{\"kbs_id\":\"$SECOND_KBID\"}" >/dev/null

# Cleanup secondary jargon lib if any
NPOSTC "$T_ALICE" "$GATEWAY/kb/jargon_groups/remove" \
  "{\"jargon_lib_name\":\"${LIB}-2\"}" >/dev/null 2>&1 || true

print_summary
exit "$FAIL"
