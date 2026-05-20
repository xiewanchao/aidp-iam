#!/usr/bin/env bash
# ============================================================================
# Gateway route black-box tests
#
# Covers:
#   GW-TC-001 Publish a route and forward to backend
#   GW-TC-002 Existing business path returns real backend response stably
#   GW-TC-003 Unavailable backend is externally observable
#   GW-TC-004 Deleted business route no longer forwards
#   GW-TC-005 Cross-namespace backend forwards after ReferenceGrant
#   GW-TC-006 Missing cross-namespace grant does not forward
#   GW-TC-007 Deleting cross-namespace grant disables existing route
#   GW-TC-008 Restoring cross-namespace grant recovers existing route
#
# Modes:
#   --mock  Start a local mock gateway and run the same assertions locally.
#   --k8s   Create temporary Gateway API resources against the current cluster.
# ============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MODE="${GATEWAY_TEST_MODE:-k8s}"
BASE_URL="${BASE_URL:-}"
GATEWAY_PORT="${GATEWAY_PORT:-30080}"
GATEWAY_NAMESPACE="${GATEWAY_NAMESPACE:-${ENVOY_GATEWAY_NS:-aidp-gateway}}"
GATEWAY_NAME="${GATEWAY_NAME:-eg}"
BACKEND_IMAGE="${GATEWAY_TEST_BACKEND_IMAGE:-python:3.11-alpine}"
MOCK_PORT="${MOCK_PORT:-18080}"
HTTP_TIMEOUT="${HTTP_TIMEOUT:-8}"
HTTP_CONNECT_TIMEOUT="${HTTP_CONNECT_TIMEOUT:-2}"
ROUTE_WAIT_SECONDS="${ROUTE_WAIT_SECONDS:-}"
KEEP_RESOURCES="${KEEP_RESOURCES:-0}"
NO_TEE_LOG="${NO_TEE_LOG:-0}"
RUN_ID="${RUN_ID:-gwtest-$(date +%Y%m%d%H%M%S)}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--mock|--k8s] [options]

Options:
  --mock                 Run against an in-process local mock gateway.
  --k8s                  Run against a real Kubernetes Gateway API cluster.
  --base-url URL         Gateway base URL. Default: mock http://127.0.0.1:18080,
                         k8s http://localhost:\$GATEWAY_PORT.
  --wait-seconds N       Route propagation wait. Default: mock 0, k8s 5.
  --keep                 Keep temporary Kubernetes resources after the run.
  --no-log               Do not tee output into da-cluster/test-output.
  -h, --help             Show this help.

Environment:
  GATEWAY_NAMESPACE      Gateway namespace, default aidp-gateway.
  GATEWAY_NAME           Gateway name, default eg.
  GATEWAY_PORT           Port-forward/local gateway port, default 30080.
  GATEWAY_TEST_BACKEND_IMAGE
                         Backend image for k8s mode, default python:3.11-alpine.
  LOG_FILE               Output log path. Default da-cluster/test-output/...
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --mock)
      MODE="mock"
      ;;
    --k8s)
      MODE="k8s"
      ;;
    --base-url)
      shift
      BASE_URL="${1:-}"
      ;;
    --wait-seconds)
      shift
      ROUTE_WAIT_SECONDS="${1:-}"
      ;;
    --keep)
      KEEP_RESOURCES="1"
      ;;
    --no-log)
      NO_TEE_LOG="1"
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
  shift
done

case "$MODE" in
  mock|k8s) ;;
  *)
    echo "Invalid mode: $MODE" >&2
    exit 2
    ;;
esac

if [ -z "$BASE_URL" ]; then
  if [ "$MODE" = "mock" ]; then
    BASE_URL="http://127.0.0.1:${MOCK_PORT}"
  else
    BASE_URL="http://localhost:${GATEWAY_PORT}"
  fi
fi
BASE_URL="${BASE_URL%/}"

if [ -z "$ROUTE_WAIT_SECONDS" ]; then
  if [ "$MODE" = "mock" ]; then
    ROUTE_WAIT_SECONDS="0"
  else
    ROUTE_WAIT_SECONDS="5"
  fi
fi

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
LOG_DIR="${LOG_DIR:-$SCRIPT_DIR/../test-output}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/test-gateway-${TIMESTAMP}.log}"
if [ "$NO_TEE_LOG" != "1" ]; then
  mkdir -p "$LOG_DIR"
  exec > >(tee "$LOG_FILE") 2>&1
fi

export MSYS_NO_PATHCONV=1

DIRECT_PREFIX="/${RUN_ID}/direct"
UNAVAILABLE_PREFIX="/${RUN_ID}/unavailable"
CROSS_PREFIX="/${RUN_ID}/cross"

DIRECT_ROUTE="${RUN_ID}-direct"
UNAVAILABLE_ROUTE="${RUN_ID}-unavailable"
CROSS_ROUTE="${RUN_ID}-cross"
GRANT_NAME="${RUN_ID}-allow-route"

DIRECT_BACKEND_NAME="${RUN_ID}-direct-backend"
CROSS_BACKEND_NAME="${RUN_ID}-cross-backend"
BACKEND_NAMESPACE="${RUN_ID}-backend"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'
if [ ! -t 1 ] || [ -n "${NO_COLOR:-}" ]; then
  GREEN=''
  RED=''
  YELLOW=''
  BLUE=''
  NC=''
fi

TOTAL=0
PASS=0
FAIL=0
MOCK_SERVER_PID=""
MOCK_SERVER_FILE=""
MOCK_SERVER_ERR=""
PF_PID=""
PYTHON_BIN=""
RESP_CODE=""
RESP_TEXT=""
RESP_HEADERS=""
RESP_ERR=""
RESP_CURL_RC=0

banner() {
  printf "\n%s======================================================================%s\n" "$BLUE" "$NC"
  printf "%s%s%s\n" "$BLUE" "$*" "$NC"
  printf "%s======================================================================%s\n" "$BLUE" "$NC"
}

case_header() {
  printf "\n%s----------------------------------------------------------------------%s\n" "$BLUE" "$NC"
  printf "%s测试项 %s%s\n" "$BLUE" "$*" "$NC"
  printf "%s----------------------------------------------------------------------%s\n" "$BLUE" "$NC"
}

precondition() {
  printf "  前置条件: %s\n" "$*"
}

step() {
  printf "  步骤: %s\n" "$*"
}

detail() {
  printf "    - %s\n" "$*"
}

bash_command() {
  printf "  Command (bash):\n"
  printf '```bash\n%s\n```\n' "$*"
}

