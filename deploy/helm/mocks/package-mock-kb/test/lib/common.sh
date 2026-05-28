#!/usr/bin/env bash
# ============================================================================
# common.sh — shared helpers for KB per-category test scripts.
#
# Source this from each category script. Provides:
#   - global config (GATEWAY, NAMESPACE, color codes, counters)
#   - tokens for 5 roles (admin / kb-admin / rubik-admin / memory-admin / normal-user)
#   - assert_eq / assert_in / assert_match / assert_contains
#   - http_code / http_body / json_get
#   - acl helpers (acl_count / acl_owner / acl_max_id / acl_latest_resource_id)
#   - section / banner / summary
#
# All assertions are STRICT: any mismatch increments FAIL and prints actual vs.
# expected. Test scripts exit 0 only when FAIL == 0.
# ============================================================================
set -uo pipefail

GATEWAY="${GATEWAY:-http://localhost:30080}"
NAMESPACE="${NAMESPACE:-mock-kb}"
IAM_NS="${IAM_NS:-aidp-iam}"
KEYCLOAK_NS="${KEYCLOAK_NS:-keycloak}"

GREEN='\033[0;32m'; RED='\033[0;31m'; CYAN='\033[0;36m'
YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

# Counters (per script). The runner aggregates by capturing stdout.
PASS=0; FAIL=0

# ── output ──────────────────────────────────────────────────────────────────
ok()      { printf "  ${GREEN}PASS${NC} %s\n" "$1"; PASS=$((PASS+1)); }
fail()    { printf "  ${RED}FAIL${NC} %s%s\n" "$1" "${2:+ — $2}"; FAIL=$((FAIL+1)); }
section() { printf "${CYAN}── %s ──${NC}\n" "$1"; }
banner()  { printf "\n${BLUE}══════════════════════════════════════════════════════════════════════${NC}\n"
            printf "${BLUE} %s${NC}\n" "$1"
            printf "${BLUE}══════════════════════════════════════════════════════════════════════${NC}\n"; }

# ── strict assertions ───────────────────────────────────────────────────────
assert_eq() {
  local desc="$1" want="$2" got="$3"
  if [ "$got" = "$want" ]; then ok "$desc (=$want)"
  else fail "$desc" "want='$want' got='$got'"
  fi
}

# Pass when $got equals any of the trailing args.
assert_in() {
  local desc="$1" got="$2"; shift 2
  for w in "$@"; do
    if [ "$got" = "$w" ]; then ok "$desc (=$got)"; return; fi
  done
  fail "$desc" "want one-of [$*] got='$got'"
}

# Pass when $got matches regex $pattern.
assert_match() {
  local desc="$1" pattern="$2" got="$3"
  if echo "$got" | grep -qE "$pattern"; then ok "$desc (matches /$pattern/)"
  else fail "$desc" "no match for /$pattern/ in '$(echo "$got" | head -c 200)'"
  fi
}

# Pass when $body contains $needle as substring.
assert_contains() {
  local desc="$1" needle="$2" body="$3"
  if echo "$body" | grep -qF "$needle"; then ok "$desc (contains '$needle')"
  else fail "$desc" "missing '$needle' in '$(echo "$body" | head -c 200)'"
  fi
}

# Pass when $body does NOT contain $needle.
assert_not_contains() {
  local desc="$1" needle="$2" body="$3"
  if echo "$body" | grep -qF "$needle"; then fail "$desc" "unexpected '$needle' in '$(echo "$body" | head -c 200)'"
  else ok "$desc (no '$needle')"
  fi
}

# ── HTTP helpers ────────────────────────────────────────────────────────────
http_code() {
  curl -sS --max-time 10 -o /dev/null -w "%{http_code}" "$@" 2>/dev/null
}

http_body() {
  curl -sS --max-time 10 "$@" 2>/dev/null
}

# Extract field via dotted path (foo.bar.baz). Returns empty on miss.
json_get() {
  local path="$1"
  python -c "import sys, json
try:
    obj = json.load(sys.stdin)
    for k in '$path'.split('.'):
        obj = obj[k] if isinstance(obj, dict) else obj[int(k)]
    print(obj if obj is not None else '')
except Exception:
    print('')
"
}

# Auth-header GET: NHTTP <token> <url>
NH() {
  local tok="$1"; shift
  curl -sS --max-time 10 -H "Authorization: Bearer $tok" "$@" 2>/dev/null
}
NHC() {
  local tok="$1"; shift
  curl -sS --max-time 10 -H "Authorization: Bearer $tok" -o /dev/null -w "%{http_code}" "$@" 2>/dev/null
}

