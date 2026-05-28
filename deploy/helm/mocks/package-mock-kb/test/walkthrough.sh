#!/usr/bin/env bash
# ============================================================================
# walkthrough.sh — curl-by-curl narration of the KB lifecycle, no assertions.
#
# Mirrors the flow of test.sh (apioption.md sections 1-7 + /acl/v1) but
# instead of strict pass/fail assertions, it just executes each curl,
# echoes the command and the (truncated) response so a human can read
# along. Useful for:
#   * demoing the IAM permission story
#   * debugging a single step interactively (copy/paste any line)
#   * onboarding — seeing real wire data instead of "PASS/FAIL"
#
# Run:
#   bash mocks/package-mock-kb/test/walkthrough.sh
#
# Optional env:
#   GATEWAY     (default http://localhost:30080)
#   IAM_NS      (default aidp-iam)
#   KEYCLOAK_NS (default keycloak)
#   PAUSE       (default 0 — set to N seconds to slow down between steps)
# ============================================================================
set -uo pipefail

GATEWAY="${GATEWAY:-http://localhost:30080}"
IAM_NS="${IAM_NS:-aidp-iam}"
KEYCLOAK_NS="${KEYCLOAK_NS:-keycloak}"
PAUSE="${PAUSE:-0}"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; BLUE='\033[0;34m'
YELLOW='\033[1;33m'; GRAY='\033[0;90m'; NC='\033[0m'

# ── pretty output helpers ───────────────────────────────────────────────────
banner() {
  printf "\n${BLUE}════════════════════════════════════════════════════════════════════════${NC}\n"
  printf "${BLUE} %s${NC}\n" "$1"
  printf "${BLUE}════════════════════════════════════════════════════════════════════════${NC}\n"
}
step() { printf "\n${CYAN}── %s ──${NC}\n" "$1"; }
note() { printf "${GRAY}    %s${NC}\n" "$1"; }

# hcurl <role> [curl-args ...] — runs curl with the named role's bearer token
# (or no auth if role is "none"), prints a *readable* version of the command
# (showing $T_ALICE / $T_BOB rather than the 1300-char JWT) plus status +
# truncated body. Captures full body into $RESP for downstream value extraction.
#
# Examples:
#   hcurl alice -X POST -H 'Content-Type: application/json' -d '{...}' "$URL"
#   hcurl bob   "$URL"
#   hcurl none  "$URL"   # no Authorization header
RESP=""; STATUS=""
hcurl() {
  local role="$1"; shift
  local token=""
  case "$role" in
    alice) token="$T_ALICE" ;;
    bob)   token="$T_BOB"   ;;
    none)  token="" ;;
    *)     printf "${YELLOW}WARN${NC} unknown role '%s'\n" "$role"; return 1 ;;
  esac

  # ── display (with placeholder, not real token) ────────────────────────
  printf "${YELLOW}\$ curl -sS"
  [ -n "$token" ] && printf " -H 'Authorization: Bearer \$T_%s'" "${role^^}"
  for a in "$@"; do
    case "$a" in
      *' '*|*'"'*|*'\\'*) printf " %q" "$a" ;;
      *)                  printf " %s"  "$a" ;;
    esac
  done
  printf "${NC}\n"

  # ── execute (with the actual token) ───────────────────────────────────
  local args=()
  [ -n "$token" ] && args+=(-H "Authorization: Bearer $token")
  local out
  out=$(curl -sS --max-time 10 "${args[@]}" "$@" -w "\n__STATUS__%{http_code}" 2>&1)
  STATUS=$(printf '%s' "$out" | sed -n 's/.*__STATUS__\([0-9]*\)$/\1/p' | tail -1)
  RESP=$(printf '%s' "$out" | sed 's/__STATUS__[0-9]*$//')

  if [ "${STATUS:-000}" -ge 200 ] 2>/dev/null && [ "${STATUS:-000}" -lt 300 ] 2>/dev/null; then
    printf "${GREEN}  HTTP %s${NC}\n" "$STATUS"
  else
    printf "${YELLOW}  HTTP %s${NC}\n" "${STATUS:-???}"
  fi
  if [ -n "$RESP" ]; then
    printf '  %s\n' "$(printf '%s' "$RESP" | head -c 400)"
    [ "$(printf '%s' "$RESP" | wc -c)" -gt 400 ] && printf "  ${GRAY}…(truncated)${NC}\n"
  fi
  [ "$PAUSE" != "0" ] && sleep "$PAUSE"
}

