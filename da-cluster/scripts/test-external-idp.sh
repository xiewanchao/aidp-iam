#!/usr/bin/env bash
# =============================================================================
# SR09: External IdP Federation Test (Keycloak OIDC Brokering)
# =============================================================================
#
# Simulates external customer IdP integration by:
#   1. Creating an "external-corp" realm as the simulated external IdP
#   2. Creating a client + user in external-corp
#   3. Adding an OIDC Identity Provider in data-agent pointing to external-corp
#   4. Testing token exchange: external-corp token -> data-agent token
#   5. Verifying brokered user gets auto-created with all-users group
#
# Usage:
#   PORT=9091 bash scripts/test-external-idp.sh
#   CLEANUP_ONLY=1 bash scripts/test-external-idp.sh
#
# Prerequisites:
#   - Kind cluster running with Keycloak deployed
#   - kubectl port-forward to Keycloak on $PORT (or script sets it up)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLUSTER_NAME="${CLUSTER_NAME:-da-cluster}"
KEYCLOAK_NS="${KEYCLOAK_NS:-keycloak}"
PORT="${PORT:-9091}"
KC_URL="http://localhost:${PORT}"

# External IdP simulation parameters
EXT_REALM="external-corp"
EXT_CLIENT_ID="ext-corp-client"
EXT_CLIENT_SECRET="ext-corp-secret-$(date +%s)"
EXT_USER="ext-user"
EXT_USER_PASSWORD="ExtUser@123"
EXT_USER_EMAIL="ext-user@external-corp.example.com"
EXT_USER_FIRST="External"
EXT_USER_LAST="User"

# Target realm
TARGET_REALM="data-agent"
IDP_ALIAS="external-corp-oidc"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

PASS=0
FAIL=0
TOTAL=0

# ── Assertion helpers (same pattern as test.sh) ─────────────────────────────
assert() {
  local desc="$1" expected="$2" actual="$3"
  TOTAL=$((TOTAL + 1))
  if [ "$expected" = "$actual" ]; then
    echo -e "  ${GREEN}PASS${NC} $desc"
    PASS=$((PASS + 1))
  else
    echo -e "  ${RED}FAIL${NC} $desc (expected=$expected, actual=$actual)"
    FAIL=$((FAIL + 1))
  fi
}

assert_contains() {
  local desc="$1" expected="$2" actual="$3"
  TOTAL=$((TOTAL + 1))
  if echo "$actual" | grep -q "$expected"; then
    echo -e "  ${GREEN}PASS${NC} $desc"
    PASS=$((PASS + 1))
  else
    echo -e "  ${RED}FAIL${NC} $desc (expected to contain '$expected')"
    FAIL=$((FAIL + 1))
  fi
}

assert_not_empty() {
  local desc="$1" actual="$2"
  TOTAL=$((TOTAL + 1))
  if [ -n "$actual" ]; then
    echo -e "  ${GREEN}PASS${NC} $desc"
    PASS=$((PASS + 1))
  else
    echo -e "  ${RED}FAIL${NC} $desc (was empty)"
    FAIL=$((FAIL + 1))
  fi
}

