#!/usr/bin/env bash
# ============================================================================
# test-apikey.sh - API Key lifecycle and auth trace tests.
#
# Default mode talks to the existing gateway / cluster:
#   BASE_URL=http://localhost:30085 ./test-apikey.sh
#
# Local mock mode starts an in-memory mock IAM + business backend:
#   ./test-apikey.sh --mock
#
# Output is grouped by test item. Each item prints preconditions, actions,
# response snippets, assertions, and a per-item result for trace retention.
# ============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

REALM="${REALM:-aidp}"
CLIENT_ID="${CLIENT_ID:-aidp-client}"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-Admin@123}"
IAM_NS="${IAM_NS:-aidp-iam}"
KEYCLOAK_NS="${KEYCLOAK_NS:-keycloak}"
GATEWAY_PORT="${GATEWAY_PORT:-30085}"
BASE_URL="${BASE_URL:-http://localhost:${GATEWAY_PORT}}"
BUSINESS_PATH="${BUSINESS_PATH:-/KnowledgeBase/Tenants/${REALM}/KnowledgeBases}"
DENIED_BUSINESS_PATH="${DENIED_BUSINESS_PATH:-/MemoryStore/Tenants/${REALM}/Instances}"
HTTP_TIMEOUT="${HTTP_TIMEOUT:-15}"
MOCK_PORT="${MOCK_PORT:-}"
MOCK_MODE=0

GREEN=$'\033[0;32m'
RED=$'\033[0;31m'
YELLOW=$'\033[1;33m'
BLUE=$'\033[0;34m'
CYAN=$'\033[0;36m'
BOLD=$'\033[1m'
NC=$'\033[0m'
if [ -n "${NO_COLOR:-}" ]; then
  GREEN=''; RED=''; YELLOW=''; BLUE=''; CYAN=''; BOLD=''; NC=''
fi

PASS=0
FAIL=0
TOTAL=0
CASE_TOTAL=0
CASE_PASS=0
CASE_FAIL=0
CURRENT_CASE_FAIL=0
RESP_CODE=""
RESP_BODY=""
ADMIN_TOKEN="${ADMIN_TOKEN:-}"
PYTHON_BIN=""
MOCK_PID=""
CREATED_KEY_IDS=()

usage() {
  cat <<EOF
Usage: $(basename "$0") [--mock] [--base-url URL] [--business-path PATH] [--denied-business-path PATH]

Options:
  --mock                    Start a local in-memory mock server and run all tests against it.
  --base-url URL            Gateway base URL. Default: $BASE_URL
  --business-path PATH      Business path expected to be allowed by allowed_paths. Default: $BUSINESS_PATH
  --denied-business-path P  Business path expected to be outside allowed_paths. Default: $DENIED_BUSINESS_PATH
  -h, --help                Show this help.

Environment:
  ADMIN_TOKEN               Use an existing admin bearer token instead of fetching one.
  REALM                     Tenant / realm. Default: aidp
  GATEWAY_PORT              Gateway port when BASE_URL is not set. Default: 30085
  MOCK_PORT                 Fixed mock port. By default an available local port is selected.
  NO_COLOR=1                Disable colored output.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --mock)
      MOCK_MODE=1
      shift
      ;;
    --base-url)
      BASE_URL="$2"
      shift 2
      ;;
    --business-path)
      BUSINESS_PATH="$2"
      shift 2
      ;;
    --denied-business-path)
      DENIED_BUSINESS_PATH="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

find_python() {
  if [ -n "$PYTHON_BIN" ]; then
    printf "%s" "$PYTHON_BIN"
    return
  fi
  local candidate
  if [ -n "${PYTHON:-}" ] && "$PYTHON" -c 'print("ok")' >/dev/null 2>&1; then
    PYTHON_BIN="$PYTHON"
    printf "%s" "$PYTHON_BIN"
    return
  fi
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'print("ok")' >/dev/null 2>&1; then
      PYTHON_BIN="$(command -v "$candidate")"
      printf "%s" "$PYTHON_BIN"
      return
    fi
  done
  local local_appdata=""
  local user_profile=""
  if command -v cygpath >/dev/null 2>&1; then
    [ -n "${LOCALAPPDATA:-}" ] && local_appdata="$(cygpath -u "$LOCALAPPDATA" 2>/dev/null || true)"
    [ -n "${USERPROFILE:-}" ] && user_profile="$(cygpath -u "$USERPROFILE" 2>/dev/null || true)"
  fi
  for candidate in \
    "${local_appdata}"/Programs/Python/Python*/python.exe \
    "${user_profile}"/AppData/Local/Programs/Python/Python*/python.exe \
    /c/Users/*/AppData/Local/Programs/Python/Python*/python.exe; do
    if [ -x "$candidate" ] && "$candidate" -c 'print("ok")' >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      printf "%s" "$PYTHON_BIN"
      return
    fi
  done
  echo "FATAL: python3/python is required" >&2
  exit 2
}

