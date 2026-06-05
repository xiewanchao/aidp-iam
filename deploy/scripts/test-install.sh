#!/usr/bin/env bash
# ============================================================================
# test-install.sh - clean install / reinstall validation runner.
#
# This script is intentionally a high-level acceptance runner. It drives the
# existing cleanup.sh, setup.sh, test.sh, and one optional mock backend test,
# while grouping output by validation item so each run leaves an auditable log.
#
# Default flow:
#   IT-AT-001  cleanup idempotency, deleting the Kind cluster for a true clean run
#   IT-AT-002  setup creates/uses the target cluster
#   IT-AT-003  custom images are built (unless --skip-build is passed)
#   IT-AT-004  gateway Helm release and Gateway dataplane are ready
#   IT-AT-005  IAM Helm release, Keycloak, Postgres, iam-services are ready
#   IT-AT-006  core deploy/scripts/test.sh passes
#   IT-AT-007  selected mock backend is absent and core test reports SKIP
#   IT-AT-008  selected mock backend is installed and its gateway smoke test passes
#   IT-AT-009  cleanup removes releases, namespaces, and Gateway resources
#   IT-AT-010  setup --skip-build reinstalls on the existing cluster
#   IT-AT-011  second core test pass after skip-build reinstall
#   IT-AT-012  optional failure-location demo, disabled by default
#
# Examples:
#   bash deploy/scripts/test-install.sh
#   bash deploy/scripts/test-install.sh --mock memory
#   bash deploy/scripts/test-install.sh --mock kb --skip-mock-build
#   bash deploy/scripts/test-install.sh --no-kind --skip-reinstall
#   bash deploy/scripts/test-install.sh --include-failure-demo
# ============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

CLUSTER_NAME="${CLUSTER_NAME:-da-cluster}"
GATEWAY_PORT="${GATEWAY_PORT:-30080}"
USE_KIND=true
SETUP_SKIP_BUILD=false
RUN_REINSTALL=true
INCLUDE_FAILURE_DEMO=false
FINAL_CLEANUP=false
MOCK_BACKEND="${MOCK_BACKEND:-memory}"
INSTALL_MOCK=true
SKIP_MOCK_BUILD=false
RESULT_DIR="${RESULT_DIR:-}"
SHOW_COMMAND_OUTPUT=true
SETUP_EXTRA_ARGS=()
RUNTIME_IMAGES=(
  "docker.io/envoyproxy/gateway:v1.7.2"
  "docker.io/envoyproxy/envoy:v1.36.5"
  "docker.io/alpine/kubectl:1.34.1"
  "docker.io/library/postgres:17"
  "docker.io/openpolicyagent/opa:0.42.2-static"
)

usage() {
  cat <<'EOF'
Usage:
  test-install.sh [options]

Options:
  --no-kind                 Pass --no-kind to setup.sh and do not delete Kind.
  --skip-build              Pass --skip-build to the first setup.sh run.
  --skip-reinstall          Skip IT-AT-010/011.
  --mock kb|memory|dataagent|none|auto
                            Select one mock backend for IT-AT-008. Default: memory.
  --no-mock                 Alias for --mock none.
  --skip-mock-build         Do not build the selected mock image locally.
  --include-failure-demo    Run IT-AT-012 non-destructive failure-location demo.
  --final-cleanup           Run cleanup.sh again after all validation steps.
  --result-dir DIR          Write command logs under DIR.
  --quiet-commands          Do not echo full command output to the console.
  --arch amd64|arm64        Pass --arch through to setup.sh.
  -h, --help                Show this help.

Environment:
  CLUSTER_NAME              Kind cluster name. Default: da-cluster.
  GATEWAY_PORT              Local gateway port. Default: 30080.
  MOCK_BACKEND              Same values as --mock. Default: memory.
  RESULT_DIR                Log directory override.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --no-kind)
      USE_KIND=false
      SETUP_EXTRA_ARGS+=(--no-kind)
      shift
      ;;
    --skip-build)
      SETUP_SKIP_BUILD=true
      shift
      ;;
    --skip-reinstall)
      RUN_REINSTALL=false
      shift
      ;;
    --mock)
      [ $# -ge 2 ] || { echo "ERROR: --mock requires a value" >&2; exit 2; }
      MOCK_BACKEND="$2"
      [ "$MOCK_BACKEND" = "none" ] && INSTALL_MOCK=false
      shift 2
      ;;
    --no-mock)
      MOCK_BACKEND="none"
      INSTALL_MOCK=false
      shift
      ;;
    --skip-mock-build)
      SKIP_MOCK_BUILD=true
      shift
      ;;
    --include-failure-demo)
      INCLUDE_FAILURE_DEMO=true
      shift
      ;;
    --final-cleanup)
      FINAL_CLEANUP=true
      shift
      ;;
    --result-dir)
      [ $# -ge 2 ] || { echo "ERROR: --result-dir requires a value" >&2; exit 2; }
      RESULT_DIR="$2"
      shift 2
      ;;
    --quiet-commands)
      SHOW_COMMAND_OUTPUT=false
      shift
      ;;
    --arch)
      [ $# -ge 2 ] || { echo "ERROR: --arch requires a value" >&2; exit 2; }
      SETUP_EXTRA_ARGS+=(--arch "$2")
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ -z "$RESULT_DIR" ]; then
  RESULT_DIR="$REPO_DIR/artifacts/test-results/install-$(date +%Y%m%d-%H%M%S)"
