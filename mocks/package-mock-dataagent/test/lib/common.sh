#!/usr/bin/env bash
# ============================================================================
# common.sh — shared helpers for DataAgent mock test scripts.
# ============================================================================
set -uo pipefail

GATEWAY="${GATEWAY:-http://localhost:30080}"
NAMESPACE="${NAMESPACE:-mock-dataagent}"
IAM_NS="${IAM_NS:-aidp-iam}"
KEYCLOAK_NS="${KEYCLOAK_NS:-keycloak}"

GREEN='\033[0;32m'; RED='\033[0;31m'; CYAN='\033[0;36m'
YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

PASS=0; FAIL=0

ok()      { printf "  ${GREEN}PASS${NC} %s\n" "$1"; PASS=$((PASS+1)); }
fail()    { printf "  ${RED}FAIL${NC} %s%s\n" "$1" "${2:+ — $2}"; FAIL=$((FAIL+1)); }
section() { printf "${CYAN}── %s ──${NC}\n" "$1"; }
banner()  { printf "\n${BLUE}══════════════════════════════════════════════════════════════════════${NC}\n"
            printf "${BLUE} %s${NC}\n" "$1"
            printf "${BLUE}══════════════════════════════════════════════════════════════════════${NC}\n"; }

assert_eq() {
  local desc="$1" want="$2" got="$3"
  if [ "$got" = "$want" ]; then ok "$desc (=$want)"
  else fail "$desc" "want='$want' got='$got'"
  fi
}

assert_in() {
  local desc="$1" got="$2"; shift 2
  for w in "$@"; do
    if [ "$got" = "$w" ]; then ok "$desc (=$got)"; return; fi
  done
  fail "$desc" "want one-of [$*] got='$got'"
}

assert_match() {
  local desc="$1" pattern="$2" got="$3"
  if echo "$got" | grep -qE "$pattern"; then ok "$desc (matches /$pattern/)"
  else fail "$desc" "no match for /$pattern/ in '$(echo "$got" | head -c 200)'"
  fi
}

assert_contains() {
  local desc="$1" needle="$2" body="$3"
  if echo "$body" | grep -qF "$needle"; then ok "$desc (contains '$needle')"
  else fail "$desc" "missing '$needle' in '$(echo "$body" | head -c 200)'"
  fi
}

assert_not_contains() {
  local desc="$1" needle="$2" body="$3"
  if echo "$body" | grep -qF "$needle"; then fail "$desc" "unexpected '$needle' in '$(echo "$body" | head -c 200)'"
  else ok "$desc (no '$needle')"
  fi
}

http_code() {
  curl -sS --max-time 10 -o /dev/null -w "%{http_code}" "$@" 2>/dev/null
}

http_body() {
  curl -sS --max-time 10 "$@" 2>/dev/null
}

NH()    { local tok="$1"; shift; curl -sS --max-time 10 -H "Authorization: Bearer $tok" "$@" 2>/dev/null; }
NHC()   { local tok="$1"; shift; curl -sS --max-time 10 -H "Authorization: Bearer $tok" -o /dev/null -w "%{http_code}" "$@" 2>/dev/null; }

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

NPUT() {
  local tok="$1" url="$2" body="${3:-}"
  if [ -n "$body" ]; then
    curl -sS --max-time 10 -X PUT -H "Authorization: Bearer $tok" \
      -H "Content-Type: application/json" -d "$body" "$url" 2>/dev/null
  else
    curl -sS --max-time 10 -X PUT -H "Authorization: Bearer $tok" "$url" 2>/dev/null
  fi
}
NPUTC() {
  local tok="$1" url="$2" body="${3:-}"
  if [ -n "$body" ]; then
    curl -sS --max-time 10 -X PUT -H "Authorization: Bearer $tok" \
      -H "Content-Type: application/json" -d "$body" \
      -o /dev/null -w "%{http_code}" "$url" 2>/dev/null
  else
    curl -sS --max-time 10 -X PUT -H "Authorization: Bearer $tok" \
      -o /dev/null -w "%{http_code}" "$url" 2>/dev/null
  fi
}

NPATCH() {
  local tok="$1" url="$2" body="$3"
  curl -sS --max-time 10 -X PATCH -H "Authorization: Bearer $tok" \
    -H "Content-Type: application/json" -d "$body" \
    -o /dev/null -w "%{http_code}" "$url" 2>/dev/null
}

