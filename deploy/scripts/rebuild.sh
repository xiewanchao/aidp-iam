#!/usr/bin/env bash
# ============================================================================
# rebuild.sh — Quick rebuild of the merged aidp-iam-app image (v1.4 layout).
#
# Dev iteration: rebuild the 4-in-1 IAM image from source, load into the
# cluster, and roll the iam-services Deployment.
#
# Usage:
#   bash deploy/scripts/rebuild.sh                    # rebuild aidp-iam-app:v1 (Kind, amd64)
#   bash deploy/scripts/rebuild.sh init               # rebuild keycloak-init:v2 + re-run Job
#   bash deploy/scripts/rebuild.sh app init           # both
#   bash deploy/scripts/rebuild.sh --no-kind          # target existing K8s, not Kind
#   bash deploy/scripts/rebuild.sh --arch arm64       # cross-build for arm64 (QEMU, slower)
#
# Environment:
#   CLUSTER_NAME    Kind cluster name (default: da-cluster)
#   K8S_NODES       space-separated node IPs for K8s mode (no Kind)
#   K8S_NODE_USER   ssh user for K8s nodes (default: root)
#   ARCH            target arch (amd64/arm64, default: host arch)
#                   override via --arch or env. arm64 on amd64 host uses
#                   `docker buildx --platform=linux/arm64` (QEMU emulation).
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
AUTH_DIR="$REPO_DIR"

CLUSTER_NAME="${CLUSTER_NAME:-da-cluster}"
IAM_NS="aidp-iam"
KEYCLOAK_NS="keycloak"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Args ─────────────────────────────────────────────────────────────────
USE_KIND=true
BUILD_APP=false
BUILD_INIT=false
TARGET_SPECIFIED=false
ARCH="${ARCH:-$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')}"
while [ $# -gt 0 ]; do
  case "$1" in
    app)        BUILD_APP=true; TARGET_SPECIFIED=true; shift ;;
    init)       BUILD_INIT=true; TARGET_SPECIFIED=true; shift ;;
    --no-kind)  USE_KIND=false; shift ;;
    --arch)     ARCH="$2"; shift 2 ;;
    -h|--help)
      sed -n '/^# ===/,/^# ===/p' "$0" | sed 's/^# \?//'
      exit 0 ;;
    *) err "Unknown arg: $1 (use --help)" ;;
  esac
done
[ "$TARGET_SPECIFIED" = false ] && BUILD_APP=true
log "Target arch: $ARCH"

K8S_NODE_USER="${K8S_NODE_USER:-root}"

image_to_filename() { echo "$1" | sed 's|/|_|g; s|:|_|g'; }

# ── Push a freshly-built image into the cluster's container runtime ─────
load_image_to_cluster() {
  local img="$1"
  if [ "$USE_KIND" = true ]; then
    log "  load $img → kind/$CLUSTER_NAME"
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
    err "neither kind/--no-kind+ctr nor K8S_NODES available"
  fi
  rm -f "$tar"
}

# ── aidp-iam-app:v1 (4-in-1 supervisord image) ──────────────────────────
if [ "$BUILD_APP" = true ]; then
  log "Building aidp-iam-app:v1..."
  DOCKERFILE="$REPO_DIR/build/docker/aidp-iam-app/Dockerfile"
  HOST_ARCH=$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')
  if [ "$ARCH" = "$HOST_ARCH" ]; then
    docker build -f "$DOCKERFILE" -t aidp-iam-app:v1 "$AUTH_DIR" >/dev/null
  else
    log "  cross-build via buildx (linux/$ARCH)..."
    CTX_WIN=$(cygpath -m "$AUTH_DIR" 2>/dev/null || echo "$AUTH_DIR")
    DOCKERFILE_WIN=$(cygpath -m "$DOCKERFILE" 2>/dev/null || echo "$DOCKERFILE")
    docker buildx build --platform="linux/$ARCH" --load \
      -f "$DOCKERFILE_WIN" -t aidp-iam-app:v1 "$CTX_WIN" >/dev/null
  fi
  load_image_to_cluster aidp-iam-app:v1
  log "Rolling iam-services DaemonSet..."
  kubectl -n "$IAM_NS" rollout restart daemonset/iam-services
  kubectl -n "$IAM_NS" rollout status  daemonset/iam-services --timeout=300s
fi

# ── keycloak-init:v2 (re-runs the Job to refresh seed data) ─────────────
if [ "$BUILD_INIT" = true ]; then
  log "Building keycloak-init:v2..."
  docker build -t keycloak-init:v2 "$REPO_DIR/build/docker/keycloak-init" >/dev/null
  load_image_to_cluster keycloak-init:v2
  log "Re-running keycloak-init Job (delete + helm upgrade)..."
  kubectl -n "$KEYCLOAK_NS" delete job -l component=init-job 2>/dev/null || true
  helm upgrade aidp-iam "$REPO_DIR/deploy/helm/aidp-iam" \
       -n "$IAM_NS" --reuse-values 2>/dev/null \
    || warn "helm upgrade aidp-iam failed; re-create the Job manually if needed"
  kubectl -n "$KEYCLOAK_NS" wait --for=condition=complete job -l component=init-job --timeout=5m \
    || warn "keycloak-init Job did not complete in 5m"
fi

log ""
log "Done."
[ "$BUILD_APP" = true ]  && kubectl -n "$IAM_NS"      get pod -l app=iam-services --no-headers
[ "$BUILD_INIT" = true ] && kubectl -n "$KEYCLOAK_NS" get pod -l component=init-job --no-headers
