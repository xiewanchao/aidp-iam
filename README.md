# AIDP IAM

Multi-tenant Identity & Access Management system. Federates customer identity providers (SAML/OIDC) via Keycloak, enforces dynamic authorization policies via OPA, and unifies everything behind a single API gateway.

## Repository Structure

```
da-cluster/           Deployment system (Helm charts, scripts, offline images, docs)
da-idb-proxy/         Keycloak Proxy API (tenant/role/group/user/IDP management)
opal-dynamic-policy/  OPA Policy Engine (pep-proxy + bundle-server + OPAL)
```

## Quick Start

See [da-cluster/README.md](da-cluster/README.md) for full deployment instructions.

```bash
cd da-cluster
# download offline packages from GitHub Releases, then:
./scripts/setup.sh              # Kind (dev)
./scripts/setup.sh --no-kind    # K8s (production)
./scripts/test.sh               # 192 tests
```

## Documentation

- [Architecture](da-cluster/docs/architecture.md)
- [Deployment Guide](da-cluster/docs/deployment-guide.md)
- [Frontend API Reference](da-cluster/docs/frontend-api-reference.md)
