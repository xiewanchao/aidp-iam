#!/usr/bin/env bash
# ============================================================================
# build-release-images.sh — 打完整离线部署包：镜像 + Helm chart + CRD
#
# 产出两部分:
#   1. 跨平台镜像 tar 包 → release-images/{amd64,arm64}/*.tar
#      **不污染本地 Docker daemon 的镜像缓存**。
#   2. 架构无关的离线资源 → da-cluster/offline/{charts,crds}/
#      - offline/charts/gateway-helm-v1.7.0.tgz  (helm pull 自 upstream)
#      - offline/crds/gateway.envoyproxy.io_*.yaml  (从 helm tgz 解出)
#      - offline/crds/gateway-api-v1.4.1-experimental.yaml  (curl 自 GitHub)
#      setup.sh / setup-isula.sh / install-crds.sh 都依赖 offline/ 下的这些文件。
#
# 原理：
#   - 自定义镜像：`docker buildx build --output type=docker,dest=X.tar`
#     直接输出文件，不 load 到 daemon
#   - 第三方镜像：`skopeo copy` 运行在容器里，从 registry 直接拉到 tar
#     也不经过 daemon
#
# 用法：
#   ./scripts/build-release-images.sh                # 全部（镜像+chart+CRD）
#   ./scripts/build-release-images.sh --arch amd64   # 只打 amd64 镜像
#   ./scripts/build-release-images.sh --arch arm64   # 只打 arm64 镜像
#   ./scripts/build-release-images.sh --custom-only  # 只打自定义镜像
#   ./scripts/build-release-images.sh --third-only   # 只拉第三方镜像
#   ./scripts/build-release-images.sh --offline-only # 只填 offline/charts + crds
#   ./scripts/build-release-images.sh --skip-offline # 跳过 offline/ 只打镜像
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
AUTH_DIR="$(cd "$PROJECT_DIR/.." && pwd)"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# Docker Desktop on Windows chokes on mount paths with spaces, so default
# the output to a space-free location. Override via OUT_ROOT env var.
if [ -z "${OUT_ROOT:-}" ]; then
  if command -v cygpath >/dev/null 2>&1 && cygpath -w "$PROJECT_DIR" | grep -q ' '; then
    OUT_ROOT="/c/tmp/aidp-iam-release-images"
    warn "PROJECT_DIR contains spaces; using OUT_ROOT=$OUT_ROOT"
  else
    OUT_ROOT="$PROJECT_DIR/release-images"
  fi
fi

# ── Parse args ───────────────────────────────────────────────────────────
ARCHES=(amd64 arm64)
DO_CUSTOM=true
DO_THIRD=true
DO_OFFLINE=true
DO_STRIP_CRDS=true
while [ $# -gt 0 ]; do
  case "$1" in
    --arch)            ARCHES=("$2"); shift 2 ;;
    --custom-only)     DO_THIRD=false; DO_OFFLINE=false; shift ;;
    --third-only)      DO_CUSTOM=false; DO_OFFLINE=false; shift ;;
    --offline-only)    DO_CUSTOM=false; DO_THIRD=false; shift ;;
    --skip-offline)    DO_OFFLINE=false; shift ;;
    --no-strip-crds)   DO_STRIP_CRDS=false; shift ;;
    -h|--help)
      grep '^# ' "$0" | head -30
      exit 0 ;;
    *) err "Unknown arg: $1" ;;
  esac
done

# docker/buildx only required if we're building images
if [ "$DO_CUSTOM" = true ] || [ "$DO_THIRD" = true ]; then
  command -v docker >/dev/null || err "docker not found"
  docker buildx version >/dev/null 2>&1 || err "docker buildx not installed"
fi

# ── Image lists ──────────────────────────────────────────────────────────
# Custom images: name:tag → relative build-context recipe name
CUSTOM_IMAGES=(
  "aidp-iam-app:v1"
  "keycloak-init:v2"
  "keycloak-custom:26.5.2"
  "mock-kb:v1"
  "mock-rubik:v1"
  "mock-memory:v1"
)

