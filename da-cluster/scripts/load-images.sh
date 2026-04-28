#!/usr/bin/env bash
set -uo pipefail

# ============================================================================
# load-images.sh — Load aidp-iam offline image tars into K8s nodes (only).
#
# Does NOT touch the cluster (no kubectl, no helm). Use this when you just
# want to re-import images or re-run the arch-suffix alias step without
# re-deploying the stack.
#
# Usage:
#   ./scripts/load-images.sh                     # load all + alias
#   ./scripts/load-images.sh --with-mocks        # also include mock-* images
#   ./scripts/load-images.sh --alias-only        # skip load, only alias
#   ./scripts/load-images.sh --help
#
# Environment variables:
#   PLATFORM        amd64 / arm64   (default: auto-detect via uname -m)
#   K8S_NODES       "IP1 IP2 ..."   (default: local-only)
#   K8S_NODE_USER   ssh user        (default: root)
#   IMAGE_DIR       remote temp dir (default: /tmp/da-images)
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OFFLINE_DIR="$PROJECT_DIR/offline"

K8S_NODE_USER="${K8S_NODE_USER:-root}"
IMAGE_DIR="${IMAGE_DIR:-/tmp/da-images}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── Args ─────────────────────────────────────────────────────────────────
WITH_MOCKS=false
ALIAS_ONLY=false
for arg in "$@"; do
  case "$arg" in
    --with-mocks)  WITH_MOCKS=true ;;
    --alias-only)  ALIAS_ONLY=true ;;
    -h|--help)
      sed -n '/^# ===/,/^# ===/{/^# /{s/^# \?//; p}}' "$0" | head -30
      exit 0 ;;
    *) err "Unknown flag: $arg (use --help)" ;;
  esac
done

# ── Platform ─────────────────────────────────────────────────────────────
if [ -z "${PLATFORM:-}" ]; then
  case "$(uname -m)" in
    x86_64|amd64)  PLATFORM=amd64 ;;
    aarch64|arm64) PLATFORM=arm64 ;;
    *)             PLATFORM=amd64; warn "Unknown arch $(uname -m), defaulting to amd64" ;;
  esac
fi
log "Platform: $PLATFORM"

IMAGES_DIR="$OFFLINE_DIR/images/$PLATFORM"
if [ "$ALIAS_ONLY" = false ]; then
  [ -d "$IMAGES_DIR" ] || err "Missing $IMAGES_DIR/ — run build-release-images.sh first"
fi

# ── Image lists (keep in sync with build-release-images.sh) ──────────────
# v1.4 layout: 4 Python services merged into aidp-iam-app:v1, OPA replaces
# OPAL controller, rancher/kubectl is the wait-for-secret initContainer image.
ALL_APP_IMAGES=(
  "aidp-iam-app:v1"
  "keycloak-init:v2"
  "keycloak-custom:26.5.2"
  "postgres:17"
  "docker.io/envoyproxy/gateway:v1.7.0"
  "docker.io/envoyproxy/envoy:distroless-v1.37.0"
  "openpolicyagent/opa:0.70.0-static"
  "rancher/kubectl:v1.31.0"
)
if [ "$WITH_MOCKS" = true ]; then
  ALL_APP_IMAGES+=("mock-kb:v1" "mock-rubik:v1" "mock-memory:v1")
fi

# Images that ship with -<arch> tag suffix (our buildx customs) — aliased
# back to the clean tag after load so Helm charts can reference them
# without caring about arch. Third-party images keep upstream tags.
CUSTOM_ARCH_IMAGES=(
  "aidp-iam-app:v1"
  "keycloak-init:v2"
)
if [ "$WITH_MOCKS" = true ]; then
  CUSTOM_ARCH_IMAGES+=("mock-kb:v1" "mock-rubik:v1" "mock-memory:v1")
fi

# ── Helpers ──────────────────────────────────────────────────────────────
image_to_filename() { echo "$1" | sed 's|/|_|g; s|:|_|g'; }

