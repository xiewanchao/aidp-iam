#!/usr/bin/env bash
# ============================================================================
# 02-models-config.sh — apioption.md (2) 模型配置
#
# Permission model:
#   读 (kb_model_view, kb-admins):
#     GET /kb/models/config
#   编辑 (kb_model_edit, kb-admins):
#     POST /kb/models/config/add
#     POST /kb/models/config/modify
#     POST /kb/models/config/remove
#
# Coverage axes:
#   - no-token (401)
#   - normal-user (path-level deny → 403; not in kb-admins)
#   - kb-admin (allow + creates ACL via response_id_field=data)
#   - response shape: GET → MODELAPI as JSON-encoded string per api.md
#   - ACL auto-write on POST /add (kb-admins group share)
# ============================================================================
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/../lib/common.sh"

banner "[02] MODELS CONFIG — apioption.md (2)"
init_tokens

# ── (A) GET /kb/models/config ──────────────────────────────────────────────
section "GET /kb/models/config (kb_model_view, kb-admins only)"
assert_eq "no-token → 401"        "401" "$(http_code "$GATEWAY/kb/models/config")"
assert_eq "normal-user → 403"     "403" "$(NHC "$T_USER"     "$GATEWAY/kb/models/config")"
assert_eq "kb-admin (list) → 200" "200" "$(NHC "$T_KBADMIN" "$GATEWAY/kb/models/config")"

# ── (B) POST /add ───────────────────────────────────────────────────────────
section "POST /kb/models/config/add"
assert_eq "no-token → 401"    "401" \
  "$(http_code -X POST "$GATEWAY/kb/models/config/add")"
assert_eq "normal-user → 403" "403" \
  "$(NPOSTC "$T_USER" "$GATEWAY/kb/models/config/add" '{"model_type":"LLM","model_name":"q","model_id":"q1","api_url":"http://x","api_key":"k"}')"

PREV=$(acl_max_id knowledgebase)
RESP=$(NPOST "$T_KBADMIN" "$GATEWAY/kb/models/config/add" \
  '{"model_type":"LLM","model_name":"qwen-test","model_id":"qt-1","api_url":"http://q","api_key":"k","provider":"prov"}')
CODE=$(NPOSTC "$T_KBADMIN" "$GATEWAY/kb/models/config/add" \
  '{"model_type":"LLM","model_name":"qwen-test-2","model_id":"qt-2","api_url":"http://q","api_key":"k"}')
assert_in "kb-admin → 200/201" "$CODE" "200" "201"
MID=$(echo "$RESP" | json_get data)
assert_match "response.data is the new model id" '^[A-Za-z0-9-]+$' "$MID"
sleep 2
# share_to_admin_group_on_create=true → 2 rows (creator + kb-admins group share)
assert_match "ACL rows written for new model_config" '^[12]$' "$(acl_count knowledgebase "$MID")"

# ── (C) GET shape — MODELAPI is JSON-encoded string ─────────────────────────
section "GET response shape"
LIST=$(NH "$T_KBADMIN" "$GATEWAY/kb/models/config")
assert_contains "list response has MODELAPI key" '"MODELAPI"' "$LIST"
# MODELAPI 必须是字符串（值以 \" 开头），不是裸 JSON 对象
assert_contains "MODELAPI is JSON-encoded string"  '"MODELAPI":"{\"' "$LIST"

# ── (D) POST /modify — needs contributor ───────────────────────────────────
section "POST /kb/models/config/modify"
assert_eq "no-token → 401"    "401" \
  "$(http_code -X POST "$GATEWAY/kb/models/config/modify")"
assert_eq "normal-user → 403" "403" \
  "$(NPOSTC "$T_USER" "$GATEWAY/kb/models/config/modify" "{\"id\":\"$MID\",\"model_api\":{}}")"
assert_eq "kb-admin → 200"    "200" \
  "$(NPOSTC "$T_KBADMIN" "$GATEWAY/kb/models/config/modify" "{\"id\":\"$MID\",\"model_api\":{\"model_name\":\"renamed\"}}")"

# ── (E) POST /remove — needs owner ─────────────────────────────────────────
section "POST /kb/models/config/remove"
assert_eq "no-token → 401"    "401" \
  "$(http_code -X POST "$GATEWAY/kb/models/config/remove")"
assert_eq "normal-user → 403" "403" \
  "$(NPOSTC "$T_USER" "$GATEWAY/kb/models/config/remove" "{\"id\":\"$MID\"}")"
assert_eq "kb-admin → 200"    "200" \
  "$(NPOSTC "$T_KBADMIN" "$GATEWAY/kb/models/config/remove" "{\"id\":\"$MID\"}")"
sleep 2
assert_eq "ACL row gone after delete" "0" "$(acl_count knowledgebase "$MID")"

print_summary
exit "$FAIL"
