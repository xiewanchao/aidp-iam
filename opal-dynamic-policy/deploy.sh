#!/bin/bash
# deploy.sh – Build, load, and deploy the opal-dynamic-policy stack.
#
# Usage:
#   ./deploy.sh                       # full deploy (K8s core only)
#   DEPLOY_ENVOY_GATEWAY=true ./deploy.sh  # also install Envoy Gateway
#
# Environment variables:
#   NAMESPACE                 – K8s namespace for the OPA stack  (default: opal-dynamic-policy)
#   VERSION                   – image tag                        (default: latest)
#   DEPLOY_ENVOY_GATEWAY      – install Envoy Gateway (default: false)
#   ENVOY_GATEWAY_NAMESPACE   – namespace for the Envoy Gateway control plane
#                               (default: envoy-gateway-system)
#   SKIP_BUILD                – skip docker build step           (default: false)

set -euo pipefail

NAMESPACE=${NAMESPACE:-"opal-dynamic-policy"}
VERSION=${VERSION:-"latest"}
DEPLOY_ENVOY_GATEWAY=${DEPLOY_ENVOY_GATEWAY:-false}
SKIP_BUILD=${SKIP_BUILD:-true}
# Namespace where the Envoy Gateway control plane is deployed.
ENVOY_GATEWAY_NAMESPACE=${ENVOY_GATEWAY_NAMESPACE:-"envoy-gateway-system"}
# GatewayClass name for Envoy Gateway (default shipped class is "eg").
ENVOY_GATEWAY_CLASS=${ENVOY_GATEWAY_CLASS:-"eg"}

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

step()  { echo -e "\n${YELLOW}>>> $*${NC}"; }
info()  { echo -e "${CYAN}    $*${NC}"; }
ok()    { echo -e "${GREEN}    OK${NC}"; }

# # ---------------------------------------------------------------------------
# # Step 1: Build and load the opal-proxy image
# # ---------------------------------------------------------------------------
if [ "$SKIP_BUILD" != "true" ]; then
    step "Building opal-proxy:${VERSION} image..."
    docker build -t "opal-proxy:${VERSION}" .
    ok

    step "Loading image into Kind cluster..."
    kind load docker-image "opal-proxy:${VERSION}" 2>/dev/null \
        || info "Note: 'kind load' failed – image may already be present or cluster is not Kind."
    ok
else
    info "SKIP_BUILD=true – skipping docker build."
fi

# ---------------------------------------------------------------------------
# Step 2: Create namespace
# ---------------------------------------------------------------------------
step "Creating namespace ${NAMESPACE}..."
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -
ok

# ---------------------------------------------------------------------------
# Step 3: Deploy core services (Services + Deployments)
# ---------------------------------------------------------------------------
step "Applying Services..."
kubectl apply -f k8s/service.yaml -n "${NAMESPACE}"
ok

step "Applying Deployments (PostgreSQL / OPAL Server / pep-proxy + bundle-server)..."
kubectl apply -f k8s/deployment.yaml -n "${NAMESPACE}"
ok

# ---------------------------------------------------------------------------
# Step 4: Wait for readiness
# ---------------------------------------------------------------------------
step "Waiting for PostgreSQL to be ready..."
kubectl wait --for=condition=ready pod -l app=postgres \
    -n "${NAMESPACE}" --timeout=120s
ok

step "Waiting for opal-server to be ready..."
kubectl wait --for=condition=available deployment/opal-server \
    -n "${NAMESPACE}" --timeout=90s
ok

step "Waiting for pep-proxy to be ready..."
kubectl wait --for=condition=available deployment/pep-proxy \
    -n "${NAMESPACE}" --timeout=180s
ok

# ---------------------------------------------------------------------------
# Step 5: (Optional) Deploy Envoy Gateway
# ---------------------------------------------------------------------------
if [ "$DEPLOY_ENVOY_GATEWAY" = "true" ]; then

    # ── 5a: Kubernetes Gateway API CRDs (cluster-scoped, install once) ────────
    if kubectl get crd gateways.gateway.networking.k8s.io &>/dev/null; then
        info "Gateway API CRDs already present – skipping install."
    else
        step "Installing Kubernetes Gateway API CRDs..."
        kubectl apply -f \
            https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.4.0/standard-install.yaml
        ok
    fi

    # ── 5b: Envoy Gateway control plane (helm) ────────────────────────────────
    if kubectl get deployment envoy-gateway -n "${ENVOY_GATEWAY_NAMESPACE}" &>/dev/null; then
        info "Envoy Gateway already installed in ${ENVOY_GATEWAY_NAMESPACE} – skipping helm install."
    else
        step "Installing Envoy Gateway via Helm (${ENVOY_GATEWAY_NAMESPACE})..."
        helm upgrade -i eg \
            oci://docker.io/envoyproxy/gateway-helm \
            --version v1.2.1 \
            --create-namespace --namespace "${ENVOY_GATEWAY_NAMESPACE}"
        ok
    fi

    info "GatewayClass: ${ENVOY_GATEWAY_CLASS} (ships with Envoy Gateway by default)"
    info "Apply your Gateway + HTTPRoute manifests separately; the legacy"
    info "k8s/agentgateway.yaml has been removed — Gateway now managed by"
    info "da-cluster/charts/envoy-gateway."

else
    info "DEPLOY_ENVOY_GATEWAY=false – skipping Envoy Gateway install."
    info "To deploy Envoy Gateway run: DEPLOY_ENVOY_GATEWAY=true ./deploy.sh"
fi


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
step "Deployment status:"
kubectl get pods -n "${NAMESPACE}"
echo ""
kubectl get svc  -n "${NAMESPACE}"

echo ""
echo -e "${GREEN}Deployment complete!${NC}"
echo ""
echo "  PEP Proxy (HTTP)   :  http://pep-proxy.${NAMESPACE}.svc.cluster.local:8000"
echo "  PEP Proxy (gRPC)   :  pep-proxy.${NAMESPACE}.svc.cluster.local:9000"
echo "  Bundle Server      :  http://bundle-server.${NAMESPACE}.svc.cluster.local:8001"
echo "  OPAL Server        :  http://opal-server.${NAMESPACE}.svc.cluster.local:7002"
if [ "$DEPLOY_ENVOY_GATEWAY" = "true" ]; then
    echo "  Envoy Gateway CP   :  envoy-gateway.${ENVOY_GATEWAY_NAMESPACE}.svc.cluster.local"
    echo "  GatewayClass       :  ${ENVOY_GATEWAY_CLASS}"
fi
echo ""
echo "  To run integration tests: ./test.sh"
