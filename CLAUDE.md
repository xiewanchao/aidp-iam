# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

Multi-tenant IAM system deployed as a Kind/K8s cluster. Four top-level components (all Python 3.11 / FastAPI) sit behind a single gateway:

- `da-idb-proxy/` — **keycloak-proxy** (port 8090). Admin API for tenants/realms/groups/users/IDPs/apps/api-keys. Talks to Keycloak Admin API + iam Postgres DB.
- `opal-dynamic-policy/pep-proxy/` — **pep-proxy** (HTTP 8000 + gRPC 9000). Gateway `ext_authz` target. Validates JWT/API-Key, does path-level auth via OPA, then resource-level auth via `resource_acl`. Owns `path_rules` CRUD.
- `opal-dynamic-policy/bundle-server/` — generates OPA Rego bundles from `apps` + `path_rules` tables; served to OPA via OPAL.
- `resource-sync/` — **resource-sync** (HTTP 8080 + gRPC ext_proc 8082). Observes POST/DELETE responses via `ext_proc`, writes/cleans `resource_acl`. Injects `X-Allowed-Ids` for list filtering. Owns `/acl/v1/resources/**` CRUD. `retry_worker.py` drains `pending_acl`.
- `da-cluster/` — Helm charts, Dockerfiles (`images/`), Kind config, offline bundles (`offline/`), deployment scripts (`scripts/`), gateway routes (`gateway-routes/`).

## Architecture (v2.1)

Groups model (NOT roles): `master-admins`, `tenant-admins`, `all-users`. JWT carries `groups` + `group_ids`.

Two-step authorization on every protected request:
1. **Path-level** (pep-proxy → OPA): app enabled? group allowed on `path_rules`? admin groups bypass.
2. **Resource-level** (pep-proxy → Postgres `resource_acl`): map action → min permission (GET→viewer, PUT/PATCH→contributor, DELETE→owner); sub-resources inherit from parent.

Flexible API adaptation: `resource_patterns` table has `id_source` (path|query|body) + `id_field` (supports nested like `data.kb_id`) + `id_query_param`. Non-standard verbs handled via `resource_actions` rows (app_name, resource_prefix, method, path_suffix, action, min_permission). Empty `resource_actions` → code `DEFAULT_ACTIONS` (standard RESTful).

ACL auto-sync: ext_proc watches 2xx response of create/delete, extracts resource ID per patterns, writes `resource_acl(owner=<subject>)` or cascades delete. Failures queued to `pending_acl` for async retry.

Gateway is **Envoy Gateway v1.7.0** (namespace `envoy-gateway-system`, GatewayClass `eg`, Gateway name `eg`). `SecurityPolicy.extAuth.bodyToExtAuth.maxRequestBytes=8192` forwards request body so pep-proxy can extract `id_source=body` IDs. `EnvoyExtensionPolicy.extProc` wires streamed body to resource-sync. Routes live in `da-cluster/gateway-routes/` (`reference-grants.yaml`, `keycloak-routes.yaml`, `protected-routes.yaml`). Our local chart `da-cluster/charts/envoy-gateway/` provides the Gateway + EnvoyProxy + GatewayClass; the upstream controller comes from `offline/charts/gateway-helm-v1.7.0.tgz`.

## iam Postgres schema

Defined in `da-cluster/charts/keycloak/templates/postgres-init-configmap.yaml`. Key tables: `apps`, `resource_patterns`, `resource_actions`, `path_rules`, `resource_acl`, `pending_acl`, `api_keys`. `apps/resource_patterns/resource_actions/path_rules` are system-level (no tenant_id); `resource_acl/api_keys` are tenant-level.

## Commands

Deployment (from `da-cluster/`):

```bash
./scripts/setup.sh                 # Create Kind cluster + deploy everything
./scripts/setup.sh --no-kind       # Deploy to existing K8s (production, air-gapped)
./scripts/setup.sh --no-kind --fat-base   # Rebuild images only (no pip/apt)
./scripts/cleanup.sh               # Tear down
./scripts/rebuild.sh <component>   # Rebuild single image + kind load + rollout
./scripts/test.sh                  # Full SR test suite (~192 tests)
./scripts/test-external-idp.sh     # SR09 external IdP scenarios
./scripts/bench.sh                 # Load test (1000 concurrent / 100 tenants)
./scripts/export-images.sh         # Build offline tar bundle
```

Component dev loop: edit code under `da-idb-proxy/`, `opal-dynamic-policy/pep-proxy/`, `opal-dynamic-policy/bundle-server/`, or `resource-sync/` → `./scripts/rebuild.sh <name>` → re-run `./scripts/test.sh` (or a targeted curl).

Gateway access: `kubectl port-forward -n envoy-gateway-system svc/<gateway-svc> 8080:80` then hit `http://localhost:8080`.

## Working on this repo

- **Always keep `diagrams/` consistent with implementation.** The design docs (`story-breakdown.md`, `data-storage.md`, `request-flow.md`, `app-integration-guide.md`, `performance-benchmark.md`, etc.) are the contract — update them in the same change as code.
- **CRD size constraint**: server rejects CRDs > 500 KB. `da-cluster/offline/crds/` holds description-stripped versions; preserve `x-kubernetes-validations` when stripping (only remove description *content*, not keys).
- **Gateway API channel**: Envoy Gateway requires the Gateway API **experimental** channel (`gateway-api-v1.4.1-experimental.yaml`), not standard.
- **Env**: Windows + Git Bash. Use forward slashes and `/dev/null` in shell.
- Current branch: `feature/iam-v2.0`. Main: `main`.
