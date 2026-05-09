#!/usr/bin/env bash
# ============================================================================
# setup.sh — Full cluster setup: build images → deploy Helm charts → init seed data.
#
# Two-step Helm deploy:
#   1. aidp-gateway  (Envoy Gateway controller + CRDs + Gateway/EnvoyProxy)
#   2. aidp-iam      (Keycloak + Postgres + 4-in-1 IAM services + OPA + routes)
#
# Usage:
#   ./scripts/setup.sh                        # Kind cluster (create + deploy)
#   ./scripts/setup.sh --no-kind              # Deploy to existing K8s
#   ./scripts/setup.sh --skip-build           # Skip image build, deploy only
#   ./scripts/setup.sh --skip-init            # Skip keycloak-init Job
#   ./scripts/setup.sh --arch arm64           # Cross-build for arm64
#
# Environment:
#   CLUSTER_NAME    Kind cluster name (default: da-cluster)
#   K8S_NODES       Space-separated node IPs for --no-kind mode
#   K8S_NODE_USER   SSH user for K8s nodes (default: root)
#   ARCH            Target arch: amd64 | arm64 (default: host arch)
#   GATEWAY_PORT    NodePort exposed by Envoy (default: 30080)
#   KEYCLOAK_HOST   Keycloak public URL passed to helm (default: http://localhost:GATEWAY_PORT)
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
AUTH_DIR="$(cd "$PROJECT_DIR/.." && pwd)"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
log()     { echo -e "${GREEN}[INFO]${NC}  $*"; }
section() { echo -e "\n${CYAN}══ $* ══${NC}"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()     { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Defaults ─────────────────────────────────────────────────────────────────
CLUSTER_NAME="${CLUSTER_NAME:-da-cluster}"
K8S_NODE_USER="${K8S_NODE_USER:-root}"
ARCH="${ARCH:-$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')}"
GATEWAY_PORT="${GATEWAY_PORT:-30080}"
IAM_NS="aidp-iam"
KEYCLOAK_NS="keycloak"

USE_KIND=true
SKIP_BUILD=false
SKIP_INIT=false

# ── Parse args ───────────────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
  case "$1" in
    --no-kind)     USE_KIND=false;   shift ;;
    --skip-build)  SKIP_BUILD=true;  shift ;;
    --skip-init)   SKIP_INIT=true;   shift ;;
    --arch)        ARCH="$2";        shift 2 ;;
    -h|--help)
      sed -n '/^# ===/,/^# ===/p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) err "Unknown argument: $1 (use --help)" ;;
  esac
done

KEYCLOAK_HOST="${KEYCLOAK_HOST:-http://localhost:${GATEWAY_PORT}}"
log "Arch: $ARCH | Kind: $USE_KIND | Skip build: $SKIP_BUILD | Skip init: $SKIP_INIT"
log "Keycloak hostname: $KEYCLOAK_HOST"

# ── Helper: load image into cluster runtime ───────────────────────────────────
image_to_filename() { echo "$1" | sed 's|/|_|g; s|:|_|g'; }

load_image() {
  local img="$1"
  if [ "$USE_KIND" = true ]; then
    log "  kind load: $img"
    kind load docker-image "$img" --name "$CLUSTER_NAME" 2>/dev/null \
      || err "kind load failed for $img"
    return
  fi
  local fname; fname="$(image_to_filename "$img").tar"
  local tar; tar=$(mktemp)
  docker save -o "$tar" "$img"
  if [ -n "${K8S_NODES:-}" ]; then
    for node in $K8S_NODES; do
      log "  scp $img → $node"
      scp -q "$tar" "${K8S_NODE_USER}@${node}:/tmp/$fname"
      ssh "${K8S_NODE_USER}@${node}" \
        "ctr -n k8s.io images import /tmp/$fname && rm -f /tmp/$fname" \
        || warn "  ctr import failed on $node"
    done
  elif command -v ctr >/dev/null 2>&1; then
    ctr -n k8s.io images import "$tar" 2>/dev/null \
      || err "ctr import failed for $img"
  else
    err "No image loader available. Set K8S_NODES or install ctr."
  fi
  rm -f "$tar"
}

# ── Helper: docker build (native or cross-arch via buildx) ───────────────────
docker_build() {
  local tag="$1"; local ctx="$2"
  local host_arch; host_arch=$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')
  if [ "$ARCH" = "$host_arch" ]; then
    docker build -t "$tag" "$ctx"
  else
    local ctx_path; ctx_path=$(cygpath -m "$ctx" 2>/dev/null || echo "$ctx")
    docker buildx build --platform="linux/$ARCH" --load -t "$tag" "$ctx_path"
  fi
}

