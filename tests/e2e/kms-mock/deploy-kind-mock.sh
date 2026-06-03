#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-da-cluster}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

docker build -t mock-oms-kms:v1 "$ROOT_DIR/tests/fixtures/mock-oms-kms"
kind load docker-image mock-oms-kms:v1 --name "$CLUSTER_NAME"
kubectl apply -f "$ROOT_DIR/tests/fixtures/mock-oms-kms/k8s.yaml"
kubectl -n agentinfra rollout status deploy/mock-oms-kms --timeout=120s

echo "mock OMS KMS ready: http://mock-oms-kms.agentinfra.svc.cluster.local:18082"