# ── Utility: decode JWT payload ─────────────────────────────────────────────
decode_jwt_payload() {
  local token="$1"
  local payload
  payload=$(echo "$token" | cut -d. -f2 | tr '_-' '/+')
  local padding=$((4 - ${#payload} % 4))
  if [ "$padding" -lt 4 ]; then
    for ((i=0; i<padding; i++)); do payload="${payload}="; done
  fi
  echo "$payload" | base64 -d 2>/dev/null || echo "{}"
}

# ── Utility: get Keycloak admin token ───────────────────────────────────────
get_admin_token() {
  local response
  response=$(curl -sf -X POST "${KC_URL}/realms/master/protocol/openid-connect/token" \
    -d "grant_type=password" \
    -d "client_id=admin-cli" \
    -d "username=admin" \
    -d "password=admin" 2>/dev/null || echo "{}")
  echo "$response" | jq -r '.access_token // empty' 2>/dev/null || echo ""
}

# ── Utility: refresh admin token if needed ──────────────────────────────────
ensure_admin_token() {
  if [ -z "${ADMIN_TOKEN:-}" ]; then
    ADMIN_TOKEN=$(get_admin_token)
    if [ -z "$ADMIN_TOKEN" ]; then
      echo -e "${RED}FATAL: Cannot get Keycloak admin token${NC}"
      exit 1
    fi
  fi
}

kc_admin() {
  local method="$1" path="$2"
  shift 2
  curl -sf -X "$method" "${KC_URL}/admin${path}" \
    -H "Authorization: Bearer ${ADMIN_TOKEN}" \
    -H "Content-Type: application/json" \
    "$@" 2>/dev/null
}

kc_admin_code() {
  local method="$1" path="$2"
  shift 2
  curl -s -o /dev/null -w "%{http_code}" -X "$method" "${KC_URL}/admin${path}" \
    -H "Authorization: Bearer ${ADMIN_TOKEN}" \
    -H "Content-Type: application/json" \
    "$@" 2>/dev/null || echo "000"
}

# ── Setup port-forward ──────────────────────────────────────────────────────
setup_port_forward() {
  echo -e "${YELLOW}Setting up port-forward to Keycloak on port ${PORT}...${NC}"

  if curl -s -o /dev/null -w "%{http_code}" "${KC_URL}/realms/master" 2>/dev/null | grep -qE '200|301|302'; then
    echo -e "  ${GREEN}Port ${PORT} already forwarded to Keycloak${NC}"
    PF_PID=""
    return
  fi

  # Kill any existing process on this port
  lsof -ti:"${PORT}" 2>/dev/null | xargs kill -9 2>/dev/null || true

  KC_SVC=$(kubectl -n "$KEYCLOAK_NS" get svc -l app=keycloak -o name 2>/dev/null | head -1)
  if [ -z "$KC_SVC" ]; then
    KC_SVC="svc/keycloak"
  fi

  kubectl -n "$KEYCLOAK_NS" port-forward "$KC_SVC" "${PORT}:8080" &
  PF_PID=$!
  sleep 3

  if ! curl -s -o /dev/null -w "%{http_code}" "${KC_URL}/realms/master" 2>/dev/null | grep -qE '200|301|302'; then
    echo -e "  ${RED}Failed to connect to Keycloak on port ${PORT}${NC}"
    exit 1
  fi
  echo -e "  ${GREEN}Connected to Keycloak${NC}"
}

# ── Cleanup function ────────────────────────────────────────────────────────
cleanup() {
  echo -e "\n${YELLOW}=== Cleanup ===${NC}"
  ensure_admin_token

  # Remove IdP from data-agent realm
  local code
  code=$(kc_admin_code "DELETE" "/realms/${TARGET_REALM}/identity-provider/instances/${IDP_ALIAS}")
  if [ "$code" = "204" ] || [ "$code" = "200" ]; then
    echo -e "  ${GREEN}Removed IdP '${IDP_ALIAS}' from ${TARGET_REALM}${NC}"
  elif [ "$code" = "404" ]; then
    echo -e "  ${CYAN}IdP '${IDP_ALIAS}' not found in ${TARGET_REALM} (already clean)${NC}"
  else
    echo -e "  ${YELLOW}IdP removal returned HTTP ${code}${NC}"
  fi

  # Remove brokered user from data-agent realm if it was created
  local users
  users=$(kc_admin "GET" "/realms/${TARGET_REALM}/users?username=${EXT_USER}&exact=true" 2>/dev/null || echo "[]")
  local user_id
  user_id=$(echo "$users" | jq -r '.[0].id // empty' 2>/dev/null || echo "")
  if [ -n "$user_id" ]; then
    kc_admin "DELETE" "/realms/${TARGET_REALM}/users/${user_id}" 2>/dev/null || true
    echo -e "  ${GREEN}Removed brokered user '${EXT_USER}' from ${TARGET_REALM}${NC}"
  else
    echo -e "  ${CYAN}Brokered user '${EXT_USER}' not found in ${TARGET_REALM} (already clean)${NC}"
  fi

  # Delete external-corp realm entirely
  code=$(kc_admin_code "DELETE" "/realms/${EXT_REALM}")
  if [ "$code" = "204" ] || [ "$code" = "200" ]; then
    echo -e "  ${GREEN}Deleted realm '${EXT_REALM}'${NC}"
  elif [ "$code" = "404" ]; then
    echo -e "  ${CYAN}Realm '${EXT_REALM}' not found (already clean)${NC}"
  else
    echo -e "  ${YELLOW}Realm deletion returned HTTP ${code}${NC}"
  fi

  echo -e "  ${GREEN}Cleanup complete${NC}"
}

# ── Trap for cleanup on exit ────────────────────────────────────────────────
trap_cleanup() {
  if [ -n "${PF_PID:-}" ]; then
    kill "$PF_PID" 2>/dev/null || true
  fi
}
trap trap_cleanup EXIT

# =============================================================================
# Main
# =============================================================================
PF_PID=""
ADMIN_TOKEN=""

setup_port_forward

echo -e "\n${CYAN}================================================================${NC}"
echo -e "${CYAN}  SR09: External IdP Federation Test (OIDC Brokering)${NC}"
echo -e "${CYAN}================================================================${NC}"

ensure_admin_token
echo -e "  ${GREEN}Admin token acquired${NC}"

# If CLEANUP_ONLY is set, just clean up and exit
if [ "${CLEANUP_ONLY:-}" = "1" ]; then
  cleanup
  exit 0
fi

# ══════════════════════════════════════════════════════════════════════════════
# Step 1: Create external-corp realm (simulated external IdP)
# ══════════════════════════════════════════════════════════════════════════════
echo -e "\n${YELLOW}=== Step 1: Create external-corp realm (simulated external IdP) ===${NC}"

# Clean up any previous run first
EXISTING_REALM=$(kc_admin_code "GET" "/realms/${EXT_REALM}")
if [ "$EXISTING_REALM" = "200" ]; then
  echo -e "  ${CYAN}Realm '${EXT_REALM}' already exists, deleting for clean test...${NC}"
  kc_admin "DELETE" "/realms/${EXT_REALM}" 2>/dev/null || true
  sleep 1
  # Refresh token after realm deletion
  ADMIN_TOKEN=$(get_admin_token)
fi

# Create realm
CREATE_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${KC_URL}/admin/realms" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{
    \"realm\": \"${EXT_REALM}\",
    \"displayName\": \"External Corp (Simulated IdP)\",
    \"enabled\": true,
    \"registrationAllowed\": false,
    \"loginWithEmailAllowed\": true
  }" 2>/dev/null || echo "000")
assert "Create external-corp realm" "201" "$CREATE_CODE"

# ══════════════════════════════════════════════════════════════════════════════
# Step 2: Create OIDC client in external-corp (for brokering)
# ══════════════════════════════════════════════════════════════════════════════
echo -e "\n${YELLOW}=== Step 2: Create OIDC client in external-corp ===${NC}"

# This client will be used by data-agent realm to authenticate against external-corp.
# It needs: confidential, directAccessGrantsEnabled, standardFlowEnabled
CLIENT_CREATE_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
  "${KC_URL}/admin/realms/${EXT_REALM}/clients" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{
    \"clientId\": \"${EXT_CLIENT_ID}\",
    \"name\": \"External Corp OIDC Client\",
    \"enabled\": true,
    \"clientAuthenticatorType\": \"client-secret\",
    \"secret\": \"${EXT_CLIENT_SECRET}\",
    \"redirectUris\": [\"${KC_URL}/realms/${TARGET_REALM}/broker/${IDP_ALIAS}/endpoint*\", \"*\"],
    \"webOrigins\": [\"*\"],
    \"directAccessGrantsEnabled\": true,
    \"standardFlowEnabled\": true,
    \"publicClient\": false,
    \"protocol\": \"openid-connect\"
  }" 2>/dev/null || echo "000")
assert "Create OIDC client in external-corp" "201" "$CLIENT_CREATE_CODE"

# Verify OIDC discovery for external-corp
EXT_OIDC_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  "${KC_URL}/realms/${EXT_REALM}/.well-known/openid-configuration" 2>/dev/null || echo "000")
assert "external-corp OIDC discovery endpoint" "200" "$EXT_OIDC_CODE"

# ══════════════════════════════════════════════════════════════════════════════
# Step 3: Create test user in external-corp
# ══════════════════════════════════════════════════════════════════════════════
echo -e "\n${YELLOW}=== Step 3: Create test user in external-corp ===${NC}"

USER_CREATE_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
  "${KC_URL}/admin/realms/${EXT_REALM}/users" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{
    \"username\": \"${EXT_USER}\",
    \"email\": \"${EXT_USER_EMAIL}\",
    \"firstName\": \"${EXT_USER_FIRST}\",
    \"lastName\": \"${EXT_USER_LAST}\",
    \"enabled\": true,
    \"emailVerified\": true
  }" 2>/dev/null || echo "000")
assert "Create ext-user in external-corp" "201" "$USER_CREATE_CODE"

# Get the user ID
EXT_USER_ID=$(kc_admin "GET" "/realms/${EXT_REALM}/users?username=${EXT_USER}&exact=true" 2>/dev/null \
  | jq -r '.[0].id // empty' 2>/dev/null || echo "")
assert_not_empty "ext-user ID retrieved" "$EXT_USER_ID"

# Set password
if [ -n "$EXT_USER_ID" ]; then
  PWD_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X PUT \
    "${KC_URL}/admin/realms/${EXT_REALM}/users/${EXT_USER_ID}/reset-password" \
    -H "Authorization: Bearer ${ADMIN_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"type\": \"password\", \"value\": \"${EXT_USER_PASSWORD}\", \"temporary\": false}" \
    2>/dev/null || echo "000")
  assert "Set ext-user password" "204" "$PWD_CODE"