NDEL() {
  local tok="$1" url="$2"
  curl -sS --max-time 10 -X DELETE -H "Authorization: Bearer $tok" \
    -o /dev/null -w "%{http_code}" "$url" 2>/dev/null
}

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

init_tokens() {
  SECRET="$(fetch_secret)"
  if [ -z "$SECRET" ]; then
    printf "${RED}FATAL${NC} Cannot fetch keycloak-aidp-client/client-secret — IAM not installed?\n"
    exit 2
  fi
  T_ADMIN="$(get_token admin Admin@123)"
  T_USER="$(get_token normal-user NormalUser@123)"
  for v in T_ADMIN T_USER; do
    [ -z "${!v}" ] && { printf "${RED}FATAL${NC} cannot get $v\n"; exit 2; }
  done
}

# ACL helpers — object_path format: DataAgent/Tenants/{tid}/Databases/{id}
acl_count() {
  local rid="$1"
  kubectl -n "$KEYCLOAK_NS" exec postgres-0 -c postgres -- \
    psql -U keycloak -d iam -tA -c \
    "SELECT COUNT(*) FROM resource_acl WHERE object_path LIKE '%/$rid'" 2>/dev/null \
    | tr -d ' \r\n'
}

acl_owner() {
  local rid="$1"
  kubectl -n "$KEYCLOAK_NS" exec postgres-0 -c postgres -- \
    psql -U keycloak -d iam -tA -c \
    "SELECT split_part(user_path, '/', -1) FROM resource_acl WHERE object_path LIKE '%/$rid' AND role_path LIKE '%/Owner' LIMIT 1" 2>/dev/null \
    | tr -d ' \r\n'
}

create_group() {
  local token="$1" tid="$2" name="$3" desc="${4:-}"
  curl -sS --max-time 10 -X PUT -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"$name\",\"description\":\"$desc\"}" \
    "$GATEWAY/AccessManager/Tenants/$tid/Groups" 2>/dev/null
}

get_group_id() {
  local token="$1" tid="$2" name="$3"
  curl -sS --max-time 10 -H "Authorization: Bearer $token" \
    "$GATEWAY/AccessManager/Tenants/$tid/Groups" 2>/dev/null \
    | python -c "
import sys, json
d = json.load(sys.stdin)
groups = d.get('groups', d) if isinstance(d, dict) else d
for g in groups:
    if g.get('name') == '$name':
        print(g.get('id',''))
        break
"
}

add_user_to_group() {
  local token="$1" tid="$2" group_id="$3" user_id="$4"
  curl -sS --max-time 10 -X PUT -H "Authorization: Bearer $token" \
    -o /dev/null -w "%{http_code}" \
    "$GATEWAY/AccessManager/Tenants/$tid/Users/$user_id/Groups/$group_id" 2>/dev/null
}

delete_group() {
  local token="$1" tid="$2" group_id="$3"
  curl -sS --max-time 10 -X DELETE -H "Authorization: Bearer $token" \
    -o /dev/null -w "%{http_code}" \
    "$GATEWAY/AccessManager/Tenants/$tid/Groups/$group_id" 2>/dev/null
}

grant_db_permission() {
  local token="$1" user_path="$2" db_obj="$3" role="$4"
  curl -sS --max-time 10 -X PUT -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d "{\"user_path\":\"$user_path\",\"object_path\":\"$db_obj\",\"role_path\":\"$role\"}" \
    -o /dev/null -w "%{http_code}" \
    "$GATEWAY/AccessManager/Tenants/$TID/ACLs" 2>/dev/null
}

revoke_permission() {
  local token="$1" user_path="$2" db_obj="$3"
  curl -sS --max-time 10 -X DELETE -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d "{\"user_path\":\"$user_path\",\"object_path\":\"$db_obj\"}" \
    -o /dev/null -w "%{http_code}" \
    "$GATEWAY/AccessManager/Tenants/$TID/ACLs" 2>/dev/null
}

print_summary() {
  local total=$((PASS+FAIL))
  printf "\n${BLUE}══════════════════════════════════════════════════════════════════════${NC}\n"
  printf "  Passed: ${GREEN}%d${NC} / %d\n" "$PASS" "$total"
  [ "$FAIL" -gt 0 ] && printf "  ${RED}FAILED: %d${NC} / %d\n" "$FAIL" "$total"
  printf "${BLUE}══════════════════════════════════════════════════════════════════════${NC}\n"
  return "$FAIL"
}