# Third-party images (pulled from public registries via skopeo).
# rancher/kubectl no longer included: chart 1.2.0+ disabled the wait-for-secret
# initContainer (kubelet's native secretKeyRef retry handles missing Secret).
# kindest/node only used for local Kind dev, not production releases.
THIRD_PARTY_IMAGES=(
  "postgres:17"
  "docker.io/envoyproxy/gateway:v1.7.0"
  "docker.io/envoyproxy/envoy:distroless-v1.37.0"
  "openpolicyagent/opa:0.70.0-static"
)

SKOPEO_IMAGE="quay.io/skopeo/stable:latest"

# Offline asset versions — keep in sync with install-crds.sh / setup-isula.sh
OFFLINE_DIR="$PROJECT_DIR/offline"
GATEWAY_HELM_VERSION="v1.7.0"
GATEWAY_API_VERSION="v1.4.1"
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

image_to_filename() {
  echo "$1" | sed 's|/|_|g; s|:|_|g'
}

# ── Populate offline/charts and offline/crds (arch-independent) ─────────
fetch_offline_assets() {
  log "Populating $OFFLINE_DIR (charts + CRDs, arch-independent)..."
  mkdir -p "$OFFLINE_DIR/charts" "$OFFLINE_DIR/crds"

  command -v helm >/dev/null || err "helm not found (required for offline charts)"
  command -v curl >/dev/null || err "curl not found (required for Gateway API CRDs)"

  # 1. Envoy Gateway Helm chart (upstream OCI)
  local eg_tgz="$OFFLINE_DIR/charts/gateway-helm-$GATEWAY_HELM_VERSION.tgz"
  if [ -s "$eg_tgz" ]; then
    log "  charts/gateway-helm-$GATEWAY_HELM_VERSION.tgz — exists, skip"
  else
    log "  helm pull oci://docker.io/envoyproxy/gateway-helm --version $GATEWAY_HELM_VERSION"
    helm pull "oci://docker.io/envoyproxy/gateway-helm" \
        --version "$GATEWAY_HELM_VERSION" \
        --destination "$OFFLINE_DIR/charts/" >/dev/null
    [ -s "$eg_tgz" ] || err "helm pull produced no tgz at $eg_tgz"

    # Strip CRDs *inside* the chart archive too. `helm install --skip-crds`
    # only stops the crds/ directory from being applied, but helm still
    # packs the full chart (including every file under crds/) into the
    # release Secret. With raw upstream CRDs that Secret balloons to
    # ~1.3 MB base64-encoded — over the 500 KB limit on clusters that
    # cap etcd writes. Repack with stripped CRDs to keep the release
    # Secret small enough for strict clusters.
    if [ "$DO_STRIP_CRDS" = true ]; then
      command -v tar >/dev/null || err "tar required to repack gateway-helm tgz"
      local py2=""
      for cand in python3 python; do
        if command -v "$cand" >/dev/null 2>&1 && "$cand" --version >/dev/null 2>&1; then
          py2="$cand"; break
        fi
      done
      [ -n "$py2" ] || err "python not found — cannot strip gateway-helm tgz"

      local tmp
      tmp=$(mktemp -d)
      log "  stripping CRDs inside gateway-helm tgz (reduces helm release Secret)..."
      tar -xzf "$eg_tgz" -C "$tmp"
      "$py2" "$SCRIPT_DIR/strip-crd-descriptions.py" \
          "$tmp/gateway-helm/crds/"*.yaml \
          "$tmp/gateway-helm/crds/generated/"*.yaml \
          || err "Failed to strip CRDs inside gateway-helm chart"
      # Re-pack; helm expects files under gateway-helm/ at tgz root.
      (cd "$tmp" && tar -czf "$eg_tgz.new" gateway-helm) \
          || err "Failed to repack gateway-helm tgz"
      mv "$eg_tgz.new" "$eg_tgz"
      rm -rf "$tmp"
      local new_size
      new_size=$(stat -c %s "$eg_tgz" 2>/dev/null || stat -f %z "$eg_tgz")
      log "    repacked tgz size: $((new_size / 1024)) KB"
    fi
  fi

  # 2. Envoy Gateway CRDs (extracted from the helm chart tgz)
  for crd in "${ENVOY_GATEWAY_CRDS[@]}"; do
    local out="$OFFLINE_DIR/crds/$crd"
    if [ -s "$out" ]; then
      log "  crds/$crd — exists, skip"
    else
      log "  crds/$crd — extract from gateway-helm tgz"
      tar -xzOf "$eg_tgz" "gateway-helm/crds/generated/$crd" > "$out" \
          || err "Failed to extract $crd from $eg_tgz"
      [ -s "$out" ] || err "Empty extraction for $crd"
    fi
  done

  # 3. Gateway API experimental channel (kubernetes-sigs/gateway-api GitHub).
  # curl fails with schannel TLS handshake on some Windows/CN networks;
  # fall back to python urllib which uses its own TLS stack.
  local gw_api_crd="$OFFLINE_DIR/crds/gateway-api-$GATEWAY_API_VERSION-experimental.yaml"
  if [ -s "$gw_api_crd" ]; then
    log "  crds/$(basename "$gw_api_crd") — exists, skip"
  else
    local url="https://github.com/kubernetes-sigs/gateway-api/releases/download/$GATEWAY_API_VERSION/experimental-install.yaml"
    log "  crds/$(basename "$gw_api_crd") — download $url"
    if curl -sSfL --connect-timeout 15 --max-time 180 "$url" -o "$gw_api_crd" 2>/dev/null \
       && [ -s "$gw_api_crd" ]; then
      :
    else
      warn "    curl failed, falling back to python urllib..."
      rm -f "$gw_api_crd"
      if command -v python3 >/dev/null 2>&1; then
        python3 -c "import urllib.request,sys; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" \
            "$url" "$gw_api_crd" 2>/dev/null || true
      elif command -v python >/dev/null 2>&1; then
        python -c "import urllib.request,sys; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" \
            "$url" "$gw_api_crd" 2>/dev/null || true
      fi
      [ -s "$gw_api_crd" ] || err "Failed to download $url. Set HTTPS_PROXY or drop the file manually at $gw_api_crd"
    fi
  fi

  # 4. Strip `description:` from every CRD yaml. Raw upstream CRDs can
  #    be 1+ MB each; production clusters often cap CRD writes at ~500 KB
  #    (etcd strain), so a fresh bundle from upstream is unusable there
  #    until descriptions are pruned. Strip is structurally equivalent —
  #    validations and schemas stay intact, only doc comments removed.
  if [ "$DO_STRIP_CRDS" = true ]; then
    # Windows Git Bash has a `python3` shim from Microsoft Store that
    # fails when run — so we probe each candidate with --version to
    # find one that actually executes before using it.
    local py=""
    for cand in python3 python; do
      if command -v "$cand" >/dev/null 2>&1 && "$cand" --version >/dev/null 2>&1; then
        py="$cand"
        break
      fi
    done
    [ -n "$py" ] || err "python not found — needed to strip CRD descriptions; re-run with --no-strip-crds to skip"
    log "  stripping description fields (reduces size 50-60%)..."
    "$py" "$SCRIPT_DIR/strip-crd-descriptions.py" "$OFFLINE_DIR/crds/"*.yaml \
        || err "Failed to strip CRDs (PyYAML missing? 'pip install pyyaml')"
  else
    warn "  CRDs are raw (unstripped) — may exceed 500KB per-CRD limit on some clusters"
  fi

  log "Offline assets ready."
}

