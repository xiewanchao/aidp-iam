#!/usr/bin/env bash
# ============================================================================
# walkthrough.sh — MemoryStore API interactive narration (no assertions)
#
# Walks through the same lifecycle as test.sh with human-readable output.
# Useful for manual verification and demos.
#
# Usage:
#   GATEWAY=http://localhost:30080 ./walkthrough.sh
# ============================================================================
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/lib/common.sh"

TID="aidp"
BASE="$GATEWAY/MemoryStore/Tenants/$TID"
INST="walkthrough-inst"

banner "MemoryStore Walkthrough"
init_tokens
T_ALICE="$T_ADMIN"   # admin: master-admins + all-users, acts as resource owner
T_BOB="$T_USER"      # normal-user: all-users only

echo ""
section "0. Health check"
echo "GET /MemoryStore/health"
http_body "$GATEWAY/MemoryStore/health" | python -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))" 2>/dev/null || true

echo ""
section "1. alice creates an Instance"
echo "PUT /MemoryStore/Tenants/$TID/Instances/$INST"
NPUT "$T_ALICE" "$BASE/Instances/$INST" '{"description":"walkthrough instance"}' \
  | python -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))" 2>/dev/null || true
sleep 2
echo "  → ACL owner: $(acl_owner MemoryStore "$INST")"

echo ""
section "2. alice creates a Memory"
echo "PUT /MemoryStore/Tenants/$TID/Instances/$INST/Memories/mem-wt"
NPUT "$T_ALICE" "$BASE/Instances/$INST/Memories/mem-wt" \
  '{"content":"Roses are red","tags":["poetry"]}' \
  | python -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))" 2>/dev/null || true

echo ""
section "3. alice queries Memories"
echo "POST /MemoryStore/Tenants/$TID/Instances/$INST/Memories/Query"
NPOST "$T_ALICE" "$BASE/Instances/$INST/Memories/Query" '{"query":"red"}' \
  | python -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))" 2>/dev/null || true

echo ""
section "4. alice creates a Template"
echo "PUT /MemoryStore/Tenants/$TID/Instances/$INST/Templates/tpl-wt"
NPUT "$T_ALICE" "$BASE/Instances/$INST/Templates/tpl-wt" \
  '{"description":"walkthrough template","enabled":true}' \
  | python -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))" 2>/dev/null || true

echo ""
section "5. alice sets Template Filters"
echo "POST /MemoryStore/Tenants/$TID/Instances/$INST/Templates/tpl-wt/Filters"
NPOST "$T_ALICE" "$BASE/Instances/$INST/Templates/tpl-wt/Filters" \
  '{"allow_agents":["agent-1"],"deny_agents":[]}' \
  | python -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))" 2>/dev/null || true

echo ""
section "6. alice enables LLM Extraction"
echo "POST /MemoryStore/Tenants/$TID/Instances/$INST/Templates/tpl-wt/LLMExtraction"
NPOST "$T_ALICE" "$BASE/Instances/$INST/Templates/tpl-wt/LLMExtraction" \
  '{"enabled":true,"model":"qwen-turbo"}' \
  | python -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))" 2>/dev/null || true

echo ""
section "7. bob tries to access alice's Instance (no share)"
echo "GET /MemoryStore/Tenants/$TID/Instances/$INST  (as bob)"
echo "  → HTTP $(NHC "$T_BOB" "$BASE/Instances/$INST") (expect 403)"

echo ""
section "8. alice shares Instance to bob (viewer)"
echo "POST /acl/v1/resources/$INST/permissions"
BOB_UID_WT=$(kubectl -n "$KEYCLOAK_NS" exec iam-store-0 -c postgres -- \
  psql -U keycloak -d keycloak -tA -c \
  "SELECT id FROM user_entity WHERE username='normal-user' AND realm_id=(SELECT id FROM realm WHERE name='aidp')" 2>/dev/null | tr -d ' \r\n')
SHARE=$(NPOST "$T_ALICE" "$GATEWAY/acl/v1/resources/$INST/permissions" \
  "{\"subject_type\":\"user\",\"subject_id\":\"$BOB_UID_WT\",\"permission\":\"viewer\",\"app_name\":\"MemoryStore\",\"resource_type\":\"Instances\"}")
echo "$SHARE" | python -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))" 2>/dev/null || true
echo "  → bob GET now: HTTP $(NHC "$T_BOB" "$BASE/Instances/$INST") (expect 200)"

echo ""
section "9. Cleanup — alice deletes Instance (cascades children)"
echo "DELETE /MemoryStore/Tenants/$TID/Instances/$INST"
echo "  → HTTP $(NDEL "$T_ALICE" "$BASE/Instances/$INST") (expect 204)"
sleep 2
echo "  → Instance ACL count: $(acl_count MemoryStore "$INST") (expect 0)"

echo ""
banner "Walkthrough complete"