fi

# Verify direct access grant works for ext-user against external-corp
EXT_TOKEN_RESP=$(curl -sf -X POST "${KC_URL}/realms/${EXT_REALM}/protocol/openid-connect/token" \
  -d "grant_type=password" \
  -d "client_id=${EXT_CLIENT_ID}" \
  -d "client_secret=${EXT_CLIENT_SECRET}" \
  -d "username=${EXT_USER}" \
  -d "password=${EXT_USER_PASSWORD}" 2>/dev/null || echo "{}")
EXT_ACCESS_TOKEN=$(echo "$EXT_TOKEN_RESP" | jq -r '.access_token // empty' 2>/dev/null || echo "")
assert_not_empty "ext-user can authenticate against external-corp" "$EXT_ACCESS_TOKEN"

if [ -n "$EXT_ACCESS_TOKEN" ]; then
  EXT_JWT=$(decode_jwt_payload "$EXT_ACCESS_TOKEN")
  EXT_ISS=$(echo "$EXT_JWT" | jq -r '.iss // empty' 2>/dev/null || echo "")
  assert_contains "ext-user token issuer is external-corp" "${EXT_REALM}" "$EXT_ISS"
  echo -e "  ${CYAN}External token issuer: ${EXT_ISS}${NC}"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Step 4: Configure OIDC Identity Provider in data-agent realm