# ── Build one custom image for one arch ─────────────────────────────────
build_custom() {
  local img="$1" arch="$2" out_dir="$3"
  local fname="$(image_to_filename "$img").tar"
  local out_path="$out_dir/$fname"

  mkdir -p "$out_dir"
  local ctx
  ctx=$(mktemp -d)

  # Prepare build context — same logic as setup.sh
  case "$img" in
    aidp-iam-app:v1)
      # 4-in-1 image: keycloak-proxy + pep-proxy + bundle-server + resource-sync
      cp "$PROJECT_DIR/images/aidp-iam-app/Dockerfile" "$ctx/Dockerfile"
      cp "$PROJECT_DIR/images/aidp-iam-app/supervisord.conf" "$ctx/"
      cp "$PROJECT_DIR/images/aidp-iam-app/requirements.txt" "$ctx/"
      # 4 service source trees, named to match the COPY paths in Dockerfile.
      # cp -r src dst/sub doesn't auto-create intermediate dirs, so mkdir all
      # parents first.
      mkdir -p "$ctx/keycloak-proxy" "$ctx/pep-proxy" "$ctx/bundle-server" "$ctx/resource-sync"
      cp -r "$AUTH_DIR/da-idb-proxy/app" "$ctx/keycloak-proxy/app"
      cp -r "$AUTH_DIR/opal-dynamic-policy/pep-proxy/app"   "$ctx/pep-proxy/app"
      cp -r "$AUTH_DIR/opal-dynamic-policy/pep-proxy/proto" "$ctx/pep-proxy/proto"
      cp -r "$AUTH_DIR/opal-dynamic-policy/bundle-server/app"  "$ctx/bundle-server/app"
      # Static seed data lives at opal-dynamic-policy/data/, gets copied into
      # the bundle-server build context so Dockerfile's `COPY bundle-server/data`
      # picks it up and lands it at /app/data inside the image.
      if [ -d "$AUTH_DIR/opal-dynamic-policy/data" ]; then
        cp -r "$AUTH_DIR/opal-dynamic-policy/data" "$ctx/bundle-server/data"
      else
        mkdir -p "$ctx/bundle-server/data"
      fi
      cp -r "$AUTH_DIR/resource-sync/app"   "$ctx/resource-sync/app"
      cp -r "$AUTH_DIR/resource-sync/proto" "$ctx/resource-sync/proto"
      ;;
    keycloak-init:v2)
      cp -r "$PROJECT_DIR/images/keycloak-init/." "$ctx/"
      ;;
    keycloak-custom:26.5.2)
      cp -r "$PROJECT_DIR/images/keycloak-custom/." "$ctx/"
      ;;
    mock-kb:v1)
      cp -r "$AUTH_DIR/mock-kb/." "$ctx/"
      ;;
    mock-rubik:v1)
      cp -r "$AUTH_DIR/mock-rubik/." "$ctx/"
      ;;
    mock-memory:v1)
      cp -r "$AUTH_DIR/mock-memory/." "$ctx/"
      ;;
    *) err "Unknown custom image: $img" ;;
  esac

  log "  buildx → linux/$arch — $img → $fname"
  # Skip if already built (allows resuming after partial failure).
  if [ -s "$out_path" ]; then
    rm -rf "$ctx"
    local size
    size=$(du -h "$out_path" | cut -f1)
    log "    skip — already exists ($size)"
    return 0
  fi
  local attempt=0
  local ok=false
  while [ $attempt -lt 3 ]; do
    if docker buildx build \
        --platform "linux/$arch" \
        -t "$img" \
        --output "type=docker,dest=$out_path" \
        "$ctx" 2>&1 | tail -3; then
      if [ -s "$out_path" ]; then
        ok=true
        break
      fi
    fi
    attempt=$((attempt+1))
    warn "    attempt $attempt failed (QEMU apt/network flake), retrying in 10s..."
    rm -f "$out_path"
    sleep 10
  done
  rm -rf "$ctx"
  if [ "$ok" != true ]; then
    err "Failed to build $img after 3 attempts"
  fi
  local size
  size=$(du -h "$out_path" | cut -f1)
  log "    saved ($size)"
}