fi
mkdir -p "$RESULT_DIR"
LOG_FILE="$RESULT_DIR/test-install.log"
exec > >(tee -a "$LOG_FILE") 2>&1

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  GREEN='\033[0;32m'
  RED='\033[0;31m'
  YELLOW='\033[1;33m'
  BLUE='\033[0;34m'
  CYAN='\033[0;36m'
  NC='\033[0m'
else
  GREEN=''; RED=''; YELLOW=''; BLUE=''; CYAN=''; NC=''
fi

PASS=0
FAIL=0
SKIP=0
CMD_INDEX=0
LAST_CMD_LOG=""
CURRENT_CASE=""
CURRENT_CASE_FAIL_START=0
HOST_PYTHON=""
PYTHON_SHIM_DIR="$RESULT_DIR/.python-bin"

info() { printf "${CYAN}[INFO]${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}[WARN]${NC} %s\n" "$*"; }

configure_python_path() {
  local found py win_py unix_py py_dir

  found="$(command -v python3 2>/dev/null || true)"
  if [ -n "$found" ] && "$found" -c "import sys" >/dev/null 2>&1; then
    HOST_PYTHON="$found"
  fi

  found="$(command -v python 2>/dev/null || true)"
  if [ -z "$HOST_PYTHON" ] && [ -n "$found" ] && "$found" -c "import sys" >/dev/null 2>&1; then
    HOST_PYTHON="$found"
  fi

  if [ -z "$HOST_PYTHON" ]; then
    for py in /c/Users/*/AppData/Local/Programs/Python/Python*/python.exe /c/Python*/python.exe; do
      if [ -f "$py" ] && "$py" -c "import sys" >/dev/null 2>&1; then
        HOST_PYTHON="$py"
        break
      fi
    done
  fi

  if [ -z "$HOST_PYTHON" ] && command -v powershell.exe >/dev/null 2>&1 && command -v cygpath >/dev/null 2>&1; then
    win_py="$(powershell.exe -NoProfile -Command "Get-Command python.exe -All | Where-Object { \$_.Source -notlike '*WindowsApps*' } | Select-Object -First 1 -ExpandProperty Source" 2>/dev/null | tr -d '\r')"
    if [ -n "$win_py" ]; then
      unix_py="$(cygpath -u "$win_py" 2>/dev/null || true)"
      if [ -n "$unix_py" ] && [ -f "$unix_py" ] && "$unix_py" -c "import sys" >/dev/null 2>&1; then
        HOST_PYTHON="$unix_py"
      fi
    fi
  fi

  if [ -n "$HOST_PYTHON" ]; then
    py_dir="$(dirname "$HOST_PYTHON")"
    mkdir -p "$PYTHON_SHIM_DIR"
    {
      printf '#!/usr/bin/env bash\n'
      printf 'exec "%s" "$@"\n' "$HOST_PYTHON"
    } > "$PYTHON_SHIM_DIR/python"
    cp "$PYTHON_SHIM_DIR/python" "$PYTHON_SHIM_DIR/python3"
    chmod +x "$PYTHON_SHIM_DIR/python" "$PYTHON_SHIM_DIR/python3"
    export PATH="$PYTHON_SHIM_DIR:$py_dir:$PATH"
    hash -r 2>/dev/null || true
    return 0
  fi

  return 1
}

slug() {
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | tr -cs 'a-z0-9._-' '-' \
    | sed 's/^-//; s/-$//' \
    | cut -c1-70
}