# ══════════════════════════════════════════════════════════════════════════════
echo -e "\n${YELLOW}=== Step 4: Configure OIDC IdP in data-agent -> external-corp ===${NC}"

# Fetch external-corp OIDC discovery to get endpoints
EXT_OIDC_DISC=$(curl -sf "${KC_URL}/realms/${EXT_REALM}/.well-known/openid-configuration" 2>/dev/null || echo "{}")
EXT_AUTH_URL=$(echo "$EXT_OIDC_DISC" | jq -r '.authorization_endpoint // empty' 2>/dev/null || echo "")
EXT_TOKEN_URL=$(echo "$EXT_OIDC_DISC" | jq -r '.token_endpoint // empty' 2>/dev/null || echo "")
EXT_USERINFO_URL=$(echo "$EXT_OIDC_DISC" | jq -r '.userinfo_endpoint // empty' 2>/dev/null || echo "")
EXT_JWKS_URL=$(echo "$EXT_OIDC_DISC" | jq -r '.jwks_uri // empty' 2>/dev/null || echo "")
EXT_ISSUER=$(echo "$EXT_OIDC_DISC" | jq -r '.issuer // empty' 2>/dev/null || echo "")
EXT_LOGOUT_URL=$(echo "$EXT_OIDC_DISC" | jq -r '.end_session_endpoint // empty' 2>/dev/null || echo "")

echo -e "  ${CYAN}Authorization URL: ${EXT_AUTH_URL}${NC}"
echo -e "  ${CYAN}Token URL: ${EXT_TOKEN_URL}${NC}"

assert_not_empty "External auth URL discovered" "$EXT_AUTH_URL"
assert_not_empty "External token URL discovered" "$EXT_TOKEN_URL"

# Remove existing IdP if any (idempotent)
kc_admin "DELETE" "/realms/${TARGET_REALM}/identity-provider/instances/${IDP_ALIAS}" 2>/dev/null || true

# Create OIDC Identity Provider in data-agent
IDP_CREATE_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
  "${KC_URL}/admin/realms/${TARGET_REALM}/identity-provider/instances" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{
    \"alias\": \"${IDP_ALIAS}\",
    \"displayName\": \"External Corp (OIDC)\",
    \"providerId\": \"oidc\",
    \"enabled\": true,
    \"trustEmail\": true,
    \"storeToken\": true,
    \"firstBrokerLoginFlowAlias\": \"first broker login\",
    \"config\": {
      \"clientId\": \"${EXT_CLIENT_ID}\",
      \"clientSecret\": \"${EXT_CLIENT_SECRET}\",
      \"tokenUrl\": \"${EXT_TOKEN_URL}\",
      \"authorizationUrl\": \"${EXT_AUTH_URL}\",
      \"userInfoUrl\": \"${EXT_USERINFO_URL}\",
      \"jwksUrl\": \"${EXT_JWKS_URL}\",
      \"issuer\": \"${EXT_ISSUER}\",
      \"logoutUrl\": \"${EXT_LOGOUT_URL}\",
      \"validateSignature\": \"true\",
      \"useJwksUrl\": \"true\",
      \"syncMode\": \"IMPORT\",
      \"clientAuthMethod\": \"client_secret_post\",
      \"defaultScope\": \"openid email profile\"
    }
  }" 2>/dev/null || echo "000")
assert "Create OIDC IdP in data-agent" "201" "$IDP_CREATE_CODE"

# Verify IdP is listed
IDP_LIST=$(kc_admin "GET" "/realms/${TARGET_REALM}/identity-provider/instances" 2>/dev/null || echo "[]")
IDP_COUNT=$(echo "$IDP_LIST" | jq 'length' 2>/dev/null || echo "0")
assert_contains "IdP '${IDP_ALIAS}' exists in data-agent" "${IDP_ALIAS}" "$IDP_LIST"
echo -e "  ${CYAN}Total IdPs in data-agent: ${IDP_COUNT}${NC}"

# ══════════════════════════════════════════════════════════════════════════════
# Step 5: Enable token exchange in data-agent realm
# ══════════════════════════════════════════════════════════════════════════════
echo -e "\n${YELLOW}=== Step 5: Enable token exchange permissions ===${NC}"