quote_arg() {
  local value="$1"
  printf "'"
  printf "%s" "$value" | sed "s/'/'\\\\''/g"
  printf "'"
}

pass_line() {
  printf "    %bPASS%b %s\n" "$GREEN" "$NC" "$*"
}

fail_line() {
  printf "    %bFAIL%b %s\n" "$RED" "$NC" "$*"
}

assert_eq() {
  local desc="$1"
  local expected="$2"
  local actual="$3"
  TOTAL=$((TOTAL + 1))
  if [ "$expected" = "$actual" ]; then
    PASS=$((PASS + 1))
    pass_line "$desc (expected=$expected actual=$actual)"
  else
    FAIL=$((FAIL + 1))
    fail_line "$desc (expected=$expected actual=$actual)"
  fi
}

assert_match() {
  local desc="$1"
  local pattern="$2"
  local actual="$3"
  TOTAL=$((TOTAL + 1))
  if printf "%s" "$actual" | grep -Eq "$pattern"; then
    PASS=$((PASS + 1))
    pass_line "$desc (actual=$actual matches $pattern)"
  else
    FAIL=$((FAIL + 1))
    fail_line "$desc (actual=$actual does not match $pattern)"
  fi
}

assert_contains() {
  local desc="$1"
  local needle="$2"
  local haystack="$3"
  TOTAL=$((TOTAL + 1))
  if printf "%s" "$haystack" | grep -Fq -- "$needle"; then
    PASS=$((PASS + 1))
    pass_line "$desc (contains: $needle)"
  else
    FAIL=$((FAIL + 1))
    fail_line "$desc (missing: $needle)"
  fi
}

assert_not_contains() {
  local desc="$1"
  local needle="$2"
  local haystack="$3"
  TOTAL=$((TOTAL + 1))
  if printf "%s" "$haystack" | grep -Fq -- "$needle"; then
    FAIL=$((FAIL + 1))
    fail_line "$desc (unexpected content: $needle)"
  else
    PASS=$((PASS + 1))
    pass_line "$desc (does not contain: $needle)"
  fi
}

assert_gt() {
  local desc="$1"
  local before="$2"
  local after="$3"
  TOTAL=$((TOTAL + 1))
  if [ "${after:-0}" -gt "${before:-0}" ] 2>/dev/null; then
    PASS=$((PASS + 1))
    pass_line "$desc (before=$before after=$after)"
  else
    FAIL=$((FAIL + 1))
    fail_line "$desc (before=$before after=$after)"
  fi
}

assert_same_number() {
  local desc="$1"
  local before="$2"
  local after="$3"
  TOTAL=$((TOTAL + 1))
  if [ "$before" = "$after" ]; then
    PASS=$((PASS + 1))
    pass_line "$desc (before=$before after=$after)"
  else
    FAIL=$((FAIL + 1))
    fail_line "$desc (before=$before after=$after)"
  fi
}

compact() {
  printf "%s" "$1" | tr '\r\n' '  ' | cut -c 1-800
}

require_cmd() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    echo "FATAL: command not found: $name" >&2
    exit 2
  fi
}

find_python() {
  if [ -n "${PYTHON_BIN_OVERRIDE:-}" ] && command -v "$PYTHON_BIN_OVERRIDE" >/dev/null 2>&1; then
    if "$PYTHON_BIN_OVERRIDE" --version >/dev/null 2>&1; then
      PYTHON_BIN="$PYTHON_BIN_OVERRIDE"
      return
    fi
  fi
  local candidate
  for candidate in python3 python py; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" --version >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      return
    fi
  done
  echo "FATAL: python3/python is required for --mock mode and k8s backend checks" >&2
  exit 2
}

http_request() {
  local method="$1"
  local url="$2"
  local data="${3:-}"
  local body_file headers_file err_file
  local curl_body_file curl_headers_file
  body_file="$(mktemp)"
  headers_file="$(mktemp)"
  err_file="$(mktemp)"
  curl_body_file="$body_file"
  curl_headers_file="$headers_file"
  if command -v cygpath >/dev/null 2>&1; then
    curl_body_file="$(cygpath -w "$body_file")"
    curl_headers_file="$(cygpath -w "$headers_file")"
  fi

  if [ -n "$data" ]; then
    RESP_CODE="$(curl -sS --connect-timeout "$HTTP_CONNECT_TIMEOUT" --max-time "$HTTP_TIMEOUT" -X "$method" \
      -H "Content-Type: application/json" \
      -D "$curl_headers_file" -o "$curl_body_file" -w "%{http_code}" \
      --data "$data" "$url" 2>"$err_file")"
  else
    RESP_CODE="$(curl -sS --connect-timeout "$HTTP_CONNECT_TIMEOUT" --max-time "$HTTP_TIMEOUT" -X "$method" \
      -D "$curl_headers_file" -o "$curl_body_file" -w "%{http_code}" \
      "$url" 2>"$err_file")"
  fi
  RESP_CURL_RC=$?
  if [ "$RESP_CURL_RC" -ne 0 ] && [ -z "$RESP_CODE" ]; then
    RESP_CODE="000"
  fi
  RESP_TEXT="$(tr -d '\r' < "$body_file")"
  RESP_HEADERS="$(tr -d '\r' < "$headers_file")"
  RESP_ERR="$(tr -d '\r' < "$err_file")"
  rm -f "$body_file" "$headers_file" "$err_file"
}

log_response() {
  local label="$1"
  detail "$label HTTP=$RESP_CODE curl_rc=$RESP_CURL_RC"
  if [ -n "$RESP_ERR" ]; then
    detail "$label curl_stderr=$(compact "$RESP_ERR")"
  fi
  detail "$label response_body=$(compact "$RESP_TEXT")"
}

wait_route_window() {
  if [ "${ROUTE_WAIT_SECONDS:-0}" -gt 0 ] 2>/dev/null; then
    detail "等待路由生效窗口 ${ROUTE_WAIT_SECONDS}s"
    sleep "$ROUTE_WAIT_SECONDS"
  else
    detail "本模式无需额外等待路由生效窗口"
  fi
}

show_curl_command() {
  local method="$1"
  local url="$2"
  local data="${3:-}"
  local cmd
  cmd="curl -i -sS --connect-timeout $(quote_arg "$HTTP_CONNECT_TIMEOUT") --max-time $(quote_arg "$HTTP_TIMEOUT") -X $(quote_arg "$method")"
  if [ -n "$data" ]; then
    cmd="$cmd -H 'Content-Type: application/json' --data $(quote_arg "$data")"
  fi
  cmd="$cmd $(quote_arg "$url")"
  bash_command "$cmd"
}