case_start() {
  CURRENT_CASE="$1"
  CURRENT_CASE_FAIL_START=$FAIL
  printf "\n${BLUE}======================================================================${NC}\n"
  printf "${BLUE}%s${NC}\n" "$1"
  printf "${BLUE}======================================================================${NC}\n"
  [ $# -gt 1 ] && printf "%s\n" "$2"
}

case_end() {
  if [ "$FAIL" -eq "$CURRENT_CASE_FAIL_START" ]; then
    printf "${GREEN}[CASE PASS]${NC} %s\n" "$CURRENT_CASE"
  else
    printf "${RED}[CASE FAIL]${NC} %s\n" "$CURRENT_CASE"
  fi
}

pass() {
  PASS=$((PASS + 1))
  printf "  ${GREEN}PASS${NC} %s\n" "$1"
}

fail() {
  FAIL=$((FAIL + 1))
  printf "  ${RED}FAIL${NC} %s\n" "$1"
  [ $# -gt 1 ] && printf "       %s\n" "$2"
}

skip() {
  SKIP=$((SKIP + 1))
  printf "  ${YELLOW}SKIP${NC} %s\n" "$1"
}

print_file_with_prefix() {
  local file="$1"
  if [ "$SHOW_COMMAND_OUTPUT" = true ]; then
    sed 's/^/    | /' "$file"
  else
    printf "    | command output captured in %s\n" "$file"
  fi
}

run_cmd() {
  local desc="$1"
  shift
  CMD_INDEX=$((CMD_INDEX + 1))
  local log_name
  log_name="$(printf '%03d-%s.log' "$CMD_INDEX" "$(slug "$desc")")"
  LAST_CMD_LOG="$RESULT_DIR/$log_name"

  printf "\n  ${CYAN}[COMMAND]${NC} %s\n" "$desc"
  printf "    cwd: %s\n" "$(pwd)"
  printf "    argv:"
  printf " %q" "$@"
  printf "\n"

  set +e
  "$@" >"$LAST_CMD_LOG" 2>&1
  local rc=$?
  set -u

  print_file_with_prefix "$LAST_CMD_LOG"
  printf "    exit_code: %s\n" "$rc"
  return "$rc"
}

assert_cmd_success() {
  local desc="$1"
  shift
  if run_cmd "$desc" "$@"; then
    pass "$desc"
    return 0
  fi
  fail "$desc" "See $LAST_CMD_LOG"
  return 1
}

assert_cmd_failure() {
  local desc="$1"
  shift
  if run_cmd "$desc" "$@"; then
    fail "$desc" "Command succeeded, but this validation expected a controlled failure."
    return 1
  fi
  pass "$desc"
  return 0
}

assert_file_contains() {
  local desc="$1"
  local file="$2"
  local pattern="$3"
  if grep -qE "$pattern" "$file"; then
    pass "$desc"
  else
    fail "$desc" "Pattern not found: $pattern; file: $file"
  fi
}

assert_file_not_contains() {
  local desc="$1"
  local file="$2"
  local pattern="$3"
  if grep -qE "$pattern" "$file"; then
    fail "$desc" "Unexpected pattern found: $pattern; file: $file"
  else
    pass "$desc"
  fi
}

assert_shell_success() {
  local desc="$1"
  local script="$2"
  if bash -lc "$script"; then
    pass "$desc"
    return 0
  fi
  fail "$desc"
  return 1
}

require_cmd() {
  local cmd="$1"
  if command -v "$cmd" >/dev/null 2>&1; then
    pass "required command exists: $cmd"
  else
    fail "required command missing: $cmd"
  fi
}

run_cleanup_delete_kind() {
  if [ "$USE_KIND" = true ]; then
    assert_cmd_success "cleanup.sh with CLUSTER_NAME=$CLUSTER_NAME (delete Kind cluster if present)" \
      env CLUSTER_NAME="$CLUSTER_NAME" bash "$SCRIPT_DIR/cleanup.sh"
  else
    assert_cmd_success "cleanup.sh on current kube context (--no-kind mode)" \
      env CLUSTER_NAME= bash "$SCRIPT_DIR/cleanup.sh"
  fi
}

run_cleanup_keep_cluster() {
  assert_cmd_success "cleanup.sh preserving current cluster" \
    env CLUSTER_NAME= bash "$SCRIPT_DIR/cleanup.sh"
}

ensure_kind_cluster_for_runtime_images() {
  if [ "$USE_KIND" != true ]; then
    skip "runtime image preload skipped in --no-kind mode"
    return 0
  fi

  if kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
    pass "Kind cluster exists before runtime image preload: $CLUSTER_NAME"
    return 0
  fi

  local kind_config="$DEPLOY_DIR/kind/kind-config.yaml"
  if [ -f "$kind_config" ]; then
    assert_cmd_success "create Kind cluster before runtime image preload" \
      kind create cluster --name "$CLUSTER_NAME" --config "$kind_config"
  else
    assert_cmd_success "create Kind cluster before runtime image preload" \
      kind create cluster --name "$CLUSTER_NAME"
  fi
}

load_kind_runtime_image() {
  local image="$1"
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    skip "runtime image not present locally, cluster will try to pull: $image"
    return 0
  fi

  if run_cmd "kind load runtime image $image" kind load docker-image "$image" --name "$CLUSTER_NAME"; then
    pass "runtime image loaded: $image"
    return 0
  fi

  warn "kind load failed for $image; retrying with docker save | ctr import"
  if run_cmd "ctr import runtime image $image" bash -lc \
    "docker save '$image' | docker exec -i '${CLUSTER_NAME}-control-plane' ctr -n k8s.io images import -"; then
    pass "runtime image imported via ctr: $image"
    return 0
  fi

  fail "runtime image load failed: $image" "See $LAST_CMD_LOG"
  return 1
}

preload_kind_runtime_images() {
  if [ "$USE_KIND" != true ]; then
    skip "runtime image preload skipped in --no-kind mode"
    return 0
  fi

  ensure_kind_cluster_for_runtime_images || return 1
  local image
  for image in "${RUNTIME_IMAGES[@]}"; do
    load_kind_runtime_image "$image" || return 1
  done
}

setup_args_first() {
  local args=("${SETUP_EXTRA_ARGS[@]}")
  [ "$SETUP_SKIP_BUILD" = true ] && args+=(--skip-build)
  [ "${#args[@]}" -gt 0 ] && printf '%s\0' "${args[@]}"
}

run_setup_first() {
  local args=()
  while IFS= read -r -d '' item; do args+=("$item"); done < <(setup_args_first)
  assert_cmd_success "setup.sh first install ${args[*]:-(default args)}" \
    env CLUSTER_NAME="$CLUSTER_NAME" GATEWAY_PORT="$GATEWAY_PORT" \
    bash "$SCRIPT_DIR/setup.sh" "${args[@]}"
}

run_setup_skip_build() {
  local args=("${SETUP_EXTRA_ARGS[@]}" --skip-build)
  assert_cmd_success "setup.sh reinstall with --skip-build ${SETUP_EXTRA_ARGS[*]:-}" \
    env CLUSTER_NAME="$CLUSTER_NAME" GATEWAY_PORT="$GATEWAY_PORT" \
    bash "$SCRIPT_DIR/setup.sh" "${args[@]}"
}

assert_kind_cluster() {
  if [ "$USE_KIND" != true ]; then
    skip "Kind cluster assertion skipped in --no-kind mode"
    return 0
  fi
  if kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
    pass "Kind cluster exists: $CLUSTER_NAME"
  else
    fail "Kind cluster exists: $CLUSTER_NAME"
  fi
}

assert_docker_image() {
  local image="$1"
  if [ "$SETUP_SKIP_BUILD" = true ]; then
    skip "image build assertion skipped because first setup used --skip-build: $image"
    return 0
  fi
  if docker image inspect "$image" >/dev/null 2>&1; then
    pass "local Docker image exists: $image"
  else
    fail "local Docker image exists: $image"
  fi
}

assert_helm_release() {
  local release="$1"
  local ns="$2"
  if helm status "$release" -n "$ns" >/dev/null 2>&1; then
    pass "Helm release ready: $ns/$release"
  else
    fail "Helm release ready: $ns/$release"
  fi
}

assert_helm_release_absent() {
  local release="$1"
  local ns="$2"
  if helm status "$release" -n "$ns" >/dev/null 2>&1; then
    fail "Helm release absent: $ns/$release"
  else
    pass "Helm release absent: $ns/$release"
  fi
}

assert_namespace_absent() {
  local ns="$1"
  if kubectl get namespace "$ns" >/dev/null 2>&1; then
    fail "namespace absent: $ns"
  else
    pass "namespace absent: $ns"
  fi
}

assert_kubectl_wait() {
  local desc="$1"
  shift
  assert_cmd_success "$desc" kubectl "$@"
}

assert_secret_exists() {
  local ns="$1"
  local name="$2"
  if kubectl -n "$ns" get secret "$name" >/dev/null 2>&1; then
    pass "secret exists: $ns/$name"
  else
    fail "secret exists: $ns/$name"
  fi
}

collect_snapshot() {
  run_cmd "diagnostic snapshot: helm list" helm list -A || true
  run_cmd "diagnostic snapshot: pods" kubectl get pods -A -o wide || true
  run_cmd "diagnostic snapshot: httproutes" kubectl get httproute -A || true
  run_cmd "diagnostic snapshot: gateways" kubectl get gateway,gatewayclass -A || true
}

mock_config() {
  local selected="$1"
  if [ "$selected" = "auto" ]; then
    selected="memory"
  fi

  case "$selected" in
      MOCK_BACKEND="memory"
      MOCK_DISPLAY="mock-memory"
      MOCK_RELEASE="aidp-mock-memory"
      MOCK_NS="mock-memory"
      MOCK_IMAGE="mock-memory:v1"
      MOCK_CONTEXT="$REPO_DIR/tests/fixtures/mock-memory"
      MOCK_CHART="$REPO_DIR/deploy/helm/mocks/package-mock-memory/charts/aidp-mock-memory"
      MOCK_TEST="$REPO_DIR/deploy/helm/mocks/package-mock-memory/test/test.sh"
      MOCK_LABEL="app=mock-memory"
      MOCK_ROUTE_PATTERN='mock-memory|MemoryStore|memorystore'
      MOCK_SKIP_PATTERN='mock-memory route not found|MemoryStore backend tests will be skipped'
      MOCK_DETECTED_PATTERN='mock-memory route detected|MemoryStore tests will run'
      ;;
    dataagent)
      MOCK_BACKEND="dataagent"
      MOCK_DISPLAY="mock-dataagent"
      MOCK_RELEASE="aidp-mock-dataagent"
      MOCK_NS="mock-dataagent"
      MOCK_IMAGE="mock-dataagent:v1"
      MOCK_CONTEXT="$REPO_DIR/tests/fixtures/mock-dataagent"
      MOCK_CHART="$REPO_DIR/deploy/helm/mocks/package-mock-dataagent/charts/aidp-mock-dataagent"
      MOCK_TEST="$REPO_DIR/deploy/helm/mocks/package-mock-dataagent/test/test.sh"
      MOCK_LABEL="app=mock-dataagent"
      MOCK_ROUTE_PATTERN='mock-dataagent|DataAgent|dataagent'
      MOCK_SKIP_PATTERN='mock-dataagent route not found|DataAgent backend tests will be skipped'
      MOCK_DETECTED_PATTERN='mock-dataagent route detected|DataAgent tests will run'
      ;;
    none)
      INSTALL_MOCK=false
      MOCK_BACKEND="none"
      MOCK_DISPLAY=""
      return 0
      ;;
    *)
      fail "valid mock selection" "Unknown mock backend: $selected"
      return 1
      ;;
  esac

  [ -d "$MOCK_CONTEXT" ] || fail "$MOCK_DISPLAY context exists" "$MOCK_CONTEXT"
  [ -d "$MOCK_CHART" ] || fail "$MOCK_DISPLAY chart exists" "$MOCK_CHART"
  [ -f "$MOCK_TEST" ] || fail "$MOCK_DISPLAY test script exists" "$MOCK_TEST"
}