# Enable fine-grained admin permissions on the IdP (required for token exchange)
PERM_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X PUT \
  "${KC_URL}/admin/realms/${TARGET_REALM}/identity-provider/instances/${IDP_ALIAS}/management/permissions" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}' 2>/dev/null || echo "000")
echo -e "  ${CYAN}IdP permissions enable: HTTP ${PERM_CODE}${NC}"

# Get the data-agent client (needed for token exchange)
DA_CLIENTS=$(kc_admin "GET" "/realms/${TARGET_REALM}/clients?clientId=data-agent" 2>/dev/null || echo "[]")
DA_CLIENT_UUID=$(echo "$DA_CLIENTS" | jq -r '.[0].id // empty' 2>/dev/null || echo "")

if [ -z "$DA_CLIENT_UUID" ]; then
  # Try alternative name
  DA_CLIENTS=$(kc_admin "GET" "/realms/${TARGET_REALM}/clients?clientId=data-agent-client" 2>/dev/null || echo "[]")
  DA_CLIENT_UUID=$(echo "$DA_CLIENTS" | jq -r '.[0].id // empty' 2>/dev/null || echo "")
fi
DA_CLIENT_ID=$(echo "$DA_CLIENTS" | jq -r '.[0].clientId // empty' 2>/dev/null || echo "")
echo -e "  ${CYAN}Target client: ${DA_CLIENT_ID} (uuid: ${DA_CLIENT_UUID})${NC}"

# ══════════════════════════════════════════════════════════════════════════════
# Step 6: Test external-corp -> data-agent token exchange
# ══════════════════════════════════════════════════════════════════════════════
echo -e "\n${YELLOW}=== Step 6: Token exchange (external-corp -> data-agent) ===${NC}"

# Get a fresh external-corp token
EXT_TOKEN_RESP=$(curl -sf -X POST "${KC_URL}/realms/${EXT_REALM}/protocol/openid-connect/token" \
  -d "grant_type=password" \
  -d "client_id=${EXT_CLIENT_ID}" \
  -d "client_secret=${EXT_CLIENT_SECRET}" \
  -d "username=${EXT_USER}" \
  -d "password=${EXT_USER_PASSWORD}" 2>/dev/null || echo "{}")
FRESH_EXT_TOKEN=$(echo "$EXT_TOKEN_RESP" | jq -r '.access_token // empty' 2>/dev/null || echo "")
assert_not_empty "Fresh external-corp token acquired" "$FRESH_EXT_TOKEN"

# Attempt token exchange via data-agent realm
# Token exchange: exchange an external IDP token for a local data-agent token
# grant_type=urn:ietf:params:oauth:grant-type:token-exchange
if [ -n "$FRESH_EXT_TOKEN" ] && [ -n "$DA_CLIENT_ID" ]; then
  # Read client secret from K8s if the client is confidential
  DA_CLIENT_SECRET=$(kubectl -n "$KEYCLOAK_NS" get secret keycloak-data-agent-client \
    -o jsonpath='{.data.client-secret}' 2>/dev/null | base64 -d 2>/dev/null || echo "")

  EXCHANGE_ARGS=(-d "grant_type=urn:ietf:params:oauth:grant-type:token-exchange" \
    -d "subject_token_type=urn:ietf:params:oauth:token-type:access_token" \
    -d "subject_token=${FRESH_EXT_TOKEN}" \
    -d "subject_issuer=${IDP_ALIAS}" \
    -d "client_id=${DA_CLIENT_ID}")

  if [ -n "$DA_CLIENT_SECRET" ]; then
    EXCHANGE_ARGS+=(-d "client_secret=${DA_CLIENT_SECRET}")
  fi

  EXCHANGE_RESP=$(curl -s -X POST "${KC_URL}/realms/${TARGET_REALM}/protocol/openid-connect/token" \
    "${EXCHANGE_ARGS[@]}" 2>/dev/null || echo "{}")

  EXCHANGED_TOKEN=$(echo "$EXCHANGE_RESP" | jq -r '.access_token // empty' 2>/dev/null || echo "")
  EXCHANGE_ERROR=$(echo "$EXCHANGE_RESP" | jq -r '.error_description // .error // empty' 2>/dev/null || echo "")

  if [ -n "$EXCHANGED_TOKEN" ]; then
    assert "Token exchange successful" "true" "true"

    # Decode the exchanged token
    DA_JWT=$(decode_jwt_payload "$EXCHANGED_TOKEN")
    DA_ISS=$(echo "$DA_JWT" | jq -r '.iss // empty' 2>/dev/null || echo "")
    DA_SUB=$(echo "$DA_JWT" | jq -r '.preferred_username // .sub // empty' 2>/dev/null || echo "")
    DA_EMAIL=$(echo "$DA_JWT" | jq -r '.email // empty' 2>/dev/null || echo "")
    DA_GROUPS=$(echo "$DA_JWT" | jq -r '.groups // empty' 2>/dev/null || echo "")

    echo -e "  ${CYAN}Exchanged token issuer: ${DA_ISS}${NC}"
    echo -e "  ${CYAN}Exchanged token subject: ${DA_SUB}${NC}"
    echo -e "  ${CYAN}Exchanged token email: ${DA_EMAIL}${NC}"
    echo -e "  ${CYAN}Exchanged token groups: ${DA_GROUPS}${NC}"

    assert_contains "Exchanged token issuer is data-agent" "${TARGET_REALM}" "$DA_ISS"
    assert_contains "Exchanged token has email" "ext-user" "$DA_EMAIL"

    # Verify user got groups (all-users is default group)
    if [ -n "$DA_GROUPS" ] && [ "$DA_GROUPS" != "null" ]; then
      assert_contains "Exchanged token has all-users group" "all-users" "$DA_GROUPS"
    else
      echo -e "  ${YELLOW}NOTE${NC} groups claim not in token (may need mapper or first-login to complete)"
    fi
  else
    echo -e "  ${YELLOW}NOTE${NC} Token exchange returned: ${EXCHANGE_ERROR}"
    echo -e "  ${CYAN}Token exchange may require preview features enabled in Keycloak.${NC}"
    echo -e "  ${CYAN}Falling back to direct user verification...${NC}"
    assert "Token exchange (may need preview features)" "true" "false"
  fi
