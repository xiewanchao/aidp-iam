#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# setup-isula.sh — Deploy da-cluster IAM on Huawei Cloud K8s (isula runtime)
#
# Prerequisites:
#   - kubectl (configured with kubeconfig)
#   - helm 3
#   - isula (on each node, only when --load-images is used)
#
# NO internet access required. NO Kind. NO build. NO httpbin.
#
# Usage:
#   ./scripts/setup-isula.sh                          # Deploy only (images already loaded)
#   ./scripts/setup-isula.sh --load-images            # Load images from offline/ then deploy
#   ./scripts/setup-isula.sh --with-mocks             # Also deploy mock-kb / mock-rubik / mock-memory
#                                                      (for end-to-end testing without a real backend)
#   ./scripts/setup-isula.sh --help                   # Show help
#
# Environment variables:
#   KC_HOSTNAME    — Keycloak external hostname (e.g. http://EIP:30080)
#   STORAGE_CLASS  — StorageClass for PVC (e.g. dorado-inner-nas, default: auto-detect)
#   PLATFORM       — amd64 or arm64 (default: auto-detect)
#   K8S_NODES      — space-separated node IPs for image loading via SSH
#   K8S_NODE_USER  — SSH user for nodes (default: root)
#   IMAGE_DIR      — remote temp dir for image tars (default: /tmp/da-images)
#   KUBECONFIG     — path to kubeconfig (default: ~/.kube/config)
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OFFLINE_DIR="$PROJECT_DIR/offline"

KEYCLOAK_NS="keycloak"
OPA_NS="opa"
RESOURCE_SYNC_NS="resource-sync"
# Where the Envoy Gateway controller + data plane Deployments live. Aligned
# with the umbrella chart (setup.sh) so test.sh's default
# ENVOY_GATEWAY_NS=aidp-iam works without override.
ENVOY_GATEWAY_NS="aidp-iam"
# Where the Gateway / HTTPRoute / *Policy CRs live — the namespace is
# hardcoded in charts/envoy-gateway/templates/*.yaml and
# gateway-routes/*.yaml, so we must ensure this ns exists before applying.
GATEWAY_CR_NS="envoy-gateway-system"

ENVOY_GATEWAY_CHART_VERSION="v1.7.0"
GATEWAY_API_VERSION="v1.4.1-experimental"
ENVOY_GATEWAY_CRDS=(
  "gateway.envoyproxy.io_backends.yaml"
  "gateway.envoyproxy.io_backendtrafficpolicies.yaml"
  "gateway.envoyproxy.io_clienttrafficpolicies.yaml"
  "gateway.envoyproxy.io_envoyextensionpolicies.yaml"
  "gateway.envoyproxy.io_envoypatchpolicies.yaml"
  "gateway.envoyproxy.io_envoyproxies.yaml"
  "gateway.envoyproxy.io_httproutefilters.yaml"
  "gateway.envoyproxy.io_securitypolicies.yaml"
)