build_and_load_mock() {
  if [ "$INSTALL_MOCK" != true ]; then
    skip "mock install disabled"
    return 0
  fi

  if [ "$SKIP_MOCK_BUILD" = true ]; then
    skip "mock image build skipped by --skip-mock-build: $MOCK_IMAGE"
  else
    assert_cmd_success "build/rebuild $MOCK_IMAGE from $MOCK_CONTEXT" \
      docker build -t "$MOCK_IMAGE" "$MOCK_CONTEXT" || return 1
  fi

  if [ "$USE_KIND" = true ]; then
    assert_cmd_success "load $MOCK_IMAGE into Kind cluster $CLUSTER_NAME" \
      kind load docker-image "$MOCK_IMAGE" --name "$CLUSTER_NAME" || return 1
  else
    skip "mock image load skipped in --no-kind mode; image must be available to cluster nodes"
  fi
}

install_mock() {
  assert_cmd_success "helm upgrade --install $MOCK_RELEASE" \
    helm upgrade --install "$MOCK_RELEASE" "$MOCK_CHART" \
      --namespace "$MOCK_NS" --create-namespace --wait --timeout 5m || return 1

  assert_kubectl_wait "wait for $MOCK_DISPLAY pod Ready" \
    -n "$MOCK_NS" wait pod -l "$MOCK_LABEL" --for=condition=Ready --timeout=180s
}