else
  echo -e "  ${YELLOW}SKIP${NC} Missing external token or client ID"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Step 7: Verify IdP configuration via keycloak-proxy API
# ══════════════════════════════════════════════════════════════════════════════
echo -e "\n${YELLOW}=== Step 7: Verify IdP via Keycloak Admin API ===${NC}"

# Verify the IdP details
IDP_DETAIL=$(kc_admin "GET" "/realms/${TARGET_REALM}/identity-provider/instances/${IDP_ALIAS}" 2>/dev/null || echo "{}")
IDP_ENABLED=$(echo "$IDP_DETAIL" | jq -r '.enabled // empty' 2>/dev/null || echo "")
IDP_PROVIDER=$(echo "$IDP_DETAIL" | jq -r '.providerId // empty' 2>/dev/null || echo "")
IDP_TRUST_EMAIL=$(echo "$IDP_DETAIL" | jq -r '.trustEmail // empty' 2>/dev/null || echo "")
IDP_FIRST_BROKER=$(echo "$IDP_DETAIL" | jq -r '.firstBrokerLoginFlowAlias // empty' 2>/dev/null || echo "")
IDP_TOKEN_URL=$(echo "$IDP_DETAIL" | jq -r '.config.tokenUrl // empty' 2>/dev/null || echo "")

assert "IdP is enabled" "true" "$IDP_ENABLED"
assert "IdP provider is oidc" "oidc" "$IDP_PROVIDER"
assert "IdP trustEmail is true" "true" "$IDP_TRUST_EMAIL"
assert "IdP firstBrokerLoginFlowAlias" "first broker login" "$IDP_FIRST_BROKER"
assert_contains "IdP tokenUrl points to external-corp" "${EXT_REALM}" "$IDP_TOKEN_URL"

# ══════════════════════════════════════════════════════════════════════════════
# Step 8: Verify brokered user (if token exchange created one)
# ══════════════════════════════════════════════════════════════════════════════
echo -e "\n${YELLOW}=== Step 8: Verify brokered user in data-agent ===${NC}"

# Check if the external user was auto-provisioned in data-agent
BROKERED_USERS=$(kc_admin "GET" "/realms/${TARGET_REALM}/users?username=${EXT_USER}" 2>/dev/null || echo "[]")
BROKERED_USER_ID=$(echo "$BROKERED_USERS" | jq -r '.[0].id // empty' 2>/dev/null || echo "")

if [ -n "$BROKERED_USER_ID" ]; then
  echo -e "  ${GREEN}Brokered user found in data-agent (id: ${BROKERED_USER_ID})${NC}"

  # Verify user details
  BROKERED_EMAIL=$(echo "$BROKERED_USERS" | jq -r '.[0].email // empty' 2>/dev/null || echo "")
  BROKERED_ENABLED=$(echo "$BROKERED_USERS" | jq -r '.[0].enabled // empty' 2>/dev/null || echo "")
  assert "Brokered user email" "${EXT_USER_EMAIL}" "$BROKERED_EMAIL"
  assert "Brokered user is enabled" "true" "$BROKERED_ENABLED"

  # Check groups
  USER_GROUPS=$(kc_admin "GET" "/realms/${TARGET_REALM}/users/${BROKERED_USER_ID}/groups" 2>/dev/null || echo "[]")
  USER_GROUP_NAMES=$(echo "$USER_GROUPS" | jq -r '.[].name' 2>/dev/null || echo "")
  echo -e "  ${CYAN}Brokered user groups: ${USER_GROUP_NAMES}${NC}"

  if echo "$USER_GROUP_NAMES" | grep -q "all-users"; then
    assert "Brokered user in all-users group (default group)" "true" "true"
  else
    echo -e "  ${YELLOW}NOTE${NC} Brokered user not yet in all-users group."
    echo -e "  ${CYAN}This is expected if first-broker-login flow has not completed (requires browser).${NC}"
    assert "Brokered user in all-users group" "true" "false"
  fi

  # Check federated identity links
  FED_LINKS=$(kc_admin "GET" "/realms/${TARGET_REALM}/users/${BROKERED_USER_ID}/federated-identity" 2>/dev/null || echo "[]")
  FED_IDP=$(echo "$FED_LINKS" | jq -r '.[0].identityProvider // empty' 2>/dev/null || echo "")
  if [ -n "$FED_IDP" ]; then
    assert "Federated identity linked to external-corp" "${IDP_ALIAS}" "$FED_IDP"
  else
    echo -e "  ${CYAN}No federated identity link found (expected if user was not created via brokering)${NC}"
  fi
