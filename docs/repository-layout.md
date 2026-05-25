# Repository Layout

This repository is organized by runtime purpose rather than by historical package names.

```text
aidp-iam/
+-- apps/
|   +-- iam-api/             IAM management API and Keycloak proxy
|   +-- pep-proxy/           Envoy ext_authz authorization service
|   +-- bundle-server/       OPA bundle generator
|   +-- resource-sync/       Envoy ext_proc ACL synchronizer
|   +-- gateway-manager/     Gateway certificate API and OMS log callback API
|   +-- keycloak-spi/        Custom Keycloak Java providers
|   +-- keycloak-theme/      Custom Keycloak login theme
+-- build/
|   +-- docker/
|   |   +-- aidp-iam-app/    Dockerfile and requirements for the merged IAM app image
|   |   +-- keycloak-custom/ Keycloak image context and provider jars
|   |   +-- keycloak-init/   Keycloak initialization job image
|   |   +-- gateway-manager/ Gateway manager image context
|   +-- openeuler/
|   |   +-- gateway/         openEuler/EulerOS build scripts for Gateway images
|   +-- release/             Release packaging helpers
+-- deploy/
|   +-- helm/
|   |   +-- aidp-gateway/    Gateway chart
|   |   +-- aidp-iam/        IAM chart
|   |   +-- mocks/           Mock backend charts
|   +-- kind/                Local Kind cluster config
|   +-- scripts/             setup, cleanup, rebuild, and test scripts
+-- docs/
|   +-- architecture/        Architecture and API design material
|   +-- api/                 API references and generated specs
|   +-- deploy/              Deployment notes and validation prompts
|   +-- onboarding/          App onboarding and integration guides
|   +-- open-source/         Open source introduction material
|   +-- release/             Package and release notes
|   +-- story-design/        Story design documents
+-- tests/
|   +-- e2e/
|   |   +-- iam/
|   |   +-- gateway/
|   |   +-- oms-log/
|   |   +-- certificate/
|   +-- fixtures/            Mock backend image sources
|   |   +-- mock-kb/
|   |   +-- mock-memory/
|   |   +-- mock-dataagent/
|   +-- manual-cases/        Manual test cases and test logs
+-- tools/
|   +-- gateway-onboarding-generator/
|   +-- api-spec-generator/
+-- legacy/                  Historical experiments not used by current deployment
+-- artifacts/               Ignored local build/test/release outputs
```

## Runtime Path

The production deployment path is:

1. `deploy/scripts/setup.sh`
2. `build/docker/*` for image build contexts
3. `apps/*` for runtime source copied into images
4. `deploy/helm/aidp-gateway`
5. `deploy/helm/aidp-iam`
6. `deploy/scripts/test.sh`

## Generated Or Local-Only Content

Do not commit:

- `artifacts/`
- image tar files
- packaged chart `.tgz` files
- `apps/keycloak-theme/node_modules/`
- `apps/keycloak-theme/dist/`
- `apps/keycloak-theme/dist_keycloak/`
- `apps/keycloak-spi/target/`
- `release-assets-v*/`