assert_mock_route_present() {
  local out
  out="$(kubectl get httproute -A 2>/dev/null || true)"
  if printf '%s\n' "$out" | grep -qEi "$MOCK_ROUTE_PATTERN"; then
    pass "$MOCK_DISPLAY HTTPRoute present"
  else
    fail "$MOCK_DISPLAY HTTPRoute present" "$out"
  fi
}

run_core_test() {
  local desc="$1"
  assert_cmd_success "$desc" \
    env GATEWAY_PORT="$GATEWAY_PORT" bash "$SCRIPT_DIR/test.sh"
}

run_selected_mock_test() {
  local health_path protected_path
  case "$MOCK_BACKEND" in
    memory)
      health_path="/MemoryStore/health"
      protected_path="/MemoryStore/Tenants/aidp/Instances"
      ;;
    dataagent)
      health_path="/DataAgent/health"
      protected_path="/DataAgent/Tenants/aidp/Databases"
      ;;
    kb)
      health_path=""
      protected_path="/DataAgent/Tenants/aidp/Databases"
      ;;
    *)
      fail "$MOCK_DISPLAY smoke test configured" "No smoke paths for mock backend: $MOCK_BACKEND"
      return 1
      ;;
  esac

  if [ -n "$health_path" ]; then
    assert_http_code_match "$MOCK_DISPLAY health route via gateway" \
      '^200$' "https://localhost:$GATEWAY_PORT$health_path"
  else
    skip "$MOCK_DISPLAY has no unauthenticated health route through its protected prefix"
  fi

  assert_http_code_match "$MOCK_DISPLAY protected route rejects no-token request" \
    '^(401|403)$' "https://localhost:$GATEWAY_PORT$protected_path"
}

