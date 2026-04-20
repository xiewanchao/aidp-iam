#!/usr/bin/env bash
# ============================================================================
# build-release-images.sh — 构建跨平台离线镜像 tar 包
#
# 对两个平台（amd64 + arm64）分别生成全部镜像的 tar 文件，输出到
# release-images/{amd64,arm64}/，**不污染本地 Docker daemon 的镜像缓存**。
#
# 原理：
#   - 自定义镜像：`docker buildx build --output type=docker,dest=X.tar`
#     直接输出文件，不 load 到 daemon
#   - 第三方镜像：`skopeo copy` 运行在容器里，从 registry 直接拉到 tar
#     也不经过 daemon
#
# 用法：
#   ./scripts/build-release-images.sh                # 全部平台 + 全部镜像
#   ./scripts/build-release-images.sh --arch amd64   # 只打 amd64
#   ./scripts/build-release-images.sh --arch arm64   # 只打 arm64
#   ./scripts/build-release-images.sh --custom-only  # 只打自定义镜像
#   ./scripts/build-release-images.sh --third-only   # 只拉第三方镜像
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
while [ $# -gt 0 ]; do
  case "$1" in
    --arch)        ARCHES=("$2"); shift 2 ;;
    --custom-only) DO_THIRD=false; shift ;;
    --third-only)  DO_CUSTOM=false; shift ;;
    -h|--help)
      grep '^# ' "$0" | head -25
      exit 0 ;;
    *) err "Unknown arg: $1" ;;
  esac
done

command -v docker >/dev/null || err "docker not found"
docker buildx version >/dev/null 2>&1 || err "docker buildx not installed"

# ── Image lists ──────────────────────────────────────────────────────────
# Custom images: name:tag → relative build-context recipe name
CUSTOM_IMAGES=(
  "keycloak-proxy:v3"
  "opal-proxy:v2"
  "keycloak-init:v2"
  "resource-sync:v1"
  "keycloak-custom:26.5.2"
  "mock-kb:v1"
  "mock-rubik:v1"
)

# Third-party images (pulled from public registries via skopeo)
THIRD_PARTY_IMAGES=(
  "postgres:17"
  "docker.io/envoyproxy/gateway:v1.7.0"
  "docker.io/envoyproxy/envoy:distroless-v1.37.0"
  "permitio/opal-server:0.7.4"
  "permitio/opal-client:0.7.4"
  "kindest/node:v1.31.1"
)

SKOPEO_IMAGE="quay.io/skopeo/stable:latest"

image_to_filename() {
  echo "$1" | sed 's|/|_|g; s|:|_|g'
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
    keycloak-proxy:v3)
      cp -r "$AUTH_DIR/da-idb-proxy/app" "$ctx/app"
      cp "$PROJECT_DIR/images/keycloak-proxy/Dockerfile" "$ctx/Dockerfile"
      ;;
    opal-proxy:v2)
      cp "$PROJECT_DIR/images/opal-proxy/Dockerfile" "$ctx/Dockerfile"
      cp "$PROJECT_DIR/images/opal-proxy/supervisord.conf" "$ctx/"
      cp "$PROJECT_DIR/images/opal-proxy/requirements.txt" "$ctx/"
      cp -r "$AUTH_DIR/opal-dynamic-policy/pep-proxy" "$ctx/pep-proxy"
      cp -r "$AUTH_DIR/opal-dynamic-policy/bundle-server" "$ctx/bundle-server"
      cp -r "$AUTH_DIR/opal-dynamic-policy/data" "$ctx/data"
      ;;
    keycloak-init:v2)
      cp -r "$PROJECT_DIR/images/keycloak-init/." "$ctx/"
      ;;
    resource-sync:v1)
      cp "$PROJECT_DIR/images/resource-sync/Dockerfile" "$ctx/Dockerfile"
      cp -r "$AUTH_DIR/resource-sync/app" "$ctx/app"
      cp -r "$AUTH_DIR/resource-sync/proto" "$ctx/proto"
      cp "$AUTH_DIR/resource-sync/requirements.txt" "$ctx/"
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
    # Note: docker-archive dest is `docker-archive:<path>` — omit the ref
    # suffix, because image names contain colons which confuse the parser.
    # `docker load` will use the manifest's tag baked into the tar.
    if MSYS_NO_PATHCONV=1 docker run --rm \
        -v "$mount_path:/out" \
        "$SKOPEO_IMAGE" \
        copy \
        --override-os linux --override-arch "$arch" \
        --retry-times 3 \
        "docker://$src" \
        "docker-archive:/out/$fname" 2>&1 | tail -2; then
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

# ── Main loop ────────────────────────────────────────────────────────────
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
log "Done. Output: $OUT_ROOT/"
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
