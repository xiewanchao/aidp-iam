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

register_dataagent_manifest() {
  local token="$1"
  local _tmpf
  _tmpf="$(mktemp /tmp/da_manifest_XXXXXX.json)"
  cat > "$_tmpf" <<'DAMF'
{"namespace":"DataAgent","display_name":"智能问数","base_url":"http://mock-dataagent.mock-dataagent.svc.cluster.local:8080","resources":[{"type":"Databases","display_name":"数据库","list_filter_mode":"gateway_inject","path_pattern":"/DataAgent/Tenants/{tenantId}/Databases/{db_id}","methods":["GET","PUT","DELETE"],"actions":[{"name":"Test","path_suffix":"/Test","http_method":"POST","required_role":"AccessManager/Tenants/System/Roles/Owner"},{"name":"Check","path_suffix":"/Check","http_method":"POST","required_role":"AccessManager/Tenants/System/Roles/Owner"},{"name":"Build","path_suffix":"/Build","http_method":"POST","required_role":"AccessManager/Tenants/System/Roles/Contributor"},{"name":"ExecuteSql","path_suffix":"/ExecuteSql","http_method":"POST","required_role":"AccessManager/Tenants/System/Roles/Contributor"},{"name":"PrettifySql","path_suffix":"/PrettifySql","http_method":"POST","required_role":"AccessManager/Tenants/System/Roles/Contributor"},{"name":"StreamBuild","path_suffix":"/StreamBuild","http_method":"POST","required_role":"AccessManager/Tenants/System/Roles/Contributor"},{"name":"Cancel","path_suffix":"/Cancel","http_method":"POST","required_role":"AccessManager/Tenants/System/Roles/Contributor"},{"name":"CheckRefresh","path_suffix":"/CheckRefresh","http_method":"POST","required_role":"AccessManager/Tenants/System/Roles/Contributor"},{"name":"StreamRefresh","path_suffix":"/StreamRefresh","http_method":"POST","required_role":"AccessManager/Tenants/System/Roles/Contributor"}],"default_acl":[],"children":[]},{"type":"SpecialKL","display_name":"特殊知识","list_filter_mode":"gateway_inject","path_pattern":"/DataAgent/Tenants/{tenantId}/Databases/SpecialKL/{item_id}","methods":["GET","PUT","PATCH","DELETE"],"actions":[],"default_acl":[],"children":[]},{"type":"Sessions","display_name":"会话","list_filter_mode":"gateway_inject","path_pattern":"/DataAgent/Tenants/{tenantId}/Sessions/{session_id}","methods":["GET","PUT","DELETE"],"actions":[{"name":"Replay","path_suffix":"/Replay","http_method":"POST","required_role":"AccessManager/Tenants/System/Roles/Contributor"}],"default_acl":[{"user_template":"AccessManager/Tenants/{tenantId}/Groups/all-users","object_template":"DataAgent/Tenants/{tenantId}/Sessions","role_path":"AccessManager/Tenants/System/Roles/Owner"},{"user_template":"AccessManager/Tenants/{tenantId}/Groups/tenant-admins","object_template":"DataAgent/Tenants/{tenantId}/Sessions","role_path":"AccessManager/Tenants/System/Roles/Owner"}],"children":[{"type":"Turns","display_name":"会话轮次","list_filter_mode":"gateway_inject","path_pattern":"/DataAgent/Tenants/{tenantId}/Sessions/{session_id}/Turns","methods":["GET"],"actions":[],"default_acl":[],"children":[]}]},{"type":"Dashboards","display_name":"Dashboard","list_filter_mode":"gateway_inject","path_pattern":"/DataAgent/Tenants/{tenantId}/Dashboards/{dashboard_id}","methods":["GET","PUT","PATCH","DELETE"],"actions":[{"name":"DraftSession","path_suffix":"/DraftSession","http_method":"POST","required_role":"AccessManager/Tenants/System/Roles/Owner"},{"name":"AddToDashboard","path_suffix":"/AddToDashboard","http_method":"POST","required_role":"AccessManager/Tenants/System/Roles/Owner"},{"name":"Find","path_suffix":"/Find","http_method":"POST","required_role":"AccessManager/Tenants/System/Roles/Owner"},{"name":"Import","path_suffix":"/Import","http_method":"POST","required_role":"AccessManager/Tenants/System/Roles/Owner"}],"default_acl":[{"user_template":"AccessManager/Tenants/{tenantId}/Groups/all-users","object_template":"DataAgent/Tenants/{tenantId}/Dashboards","role_path":"AccessManager/Tenants/System/Roles/Owner"},{"user_template":"AccessManager/Tenants/{tenantId}/Groups/tenant-admins","object_template":"DataAgent/Tenants/{tenantId}/Dashboards","role_path":"AccessManager/Tenants/System/Roles/Owner"}],"children":[{"type":"Summary","display_name":"Dashboard摘要","path_pattern":"/DataAgent/Tenants/{tenantId}/Dashboards/{dashboard_id}/Summary/{summary_id}","methods":["GET","PUT"],"actions":[],"default_acl":[],"children":[]},{"type":"Guidance","display_name":"Dashboard引导摘要","path_pattern":"/DataAgent/Tenants/{tenantId}/Dashboards/{dashboard_id}/Guidance/{guidance_id}","methods":["PUT"],"actions":[],"default_acl":[],"children":[]},{"type":"Share","display_name":"分享链接","path_pattern":"/DataAgent/Tenants/{tenantId}/Dashboards/{dashboard_id}/Share/{share_id}","methods":["GET","PUT","DELETE"],"actions":[],"default_acl":[],"children":[]},{"type":"Charts","display_name":"Charts","path_pattern":"/DataAgent/Tenants/{tenantId}/Dashboards/{dashboard_id}/Charts/{chart_id}","methods":["GET"],"actions":[],"default_acl":[],"children":[]}]}],"supported_roles":["AccessManager/Tenants/System/Roles/Owner","AccessManager/Tenants/System/Roles/Contributor","AccessManager/Tenants/System/Roles/Viewer"],"custom_roles":[]}
DAMF
  local code
  code=$(curl -sS --max-time 15 -X PUT -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d "@$_tmpf" \
    -o /dev/null -w "%{http_code}" \
    "$GATEWAY/AccessManager/Tenants/System/AppManifests/DataAgent" 2>/dev/null)
  rm -f "$_tmpf"
  echo "$code"
}

