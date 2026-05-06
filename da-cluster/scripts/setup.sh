#!/usr/bin/env bash
# ============================================================================
# setup.sh — Full cluster setup: build images → deploy Helm chart → init seed data.
#
# Usage:
#   ./scripts/setup.sh                        # Kind cluster (create + deploy)
#   ./scripts/setup.sh --no-kind              # Deploy to existing K8s (air-gapped / prod)
#   ./scripts/setup.sh --no-kind --fat-base   # Rebuild images only (no pip/apt)
#   ./scripts/setup.sh --skip-build           # Skip image build, deploy only
#   ./scripts/setup.sh --skip-init            # Skip keycloak-init Job
#   ./scripts/setup.sh --arch arm64           # Cross-build for arm64
#
# Environment:
#   CLUSTER_NAME    Kind cluster name (default: da-cluster)
#   K8S_NODES       Space-separated node IPs for --no-kind mode
#   K8S_NODE_USER   SSH user for K8s nodes (default: root)
#   ARCH            Target arch: amd64 | arm64 (default: host arch)
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
AUTH_DIR="$(cd "$PROJECT_DIR/.." && pwd)"

# ── Colours ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
log()     { echo -e "${GREEN}[INFO]${NC}  $*"; }
section() { echo -e "\n${CYAN}══ $* ══${NC}"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()     { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Defaults ─────────────────────────────────────────────────────────────────
CLUSTER_NAME="${CLUSTER_NAME:-da-cluster}"
K8S_NODE_USER="${K8S_NODE_USER:-root}"
ARCH="${ARCH:-$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')}"
IAM_NS="aidp-iam"
KEYCLOAK_NS="keycloak"

USE_KIND=true
SKIP_BUILD=false
SKIP_INIT=false
FAT_BASE=false

# ── Parse args ───────────────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
  case "$1" in
    --no-kind)     USE_KIND=false;   shift ;;
    --skip-build)  SKIP_BUILD=true;  shift ;;
    --skip-init)   SKIP_INIT=true;   shift ;;
    --fat-base)    FAT_BASE=true;    shift ;;
    --arch)        ARCH="$2";        shift 2 ;;
    -h|--help)
      sed -n '/^# ===/,/^# ===/p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) err "Unknown argument: $1 (use --help)" ;;
  esac
done

log "Arch: $ARCH | Kind: $USE_KIND | Skip build: $SKIP_BUILD | Skip init: $SKIP_INIT"

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
  if [ "$ARCH" = "$host_arch" ] && [ "$FAT_BASE" = false ]; then
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
  if [ -d "$AUTH_DIR/opal-dynamic-policy/data" ]; then
    cp -r "$AUTH_DIR/opal-dynamic-policy/data" "$CTX/bundle-server/data"
  else
    mkdir -p "$CTX/bundle-server/data"
  fi
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
# STEP 3: Install offline CRDs (Gateway API experimental channel)
# ════════════════════════════════════════════════════════════════════════════
section "Step 3: Install Gateway API CRDs"

CRDS_DIR="$PROJECT_DIR/offline/crds"
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
# STEP 4: Helm install / upgrade aidp-iam umbrella chart
# ════════════════════════════════════════════════════════════════════════════
section "Step 4: Helm deploy aidp-iam"

CHART_DIR="$PROJECT_DIR/charts/aidp-iam"
HELM_RELEASE="aidp-iam"