else
  echo -e "  ${YELLOW}NOTE${NC} Brokered user not found in data-agent realm."
  echo -e "  ${CYAN}This is expected: OIDC brokering normally requires a browser-based flow.${NC}"
  echo -e "  ${CYAN}Token exchange may require Keycloak preview features to be enabled.${NC}"
  echo -e "  ${CYAN}The IdP configuration itself has been verified successfully above.${NC}"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Step 9: Test manual user linking (simulate what first-broker-login does)
# ══════════════════════════════════════════════════════════════════════════════
echo -e "\n${YELLOW}=== Step 9: Manual user linking (simulated first-broker-login) ===${NC}"

# If the brokered user was not created via token exchange, create manually
# and link to the external-corp IdP. This simulates what the browser-based
# first-broker-login flow would do.
if [ -z "$BROKERED_USER_ID" ]; then
  echo -e "  ${CYAN}Creating ext-user in data-agent manually...${NC}"

  MANUAL_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    "${KC_URL}/admin/realms/${TARGET_REALM}/users" \
    -H "Authorization: Bearer ${ADMIN_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{
      \"username\": \"${EXT_USER}\",
      \"email\": \"${EXT_USER_EMAIL}\",
      \"firstName\": \"${EXT_USER_FIRST}\",
      \"lastName\": \"${EXT_USER_LAST}\",
      \"enabled\": true,
      \"emailVerified\": true
    }" 2>/dev/null || echo "000")
  assert "Manually create ext-user in data-agent" "201" "$MANUAL_CODE"

  # Retrieve the user ID
  BROKERED_USERS=$(kc_admin "GET" "/realms/${TARGET_REALM}/users?username=${EXT_USER}&exact=true" 2>/dev/null || echo "[]")
  BROKERED_USER_ID=$(echo "$BROKERED_USERS" | jq -r '.[0].id // empty' 2>/dev/null || echo "")
  assert_not_empty "Manual ext-user ID retrieved" "$BROKERED_USER_ID"
fi

if [ -n "$BROKERED_USER_ID" ]; then
  # Link federated identity (simulates the IdP link that first-broker-login creates)
  LINK_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    "${KC_URL}/admin/realms/${TARGET_REALM}/users/${BROKERED_USER_ID}/federated-identity/${IDP_ALIAS}" \
    -H "Authorization: Bearer ${ADMIN_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{
      \"identityProvider\": \"${IDP_ALIAS}\",
      \"userId\": \"${EXT_USER_ID}\",
      \"userName\": \"${EXT_USER}\"
    }" 2>/dev/null || echo "000")

  if [ "$LINK_CODE" = "204" ] || [ "$LINK_CODE" = "200" ] || [ "$LINK_CODE" = "201" ]; then
    assert "Link federated identity to ext-user" "true" "true"
  elif [ "$LINK_CODE" = "409" ]; then
    echo -e "  ${CYAN}Federated identity link already exists${NC}"
    assert "Link federated identity (already exists)" "true" "true"
  else
    assert "Link federated identity to ext-user" "20x" "$LINK_CODE"
  fi

  # Verify the link
  FED_LINKS=$(kc_admin "GET" "/realms/${TARGET_REALM}/users/${BROKERED_USER_ID}/federated-identity" 2>/dev/null || echo "[]")
  FED_IDP=$(echo "$FED_LINKS" | jq -r '.[0].identityProvider // empty' 2>/dev/null || echo "")
  assert "Federated identity linked" "${IDP_ALIAS}" "$FED_IDP"

  # Verify user is in default group (all-users)
  USER_GROUPS=$(kc_admin "GET" "/realms/${TARGET_REALM}/users/${BROKERED_USER_ID}/groups" 2>/dev/null || echo "[]")
  USER_GROUP_NAMES=$(echo "$USER_GROUPS" | jq -r '.[].name' 2>/dev/null || echo "")
  echo -e "  ${CYAN}User groups: ${USER_GROUP_NAMES}${NC}"
  assert_contains "ext-user in all-users group" "all-users" "$USER_GROUP_NAMES"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Step 10: Verify end-to-end JWT claims for brokered user