# Quiet curl helper — no echo, just capture (for token + UID resolution)
quiet_curl() { curl -sS --max-time 10 "$@" 2>/dev/null; }

# Extract a JSON path from $RESP. e.g.   jp data.KDSID
jp() {
  printf '%s' "$RESP" | python -c "
import sys, json
try:
    obj = json.load(sys.stdin)
    for k in '$1'.split('.'):
        obj = obj[k] if isinstance(obj, dict) else obj[int(k)]
    print(obj if obj is not None else '')
except Exception:
    print('')
"
}

# ── Bootstrap: tokens + UUIDs ───────────────────────────────────────────────
banner "BOOTSTRAP — fetch tokens + UUIDs"

step "Fetch keycloak-aidp-client/client-secret from $IAM_NS"
SECRET=$(kubectl -n "$IAM_NS" get secret keycloak-aidp-client \
  -o go-template='{{index .data "client-secret" | base64decode}}' 2>/dev/null)
note "secret = ${SECRET:0:20}..."

get_token() {
  quiet_curl -X POST "$GATEWAY/realms/aidp/protocol/openid-connect/token" \
    -d "grant_type=password" -d "client_id=aidp-client" -d "client_secret=$SECRET" \
    -d "username=$1" -d "password=$2" \
    | python -c "import sys,json
try: print(json.load(sys.stdin)['access_token'])
except: pass"
}

step "Password-grant tokens for kb-admin and rubik-admin"
T_ALICE=$(get_token kb-admin    AppAdmin@123)
T_BOB=$(get_token rubik-admin AppAdmin@123)
note "alice (kb-admin)    token len: ${#T_ALICE}"
note "bob   (rubik-admin) token len: ${#T_BOB}"

step "Resolve user UUIDs (needed for /acl/v1 sharing)"
ALICE_UID=$(kubectl -n "$KEYCLOAK_NS" exec iam-store-0 -c postgres -- \
  psql -U keycloak -d keycloak -tA -c \
  "SELECT id FROM user_entity WHERE username='kb-admin' AND realm_id=(SELECT id FROM realm WHERE name='aidp')" 2>/dev/null | tr -d ' \r\n')
BOB_UID=$(kubectl -n "$KEYCLOAK_NS" exec iam-store-0 -c postgres -- \
  psql -U keycloak -d keycloak -tA -c \
  "SELECT id FROM user_entity WHERE username='rubik-admin' AND realm_id=(SELECT id FROM realm WHERE name='aidp')" 2>/dev/null | tr -d ' \r\n')
note "alice UUID = $ALICE_UID"
note "bob   UUID = $BOB_UID"

KB_NAME="walkthrough-kb"
LIB="walkthrough-lib"
JNAME="freeride"

# ════════════════════════════════════════════════════════════════════════════
# PART A — KB lifecycle (apioption.md 1) + /acl/v1 sharing
# ════════════════════════════════════════════════════════════════════════════
banner "PART A — KB lifecycle (apioption.md 1) + /acl/v1 sharing"

step "A.1 alice creates the KB"
hcurl alice -X POST -H "Content-Type: application/json" \
  -d "{\"name\":\"$KB_NAME\",\"description\":\"alice's KB\"}" \
  "$GATEWAY/kb/knowledge_bases/add"
KBID=$(jp data.KDSID)
note "→ KBID = $KBID"
sleep 2

step "A.1b confirm ext_proc auto-wrote the owner ACL"
note "$ kubectl psql query (truncated):"
ACL_OWNER=$(kubectl -n "$KEYCLOAK_NS" exec iam-store-0 -c postgres -- \
  psql -U keycloak -d iam -tA -c \
  "SELECT subject_id FROM resource_acl WHERE resource_id='$KBID' AND permission='owner'" 2>/dev/null | tr -d ' \r\n')
