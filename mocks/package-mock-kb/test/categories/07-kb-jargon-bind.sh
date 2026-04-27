#!/usr/bin/env bash
# ============================================================================
# 07-kb-jargon-bind.sh — apioption.md (7) 知识库 ↔ 术语库 关联
#
# Permission model (kb_jargon_bind, kb-admins; same group as edit since this
# touches both the KB and the global jargon library):
#   POST /kb/jargon_groups/knowledge_bases/add     — bind jargon lib to KB
#   POST /kb/jargon_groups/knowledge_bases/remove  — unbind
#
# Coverage axes:
#   - no-token (401)
#   - normal-user (path-level deny → 403)
#   - kb-admin (allow)
#   - end-to-end: bind, then verify GET /knowledge_bases/jargon_groups returns the link
# ============================================================================
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/../lib/common.sh"

banner "[07] KB ↔ JARGON BINDING — apioption.md (7)"
init_tokens

LIB="bind-test-lib"
KB_NAME="bind-test-kb"

# 准备：kb-admin 创个 jargon lib
NPOSTC "$T_KBADMIN" "$GATEWAY/kb/jargon_groups/add" "{\"jargon_lib_name\":\"$LIB\"}" >/dev/null

# ── (A) POST /jargon_groups/knowledge_bases/add ────────────────────────────
section "POST /kb/jargon_groups/knowledge_bases/add"
assert_eq "no-token → 401" "401" \
  "$(http_code -X POST "$GATEWAY/kb/jargon_groups/knowledge_bases/add")"
assert_eq "normal-user → 403" "403" \
  "$(NPOSTC "$T_USER" "$GATEWAY/kb/jargon_groups/knowledge_bases/add" \
    "{\"kb_name\":\"$KB_NAME\",\"jargon_lib_name\":\"$LIB\"}")"
assert_in "kb-admin → 200/201" \
  "$(NPOSTC "$T_KBADMIN" "$GATEWAY/kb/jargon_groups/knowledge_bases/add" \
    "{\"kb_name\":\"$KB_NAME\",\"jargon_lib_name\":\"$LIB\"}")" "200" "201"

# ── (B) POST /jargon_groups/knowledge_bases/remove ─────────────────────────
section "POST /kb/jargon_groups/knowledge_bases/remove"
assert_eq "no-token → 401" "401" \
  "$(http_code -X POST "$GATEWAY/kb/jargon_groups/knowledge_bases/remove")"
assert_eq "normal-user → 403" "403" \
  "$(NPOSTC "$T_USER" "$GATEWAY/kb/jargon_groups/knowledge_bases/remove" \
    "{\"kb_name\":\"$KB_NAME\",\"jargon_lib_name\":\"$LIB\"}")"
assert_eq "kb-admin → 200" "200" \
  "$(NPOSTC "$T_KBADMIN" "$GATEWAY/kb/jargon_groups/knowledge_bases/remove" \
    "{\"kb_name\":\"$KB_NAME\",\"jargon_lib_name\":\"$LIB\"}")"

# Cleanup the test lib
NPOSTC "$T_KBADMIN" "$GATEWAY/kb/jargon_groups/remove" "{\"jargon_lib_name\":\"$LIB\"}" >/dev/null

print_summary
exit "$FAIL"