# ════════════════════════════════════════════════════════════════════════════
# STEP 1: Create Kind cluster (if needed)
# ════════════════════════════════════════════════════════════════════════════
if [ "$USE_KIND" = true ]; then
  section "Step 1: Kind cluster"
  if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
    log "Kind cluster '$CLUSTER_NAME' already exists — skipping creation."
  else
    log "Creating Kind cluster '$CLUSTER_NAME'..."
    KIND_CONFIG="$PROJECT_DIR/kind-config.yaml"
    if [ -f "$KIND_CONFIG" ]; then
      kind create cluster --name "$CLUSTER_NAME" --config "$KIND_CONFIG"
    else
      kind create cluster --name "$CLUSTER_NAME"
    fi
    log "Kind cluster created."
  fi
else
  section "Step 1: Using existing K8s cluster"
  kubectl cluster-info || err "kubectl cannot reach the cluster. Check your kubeconfig."
fi

# ════════════════════════════════════════════════════════════════════════════
# STEP 2: Build images
# ════════════════════════════════════════════════════════════════════════════
section "Step 2: Build images"

if [ "$SKIP_BUILD" = true ]; then
  log "Skipping image build (--skip-build)."
else
  # ── 2a. aidp-iam-app:v1 (4-in-1: keycloak-proxy + pep-proxy + bundle-server + resource-sync)
  log "Building aidp-iam-app:v1..."
  CTX=$(mktemp -d)
  trap 'rm -rf "$CTX"' EXIT

  cp "$PROJECT_DIR/images/aidp-iam-app/Dockerfile"       "$CTX/Dockerfile"
  cp "$PROJECT_DIR/images/aidp-iam-app/supervisord.conf" "$CTX/"
  cp "$PROJECT_DIR/images/aidp-iam-app/requirements.txt" "$CTX/"

  mkdir -p "$CTX/keycloak-proxy" "$CTX/pep-proxy" "$CTX/bundle-server" "$CTX/resource-sync"
  cp -r "$AUTH_DIR/da-idb-proxy/app"                       "$CTX/keycloak-proxy/app"
  cp -r "$AUTH_DIR/opal-dynamic-policy/pep-proxy/app"      "$CTX/pep-proxy/app"
  cp -r "$AUTH_DIR/opal-dynamic-policy/pep-proxy/proto"    "$CTX/pep-proxy/proto"
  cp -r "$AUTH_DIR/opal-dynamic-policy/bundle-server/app"  "$CTX/bundle-server/app"
  mkdir -p "$CTX/bundle-server/data"
  cp -r "$AUTH_DIR/resource-sync/app"   "$CTX/resource-sync/app"
  cp -r "$AUTH_DIR/resource-sync/proto" "$CTX/resource-sync/proto"

  docker_build "aidp-iam-app:v1" "$CTX"
  trap - EXIT
  rm -rf "$CTX"
  log "aidp-iam-app:v1 built."

  # ── 2b. keycloak-init:v2
  log "Building keycloak-init:v2..."
  docker_build "keycloak-init:v2" "$PROJECT_DIR/images/keycloak-init"
  log "keycloak-init:v2 built."

  # ── 2c. Load images into cluster
  section "Step 2c: Load images into cluster"
  load_image "aidp-iam-app:v1"
  load_image "keycloak-init:v2"
fi

# ════════════════════════════════════════════════════════════════════════════
# STEP 3: Install Gateway API + Envoy Gateway CRDs
# ════════════════════════════════════════════════════════════════════════════
section "Step 3: Install CRDs"

