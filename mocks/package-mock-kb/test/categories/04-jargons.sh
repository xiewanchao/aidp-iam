#!/usr/bin/env bash
# ============================================================================
# 04-jargons.sh — apioption.md (4) 术语管理
#
# Permission model (kb-admins only for the whole section):
#   读 (kb_jargon_view, kb-admins):
#     GET /kb/jargon_groups
#     GET /kb/jargon_groups/jargons?jargon_lib_name=…
#     GET /kb/jargons_groups/jargon  (note spelling — apioption.md keeps it)
#     GET /kb/jargon_groups/version?jargon_lib_name=…
#   编辑 (kb_jargon_edit, kb-admins):
#     POST /kb/jargon_groups/add        — creates lib, ACL written
#     POST /kb/jargon_groups/remove
#     POST /kb/jargons/add              — batch jargon entries
#     POST /kb/jargons/modify
#     POST /kb/jargons/remove
#
# Coverage axes:
#   - no-token (401)
#   - normal-user (path-level deny → 403; not in kb-admins)
#   - kb-admin (allow + ACL writes via response_id_field=data.jargon_lib_name)
# ============================================================================
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/../lib/common.sh"

banner "[04] JARGONS — apioption.md (4)"
init_tokens

LIB="e2e-jargon-lib"

# ── (A) POST /jargon_groups/add ─────────────────────────────────────────────
section "POST /kb/jargon_groups/add (kb-admins only)"
assert_eq "no-token → 401"    "401" "$(http_code -X POST "$GATEWAY/kb/jargon_groups/add")"
assert_eq "normal-user → 403" "403" \
  "$(NPOSTC "$T_USER" "$GATEWAY/kb/jargon_groups/add" "{\"jargon_lib_name\":\"hack\"}")"
RESP=$(NPOST "$T_KBADMIN" "$GATEWAY/kb/jargon_groups/add" \
  "{\"jargon_lib_name\":\"$LIB\",\"description\":\"e2e\"}")
CODE=$(NPOSTC "$T_KBADMIN" "$GATEWAY/kb/jargon_groups/add" \
  "{\"jargon_lib_name\":\"${LIB}-2\",\"description\":\"d2\"}")
assert_in "kb-admin → 200/201" "$CODE" "200" "201"
assert_contains "response has data.jargon_lib_id" '"jargon_lib_id"' "$RESP"
sleep 2
assert_match "ACL rows written for jargon lib" '^[12]$' "$(acl_count knowledgebase "$LIB")"

# ── (B) GET /jargon_groups (list) ──────────────────────────────────────────
section "GET /kb/jargon_groups"
assert_eq "no-token → 401"    "401" "$(http_code "$GATEWAY/kb/jargon_groups")"
assert_eq "normal-user → 403" "403" "$(NHC "$T_USER"    "$GATEWAY/kb/jargon_groups")"
assert_eq "kb-admin → 200"    "200" "$(NHC "$T_KBADMIN" "$GATEWAY/kb/jargon_groups")"
LIST=$(NH "$T_KBADMIN" "$GATEWAY/kb/jargon_groups")
assert_contains "list has 'list' field"        '"list"' "$LIST"
assert_contains "list has 'total_pages'"       '"total_pages"' "$LIST"

# ── (C) GET /jargon_groups/jargons ─────────────────────────────────────────
section "GET /kb/jargon_groups/jargons"
assert_eq "no-token → 401"    "401" "$(http_code "$GATEWAY/kb/jargon_groups/jargons")"
assert_eq "normal-user → 403" "403" "$(NHC "$T_USER"    "$GATEWAY/kb/jargon_groups/jargons?jargon_lib_name=$LIB")"
assert_eq "kb-admin → 200"    "200" "$(NHC "$T_KBADMIN" "$GATEWAY/kb/jargon_groups/jargons?jargon_lib_name=$LIB")"

# ── (D) GET /jargon_groups/version ─────────────────────────────────────────
section "GET /kb/jargon_groups/version"
assert_eq "kb-admin → 200" "200" \
  "$(NHC "$T_KBADMIN" "$GATEWAY/kb/jargon_groups/version?jargon_lib_name=$LIB")"
VBODY=$(NH "$T_KBADMIN" "$GATEWAY/kb/jargon_groups/version?jargon_lib_name=$LIB")
assert_contains "version response has 'sequence_id'" '"sequence_id"' "$VBODY"

