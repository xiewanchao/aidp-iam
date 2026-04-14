#!/usr/bin/env bash
# Re-pack the aidp-iam umbrella subcharts whenever a sibling chart changes.
# Unpacks the resulting tgz files into charts/aidp-iam/charts/<name>/ so Helm
# can resolve dependencies without a remote repo or `helm dep build`.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
UMBRELLA_SUBCHARTS="$PROJECT_DIR/charts/aidp-iam/charts"

rm -rf "$UMBRELLA_SUBCHARTS"
mkdir -p "$UMBRELLA_SUBCHARTS"

cd "$PROJECT_DIR"
for c in envoy-gateway keycloak opa resource-sync; do
  echo "packaging $c ..."
  helm package "charts/$c" -d "charts/aidp-iam/charts/" >/dev/null
done

# Upstream Envoy Gateway controller — copy then unpack
cp "offline/charts/gateway-helm-v1.7.0.tgz" "charts/aidp-iam/charts/"

# Unpack every tgz into a per-chart directory; Helm auto-discovers them.
cd "charts/aidp-iam/charts"
for t in *.tgz; do
  tar xzf "$t"
done
rm -f *.tgz

echo "umbrella subcharts refreshed:"
ls -d */
