#!/usr/bin/env bash
# Build the db-provisioner image and side-load it into the kind cluster.
set -euo pipefail

IMG="${IMG:-db-provisioner:local}"
CLUSTER="${CLUSTER:-db-test}"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here"

docker build -t "$IMG" .
kind load docker-image "$IMG" --name "$CLUSTER"
echo "[build] $IMG built and loaded into kind cluster '$CLUSTER'"