assert_http_code_match() {
  local desc="$1"
  local pattern="$2"
  local url="$3"
  assert_cmd_success "$desc" bash -lc '
    url="$1"
    pattern="$2"
    body="$(mktemp)"
    code="$(curl -k -sS --max-time 10 -o "$body" -w "%{http_code}" "$url" || true)"
    echo "url=$url"
    echo "expected_http_code_regex=$pattern"
    echo "actual_http_code=$code"
    echo "body_preview:"
    head -c 500 "$body" || true
    echo
    rm -f "$body"
    [[ "$code" =~ $pattern ]]
  ' _ "$url" "$pattern"
}

verify_cleanup_residue() {
  assert_helm_release_absent "aidp-iam" "aidp-iam"
  assert_helm_release_absent "aidp-gateway" "aidp-gateway"
  [ "$MOCK_BACKEND" = "memory" ] && assert_helm_release_absent "aidp-mock-memory" "mock-memory"
  [ "$MOCK_BACKEND" = "dataagent" ] && assert_helm_release_absent "aidp-mock-dataagent" "mock-dataagent"

  [ "$MOCK_BACKEND" = "memory" ] && assert_namespace_absent "mock-memory"
  [ "$MOCK_BACKEND" = "dataagent" ] && assert_namespace_absent "mock-dataagent"
  assert_namespace_absent "aidp-iam"
  assert_namespace_absent "keycloak"
  assert_namespace_absent "aidp-gateway"
  assert_namespace_absent "envoy-gateway-system"

  if kubectl -n aidp-gateway get gateway eg >/dev/null 2>&1; then
    fail "Gateway resource absent: aidp-gateway/eg"
  else
    pass "Gateway resource absent: aidp-gateway/eg"
  fi
  if kubectl get gatewayclass eg >/dev/null 2>&1; then
    fail "GatewayClass absent: eg"
  else
    pass "GatewayClass absent: eg"
  fi
}

maybe_cleanup_extra_mock() {
  case "$MOCK_BACKEND" in
    memory)
      run_cmd "cleanup extra mock-memory release" helm uninstall aidp-mock-memory -n mock-memory || true
      run_cmd "delete mock-memory namespace" kubectl delete namespace mock-memory --timeout=60s || true
      ;;
    dataagent)
      run_cmd "cleanup extra mock-dataagent release" helm uninstall aidp-mock-dataagent -n mock-dataagent || true
      run_cmd "delete mock-dataagent namespace" kubectl delete namespace mock-dataagent --timeout=60s || true
      ;;
  esac
}

print_summary() {
  printf "\n${BLUE}======================================================================${NC}\n"
  printf "${BLUE}INSTALL VALIDATION SUMMARY${NC}\n"
  printf "${BLUE}======================================================================${NC}\n"
  printf "  Result dir: %s\n" "$RESULT_DIR"
  printf "  Full log:   %s\n" "$LOG_FILE"
  printf "  Passed:     ${GREEN}%d${NC}\n" "$PASS"
  printf "  Failed:     ${RED}%d${NC}\n" "$FAIL"
  printf "  Skipped:    ${YELLOW}%d${NC}\n" "$SKIP"
  if [ "$FAIL" -eq 0 ]; then
    printf "  ${GREEN}ALL VALIDATION ITEMS PASSED${NC}\n"
  else
    printf "  ${RED}VALIDATION FAILED${NC}\n"
  fi
}

configure_python_path

case_start "PRECHECK: local command and script prerequisites" \
  "Validate that the runner can invoke the required local tools before making cluster changes."
