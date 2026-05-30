#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-https://localhost:30080}"
REALM="${REALM:-aidp}"
CLIENT_ID="${CLIENT_ID:-cas-test}"
SERVICE_URL="${SERVICE_URL:-http://localhost:18080/cas/callback}"
USERNAME="${USERNAME:-normal-user}"
PASSWORD="${PASSWORD:-NormalUser@123}"
ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin}"
SKIP_CLIENT_SETUP="${SKIP_CLIENT_SETUP:-false}"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing required command: $1" >&2
    exit 1
  }
}

join_url() {
  local left="${1%/}"
  local right="${2#/}"
  printf '%s/%s' "$left" "$right"
}

urlencode() {
  python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"
}

json_get_access_token() {
  python3 -c 'import json, sys; print(json.load(sys.stdin)["access_token"])'
}

json_first_client_id() {
  python3 -c 'import json, sys; data=json.load(sys.stdin); print(data[0]["id"] if data else "")'
}

client_payload() {
  CLIENT_ID="$CLIENT_ID" SERVICE_URL="$SERVICE_URL" python3 - <<'PY'
import json
import os

print(json.dumps({
    "clientId": os.environ["CLIENT_ID"],
    "protocol": "cas",
    "enabled": True,
    "publicClient": True,
    "redirectUris": [os.environ["SERVICE_URL"]],
}))
PY
}

extract_form_action() {
  python3 - "$1" <<'PY'
from html import unescape
from html.parser import HTMLParser
import sys

class LoginFormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = None

    def handle_starttag(self, tag, attrs):
        if tag != "form" or self.action:
            return
        values = dict(attrs)
        action = values.get("action")
        if action:
            self.action = unescape(action)

parser = LoginFormParser()
with open(sys.argv[1], encoding="utf-8") as f:
    parser.feed(f.read())

if not parser.action:
    raise SystemExit("could not find login form action")

print(parser.action)
PY
}

extract_location() {
  python3 - "$1" <<'PY'
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    for line in f:
        if line.lower().startswith("location:"):
            print(line.split(":", 1)[1].strip())
            break
PY
}

extract_ticket() {
  python3 -c 'import sys, urllib.parse; u=urllib.parse.urlparse(sys.argv[1]); print(urllib.parse.parse_qs(u.query).get("ticket", [""])[0])' "$1"
}

ensure_cas_client() {
  local token_endpoint token clients_url clients client_id payload

  token_endpoint="$(join_url "$BASE_URL" "realms/master/protocol/openid-connect/token")"
  token="$(
    curl -sS -f -X POST "$token_endpoint" \
      -H 'Content-Type: application/x-www-form-urlencoded' \
      --data-urlencode 'client_id=admin-cli' \
      --data-urlencode "username=$ADMIN_USERNAME" \
      --data-urlencode "password=$ADMIN_PASSWORD" \
      --data-urlencode 'grant_type=password' |
      json_get_access_token
  )"

  clients_url="$(join_url "$BASE_URL" "admin/realms/$REALM/clients")"
  clients="$(
    curl -sS -f "$clients_url?clientId=$(urlencode "$CLIENT_ID")" \
      -H "Authorization: Bearer $token"
  )"
  client_id="$(printf '%s' "$clients" | json_first_client_id)"
  payload="$(client_payload)"

  if [[ -n "$client_id" ]]; then
    curl -sS -f -X PUT "$clients_url/$client_id" \
      -H "Authorization: Bearer $token" \
      -H 'Content-Type: application/json' \
      -d "$payload" >/dev/null
    echo "Updated CAS client: $CLIENT_ID"
  else
    curl -sS -f -X POST "$clients_url" \
      -H "Authorization: Bearer $token" \
      -H 'Content-Type: application/json' \
      -d "$payload" >/dev/null
    echo "Created CAS client: $CLIENT_ID"
  fi
}

need curl
need python3
need awk

if [[ "$SKIP_CLIENT_SETUP" != "true" ]]; then
  ensure_cas_client
fi

work_dir="$(mktemp -d)"
cookie_jar="$work_dir/cookies.txt"
login_html="$work_dir/login.html"
headers_file="$work_dir/headers.txt"
body_file="$work_dir/body.html"

login_url="$(join_url "$BASE_URL" "realms/$REALM/protocol/cas/login")?service=$(urlencode "$SERVICE_URL")"

curl -sS -L -c "$cookie_jar" -b "$cookie_jar" -o "$login_html" "$login_url"

# Local kind uses plain HTTP. Keycloak sets Secure cookies for browser flows, so
# the cookie jar must be loosened for this local-only smoke test. Production
# should use HTTPS and does not need this workaround.
if [[ "$BASE_URL" == http://* ]]; then
  python3 - "$cookie_jar" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
path.write_text(path.read_text().replace("\tTRUE\t", "\tFALSE\t"))
PY
fi

login_action="$(extract_form_action "$login_html")"

curl -sS -i -c "$cookie_jar" -b "$cookie_jar" \
  -o "$body_file" -D "$headers_file" \
  -X POST "$login_action" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=$USERNAME" \
  --data-urlencode "password=$PASSWORD" \
  --data-urlencode 'credentialId=' \
  --max-redirs 0 >/dev/null

redirect_location="$(extract_location "$headers_file")"
if [[ -z "$redirect_location" ]]; then
  echo "Login did not return a CAS service redirect. Headers:" >&2
  cat "$headers_file" >&2
  exit 1
fi

ticket="$(extract_ticket "$redirect_location")"
if [[ -z "$ticket" ]]; then
  echo "Service redirect did not include a CAS ticket: $redirect_location" >&2
  exit 1
fi

validate_url="$(join_url "$BASE_URL" "realms/$REALM/protocol/cas/serviceValidate")?service=$(urlencode "$SERVICE_URL")&ticket=$(urlencode "$ticket")"
validation="$(curl -sS -f "$validate_url")"

echo "CAS redirect: $redirect_location"
echo "CAS ticket: $ticket"
echo "CAS validation status: 200"
echo "$validation"

if [[ "$validation" != *"<cas:authenticationSuccess>"* ]]; then
  echo "CAS serviceValidate did not return authenticationSuccess." >&2
  exit 1
fi