K8S_NODE_USER="${K8S_NODE_USER:-root}"
IMAGE_DIR="${IMAGE_DIR:-/tmp/da-images}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Parse arguments ─────────────────────────────────────────────────────
LOAD_IMAGES=false
WITH_MOCKS=false
for arg in "$@"; do
  case "$arg" in
    --load-images)  LOAD_IMAGES=true ;;
    --with-mocks)   WITH_MOCKS=true ;;
    --help|-h)
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Deploy da-cluster IAM system to an existing Huawei Cloud K8s cluster."
      echo "Designed for isula container runtime. No Kind, no build, no httpbin."
      echo ""
      echo "Options:"
      echo "  --load-images     Load images from offline/images/ via isula before deploying."
      echo "                    Without this flag, images are assumed to be already loaded."
      echo "  --with-mocks      Also deploy mock-kb / mock-rubik / mock-memory + their HTTPRoutes."
      echo "                    Useful for end-to-end testing without a real backend."
      echo "  --help, -h        Show this help message."
      echo ""
      echo "Environment variables:"
      echo "  KC_HOSTNAME       Keycloak external URL (e.g. http://1.2.3.4:30080)"
      echo "                    Required for OIDC redirects to work correctly."
      echo "  STORAGE_CLASS     StorageClass for PostgreSQL PVC (e.g. dorado-inner-nas)"
      echo "                    Auto-detected from cluster if not set."
      echo "  PLATFORM          Force platform: amd64 or arm64 (default: auto-detect)"
      echo "  K8S_NODES         Space-separated node IPs for multi-node image loading via SSH"
      echo "                    (only used with --load-images)"
      echo "  K8S_NODE_USER     SSH user for nodes (default: root)"
      echo "  IMAGE_DIR         Remote temp dir for image tars (default: /tmp/da-images)"
      echo "  KUBECONFIG        Path to kubeconfig (default: ~/.kube/config)"
      echo ""
      echo "Examples:"
      echo "  # Deploy (images pre-loaded):"
      echo "  KC_HOSTNAME=http://80.10.79.111:30080 STORAGE_CLASS=dorado-inner-nas $0"
      echo ""
      echo "  # Load images first, then deploy:"
      echo "  KC_HOSTNAME=http://80.10.79.111:30080 $0 --load-images"
      echo ""
      echo "  # Multi-node image loading:"
      echo "  K8S_NODES=\"10.0.0.1 10.0.0.2\" KC_HOSTNAME=http://EIP:30080 $0 --load-images"
      exit 0
      ;;
    *) err "Unknown argument: $arg (use --help)" ;;
  esac
done

# ── Platform detection ────────────────────────────────────────────────────
if [ -z "${PLATFORM:-}" ]; then
  ARCH=$(uname -m)
  case "$ARCH" in
    x86_64|amd64)   PLATFORM="amd64" ;;
    aarch64|arm64)   PLATFORM="arm64" ;;
    *)               PLATFORM="amd64"; warn "Unknown arch '$ARCH', defaulting to amd64" ;;
  esac
fi
log "Platform: $PLATFORM"

# ── Pre-flight checks ─────────────────────────────────────────────────────
log "Pre-flight: checking resources..."
[ -d "$OFFLINE_DIR/charts" ] || err "Missing $OFFLINE_DIR/charts/ — run ./scripts/build-release-images.sh --offline-only first"
[ -d "$OFFLINE_DIR/crds" ]   || err "Missing $OFFLINE_DIR/crds/ — run ./scripts/build-release-images.sh --offline-only first"

if [ "$LOAD_IMAGES" = true ]; then
  IMAGES_DIR="$OFFLINE_DIR/images/$PLATFORM"
  [ -d "$IMAGES_DIR" ] || err "Missing $IMAGES_DIR/ — run ./scripts/build-release-images.sh first"
fi

for cmd in kubectl helm; do
  command -v "$cmd" &>/dev/null || err "'$cmd' not found in PATH"
done

# ── Helper: convert image name to tar filename ─────────────────────────
image_to_filename() {
  echo "$1" | sed 's|/|_|g; s|:|_|g'
}

# ── Helper: alias arch-suffixed custom images back to clean tag ────────
# Usage:
#   alias_arch_images_local         → run isula/ctr on this host
#   alias_arch_images_remote <host> → SSH to <host> and run there
# No-ops if the suffixed tag is missing (local-build / amd64-on-amd64).
alias_arch_images_local() {
  local tool=""
  if command -v isula &>/dev/null; then tool=isula
  elif command -v ctr &>/dev/null; then tool=ctr
  else return 0; fi
  for img in "${CUSTOM_ARCH_IMAGES[@]}"; do
    local src="docker.io/library/${img}-${PLATFORM}"
    local dst="docker.io/library/${img}"
    if [ "$tool" = isula ]; then
      isula tag "$src" "$dst" 2>/dev/null && log "    aliased (isula): $src -> $dst"
    else
      ctr -n k8s.io images tag "$src" "$dst" 2>/dev/null && log "    aliased (ctr):   $src -> $dst"
    fi
  done
}

