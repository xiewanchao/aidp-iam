#!/usr/bin/env bash
# ============================================================================
# setup.sh - Full cluster setup: build images, deploy Helm charts, and initialize seed data.
#
# Two-step Helm deploy:
#   1. aidp-gateway  (Envoy Gateway controller + CRDs + Gateway/EnvoyProxy)
#   2. aidp-iam      (Keycloak + Postgres + 4-in-1 IAM services + OPA + routes)
#
# Usage:
#   bash deploy/scripts/setup.sh                        # Kind cluster (create + deploy)
#   bash deploy/scripts/setup.sh --no-kind              # Deploy to existing K8s
#   bash deploy/scripts/setup.sh --skip-build           # Skip image build, deploy only
#   bash deploy/scripts/setup.sh --skip-init            # Skip keycloak-init Job
#   bash deploy/scripts/setup.sh --arch arm64           # Cross-build for arm64
#
# Environment:
#   CLUSTER_NAME    Kind cluster name (default: da-cluster)
#   K8S_NODES       Space-separated node IPs for --no-kind mode
#   K8S_NODE_USER   SSH user for K8s nodes (default: root)
#   ARCH            Target arch: amd64 | arm64 (default: host arch)
#   GATEWAY_PORT        HTTP NodePort exposed by Envoy (default: 30080)
#   GATEWAY_HTTPS_PORT  HTTPS NodePort exposed by Envoy (default: 30443)
#   KEYCLOAK_HOST       Optional static Keycloak public URL. Leave empty for
#                       dynamic Host/X-Forwarded HTTPS mode.
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEPLOY_DIR="$REPO_DIR/deploy"
AUTH_DIR="$REPO_DIR"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
log()     { echo -e "${GREEN}[INFO]${NC}  $*"; }
section() { echo -e "\n${CYAN}== $* ==${NC}"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()     { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# Defaults
CLUSTER_NAME="${CLUSTER_NAME:-da-cluster}"
K8S_NODE_USER="${K8S_NODE_USER:-root}"
ARCH="${ARCH:-$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')}"
GATEWAY_PORT="${GATEWAY_PORT:-30080}"
GATEWAY_HTTPS_PORT="${GATEWAY_HTTPS_PORT:-30443}"
GATEWAY_NS="aidp-gateway"
IAM_NS="aidp-iam"
KEYCLOAK_NS="keycloak"

USE_KIND=true
SKIP_BUILD=false
SKIP_INIT=false

# Parse args
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

KEYCLOAK_HOST="${KEYCLOAK_HOST:-}"
log "Arch: $ARCH | Kind: $USE_KIND | Skip build: $SKIP_BUILD | Skip init: $SKIP_INIT"
if [ -n "$KEYCLOAK_HOST" ]; then
  log "Keycloak static hostname: $KEYCLOAK_HOST"
else
  log "Keycloak hostname: dynamic (Gateway forwards HTTPS host/port)"
fi

# Helper: load image into cluster runtime
image_to_filename() { echo "$1" | sed 's|/|_|g; s|:|_|g'; }

load_image() {
  local img="$1"
  if [ "$USE_KIND" = true ]; then
    log "  kind load: $img"
    kind load docker-image "$img" --name "$CLUSTER_NAME" 2>/dev/null && return
    warn "  kind load failed for $img; falling back to ctr import"
    docker save "$img" | docker exec --privileged -i "${CLUSTER_NAME}-control-plane" \
      ctr -n k8s.io images import - >/dev/null \
      || err "kind load failed for $img"
    return
  fi
  local fname; fname="$(image_to_filename "$img").tar"
  local tar; tar=$(mktemp)
  docker save -o "$tar" "$img"
  if [ -n "${K8S_NODES:-}" ]; then
    for node in $K8S_NODES; do
      log "  scp $img -> $node"
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

# Helper: docker build (native or cross-arch via buildx)
ensure_local_image() {
  local img="$1"
  log "  docker pull: $img"
  if docker pull --platform="linux/$ARCH" "$img" >/dev/null; then
    return
  fi
  if docker image inspect "$img" >/dev/null 2>&1; then
    warn "  docker pull failed for $img; using existing local image"
    return
  fi
  err "docker pull failed for $img and no local image is available"
}

docker_build() {
  local tag="$1"; local ctx="$2"; local dockerfile="${3:-}"
  local host_arch; host_arch=$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')
  if [ "$ARCH" = "$host_arch" ]; then
    if [ -n "$dockerfile" ]; then
      docker build -f "$dockerfile" -t "$tag" "$ctx"
    else
      docker build -t "$tag" "$ctx"
    fi
  else
    local ctx_path; ctx_path=$(cygpath -m "$ctx" 2>/dev/null || echo "$ctx")
    local dockerfile_path="$dockerfile"
    if [ -n "$dockerfile_path" ]; then
      dockerfile_path=$(cygpath -m "$dockerfile_path" 2>/dev/null || echo "$dockerfile_path")
      docker buildx build --platform="linux/$ARCH" --load -f "$dockerfile_path" -t "$tag" "$ctx_path"
    else
      docker buildx build --platform="linux/$ARCH" --load -t "$tag" "$ctx_path"
    fi
  fi
}

# ----------------------------------------------------------------------------
docker_mount_path() {
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$1"
  else
    echo "$1"
  fi
}

build_keycloak_custom_artifacts() {
  local custom_dir="$REPO_DIR/build/docker/keycloak-custom"
  local spi_dir="$REPO_DIR/apps/keycloak-spi"
  local theme_dir="$REPO_DIR/apps/keycloak-theme"
  local theme_output_dir="$theme_dir"
  local theme_build_dir=""

  log "Preparing Keycloak SPI provider..."
  if [ "${BUILD_KEYCLOAK_SPI:-false}" != "true" ] && [ -f "$custom_dir/data-agent-mapper.jar" ]; then
    warn "Using existing $custom_dir/data-agent-mapper.jar (set BUILD_KEYCLOAK_SPI=true to rebuild it)"
  elif command -v mvn >/dev/null 2>&1; then
    if (cd "$spi_dir" && mvn -q -DskipTests -Dmaven.test.skip=true package); then
      cp "$spi_dir/target/structured-role-mapper-1.0.0.jar" "$custom_dir/data-agent-mapper.jar"
    elif [ "${BUILD_KEYCLOAK_SPI:-false}" = "true" ]; then
      err "Forced Keycloak SPI rebuild failed"
    elif [ -f "$custom_dir/data-agent-mapper.jar" ]; then
      warn "Maven build failed; keeping existing $custom_dir/data-agent-mapper.jar"
    else
      err "Maven build failed and no existing data-agent-mapper.jar is available"
    fi
  else
    local spi_mount; spi_mount="$(docker_mount_path "$spi_dir")"
    MSYS_NO_PATHCONV=1 docker run --rm \
      -v "$spi_mount:/workspace" \
      -w /workspace \
      maven:3.9-eclipse-temurin-21 \
      mvn -q -DskipTests -Dmaven.test.skip=true package
    cp "$spi_dir/target/structured-role-mapper-1.0.0.jar" "$custom_dir/data-agent-mapper.jar"
  fi

  log "Preparing Keycloak login theme provider..."
  if [ "${BUILD_KEYCLOAK_THEME:-false}" != "true" ] && [ -f "$custom_dir/keycloak-theme.jar" ]; then
    warn "Using existing $custom_dir/keycloak-theme.jar (set BUILD_KEYCLOAK_THEME=true to rebuild it)"
    theme_output_dir=""
  elif command -v mvn >/dev/null 2>&1; then
    if [ ! -d "$theme_dir/node_modules" ]; then
      (cd "$theme_dir" && npm ci)
    fi
    if ! (cd "$theme_dir" && npm run build-keycloak-theme); then
      warn "Keycloakify Maven jar build failed; packaging generated theme resources directly."
      (cd "$theme_dir" && npm run build && (npx keycloakify build || true))
      if [ -d "$theme_dir/dist_keycloak/resources/theme/password-reset-confirm" ]; then
        rm -rf "$theme_dir/.theme-jar-root"
        mkdir -p "$theme_dir/.theme-jar-root/META-INF" "$theme_dir/.theme-jar-root/theme"
        cp -R "$theme_dir/dist_keycloak/resources/theme/." "$theme_dir/.theme-jar-root/theme/"
        printf "{\n    \"themes\": [{\n        \"name\": \"password-reset-confirm\",\n        \"types\": [\"login\"]\n    }]\n}\n" > "$theme_dir/.theme-jar-root/META-INF/keycloak-themes.json"
        node "$custom_dir/build-theme-jar.mjs" \
          "$theme_dir/.theme-jar-root" \
          "$theme_dir/dist_keycloak/keycloak-theme-for-kc-all-other-versions.jar"
      elif [ "${BUILD_KEYCLOAK_THEME:-false}" = "true" ]; then
        err "Forced Keycloak theme rebuild failed"
      elif [ -f "$custom_dir/keycloak-theme.jar" ]; then
        warn "Keycloakify did not generate theme resources; keeping existing $custom_dir/keycloak-theme.jar"
        theme_output_dir=""
      else
        err "Keycloak theme build failed and no existing keycloak-theme.jar is available"
      fi
    fi
  else
    theme_build_dir="$custom_dir/.theme-build"
    rm -rf "$theme_build_dir"
    mkdir -p "$theme_build_dir"
    (cd "$theme_dir" && tar --exclude='./node_modules' --exclude='./dist' -cf - .) \
      | (cd "$theme_build_dir" && tar -xf -)
    cp "$custom_dir/build-theme-jar.mjs" "$theme_build_dir/build-theme-jar.mjs"
    theme_output_dir="$theme_build_dir"
    local theme_mount; theme_mount="$(docker_mount_path "$theme_build_dir")"
    MSYS_NO_PATHCONV=1 docker run --rm \
      -v "$theme_mount:/workspace" \
      -w /workspace \
      node:24-bookworm \
      bash -lc 'set -e; apt-get update >/dev/null && apt-get install -y --no-install-recommends maven >/dev/null; npm ci && npm run build && (npx keycloakify build || true); test -d dist_keycloak/resources/theme/password-reset-confirm; rm -rf .theme-jar-root; mkdir -p .theme-jar-root/META-INF .theme-jar-root/theme; cp -R dist_keycloak/resources/theme/. .theme-jar-root/theme/; printf "{\n    \"themes\": [{\n        \"name\": \"password-reset-confirm\",\n        \"types\": [\"login\"]\n    }]\n}\n" > .theme-jar-root/META-INF/keycloak-themes.json; rm -f dist_keycloak/keycloak-theme-for-kc-all-other-versions.jar; node ./build-theme-jar.mjs .theme-jar-root dist_keycloak/keycloak-theme-for-kc-all-other-versions.jar'
  fi
  if [ -n "$theme_output_dir" ] && [ -f "$theme_output_dir/dist_keycloak/keycloak-theme-for-kc-all-other-versions.jar" ]; then
    cp "$theme_output_dir/dist_keycloak/keycloak-theme-for-kc-all-other-versions.jar" "$custom_dir/keycloak-theme.jar"
  elif [ "${BUILD_KEYCLOAK_THEME:-false}" = "true" ]; then
    err "Forced Keycloak theme rebuild did not produce keycloak-theme.jar"
  elif [ -f "$custom_dir/keycloak-theme.jar" ]; then
    warn "Using existing $custom_dir/keycloak-theme.jar"
  else
    err "Keycloak theme jar is missing"
  fi
  if [ -n "$theme_build_dir" ]; then
    rm -rf "$theme_build_dir"
  fi
}

# STEP 1: Create Kind cluster (if needed)
# ----------------------------------------------------------------------------
if [ "$USE_KIND" = true ]; then
  section "Step 1: Kind cluster"
  if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
    log "Kind cluster '$CLUSTER_NAME' already exists; skipping creation."
  else
    log "Creating Kind cluster '$CLUSTER_NAME'..."
    KIND_CONFIG="$DEPLOY_DIR/kind/kind-config.yaml"
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

# ----------------------------------------------------------------------------
# STEP 2: Build images
# ----------------------------------------------------------------------------
section "Step 2: Build images"

if [ "$SKIP_BUILD" = true ]; then
  log "Skipping image build (--skip-build)."
else
  # 2a. aidp-iam-app:v1 (4-in-1: keycloak-proxy + pep-proxy + bundle-server + resource-sync)
  log "Building aidp-iam-app:v1..."
  docker_build "aidp-iam-app:v1" "$REPO_DIR" "$REPO_DIR/build/docker/aidp-iam-app/Dockerfile"
  log "aidp-iam-app:v1 built."

  # 2b. keycloak-custom:26.5.2 (Keycloak + mapper/theme/CAS providers)
  log "Building keycloak-custom:26.5.2..."
  build_keycloak_custom_artifacts
  docker_build "keycloak-custom:26.5.2" "$REPO_DIR/build/docker/keycloak-custom"
  log "keycloak-custom:26.5.2 built."

  # 2c. keycloak-init:v2
  log "Building keycloak-init:v2..."
  docker_build "keycloak-init:v2" "$REPO_DIR/build/docker/keycloak-init"
  log "keycloak-init:v2 built."

  # 2d. gateway-manager:v1 (Gateway certificate and log management API)
  log "Building gateway-manager:v1..."
  docker_build "gateway-manager:v1" "$REPO_DIR" "$REPO_DIR/build/docker/gateway-manager/Dockerfile"
  log "gateway-manager:v1 built."
fi

  # 2e. Load images into cluster
section "Step 2e: Load images into cluster"
load_image "aidp-iam-app:v1"
load_image "keycloak-custom:26.5.2"
load_image "keycloak-init:v2"
load_image "gateway-manager:v1"

  # External runtime images used by the charts. Loading them into Kind avoids
  # node-side registry/proxy dependencies during Helm hooks and pod startup.
for img in \
  "docker.io/envoyproxy/gateway:v1.7.2" \
  "docker.io/envoyproxy/envoy:v1.36.5" \
  "docker.io/alpine/kubectl:1.34.1" \
  "postgres:17" \
  "openpolicyagent/opa:0.42.2-static"; do
  ensure_local_image "$img"
  load_image "$img"
done

# ----------------------------------------------------------------------------
# STEP 3: Deploy aidp-gateway (Envoy Gateway controller + Gateway resources)
# CRDs are bundled under the chart's top-level crds/ directory and are installed
# by Helm on first install for offline production deployments.
# ----------------------------------------------------------------------------
section "Step 3: Helm deploy aidp-gateway"

GATEWAY_CHART="$REPO_DIR/deploy/helm/aidp-gateway"
GATEWAY_RELEASE="aidp-gateway"

if helm status "$GATEWAY_RELEASE" -n "$GATEWAY_NS" >/dev/null 2>&1; then
  log "Upgrading existing Helm release '$GATEWAY_RELEASE'..."
  helm upgrade "$GATEWAY_RELEASE" "$GATEWAY_CHART" \
    --namespace "$GATEWAY_NS" \
    --reuse-values \
    --set gateway.namespace="$GATEWAY_NS" \
    --timeout 5m \
    --wait
else
  log "Installing Helm release '$GATEWAY_RELEASE'..."
  helm install "$GATEWAY_RELEASE" "$GATEWAY_CHART" \
    --namespace "$GATEWAY_NS" \
    --create-namespace \
    --set gateway.namespace="$GATEWAY_NS" \
    --set proxy.service.nodePort="$GATEWAY_PORT" \
    --set proxy.service.httpsNodePort="$GATEWAY_HTTPS_PORT" \
    --timeout 5m \
    --wait
fi

log "Waiting for Envoy Gateway controller..."
kubectl -n "$GATEWAY_NS" wait pod \
  --for=condition=Ready \
  -l control-plane=envoy-gateway \
  --timeout=120s 2>/dev/null \
  || warn "Envoy Gateway controller not ready after 2m"

# ----------------------------------------------------------------------------
# STEP 4: Deploy aidp-iam (Keycloak + IAM services + OPA + routes)
# ----------------------------------------------------------------------------
section "Step 4: Helm deploy aidp-iam"

IAM_CHART="$REPO_DIR/deploy/helm/aidp-iam"
IAM_RELEASE="aidp-iam"

helm_iam_args=(
  --namespace "$IAM_NS"
  --create-namespace
  --set "keycloak.namespaceOverride=$KEYCLOAK_NS"
  --set "keycloak.iamNamespaceOverride=$IAM_NS"
  --set "iam-app.namespace=$IAM_NS"
  --set "iam-app.logCollect.keycloakNamespace=$KEYCLOAK_NS"
  --set "iam-app.logCollect.gatewayNamespace=$GATEWAY_NS"
  --set "routes.gatewayNamespace=$GATEWAY_NS"
  --timeout 10m
  --wait
)

if [ -n "$KEYCLOAK_HOST" ]; then
  helm_iam_args+=(--set "keycloak.keycloak.config.hostname=$KEYCLOAK_HOST")
fi

if [ "$USE_KIND" = true ]; then
  helm_iam_args+=(
    --set "keycloak.keycloak.replicas=1"
    --set "iam-app.replicas=1"
    --set "iam-app.podAntiAffinity.enabled=false"
  )
fi

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

# ----------------------------------------------------------------------------
# STEP 5: Wait for core pods
# ----------------------------------------------------------------------------
section "Step 5: Wait for pods"

wait_pod() {
  local ns="$1"; local label="$2"; local timeout="${3:-300s}"
  log "  Waiting for $label in $ns..."
  kubectl -n "$ns" wait pod --for=condition=Ready -l "$label" \
    --timeout="$timeout" 2>/dev/null \
    || warn "  Timeout waiting for $label in $ns"
}

wait_pod "$KEYCLOAK_NS" "app=keycloak"
wait_pod "$IAM_NS"      "app=iam-services"

# ----------------------------------------------------------------------------
# STEP 6: Run keycloak-init Job (seed data)
# ----------------------------------------------------------------------------
section "Step 6: Keycloak init"

if [ "$SKIP_INIT" = true ]; then
  log "Skipping keycloak-init (--skip-init)."
else
  log "Waiting for Keycloak to be ready..."
  kubectl -n "$KEYCLOAK_NS" wait pod --for=condition=Ready -l app=keycloak \
    --timeout=300s 2>/dev/null \
    || warn "Keycloak not ready after 5m 鈥?init may fail."

  log "Deleting any previous keycloak-init Jobs..."
  kubectl -n "$KEYCLOAK_NS" delete job -l component=init-job 2>/dev/null || true

  log "Triggering keycloak-init Job via helm upgrade --reuse-values..."
  helm upgrade "$IAM_RELEASE" "$IAM_CHART" \
    -n "$IAM_NS" \
    --reuse-values \
    --timeout 5m \
    2>/dev/null \
    || warn "helm upgrade for init trigger failed 鈥?check Job manually."

  log "Waiting for keycloak-init Job to complete (up to 5m)..."
  kubectl -n "$KEYCLOAK_NS" wait --for=condition=complete job -l component=init-job \
    --timeout=5m \
    || warn "keycloak-init Job did not complete in 5m 鈥?check logs:"
  kubectl -n "$KEYCLOAK_NS" logs -l component=init-job --tail=30 2>/dev/null || true
fi

# ----------------------------------------------------------------------------
# Done
# ----------------------------------------------------------------------------
section "Setup complete"
echo ""
log "Gateway HTTP     : http://localhost:${GATEWAY_PORT}"
log "Gateway HTTPS    : https://localhost:${GATEWAY_HTTPS_PORT}"
log "Keycloak console : https://localhost:${GATEWAY_HTTPS_PORT}/realms/master/account"
log "IAM API          : https://localhost:${GATEWAY_HTTPS_PORT}/api/v1/common/health"
echo ""
log "Quick checks:"
log "  kubectl -n $IAM_NS      get pod"
log "  kubectl -n $KEYCLOAK_NS get pod"
log "  kubectl -n $GATEWAY_NS  get gateway eg"
echo ""
log "Run tests:"
log "  bash deploy/scripts/test.sh"