# ══════════════════════════════════════════════════════════════════════════════
echo -e "\n${YELLOW}=== Step 10: Verify JWT claims for brokered user ===${NC}"

# The manually-created user will not have a password in data-agent realm
# (they should authenticate via the external IdP). But we can set one for
# testing purposes to verify the JWT contains correct claims.
if [ -n "$BROKERED_USER_ID" ]; then
  # Set a temporary password for direct testing
  TEMP_PWD="BrokeredTest@123"
  curl -s -o /dev/null -X PUT \
    "${KC_URL}/admin/realms/${TARGET_REALM}/users/${BROKERED_USER_ID}/reset-password" \
    -H "Authorization: Bearer ${ADMIN_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"type\": \"password\", \"value\": \"${TEMP_PWD}\", \"temporary\": false}" \
    2>/dev/null || true

  # Get the client ID for data-agent realm (public client, no secret needed)
  DA_TOKEN_RESP=$(curl -s -X POST \
    "${KC_URL}/realms/${TARGET_REALM}/protocol/openid-connect/token" \
    -d "grant_type=password" \
    -d "client_id=${DA_CLIENT_ID}" \
    ${DA_CLIENT_SECRET:+-d "client_secret=${DA_CLIENT_SECRET}"} \
    -d "username=${EXT_USER}" \
    -d "password=${TEMP_PWD}" 2>/dev/null || echo "{}")

  DA_ACCESS_TOKEN=$(echo "$DA_TOKEN_RESP" | jq -r '.access_token // empty' 2>/dev/null || echo "")

  if [ -n "$DA_ACCESS_TOKEN" ]; then
    assert "Brokered user can get data-agent token" "true" "true"

    JWT_PAYLOAD=$(decode_jwt_payload "$DA_ACCESS_TOKEN")
    JWT_ISS=$(echo "$JWT_PAYLOAD" | jq -r '.iss // empty' 2>/dev/null || echo "")
    JWT_EMAIL=$(echo "$JWT_PAYLOAD" | jq -r '.email // empty' 2>/dev/null || echo "")
    JWT_GROUPS=$(echo "$JWT_PAYLOAD" | jq -r '.groups // empty' 2>/dev/null || echo "")
    JWT_AZP=$(echo "$JWT_PAYLOAD" | jq -r '.azp // empty' 2>/dev/null || echo "")

    echo -e "  ${CYAN}JWT issuer:  ${JWT_ISS}${NC}"
    echo -e "  ${CYAN}JWT email:   ${JWT_EMAIL}${NC}"
    echo -e "  ${CYAN}JWT groups:  ${JWT_GROUPS}${NC}"
    echo -e "  ${CYAN}JWT azp:     ${JWT_AZP}${NC}"

    assert_contains "JWT issuer is data-agent" "${TARGET_REALM}" "$JWT_ISS"
    assert "JWT email matches ext-user" "${EXT_USER_EMAIL}" "$JWT_EMAIL"
    assert "JWT azp matches client" "${DA_CLIENT_ID}" "$JWT_AZP"

    if [ -n "$JWT_GROUPS" ] && [ "$JWT_GROUPS" != "null" ]; then
      assert_contains "JWT groups contains all-users" "all-users" "$JWT_GROUPS"
    else
      echo -e "  ${YELLOW}NOTE${NC} groups claim not present in JWT (check groups-mapper config on client)"
    fi
  else
    DA_TOKEN_ERR=$(echo "$DA_TOKEN_RESP" | jq -r '.error_description // .error // empty' 2>/dev/null || echo "")
    echo -e "  ${YELLOW}NOTE${NC} Could not get token for brokered user: ${DA_TOKEN_ERR}"
    echo -e "  ${CYAN}This may be expected if the client requires specific auth flows.${NC}"
    assert "Brokered user token" "non-empty" "empty"
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# Cleanup
# ══════════════════════════════════════════════════════════════════════════════
if [ "${SKIP_CLEANUP:-}" != "1" ]; then
  cleanup
else
  echo -e "\n${YELLOW}=== Cleanup skipped (SKIP_CLEANUP=1) ===${NC}"
  echo -e "  ${CYAN}To clean up manually: CLEANUP_ONLY=1 bash $0${NC}"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════════════
echo -e "\n${YELLOW}========================================${NC}"
echo -e "${CYAN}SR09: External IdP Federation Test${NC}"
echo -e "Test Results: ${GREEN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC}, ${TOTAL} total"
if [ "$FAIL" -gt 0 ]; then
  echo -e "${RED}SOME TESTS FAILED${NC}"
  exit 1
else
  echo -e "${GREEN}ALL TESTS PASSED${NC}"
fi
