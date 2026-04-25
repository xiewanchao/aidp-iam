#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# setup.sh — Air-gapped deployment of da-cluster unified auth system
#
# Prerequisites on the target Linux server:
#   - docker (with containerd)
#   - kubectl
#   - helm 3
#   - kind (only for Kind mode)
#   - ctr  (only for K8s mode, usually bundled with containerd)
#
# All images, Helm charts, and CRDs are loaded from offline/ directory.
# NO internet access required (unless --build is used).
#
# Usage:
#   ./scripts/setup.sh              # Kind mode (default): create Kind cluster
#   ./scripts/setup.sh --no-kind    # K8s mode: deploy to existing cluster
#   ./scripts/setup.sh --build      # Rebuild keycloak-proxy & opal-proxy from source
#   ./scripts/setup.sh --build --no-kind  # Combine both flags
#   CLUSTER_NAME=xxx ./scripts/setup.sh  # Use custom Kind cluster name (in Kind mode)
#
# Environment variables:
#   CLUSTER_NAME   — Kind cluster name (default: da-cluster)
#   KUBECONFIG     — path to kubeconfig (K8s mode, default: ~/.kube/config)
#   K8S_NODES      — space-separated list of node IPs for image loading
#                    (K8s mode, e.g. "192.168.1.10 192.168.1.11")
#   K8S_NODE_USER  — SSH user for nodes (K8s mode, default: root)
#   IMAGE_DIR      — remote path to copy image tars (K8s mode, default: /tmp/da-images)
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OFFLINE_DIR="$PROJECT_DIR/offline"

CLUSTER_NAME="${CLUSTER_NAME:-da-cluster}"
KEYCLOAK_NS="keycloak"
OPA_NS="opa"
ENVOY_GATEWAY_NS="envoy-gateway-system"
MOCK_KB_NS="mock-kb"
MOCK_RUBIK_NS="mock-rubik"
MOCK_MEMORY_NS="mock-memory"
RESOURCE_SYNC_NS="resource-sync"

KIND_NODE_IMAGE="${KIND_NODE_IMAGE:-}"
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

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Parse arguments ─────────────────────────────────────────────────────
USE_KIND=true
USE_BUILD=false
EXISTING_KIND=false
USE_FAT_BASE=false
for arg in "$@"; do
  case "$arg" in
    --no-kind)        USE_KIND=false ;;
    --build)          USE_BUILD=true ;;
    --existing-kind)  EXISTING_KIND=true ;;
    --fat-base)       USE_FAT_BASE=true ;;
    --help|-h)
      echo "Usage: $0 [--no-kind] [--build] [--fat-base] [--existing-kind]"
      echo ""
      echo "  (default)         Kind mode: create Kind cluster, load images via docker, deploy"
      echo "  --no-kind         K8s mode: deploy to existing K8s cluster (no Kind)"
      echo "  --build           Rebuild custom images from source (requires docker + internet)"
      echo "  --fat-base        Use fat base images for code-only rebuild (no network needed)"
      echo "                    Base images must be pre-loaded; only copies code into them."
      echo "  --existing-kind   Use existing Kind cluster: skip cluster creation, import"
      echo "                    offline tars directly into Kind containerd (no docker build)"
      echo ""
      echo "Environment variables:"
      echo "  CLUSTER_NAME   Kind cluster name (default: da-cluster)"
      echo "  PLATFORM       Force platform: amd64 or arm64 (default: auto-detect)"
      echo "  K8S_NODES      space-separated node IPs (K8s mode, for image loading via SSH)"
      echo "  K8S_NODE_USER  SSH user for nodes (default: root)"
      echo "  IMAGE_DIR      remote temp dir for images (default: /tmp/da-images)"
      echo ""
      echo "Examples:"
      echo "  ./scripts/setup.sh                              # Create Kind cluster + deploy"
      echo "  ./scripts/setup.sh --build                      # Build images from source + deploy"
      echo "  ./scripts/setup.sh --fat-base                   # Rebuild from fat base (air-gapped)"
      echo "  ./scripts/setup.sh --existing-kind              # Deploy to existing Kind cluster"
      echo "  PLATFORM=arm64 ./scripts/setup.sh --no-kind     # Deploy arm64 to existing K8s"
      exit 0
      ;;
    *) err "Unknown argument: $arg (use --help)" ;;
  esac