alias_arch_images_remote() {
  local node="$1"
  for img in "${CUSTOM_ARCH_IMAGES[@]}"; do
    local src="docker.io/library/${img}-${PLATFORM}"
    local dst="docker.io/library/${img}"
    ssh "${K8S_NODE_USER}@${node}" "isula tag '$src' '$dst' 2>/dev/null || \
      ctr -n k8s.io images tag '$src' '$dst' 2>/dev/null || true" \
      && log "    aliased on $node: $src -> $dst"
  done
}

# Core application images (no httpbin; no mocks unless --with-mocks)
ALL_APP_IMAGES=(
  "keycloak-proxy:v3"
  "opal-proxy:v2"
  "keycloak-init:v2"
  "resource-sync:v1"
  "keycloak-custom:26.5.2"
  "postgres:17"
  "docker.io/envoyproxy/gateway:v1.7.0"
  "docker.io/envoyproxy/envoy:distroless-v1.37.0"
  "permitio/opal-server:0.7.4"
  "permitio/opal-client:0.7.4"
  "nginx:alpine"
)

# Appended to ALL_APP_IMAGES when --with-mocks is set.
MOCK_IMAGES=(
  "mock-kb:v1"
  "mock-rubik:v1"
  "mock-memory:v1"
)
if [ "$WITH_MOCKS" = true ]; then
  ALL_APP_IMAGES+=("${MOCK_IMAGES[@]}")
fi

# ── Naming convention for release tars ─────────────────────────────────
# Custom images built by scripts/build-release-images.sh and the
# pack-release skill are tagged with an arch suffix inside the tar:
#   keycloak-proxy:v3-arm64, opal-proxy:v2-amd64, ... (docker.io/library/<name>:<tag>-<arch>)
# This lets amd64 and arm64 tars coexist on the same developer machine.
# After `isula load` / `ctr import` on a node, we alias the suffixed tag
# back to the clean tag (keycloak-proxy:v3) so Helm charts don't need to
# know about arch. Third-party images (postgres, envoyproxy/*, permitio/*,
# nginx, keycloak-custom) keep their original tags — not in this list.
CUSTOM_ARCH_IMAGES=(
  "keycloak-proxy:v3"
  "opal-proxy:v2"
  "keycloak-init:v2"
  "resource-sync:v1"
)
if [ "$WITH_MOCKS" = true ]; then
  CUSTOM_ARCH_IMAGES+=(
    "mock-kb:v1"
    "mock-rubik:v1"
    "mock-memory:v1"
  )
fi

# ════════════════════════════════════════════════════════════════════════
# Step 1: Verify K8s cluster connectivity
# ════════════════════════════════════════════════════════════════════════
log "Step 1: Verifying K8s cluster connectivity..."
kubectl cluster-info || err "Cannot connect to K8s cluster. Check KUBECONFIG."