json_get() {
  local path="$1"
  "$(find_python)" -c '
import json
import sys

path = sys.argv[1]
try:
    obj = json.load(sys.stdin)
    for key in path.split("."):
        if isinstance(obj, list):
            obj = obj[int(key)]
        else:
            obj = obj[key]
    if obj is None:
        print("")
    elif isinstance(obj, (dict, list)):
        print(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
    else:
        print(obj)
except Exception:
    print("")
' "$path"
}

redact_text() {
  printf "%s" "$1" | sed -E 's/ak_[A-Za-z0-9_-]{16,}/ak_<redacted>/g'
}

compact() {
  printf "%s" "$1" \
    | tr '\r\n' '  ' \
    | sed 's/[[:space:]][[:space:]]*/ /g' \
    | sed -E 's/ak_[A-Za-z0-9_-]{16,}/ak_<redacted>/g' \
    | cut -c1-500
}

line() {
  printf "%s\n" "$*"
}

banner() {
  line ""
  line "${BLUE}${BOLD}======================================================================${NC}"
  line "${BLUE}${BOLD}$*${NC}"
  line "${BLUE}${BOLD}======================================================================${NC}"
}

case_begin() {
  CASE_TOTAL=$((CASE_TOTAL + 1))
  CURRENT_CASE_FAIL=0
  line ""
  line "${CYAN}${BOLD}[$1] $2${NC}"
}

case_note() {
  line "  ${BOLD}$1${NC} $2"
}

case_end() {
  if [ "$CURRENT_CASE_FAIL" -eq 0 ]; then
    CASE_PASS=$((CASE_PASS + 1))
    line "  ${GREEN}${BOLD}RESULT PASS${NC} $1"
  else
    CASE_FAIL=$((CASE_FAIL + 1))
    line "  ${RED}${BOLD}RESULT FAIL${NC} $1 (${CURRENT_CASE_FAIL} failed assertion(s))"
  fi
}

ok() {
  TOTAL=$((TOTAL + 1))
  PASS=$((PASS + 1))
  line "    ${GREEN}PASS${NC} $1"
}

fail() {
  TOTAL=$((TOTAL + 1))
  FAIL=$((FAIL + 1))
  CURRENT_CASE_FAIL=$((CURRENT_CASE_FAIL + 1))
  line "    ${RED}FAIL${NC} $1${2:+ - $2}"
}

assert_eq() {
  local desc="$1" want="$2" got="$3"
  if [ "$want" = "$got" ]; then
    ok "$desc (actual=$got)"
  else
    fail "$desc" "expected=$want actual=$got"
  fi
}

assert_nonempty() {
  local desc="$1" got="$2"
  if [ -n "$got" ]; then
    ok "$desc (actual=$(compact "$got"))"
  else
    fail "$desc" "value is empty"
  fi
}

assert_match() {
  local desc="$1" pattern="$2" got="$3"
  if printf "%s" "$got" | grep -qE "$pattern"; then
    ok "$desc (matches /$pattern/)"
  else
    fail "$desc" "expected match /$pattern/, actual=$(compact "$got")"
  fi
}

assert_contains() {
  local desc="$1" needle="$2" body="$3"
  if printf "%s" "$body" | grep -qF "$needle"; then
    ok "$desc (contains '$needle')"
  else
    fail "$desc" "missing '$needle' in $(compact "$body")"
  fi
}

assert_not_contains() {
  local desc="$1" needle="$2" body="$3"
  if printf "%s" "$body" | grep -qF "$needle"; then
    fail "$desc" "unexpected '$(redact_text "$needle")' in $(compact "$body")"
  else
    ok "$desc (does not contain '$(redact_text "$needle")')"
  fi
}

assert_code_in() {
  local desc="$1" got="$2"
  shift 2
  local want
  for want in "$@"; do
    if [ "$got" = "$want" ]; then
      ok "$desc (http=$got)"
      return
    fi
  done
  fail "$desc" "expected one of [$*], actual=$got"
}

shell_quote() {
  local value="$1"
  printf "'%s'" "$(printf "%s" "$value" | sed "s/'/'\\\\''/g")"
}

emit_curl_command() {
  local method="$1" path="$2" body="$3" auth_kind="$4" key_ref="${5:-}" key_literal="${6:-}"
  case_note "运行命令:" ""
  line "    curl -sS --max-time ${HTTP_TIMEOUT} -X ${method} \\"
  case "$auth_kind" in
    admin)
      line '      -H "Authorization: Bearer ${ADMIN_TOKEN}" \'
      ;;
    apikey)
      if [ -n "$key_ref" ]; then
        line "      -H \"X-API-Key: \${${key_ref}}\" \\"
      else
        line "      -H $(shell_quote "X-API-Key: $(redact_text "$key_literal")") \\"
      fi
      ;;
  esac
  if [ -n "$body" ]; then
    line "      -H 'Content-Type: application/json' \\"
    line "      -d $(shell_quote "$body") \\"
  fi
  line "      \"\${BASE_URL}${path}\""
}

emit_db_command() {
  local key_id="$1" sql="$2"
  case_note "DB查询命令:" ""
  if [ "$MOCK_MODE" -eq 1 ]; then
    line "    curl -sS --max-time ${HTTP_TIMEOUT} -X GET \\"
    line "      \"\${BASE_URL}/__mock__/db/api-keys/${key_id}\""
  else
    line "    kubectl -n ${KEYCLOAK_NS} exec iam-store-0 -c postgres -- \\"
    line "      psql -U keycloak -d iam -tA -c $(shell_quote "$sql")"
  fi
}