# Ensure chart dependencies (sub-charts) are present
if [ -d "$CHART_DIR/charts" ] && ls "$CHART_DIR/charts"/*.tgz >/dev/null 2>&1; then
  log "Sub-chart tarballs found in $CHART_DIR/charts — skipping helm dep update."
else
  log "Running helm dependency update..."
  helm dependency update "$CHART_DIR" \
    || warn "helm dep update failed — sub-charts may be missing."
fi

# Install or upgrade
if helm status "$HELM_RELEASE" -n "$IAM_NS" >/dev/null 2>&1; then
  log "Upgrading existing Helm release '$HELM_RELEASE'..."
  helm upgrade "$HELM_RELEASE" "$CHART_DIR" \
    --namespace "$IAM_NS" \
    --create-namespace \
    --reuse-values \
    --timeout 10m \
    --wait
else
  log "Installing Helm release '$HELM_RELEASE'..."
  helm install "$HELM_RELEASE" "$CHART_DIR" \
    --namespace "$IAM_NS" \
    --create-namespace \
    --timeout 10m \
    --wait
fi

log "Helm release '$HELM_RELEASE' deployed."

# ════════════════════════════════════════════════════════════════════════════
# STEP 5: Wait for core pods to be ready
# ════════════════════════════════════════════════════════════════════════════
section "Step 5: Wait for pods"

wait_deploy() {
  local ns="$1"; local label="$2"; local timeout="${3:-300s}"
  log "  Waiting for $label in $ns..."
  kubectl -n "$ns" wait pod --for=condition=Ready -l "$label" \
    --timeout="$timeout" 2>/dev/null \
    || warn "  Timeout waiting for $label in $ns"
}

wait_deploy "$KEYCLOAK_NS"          "app=keycloak"
wait_deploy "envoy-gateway-system"  "app.kubernetes.io/name=gateway-helm"
wait_deploy "$IAM_NS"               "app=iam-services"
wait_deploy "$IAM_NS"               "app=opa"

# ════════════════════════════════════════════════════════════════════════════
# STEP 6: Run keycloak-init Job (seed data)
# ════════════════════════════════════════════════════════════════════════════
section "Step 6: Keycloak init"

if [ "$SKIP_INIT" = true ]; then
  log "Skipping keycloak-init (--skip-init)."
else
  log "Waiting for Keycloak to be ready before init..."
  kubectl -n "$KEYCLOAK_NS" wait pod --for=condition=Ready -l app=keycloak \
    --timeout=300s 2>/dev/null \
    || warn "Keycloak not ready after 5m — init may fail."

  log "Deleting any previous keycloak-init Job..."
  kubectl -n "$KEYCLOAK_NS" delete job keycloak-init 2>/dev/null || true

  log "Triggering keycloak-init Job via helm upgrade --reuse-values..."
  helm upgrade "$HELM_RELEASE" "$CHART_DIR" \
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
# STEP 7: Register app manifests (AccessManager)
# ════════════════════════════════════════════════════════════════════════════
section "Step 7: Register app manifests"

MANIFEST_DIR="$AUTH_DIR/diagrams"
GATEWAY_PORT="${GATEWAY_PORT:-30080}"
IAM_BASE="http://localhost:${GATEWAY_PORT}"

# Wait for iam-services to be ready before registering manifests
kubectl -n "$IAM_NS" rollout status deployment/iam-services --timeout=120s 2>/dev/null \
  || warn "iam-services not ready — skipping manifest registration."

# Get admin token for manifest registration
REALM="${REALM:-aidp}"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASS="${ADMIN_PASS:-Admin@123}"
CLIENT_ID="${CLIENT_ID:-aidp-client}"

TOKEN=$(curl -s -X POST \
  "${IAM_BASE}/realms/${REALM}/protocol/openid-connect/token" \
  -d "grant_type=password&client_id=${CLIENT_ID}&username=${ADMIN_USER}&password=${ADMIN_PASS}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || true)

if [ -z "$TOKEN" ]; then
  warn "Could not obtain admin token — skipping manifest registration."
  warn "Run manually: PUT ${IAM_BASE}/AccessManager/Tenants/System/AppManifests/{namespace}"
else
  # Register each manifest.json found under diagrams/ or manifests/
  for mf in "$MANIFEST_DIR"/manifest-*.json "$AUTH_DIR"/*/manifest.json; do
    [ -f "$mf" ] || continue
    NS=$(python3 -c "import json; d=json.load(open('$mf')); print(d.get('namespace',''))" 2>/dev/null || true)
    BASE_URL=$(python3 -c "import json; d=json.load(open('$mf')); print(d.get('base_url',''))" 2>/dev/null || true)
    [ -z "$NS" ] && continue
    log "  Registering manifest: $NS"
    curl -s -X PUT \
      "${IAM_BASE}/AccessManager/Tenants/System/AppManifests/${NS}" \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"base_url\":\"${BASE_URL}\",\"manifest_json\":$(cat "$mf")}" \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  → {d.get(\"namespace\",\"?\")} acls_synced={d.get(\"acls_synced\",\"?\")}' )" 2>/dev/null \
      || warn "  Manifest registration failed for $NS"
  done
fi

# ════════════════════════════════════════════════════════════════════════════
# Done
# ════════════════════════════════════════════════════════════════════════════
section "Setup complete"
echo ""
log "Gateway NodePort : http://localhost:${GATEWAY_PORT}"
log "Keycloak console : http://localhost:${GATEWAY_PORT}/auth/admin"
log "IAM API          : http://localhost:${GATEWAY_PORT}/AccessManager/Tenants/${REALM}/Users"
echo ""
log "Quick checks:"
log "  kubectl -n $IAM_NS      get pod"
log "  kubectl -n $KEYCLOAK_NS get pod"
log "  kubectl -n envoy-gateway-system get pod"
echo ""
log "Run tests:"
log "  ./scripts/test.sh"
