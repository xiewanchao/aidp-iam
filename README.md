# AIDP IAM

AIDP IAM provides a local Keycloak-based identity service, OPA-backed path authorization, resource ACL synchronization, API Key support, and an Envoy Gateway entry point.

## Repository Layout

```text
aidp-iam/
+-- apps/                    Runtime application source
|   +-- iam-api/             IAM management API and Keycloak proxy
|   +-- pep-proxy/           Envoy ext_authz service
|   +-- bundle-server/       OPA bundle generator
|   +-- resource-sync/       Envoy ext_proc ACL synchronizer
|   +-- gateway-manager/     Gateway certificate and OMS log APIs
|   +-- keycloak-spi/        Custom Keycloak Java providers
|   +-- keycloak-theme/      Custom Keycloak login theme
+-- build/
|   +-- docker/              Docker build contexts for IAM and Gateway images
|   +-- openeuler/           openEuler/EulerOS image build scripts
|   +-- release/             Release packaging helpers
+-- deploy/
|   +-- helm/                Production Helm charts and mock charts
|   +-- kind/                Local Kind cluster config
|   +-- scripts/             setup, cleanup, rebuild, and test scripts
+-- docs/                    Architecture, API, release, story, onboarding, and OSS docs
+-- tests/
|   +-- e2e/                 Cluster validation helpers
|   +-- fixtures/            Mock backend image sources
|   +-- manual-cases/        Manual test cases and validation logs
+-- tools/                   Developer and onboarding tools
+-- legacy/                  Historical reference only
+-- artifacts/               Ignored local build/test/release outputs
```

## Local Validation

Run from the repository root:

```bash
bash deploy/scripts/cleanup.sh
bash deploy/scripts/setup.sh
bash deploy/scripts/test.sh
```

For a reinstall check:

```bash
helm uninstall aidp-iam -n aidp-iam
helm uninstall aidp-gateway -n aidp-gateway
bash deploy/scripts/setup.sh --skip-build
bash deploy/scripts/test.sh
```

Force rebuild Keycloak SPI and theme jars:

```bash
BUILD_KEYCLOAK_SPI=true \
BUILD_KEYCLOAK_THEME=true \
bash deploy/scripts/setup.sh
```

## Main Charts

```bash
helm install aidp-gateway deploy/helm/aidp-gateway \
  --namespace aidp-gateway --create-namespace \
  --set proxy.service.nodePort=30080 \
  --set proxy.service.httpsNodePort=30443 \
  --timeout 5m --wait

helm install aidp-iam deploy/helm/aidp-iam \
  --namespace aidp-iam --create-namespace \
  --timeout 10m --wait
```

## Key Docs

- [Repository layout](docs/repository-layout.md)
- [Local Kind deploy](docs/deploy/local-kind.md)
- [Full rebuild validation prompt](docs/deploy/full-rebuild-validation-prompt.md)
- [Gateway package](docs/release/gateway-package.md)
- [IAM package](docs/release/iam-package.md)
- [Architecture docs](docs/architecture)
- [API docs](docs/api)
- [Story design docs](docs/story-design)
- [Open source docs](docs/open-source)