# ════════════════════════════════════════════════════════════════════════
# Step 2: Load images (optional, only with --load-images)
# ════════════════════════════════════════════════════════════════════════
if [ "$LOAD_IMAGES" = true ]; then
  log "Step 2: Loading images into K8s nodes (isula)..."

  if [ -n "${K8S_NODES:-}" ]; then
    # ── Multi-node: SCP tars to each node, then isula load ──────────
    for node in $K8S_NODES; do
      log "  Node: $node"
      ssh "${K8S_NODE_USER}@${node}" "mkdir -p ${IMAGE_DIR}" 2>/dev/null || true

      for img in "${ALL_APP_IMAGES[@]}"; do
        fname="$(image_to_filename "$img").tar"
        tarpath="$IMAGES_DIR/$fname"

        if [ ! -f "$tarpath" ]; then
          warn "    Image tar not found: $fname"
          continue
        fi

        log "    Loading: $img"
        scp -q "$tarpath" "${K8S_NODE_USER}@${node}:${IMAGE_DIR}/$fname"
        ssh "${K8S_NODE_USER}@${node}" "isula load -i ${IMAGE_DIR}/$fname" 2>/dev/null \
          || ssh "${K8S_NODE_USER}@${node}" "ctr -n k8s.io images import ${IMAGE_DIR}/$fname" 2>/dev/null \
          || warn "    Failed to load $img on $node"
      done

      # Alias <name>:<tag>-<arch> → <name>:<tag> so charts see the clean tag.
      log "  Aliasing arch-suffixed tags on $node..."
      alias_arch_images_remote "$node"

      ssh "${K8S_NODE_USER}@${node}" "rm -rf ${IMAGE_DIR}" 2>/dev/null || true
    done
  else
    # ── Single-node / local ─────────────────────────────────────────
    if command -v isula &>/dev/null; then
      for img in "${ALL_APP_IMAGES[@]}"; do
        fname="$(image_to_filename "$img").tar"
        tarpath="$IMAGES_DIR/$fname"

        if [ ! -f "$tarpath" ]; then
          warn "  Image tar not found: $fname"
          continue
        fi

        log "  Loading: $img"
        isula load -i "$tarpath" 2>/dev/null \
          || ctr -n k8s.io images import "$tarpath" 2>/dev/null \
          || warn "  Failed to load $img"
      done
    elif command -v ctr &>/dev/null; then
      for img in "${ALL_APP_IMAGES[@]}"; do
        fname="$(image_to_filename "$img").tar"
        tarpath="$IMAGES_DIR/$fname"

        if [ ! -f "$tarpath" ]; then
          warn "  Image tar not found: $fname"
          continue
        fi

        log "  Loading: $img"
        ctr -n k8s.io images import "$tarpath" 2>/dev/null \
          || warn "  Failed to load $img"
      done
    else
      err "'isula' and 'ctr' not found. Cannot load images."
    fi

    # Alias <name>:<tag>-<arch> → <name>:<tag> so charts see the clean tag.
    # Safe no-op when tar already used the clean tag (e.g. local amd64 build).
    log "  Aliasing arch-suffixed tags locally..."
    alias_arch_images_local
  fi
else
  log "Step 2: Skipping image loading (use --load-images to load from offline/)"
  # Even without --load-images, someone may have pre-loaded arch-suffixed tars.
  # Run the alias step so deploys against pre-loaded nodes still work.
  alias_arch_images_local
fi

# ════════════════════════════════════════════════════════════════════════
# Step 3: Install Gateway API + Envoy Gateway CRDs
# ════════════════════════════════════════════════════════════════════════
log "Step 3: Installing Gateway API + Envoy Gateway CRDs..."
GW_API_CRD="$OFFLINE_DIR/crds/gateway-api-${GATEWAY_API_VERSION}.yaml"
[ -f "$GW_API_CRD" ] || err "Missing CRD file: $GW_API_CRD"
kubectl apply --server-side --force-conflicts -f "$GW_API_CRD"
for crd in "${ENVOY_GATEWAY_CRDS[@]}"; do
  crd_path="$OFFLINE_DIR/crds/$crd"
  [ -f "$crd_path" ] || err "Missing Envoy Gateway CRD: $crd_path"
  kubectl apply --server-side --force-conflicts -f "$crd_path"
done

# ════════════════════════════════════════════════════════════════════════
# Step 4: Install Envoy Gateway controller
# ════════════════════════════════════════════════════════════════════════
log "Step 4: Installing Envoy Gateway controller..."
ENVOY_GATEWAY_TGZ="$OFFLINE_DIR/charts/gateway-helm-${ENVOY_GATEWAY_CHART_VERSION}.tgz"
[ -f "$ENVOY_GATEWAY_TGZ" ] || err "Missing chart: $ENVOY_GATEWAY_TGZ"

# Controller / data-plane lives in $ENVOY_GATEWAY_NS; CRs (Gateway, HTTPRoute,
# *Policy) live in $GATEWAY_CR_NS because that namespace is hardcoded in the
# chart templates and gateway-routes YAMLs. Create both.
kubectl create namespace "$ENVOY_GATEWAY_NS" --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace "$GATEWAY_CR_NS" --dry-run=client -o yaml | kubectl apply -f -

helm upgrade -i eg \
  "$ENVOY_GATEWAY_TGZ" \
  --namespace "$ENVOY_GATEWAY_NS" \
  --skip-crds \
  --set deployment.envoyGateway.image.pullPolicy=IfNotPresent

