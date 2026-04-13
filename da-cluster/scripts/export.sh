#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# export.sh — Export all resources for air-gapped deployment
#
# Run this on an INTERNET-CONNECTED machine. It will:
#   1. Build the 3 custom Docker images
#   2. Export ALL Docker images (custom + registry) as .tar files
#   3. Pull OCI Helm charts as .tgz files
#   4. Download Gateway API CRDs YAML
#
# After running, the entire da-cluster/ directory can be copied to the
# air-gapped server and deployed with: ./scripts/setup.sh
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
AUTH_DIR="$(cd "$PROJECT_DIR/.." && pwd)"
OFFLINE_DIR="$PROJECT_DIR/offline"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Configuration ──────────────────────────────────────────────────────────
# All images required by the cluster
CUSTOM_IMAGES=(
  "keycloak-proxy:v3"
  "opal-proxy:v2"
  "keycloak-init:v2"
  "resource-sync:v1"
)

REGISTRY_IMAGES=(
  "quay.io/keycloak/keycloak:26.5.2"
  "postgres:17"
  "docker.io/envoyproxy/gateway:v1.7.0"
  "docker.io/envoyproxy/envoy:distroless-v1.37.0"
  "permitio/opal-server:0.7.4"
  "permitio/opal-client:0.7.4"
  "mccutchen/go-httpbin:v2.6.0"
  "nginx:alpine"
)

KIND_NODE_IMAGE="kindest/node:v1.35.0"
GATEWAY_API_VERSION="v1.4.1-experimental"
ENVOY_GATEWAY_CHART_VERSION="v1.7.0"

# ── Create directories ─────────────────────────────────────────────────────
mkdir -p "$OFFLINE_DIR/images" "$OFFLINE_DIR/charts" "$OFFLINE_DIR/crds"

# ── Step 1: Build custom images ────────────────────────────────────────────
log "Step 1: Building custom Docker images..."

log "  Building keycloak-proxy:v3..."
PROXY_BUILD_DIR=$(mktemp -d)
cp -r "$AUTH_DIR/da-idb-proxy/app" "$PROXY_BUILD_DIR/app"
cp "$PROJECT_DIR/images/keycloak-proxy/Dockerfile" "$PROXY_BUILD_DIR/Dockerfile"
docker build -t keycloak-proxy:v3 "$PROXY_BUILD_DIR"
rm -rf "$PROXY_BUILD_DIR"

log "  Building opal-proxy:v2..."
OPAL_BUILD_DIR=$(mktemp -d)
cp "$PROJECT_DIR/images/opal-proxy/Dockerfile" "$OPAL_BUILD_DIR/Dockerfile"
cp "$PROJECT_DIR/images/opal-proxy/supervisord.conf" "$OPAL_BUILD_DIR/supervisord.conf"
cp "$PROJECT_DIR/images/opal-proxy/requirements.txt" "$OPAL_BUILD_DIR/requirements.txt"
cp -r "$AUTH_DIR/opal-dynamic-policy/pep-proxy" "$OPAL_BUILD_DIR/pep-proxy"
cp -r "$AUTH_DIR/opal-dynamic-policy/bundle-server" "$OPAL_BUILD_DIR/bundle-server"
cp -r "$AUTH_DIR/opal-dynamic-policy/data" "$OPAL_BUILD_DIR/data"
docker build -t opal-proxy:v2 "$OPAL_BUILD_DIR"
rm -rf "$OPAL_BUILD_DIR"

log "  Building keycloak-init:v2..."
docker build -t keycloak-init:v2 "$PROJECT_DIR/images/keycloak-init"

log "  Building resource-sync:v1..."
RS_BUILD_DIR=$(mktemp -d)
cp "$PROJECT_DIR/images/resource-sync/Dockerfile" "$RS_BUILD_DIR/Dockerfile"
cp -r "$AUTH_DIR/resource-sync/app" "$RS_BUILD_DIR/app"
cp -r "$AUTH_DIR/resource-sync/proto" "$RS_BUILD_DIR/proto"
cp "$AUTH_DIR/resource-sync/requirements.txt" "$RS_BUILD_DIR/requirements.txt"
docker build -t resource-sync:v1 "$RS_BUILD_DIR"
rm -rf "$RS_BUILD_DIR"

# ── Step 2: Pull registry images ──────────────────────────────────────────
log "Step 2: Pulling registry images..."
for img in "${REGISTRY_IMAGES[@]}"; do
  log "  Pulling $img..."
  docker pull "$img" || warn "Failed to pull $img (may already exist locally)"
done

log "  Pulling Kind node image: $KIND_NODE_IMAGE..."
docker pull "$KIND_NODE_IMAGE" || warn "Failed to pull $KIND_NODE_IMAGE"