show_kubectl_apply_yaml_command() {
  local yaml="$1"
  bash_command "cat <<'YAML' | kubectl apply -f -
${yaml}
YAML"
}

show_publish_direct_route_command() {
  if [ "$MODE" = "mock" ]; then
    show_curl_command PUT "$BASE_URL/__control/routes/${DIRECT_ROUTE}" \
      "{\"prefix\":\"${DIRECT_PREFIX}\",\"target\":\"direct\",\"requires_grant\":false}"
    return
  fi
  show_kubectl_apply_yaml_command "$(k8s_route_yaml "$DIRECT_ROUTE" "$DIRECT_PREFIX" "$DIRECT_BACKEND_NAME" "")"
}

show_delete_direct_route_command() {
  if [ "$MODE" = "mock" ]; then
    show_curl_command DELETE "$BASE_URL/__control/routes/${DIRECT_ROUTE}"
    return
  fi
  bash_command "kubectl -n $(quote_arg "$GATEWAY_NAMESPACE") delete httproute $(quote_arg "$DIRECT_ROUTE") --ignore-not-found"
}

show_publish_unavailable_route_command() {
  if [ "$MODE" = "mock" ]; then
    show_curl_command PUT "$BASE_URL/__control/routes/${UNAVAILABLE_ROUTE}" \
      "{\"prefix\":\"${UNAVAILABLE_PREFIX}\",\"target\":\"unavailable\",\"requires_grant\":false}"
    return
  fi
  show_kubectl_apply_yaml_command "$(k8s_route_yaml "$UNAVAILABLE_ROUTE" "$UNAVAILABLE_PREFIX" "${RUN_ID}-missing-service" "")"
}

show_publish_cross_route_command() {
  if [ "$MODE" = "mock" ]; then
    show_curl_command PUT "$BASE_URL/__control/routes/${CROSS_ROUTE}" \
      "{\"prefix\":\"${CROSS_PREFIX}\",\"target\":\"cross\",\"requires_grant\":true,\"grant\":\"${GRANT_NAME}\"}"
    return
  fi
  show_kubectl_apply_yaml_command "$(k8s_route_yaml "$CROSS_ROUTE" "$CROSS_PREFIX" "$CROSS_BACKEND_NAME" "$BACKEND_NAMESPACE")"
}

show_apply_cross_grant_command() {
  if [ "$MODE" = "mock" ]; then
    show_curl_command PUT "$BASE_URL/__control/grants/${GRANT_NAME}"
    return
  fi
  show_kubectl_apply_yaml_command "$(k8s_reference_grant_yaml)"
}

show_delete_cross_grant_command() {
  if [ "$MODE" = "mock" ]; then
    show_curl_command DELETE "$BASE_URL/__control/grants/${GRANT_NAME}"
    return
  fi
  bash_command "kubectl -n $(quote_arg "$BACKEND_NAMESPACE") delete referencegrant $(quote_arg "$GRANT_NAME") --ignore-not-found"
}

show_hits_command() {
  local backend="$1"
  if [ "$MODE" = "mock" ]; then
    bash_command "curl -sS $(quote_arg "$BASE_URL/__hits/${backend}") | sed -n 's/.*\"hits\"[[:space:]]*:[[:space:]]*\\([0-9][0-9]*\\).*/\\1/p'"
    return
  fi
  local ns name code
  read -r ns name <<<"$(k8s_backend_ns_name "$backend")"
  code="import json, urllib.request; print(json.load(urllib.request.urlopen('http://127.0.0.1:8080/__hits', timeout=5))['hits'])"
  bash_command "kubectl -n $(quote_arg "$ns") exec $(quote_arg "deploy/${name}") -- python -c $(quote_arg "$code")"
}