log "  Patching Envoy Gateway controller for Huawei Cloud (securityContext)..."
kubectl patch deployment envoy-gateway -n "$ENVOY_GATEWAY_NS" -p '{
  "spec":{"template":{"spec":{
    "securityContext":{"fsGroup":0,"runAsUser":0}
  }}}
}' 2>/dev/null || true

log "  Waiting for Envoy Gateway controller to be ready..."
kubectl -n "$ENVOY_GATEWAY_NS" rollout status deployment/envoy-gateway --timeout=120s 2>/dev/null || true

# Apply Gateway + EnvoyProxy + GatewayClass (local chart)
helm upgrade -i envoy-gateway-proxy \
  "$PROJECT_DIR/charts/envoy-gateway" \
  --namespace "$ENVOY_GATEWAY_NS"

# Wait for proxy pod
log "  Waiting for gateway proxy pod..."
for i in $(seq 1 30); do
  PROXY_DEPLOY=$(kubectl -n "$ENVOY_GATEWAY_NS" get deploy -l gateway.envoyproxy.io/owning-gateway-name=eg -o name 2>/dev/null | head -1)
  if [ -n "$PROXY_DEPLOY" ]; then
    kubectl -n "$ENVOY_GATEWAY_NS" rollout status "$PROXY_DEPLOY" --timeout=60s 2>/dev/null || true
    break
  fi
  sleep 2
done

# ════════════════════════════════════════════════════════════════════════
# Step 5: Install Keycloak stack
# ════════════════════════════════════════════════════════════════════════
log "Step 5: Installing Keycloak stack..."
kubectl create namespace "$KEYCLOAK_NS" --dry-run=client -o yaml | kubectl apply -f -

