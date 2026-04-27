#!/usr/bin/env bash
# ============================================================================
# 00-prereq.sh — preconditions before any per-category test
#
# Asserts:
#   1. mock-kb pod is Ready
#   2. public OIDC route works (200)
#   3. IAM has knowledgebase app registered
#   4. /kb/* without token rejects 401 (path-level auth in place)
#   5. all 5 role tokens (admin / kb-admin / rubik-admin / memory-admin /
#      normal-user) can be acquired via password grant
# ============================================================================
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/../lib/common.sh"

banner "[00] PREREQ"
init_tokens

section "Pod health"
ready=$(kubectl -n "$NAMESPACE" get pod -l app=mock-kb \
  -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
assert_eq "mock-kb Pod Ready=True" "True" "$ready"

section "Public OIDC route"
assert_eq "GET /realms/aidp/.well-known/openid-configuration → 200" "200" \
  "$(http_code "$GATEWAY/realms/aidp/.well-known/openid-configuration")"

section "IAM app registry has 'knowledgebase'"
APPS=$(NH "$T_ADMIN" "$GATEWAY/api/v1/apps")
assert_contains "GET /api/v1/apps contains 'knowledgebase'" '"knowledgebase"' "$APPS"

section "Protected /kb/* rejects no-token"
assert_eq "GET /kb/health (no token) → 401" "401" \
  "$(http_code "$GATEWAY/kb/health")"
assert_eq "GET /kb/knowledge_bases/page (no token) → 401" "401" \
  "$(http_code "$GATEWAY/kb/knowledge_bases/page")"
assert_eq "POST /kb/knowledge_bases/add (no token) → 401" "401" \
  "$(http_code -X POST "$GATEWAY/kb/knowledge_bases/add")"

section "Token acquisition (5 roles)"
assert_match "admin token (jwt-shape)"        '^[A-Za-z0-9_.-]{800,}$' "$T_ADMIN"
assert_match "kb-admin token"                 '^[A-Za-z0-9_.-]{800,}$' "$T_KBADMIN"
assert_match "rubik-admin token"              '^[A-Za-z0-9_.-]{800,}$' "$T_RUBIKADMIN"
assert_match "memory-admin token"             '^[A-Za-z0-9_.-]{800,}$' "$T_MEMADMIN"
assert_match "normal-user token"              '^[A-Za-z0-9_.-]{800,}$' "$T_USER"

print_summary
exit "$FAIL"
