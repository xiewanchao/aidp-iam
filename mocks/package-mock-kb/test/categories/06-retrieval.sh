#!/usr/bin/env bash
# ============================================================================
# 06-retrieval.sh — apioption.md (6) 检索
#
# Permission model (kb_retrieval, all-users):
#   POST /kb/retrieval/fusion_search  — multi-KB fusion search
#
# Coverage axes:
#   - no-token (401)
#   - normal-user (allow, full body schema)
#   - kb-admin (allow)
#   - response shape: {data: {data:[…], total_return_count}}
# ============================================================================
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/../lib/common.sh"

banner "[06] RETRIEVAL — apioption.md (6)"
init_tokens

section "POST /kb/retrieval/fusion_search"
assert_eq "no-token → 401" "401" "$(http_code -X POST "$GATEWAY/kb/retrieval/fusion_search")"

# normal-user 用最小 body
assert_eq "normal-user (minimal body) → 200" "200" \
  "$(NPOSTC "$T_USER" "$GATEWAY/kb/retrieval/fusion_search" \
    '{"query":"hello","kds_list":["kb-1"],"search_method":"hybrid_search"}')"

# 全字段 body
RESP=$(NPOST "$T_USER" "$GATEWAY/kb/retrieval/fusion_search" \
  '{"query":"deep test","kds_list":["kb-1","kb-2"],"search_method":"hybrid_search","reranking_enable":true,"reranking_mode":"high_accuracy","top_k":5,"multi_model":false}')
assert_contains "response has data.data array" '"data"'              "$RESP"
assert_contains "response has total_return_count" '"total_return_count"' "$RESP"

assert_eq "kb-admin → 200" "200" \
  "$(NPOSTC "$T_KBADMIN" "$GATEWAY/kb/retrieval/fusion_search" \
    '{"query":"q","kds_list":[],"search_method":"vector_search"}')"

print_summary
exit "$FAIL"
