#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-da-cluster}"

echo "Cleaning up namespaces..."
for ns in keycloak opa resource-sync envoy-gateway-system aidp-iam mock-kb mock-rubik mock-memory; do
  kubectl delete namespace "$ns" --timeout=60s 2>/dev/null || true
done

echo "Deleting Kind cluster '$CLUSTER_NAME'..."
kind delete cluster --name "$CLUSTER_NAME"
echo "Done."