done

K8S_NODE_USER="${K8S_NODE_USER:-root}"
IMAGE_DIR="${IMAGE_DIR:-/tmp/da-images}"

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
log "Pre-flight: checking offline resources..."
IMAGES_DIR="$OFFLINE_DIR/images/$PLATFORM"
[ -d "$IMAGES_DIR" ] || err "Missing $IMAGES_DIR/ — run ./scripts/build-release-images.sh or drop the release tar into offline/images/$PLATFORM/"
[ -d "$OFFLINE_DIR/charts" ] || err "Missing $OFFLINE_DIR/charts/ — run ./scripts/build-release-images.sh --offline-only first"
[ -d "$OFFLINE_DIR/crds" ]   || err "Missing $OFFLINE_DIR/crds/ — run ./scripts/build-release-images.sh --offline-only first"

REQUIRED_CMDS=(kubectl helm)
if [ "$EXISTING_KIND" = true ]; then
  REQUIRED_CMDS+=(docker)
  log "Mode: --existing-kind (use existing Kind cluster, import offline tars)"
elif [ "$USE_KIND" = true ]; then
  REQUIRED_CMDS+=(docker kind)
else
  log "Mode: K8s (--no-kind)"
fi
if [ "$USE_BUILD" = true ]; then
  if [ "$EXISTING_KIND" = true ]; then
    err "--build and --existing-kind are mutually exclusive"
  fi
  if [[ ! " ${REQUIRED_CMDS[*]} " =~ " docker " ]]; then
    REQUIRED_CMDS+=(docker)
  fi
  log "Mode: --build (will rebuild custom images from source)"
fi
if [ "$USE_FAT_BASE" = true ]; then
  if [[ ! " ${REQUIRED_CMDS[*]} " =~ " docker " ]]; then
    REQUIRED_CMDS+=(docker)
  fi
  log "Mode: --fat-base (code-only rebuild from base images, no network needed)"
fi
for cmd in "${REQUIRED_CMDS[@]}"; do
  command -v "$cmd" &>/dev/null || err "'$cmd' not found in PATH"
done

# ── Helper: convert image name to tar filename ─────────────────────────
image_to_filename() {
  echo "$1" | sed 's|/|_|g; s|:|_|g'
}

# ── Helpers: alias arch-suffixed custom-image tags back to clean tags ──
# Release tars built by pack-release skill carry "-<arch>" tag suffix
# (keycloak-proxy:v3-arm64). Helm charts reference clean tags
# (keycloak-proxy:v3). After load we add the clean alias so kubelet can
# pull. All three helpers are no-ops when the suffixed tag isn't present
# (e.g. --build path puts clean tags directly in local docker).
#
# IMPORTANT: each tag invocation is wrapped in `if ... then ... fi` to
# stay safe under `set -euo pipefail`. A `cmd && log` whose first half
# fails would propagate non-zero out of the function and abort the script.
alias_arch_images_kind() {
  for img in "${CUSTOM_ARCH_IMAGES[@]}"; do
    local src="docker.io/library/${img}-${PLATFORM}"
    local dst="docker.io/library/${img}"
    # First try host docker — sometimes `docker load` lands the suffix-tagged
    # image there. Adding a clean alias on the host means subsequent
    # `kind load docker-image $img` will succeed without us needing to alias
    # inside the Kind node.
    if docker image inspect "${img}-${PLATFORM}" &>/dev/null \
       && ! docker image inspect "$img" &>/dev/null; then
      if docker tag "${img}-${PLATFORM}" "$img" 2>/dev/null; then
        log "    aliased (docker host): ${img}-${PLATFORM} -> ${img}"
        # Re-sync into Kind — alias was added after the original kind load.
        kind load docker-image "$img" --name "$CLUSTER_NAME" 2>/dev/null || true
      fi
    fi
    # Belt-and-suspenders: also alias inside the Kind node's containerd in
    # case ctr-import (not docker load) was the path the load step took.
    if docker exec "$CONTROL_PLANE" ctr -n k8s.io images tag "$src" "$dst" 2>/dev/null; then
      log "    aliased (Kind ctr):    $src -> $dst"
    fi
  done
  return 0
}