require_cmd bash
require_cmd kubectl
require_cmd helm
require_cmd curl
require_cmd sed
require_cmd grep
if [ "$USE_KIND" = true ]; then require_cmd kind; fi
if [ "$SETUP_SKIP_BUILD" != true ] || [ "$INSTALL_MOCK" = true ]; then require_cmd docker; fi
if [ -n "$HOST_PYTHON" ] && "$HOST_PYTHON" -c "import sys" >/dev/null 2>&1; then
  pass "python interpreter executable: $HOST_PYTHON"
else
  fail "python interpreter executable"
fi
[ -f "$SCRIPT_DIR/cleanup.sh" ] && pass "cleanup.sh exists" || fail "cleanup.sh exists"
[ -f "$SCRIPT_DIR/setup.sh" ] && pass "setup.sh exists" || fail "setup.sh exists"
[ -f "$SCRIPT_DIR/test.sh" ] && pass "test.sh exists" || fail "test.sh exists"
mock_config "$MOCK_BACKEND"
case_end

if [ "$FAIL" -ne 0 ]; then
  warn "Precheck failed; stopping before any install/cleanup action."
  print_summary
  exit 1
fi

info "Result directory: $RESULT_DIR"
info "Selected mock backend: $MOCK_BACKEND"
info "Kind mode: $USE_KIND | Cluster: $CLUSTER_NAME | Gateway port: $GATEWAY_PORT"
info "Python interpreter: $HOST_PYTHON"

case_start "IT-AT-001: cleanup idempotency" \
  "Run cleanup twice. In Kind mode this deletes the target Kind cluster first, giving setup a clean start."
run_cleanup_delete_kind
run_cleanup_delete_kind
case_end

if [ "$FAIL" -ne 0 ]; then
  warn "Cleanup failed; stopping because subsequent install state would be ambiguous."
  print_summary
  exit 1
fi

case_start "IT-AT-002: Kind from-zero creation / setup entrypoint" \
  "Prepare the clean Kind cluster, preload local runtime images, then run setup.sh to install gateway + IAM."
if preload_kind_runtime_images && run_setup_first; then
  assert_kind_cluster
else
  collect_snapshot
fi
case_end

if [ "$FAIL" -ne 0 ]; then
  warn "First setup failed; stopping before functional tests."
  print_summary
  exit 1
fi

case_start "IT-AT-003: custom image build evidence" \
  "Check the four custom images built by setup.sh unless the first setup intentionally used --skip-build."
assert_docker_image "aidp-iam-app:v1"
assert_docker_image "keycloak-custom:26.5.2"
assert_docker_image "keycloak-init:v2"
assert_docker_image "gateway-manager:v1"
case_end

case_start "IT-AT-004: Gateway Helm install and dataplane readiness" \
  "Verify the gateway release, Gateway Programmed condition, controller, and generated Envoy dataplane."
assert_helm_release "aidp-gateway" "aidp-gateway"
assert_kubectl_wait "wait Gateway aidp-gateway/eg Programmed" \
  -n aidp-gateway wait gateway/eg --for=condition=Programmed --timeout=180s
assert_kubectl_wait "wait Envoy Gateway controller Ready" \
  -n aidp-gateway wait pod -l control-plane=envoy-gateway --for=condition=Ready --timeout=180s
assert_kubectl_wait "wait Envoy dataplane Ready" \
  -n aidp-gateway wait pod -l gateway.envoyproxy.io/owning-gateway-name=eg --for=condition=Ready --timeout=180s
case_end

case_start "IT-AT-005: IAM Helm install and core pod readiness" \
  "Verify IAM release, Keycloak/Postgres StatefulSets, iam-services deployment, and client secret."
assert_helm_release "aidp-iam" "aidp-iam"
assert_kubectl_wait "rollout status postgres" -n keycloak rollout status statefulset/iam-store --timeout=240s
assert_kubectl_wait "rollout status keycloak" -n keycloak rollout status statefulset/keycloak --timeout=240s
assert_kubectl_wait "rollout status iam-services" -n aidp-iam rollout status deployment/iam-services --timeout=240s
assert_secret_exists "aidp-iam" "keycloak-aidp-client"
case_end

case_start "IT-AT-006: core IAM test suite" \
  "Run deploy/scripts/test.sh and require exit code 0."
if run_core_test "core IAM E2E test.sh before mock install"; then
  CORE_TEST_BEFORE_LOG="$LAST_CMD_LOG"
else
  CORE_TEST_BEFORE_LOG="$LAST_CMD_LOG"
  collect_snapshot
fi
case_end

case_start "IT-AT-007: mock backend absent is reported as SKIP" \
  "Before installing the selected mock, the core test output should explicitly show the selected mock route is absent/skipped."