# ── Pull one third-party image via skopeo container ─────────────────────
pull_third() {
  local img="$1" arch="$2" out_dir="$3"
  local fname="$(image_to_filename "$img").tar"
  local out_path="$out_dir/$fname"
  mkdir -p "$out_dir"

  # Normalize: skopeo needs docker://<name>, but docker.io prefix is default
  local src="$img"
  case "$src" in
    docker.io/*) src="${src#docker.io/}" ;;
  esac

  log "  skopeo copy (linux/$arch) — $img → $fname"
  # Skip if already pulled (resume-safe).
  if [ -s "$out_path" ]; then
    local size
    size=$(du -h "$out_path" | cut -f1)
    log "    skip — already exists ($size)"
    return 0
  fi
  # Windows Git Bash: docker -v needs Windows-style forward-slash path.
  local mount_path="$out_dir"
  if command -v cygpath >/dev/null 2>&1; then
    mount_path="$(cygpath -m "$out_dir")"
  fi
  local attempt=0
  while [ $attempt -lt 3 ]; do
    # skopeo docker-archive refuses to overwrite; drop any stale tar first.
    rm -f "$out_path"
    # Docker Desktop on Windows sometimes can't create a fresh file on a
    # bind-mounted dir from inside a container — pre-touch first.
    touch "$out_path"
    # docker-archive destination supports `:<image>:<tag>` suffix to embed
    # RepoTags into the tar's manifest.json. Without it RepoTags is [] and
    # `ctr images import` silently no-ops (image extracted but never tagged).
    # The image name may contain colons, but skopeo parses the LAST colon as
    # the tag separator so we just append the full image ref verbatim.
    if MSYS_NO_PATHCONV=1 docker run --rm \
        -v "$mount_path:/out" \
        "$SKOPEO_IMAGE" \
        copy \
        --override-os linux --override-arch "$arch" \
        --retry-times 3 \
        "docker://$src" \
        "docker-archive:/out/$fname:$src" 2>&1 | tail -2; then
      [ -s "$out_path" ] && return 0
    fi
    attempt=$((attempt+1))
    warn "    attempt $attempt failed, retrying in 5s..."
    sleep 5
  done
  err "Failed to pull $img after 3 attempts"

  local size
  size=$(du -h "$out_path" | cut -f1)
  log "    saved ($size)"
}

# ── Offline assets (arch-independent, populated once) ───────────────────
if [ "$DO_OFFLINE" = true ]; then
  fetch_offline_assets
fi

# ── Main loop ────────────────────────────────────────────────────────────
if [ "$DO_CUSTOM" != true ] && [ "$DO_THIRD" != true ]; then
  log ""
  log "============================================================"
  log "Done. Offline assets at $OFFLINE_DIR/{charts,crds}"
  log "============================================================"
  exit 0
fi

for arch in "${ARCHES[@]}"; do
  out_dir="$OUT_ROOT/$arch"
  log "============================================================"
  log " Platform: linux/$arch → $out_dir"
  log "============================================================"

  if [ "$DO_CUSTOM" = true ]; then
    log "Building ${#CUSTOM_IMAGES[@]} custom images for $arch..."
    for img in "${CUSTOM_IMAGES[@]}"; do
      build_custom "$img" "$arch" "$out_dir"
    done
  fi

  if [ "$DO_THIRD" = true ]; then
    log "Pulling ${#THIRD_PARTY_IMAGES[@]} third-party images for $arch..."
    for img in "${THIRD_PARTY_IMAGES[@]}"; do
      pull_third "$img" "$arch" "$out_dir"
    done
  fi
done

log ""
log "============================================================"
log "Done."
log "  Images:        $OUT_ROOT/"
[ "$DO_OFFLINE" = true ] && log "  Offline chart: $OFFLINE_DIR/charts/"
[ "$DO_OFFLINE" = true ] && log "  Offline CRDs:  $OFFLINE_DIR/crds/"
log "============================================================"
for arch in "${ARCHES[@]}"; do
  if [ -d "$OUT_ROOT/$arch" ]; then
    count=$(ls "$OUT_ROOT/$arch" | wc -l | tr -d ' ')
    size=$(du -sh "$OUT_ROOT/$arch" | cut -f1)
    log "  $arch: $count files, $size"
  fi
done
log ""
log "Bundle for release:"
log "  cd $OUT_ROOT"
log "  tar czf aidp-iam-images-amd64.tar.gz amd64/"
log "  tar czf aidp-iam-images-arm64.tar.gz arm64/"
log "  gh release create vX.Y.Z aidp-iam-images-*.tar.gz --title 'vX.Y.Z offline images'"