alias_arch_images_k8s_local() {
  if ! command -v ctr &>/dev/null; then return 0; fi
  for img in "${CUSTOM_ARCH_IMAGES[@]}"; do
    local src="docker.io/library/${img}-${PLATFORM}"
    local dst="docker.io/library/${img}"
    if ctr -n k8s.io images tag "$src" "$dst" 2>/dev/null; then
      log "    aliased (ctr):   $src -> $dst"
    fi
  done
  return 0
}

alias_arch_images_k8s_remote() {
  local node="$1"
  for img in "${CUSTOM_ARCH_IMAGES[@]}"; do
    local src="docker.io/library/${img}-${PLATFORM}"
    local dst="docker.io/library/${img}"
    if ssh "${K8S_NODE_USER}@${node}" "ctr -n k8s.io images tag '$src' '$dst' 2>/dev/null" 2>/dev/null; then
      log "    aliased on $node: $src -> $dst"
    fi
  done
  return 0
}

# All application images to load
ALL_APP_IMAGES=(
  "keycloak-proxy:v3"
  "opal-proxy:v2"
  "keycloak-init:v2"
  "resource-sync:v1"
  "keycloak-custom:26.5.2"
  "mock-kb:v1"
  "mock-rubik:v1"
  "mock-memory:v1"
  "postgres:17"
  "docker.io/envoyproxy/gateway:v1.7.0"
  "docker.io/envoyproxy/envoy:distroless-v1.37.0"
  "permitio/opal-server:0.7.4"
  "permitio/opal-client:0.7.4"
  "nginx:alpine"
)

# ── Source directories (for --build mode) ────────────────────────────────
AUTH_DIR="$(cd "$PROJECT_DIR/.." && pwd)"

# Images that --build will rebuild from source (others still use offline tar)
BUILD_IMAGES=("keycloak-proxy:v3" "opal-proxy:v2" "keycloak-init:v2" "resource-sync:v1" "keycloak-custom:26.5.2" "mock-kb:v1" "mock-rubik:v1" "mock-memory:v1")

FAT_BASE_IMAGES=("keycloak-proxy:v3" "opal-proxy:v2" "keycloak-init:v2")

# Images that release tars carry with a "-<arch>" tag suffix (built by the
# pack-release skill / build-release-images.sh via `docker buildx build -t
# X:tag-<arch>`) so amd64 and arm64 variants don't collide on a multi-arch
# builder. After loading on a node, alias them back to the clean tag so the
# Helm charts (which reference clean tags) can pull. Excludes keycloak-custom
# (built without arch suffix) and all third-party images (postgres, envoy,
# permitio, etc. — their upstream tags never carry an arch).
CUSTOM_ARCH_IMAGES=("keycloak-proxy:v3" "opal-proxy:v2" "keycloak-init:v2" "resource-sync:v1" "mock-kb:v1" "mock-rubik:v1" "mock-memory:v1")

is_build_image() {
  local img="$1"
  for bi in "${BUILD_IMAGES[@]}"; do
    [ "$img" = "$bi" ] && return 0
  done
  return 1
}

is_fat_base_image() {
  local img="$1"
  for fi in "${FAT_BASE_IMAGES[@]}"; do
    [ "$img" = "$fi" ] && return 0
  done
  return 1
}

# ════════════════════════════════════════════════════════════════════════
# Step 0a (--fat-base only): Rebuild from fat base images (no network)
# ════════════════════════════════════════════════════════════════════════