# Auto-detect StorageClass if not set
if [ -z "${STORAGE_CLASS:-}" ]; then
  STORAGE_CLASS=$(kubectl get sc -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
  if [ -n "$STORAGE_CLASS" ]; then
    log "  Auto-detected StorageClass: $STORAGE_CLASS"
  else
    warn "  No StorageClass found. PostgreSQL PVC may fail to bind."
  fi
else
  log "  Using StorageClass: $STORAGE_CLASS"
fi

HELM_EXTRA_ARGS=()
if [ -n "${KC_HOSTNAME:-}" ]; then
  log "  Using KC_HOSTNAME: $KC_HOSTNAME"
  HELM_EXTRA_ARGS+=(--set "keycloak.config.hostname=$KC_HOSTNAME")
fi
if [ -n "${STORAGE_CLASS:-}" ]; then
  HELM_EXTRA_ARGS+=(--set "postgres.persistence.storageClass=$STORAGE_CLASS")
fi

helm upgrade -i keycloak \
  "$PROJECT_DIR/charts/keycloak" \
  --namespace "$KEYCLOAK_NS" \
  "${HELM_EXTRA_ARGS[@]+"${HELM_EXTRA_ARGS[@]}"}"

log "  Waiting for PostgreSQL..."
kubectl -n "$KEYCLOAK_NS" rollout status statefulset/postgres --timeout=120s

log "  Waiting for Keycloak (this may take several minutes)..."
kubectl -n "$KEYCLOAK_NS" rollout status statefulset/keycloak --timeout=600s

log "  Waiting for keycloak-init job to complete..."
kubectl -n "$KEYCLOAK_NS" wait --for=condition=complete job/keycloak-init --timeout=300s || warn "keycloak-init job not yet complete, continuing..."

log "  Restarting keycloak-proxy to pick up client secret..."
kubectl -n "$KEYCLOAK_NS" rollout restart deployment/keycloak-proxy
kubectl -n "$KEYCLOAK_NS" rollout status deployment/keycloak-proxy --timeout=120s 2>/dev/null || warn "keycloak-proxy not ready yet"

# ════════════════════════════════════════════════════════════════════════
# Step 6: Install OPA stack
# ════════════════════════════════════════════════════════════════════════
log "Step 6: Installing OPA stack..."
kubectl create namespace "$OPA_NS" --dry-run=client -o yaml | kubectl apply -f -

helm upgrade -i opa \
  "$PROJECT_DIR/charts/opa" \
  --namespace "$OPA_NS"

log "  Waiting for OPAL server..."
kubectl -n "$OPA_NS" rollout status deployment/opal-server --timeout=120s

log "  Waiting for PEP proxy..."
kubectl -n "$OPA_NS" rollout status deployment/pep-proxy --timeout=180s

# ════════════════════════════════════════════════════════════════════════
# Step 6b: Install resource-sync
# ════════════════════════════════════════════════════════════════════════
log "Step 6b: Installing resource-sync..."
kubectl create namespace "$RESOURCE_SYNC_NS" --dry-run=client -o yaml | kubectl apply -f -

helm upgrade -i resource-sync \
  "$PROJECT_DIR/charts/resource-sync" \
  --namespace "$RESOURCE_SYNC_NS"

log "  Waiting for resource-sync..."
kubectl -n "$RESOURCE_SYNC_NS" rollout status deployment/resource-sync --timeout=120s 2>/dev/null || warn "resource-sync not ready yet"

# ════════════════════════════════════════════════════════════════════════
# Step 7: Apply gateway routes
# ════════════════════════════════════════════════════════════════════════
log "Step 7: Applying gateway routes..."
kubectl apply -f "$PROJECT_DIR/gateway-routes/reference-grants.yaml"
kubectl apply -f "$PROJECT_DIR/gateway-routes/keycloak-routes.yaml"
kubectl apply -f "$PROJECT_DIR/gateway-routes/protected-routes.yaml"

# protected-routes.yaml is the dev/umbrella layout — it bundles mock-kb-route
# and mock-rubik-route, plus SecurityPolicy/EnvoyExtensionPolicy targetRefs
# pointing at them. In isula mode we normally strip them to avoid
# ResolvedRefs=False dangling statuses; with --with-mocks we deploy the
# mocks and keep the routes alive.
if [ "$WITH_MOCKS" = true ]; then
  log "Step 7b: Deploying mock backends (--with-mocks)..."
  kubectl apply -f "$PROJECT_DIR/mock-deployments/mock-kb.yaml"
  kubectl apply -f "$PROJECT_DIR/mock-deployments/mock-rubik.yaml"
  kubectl apply -f "$PROJECT_DIR/mock-deployments/mock-memory.yaml"
  log "  Waiting for mock pods..."
  kubectl -n mock-kb     rollout status deployment/mock-kb     --timeout=120s 2>/dev/null || warn "mock-kb not ready"
  kubectl -n mock-rubik  rollout status deployment/mock-rubik  --timeout=120s 2>/dev/null || warn "mock-rubik not ready"
  kubectl -n mock-memory rollout status deployment/mock-memory --timeout=120s 2>/dev/null || warn "mock-memory not ready"
  # Ensure pep-proxy ext_authz covers the mock routes + resource-sync extproc
  # targets them for ACL auto-sync.
  kubectl -n "$GATEWAY_CR_NS" patch securitypolicy pep-proxy-extauthz --type=merge \
    -p '{"spec":{"targetRefs":[{"group":"gateway.networking.k8s.io","kind":"HTTPRoute","name":"keycloak-proxy-route"},{"group":"gateway.networking.k8s.io","kind":"HTTPRoute","name":"identity-api-route"},{"group":"gateway.networking.k8s.io","kind":"HTTPRoute","name":"acl-api-route"},{"group":"gateway.networking.k8s.io","kind":"HTTPRoute","name":"path-rules-route"},{"group":"gateway.networking.k8s.io","kind":"HTTPRoute","name":"mock-kb-route"},{"group":"gateway.networking.k8s.io","kind":"HTTPRoute","name":"mock-rubik-route"},{"group":"gateway.networking.k8s.io","kind":"HTTPRoute","name":"mock-memory-route"}]}}' \
    2>/dev/null || warn "    Failed to patch SecurityPolicy"
else
  log "  Stripping mock-kb / mock-rubik / mock-memory routes (not deployed in isula without --with-mocks)..."
  kubectl -n "$GATEWAY_CR_NS" delete httproute mock-kb-route mock-rubik-route mock-memory-route \
    --ignore-not-found 2>/dev/null || true
  kubectl -n "$GATEWAY_CR_NS" patch securitypolicy pep-proxy-extauthz --type=merge \
    -p '{"spec":{"targetRefs":[{"group":"gateway.networking.k8s.io","kind":"HTTPRoute","name":"keycloak-proxy-route"},{"group":"gateway.networking.k8s.io","kind":"HTTPRoute","name":"identity-api-route"},{"group":"gateway.networking.k8s.io","kind":"HTTPRoute","name":"acl-api-route"},{"group":"gateway.networking.k8s.io","kind":"HTTPRoute","name":"path-rules-route"}]}}' \
    2>/dev/null || warn "    Failed to patch SecurityPolicy; manual cleanup may be needed"
  # resource-sync-extproc only targets the 2 mock HTTPRoutes; without real
  # business routes it has nothing to observe. Drop it — the admin will
  # recreate it later scoped to their real KB/Rubik/... HTTPRoutes.
  kubectl -n "$GATEWAY_CR_NS" delete envoyextensionpolicy resource-sync-extproc \
    --ignore-not-found 2>/dev/null || true
fi

# ════════════════════════════════════════════════════════════════════════
# Step 8: Expose gateway via NodePort
# ════════════════════════════════════════════════════════════════════════
log "Step 8: Exposing gateway on NodePort 30080..."
EG_SVC=$(kubectl -n "$ENVOY_GATEWAY_NS" get svc -l gateway.envoyproxy.io/owning-gateway-name=eg -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
if [ -n "$EG_SVC" ]; then
  kubectl -n "$ENVOY_GATEWAY_NS" patch svc "$EG_SVC" \
    -p '{"spec":{"type":"NodePort","ports":[{"port":80,"targetPort":10080,"nodePort":30080,"protocol":"TCP","name":"http"}]}}' \
    2>/dev/null || warn "Failed to patch NodePort (may already be set)"
else
  warn "Envoy proxy service not found yet; expose manually once pod is ready."
fi

# ── Summary ───────────────────────────────────────────────────────────────
log ""
log "==============================================="
log "da-cluster deployment complete! (Huawei Cloud K8s + isula, $PLATFORM)"
log "==============================================="
log ""
log "Pods by namespace:"
for ns in "$KEYCLOAK_NS" "$OPA_NS" "$RESOURCE_SYNC_NS" "$ENVOY_GATEWAY_NS" "$GATEWAY_CR_NS"; do
  log "  $ns:"
  kubectl -n "$ns" get pods --no-headers 2>/dev/null | while read line; do echo "    $line"; done
done
log ""
log "Gateway access:"
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || echo "<NODE_IP>")
log "  http://${NODE_IP}:30080/"
log ""
log "Quick test:"
log "  curl http://${NODE_IP}:30080/realms/master/.well-known/openid-configuration"
log ""
if [ -n "${KC_HOSTNAME:-}" ]; then
  log "KC_HOSTNAME: $KC_HOSTNAME"
else
  warn "KC_HOSTNAME not set. If Keycloak redirects break, re-run with:"
  warn "  KC_HOSTNAME=http://<EIP>:30080 STORAGE_CLASS=dorado-inner-nas $0"
fi
if [ -n "${STORAGE_CLASS:-}" ]; then
  log "StorageClass: $STORAGE_CLASS"
fi
log ""
log "Onboard real business apps (KB / Rubik / ...):"
log "  1. Deploy your backend + Service(s) in their own namespace."
log "  2. Apply an HTTPRoute in $GATEWAY_CR_NS pointing at the Service."
log "  3. Patch securitypolicy/pep-proxy-extauthz in $GATEWAY_CR_NS to include"
log "     the new HTTPRoute in spec.targetRefs (for JWT + path-level authz)."
log "  4. Create an EnvoyExtensionPolicy in $GATEWAY_CR_NS whose extProc points"
log "     at resource-sync.$RESOURCE_SYNC_NS:8082, targeting the HTTPRoute(s)"
log "     that need ACL auto-sync."
log ""
log "Run tests:"
log "  ./scripts/test.sh"
log "  (Section 10/11 mock-KB/mock-Rubik e2e will SKIP/FAIL — mocks aren't"
log "   deployed in isula mode. Other 20+ sections exercise the real stack.)"