note "owner subject_id = $ACL_OWNER"
note "alice UUID       = $ALICE_UID  (should match)"

step "A.2 alice runs all GET endpoints on her KB"
for path in "/page" "/count" "?kbs_id=$KBID" "/mappings?kbs_id=$KBID" "/files?kbs_id=$KBID" \
            "/files/count?kbs_id=$KBID" "/files/history?kbs_id=$KBID" "/files/filesystem"; do
  hcurl alice "$GATEWAY/kb/knowledge_bases$path"
done

step "A.3 alice modifies + adds children"
hcurl alice -X POST -H "Content-Type: application/json" \
  -d "{\"kbs_id\":\"$KBID\",\"name\":\"${KB_NAME}-renamed\"}" \
  "$GATEWAY/kb/knowledge_bases/modify"

hcurl alice -X POST -H "Content-Type: application/json" \
  -d "{\"kbs_id\":\"$KBID\",\"src_dir\":\"/data\",\"fs_id\":\"f1\",\"fs_name\":\"local\",\"channel_name\":\"c1\"}" \
  "$GATEWAY/kb/knowledge_bases/mappings/add"
DM_ID=$(jp data.CHANNELID)
note "→ DM_ID = $DM_ID"

step "A.3b alice uploads a file (multipart via stdin)"
note "$ printf 'hi' | curl -F kbs_id=$KBID -F files=@-;filename=test.txt ..."
printf 'walkthrough content\n' | curl -sS --max-time 10 -X POST \
  -H "Authorization: Bearer $T_ALICE" \
  -F "kbs_id=$KBID" -F "files=@-;filename=walkthrough.txt;type=text/plain" \
  -w "\n  HTTP %{http_code}\n" \
  "$GATEWAY/kb/knowledge_bases/files/upload" | head -c 400; echo

step "A.3c alice removes the mapping + files (contributor-level child remove)"
hcurl alice -X POST -H "Content-Type: application/json" \
  -d "{\"kbs_id\":\"$KBID\",\"kbs_dm_id\":\"$DM_ID\"}" \
  "$GATEWAY/kb/knowledge_bases/mappings/remove"

hcurl alice -X POST -H "Content-Type: application/json" \
  -d "{\"kbs_id\":\"$KBID\",\"file_ids\":[]}" \
  "$GATEWAY/kb/knowledge_bases/files/remove"

note "(parent KB ACL should still exist — owner-cascade fix)"
sleep 1
note "ACL count for $KBID = $(kubectl -n "$KEYCLOAK_NS" exec iam-store-0 -c postgres -- \
  psql -U keycloak -d iam -tA -c \
  "SELECT count(*) FROM resource_acl WHERE resource_id='$KBID'" 2>/dev/null | tr -d ' \r\n')"

step "A.4 bob (no share) tries to read alice's KB"
hcurl bob "$GATEWAY/kb/knowledge_bases?kbs_id=$KBID"
note "↑ expect 403 — bob is in all-users, path-level OK, but no resource-level ACL"

step "A.5 alice POST /acl/v1/.../permissions to share KB to bob as VIEWER"
hcurl alice -X POST -H "Content-Type: application/json" \
  -d "{\"subject_type\":\"user\",\"subject_id\":\"$BOB_UID\",\"permission\":\"viewer\",\"app_name\":\"knowledgebase\",\"resource_type\":\"kb\"}" \
  "$GATEWAY/acl/v1/resources/$KBID/permissions"
ACL_ID=$(jp id)
note "→ ACL_ID = $ACL_ID"

step "A.5b GET /acl/v1/.../permissions (list current shares)"
hcurl alice \
  "$GATEWAY/acl/v1/resources/$KBID/permissions?app_name=knowledgebase&resource_type=kb"

step "A.6 bob with viewer: GETs work, POST modify denied"
hcurl bob "$GATEWAY/kb/knowledge_bases?kbs_id=$KBID"
hcurl bob -X POST -H "Content-Type: application/json" \
  -d "{\"kbs_id\":\"$KBID\",\"name\":\"hack\"}" \
  "$GATEWAY/kb/knowledge_bases/modify"

