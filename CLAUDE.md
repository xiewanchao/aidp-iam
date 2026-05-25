# CLAUDE.md

This repository is a Kubernetes-deployed IAM and Gateway stack. Keep runtime code, Helm charts, deployment scripts, and docs aligned when making changes.

## Active Layout

- `apps/iam-api/` - IAM management API and Keycloak proxy.
- `apps/pep-proxy/` - Envoy `ext_authz` HTTP/gRPC authorization service.
- `apps/bundle-server/` - OPA bundle generator.
- `apps/resource-sync/` - Envoy `ext_proc` ACL synchronizer.
- `apps/gateway-manager/` - Gateway certificate API and OMS log callback API.
- `apps/keycloak-spi/` - custom Keycloak Java providers.
- `apps/keycloak-theme/` - custom Keycloak login theme.
- `build/docker/` - Docker build contexts for IAM and Gateway images.
- `build/openeuler/` - openEuler/EulerOS image build scripts.
- `deploy/helm/` - production Helm charts and mock charts.
- `deploy/scripts/` - local setup, cleanup, rebuild, and test entry points.
- `deploy/kind/` - local Kind cluster config.
- `docs/` - architecture, API, release, story, onboarding, and OSS documentation.
- `tests/fixtures/` - mock backend image sources.
- `tests/manual-cases/` - manual validation scripts and logs.
- `legacy/` - historical material not used by the current deployment path.

## Main Commands

Run from the repository root:

```bash
bash deploy/scripts/cleanup.sh
bash deploy/scripts/setup.sh
bash deploy/scripts/test.sh
```

Fast IAM image iteration:

```bash
bash deploy/scripts/rebuild.sh app
```

Gateway route black-box checks:

```bash
bash deploy/scripts/test-gateway.sh --k8s
```

## Architecture Notes

- Gateway chart: `deploy/helm/aidp-gateway`.
- IAM chart: `deploy/helm/aidp-iam`.
- Gateway namespace defaults to `aidp-gateway`; GatewayClass and Gateway name are both `eg`.
- IAM namespace defaults to `aidp-iam`; Keycloak runs in `keycloak`.
- Protected traffic flows through Envoy Gateway -> `pep-proxy` -> OPA/Postgres.
- ACL synchronization flows through Envoy Gateway -> `resource-sync`.
- Gateway management APIs are served by `gateway-manager:v1`.

## Editing Notes

- Keep docs under `docs/` consistent with implementation.
- Do not commit generated release assets, image tars, chart packages, local node modules, or `artifacts/`.
- The local validation path is `deploy/scripts/setup.sh`; update it whenever image or chart locations change.