if [ "$USE_FAT_BASE" = true ]; then
  log "Step 0: Building custom images from fat base (no network)..."

  # Ensure base images are loaded
  for base_img in "base-keycloak-proxy:v1" "base-opal-proxy:v1" "base-keycloak-init:v1"; do
    base_fname="$(image_to_filename "$base_img").tar"
    base_tar="$IMAGES_DIR/$base_fname"
    if ! docker image inspect "$base_img" &>/dev/null; then
      if [ -f "$base_tar" ]; then
        log "  Loading base image: $base_img"
        docker load -i "$base_tar" 2>/dev/null || true
      else
        err "Base image '$base_img' not found and tar missing: $base_tar"
      fi
    fi
  done

  log "  Building keycloak-proxy:v2 (slim, from base)..."
  PROXY_BUILD_DIR=$(mktemp -d)
  cp -r "$AUTH_DIR/da-idb-proxy/app" "$PROXY_BUILD_DIR/app"
  cp "$PROJECT_DIR/images/keycloak-proxy/Dockerfile.slim" "$PROXY_BUILD_DIR/Dockerfile"
  docker build -t keycloak-proxy:v2 --build-arg BASE_IMAGE=base-keycloak-proxy:v1 "$PROXY_BUILD_DIR"
  rm -rf "$PROXY_BUILD_DIR"

  log "  Building opal-proxy:v1 (slim, from base)..."
  OPAL_BUILD_DIR=$(mktemp -d)
  cp "$PROJECT_DIR/images/opal-proxy/Dockerfile.slim" "$OPAL_BUILD_DIR/Dockerfile"
  cp "$PROJECT_DIR/images/opal-proxy/supervisord.conf" "$OPAL_BUILD_DIR/supervisord.conf"
  cp -r "$AUTH_DIR/opal-dynamic-policy/pep-proxy" "$OPAL_BUILD_DIR/pep-proxy"
  cp -r "$AUTH_DIR/opal-dynamic-policy/bundle-server" "$OPAL_BUILD_DIR/bundle-server"
  cp -r "$AUTH_DIR/opal-dynamic-policy/data" "$OPAL_BUILD_DIR/data"
  docker build -t opal-proxy:v1 --build-arg BASE_IMAGE=base-opal-proxy:v1 "$OPAL_BUILD_DIR"
  rm -rf "$OPAL_BUILD_DIR"

  log "  Building keycloak-init:v1 (slim, from base)..."
  INIT_BUILD_DIR=$(mktemp -d)
  cp "$PROJECT_DIR/images/keycloak-init/Dockerfile.slim" "$INIT_BUILD_DIR/Dockerfile"
  cp "$PROJECT_DIR/images/keycloak-init/init-keycloak.py" "$INIT_BUILD_DIR/init-keycloak.py"
  docker build -t keycloak-init:v1 --build-arg BASE_IMAGE=base-keycloak-init:v1 "$INIT_BUILD_DIR"
  rm -rf "$INIT_BUILD_DIR"

  log "  Fat-base build complete (keycloak-custom uses offline tar, no rebuild needed)"
fi

# ════════════════════════════════════════════════════════════════════════
# Step 0b (--build only): Rebuild custom images from source
# ════════════════════════════════════════════════════════════════════════

