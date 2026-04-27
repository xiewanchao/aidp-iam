#!/usr/bin/env bash
# ============================================================================
# test.sh — entry point for KB e2e tests.
#
# Delegates to run-all.sh which orchestrates per-category tests under
# categories/ (one file per apioption.md section). Keeps the legacy
# `bash test.sh` invocation working for external callers (CI, README).
#
# To run a single category instead of the full suite:
#   bash mocks/package-mock-kb/test/categories/02-models-config.sh
#
# Common env vars (defaults match local Kind):
#   GATEWAY         — gateway URL (default: http://localhost:30080)
#   NAMESPACE       — mock-kb pod ns (default: mock-kb)
#   IAM_NS          — namespace holding keycloak-aidp-client Secret (default: aidp-iam)
#   KEYCLOAK_NS     — Keycloak/Postgres ns (default: keycloak)
# ============================================================================
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec bash "$SCRIPT_DIR/run-all.sh" "$@"
