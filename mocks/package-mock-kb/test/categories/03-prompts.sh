#!/usr/bin/env bash
# ============================================================================
# 03-prompts.sh — apioption.md (3) 提示词管理
#
# Permission model:
#   读 (kb_prompt_view, all-users):
#     GET /kb/prompts/detail?id=…
#     GET /kb/prompts/options?mode=…
#     GET /kb/prompts/page
#   编辑 (kb_prompt_edit, kb-admins):
#     POST /kb/prompts/add
#     POST /kb/prompts/modify
#     POST /kb/prompts/remove
#
# Coverage axes:
#   - no-token (401)
#   - normal-user can READ but cannot EDIT
#   - kb-admin can READ + EDIT
#   - response shape: page returns {total, pages, items}; add returns data.id
#   - ACL auto-write on POST /add via response_id_field=data.id
# ============================================================================
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/../lib/common.sh"

banner "[03] PROMPTS — apioption.md (3)"
init_tokens

# ── (A) GET /page — list ──────────────────────────────────────────────────
section "GET /kb/prompts/page"
assert_eq "no-token → 401"    "401" "$(http_code "$GATEWAY/kb/prompts/page")"
assert_eq "normal-user → 200" "200" "$(NHC "$T_USER"    "$GATEWAY/kb/prompts/page")"
assert_eq "kb-admin → 200"    "200" "$(NHC "$T_KBADMIN" "$GATEWAY/kb/prompts/page")"

PAGE_BODY=$(NH "$T_KBADMIN" "$GATEWAY/kb/prompts/page")
assert_contains "page response has 'items' field" '"items"' "$PAGE_BODY"
assert_contains "page response has 'total'"        '"total"' "$PAGE_BODY"

# ── (B) POST /add — kb-admin only ───────────────────────────────────────────
section "POST /kb/prompts/add"
assert_eq "no-token → 401"    "401" \
  "$(http_code -X POST "$GATEWAY/kb/prompts/add")"
assert_eq "normal-user → 403" "403" \
  "$(NPOSTC "$T_USER" "$GATEWAY/kb/prompts/add" '{"title":"hack","description":"d","mode":"NAIVE","prompts":{}}')"

RESP=$(NPOST "$T_KBADMIN" "$GATEWAY/kb/prompts/add" \
  '{"title":"e2e-prompt","description":"by kb-admin","mode":"NAIVE","prompts":{"system":"you are helpful"}}')
CODE=$(NPOSTC "$T_KBADMIN" "$GATEWAY/kb/prompts/add" \
  '{"title":"e2e-prompt-2","description":"d2","mode":"DEEP_QA","prompts":{}}')
assert_in "kb-admin → 200/201" "$CODE" "200" "201"
PID=$(echo "$RESP" | json_get data.id)
assert_match "response.data.id is new prompt id" '^[A-Za-z0-9-]+$' "$PID"
sleep 2
assert_match "ACL rows written for new prompt" '^[12]$' "$(acl_count knowledgebase "$PID")"

# ── (C) GET /detail?id=… ────────────────────────────────────────────────────
section "GET /kb/prompts/detail?id=…"
assert_eq "no-token → 401"  "401" "$(http_code "$GATEWAY/kb/prompts/detail?id=$PID")"
assert_eq "normal-user → 200" "200" "$(NHC "$T_USER"    "$GATEWAY/kb/prompts/detail?id=$PID")"
assert_eq "kb-admin → 200"    "200" "$(NHC "$T_KBADMIN" "$GATEWAY/kb/prompts/detail?id=$PID")"
DET=$(NH "$T_KBADMIN" "$GATEWAY/kb/prompts/detail?id=$PID")
assert_contains "detail has 'kb_ids' field"     '"kb_ids"' "$DET"
assert_contains "detail has 'prompts' field"    '"prompts"' "$DET"

# ── (D) GET /options?mode=… ────────────────────────────────────────────────
section "GET /kb/prompts/options"
assert_eq "no-token → 401"    "401" "$(http_code "$GATEWAY/kb/prompts/options")"
assert_eq "normal-user → 200" "200" "$(NHC "$T_USER"    "$GATEWAY/kb/prompts/options")"
assert_eq "kb-admin (NAIVE filter) → 200" "200" \
  "$(NHC "$T_KBADMIN" "$GATEWAY/kb/prompts/options?mode=NAIVE")"

# ── (E) POST /modify — kb-admin only ────────────────────────────────────────
section "POST /kb/prompts/modify"
assert_eq "no-token → 401"    "401" "$(http_code -X POST "$GATEWAY/kb/prompts/modify")"
assert_eq "normal-user → 403" "403" \
  "$(NPOSTC "$T_USER" "$GATEWAY/kb/prompts/modify" "{\"id\":\"$PID\",\"title\":\"hack\"}")"
assert_eq "kb-admin → 200"    "200" \
  "$(NPOSTC "$T_KBADMIN" "$GATEWAY/kb/prompts/modify" "{\"id\":\"$PID\",\"title\":\"renamed\"}")"

# ── (F) POST /remove — kb-admin only ────────────────────────────────────────
section "POST /kb/prompts/remove"
assert_eq "no-token → 401"    "401" "$(http_code -X POST "$GATEWAY/kb/prompts/remove")"
assert_eq "normal-user → 403" "403" \
  "$(NPOSTC "$T_USER" "$GATEWAY/kb/prompts/remove" "{\"id\":\"$PID\"}")"
assert_eq "kb-admin → 200"    "200" \
  "$(NPOSTC "$T_KBADMIN" "$GATEWAY/kb/prompts/remove" "{\"id\":\"$PID\"}")"
sleep 2
assert_eq "ACL row gone after delete" "0" "$(acl_count knowledgebase "$PID")"

print_summary
exit "$FAIL"
