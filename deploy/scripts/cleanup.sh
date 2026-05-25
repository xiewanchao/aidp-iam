#!/usr/bin/env bash
# ============================================================================
# cleanup.sh — Tear down everything this repo installs into the cluster.
#
# Removes the helm releases (aidp-gateway / aidp-iam / aidp-mock-kb) and
# their namespaces. Safe to re-run; missing items just no-op.
#
# With CLUSTER_NAME set, also deletes the matching Kind cluster.
# ============================================================================
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-}"

helm uninstall aidp-mock-kb     -n mock-kb     2>/dev/null || true
helm uninstall aidp-iam         -n aidp-iam    2>/dev/null || true
helm uninstall aidp-gateway     -n aidp-gateway 2>/dev/null || true

echo "Deleting namespaces..."
for ns in mock-kb aidp-iam keycloak aidp-gateway envoy-gateway-system; do
  kubectl delete namespace "$ns" --timeout=60s 2>/dev/null || true
done

if [ -n "$CLUSTER_NAME" ] && command -v kind >/dev/null 2>&1; then
  echo "Deleting Kind cluster '$CLUSTER_NAME'..."
  kind delete cluster --name "$CLUSTER_NAME"
fi

echo "Done."