step "A.7 alice promotes bob to CONTRIBUTOR"
hcurl alice -X PUT -H "Content-Type: application/json" \
  -d '{"permission":"contributor"}' \
  "$GATEWAY/acl/v1/resources/$KBID/permissions/$ACL_ID"

step "A.8 bob can now modify; cannot remove (still not owner)"
hcurl bob -X POST -H "Content-Type: application/json" \
  -d "{\"kbs_id\":\"$KBID\",\"name\":\"bob-modified\"}" \
  "$GATEWAY/kb/knowledge_bases/modify"
hcurl bob -X POST -H "Content-Type: application/json" \
  -d "{\"kbs_id\":\"$KBID\"}" \
  "$GATEWAY/kb/knowledge_bases/remove"

step "A.9 alice DELETE /acl/v1/.../permissions/{acl_id} — revoke bob"
hcurl alice -X DELETE \
  "$GATEWAY/acl/v1/resources/$KBID/permissions/$ACL_ID"

note "After revoke, bob is back to no-permission:"
hcurl bob "$GATEWAY/kb/knowledge_bases?kbs_id=$KBID"


# ════════════════════════════════════════════════════════════════════════════
# PART B — Model config (apioption.md 2, kb-admins-only)
# ════════════════════════════════════════════════════════════════════════════
banner "PART B — Model config (apioption.md 2, kb-admins-only)"

step "B.1 bob (not in kb-admins) — denied at path-level"
hcurl bob "$GATEWAY/kb/models/config"
note "↑ expect 403 — kb_model_view path_rule restricts to kb-admins"

step "B.2 alice creates a model"
hcurl alice -X POST -H "Content-Type: application/json" \
  -d '{"model_type":"LLM","model_name":"qwen-walk","model_id":"qw-1","api_url":"http://x","api_key":"k","provider":"prov"}' \
  "$GATEWAY/kb/models/config/add"
MID=$(jp data)
note "→ MID = $MID"
sleep 2

step "B.3 alice GET /models/config (note MODELAPI is JSON-encoded string)"
hcurl alice "$GATEWAY/kb/models/config"

step "B.4 alice modifies + removes the model"
hcurl alice -X POST -H "Content-Type: application/json" \
  -d "{\"id\":\"$MID\",\"model_api\":{\"model_name\":\"renamed\"}}" \
  "$GATEWAY/kb/models/config/modify"
hcurl alice -X POST -H "Content-Type: application/json" \
  -d "{\"id\":\"$MID\"}" \
  "$GATEWAY/kb/models/config/remove"


# ════════════════════════════════════════════════════════════════════════════
# PART C — Prompts (apioption.md 3, read all-users / edit kb-admins)
# ════════════════════════════════════════════════════════════════════════════
banner "PART C — Prompts (apioption.md 3)"

step "C.1 alice creates a prompt group"
hcurl alice -X POST -H "Content-Type: application/json" \
  -d '{"title":"walkthrough-prompt","description":"by alice","mode":"NAIVE","prompts":{"system":"hi"}}' \
  "$GATEWAY/kb/prompts/add"
PID=$(jp data.id)
note "→ PID = $PID"

step "C.2 alice + bob both can READ (kb_prompt_view = all-users)"
hcurl alice "$GATEWAY/kb/prompts/detail?id=$PID"
hcurl bob   "$GATEWAY/kb/prompts/detail?id=$PID"
hcurl bob   "$GATEWAY/kb/prompts/page"

step "C.3 only kb-admins can EDIT — bob denied, alice allowed"
hcurl bob -X POST -H "Content-Type: application/json" \
  -d "{\"id\":\"$PID\",\"title\":\"hack\"}" \
  "$GATEWAY/kb/prompts/modify"
note "↑ expect 403"
hcurl alice -X POST -H "Content-Type: application/json" \
  -d "{\"id\":\"$PID\",\"title\":\"renamed\"}" \
  "$GATEWAY/kb/prompts/modify"

step "C.4 alice removes the prompt"
hcurl alice -X POST -H "Content-Type: application/json" \
  -d "{\"id\":\"$PID\"}" \
  "$GATEWAY/kb/prompts/remove"