mock_write_server() {
  MOCK_SERVER_FILE="$(mktemp "${TMPDIR:-/tmp}/gateway-mock.XXXXXX.py")"
  cat > "$MOCK_SERVER_FILE" <<'PY'
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

routes = {}
grants = set()
hits = {"direct": 0, "cross": 0}

def send(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)

class Handler(BaseHTTPRequestHandler):
    server_version = "gateway-test-mock/1.0"

    def log_message(self, fmt, *args):
        return

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        self.handle_all()

    def do_POST(self):
        self.handle_all()

    def do_PUT(self):
        self.handle_all()

    def do_DELETE(self):
        self.handle_all()

    def handle_all(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/__health":
            send(self, 200, {"status": "ok", "service": "gateway-test-mock"})
            return

        if path.startswith("/__hits/"):
            parts = [p for p in path.split("/") if p]
            target = parts[1] if len(parts) >= 2 else ""
            if len(parts) == 3 and parts[2] == "reset" and self.command == "POST":
                hits[target] = 0
                send(self, 200, {"target": target, "hits": hits.get(target, 0), "reset": True})
                return
            if self.command == "GET":
                send(self, 200, {"target": target, "hits": hits.get(target, 0)})
                return
            send(self, 405, {"error": "method not allowed"})
            return

        if path.startswith("/__control/routes/"):
            name = path.rsplit("/", 1)[-1]
            if self.command == "PUT":
                body = self.read_json()
                routes[name] = {
                    "prefix": body.get("prefix", ""),
                    "target": body.get("target", "direct"),
                    "requires_grant": bool(body.get("requires_grant", False)),
                    "grant": body.get("grant", ""),
                }
                send(self, 200, {"status": "ok", "route": name, "routes": sorted(routes)})
                return
            if self.command == "DELETE":
                routes.pop(name, None)
                send(self, 200, {"status": "deleted", "route": name, "routes": sorted(routes)})
                return
            send(self, 405, {"error": "method not allowed"})
            return

        if path.startswith("/__control/grants/"):
            name = path.rsplit("/", 1)[-1]
            if self.command == "PUT":
                grants.add(name)
                send(self, 200, {"status": "ok", "grant": name, "grants": sorted(grants)})
                return
            if self.command == "DELETE":
                grants.discard(name)
                send(self, 200, {"status": "deleted", "grant": name, "grants": sorted(grants)})
                return
            send(self, 405, {"error": "method not allowed"})
            return

        selected_name = None
        selected = None
        for name, route in routes.items():
            prefix = route.get("prefix") or ""
            if path == prefix or path.startswith(prefix + "/"):
                if selected is None or len(prefix) > len(selected.get("prefix", "")):
                    selected_name = name
                    selected = route

        if selected is None:
            send(self, 404, {"error": "no route matched", "path": self.path})
            return

        target = selected.get("target", "direct")
        if target == "unavailable":
            send(self, 503, {"error": "upstream unavailable", "route": selected_name, "path": self.path})
            return

        grant = selected.get("grant") or selected_name
        if selected.get("requires_grant") and grant not in grants:
            send(self, 502, {"error": "cross namespace reference is not permitted", "route": selected_name, "path": self.path})
            return

        hits[target] = hits.get(target, 0) + 1
        send(self, 200, {
            "marker": "gateway-test-backend",
            "backend": target,
            "route": selected_name,
            "method": self.command,
            "path": self.path,
            "hit": hits[target],
            "headers": {
                "host": self.headers.get("Host", ""),
                "user-agent": self.headers.get("User-Agent", ""),
            },
        })

if __name__ == "__main__":
    port = int(sys.argv[1])
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.serve_forever()
PY
}

mock_start() {
  find_python
  mock_write_server
  MOCK_SERVER_ERR="$(mktemp "${TMPDIR:-/tmp}/gateway-mock.XXXXXX.err")"
  local server_file_arg="$MOCK_SERVER_FILE"
  if command -v cygpath >/dev/null 2>&1; then
    server_file_arg="$(cygpath -w "$MOCK_SERVER_FILE")"
  fi
  "$PYTHON_BIN" "$server_file_arg" "$MOCK_PORT" >/dev/null 2>"$MOCK_SERVER_ERR" &
  MOCK_SERVER_PID=$!

  local i=0
  while [ "$i" -lt 20 ]; do
    RESP_CODE="$(curl -sS --connect-timeout 1 --max-time 1 -o /dev/null -w "%{http_code}" "$BASE_URL/__health" 2>/dev/null || true)"
    if [ "$RESP_CODE" = "200" ]; then
      return 0
    fi
    if ! kill -0 "$MOCK_SERVER_PID" >/dev/null 2>&1; then
      break
    fi
    i=$((i + 1))
    sleep 0.1
  done
  if [ -n "$MOCK_SERVER_ERR" ] && [ -s "$MOCK_SERVER_ERR" ]; then
    echo "Mock server stderr:" >&2
    sed 's/^/  /' "$MOCK_SERVER_ERR" >&2
  fi
  echo "FATAL: local mock gateway did not start on $BASE_URL" >&2
  exit 2
}

mock_control() {
  local method="$1"
  local path="$2"
  local data="${3:-}"
  http_request "$method" "$BASE_URL$path" "$data"
  printf "%s" "$RESP_TEXT"
  printf "\nHTTP=%s\n" "$RESP_CODE"
  printf "%s" "$RESP_CODE" | grep -Eq '^2[0-9][0-9]$'
}

k8s_backend_script_yaml() {
  local ns="$1"
  local name="$2"
  local backend_id="$3"
  cat <<YAML
apiVersion: v1
kind: ConfigMap
metadata:
  name: ${name}-script
  namespace: ${ns}
  labels:
    aidp-gateway-test: "${RUN_ID}"
data:
  server.py: |
    import json
    import os
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    BACKEND_ID = os.environ.get("BACKEND_ID", "backend")
    hits = 0

    def send(handler, status, payload):
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    class Handler(BaseHTTPRequestHandler):
        server_version = "gateway-test-backend/1.0"

        def log_message(self, fmt, *args):
            return

        def do_GET(self):
            self.handle_all()

        def do_POST(self):
            self.handle_all()

        def do_PUT(self):
            self.handle_all()

        def do_DELETE(self):
            self.handle_all()

        def handle_all(self):
            global hits
            path = self.path.split("?", 1)[0]
            if path == "/health":
                send(self, 200, {"status": "ok", "service": "gateway-test-backend", "backend": BACKEND_ID})
                return
            if path == "/__hits":
                send(self, 200, {"backend": BACKEND_ID, "hits": hits})
                return
            if path == "/__reset":
                hits = 0
                send(self, 200, {"backend": BACKEND_ID, "hits": hits, "reset": True})
                return
            hits += 1
            send(self, 200, {
                "marker": "gateway-test-backend",
                "backend": BACKEND_ID,
                "method": self.command,
                "path": self.path,
                "hit": hits,
                "headers": {
                    "host": self.headers.get("Host", ""),
                    "user-agent": self.headers.get("User-Agent", ""),
                },
            })

    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${name}
  namespace: ${ns}
  labels:
    app: ${name}
    aidp-gateway-test: "${RUN_ID}"
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ${name}
  template:
    metadata:
      labels:
        app: ${name}
        aidp-gateway-test: "${RUN_ID}"
    spec:
      containers:
      - name: backend
        image: ${BACKEND_IMAGE}
        imagePullPolicy: IfNotPresent
        command: ["python", "-u", "/app/server.py"]
        env:
        - name: BACKEND_ID
          value: "${backend_id}"
        ports:
        - containerPort: 8080
        readinessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 1
          periodSeconds: 2
        volumeMounts:
        - name: script
          mountPath: /app
      volumes:
      - name: script
        configMap:
          name: ${name}-script
---
apiVersion: v1
kind: Service
metadata:
  name: ${name}
  namespace: ${ns}
  labels:
    app: ${name}
    aidp-gateway-test: "${RUN_ID}"
spec:
  type: ClusterIP
  ports:
  - name: http
    port: 8080
    targetPort: 8080
  selector:
    app: ${name}
YAML
}

k8s_apply_backend() {
  local ns="$1"
  local name="$2"
  local backend_id="$3"
  if [ "$ns" != "$GATEWAY_NAMESPACE" ]; then
    kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f -
  fi
  k8s_backend_script_yaml "$ns" "$name" "$backend_id" | kubectl apply -f -
  kubectl -n "$ns" rollout status "deploy/${name}" --timeout=120s
}

k8s_delete_backend() {
  local ns="$1"
  local name="$2"
  kubectl -n "$ns" delete deploy "$name" --ignore-not-found >/dev/null 2>&1 || true
  kubectl -n "$ns" delete svc "$name" --ignore-not-found >/dev/null 2>&1 || true
  kubectl -n "$ns" delete configmap "${name}-script" --ignore-not-found >/dev/null 2>&1 || true
}

k8s_route_yaml() {
  local route_name="$1"
  local prefix="$2"
  local backend_name="$3"
  local backend_ns="${4:-}"
  local ns_line=""
  if [ -n "$backend_ns" ]; then
    ns_line="      namespace: ${backend_ns}"
  fi
  cat <<YAML
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: ${route_name}
  namespace: ${GATEWAY_NAMESPACE}
  labels:
    aidp-gateway-test: "${RUN_ID}"
spec:
  parentRefs:
  - name: ${GATEWAY_NAME}
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: ${prefix}
    backendRefs:
    - name: ${backend_name}
${ns_line}
      port: 8080
YAML
}

k8s_reference_grant_yaml() {
  cat <<YAML
apiVersion: gateway.networking.k8s.io/v1beta1
kind: ReferenceGrant
metadata:
  name: ${GRANT_NAME}
  namespace: ${BACKEND_NAMESPACE}
  labels:
    aidp-gateway-test: "${RUN_ID}"
spec:
  from:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    namespace: ${GATEWAY_NAMESPACE}
  to:
  - group: ""
    kind: Service
    name: ${CROSS_BACKEND_NAME}
YAML
}

ensure_gateway_access() {
  http_request GET "${BASE_URL}/"
  if [ "$RESP_CURL_RC" -eq 0 ] && printf "%s" "$RESP_CODE" | grep -Eq '^[1-5][0-9][0-9]$'; then
    detail "Gateway URL 已可访问: ${BASE_URL} (HTTP=${RESP_CODE})"
    return 0
  fi

  detail "Gateway URL 暂不可访问，尝试 kubectl port-forward 到 ${BASE_URL}"
  local svc
  svc="$(kubectl -n "$GATEWAY_NAMESPACE" get svc \
    -l "gateway.envoyproxy.io/owning-gateway-name=${GATEWAY_NAME}" \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  if [ -z "$svc" ]; then
    svc="$(kubectl -n "$GATEWAY_NAMESPACE" get svc -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  fi
  if [ -z "$svc" ]; then
    echo "FATAL: cannot find Gateway service in namespace ${GATEWAY_NAMESPACE}" >&2
    exit 2
  fi
  kubectl -n "$GATEWAY_NAMESPACE" port-forward "svc/${svc}" "${GATEWAY_PORT}:80" >/dev/null 2>&1 &
  PF_PID=$!
  sleep 3
  http_request GET "${BASE_URL}/"
  if [ "$RESP_CURL_RC" -ne 0 ]; then
    echo "FATAL: Gateway is still unreachable at ${BASE_URL}: ${RESP_ERR}" >&2
    exit 2
  fi
  detail "已建立 port-forward: svc/${svc} -> localhost:${GATEWAY_PORT}"
}

k8s_setup() {
  require_cmd kubectl
  require_cmd curl
  find_python

  banner "K8s 模式准备"
  detail "Gateway namespace=${GATEWAY_NAMESPACE}"
  detail "Gateway name=${GATEWAY_NAME}"
  detail "Backend image=${BACKEND_IMAGE}"
  ensure_gateway_access

  detail "创建同 namespace 测试后端: ${GATEWAY_NAMESPACE}/${DIRECT_BACKEND_NAME}"
  k8s_apply_backend "$GATEWAY_NAMESPACE" "$DIRECT_BACKEND_NAME" "direct"
  detail "创建跨 namespace 测试后端: ${BACKEND_NAMESPACE}/${CROSS_BACKEND_NAME}"
  k8s_apply_backend "$BACKEND_NAMESPACE" "$CROSS_BACKEND_NAME" "cross"
}

k8s_backend_ns_name() {
  local backend="$1"
  if [ "$backend" = "direct" ]; then
    printf "%s %s" "$GATEWAY_NAMESPACE" "$DIRECT_BACKEND_NAME"
  else
    printf "%s %s" "$BACKEND_NAMESPACE" "$CROSS_BACKEND_NAME"
  fi
}

k8s_backend_exec_python() {
  local backend="$1"
  local code="$2"
  local ns name
  read -r ns name <<<"$(k8s_backend_ns_name "$backend")"
  kubectl -n "$ns" exec "deploy/${name}" -- python -c "$code" 2>/dev/null | tr -d '\r'
}

publish_direct_route() {
  if [ "$MODE" = "mock" ]; then
    mock_control PUT "/__control/routes/${DIRECT_ROUTE}" \
      "{\"prefix\":\"${DIRECT_PREFIX}\",\"target\":\"direct\",\"requires_grant\":false}"
    return $?
  fi
  k8s_route_yaml "$DIRECT_ROUTE" "$DIRECT_PREFIX" "$DIRECT_BACKEND_NAME" "" | kubectl apply -f -
}

delete_direct_route() {
  if [ "$MODE" = "mock" ]; then
    mock_control DELETE "/__control/routes/${DIRECT_ROUTE}"
    return $?
  fi
  kubectl -n "$GATEWAY_NAMESPACE" delete httproute "$DIRECT_ROUTE" --ignore-not-found
}

publish_unavailable_route() {
  if [ "$MODE" = "mock" ]; then
    mock_control PUT "/__control/routes/${UNAVAILABLE_ROUTE}" \
      "{\"prefix\":\"${UNAVAILABLE_PREFIX}\",\"target\":\"unavailable\",\"requires_grant\":false}"
    return $?
  fi
  k8s_route_yaml "$UNAVAILABLE_ROUTE" "$UNAVAILABLE_PREFIX" "${RUN_ID}-missing-service" "" | kubectl apply -f -
}

publish_cross_route() {
  if [ "$MODE" = "mock" ]; then
    mock_control PUT "/__control/routes/${CROSS_ROUTE}" \
      "{\"prefix\":\"${CROSS_PREFIX}\",\"target\":\"cross\",\"requires_grant\":true,\"grant\":\"${GRANT_NAME}\"}"
    return $?
  fi
  k8s_route_yaml "$CROSS_ROUTE" "$CROSS_PREFIX" "$CROSS_BACKEND_NAME" "$BACKEND_NAMESPACE" | kubectl apply -f -
}

apply_cross_grant() {
  if [ "$MODE" = "mock" ]; then
    mock_control PUT "/__control/grants/${GRANT_NAME}"
    return $?
  fi
  k8s_reference_grant_yaml | kubectl apply -f -
}

delete_cross_grant() {
  if [ "$MODE" = "mock" ]; then
    mock_control DELETE "/__control/grants/${GRANT_NAME}"
    return $?
  fi
  kubectl -n "$BACKEND_NAMESPACE" delete referencegrant "$GRANT_NAME" --ignore-not-found
}

get_hits() {
  local backend="$1"
  if [ "$MODE" = "mock" ]; then
    http_request GET "$BASE_URL/__hits/${backend}"
    printf "%s" "$RESP_TEXT" | sed -n 's/.*"hits"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p'
    return
  fi
  k8s_backend_exec_python "$backend" \
    "import json, urllib.request; print(json.load(urllib.request.urlopen('http://127.0.0.1:8080/__hits', timeout=5))['hits'])"
}

reset_hits() {
  local backend="$1"
  if [ "$MODE" = "mock" ]; then
    http_request POST "$BASE_URL/__hits/${backend}/reset"
    return
  fi
  k8s_backend_exec_python "$backend" \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/__reset', timeout=5).read(); print('ok')" >/dev/null
}

cleanup() {
  local rc=$?
  trap - EXIT
  if [ -n "$PF_PID" ]; then
    kill "$PF_PID" >/dev/null 2>&1 || true
  fi
  if [ "$MODE" = "mock" ]; then
    if [ -n "$MOCK_SERVER_PID" ]; then
      kill "$MOCK_SERVER_PID" >/dev/null 2>&1 || true
    fi
    if [ -n "$MOCK_SERVER_FILE" ]; then
      rm -f "$MOCK_SERVER_FILE"
    fi
    if [ -n "$MOCK_SERVER_ERR" ]; then
      rm -f "$MOCK_SERVER_ERR"
    fi
  elif [ "$KEEP_RESOURCES" != "1" ]; then
    kubectl -n "$GATEWAY_NAMESPACE" delete httproute "$DIRECT_ROUTE" "$UNAVAILABLE_ROUTE" "$CROSS_ROUTE" --ignore-not-found >/dev/null 2>&1 || true
    kubectl -n "$BACKEND_NAMESPACE" delete referencegrant "$GRANT_NAME" --ignore-not-found >/dev/null 2>&1 || true
    k8s_delete_backend "$GATEWAY_NAMESPACE" "$DIRECT_BACKEND_NAME"
    k8s_delete_backend "$BACKEND_NAMESPACE" "$CROSS_BACKEND_NAME"
    kubectl delete namespace "$BACKEND_NAMESPACE" --ignore-not-found >/dev/null 2>&1 || true
  fi
  exit "$rc"
}
trap cleanup EXIT

run_case_001() {
  case_header "GW-TC-001 发布测试路由后 Gateway 能够转发到测试后端"
  precondition "Gateway 入口可访问；测试后端具备真实响应和命中计数接口。"
  precondition "本次路径: ${DIRECT_PREFIX}"

  step "1. 发布指向测试后端的路由配置。"
  local out rc before after
  show_publish_direct_route_command
  out="$(publish_direct_route 2>&1)"
  rc=$?
  detail "发布结果: $(compact "$out")"
  assert_eq "路由发布命令返回成功" "0" "$rc"
  wait_route_window

  step "2. 从外部客户端访问该路由路径，记录 HTTP 状态码和响应体。"
  show_hits_command direct
  before="$(get_hits direct)"
  show_curl_command GET "${BASE_URL}${DIRECT_PREFIX}/ping?case=GW-TC-001"
  http_request GET "${BASE_URL}${DIRECT_PREFIX}/ping?case=GW-TC-001"
  log_response "外部访问"
  assert_eq "外部访问返回 HTTP 200" "200" "$RESP_CODE"
  assert_contains "响应体包含后端标识" "gateway-test-backend" "$RESP_TEXT"
  assert_contains "响应体包含请求路径回显" "${DIRECT_PREFIX}/ping" "$RESP_TEXT"

  step "3. 读取测试后端命中计数，确认请求到达后端。"
  show_hits_command direct
  after="$(get_hits direct)"
  detail "后端命中计数: before=${before:-NA} after=${after:-NA}"
  assert_gt "后端命中计数增加" "${before:-0}" "${after:-0}"
}

run_case_002() {
  case_header "GW-TC-002 已生效业务路径外部访问返回后端真实响应"
  precondition "业务路径已发布并完成生效等待。"
  precondition "本次路径: ${DIRECT_PREFIX}/stable"

  local i codes bodies bad=0
  codes=""
  bodies=""
  step "1. 连续 3 次从外部客户端访问业务路径。"
  for i in 1 2 3; do
    show_curl_command GET "${BASE_URL}${DIRECT_PREFIX}/stable"
    http_request GET "${BASE_URL}${DIRECT_PREFIX}/stable"
    log_response "第 ${i} 次访问"
    codes="${codes}${RESP_CODE} "
    bodies="${bodies}
${RESP_TEXT}"
    if [ "$RESP_CODE" != "200" ]; then
      bad=$((bad + 1))
    fi
    if printf "%s" "$RESP_CODE" | grep -Eq '^(404|502|503|000)$'; then
      bad=$((bad + 1))
    fi
  done
  detail "三次 HTTP 状态码: ${codes}"

  step "2. 检查响应体是否包含后端标识和路径回显。"
  assert_eq "3 次访问均稳定返回 HTTP 200 且无 404/502/503/超时" "0" "$bad"
  assert_contains "响应内容为测试后端真实响应" "gateway-test-backend" "$bodies"
  assert_contains "响应内容包含请求路径回显" "${DIRECT_PREFIX}/stable" "$bodies"
}

run_case_003() {
  case_header "GW-TC-003 后端不可用时路由异常可被外部访问感知"
  precondition "准备一条指向不可用后端的测试路由，同时保留正常后端路由。"
  precondition "异常路径: ${UNAVAILABLE_PREFIX}; 正常路径: ${DIRECT_PREFIX}"

  step "1. 发布指向不可用后端的测试路由。"
  local out rc
  show_publish_unavailable_route_command
  out="$(publish_unavailable_route 2>&1)"
  rc=$?
  detail "发布结果: $(compact "$out")"
  assert_eq "异常路由发布命令返回成功" "0" "$rc"
  wait_route_window

  step "2. 访问异常路由路径，记录 HTTP 返回码和响应体。"
  show_curl_command GET "${BASE_URL}${UNAVAILABLE_PREFIX}/ping?case=GW-TC-003"
  http_request GET "${BASE_URL}${UNAVAILABLE_PREFIX}/ping?case=GW-TC-003"
  log_response "异常路由访问"
  assert_match "异常路由返回 500/502/503/504 等可感知错误" '^(500|502|503|504)$' "$RESP_CODE"
  assert_not_contains "异常路由不返回正常后端业务内容" "gateway-test-backend" "$RESP_TEXT"

  step "3. 访问正常后端路由，确认 Gateway 整体未受单条异常路由影响。"
  show_curl_command GET "${BASE_URL}${DIRECT_PREFIX}/healthy?case=GW-TC-003"
  http_request GET "${BASE_URL}${DIRECT_PREFIX}/healthy?case=GW-TC-003"
  log_response "正常路由访问"
  assert_eq "正常后端路由仍返回 HTTP 200" "200" "$RESP_CODE"
  assert_contains "正常路由仍返回后端真实响应" "gateway-test-backend" "$RESP_TEXT"
}

run_case_004() {
  case_header "GW-TC-004 删除业务路由后原路径不再转发"
  precondition "业务路由已配置且路径可正常访问；测试后端提供命中计数接口。"
  precondition "待删除路径: ${DIRECT_PREFIX}"

  local before after_delete before_delete out rc
  step "1. 删除前访问业务路径，确认路由可用并记录命中计数。"
  show_hits_command direct
  before="$(get_hits direct)"
  show_curl_command GET "${BASE_URL}${DIRECT_PREFIX}/before-delete?case=GW-TC-004"
  http_request GET "${BASE_URL}${DIRECT_PREFIX}/before-delete?case=GW-TC-004"
  log_response "删除前访问"
  assert_eq "删除前业务路径返回 HTTP 200" "200" "$RESP_CODE"
  assert_contains "删除前返回后端真实响应" "gateway-test-backend" "$RESP_TEXT"
  show_hits_command direct
  before_delete="$(get_hits direct)"
  detail "删除前后端命中计数: before=${before:-NA} after_first_access=${before_delete:-NA}"
  assert_gt "删除前访问使后端命中计数增加" "${before:-0}" "${before_delete:-0}"

  step "2. 删除对应业务路由配置。"
  show_delete_direct_route_command
  out="$(delete_direct_route 2>&1)"
  rc=$?
  detail "删除结果: $(compact "$out")"
  assert_eq "删除操作返回成功" "0" "$rc"
  wait_route_window

  step "3. 再次访问原业务路径并读取后端命中计数。"
  show_curl_command GET "${BASE_URL}${DIRECT_PREFIX}/after-delete?case=GW-TC-004"
  http_request GET "${BASE_URL}${DIRECT_PREFIX}/after-delete?case=GW-TC-004"
  log_response "删除后访问"
  assert_match "删除后原路径返回 404 或等价未匹配响应" '^(404)$' "$RESP_CODE"
  assert_not_contains "删除后响应不包含后端内容" "gateway-test-backend" "$RESP_TEXT"
  show_hits_command direct
  after_delete="$(get_hits direct)"
  detail "删除后后端命中计数: before_delete=${before_delete:-NA} after_delete=${after_delete:-NA}"
  assert_same_number "删除后后端命中计数不增加" "${before_delete:-0}" "${after_delete:-0}"
}

run_case_005() {
  case_header "GW-TC-005 跨 namespace 引用后端并完成授权后请求可转发"
  precondition "测试路由位于 Gateway namespace，后端服务位于独立 namespace。"
  precondition "跨 namespace 路径: ${CROSS_PREFIX}"

  local out rc before after_no_grant after_grant
  step "1. 确保授权配置不存在，发布跨 namespace 引用后端的测试路由。"
  show_delete_cross_grant_command
  out="$(delete_cross_grant 2>&1)"
  detail "清理授权结果: $(compact "$out")"
  show_publish_cross_route_command
  out="$(publish_cross_route 2>&1)"
  rc=$?
  detail "发布跨 namespace 路由结果: $(compact "$out")"
  assert_eq "跨 namespace 路由发布命令返回成功" "0" "$rc"
  wait_route_window

  step "2. 授权发布前访问跨 namespace 路径，确认请求不能正常到达后端。"
  show_hits_command cross
  before="$(get_hits cross)"
  show_curl_command GET "${BASE_URL}${CROSS_PREFIX}/before-grant?case=GW-TC-005"
  http_request GET "${BASE_URL}${CROSS_PREFIX}/before-grant?case=GW-TC-005"
  log_response "授权前访问"
  assert_match "授权前返回 404/500/502/503/504 等错误响应" '^(404|500|502|503|504)$' "$RESP_CODE"
  assert_not_contains "授权前响应不包含后端业务内容" "gateway-test-backend" "$RESP_TEXT"
  show_hits_command cross
  after_no_grant="$(get_hits cross)"
  detail "授权前后端命中计数: before=${before:-NA} after=${after_no_grant:-NA}"
  assert_same_number "授权前后端命中计数不增加" "${before:-0}" "${after_no_grant:-0}"

  step "3. 发布允许跨 namespace 引用的授权配置并再次访问同一路径。"
  show_apply_cross_grant_command
  out="$(apply_cross_grant 2>&1)"
  rc=$?
  detail "发布授权结果: $(compact "$out")"
  assert_eq "授权配置发布成功" "0" "$rc"
  wait_route_window
  show_curl_command GET "${BASE_URL}${CROSS_PREFIX}/after-grant?case=GW-TC-005"
  http_request GET "${BASE_URL}${CROSS_PREFIX}/after-grant?case=GW-TC-005"
  log_response "授权后访问"
  assert_eq "授权后同一路径返回 HTTP 200" "200" "$RESP_CODE"
  assert_contains "授权后返回后端真实响应" "gateway-test-backend" "$RESP_TEXT"
  show_hits_command cross
  after_grant="$(get_hits cross)"
  detail "授权后后端命中计数: before=${after_no_grant:-NA} after=${after_grant:-NA}"
  assert_gt "授权后后端命中计数增加" "${after_no_grant:-0}" "${after_grant:-0}"
}

run_case_006() {
  case_header "GW-TC-006 缺失跨 namespace 授权时请求不转发"
  precondition "跨 namespace 路由已存在，但后端 namespace 未发布允许引用的授权配置。"

  local out before after
  step "1. 删除跨 namespace 授权配置并记录当前后端命中计数。"
  show_delete_cross_grant_command
  out="$(delete_cross_grant 2>&1)"
  detail "删除授权结果: $(compact "$out")"
  wait_route_window
  show_hits_command cross
  before="$(get_hits cross)"
  detail "访问前后端命中计数: ${before:-NA}"

  step "2. 从外部客户端访问跨 namespace 路径。"
  show_curl_command GET "${BASE_URL}${CROSS_PREFIX}/missing-grant?case=GW-TC-006"
  http_request GET "${BASE_URL}${CROSS_PREFIX}/missing-grant?case=GW-TC-006"
  log_response "缺失授权访问"
  assert_match "缺失授权时返回 404/500/502/503/504 等错误响应" '^(404|500|502|503|504)$' "$RESP_CODE"
  assert_not_contains "缺失授权响应不包含后端业务内容" "gateway-test-backend" "$RESP_TEXT"

  step "3. 再次读取后端命中计数。"
  show_hits_command cross
  after="$(get_hits cross)"
  detail "访问后后端命中计数: ${after:-NA}"
  assert_same_number "缺失授权时后端命中计数不增加" "${before:-0}" "${after:-0}"
}

run_case_007() {
  case_header "GW-TC-007 删除跨 namespace 授权后已存在路由变为不可用"
  precondition "跨 namespace 路由已存在；先补齐授权确认删除前可用。"

  local out rc before after_ok after_delete
  step "1. 发布授权配置并访问跨 namespace 路径，确认删除前路由可用。"
  show_apply_cross_grant_command
  out="$(apply_cross_grant 2>&1)"
  rc=$?
  detail "发布授权结果: $(compact "$out")"
  assert_eq "授权配置发布成功" "0" "$rc"
  wait_route_window
  show_hits_command cross
  before="$(get_hits cross)"
  show_curl_command GET "${BASE_URL}${CROSS_PREFIX}/before-delete-grant?case=GW-TC-007"
  http_request GET "${BASE_URL}${CROSS_PREFIX}/before-delete-grant?case=GW-TC-007"
  log_response "删除授权前访问"
  assert_eq "删除授权前请求返回 HTTP 200" "200" "$RESP_CODE"
  assert_contains "删除授权前返回后端真实响应" "gateway-test-backend" "$RESP_TEXT"
  show_hits_command cross
  after_ok="$(get_hits cross)"
  detail "删除授权前命中计数: before=${before:-NA} after_ok=${after_ok:-NA}"
  assert_gt "删除授权前请求已转发到后端" "${before:-0}" "${after_ok:-0}"

  step "2. 删除对应跨 namespace 授权配置。"
  show_delete_cross_grant_command
  out="$(delete_cross_grant 2>&1)"
  rc=$?
  detail "删除授权结果: $(compact "$out")"
  assert_eq "授权配置删除操作返回成功" "0" "$rc"
  wait_route_window

  step "3. 再次访问同一路径并读取命中计数。"
  show_curl_command GET "${BASE_URL}${CROSS_PREFIX}/after-delete-grant?case=GW-TC-007"
  http_request GET "${BASE_URL}${CROSS_PREFIX}/after-delete-grant?case=GW-TC-007"
  log_response "删除授权后访问"
  assert_match "删除授权后路由变为不可用" '^(404|500|502|503|504)$' "$RESP_CODE"
  assert_not_contains "删除授权后响应不包含后端业务内容" "gateway-test-backend" "$RESP_TEXT"
  show_hits_command cross
  after_delete="$(get_hits cross)"
  detail "删除授权后命中计数: after_ok=${after_ok:-NA} after_delete=${after_delete:-NA}"
  assert_same_number "删除授权后后端命中计数不增加" "${after_ok:-0}" "${after_delete:-0}"
}

run_case_008() {
  case_header "GW-TC-008 重新补齐跨 namespace 授权后路由自动恢复"
  precondition "跨 namespace 路由已存在，当前缺少授权配置且访问不可用。"

  local out rc before_error before_recover after_recover
  step "1. 授权补齐前访问跨 namespace 路径，确认当前返回错误响应。"
  show_hits_command cross
  before_error="$(get_hits cross)"
  show_curl_command GET "${BASE_URL}${CROSS_PREFIX}/before-recover?case=GW-TC-008"
  http_request GET "${BASE_URL}${CROSS_PREFIX}/before-recover?case=GW-TC-008"
  log_response "补齐授权前访问"
  assert_match "补齐授权前路径不可用" '^(404|500|502|503|504)$' "$RESP_CODE"
  assert_not_contains "补齐授权前不返回后端业务内容" "gateway-test-backend" "$RESP_TEXT"
  show_hits_command cross
  before_recover="$(get_hits cross)"
  detail "补齐授权前命中计数: before=${before_error:-NA} after=${before_recover:-NA}"
  assert_same_number "补齐授权前后端命中计数不增加" "${before_error:-0}" "${before_recover:-0}"

  step "2. 重新发布跨 namespace 授权配置。"
  show_apply_cross_grant_command
  out="$(apply_cross_grant 2>&1)"
  rc=$?
  detail "重新发布授权结果: $(compact "$out")"
  assert_eq "授权配置补齐发布成功" "0" "$rc"
  wait_route_window

  step "3. 再次从外部客户端访问同一路径。"
  show_curl_command GET "${BASE_URL}${CROSS_PREFIX}/after-recover?case=GW-TC-008"
  http_request GET "${BASE_URL}${CROSS_PREFIX}/after-recover?case=GW-TC-008"
  log_response "补齐授权后访问"
  assert_eq "授权补齐后外部访问恢复 HTTP 200" "200" "$RESP_CODE"
  assert_contains "授权补齐后返回后端真实响应" "gateway-test-backend" "$RESP_TEXT"
  show_hits_command cross
  after_recover="$(get_hits cross)"
  detail "补齐授权后命中计数: before_recover=${before_recover:-NA} after_recover=${after_recover:-NA}"
  assert_gt "补齐授权后后端命中计数增加" "${before_recover:-0}" "${after_recover:-0}"
}

main() {
  require_cmd curl
  if [ "$MODE" = "mock" ]; then
    mock_start
  else
    k8s_setup
  fi

  reset_hits direct
  reset_hits cross

  banner "Gateway 路由黑盒测试"
  detail "mode=${MODE}"
  detail "base_url=${BASE_URL}"
  detail "run_id=${RUN_ID}"
  detail "direct_prefix=${DIRECT_PREFIX}"
  detail "unavailable_prefix=${UNAVAILABLE_PREFIX}"
  detail "cross_prefix=${CROSS_PREFIX}"
  detail "route_wait_seconds=${ROUTE_WAIT_SECONDS}"
  if [ "$NO_TEE_LOG" != "1" ]; then
    detail "log_file=${LOG_FILE}"
  fi

  run_case_001
  run_case_002
  run_case_003
  run_case_004
  run_case_005
  run_case_006
  run_case_007
  run_case_008

  banner "测试汇总"
  detail "TOTAL=${TOTAL}"
  detail "PASS=${PASS}"
  detail "FAIL=${FAIL}"
  if [ "$NO_TEE_LOG" != "1" ]; then
    detail "log_file=${LOG_FILE}"
  fi

  if [ "$FAIL" -eq 0 ]; then
    printf "%bRESULT: PASS%b\n" "$GREEN" "$NC"
    exit 0
  fi
  printf "%bRESULT: FAIL%b\n" "$RED" "$NC"
  exit 1
}

main "$@"