if [ "$USE_BUILD" = true ]; then
  log "Step 0: Building custom images from source..."

  log "  Building keycloak-proxy:v2 from $AUTH_DIR/da-idb-proxy..."
  PROXY_BUILD_DIR=$(mktemp -d)
  cp -r "$AUTH_DIR/da-idb-proxy/app" "$PROXY_BUILD_DIR/app"
  cp "$PROJECT_DIR/images/keycloak-proxy/Dockerfile" "$PROXY_BUILD_DIR/Dockerfile"
  docker build -t keycloak-proxy:v3 "$PROXY_BUILD_DIR"
  rm -rf "$PROXY_BUILD_DIR"

  log "  Building opal-proxy:v1 from $AUTH_DIR/opal-dynamic-policy..."
  OPAL_BUILD_DIR=$(mktemp -d)
  cp "$PROJECT_DIR/images/opal-proxy/Dockerfile" "$OPAL_BUILD_DIR/Dockerfile"
  cp "$PROJECT_DIR/images/opal-proxy/supervisord.conf" "$OPAL_BUILD_DIR/supervisord.conf"
  cp "$PROJECT_DIR/images/opal-proxy/requirements.txt" "$OPAL_BUILD_DIR/requirements.txt"
  cp -r "$AUTH_DIR/opal-dynamic-policy/pep-proxy" "$OPAL_BUILD_DIR/pep-proxy"
  cp -r "$AUTH_DIR/opal-dynamic-policy/bundle-server" "$OPAL_BUILD_DIR/bundle-server"
  cp -r "$AUTH_DIR/opal-dynamic-policy/data" "$OPAL_BUILD_DIR/data"
  docker build -t opal-proxy:v2 "$OPAL_BUILD_DIR"
  rm -rf "$OPAL_BUILD_DIR"

  log "  Building keycloak-init:v1 from $PROJECT_DIR/images/keycloak-init..."
  docker build -t keycloak-init:v2 "$PROJECT_DIR/images/keycloak-init"

  log "  Building resource-sync:v1 from $AUTH_DIR/resource-sync..."
  RS_BUILD_DIR=$(mktemp -d)
  cp "$PROJECT_DIR/images/resource-sync/Dockerfile" "$RS_BUILD_DIR/Dockerfile"
  cp -r "$AUTH_DIR/resource-sync/app" "$RS_BUILD_DIR/app"
  cp -r "$AUTH_DIR/resource-sync/proto" "$RS_BUILD_DIR/proto"
  cp "$AUTH_DIR/resource-sync/requirements.txt" "$RS_BUILD_DIR/requirements.txt"
  docker build -t resource-sync:v1 "$RS_BUILD_DIR"
  rm -rf "$RS_BUILD_DIR"

  log "  Building keycloak-custom:26.5.2 from $PROJECT_DIR/images/keycloak-custom..."
  docker build -t keycloak-custom:26.5.2 "$PROJECT_DIR/images/keycloak-custom"

  log "  Building mock-kb:v1 from $AUTH_DIR/mock-kb..."
  docker build -t mock-kb:v1 "$AUTH_DIR/mock-kb"

  log "  Building mock-rubik:v1 from $AUTH_DIR/mock-rubik..."
  docker build -t mock-rubik:v1 "$AUTH_DIR/mock-rubik"

  log "  Building mock-memory:v1 from $AUTH_DIR/mock-memory..."
  docker build -t mock-memory:v1 "$AUTH_DIR/mock-memory"

  log "  Custom images built successfully"
fi

# ════════════════════════════════════════════════════════════════════════
# Step 1 & 2: Cluster creation + Image loading (differs by mode)
# ════════════════════════════════════════════════════════════════════════