# ── (E) POST /jargons/add — batch jargon entries ───────────────────────────
section "POST /kb/jargons/add (batch)"
assert_eq "no-token → 401"    "401" "$(http_code -X POST "$GATEWAY/kb/jargons/add")"
assert_eq "normal-user → 403" "403" \
  "$(NPOSTC "$T_USER" "$GATEWAY/kb/jargons/add" '{"jargon_info_list":[]}')"
# Use ASCII jargon name (avoid bash/curl unicode encoding ambiguity in tests)
JNAME="freeride"
ADDB="{\"jargon_info_list\":[{\"jargon_name\":\"$JNAME\",\"description\":\"get-for-free\",\"target_term\":[\"free\"],\"jargon_lib_name\":\"$LIB\",\"creator_name\":\"kb-admin\",\"creator_id\":\"u1\"}]}"
assert_in "kb-admin batch add → 200/201" \
  "$(NPOSTC "$T_KBADMIN" "$GATEWAY/kb/jargons/add" "$ADDB")" "200" "201"

# ── (F) GET /jargons_groups/jargon — query specific entries ────────────────
section "GET /kb/jargons_groups/jargon  (note spelling: 'jargons_groups')"
assert_eq "no-token → 401"    "401" "$(http_code "$GATEWAY/kb/jargons_groups/jargon")"
assert_eq "normal-user → 403" "403" "$(NHC "$T_USER"    "$GATEWAY/kb/jargons_groups/jargon")"
# kb-admin 查
RESP=$(NH "$T_KBADMIN" "$GATEWAY/kb/jargons_groups/jargon?jargon_lib_name=$LIB")
assert_contains "response has 'data'" '"data"' "$RESP"

# ── (G) POST /jargons/modify ───────────────────────────────────────────────
section "POST /kb/jargons/modify"
assert_eq "no-token → 401"    "401" "$(http_code -X POST "$GATEWAY/kb/jargons/modify")"
assert_eq "normal-user → 403" "403" \
  "$(NPOSTC "$T_USER" "$GATEWAY/kb/jargons/modify" "{\"jargon_lib_name\":\"$LIB\",\"jargon_name\":\"$JNAME\",\"description\":\"hack\"}")"
assert_eq "kb-admin → 200"    "200" \
  "$(NPOSTC "$T_KBADMIN" "$GATEWAY/kb/jargons/modify" "{\"jargon_lib_name\":\"$LIB\",\"jargon_name\":\"$JNAME\",\"description\":\"updated\"}")"

# ── (H) POST /jargons/remove ───────────────────────────────────────────────
section "POST /kb/jargons/remove"
assert_eq "no-token → 401"    "401" "$(http_code -X POST "$GATEWAY/kb/jargons/remove")"
assert_eq "normal-user → 403" "403" \
  "$(NPOSTC "$T_USER" "$GATEWAY/kb/jargons/remove" "{\"jargon_lib_name\":\"$LIB\",\"jargon_name\":\"$JNAME\"}")"
assert_eq "kb-admin → 200"    "200" \
  "$(NPOSTC "$T_KBADMIN" "$GATEWAY/kb/jargons/remove" "{\"jargon_lib_name\":\"$LIB\",\"jargon_name\":\"$JNAME\"}")"

# ── (I) POST /jargon_groups/remove ─────────────────────────────────────────
section "POST /kb/jargon_groups/remove"
assert_eq "no-token → 401"    "401" "$(http_code -X POST "$GATEWAY/kb/jargon_groups/remove")"
assert_eq "normal-user → 403" "403" \
  "$(NPOSTC "$T_USER" "$GATEWAY/kb/jargon_groups/remove" "{\"jargon_lib_name\":\"$LIB\"}")"
assert_eq "kb-admin → 200"    "200" \
  "$(NPOSTC "$T_KBADMIN" "$GATEWAY/kb/jargon_groups/remove" "{\"jargon_lib_name\":\"$LIB\"}")"
sleep 2
assert_eq "ACL row gone after lib delete" "0" "$(acl_count knowledgebase "$LIB")"

# Cleanup leftover lib
NPOSTC "$T_KBADMIN" "$GATEWAY/kb/jargon_groups/remove" "{\"jargon_lib_name\":\"${LIB}-2\"}" >/dev/null

print_summary
exit "$FAIL"
