#!/usr/bin/env bash
# ============================================================================
# 05-conversations.sh — apioption.md (5) 问答
#
# Permission model (all-users for the whole section):
#   读 (kb_conv_view, all-users):
#     GET /kb/conversations/list           — list user's conversations
#     GET /kb/conversations?thread_id=…    — single conversation
#     GET /kb/conversations/images/download?token=…
#   编辑 (kb_conv_edit, all-users):
#     POST /kb/conversations/start         — issue Q
#     POST /kb/conversations/stop          — abort
#     POST /kb/conversations/images/generate
#     POST /kb/conversations/remove
#
# Coverage axes:
#   - no-token (401)
#   - normal-user happy path (full lifecycle)
#   - kb-admin (also allowed via kb_admin_full)
#   - response shape: start returns {event, data.thread_id}
# ============================================================================
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/../lib/common.sh"

banner "[05] CONVERSATIONS — apioption.md (5)"
init_tokens

# ── (A) POST /start — issue a question ─────────────────────────────────────
section "POST /kb/conversations/start"
assert_eq "no-token → 401" "401" "$(http_code -X POST "$GATEWAY/kb/conversations/start")"
RESP=$(NPOST "$T_USER" "$GATEWAY/kb/conversations/start" \
  '{"query":"hello","resources":[{"collection_name":"kb1"}],"thread_id":"","user_id":"u1","output_format":"qa","rag_mode":"naive_rag","enable_proxy":false}')
CODE=$(NPOSTC "$T_USER" "$GATEWAY/kb/conversations/start" \
  '{"query":"hi","resources":[],"thread_id":"","user_id":"u2","output_format":"qa","rag_mode":"naive_rag"}')
assert_in "normal-user → 200/201" "$CODE" "200" "201"
TID=$(echo "$RESP" | json_get data.thread_id)
assert_match "response has data.thread_id" '^[A-Za-z0-9-]+$' "$TID"
assert_contains "response shape: {event, data}" '"event"' "$RESP"

RESP_A=$(NPOST "$T_KBADMIN" "$GATEWAY/kb/conversations/start" \
  '{"query":"q","resources":[],"thread_id":"","user_id":"a","output_format":"qa","rag_mode":"naive_rag"}')
CODE_A=$(NPOSTC "$T_KBADMIN" "$GATEWAY/kb/conversations/start" \
  '{"query":"q2","resources":[],"thread_id":"","user_id":"a","output_format":"qa","rag_mode":"naive_rag"}')
assert_in "kb-admin → 201" "$CODE_A" "200" "201"
TID_ADMIN=$(echo "$RESP_A" | json_get data.thread_id)
assert_match "kb-admin's thread_id" '^[A-Za-z0-9-]+$' "$TID_ADMIN"

# ── (B) GET /list ──────────────────────────────────────────────────────────
section "GET /kb/conversations/list"
assert_eq "no-token → 401"    "401" "$(http_code "$GATEWAY/kb/conversations/list")"
assert_eq "normal-user → 200" "200" "$(NHC "$T_USER"    "$GATEWAY/kb/conversations/list")"
assert_eq "kb-admin → 200"    "200" "$(NHC "$T_KBADMIN" "$GATEWAY/kb/conversations/list")"

# ── (C) GET /  (single by thread_id) ───────────────────────────────────────
section "GET /kb/conversations?thread_id=…"
assert_eq "no-token → 401"  "401" "$(http_code "$GATEWAY/kb/conversations")"
assert_eq "normal-user → 200" "200" \
  "$(NHC "$T_USER" "$GATEWAY/kb/conversations?thread_id=$TID")"
SBODY=$(NH "$T_USER" "$GATEWAY/kb/conversations?thread_id=$TID")
assert_contains "single response has thread_id" "\"$TID\"" "$SBODY"

# ── (D) POST /stop ─────────────────────────────────────────────────────────
section "POST /kb/conversations/stop"
assert_eq "no-token → 401" "401" "$(http_code -X POST "$GATEWAY/kb/conversations/stop")"
assert_eq "normal-user stops own thread → 200" "200" \
  "$(NPOSTC "$T_USER" "$GATEWAY/kb/conversations/stop" "{\"thread_id\":\"$TID\"}")"
# kb-admin uses ITS OWN thread_id (resource ACL is per-thread; cross-user stop is denied)
assert_eq "kb-admin stops own thread → 200" "200" \
  "$(NPOSTC "$T_KBADMIN" "$GATEWAY/kb/conversations/stop" "{\"thread_id\":\"$TID_ADMIN\"}")"

# ── (E) POST /images/generate ──────────────────────────────────────────────
section "POST /kb/conversations/images/generate"
assert_eq "no-token → 401" "401" "$(http_code -X POST "$GATEWAY/kb/conversations/images/generate")"
assert_eq "normal-user → 200" "200" \
  "$(NPOSTC "$T_USER" "$GATEWAY/kb/conversations/images/generate" '{"image_url":"/x.png"}')"
GBODY=$(NPOST "$T_USER" "$GATEWAY/kb/conversations/images/generate" '{"image_url":"/y.png"}')
assert_contains "response has data.token" '"token"' "$GBODY"

# ── (F) GET /images/download ───────────────────────────────────────────────
section "GET /kb/conversations/images/download"
assert_eq "no-token → 401"   "401" "$(http_code "$GATEWAY/kb/conversations/images/download")"
assert_eq "normal-user → 200" "200" \
  "$(NHC "$T_USER" "$GATEWAY/kb/conversations/images/download?token=mock-tok")"

# ── (G) POST /remove ───────────────────────────────────────────────────────
section "POST /kb/conversations/remove"
assert_eq "no-token → 401" "401" "$(http_code -X POST "$GATEWAY/kb/conversations/remove")"
assert_eq "normal-user → 200" "200" \
  "$(NPOSTC "$T_USER" "$GATEWAY/kb/conversations/remove" "{\"thread_id\":\"$TID\"}")"

print_summary
exit "$FAIL"
