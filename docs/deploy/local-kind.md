# Local Kind Deployment

This document describes the current local validation path. Run commands from the repository root.

## Architecture

```text
Client
  |
Envoy Gateway Proxy
  |
  +-- /realms/*, /admin/*                         -> Keycloak
  +-- /AccessManager/*, /api/v1/*                 -> IAM API
  +-- /acl/v1/resources/**                        -> resource-sync
  +-- business paths                              -> registered backend services
```

## Quick Start

```bash
export CLUSTER_NAME=da-cluster
bash deploy/scripts/cleanup.sh
bash deploy/scripts/setup.sh
bash deploy/scripts/test.sh
```

## Reinstall Check

```bash
helm uninstall aidp-iam -n aidp-iam
helm uninstall aidp-gateway -n aidp-gateway

bash deploy/scripts/setup.sh --skip-build
bash deploy/scripts/test.sh
```

## Force Keycloak Provider Rebuild

```bash
BUILD_KEYCLOAK_SPI=true \
BUILD_KEYCLOAK_THEME=true \
bash deploy/scripts/setup.sh
```

## Image Build Contexts

The local setup script builds these images:

```text
aidp-iam-app:v1        build/docker/aidp-iam-app/Dockerfile, context repository root
keycloak-custom:26.5.2 build/docker/keycloak-custom/Dockerfile, context build/docker/keycloak-custom
keycloak-init:v2       build/docker/keycloak-init/Dockerfile, context build/docker/keycloak-init
gateway-manager:v1     build/docker/gateway-manager/Dockerfile, context repository root
```

## Charts

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

## Useful Environment Variables

```text
CLUSTER_NAME              Kind cluster name, default da-cluster
ARCH                      Image architecture override
BUILD_KEYCLOAK_SPI=true   Force rebuild data-agent-mapper.jar
BUILD_KEYCLOAK_THEME=true Force rebuild keycloak-theme.jar
```