request() {
  local method="$1" url="$2" body="$3"
  shift 3
  local tmp err code
  tmp="$(mktemp)"
  err="$(mktemp)"
  local args=(-sS --max-time "$HTTP_TIMEOUT" -o "$tmp" -w "%{http_code}" -X "$method")
  local header
  for header in "$@"; do
    [ -n "$header" ] && args+=(-H "$header")
  done
  if [ -n "$body" ]; then
    args+=(-H "Content-Type: application/json" -d "$body")
  fi
  if ! code="$(curl "${args[@]}" "$url" 2>"$err")"; then
    code="000"
  fi
  RESP_CODE="$code"
  RESP_BODY="$(tr -d '\r' < "$tmp")"
  if [ "$RESP_CODE" = "000" ] && [ -s "$err" ]; then
    RESP_BODY="$(compact "$(cat "$err")")"
  fi
  rm -f "$tmp" "$err"
}

admin_request() {
  local method="$1" path="$2" body="${3:-}"
  emit_curl_command "$method" "$path" "$body" "admin"
  request "$method" "${BASE_URL}${path}" "$body" "Authorization: Bearer ${ADMIN_TOKEN}"
  case_note "HTTP" "$method $path -> $RESP_CODE"
  [ -n "$RESP_BODY" ] && case_note "响应摘录:" "$(compact "$RESP_BODY")"
}

business_request() {
  local method="$1" path="$2" api_key="$3" body="${4:-}"
  local key_ref="${5-API_KEY}"
  emit_curl_command "$method" "$path" "$body" "apikey" "$key_ref" "$api_key"
  request "$method" "${BASE_URL}${path}" "$body" "X-API-Key: ${api_key}"
  case_note "HTTP" "$method $path (X-API-Key=${api_key:0:8}...) -> $RESP_CODE"
  [ -n "$RESP_BODY" ] && case_note "响应摘录:" "$(compact "$RESP_BODY")"
}

remember_key() {
  local id="$1"
  [ -n "$id" ] && CREATED_KEY_IDS+=("$id")
}

silent_delete_key() {
  local id="$1"
  [ -z "$id" ] && return
  curl -sS --max-time 5 -o /dev/null -X DELETE \
    -H "Authorization: Bearer ${ADMIN_TOKEN}" \
    "${BASE_URL}/AccessManager/Tenants/${REALM}/ApiKeys/${id}" >/dev/null 2>&1 || true
}