alias_arch_images_local() {
  local tool=""
  if command -v isula &>/dev/null; then tool=isula
  elif command -v ctr &>/dev/null; then tool=ctr
  else
    warn "  Neither isula nor ctr installed locally — skipping alias step"
    return 0
  fi
  for img in "${CUSTOM_ARCH_IMAGES[@]}"; do
    local src="docker.io/library/${img}-${PLATFORM}"
    local dst="docker.io/library/${img}"
    if [ "$tool" = isula ]; then
      if isula tag "$src" "$dst" 2>/dev/null; then
        log "    aliased (isula): $src -> $dst"
      fi
    else
      if ctr -n k8s.io images tag "$src" "$dst" 2>/dev/null; then
        log "    aliased (ctr):   $src -> $dst"
      fi
    fi
  done
  return 0
}

alias_arch_images_remote() {
  local node="$1"
  for img in "${CUSTOM_ARCH_IMAGES[@]}"; do
    local src="docker.io/library/${img}-${PLATFORM}"
    local dst="docker.io/library/${img}"
    if ssh "${K8S_NODE_USER}@${node}" "isula tag '$src' '$dst' 2>/dev/null || \
        ctr -n k8s.io images tag '$src' '$dst' 2>/dev/null" 2>/dev/null; then
      log "    aliased on $node: $src -> $dst"
    fi
  done
  return 0
}

load_local() {
  for img in "${ALL_APP_IMAGES[@]}"; do
    local fname tarpath
    fname="$(image_to_filename "$img").tar"
    tarpath="$IMAGES_DIR/$fname"
    if [ ! -f "$tarpath" ]; then
      warn "  Tar missing: $fname (image $img will not be loaded)"
      continue
    fi
    log "  Loading: $img"
    isula load -i "$tarpath" 2>/dev/null \
      || ctr -n k8s.io images import "$tarpath" 2>/dev/null \
      || warn "  Failed to load $img (need isula or ctr)"
  done
}

load_remote() {
  local node="$1"
  log "  Node: $node"
  ssh "${K8S_NODE_USER}@${node}" "mkdir -p ${IMAGE_DIR}" 2>/dev/null || true
  for img in "${ALL_APP_IMAGES[@]}"; do
    local fname tarpath
    fname="$(image_to_filename "$img").tar"
    tarpath="$IMAGES_DIR/$fname"
    if [ ! -f "$tarpath" ]; then
      warn "  Tar missing: $fname"
      continue
    fi
    log "    Loading: $img"
    scp -q "$tarpath" "${K8S_NODE_USER}@${node}:${IMAGE_DIR}/$fname"
    ssh "${K8S_NODE_USER}@${node}" "isula load -i ${IMAGE_DIR}/$fname" 2>/dev/null \
      || ssh "${K8S_NODE_USER}@${node}" "ctr -n k8s.io images import ${IMAGE_DIR}/$fname" 2>/dev/null \
      || warn "    Failed to load $img on $node"
  done
  ssh "${K8S_NODE_USER}@${node}" "rm -rf ${IMAGE_DIR}" 2>/dev/null || true
}

# ── Main ─────────────────────────────────────────────────────────────────
if [ "$ALIAS_ONLY" = true ]; then
  log "Mode: alias-only (skipping image load)"
else
  log "Mode: load + alias"
fi

if [ -n "${K8S_NODES:-}" ]; then
  log "Target: remote nodes = $K8S_NODES"
  for node in $K8S_NODES; do
    if [ "$ALIAS_ONLY" = false ]; then
      load_remote "$node"
    fi
    log "  Aliasing arch-suffixed tags on $node..."
    alias_arch_images_remote "$node"
  done
else
  log "Target: local node"
  if [ "$ALIAS_ONLY" = false ]; then
    command -v isula &>/dev/null || command -v ctr &>/dev/null \
      || err "Neither 'isula' nor 'ctr' found. Can't load."
    load_local
  fi
  log "Aliasing arch-suffixed tags locally..."
  alias_arch_images_local
fi

log ""
log "Done. Summary:"
if command -v isula &>/dev/null; then
  isula images 2>/dev/null | grep -E "keycloak|opal|resource-sync|mock-|postgres|envoyproxy|permitio" | head -20
elif command -v ctr &>/dev/null; then
  ctr -n k8s.io images list -q 2>/dev/null | grep -E "keycloak|opal|resource-sync|mock-|postgres|envoyproxy|permitio" | head -20
fi
