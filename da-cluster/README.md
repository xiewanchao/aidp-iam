# da-cluster: Unified Auth System

Consolidates Keycloak + keycloak-proxy + OPAL dynamic policy behind a single AgentGateway. Deployable to Kind cluster with one script.

## Architecture

```
Client (curl / backend)
    |
AgentGateway Proxy (agentgateway-system, port 80)
    |
    +-- /realms/*, /resources/*, /admin/*  --> Keycloak (no auth)
    +-- /api/v1/tenants/*, /api/v1/common/* --> keycloak-proxy (ext-authz)
    +-- /api/v1/auth/*, /api/v1/policies/*  --> pep-proxy (ext-authz)
    +-- /{tenant-id}/**                     --> httpbin test backend (ext-authz)
```

## Namespaces

| Namespace | Components |
|-----------|-----------|
| `agentgateway-system` | Gateway Controller + Proxy |
| `keycloak` | Keycloak, PostgreSQL (shared), keycloak-proxy, keycloak-init |
| `opa` | OPAL Server, PEP Proxy (pep-proxy + bundle-server + opal-client) |
| `httpbin` | Test backend |

## Prerequisites

- Docker
- Kind (`kind`)
- kubectl
- Helm 3
- Internet access (for Gateway API CRDs and AgentGateway Helm chart)

## Quick Start

```bash
cd da-cluster
./scripts/setup.sh
```

This will:
1. Create Kind cluster `da-cluster`
2. Build and load all images
3. Install Gateway API CRDs + AgentGateway controller
4. Deploy Keycloak stack (PostgreSQL + Keycloak + keycloak-proxy + init job)
5. Deploy OPA stack (OPAL Server + PEP Proxy)
6. Deploy httpbin test backend
7. Apply gateway routes

## Testing

```bash
./scripts/test.sh
```

Covers: health checks, Keycloak route passthrough, auth enforcement (no token -> 401/403), token acquisition, authenticated CRUD operations.

## Access Services

```bash
# Gateway (all services)
kubectl -n agentgateway-system port-forward svc/agentgateway-proxy 8080:80

# Direct access
kubectl -n keycloak port-forward svc/keycloak 8080:8080          # Keycloak
kubectl -n keycloak port-forward svc/keycloak-proxy 8090:8090    # keycloak-proxy
kubectl -n opa port-forward svc/pep-proxy 8000:8000              # pep-proxy
```

## Cleanup

```bash
./scripts/cleanup.sh
```

## Images

| Image | Source | Built By |
|-------|--------|----------|
| `keycloak-proxy:v2` | `da-idb-proxy/app/` | setup.sh |
| `opal-proxy:v1` | `opal-dynamic-policy/` | setup.sh |
| `keycloak-init:v1` | `da-idb-proxy/k8s/.../files/` | setup.sh |
| `quay.io/keycloak/keycloak:26.5.2` | Pre-built tar | docker load |
| `postgres:17` | Pre-built tar | docker load |
| `cr.agentgateway.dev/controller:v2.2.0-main` | Pre-built tar | docker load |
| `cr.agentgateway.dev/agentgateway:0.11.1` | Pre-built tar | docker load |
| `permitio/opal-server:0.7.4` | Registry | pulled |
| `permitio/opal-client:0.7.4` | Registry | pulled |