if [ "$INSTALL_MOCK" = true ]; then
  assert_file_contains "$MOCK_DISPLAY absent/skip reported by core test" "$CORE_TEST_BEFORE_LOG" "$MOCK_SKIP_PATTERN"
else
  skip "mock skip assertion disabled by --no-mock"
fi
case_end

case_start "IT-AT-008: selected mock backend install and gateway smoke validation" \
  "Build/load one mock backend, install its Helm chart, verify route detection, then run gateway smoke checks."
if [ "$INSTALL_MOCK" = true ]; then
  if build_and_load_mock && install_mock; then
    assert_mock_route_present
    if run_core_test "core IAM E2E test.sh after $MOCK_DISPLAY install"; then
      CORE_TEST_AFTER_MOCK_LOG="$LAST_CMD_LOG"
      assert_file_contains "$MOCK_DISPLAY detected by core test" "$CORE_TEST_AFTER_MOCK_LOG" "$MOCK_DETECTED_PATTERN"
      assert_file_not_contains "$MOCK_DISPLAY no longer reported as absent" "$CORE_TEST_AFTER_MOCK_LOG" "$MOCK_DISPLAY route not found"
    else
      collect_snapshot
    fi
    run_selected_mock_test || collect_snapshot
  else
    collect_snapshot
  fi
else
  skip "mock install disabled by --no-mock"
fi
case_end

case_start "IT-AT-009: cleanup residue check" \
  "Run cleanup while preserving the cluster, then verify releases, namespaces, Gateway, and GatewayClass are gone."
CLEANUP_RESIDUE_FAIL_START=$CURRENT_CASE_FAIL_START
maybe_cleanup_extra_mock
run_cleanup_keep_cluster
verify_cleanup_residue
case_end

if [ "$RUN_REINSTALL" = true ] && [ "$FAIL" -ne "$CLEANUP_RESIDUE_FAIL_START" ]; then
  warn "Cleanup residue check failed; stopping before skip-build reinstall."
  print_summary
  exit 1
fi

if [ "$RUN_REINSTALL" = true ]; then
  case_start "IT-AT-010: reinstall with --skip-build" \
    "Reinstall on the existing cluster using setup.sh --skip-build. This validates the no-rebuild path after cleanup."
  REINSTALL_FAIL_START=$CURRENT_CASE_FAIL_START
  if run_setup_skip_build; then
    assert_helm_release "aidp-gateway" "aidp-gateway"
    assert_helm_release "aidp-iam" "aidp-iam"
    assert_kubectl_wait "rollout status iam-services after skip-build reinstall" \
      -n aidp-iam rollout status deployment/iam-services --timeout=240s
  else
    collect_snapshot
  fi
  case_end

  if [ "$FAIL" -ne "$REINSTALL_FAIL_START" ]; then
    warn "Skip-build reinstall failed; stopping before second core test."
    print_summary
    exit 1
  fi

  case_start "IT-AT-011: second core test after skip-build reinstall" \
    "Run deploy/scripts/test.sh again after the skip-build reinstall."
  if ! run_core_test "core IAM E2E test.sh after skip-build reinstall"; then
    collect_snapshot
  fi
  case_end
else
  case_start "IT-AT-010/011: skip-build reinstall disabled" \
    "The caller passed --skip-reinstall, so reinstall and second-pass validation are intentionally skipped."
  skip "IT-AT-010 skipped by --skip-reinstall"
  skip "IT-AT-011 skipped by --skip-reinstall"
  case_end
fi

case_start "IT-AT-012: failure-location evidence" \
  "Optional non-destructive negative run. It uses an invalid gateway port and expects test.sh to fail with visible errors."
if [ "$INCLUDE_FAILURE_DEMO" = true ]; then
  if assert_cmd_failure "controlled failure: test.sh with invalid GATEWAY_PORT=1" \
    env GATEWAY_PORT=1 bash "$SCRIPT_DIR/test.sh"; then
    FAILURE_DEMO_LOG="$LAST_CMD_LOG"
    assert_file_contains "controlled failure output contains FAIL/FATAL/error evidence" \
      "$FAILURE_DEMO_LOG" 'FAIL|FATAL|ERROR|failed|cannot|get token|000'
  fi
else
  skip "failure-location demo disabled by default; pass --include-failure-demo to run it"
fi
case_end

if [ "$FINAL_CLEANUP" = true ]; then
  case_start "FINAL: cleanup after validation" \
    "Run cleanup.sh one final time because --final-cleanup was requested."
  run_cleanup_keep_cluster
  case_end
fi

print_summary
[ "$FAIL" -eq 0 ]