# Auth POST helpers — body via -d
NPOST() {
  local tok="$1" url="$2" body="$3"
  curl -sS --max-time 10 -X POST -H "Authorization: Bearer $tok" \
    -H "Content-Type: application/json" -d "$body" "$url" 2>/dev/null
}
NPOSTC() {
  local tok="$1" url="$2" body="$3"
  curl -sS --max-time 10 -X POST -H "Authorization: Bearer $tok" \
    -H "Content-Type: application/json" -d "$body" \
    -o /dev/null -w "%{http_code}" "$url" 2>/dev/null
}

# ── token grab ──────────────────────────────────────────────────────────────
fetch_secret() {
  kubectl -n "$IAM_NS" get secret keycloak-aidp-client \
    -o go-template='{{index .data "client-secret" | base64decode}}' 2>/dev/null
}

get_token() {
  local username="$1" password="$2"
  curl -sS --max-time 10 -X POST "$GATEWAY/realms/aidp/protocol/openid-connect/token" \
    -d "grant_type=password" -d "client_id=aidp-client" -d "client_secret=$SECRET" \
    -d "username=$username" -d "password=$password" 2>/dev/null \
    | python -c "import sys,json
try: print(json.load(sys.stdin).get('access_token',''))
except: pass"
}

# Initialise SECRET + 5 tokens. Required at the top of each category script.
init_tokens() {
  SECRET="$(fetch_secret)"
  if [ -z "$SECRET" ]; then
    printf "${RED}FATAL${NC} Cannot fetch keycloak-aidp-client/client-secret — IAM not installed?\n"
    exit 2
  fi
  T_ADMIN="$(get_token admin Admin@123)"
  T_KBADMIN="$(get_token kb-admin AppAdmin@123)"
  T_RUBIKADMIN="$(get_token rubik-admin AppAdmin@123)"
  T_MEMADMIN="$(get_token memory-admin AppAdmin@123)"
  T_USER="$(get_token normal-user NormalUser@123)"
  for v in T_ADMIN T_KBADMIN T_RUBIKADMIN T_MEMADMIN T_USER; do
    [ -z "${!v}" ] && { printf "${RED}FATAL${NC} cannot get $v\n"; exit 2; }
  done
}

# ── ACL helpers (talk to iam DB via psql) ───────────────────────────────────
acl_count() {
  local app="$1" rid="$2"
  kubectl -n "$KEYCLOAK_NS" exec iam-store-0 -c postgres -- \
    psql -U keycloak -d iam -tA -c \
    "SELECT COUNT(*) FROM resource_acl WHERE app_name='$app' AND resource_id='$rid'" 2>/dev/null \
    | tr -d ' \r\n'
}

acl_max_id() {
  local app="$1"
  kubectl -n "$KEYCLOAK_NS" exec iam-store-0 -c postgres -- \
    psql -U keycloak -d iam -tA -c \
    "SELECT COALESCE(MAX(id), 0) FROM resource_acl WHERE app_name='$app'" 2>/dev/null \
    | tr -d ' \r\n'
}

acl_latest_resource_id() {
  local app="$1" prev_max="$2"
  kubectl -n "$KEYCLOAK_NS" exec iam-store-0 -c postgres -- \
    psql -U keycloak -d iam -tA -c \
    "SELECT resource_id FROM resource_acl WHERE app_name='$app' AND id > $prev_max ORDER BY id DESC LIMIT 1" 2>/dev/null \
    | tr -d ' \r\n'
}

acl_owner() {
  local app="$1" rid="$2"
  kubectl -n "$KEYCLOAK_NS" exec iam-store-0 -c postgres -- \
    psql -U keycloak -d iam -tA -c \
    "SELECT subject_id FROM resource_acl WHERE app_name='$app' AND resource_id='$rid' AND permission='owner' AND subject_type='user' LIMIT 1" 2>/dev/null \
    | tr -d ' \r\n'
}

# ── final summary ───────────────────────────────────────────────────────────
print_summary() {
  local total=$((PASS+FAIL))
  printf "\n${BLUE}══════════════════════════════════════════════════════════════════════${NC}\n"
  printf "  Passed: ${GREEN}%d${NC} / %d\n" "$PASS" "$total"
  [ "$FAIL" -gt 0 ] && printf "  ${RED}FAILED: %d${NC} / %d\n" "$FAIL" "$total"
  printf "${BLUE}══════════════════════════════════════════════════════════════════════${NC}\n"
  return "$FAIL"
}
