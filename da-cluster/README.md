# da-cluster: Unified Multi-Tenant Auth System

Consolidates Keycloak + keycloak-proxy + OPAL dynamic policy behind a single Envoy Gateway. Supports air-gapped deployment on amd64 and arm64.

## Architecture

```
Client (browser / backend)
    |
Envoy Gateway Proxy (unified entry, port 80)
    |
    +-- /realms/*, /admin/*                    --> Keycloak (no auth)
    +-- /api/v1/tenants, /api/v1/common        --> keycloak-proxy (ext-authz)
    +-- /api/v1/{realm}/roles|groups|users|idp  --> keycloak-proxy (ext-authz)
    +-- /api/v1/apps, /api/v1/{realm}/api-keys  --> keycloak-proxy (ext-authz)
    +-- /api/v1/path-rules, /api/v1/auth        --> pep-proxy (ext-authz)
    +-- /acl/v1/resources/**                    --> resource-sync (ext-authz)
    +-- /{tenant-id}/**                         --> your backend (ext-authz)
```

## Quick Start (Kind, for development)

```bash
cd da-cluster

# 1. Download offline packages from GitHub Releases
wget https://github.com/zzzYesYes/aidp-iam/releases/download/v1.0.0/aidp-iam-offline-amd64-v1.0.0.tar.gz
wget https://github.com/zzzYesYes/aidp-iam/releases/download/v1.0.0/aidp-iam-offline-common-v1.0.0.tar.gz

# 2. Extract into offline/ directory
tar xzf aidp-iam-offline-amd64-v1.0.0.tar.gz -C offline/
tar xzf aidp-iam-offline-common-v1.0.0.tar.gz -C offline/

# 3. Deploy
./scripts/setup.sh

# 4. Test
./scripts/test.sh
```

## Deploy to Production (K8s, air-gapped server)

```bash
# On your dev machine: clone and download offline packages
git clone https://github.com/zzzYesYes/aidp-iam.git
cd aidp-iam/da-cluster

# Download the correct platform package (amd64 or arm64) + common
wget .../aidp-iam-offline-arm64-v1.0.0.tar.gz
wget .../aidp-iam-offline-common-v1.0.0.tar.gz

# Transfer everything to server
scp -r ../aidp-iam user@server:/opt/

# On the server:
cd /opt/aidp-iam/da-cluster
tar xzf aidp-iam-offline-arm64-v1.0.0.tar.gz -C offline/
tar xzf aidp-iam-offline-common-v1.0.0.tar.gz -C offline/
./scripts/setup.sh --no-kind
```

## Update Code Only (no network needed)

When you only changed IAM application code, rebuild the merged IAM image from
the repository root. This Dockerfile copies source directly from
`da-idb-proxy/`, `opal-dynamic-policy/`, and `resource-sync/`, so it does not
need `setup.sh` to prepare a temporary build context.

```bash
cd /opt/aidp-iam
git pull
docker build -f da-cluster/images/aidp-iam-app/Dockerfile -t aidp-iam-app:v1 .
```

After the new image is built, push it to your registry or import it into each
production node's container runtime, then roll the Helm release with
`helm upgrade`.

For local Kind iteration, use:

```bash
cd da-cluster
./scripts/rebuild.sh app
```

## Upgrade (new image version)

When images have changed:

```bash
# Download new version's offline package
wget .../aidp-iam-offline-arm64-v1.1.0.tar.gz
tar xzf aidp-iam-offline-arm64-v1.1.0.tar.gz -C offline/    # overwrites old images
git pull
./scripts/setup.sh --no-kind
```

## setup.sh Options

```bash
./scripts/setup.sh [OPTIONS]

Options:
  (default)         Create Kind cluster + deploy (development)
  --no-kind         Deploy to existing K8s cluster (production)
  --build           Rebuild custom images from source (requires network)
  --fat-base        Rebuild from fat base images (no network, code-only)
  --existing-kind   Use existing Kind cluster, skip creation

Environment:
  PLATFORM=arm64    Force platform (default: auto-detect via uname -m)
  CLUSTER_NAME=xxx  Kind cluster name (default: da-cluster)
  K8S_NODES="..."   Space-separated node IPs for multi-node K8s
  K8S_NODE_USER=xxx SSH user for nodes (default: root)
```

## Offline Package Structure

After extracting, the `offline/` directory should look like:

```
offline/
  images/
    amd64/ (or arm64/)
      keycloak-proxy_v3.tar
      opal-proxy_v2.tar
      keycloak-init_v2.tar
      resource-sync_v1.tar
      keycloak-custom_26.5.2.tar
      postgres_17.tar
      nginx_alpine.tar
      envoyproxy_gateway_v1.7.2.tar
      envoyproxy_envoy_v1.36.5.tar
      permitio_opal-server_0.7.4.tar
      permitio_opal-client_0.7.4.tar
      mccutchen_go-httpbin_v2.6.0.tar
      base-keycloak-proxy_v1.tar      # fat base (for --fat-base mode)
      base-opal-proxy_v1.tar
      base-keycloak-init_v1.tar
      base-resource-sync_v1.tar
  charts/
    aidp-gateway-1.7.2.tgz
    aidp-iam-1.3.0.tgz
```

Gateway CRD 已经打进 `aidp-gateway-1.7.2.tgz` 的顶层 `crds/` 目录，
生产部署不需要额外执行 `kubectl apply crds/`。

## For Maintainers: Creating a Release

```bash
cd da-cluster

# 1. Package offline assets
tar czf aidp-iam-offline-amd64-v1.0.0.tar.gz -C offline images/amd64
tar czf aidp-iam-offline-arm64-v1.0.0.tar.gz -C offline images/arm64
tar czf aidp-iam-offline-common-v1.0.0.tar.gz -C offline charts crds

# 2. Push code + create release
git push
gh release create v1.0.0 \
  aidp-iam-offline-amd64-v1.0.0.tar.gz \
  aidp-iam-offline-arm64-v1.0.0.tar.gz \
  aidp-iam-offline-common-v1.0.0.tar.gz \
  --title "v1.0.0" \
  --notes "Initial release with offline images for amd64 and arm64"
```

## Testing

```bash
./scripts/test.sh    # 192 tests covering all API endpoints
```

## Access Services

```bash
# Gateway (all services via single entry point)
kubectl -n envoy-gateway-system port-forward svc/eg 8080:80

# Direct access (debugging)
kubectl -n keycloak port-forward svc/keycloak 8080:8080
kubectl -n keycloak port-forward svc/keycloak-proxy 8090:8090
kubectl -n opa port-forward svc/pep-proxy 8000:8000
```

## Default Accounts

| User | Realm | Password | Groups |
|------|-------|----------|--------|
| super-admin | master | SuperInit@123 | master-admins |
| tenant-admin | data-agent | TenantAdmin@123 | tenant-admins, all-users |
| normal-user | data-agent | NormalUser@123 | all-users |

## Documentation

- [Architecture](docs/architecture.md) - System design and component overview
- [Deployment Guide](docs/deployment-guide.md) - Full deployment instructions
- [Design Specification](docs/design-specification.md) - Detailed design specification
- [Frontend API Reference](docs/frontend-api-reference.md) - Complete API documentation
- [Frontend Sync Checklist](docs/frontend-sync-checklist.md) - Preparation for frontend integration
- [Integration Guide](docs/integration-guide.md) - Frontend & backend integration guide

## Cleanup

```bash
./scripts/cleanup.sh
```