# ════════════════════════════════════════════════════════════════════════════
# PART D — Jargon library (apioption.md 4, kb-admins-only)
# ════════════════════════════════════════════════════════════════════════════
banner "PART D — Jargon library (apioption.md 4)"

step "D.1 bob denied at path-level"
hcurl bob "$GATEWAY/kb/jargon_groups"
note "↑ expect 403 — kb_jargon_view = kb-admins only"

step "D.2 alice creates a jargon library"
hcurl alice -X POST -H "Content-Type: application/json" \
  -d "{\"jargon_lib_name\":\"$LIB\",\"description\":\"walkthrough\"}" \
  "$GATEWAY/kb/jargon_groups/add"
sleep 1

step "D.3 alice lists libs and queries version"
hcurl alice "$GATEWAY/kb/jargon_groups"
hcurl alice "$GATEWAY/kb/jargon_groups/version?jargon_lib_name=$LIB"

step "D.4 alice adds a jargon entry"
hcurl alice -X POST -H "Content-Type: application/json" \
  -d "{\"jargon_info_list\":[{\"jargon_name\":\"$JNAME\",\"description\":\"d\",\"target_term\":[\"free\"],\"jargon_lib_name\":\"$LIB\",\"creator_name\":\"alice\",\"creator_id\":\"$ALICE_UID\"}]}" \
  "$GATEWAY/kb/jargons/add"

step "D.5 alice queries entries inside the lib"
hcurl alice "$GATEWAY/kb/jargon_groups/jargons?jargon_lib_name=$LIB"
hcurl alice "$GATEWAY/kb/jargons_groups/jargon?jargon_lib_name=$LIB"


# ════════════════════════════════════════════════════════════════════════════
# PART E — KB ↔ jargon binding (apioption.md 7)
# ════════════════════════════════════════════════════════════════════════════
banner "PART E — KB ↔ jargon binding (apioption.md 7)"

step "E.1 BEFORE bind: GET /knowledge_bases/jargon_groups → empty"
hcurl alice \
  "$GATEWAY/kb/knowledge_bases/jargon_groups?kb_name=${KB_NAME}-renamed"

step "E.2 bob denied; alice binds"
hcurl bob -X POST -H "Content-Type: application/json" \
  -d "{\"kb_name\":\"${KB_NAME}-renamed\",\"jargon_lib_name\":\"$LIB\"}" \
  "$GATEWAY/kb/jargon_groups/knowledge_bases/add"
hcurl alice -X POST -H "Content-Type: application/json" \
  -d "{\"kb_name\":\"${KB_NAME}-renamed\",\"jargon_lib_name\":\"$LIB\"}" \
  "$GATEWAY/kb/jargon_groups/knowledge_bases/add"

step "E.3 AFTER bind: GET reflects the binding"
hcurl alice \
  "$GATEWAY/kb/knowledge_bases/jargon_groups?kb_name=${KB_NAME}-renamed"

step "E.4 alice unbinds → GET goes empty again"
hcurl alice -X POST -H "Content-Type: application/json" \
  -d "{\"kb_name\":\"${KB_NAME}-renamed\",\"jargon_lib_name\":\"$LIB\"}" \
  "$GATEWAY/kb/jargon_groups/knowledge_bases/remove"
hcurl alice \
  "$GATEWAY/kb/knowledge_bases/jargon_groups?kb_name=${KB_NAME}-renamed"


# ════════════════════════════════════════════════════════════════════════════
# PART F — Conversations (apioption.md 5, per-thread ownership)
# ════════════════════════════════════════════════════════════════════════════
banner "PART F — Conversations (apioption.md 5)"

step "F.1 alice + bob each open their own conversation"
hcurl alice -X POST -H "Content-Type: application/json" \
  -d '{"query":"hi","resources":[{"collection_name":"kb1"}],"thread_id":"","user_id":"a","output_format":"qa","rag_mode":"naive_rag","enable_proxy":false}' \
  "$GATEWAY/kb/conversations/start"
TID_ALICE=$(jp data.thread_id)
note "→ TID_ALICE = $TID_ALICE"

hcurl bob -X POST -H "Content-Type: application/json" \
  -d '{"query":"hi","resources":[],"thread_id":"","user_id":"b","output_format":"qa","rag_mode":"naive_rag"}' \
  "$GATEWAY/kb/conversations/start"