CRDS_DIR="$AUTH_DIR/package-gateway/charts/aidp-gateway/crds"
if [ -d "$CRDS_DIR" ] && ls "$CRDS_DIR"/*.yaml >/dev/null 2>&1; then
  log "Applying CRDs from $CRDS_DIR..."
  for crd in "$CRDS_DIR"/*.yaml; do
    kubectl apply -f "$crd" --server-side 2>/dev/null \
      || kubectl apply -f "$crd" \
      || warn "CRD apply failed: $crd"
  done
  log "CRDs applied."
else
  warn "No CRDs found in $CRDS_DIR — skipping. Gateway API CRDs must be pre-installed."
fi

# ════════════════════════════════════════════════════════════════════════════
# STEP 4: Deploy aidp-gateway (Envoy Gateway controller + Gateway resources)
# ════════════════════════════════════════════════════════════════════════════
section "Step 4: Helm deploy aidp-gateway"

GATEWAY_CHART="$AUTH_DIR/package-gateway/charts/aidp-gateway"
GATEWAY_RELEASE="aidp-gateway"

if helm status "$GATEWAY_RELEASE" -n "$IAM_NS" >/dev/null 2>&1; then
  log "Upgrading existing Helm release '$GATEWAY_RELEASE'..."
  helm upgrade "$GATEWAY_RELEASE" "$GATEWAY_CHART" \
    --namespace "$IAM_NS" \
    --reuse-values \
    --timeout 5m \
    --wait
else
  log "Installing Helm release '$GATEWAY_RELEASE'..."
  helm install "$GATEWAY_RELEASE" "$GATEWAY_CHART" \
    --namespace "$IAM_NS" \
    --create-namespace \
    --set proxy.service.nodePort="$GATEWAY_PORT" \
    --timeout 5m \
    --wait
fi

log "Waiting for Envoy Gateway controller..."
kubectl -n "$IAM_NS" wait pod \
  --for=condition=Ready \
  -l control-plane=envoy-gateway \
  --timeout=120s 2>/dev/null \
  || warn "Envoy Gateway controller not ready after 2m"

# ════════════════════════════════════════════════════════════════════════════
# STEP 5: Deploy aidp-iam (Keycloak + IAM services + OPA + routes)
# ════════════════════════════════════════════════════════════════════════════
section "Step 5: Helm deploy aidp-iam"

IAM_CHART="$AUTH_DIR/package-iam/charts/aidp-iam"
IAM_RELEASE="aidp-iam"

helm_iam_args=(
  --namespace "$IAM_NS"
  --create-namespace
  --timeout 10m
  --wait
  --set "keycloak.keycloak.config.hostname=$KEYCLOAK_HOST"
)

if helm status "$IAM_RELEASE" -n "$IAM_NS" >/dev/null 2>&1; then
  log "Upgrading existing Helm release '$IAM_RELEASE'..."
  helm upgrade "$IAM_RELEASE" "$IAM_CHART" \
    --reuse-values \
    "${helm_iam_args[@]}"
else
  log "Installing Helm release '$IAM_RELEASE'..."
  helm install "$IAM_RELEASE" "$IAM_CHART" \
    "${helm_iam_args[@]}"
fi

log "Helm release '$IAM_RELEASE' deployed."

# ════════════════════════════════════════════════════════════════════════════
# STEP 6: Wait for core pods
# ════════════════════════════════════════════════════════════════════════════
section "Step 6: Wait for pods"

wait_pod() {
  local ns="$1"; local label="$2"; local timeout="${3:-300s}"
  log "  Waiting for $label in $ns..."
  kubectl -n "$ns" wait pod --for=condition=Ready -l "$label" \
    --timeout="$timeout" 2>/dev/null \
    || warn "  Timeout waiting for $label in $ns"
}

wait_pod "$KEYCLOAK_NS" "app=keycloak"
wait_pod "$IAM_NS"      "app=iam-services"

# ════════════════════════════════════════════════════════════════════════════
# STEP 7: Run keycloak-init Job (seed data)
# ════════════════════════════════════════════════════════════════════════════
section "Step 7: Keycloak init"

if [ "$SKIP_INIT" = true ]; then
  log "Skipping keycloak-init (--skip-init)."
else
  log "Waiting for Keycloak to be ready..."
  kubectl -n "$KEYCLOAK_NS" wait pod --for=condition=Ready -l app=keycloak \
    --timeout=300s 2>/dev/null \
    || warn "Keycloak not ready after 5m — init may fail."

  log "Deleting any previous keycloak-init Job..."
  kubectl -n "$KEYCLOAK_NS" delete job keycloak-init 2>/dev/null || true

  log "Triggering keycloak-init Job via helm upgrade --reuse-values..."
  helm upgrade "$IAM_RELEASE" "$IAM_CHART" \
    -n "$IAM_NS" \
    --reuse-values \
    --timeout 5m \
    2>/dev/null \
    || warn "helm upgrade for init trigger failed — check Job manually."

  log "Waiting for keycloak-init Job to complete (up to 5m)..."
  kubectl -n "$KEYCLOAK_NS" wait --for=condition=complete job/keycloak-init \
    --timeout=5m \
    || warn "keycloak-init Job did not complete in 5m — check logs:"
  kubectl -n "$KEYCLOAK_NS" logs job/keycloak-init --tail=30 2>/dev/null || true
fi

# ════════════════════════════════════════════════════════════════════════════
# Done
# ════════════════════════════════════════════════════════════════════════════
section "Setup complete"
echo ""
log "Gateway NodePort : http://localhost:${GATEWAY_PORT}"
log "Keycloak console : http://localhost:${GATEWAY_PORT}/realms/master/account"
log "IAM API          : http://localhost:${GATEWAY_PORT}/api/v1/common/health"
echo ""
log "Quick checks:"
log "  kubectl -n $IAM_NS      get pod"
log "  kubectl -n $KEYCLOAK_NS get pod"
log "  kubectl -n $IAM_NS      get gateway eg"
echo ""
log "Run tests:"
log "  ./scripts/test.sh"