cleanup() {
  local id
  for id in "${CREATED_KEY_IDS[@]:-}"; do
    silent_delete_key "$id"
  done
  if [ -n "${MOCK_PID:-}" ]; then
    kill "$MOCK_PID" >/dev/null 2>&1 || true
    wait "$MOCK_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

start_mock_server() {
  local py
  py="$(find_python)"
  if [ -z "$MOCK_PORT" ]; then
    MOCK_PORT="$("$py" - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)"
  fi
  BASE_URL="http://127.0.0.1:${MOCK_PORT}"
  "$py" - "$MOCK_PORT" "$REALM" <<'PY' &
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import hashlib
import json
import secrets
import sys
import uuid

PORT = int(sys.argv[1])
REALM = sys.argv[2]
keys = {}

def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None

def public_record(rec, include_plain=None):
    out = {
        "id": rec["id"],
        "key_prefix": rec["key_prefix"],
        "tenant_id": rec["tenant_id"],
        "app_name": rec["app_name"],
        "description": rec.get("description"),
        "subject_id": rec["subject_id"],
        "subject_type": rec["subject_type"],
        "allowed_paths": rec.get("allowed_paths"),
        "rate_limit": rec.get("rate_limit", 100),
        "expires_at": rec.get("expires_at"),
        "enabled": rec["enabled"],
        "created_by": rec["created_by"],
        "created_at": rec["created_at"],
        "updated_at": rec["updated_at"],
        "last_used_at": rec.get("last_used_at"),
    }
    if include_plain is not None:
        out["api_key"] = include_plain
    return out

def generate_key():
    return "ak_" + secrets.token_hex(32)

def key_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()

def is_allowed_path(allowed_paths, request_path):
    if not allowed_paths:
        return True
    for allowed in allowed_paths:
        if request_path == allowed or request_path.startswith(allowed.rstrip("/") + "/"):
            return True
    return False

class Handler(BaseHTTPRequestHandler):
    server_version = "aidp-apikey-mock/1.0"

    def log_message(self, fmt, *args):
        return

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def send_json(self, code, payload):
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def send_empty(self, code):
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def require_admin(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return True
        self.send_json(401, {"detail": "missing admin bearer token"})
        return False

    def route_parts(self):
        return [p for p in urlparse(self.path).path.split("/") if p]

    def do_GET(self):
        path = urlparse(self.path).path
        parts = self.route_parts()
        if path == "/__mock__/health":
            self.send_json(200, {"status": "ok"})
            return
        if len(parts) == 4 and parts[:3] == ["__mock__", "db", "api-keys"]:
            rec = keys.get(parts[3])
            if not rec:
                self.send_json(404, {"detail": "not found"})
            else:
                self.send_json(200, {
                    "api_key_hash": rec["api_key_hash"],
                    "enabled": rec["enabled"],
                    "count": 1,
                    "last_used_at": rec.get("last_used_at"),
                })
            return
        if len(parts) >= 4 and parts[:3] == ["AccessManager", "Tenants", REALM] and parts[3] == "ApiKeys":
            if not self.require_admin():
                return
            if len(parts) == 4:
                rows = [public_record(r) for r in sorted(keys.values(), key=lambda x: x["created_at"], reverse=True)]
                self.send_json(200, rows)
                return
            if len(parts) == 5:
                rec = keys.get(parts[4])
                if not rec:
                    self.send_json(404, {"detail": "API key not found"})
                else:
                    self.send_json(200, public_record(rec))
                return
        self.handle_business(path)

    def do_POST(self):
        path = urlparse(self.path).path
        parts = self.route_parts()
        if parts == ["realms", REALM, "protocol", "openid-connect", "token"]:
            self.send_json(200, {"access_token": "mock-admin-token", "token_type": "Bearer"})
            return
        if len(parts) >= 4 and parts[:3] == ["AccessManager", "Tenants", REALM] and parts[3] == "ApiKeys":
            if not self.require_admin():
                return
            if len(parts) == 4:
                payload = self.read_json()
                plain = generate_key()
                key_id = str(uuid.uuid4())
                subject = f"{payload.get('app_name', 'app')}-svc-{uuid.uuid4().hex[:8]}"
                rec = {
                    "id": key_id,
                    "api_key_hash": key_hash(plain),
                    "key_prefix": plain[:8],
                    "tenant_id": REALM,
                    "app_name": payload.get("app_name", "KnowledgeBase"),
                    "description": payload.get("description"),
                    "subject_id": subject,
                    "subject_type": "service",
                    "allowed_paths": payload.get("allowed_paths"),
                    "rate_limit": payload.get("rate_limit", 100),
                    "expires_at": payload.get("expires_at"),
                    "enabled": True,
                    "created_by": subject,
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                    "last_used_at": None,
                }
                keys[key_id] = rec
                self.send_json(201, public_record(rec, include_plain=plain))
                return
            if len(parts) == 6 and parts[5] == "Rotate":
                rec = keys.get(parts[4])
                if not rec:
                    self.send_json(404, {"detail": "API key not found"})
                    return
                plain = generate_key()
                rec["api_key_hash"] = key_hash(plain)
                rec["key_prefix"] = plain[:8]
                rec["updated_at"] = now_iso()
                self.send_json(200, public_record(rec, include_plain=plain))
                return
        self.handle_business(path)

    def do_PUT(self):
        parts = self.route_parts()
        if len(parts) == 5 and parts[:3] == ["AccessManager", "Tenants", REALM] and parts[3] == "ApiKeys":
            if not self.require_admin():
                return
            rec = keys.get(parts[4])
            if not rec:
                self.send_json(404, {"detail": "API key not found"})
                return
            payload = self.read_json()
            for field in ("description", "enabled", "allowed_paths", "rate_limit", "expires_at"):
                if field in payload:
                    rec[field] = payload[field]
            rec["updated_at"] = now_iso()
            self.send_json(200, public_record(rec))
            return
        self.send_json(404, {"detail": "not found"})

    def do_DELETE(self):
        parts = self.route_parts()
        if len(parts) == 5 and parts[:3] == ["AccessManager", "Tenants", REALM] and parts[3] == "ApiKeys":
            if not self.require_admin():
                return
            if parts[4] not in keys:
                self.send_json(404, {"detail": "API key not found"})
                return
            del keys[parts[4]]
            self.send_empty(204)
            return
        self.send_json(404, {"detail": "not found"})

    def handle_business(self, path):
        api_key = self.headers.get("X-API-Key", "")
        if not api_key:
            self.send_json(401, {"detail": "missing X-API-Key"})
            return
        digest = key_hash(api_key)
        rec = next((r for r in keys.values() if r["api_key_hash"] == digest), None)
        if not rec:
            self.send_json(401, {"detail": "invalid API key"})
            return
        if not rec.get("enabled", True):
            self.send_json(401, {"detail": "API key disabled"})
            return
        expires_at = parse_dt(rec.get("expires_at"))
        if expires_at and datetime.now(timezone.utc) >= expires_at:
            self.send_json(401, {"detail": "API key expired"})
            return
        if not is_allowed_path(rec.get("allowed_paths"), path):
            self.send_json(403, {"detail": "path_not_allowed", "path": path, "allowed_paths": rec.get("allowed_paths")})
            return
        rec["last_used_at"] = now_iso()
        rec["updated_at"] = rec["updated_at"]
        self.send_json(200, {
            "ok": True,
            "tenant_id": rec["tenant_id"],
            "subject_id": rec["subject_id"],
            "subject_type": rec["subject_type"],
            "app_name": rec["app_name"],
            "key_prefix": rec["key_prefix"],
            "path": path,
        })

ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
PY
  MOCK_PID=$!
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if curl -sS --max-time 1 "${BASE_URL}/__mock__/health" >/dev/null 2>&1; then
      return
    fi
    sleep 0.2
  done
  echo "FATAL: local mock server did not start on ${BASE_URL}" >&2
  exit 2
}

fetch_admin_token() {
  if [ -n "$ADMIN_TOKEN" ]; then
    return
  fi
  local secret=""
  if [ "$MOCK_MODE" -eq 0 ]; then
    if command -v kubectl >/dev/null 2>&1; then
      secret="$(kubectl -n "$IAM_NS" get secret keycloak-aidp-client -o jsonpath='{.data.client-secret}' 2>/dev/null | base64 -d 2>/dev/null || true)"
    fi
  fi
  local tmp err code
  tmp="$(mktemp)"
  err="$(mktemp)"
  if ! code="$(curl -sS --max-time "$HTTP_TIMEOUT" -o "$tmp" -w "%{http_code}" -X POST \
    "${BASE_URL}/realms/${REALM}/protocol/openid-connect/token" \
    -d "client_id=${CLIENT_ID}" \
    -d "client_secret=${secret}" \
    -d "grant_type=password" \
    -d "username=${ADMIN_USER}" \
    -d "password=${ADMIN_PASSWORD}" 2>"$err")"; then
    code="000"
  fi
  RESP_CODE="$code"
  RESP_BODY="$(tr -d '\r' < "$tmp")"
  rm -f "$tmp" "$err"
  ADMIN_TOKEN="$(printf "%s" "$RESP_BODY" | json_get access_token)"
  if [ -z "$ADMIN_TOKEN" ]; then
    echo "FATAL: cannot get admin token from ${BASE_URL} (http=${RESP_CODE}, body=$(compact "$RESP_BODY"))" >&2
    echo "       Set ADMIN_TOKEN=... or run with --mock for local mock validation." >&2
    exit 2
  fi
}

psql_iam() {
  MSYS_NO_PATHCONV=1 kubectl -n "$KEYCLOAK_NS" exec iam-store-0 -c postgres -- \
    psql -U keycloak -d iam -tA -c "$1" 2>/dev/null | tr -d '\r'
}

mock_db_request() {
  local key_id="$1"
  request "GET" "${BASE_URL}/__mock__/db/api-keys/${key_id}" "" ""
}

db_hash_for() {
  local key_id="$1"
  if [ "$MOCK_MODE" -eq 1 ]; then
    mock_db_request "$key_id"
    printf "%s" "$RESP_BODY" | json_get api_key_hash
  else
    psql_iam "SELECT api_key_hash FROM api_keys WHERE id='${key_id}';"
  fi
}

db_enabled_for() {
  local key_id="$1"
  if [ "$MOCK_MODE" -eq 1 ]; then
    mock_db_request "$key_id"
    printf "%s" "$RESP_BODY" | json_get enabled
  else
    psql_iam "SELECT enabled FROM api_keys WHERE id='${key_id}';"
  fi
}

db_count_for() {
  local key_id="$1"
  if [ "$MOCK_MODE" -eq 1 ]; then
    mock_db_request "$key_id"
    if [ "$RESP_CODE" = "404" ]; then printf "0"; else printf "%s" "$RESP_BODY" | json_get count; fi
  else
    psql_iam "SELECT COUNT(*) FROM api_keys WHERE id='${key_id}';"
  fi
}

db_last_used_at_for() {
  local key_id="$1"
  if [ "$MOCK_MODE" -eq 1 ]; then
    mock_db_request "$key_id"
    printf "%s" "$RESP_BODY" | json_get last_used_at
  else
    psql_iam "SELECT COALESCE(last_used_at::text,'') FROM api_keys WHERE id='${key_id}';"
  fi
}

create_api_key() {
  local desc="$1" allowed_paths_json="$2" expires_at="$3" rate_limit="${4:-100}"
  local body
  body="{\"app_name\":\"KnowledgeBase\",\"description\":\"${desc}\",\"allowed_paths\":${allowed_paths_json},\"rate_limit\":${rate_limit},\"expires_at\":\"${expires_at}\"}"
  admin_request "POST" "/AccessManager/Tenants/${REALM}/ApiKeys" "$body"
}

get_api_key_detail() {
  local key_id="$1"
  admin_request "GET" "/AccessManager/Tenants/${REALM}/ApiKeys/${key_id}" ""
}

list_api_keys() {
  admin_request "GET" "/AccessManager/Tenants/${REALM}/ApiKeys" ""
}

update_api_key() {
  local key_id="$1" body="$2"
  admin_request "PUT" "/AccessManager/Tenants/${REALM}/ApiKeys/${key_id}" "$body"
}

delete_api_key() {
  local key_id="$1"
  admin_request "DELETE" "/AccessManager/Tenants/${REALM}/ApiKeys/${key_id}" ""
}

rotate_api_key() {
  local key_id="$1"
  admin_request "POST" "/AccessManager/Tenants/${REALM}/ApiKeys/${key_id}/Rotate" ""
}

summary() {
  line ""
  line "${BLUE}${BOLD}============================== Summary ==============================${NC}"
  line "Mode:              $([ "$MOCK_MODE" -eq 1 ] && printf "local mock" || printf "gateway")"
  line "Base URL:          ${BASE_URL}"
  line "Allowed path:      ${BUSINESS_PATH}"
  line "Denied path:       ${DENIED_BUSINESS_PATH}"
  line "Test items:        ${CASE_PASS}/${CASE_TOTAL} passed, ${CASE_FAIL} failed"
  line "Assertions:        ${PASS}/${TOTAL} passed, ${FAIL} failed"
  if [ "$FAIL" -eq 0 ]; then
    line "${GREEN}${BOLD}ALL API KEY TESTS PASSED${NC}"
  else
    line "${RED}${BOLD}API KEY TESTS FAILED${NC}"
  fi
}

if [ "$MOCK_MODE" -eq 1 ]; then
  start_mock_server
fi

fetch_admin_token

banner "API Key 认证与管理专项测试"
line "脚本目录: ${SCRIPT_DIR}"
line "运行模式: $([ "$MOCK_MODE" -eq 1 ] && printf "本地 mock" || printf "真实网关/集群")"
line "BASE_URL: ${BASE_URL}"
line "业务允许路径: ${BUSINESS_PATH}"
line "业务拒绝路径: ${DENIED_BUSINESS_PATH}"

FUTURE_EXPIRES_AT="2099-12-31T23:59:59Z"
PAST_EXPIRES_AT="2000-01-01T00:00:00Z"

PRIMARY_ID=""
PRIMARY_KEY=""
PRIMARY_PREFIX=""
PRIMARY_SUBJECT=""
ROTATED_KEY=""
AUTH_ID=""
AUTH_KEY=""
AUTH_SUBJECT=""
AUTH_PREFIX=""
EXPIRED_ID=""
EXPIRED_KEY=""
SCOPE_ID=""
SCOPE_KEY=""

case_begin "AK-LC-001" "创建访问密钥后仅展示一次完整密钥"
case_note "前置条件:" "管理员 Bearer Token 已获取；目标租户 ${REALM} 可访问。"
case_note "操作:" "创建一把 KnowledgeBase API Key，并记录创建响应中的一次性明文。"
create_api_key "trace-create-once" "[\"/KnowledgeBase\"]" "$FUTURE_EXPIRES_AT" "100"
assert_code_in "创建接口返回成功" "$RESP_CODE" "200" "201"
PRIMARY_KEY="$(printf "%s" "$RESP_BODY" | json_get api_key)"
PRIMARY_ID="$(printf "%s" "$RESP_BODY" | json_get id)"
PRIMARY_PREFIX="$(printf "%s" "$RESP_BODY" | json_get key_prefix)"
PRIMARY_SUBJECT="$(printf "%s" "$RESP_BODY" | json_get subject_id)"
remember_key "$PRIMARY_ID"
assert_match "创建响应返回完整明文，格式为 ak_ + 随机串" '^ak_[A-Za-z0-9_-]{20,}$' "$PRIMARY_KEY"
assert_nonempty "创建响应返回 key id" "$PRIMARY_ID"
assert_eq "创建响应 key_prefix 等于明文前 8 位" "${PRIMARY_KEY:0:8}" "$PRIMARY_PREFIX"
emit_db_command "$PRIMARY_ID" "SELECT api_key_hash FROM api_keys WHERE id='${PRIMARY_ID}';"
DB_HASH="$(db_hash_for "$PRIMARY_ID")"
case_note "DB观察:" "api_key_hash=$(compact "$DB_HASH")"
assert_match "DB 只保存 SHA-256 hash" '^[a-f0-9]{64}$' "$DB_HASH"
assert_not_contains "DB hash 不包含完整明文" "$PRIMARY_KEY" "$DB_HASH"
case_end "创建访问密钥后仅展示一次完整密钥"

case_begin "AK-LC-002" "访问密钥列表和详情不泄露完整密钥"
case_note "前置条件:" "已创建 API Key：id=${PRIMARY_ID}，完整明文已记录用于泄露检查。"
case_note "操作:" "查询列表和详情，检查只展示前缀、状态、有效期等元数据。"
list_api_keys
LIST_BODY="$RESP_BODY"
assert_code_in "列表接口返回成功" "$RESP_CODE" "200"
assert_contains "列表包含 key_prefix" "$PRIMARY_PREFIX" "$LIST_BODY"
assert_contains "列表包含 enabled 字段" '"enabled"' "$LIST_BODY"
assert_contains "列表包含 expires_at 字段" '"expires_at"' "$LIST_BODY"
assert_not_contains "列表不返回完整明文字段 api_key" '"api_key"' "$LIST_BODY"
assert_not_contains "列表不返回 api_key_hash" '"api_key_hash"' "$LIST_BODY"
assert_not_contains "列表不包含完整明文值" "$PRIMARY_KEY" "$LIST_BODY"
get_api_key_detail "$PRIMARY_ID"
DETAIL_BODY="$RESP_BODY"
assert_code_in "详情接口返回成功" "$RESP_CODE" "200"
assert_contains "详情包含 key_prefix" "$PRIMARY_PREFIX" "$DETAIL_BODY"
assert_contains "详情包含 allowed_paths" '"allowed_paths"' "$DETAIL_BODY"
assert_contains "详情包含 last_used_at" '"last_used_at"' "$DETAIL_BODY"
assert_not_contains "详情不返回完整明文字段 api_key" '"api_key"' "$DETAIL_BODY"
assert_not_contains "详情不返回 api_key_hash" '"api_key_hash"' "$DETAIL_BODY"
assert_not_contains "详情不包含完整明文值" "$PRIMARY_KEY" "$DETAIL_BODY"
case_end "访问密钥列表和详情不泄露完整密钥"

case_begin "AK-LC-003" "禁用访问密钥立即生效"
case_note "前置条件:" "API Key 当前启用，并允许访问 ${BUSINESS_PATH}。"
case_note "操作:" "先访问业务路径确认可用，再将 enabled=false，继续用旧 Key 访问业务。"
business_request "GET" "$BUSINESS_PATH" "$PRIMARY_KEY" "" "PRIMARY_KEY"
assert_code_in "禁用前业务访问不是认证失败" "$RESP_CODE" "200" "403"
update_api_key "$PRIMARY_ID" '{"enabled":false}'
assert_code_in "禁用接口返回成功" "$RESP_CODE" "200"
assert_contains "禁用后响应 enabled=false" '"enabled":false' "$RESP_BODY"
emit_db_command "$PRIMARY_ID" "SELECT enabled FROM api_keys WHERE id='${PRIMARY_ID}';"
DB_ENABLED="$(db_enabled_for "$PRIMARY_ID")"
case_note "DB观察:" "enabled=${DB_ENABLED}"
assert_match "DB enabled 已变为 false" '^(f|false|False)$' "$DB_ENABLED"
business_request "GET" "$BUSINESS_PATH" "$PRIMARY_KEY" "" "PRIMARY_KEY"
assert_code_in "禁用后旧 Key 访问失败" "$RESP_CODE" "401" "403"
case_end "禁用访问密钥立即生效"

case_begin "AK-LC-004" "重新启用访问密钥后访问恢复"
case_note "前置条件:" "上一项已将 API Key 禁用。"
case_note "操作:" "将 enabled=true，再使用同一把 Key 访问业务路径。"
update_api_key "$PRIMARY_ID" '{"enabled":true}'
assert_code_in "启用接口返回成功" "$RESP_CODE" "200"
assert_contains "启用后响应 enabled=true" '"enabled":true' "$RESP_BODY"
emit_db_command "$PRIMARY_ID" "SELECT enabled FROM api_keys WHERE id='${PRIMARY_ID}';"
DB_ENABLED="$(db_enabled_for "$PRIMARY_ID")"
case_note "DB观察:" "enabled=${DB_ENABLED}"
assert_match "DB enabled 已恢复为 true" '^(t|true|True)$' "$DB_ENABLED"
business_request "GET" "$BUSINESS_PATH" "$PRIMARY_KEY" "" "PRIMARY_KEY"
assert_code_in "重新启用后不再因 Key 认证失败" "$RESP_CODE" "200" "403"
case_end "重新启用访问密钥后访问恢复"

case_begin "AK-LC-005" "轮换访问密钥后旧密钥失效"
case_note "前置条件:" "API Key 已启用，subject_id=${PRIMARY_SUBJECT}。"
case_note "操作:" "调用 Rotate，验证新明文只在轮换响应出现；旧 Key 失败，新 Key 可用。"
rotate_api_key "$PRIMARY_ID"
ROTATE_BODY="$RESP_BODY"
assert_code_in "轮换接口返回成功" "$RESP_CODE" "200"
ROTATED_KEY="$(printf "%s" "$ROTATE_BODY" | json_get api_key)"
ROTATED_SUBJECT="$(printf "%s" "$ROTATE_BODY" | json_get subject_id)"
assert_match "轮换响应返回新完整明文" '^ak_[A-Za-z0-9_-]{20,}$' "$ROTATED_KEY"
if [ "$ROTATED_KEY" != "$PRIMARY_KEY" ]; then
  ok "新旧明文不同"
else
  fail "新旧明文不同" "rotated key equals old key"
fi
assert_eq "轮换保留 subject_id，授权主体不变" "$PRIMARY_SUBJECT" "$ROTATED_SUBJECT"
business_request "GET" "$BUSINESS_PATH" "$PRIMARY_KEY" "" "PRIMARY_KEY"
assert_code_in "轮换后旧 Key 访问失败" "$RESP_CODE" "401" "403"
business_request "GET" "$BUSINESS_PATH" "$ROTATED_KEY" "" "ROTATED_KEY"
assert_code_in "轮换后新 Key 可进入业务鉴权链路" "$RESP_CODE" "200" "403"
list_api_keys
assert_not_contains "轮换后列表不展示新完整明文" "$ROTATED_KEY" "$RESP_BODY"
case_end "轮换访问密钥后旧密钥失效"

case_begin "AK-LC-006" "删除访问密钥后访问失败"
case_note "前置条件:" "已完成轮换，新 Key 可用于后续删除验证。"
case_note "操作:" "删除 Key，确认详情不可查、列表不再出现、业务访问失败。"
delete_api_key "$PRIMARY_ID"
assert_code_in "删除接口返回成功" "$RESP_CODE" "200" "204"
get_api_key_detail "$PRIMARY_ID"
assert_code_in "删除后详情查询返回 404" "$RESP_CODE" "404"
list_api_keys
assert_not_contains "删除后列表不再出现 key id" "$PRIMARY_ID" "$RESP_BODY"
business_request "GET" "$BUSINESS_PATH" "$ROTATED_KEY" "" "ROTATED_KEY"
assert_code_in "删除后新 Key 也访问失败" "$RESP_CODE" "401" "403"
emit_db_command "$PRIMARY_ID" "SELECT COUNT(*) FROM api_keys WHERE id='${PRIMARY_ID}';"
DB_COUNT="$(db_count_for "$PRIMARY_ID")"
case_note "DB观察:" "row_count=${DB_COUNT}"
assert_eq "DB 记录已删除" "0" "$DB_COUNT"
PRIMARY_ID=""
case_end "删除访问密钥后访问失败"

case_begin "AK-LC-007" "过期访问密钥访问失败"
case_note "前置条件:" "创建 expires_at=${PAST_EXPIRES_AT} 的已过期 Key。"
case_note "操作:" "使用已过期 Key 访问业务路径。"
create_api_key "trace-expired" "[\"/KnowledgeBase\"]" "$PAST_EXPIRES_AT" "100"
assert_code_in "过期 Key 创建接口返回成功" "$RESP_CODE" "200" "201"
EXPIRED_KEY="$(printf "%s" "$RESP_BODY" | json_get api_key)"
EXPIRED_ID="$(printf "%s" "$RESP_BODY" | json_get id)"
remember_key "$EXPIRED_ID"
assert_nonempty "过期 Key id 已记录" "$EXPIRED_ID"
business_request "GET" "$BUSINESS_PATH" "$EXPIRED_KEY" "" "EXPIRED_KEY"
assert_code_in "过期 Key 访问失败" "$RESP_CODE" "401" "403"
get_api_key_detail "$EXPIRED_ID"
assert_contains "详情保留过期时间用于排查" "$PAST_EXPIRES_AT" "$RESP_BODY"
case_end "过期访问密钥访问失败"

case_begin "AK-AUTH-001" "有效访问密钥可访问业务接口"
case_note "前置条件:" "创建 enabled=true 且 allowed_paths 覆盖 ${BUSINESS_PATH} 的 Key。"
case_note "操作:" "外部调用方携带 X-API-Key 访问业务接口。"
create_api_key "trace-valid-auth" "[\"/KnowledgeBase\"]" "$FUTURE_EXPIRES_AT" "100"
assert_code_in "有效 Key 创建接口返回成功" "$RESP_CODE" "200" "201"
AUTH_KEY="$(printf "%s" "$RESP_BODY" | json_get api_key)"
AUTH_ID="$(printf "%s" "$RESP_BODY" | json_get id)"
AUTH_SUBJECT="$(printf "%s" "$RESP_BODY" | json_get subject_id)"
AUTH_PREFIX="$(printf "%s" "$RESP_BODY" | json_get key_prefix)"
remember_key "$AUTH_ID"
assert_nonempty "有效 Key id 已记录" "$AUTH_ID"
business_request "GET" "$BUSINESS_PATH" "$AUTH_KEY" "" "AUTH_KEY"
AUTH_BUSINESS_BODY="$RESP_BODY"
assert_code_in "有效 Key 可进入业务鉴权链路" "$RESP_CODE" "200" "403"
if [ "$RESP_CODE" = "200" ]; then
  assert_contains "业务响应包含服务主体 subject_id" "$AUTH_SUBJECT" "$AUTH_BUSINESS_BODY"
  assert_contains "业务响应包含 key_prefix，便于审计定位" "$AUTH_PREFIX" "$AUTH_BUSINESS_BODY"
else
  ok "业务返回 403 时表示 Key 认证已通过但资源级鉴权未放行，未返回 401"
fi
case_end "有效访问密钥可访问业务接口"

case_begin "AK-AUTH-002" "无效访问密钥访问失败"
case_note "前置条件:" "业务接口需要认证访问。"
case_note "操作:" "外部调用方携带伪造的 X-API-Key 访问业务接口。"
business_request "GET" "$BUSINESS_PATH" "ak_invalid_xxx" "" ""
assert_code_in "无效 Key 被拒绝" "$RESP_CODE" "401" "403"
case_end "无效访问密钥访问失败"

case_begin "AK-AUTH-003" "访问密钥路径白名单外访问失败"
case_note "前置条件:" "Key 的 allowed_paths=[\"/KnowledgeBase\"]。"
case_note "操作:" "使用该 Key 访问白名单外路径 ${DENIED_BUSINESS_PATH}。"
create_api_key "trace-scope-deny" "[\"/KnowledgeBase\"]" "$FUTURE_EXPIRES_AT" "100"
assert_code_in "路径白名单测试 Key 创建成功" "$RESP_CODE" "200" "201"
SCOPE_KEY="$(printf "%s" "$RESP_BODY" | json_get api_key)"
SCOPE_ID="$(printf "%s" "$RESP_BODY" | json_get id)"
remember_key "$SCOPE_ID"
business_request "GET" "$DENIED_BUSINESS_PATH" "$SCOPE_KEY" "" "SCOPE_KEY"
assert_code_in "白名单外路径访问失败" "$RESP_CODE" "403" "401"
case_end "访问密钥路径白名单外访问失败"

case_begin "AK-AUTH-004" "访问密钥使用后记录 last_used_at"
case_note "前置条件:" "有效 Key 已至少访问过一次业务接口：id=${AUTH_ID}。"
case_note "操作:" "再次访问业务接口后查询详情/DB，确认最近使用时间可追踪。"
emit_db_command "$AUTH_ID" "SELECT COALESCE(last_used_at::text,'') FROM api_keys WHERE id='${AUTH_ID}';"
BEFORE_LAST_USED="$(db_last_used_at_for "$AUTH_ID")"
case_note "访问前观察:" "last_used_at=$(compact "$BEFORE_LAST_USED")"
business_request "GET" "$BUSINESS_PATH" "$AUTH_KEY" "" "AUTH_KEY"
assert_code_in "用于审计验证的业务访问不返回认证失败" "$RESP_CODE" "200" "403"
emit_db_command "$AUTH_ID" "SELECT COALESCE(last_used_at::text,'') FROM api_keys WHERE id='${AUTH_ID}';"
AFTER_LAST_USED="$(db_last_used_at_for "$AUTH_ID")"
case_note "访问后观察:" "last_used_at=$(compact "$AFTER_LAST_USED")"
assert_nonempty "last_used_at 已记录" "$AFTER_LAST_USED"
get_api_key_detail "$AUTH_ID"
assert_contains "详情接口展示 key_prefix" "$AUTH_PREFIX" "$RESP_BODY"
assert_contains "详情接口展示 last_used_at 字段" '"last_used_at"' "$RESP_BODY"
case_end "访问密钥使用后记录 last_used_at"

summary
[ "$FAIL" -eq 0 ]