TID_BOB=$(jp data.thread_id)
note "→ TID_BOB = $TID_BOB"

step "F.2 list (each user sees their own list)"
hcurl alice "$GATEWAY/kb/conversations/list"

step "F.3 single-thread access is owner-only"
hcurl alice "$GATEWAY/kb/conversations?thread_id=$TID_ALICE"
hcurl bob   "$GATEWAY/kb/conversations?thread_id=$TID_ALICE"
note "↑ second call expect 403 — bob isn't owner of alice's thread"

step "F.4 stop is owner-only"
hcurl bob -X POST -H "Content-Type: application/json" \
  -d "{\"thread_id\":\"$TID_ALICE\"}" \
  "$GATEWAY/kb/conversations/stop"
note "↑ expect 403"
hcurl alice -X POST -H "Content-Type: application/json" \
  -d "{\"thread_id\":\"$TID_ALICE\"}" \
  "$GATEWAY/kb/conversations/stop"

step "F.5 image generate / download (no per-image ACL)"
hcurl alice -X POST -H "Content-Type: application/json" \
  -d '{"image_url":"/x.png"}' \
  "$GATEWAY/kb/conversations/images/generate"
hcurl alice "$GATEWAY/kb/conversations/images/download?token=mock-tok"

step "F.6 owners remove their own threads"
hcurl alice -X POST -H "Content-Type: application/json" \
  -d "{\"thread_id\":\"$TID_ALICE\"}" \
  "$GATEWAY/kb/conversations/remove"
hcurl bob -X POST -H "Content-Type: application/json" \
  -d "{\"thread_id\":\"$TID_BOB\"}" \
  "$GATEWAY/kb/conversations/remove"


# ════════════════════════════════════════════════════════════════════════════
# PART G — Retrieval (apioption.md 6, stateless)
# ════════════════════════════════════════════════════════════════════════════
banner "PART G — Retrieval (apioption.md 6)"

step "G.1 alice fusion_search (minimal body)"
hcurl alice -X POST -H "Content-Type: application/json" \
  -d '{"query":"hello","kds_list":["k1"],"search_method":"hybrid_search"}' \
  "$GATEWAY/kb/retrieval/fusion_search"

step "G.2 alice fusion_search (full body — reranking + multi_kds)"
hcurl alice -X POST -H "Content-Type: application/json" \
  -d '{"query":"deep test","kds_list":["k1","k2"],"search_method":"hybrid_search","reranking_enable":true,"reranking_mode":"high_accuracy","top_k":5,"multi_model":false}' \
  "$GATEWAY/kb/retrieval/fusion_search"

step "G.3 bob also can search (kb_retrieval = all-users)"
hcurl bob -X POST -H "Content-Type: application/json" \
  -d '{"query":"q","kds_list":[],"search_method":"vector_search"}' \
  "$GATEWAY/kb/retrieval/fusion_search"


# ════════════════════════════════════════════════════════════════════════════
# PART H — Cleanup
# ════════════════════════════════════════════════════════════════════════════
banner "PART H — Cleanup"

step "H.1 remove jargon entry"
hcurl alice -X POST -H "Content-Type: application/json" \
  -d "{\"jargon_lib_name\":\"$LIB\",\"jargon_name\":\"$JNAME\"}" \
  "$GATEWAY/kb/jargons/remove"

step "H.2 remove jargon library (owner-level — cascade ACL)"
hcurl alice -X POST -H "Content-Type: application/json" \
  -d "{\"jargon_lib_name\":\"$LIB\"}" \
  "$GATEWAY/kb/jargon_groups/remove"

step "H.3 remove the KB (owner-level — cascade ACL)"
hcurl alice -X POST -H "Content-Type: application/json" \
  -d "{\"kbs_id\":\"$KBID\"}" \
  "$GATEWAY/kb/knowledge_bases/remove"

printf "\n${GREEN}══════════════════════════════════════════════════════════════════════${NC}\n"
printf "${GREEN} Walkthrough complete${NC}\n"
printf "${GREEN}══════════════════════════════════════════════════════════════════════${NC}\n"
