#!/usr/bin/env bash
# ============================================================================
# install-crds.sh — one-time infrastructure: Gateway API + Envoy Gateway CRDs
#
# Idempotent. Apply to any cluster (kind or existing K8s) before running
# `helm install aidp-iam charts/aidp-iam`.
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OFFLINE_DIR="$PROJECT_DIR/offline"

GATEWAY_API_VERSION="${GATEWAY_API_VERSION:-v1.4.1-experimental}"
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

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

command -v kubectl >/dev/null || err "kubectl not found"

log "Installing Gateway API CRDs (experimental channel)..."
GW_API_CRD="$OFFLINE_DIR/crds/gateway-api-${GATEWAY_API_VERSION}.yaml"
[ -f "$GW_API_CRD" ] || err "Missing $GW_API_CRD — run ./scripts/build-release-images.sh --offline-only first"
kubectl apply --server-side --force-conflicts -f "$GW_API_CRD" >/dev/null

log "Installing Envoy Gateway CRDs..."
for crd in "${ENVOY_GATEWAY_CRDS[@]}"; do
  p="$OFFLINE_DIR/crds/$crd"
  [ -f "$p" ] || err "Missing $p"
  kubectl apply --server-side --force-conflicts -f "$p" >/dev/null
done

log "CRDs installed. Run: helm dep update charts/aidp-iam && helm install aidp-iam charts/aidp-iam -n aidp-iam --create-namespace --wait --timeout 15m"