if [ "$USE_KIND" = true ] || [ "$EXISTING_KIND" = true ]; then
  # ── Kind Mode ────────────────────────────────────────────────────────
  CONTROL_PLANE="${CLUSTER_NAME}-control-plane"

  if [ "$EXISTING_KIND" = true ]; then
    # --existing-kind: skip cluster creation, verify it exists
    log "Step 1: Using existing Kind cluster '$CLUSTER_NAME'..."
    if ! docker ps --format '{{.Names}}' | grep -q "^${CONTROL_PLANE}$"; then
      err "Kind cluster '$CLUSTER_NAME' not found (container '${CONTROL_PLANE}' not running)"
    fi
    kubectl cluster-info --context "kind-${CLUSTER_NAME}" 2>/dev/null \
      || kubectl cluster-info \
      || err "Cannot connect to Kind cluster"
  else
    # Default Kind mode: create cluster
    log "Step 1: Creating Kind cluster '$CLUSTER_NAME'..."

    # Try loading Kind node image from offline tar
    KIND_NODE_TAR=$(find "$IMAGES_DIR" -name 'kindest_node_*.tar' 2>/dev/null | head -1 || true)
    if [ -n "$KIND_NODE_TAR" ] && [ -f "$KIND_NODE_TAR" ]; then
      log "  Loading Kind node image from $KIND_NODE_TAR..."
      docker load -i "$KIND_NODE_TAR" 2>/dev/null || true
    fi

    # Auto-detect Kind node image if not set
    if [ -z "$KIND_NODE_IMAGE" ]; then
      AVAILABLE_IMAGES=($(docker images --format '{{.Repository}}:{{.Tag}}' | grep '^kindest/node:' || true))
      # sort by version descending (fallback if sort -V not available)
      if [ ${#AVAILABLE_IMAGES[@]} -gt 1 ]; then
        AVAILABLE_IMAGES=($(printf '%s\n' "${AVAILABLE_IMAGES[@]}" | sort -t: -k2 -rV 2>/dev/null || printf '%s\n' "${AVAILABLE_IMAGES[@]}" | sort -r))
      fi
      if [ ${#AVAILABLE_IMAGES[@]} -eq 0 ]; then
        err "No kindest/node image found. Pull one first: docker pull kindest/node:v1.31.6"
      elif [ ${#AVAILABLE_IMAGES[@]} -eq 1 ]; then
        KIND_NODE_IMAGE="${AVAILABLE_IMAGES[0]}"
        log "  Using Kind node image: $KIND_NODE_IMAGE"
      else
        echo -e "${YELLOW}Available Kind node images:${NC}"
        for i in "${!AVAILABLE_IMAGES[@]}"; do
          echo "  $((i+1)). ${AVAILABLE_IMAGES[$i]}"
        done
        echo -n "Select [1]: "
        read -r CHOICE
        CHOICE=${CHOICE:-1}
        KIND_NODE_IMAGE="${AVAILABLE_IMAGES[$((CHOICE-1))]}"
        log "  Selected: $KIND_NODE_IMAGE"
      fi
    fi

    if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
      warn "Cluster '$CLUSTER_NAME' already exists, skipping creation"
    else
      kind create cluster --name "$CLUSTER_NAME" --image "$KIND_NODE_IMAGE" --wait 60s
    fi
    kubectl cluster-info --context "kind-${CLUSTER_NAME}"
  fi

  log "Step 2: Loading images into Kind cluster..."
  for img in "${ALL_APP_IMAGES[@]}"; do
    # --build or --fat-base images: already in docker, load directly into Kind
    if { [ "$USE_BUILD" = true ] && is_build_image "$img"; } || \
       { [ "$USE_FAT_BASE" = true ] && is_fat_base_image "$img"; }; then
      log "  Loading (built): $img"
      kind load docker-image "$img" --name "$CLUSTER_NAME" 2>/dev/null \
        || warn "    Failed to load $img into Kind"
      continue
    fi

    fname="$(image_to_filename "$img").tar"
    tarpath="$IMAGES_DIR/$fname"
    if [ ! -f "$tarpath" ]; then
      # Fallback: if the image is already in local docker, load it directly.
      if docker image inspect "$img" &>/dev/null; then
        log "  Tar missing, loading from local docker: $img"
        kind load docker-image "$img" --name "$CLUSTER_NAME" 2>/dev/null \
          || warn "    Failed to kind-load $img"
      else
        warn "  Image tar not found and not in docker: $fname"
      fi
      continue
    fi

    log "  Loading: $img"
    # Try docker load + kind load first; fallback to direct ctr import
    if docker load -i "$tarpath" 2>/dev/null | grep -q "Loaded"; then
      kind load docker-image "$img" --name "$CLUSTER_NAME" 2>/dev/null \
        || docker exec -i "$CONTROL_PLANE" ctr --namespace=k8s.io images import - < "$tarpath" 2>/dev/null \
        || warn "    Failed to load $img into Kind"
    else
      docker exec -i "$CONTROL_PLANE" ctr --namespace=k8s.io images import - < "$tarpath" 2>/dev/null \
        || warn "    Failed to ctr-import $img"
    fi
  done

  # Alias <img>-<arch> → <img> for release tars built with the suffix
  # convention. Safe no-op when --build/--fat-base put clean tags directly.
  log "  Aliasing arch-suffixed tags in Kind..."
  alias_arch_images_kind

else
  # ── K8s Mode ─────────────────────────────────────────────────────────
  log "Step 1: Skipping cluster creation (--no-kind, using existing K8s cluster)"
  kubectl cluster-info || err "Cannot connect to K8s cluster. Check KUBECONFIG."

  log "Step 2: Loading images into K8s nodes..."

  # For --build or --fat-base images in K8s mode, save to temp tar first
  if [ "$USE_BUILD" = true ] || [ "$USE_FAT_BASE" = true ]; then
    BUILD_TMP_DIR=$(mktemp -d)
    for img in "${BUILD_IMAGES[@]}"; do
      fname="$(image_to_filename "$img").tar"
      log "  Saving built image $img to temp tar..."
      docker save -o "$BUILD_TMP_DIR/$fname" "$img"
    done
  fi

  if [ -n "${K8S_NODES:-}" ]; then
    # ── Multi-node: SCP tars to each node, then ctr import ────────────
    for node in $K8S_NODES; do
      log "  Node: $node"
      ssh "${K8S_NODE_USER}@${node}" "mkdir -p ${IMAGE_DIR}" 2>/dev/null || true

      for img in "${ALL_APP_IMAGES[@]}"; do
        fname="$(image_to_filename "$img").tar"

        # --build images use temp tar; others use offline tar
        if [ "$USE_BUILD" = true ] && is_build_image "$img"; then
          tarpath="$BUILD_TMP_DIR/$fname"
        elif [ "$USE_FAT_BASE" = true ] && is_fat_base_image "$img"; then
          # fat-base built images: save to temp tar for SCP/ctr import
          tarpath="$BUILD_TMP_DIR/$fname"
          if [ ! -f "$tarpath" ]; then
            docker save -o "$tarpath" "$img" 2>/dev/null
          fi
        else
          tarpath="$IMAGES_DIR/$fname"
        fi

        if [ ! -f "$tarpath" ]; then
          warn "    Image tar not found: $fname"
          continue
        fi

        log "    Loading: $img"
        scp -q "$tarpath" "${K8S_NODE_USER}@${node}:${IMAGE_DIR}/$fname"
        ssh "${K8S_NODE_USER}@${node}" "ctr -n k8s.io images import ${IMAGE_DIR}/$fname" 2>/dev/null \
          || warn "    Failed to import $img on $node"
      done

      # Alias arch-suffixed tags on this node before moving to the next.
      log "  Aliasing arch-suffixed tags on $node..."
      alias_arch_images_k8s_remote "$node"

      # Clean up remote temp files
      ssh "${K8S_NODE_USER}@${node}" "rm -rf ${IMAGE_DIR}" 2>/dev/null || true
    done
  else
    # ── Single-node / local: ctr import directly ──────────────────────
    if command -v ctr &>/dev/null; then
      for img in "${ALL_APP_IMAGES[@]}"; do
        fname="$(image_to_filename "$img").tar"

        if [ "$USE_BUILD" = true ] && is_build_image "$img"; then
          tarpath="$BUILD_TMP_DIR/$fname"
        elif [ "$USE_FAT_BASE" = true ] && is_fat_base_image "$img"; then
          # fat-base built images: save to temp tar for SCP/ctr import
          tarpath="$BUILD_TMP_DIR/$fname"
          if [ ! -f "$tarpath" ]; then
            docker save -o "$tarpath" "$img" 2>/dev/null
          fi
        else
          tarpath="$IMAGES_DIR/$fname"
        fi

        if [ ! -f "$tarpath" ]; then
          warn "  Image tar not found: $fname"
          continue
        fi

        log "  Loading: $img"
        ctr -n k8s.io images import "$tarpath" 2>/dev/null \
          || warn "  Failed to import $img"
      done
      log "  Aliasing arch-suffixed tags locally..."
      alias_arch_images_k8s_local
    else
      warn "  'ctr' not found and K8S_NODES not set."
      warn "  Please load images manually into containerd on all nodes:"
      warn "    ctr -n k8s.io images import <image>.tar"
    fi
  fi

  # Clean up temp build tars
  if { [ "$USE_BUILD" = true ] || [ "$USE_FAT_BASE" = true ]; } && [ -d "${BUILD_TMP_DIR:-}" ]; then
    rm -rf "$BUILD_TMP_DIR"
  fi
fi

# ════════════════════════════════════════════════════════════════════════
# Steps 3-8: Common for both Kind and K8s
# ════════════════════════════════════════════════════════════════════════

# ── Step 3: Install CRDs (Gateway API + Envoy Gateway) ──────────────────
log "Step 3: Installing CRDs (via scripts/install-crds.sh)..."
"$SCRIPT_DIR/install-crds.sh"

# ── Step 4: Re-pack umbrella subcharts (so code changes are picked up) ──
log "Step 4: Packaging umbrella subcharts..."
"$SCRIPT_DIR/package-umbrella.sh" >/dev/null

# ── Step 5: Install the entire stack in one helm command ────────────────
log "Step 5: helm install aidp-iam (all 5 components in one release)..."
helm upgrade -i aidp-iam "$PROJECT_DIR/charts/aidp-iam" \
  --namespace aidp-iam --create-namespace \
  --skip-crds \
  --wait --timeout 15m 2>&1 | tail -5

# ── Summary ───────────────────────────────────────────────────────────────
log ""
log "==============================================="
MODE_DESC=""
[ "$USE_KIND" = true ] && MODE_DESC="Kind" || MODE_DESC="K8s"
[ "$USE_BUILD" = true ] && MODE_DESC="$MODE_DESC + build"
[ "$USE_FAT_BASE" = true ] && MODE_DESC="$MODE_DESC + fat-base"
MODE_DESC="$MODE_DESC ($PLATFORM)"
log "da-cluster deployment complete! ($MODE_DESC mode)"
log "==============================================="
log ""
log "Pods by namespace:"
for ns in "$KEYCLOAK_NS" "$OPA_NS" "$RESOURCE_SYNC_NS" "$ENVOY_GATEWAY_NS" "$MOCK_KB_NS" "$MOCK_RUBIK_NS" "$MOCK_MEMORY_NS"; do
  log "  $ns:"
  kubectl -n "$ns" get pods --no-headers 2>/dev/null | while read line; do echo "    $line"; done
done
log ""
EG_SVC=$(kubectl -n "$ENVOY_GATEWAY_NS" get svc -l gateway.envoyproxy.io/owning-gateway-name=eg -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo '<envoy-proxy-svc>')
if [ "$USE_KIND" = true ]; then
  log "To access via port-forward:"
  log "  kubectl -n $ENVOY_GATEWAY_NS port-forward svc/$EG_SVC 8080:80 &"
else
  log "To access the gateway:"
  log "  Option 1 (port-forward): kubectl -n $ENVOY_GATEWAY_NS port-forward svc/$EG_SVC 8080:80 --address 0.0.0.0 &"
  log "  Option 2 (NodePort):     kubectl -n $ENVOY_GATEWAY_NS patch svc $EG_SVC -p '{\"spec\":{\"type\":\"NodePort\",\"ports\":[{\"port\":80,\"nodePort\":30080}]}}'"
fi
log "  curl http://localhost:8080/realms/master/.well-known/openid-configuration"
log ""
log "Run tests:"
log "  ./scripts/test.sh"