wait_opa_dataagent() {
  local i
  for i in $(seq 1 14); do
    sleep 5
    local cnt
    cnt=$(MSYS_NO_PATHCONV=1 kubectl -n "$IAM_NS" exec deploy/iam-services -c aidp-iam-app -- \
      curl -s "http://localhost:8181/v1/data/path_rules" 2>/dev/null | \
      python -c "import sys,json; rules=json.load(sys.stdin).get('result',[]); print(sum(1 for r in rules if 'DataAgent' in r.get('path_prefix','')))" 2>/dev/null || echo 0)
    if [ "${cnt:-0}" -gt 0 ]; then
      printf "  OPA has DataAgent path_rules (%s rules, after %ds)\n" "$cnt" "$((i*5))"
      return 0
    fi
  done
  printf "${RED}FATAL${NC} OPA did not receive DataAgent path_rules after 70s\n"
  return 1
}

print_summary() {
  local total=$((PASS+FAIL))
  printf "\n${BLUE}══════════════════════════════════════════════════════════════════════${NC}\n"
  printf "  Passed: ${GREEN}%d${NC} / %d\n" "$PASS" "$total"
  [ "$FAIL" -gt 0 ] && printf "  ${RED}FAILED: %d${NC} / %d\n" "$FAIL" "$total"
  printf "${BLUE}══════════════════════════════════════════════════════════════════════${NC}\n"
  return "$FAIL"
}
