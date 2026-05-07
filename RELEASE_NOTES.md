# aidp-iam v1.6.2

## Highlights

- Bundled `keycloak-protocol-cas` `26.5.2` into `keycloak-custom:26.5.2`.
- Added a visual CAS Client demo:
  - Python source: `tests/iam-auth/cas-client-demo.py`
  - Container image: `cas-client-demo:v1`
  - K8s manifest: `tests/iam-auth/cas-client-demo.k8s.yaml`
- Added automated CAS smoke tests for Windows and Linux/macOS.
- Fixed namespace handling in Gateway and IAM charts. Namespaces are no longer
  rendered as `pre-upgrade` hooks, preventing Helm upgrades from deleting
  `envoy-gateway-system` or `keycloak`.
- Published amd64 and arm64 image bundles for Gateway and IAM.

## Deployment

Install Gateway first:

```bash
helm install aidp-gateway aidp-gateway-1.6.2.tgz \
  --namespace aidp-gateway --create-namespace \
  --wait --timeout=10m
```

Install IAM:

```bash
helm install aidp-iam aidp-iam-1.6.2.tgz \
  --namespace aidp-iam --create-namespace \
  --wait --timeout=12m
```

Single-node kind/dev override:

```bash
helm install aidp-iam aidp-iam-1.6.2.tgz \
  --namespace aidp-iam --create-namespace \
  --wait --timeout=12m \
  --set iam-app.replicas=1 \
  --set iam-app.podAntiAffinity.enabled=false
```

See `docs/release-v1.6.2-deploy.md` for image loading, CAS client setup, and
visual demo commands.

## Verification Run

Validated from a freshly recreated `kind-da-cluster`:

- `helm install aidp-gateway package-gateway/charts/aidp-gateway`
- Gateway `eg` reached `PROGRAMMED=True`.
- `http://localhost:30080/` returned HTTP `404` before IAM routes, confirming
  Envoy data-plane traffic was active.
- `helm install aidp-iam package-iam/charts/aidp-iam` completed.
- Keycloak, Postgres, `keycloak-init`, and `iam-services` all reached ready.
- OIDC discovery returned issuer `http://localhost:30080/realms/aidp`.
- Password grant for `admin / Admin@123` returned a JWT.
- CAS smoke test created `cas-test`, received an `ST-...` ticket, and
  `/serviceValidate` returned `<cas:authenticationSuccess>` for `normal-user`.
- `cas-client-demo:v1` deployed in Kubernetes and returned `/health` HTTP 200
  through `kubectl port-forward`.

## Release Assets

- `aidp-gateway-1.6.2.tgz`
- `aidp-gateway-v1.6.2-images-amd64.tar.gz`
- `aidp-gateway-v1.6.2-images-arm64.tar.gz`
- `aidp-iam-1.6.2.tgz`
- `aidp-iam-v1.6.2-images-amd64.tar.gz`
- `aidp-iam-v1.6.2-images-arm64.tar.gz`
- `release-v1.6.2-deploy.md`