# ── Step 3: Export all images as .tar ─────────────────────────────────────
log "Step 3: Exporting images to $OFFLINE_DIR/images/..."

image_to_filename() {
  echo "$1" | sed 's|/|_|g; s|:|_|g'
}

ALL_IMAGES=("${CUSTOM_IMAGES[@]}" "${REGISTRY_IMAGES[@]}" "$KIND_NODE_IMAGE")

for img in "${ALL_IMAGES[@]}"; do
  fname="$(image_to_filename "$img").tar"
  tarpath="$OFFLINE_DIR/images/$fname"
  if [ -f "$tarpath" ] && [ "$(stat -c%s "$tarpath" 2>/dev/null || stat -f%z "$tarpath" 2>/dev/null)" -gt 102400 ]; then
    warn "  $fname already exists ($(du -h "$tarpath" | cut -f1)), skipping (delete to re-export)"
    continue
  fi
  log "  Exporting $img -> $fname"
  docker save -o "$tarpath" "$img" 2>/dev/null
  # Verify the tar is reasonable (>100KB). If not, Docker Desktop's containerd
  # may have produced only a manifest stub. Fall back to ctr export from Kind.
  filesize=$(stat -c%s "$tarpath" 2>/dev/null || stat -f%z "$tarpath" 2>/dev/null || echo 0)
  if [ "$filesize" -lt 102400 ]; then
    warn "  docker save produced only ${filesize} bytes for $img — trying ctr export from Kind..."
    CLUSTER="${CLUSTER_NAME:-da-cluster}"
    if docker exec "${CLUSTER}-control-plane" ctr --namespace=k8s.io images export - "$img" > "$tarpath" 2>/dev/null; then
      filesize=$(stat -c%s "$tarpath" 2>/dev/null || stat -f%z "$tarpath" 2>/dev/null || echo 0)
      log "  ctr export succeeded: $(du -h "$tarpath" | cut -f1)"
    else
      warn "  ctr export also failed for $img"
    fi
  fi
done

# ── Step 4: Pull OCI Helm charts ─────────────────────────────────────────
log "Step 4: Pulling OCI Helm charts..."

EG_TGZ="$OFFLINE_DIR/charts/gateway-helm-${ENVOY_GATEWAY_CHART_VERSION}.tgz"
if [ ! -f "$EG_TGZ" ]; then
  log "  Pulling envoy-gateway helm chart ${ENVOY_GATEWAY_CHART_VERSION}..."
  helm pull oci://docker.io/envoyproxy/gateway-helm \
    --version "$ENVOY_GATEWAY_CHART_VERSION" \
    --destination "$OFFLINE_DIR/charts"
else
  warn "  gateway-helm chart already exists, skipping"
fi

# ── Step 5: Download Gateway API CRDs (experimental channel, required by EG) ─
log "Step 5: Downloading Gateway API CRDs..."

CRD_FILE="$OFFLINE_DIR/crds/gateway-api-${GATEWAY_API_VERSION}.yaml"
if [ ! -f "$CRD_FILE" ]; then
  # EG requires the experimental channel of Gateway API
  GW_API_VERSION_PLAIN="${GATEWAY_API_VERSION%-experimental}"
  curl -sL -o "$CRD_FILE" \
    "https://github.com/kubernetes-sigs/gateway-api/releases/download/${GW_API_VERSION_PLAIN}/experimental-install.yaml"
  log "  Downloaded gateway-api experimental CRDs ($(wc -l < "$CRD_FILE") lines)"
else
  warn "  gateway-api CRDs already exist, skipping"
fi

log "  Envoy Gateway CRDs should be pre-committed under offline/crds/ (gateway.envoyproxy.io_*.yaml)"

# ── Summary ──────────────────────────────────────────────────────────────
log ""
log "==============================================="
log "Export complete!"
log "==============================================="
log ""
log "Images exported to: $OFFLINE_DIR/images/"
ls -lh "$OFFLINE_DIR/images/"/*.tar 2>/dev/null | awk '{print "  " $NF " (" $5 ")"}'
log ""
log "Helm charts: $OFFLINE_DIR/charts/"
ls -lh "$OFFLINE_DIR/charts/"*.tgz 2>/dev/null | awk '{print "  " $NF " (" $5 ")"}'
log ""
log "CRDs: $OFFLINE_DIR/crds/"
ls -lh "$OFFLINE_DIR/crds/"*.yaml 2>/dev/null | awk '{print "  " $NF " (" $5 ")"}'
log ""
TOTAL_SIZE=$(du -sh "$OFFLINE_DIR" | cut -f1)
log "Total offline bundle size: $TOTAL_SIZE"
log ""
log "Next: copy the entire da-cluster/ directory to the air-gapped server"
log "      and run: ./scripts/setup.sh"
