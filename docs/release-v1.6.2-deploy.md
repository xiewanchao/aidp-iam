# AIDP IAM v1.6.2 Deployment

This release installs Gateway first, then IAM. The CAS protocol extension is
bundled into `keycloak-custom:26.5.2`, and the optional visual CAS client demo
is published as `cas-client-demo:v1`.

## Assets

Download the assets for your CPU architecture from GitHub release `v1.6.2`:

```text
aidp-gateway-1.6.2.tgz
aidp-gateway-v1.6.2-images-amd64.tar.gz
aidp-gateway-v1.6.2-images-arm64.tar.gz
aidp-iam-1.6.2.tgz
aidp-iam-v1.6.2-images-amd64.tar.gz
aidp-iam-v1.6.2-images-arm64.tar.gz
```

Use `amd64` for x86_64 nodes and `arm64` for ARM nodes.

## Load Images

Docker:

```bash
tar xzf aidp-gateway-v1.6.2-images-amd64.tar.gz
tar xzf aidp-iam-v1.6.2-images-amd64.tar.gz

for tar in package-gateway/images/amd64/*.tar; do docker load -i "$tar"; done
for tar in package-iam/images/amd64/*.tar; do docker load -i "$tar"; done
```

containerd:

```bash
for tar in package-gateway/images/amd64/*.tar; do ctr -n k8s.io images import "$tar"; done
for tar in package-iam/images/amd64/*.tar; do ctr -n k8s.io images import "$tar"; done
```

iSulad:

```bash
for tar in package-gateway/images/amd64/*.tar; do isula load -i "$tar"; done
for tar in package-iam/images/amd64/*.tar; do isula load -i "$tar"; done
```

Replace `amd64` with `arm64` on ARM nodes.

## Install Gateway

```bash
helm install aidp-gateway aidp-gateway-1.6.2.tgz \
  --namespace aidp-gateway --create-namespace \
  --wait --timeout=10m
```

Verify:

```bash
kubectl -n envoy-gateway-system get gateway eg
kubectl -n aidp-gateway get svc -l gateway.envoyproxy.io/owning-gateway-name=eg
curl -sS -o /dev/null -w "HTTP %{http_code}\n" http://<node-ip>:30080/
```

Expected gateway status is `PROGRAMMED=True`; HTTP `404` is normal before IAM
routes are installed.

## Install IAM

For a production DNS name or EIP:

```bash
helm install aidp-iam aidp-iam-1.6.2.tgz \
  --namespace aidp-iam --create-namespace \
  --wait --timeout=12m \
  --set keycloak.keycloak.config.hostname=http://<EIP-or-DNS>:30080
```

For local kind or dynamic host testing, omit the hostname:

```bash
helm install aidp-iam aidp-iam-1.6.2.tgz \
  --namespace aidp-iam --create-namespace \
  --wait --timeout=12m
```

Single-node development clusters should reduce IAM replicas:

```bash
helm install aidp-iam aidp-iam-1.6.2.tgz \
  --namespace aidp-iam --create-namespace \
  --wait --timeout=12m \
  --set iam-app.replicas=1 \
  --set iam-app.podAntiAffinity.enabled=false
```

Verify:

```bash
kubectl get pods -n keycloak
kubectl get pods -n aidp-iam
curl http://<EIP-or-node-ip>:30080/realms/aidp/.well-known/openid-configuration | head -c 200
```

Fetch an admin token:

```bash
SECRET=$(kubectl -n aidp-iam get secret keycloak-aidp-client \
  -o go-template='{{index .data "client-secret" | base64decode}}')

curl -sS -X POST http://<EIP-or-node-ip>:30080/realms/aidp/protocol/openid-connect/token \
  -d "grant_type=password&client_id=aidp-client&client_secret=$SECRET&username=admin&password=Admin@123" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'][:50])"
```

## CAS Client Configuration

Create a CAS client in Keycloak Admin Console:

```text
Realm: aidp
Clients -> Create client
Client type: CAS
Client ID: cas-test
Valid redirect URIs: http://localhost:18080/cas/callback
Web origins: http://localhost:18080
```

CAS endpoint values for a real OM CAS Client:

```text
CAS base URL:       http://<EIP-or-node-ip>:30080/realms/aidp/protocol/cas
CAS login URL:      http://<EIP-or-node-ip>:30080/realms/aidp/protocol/cas/login
CAS validate URL:   http://<EIP-or-node-ip>:30080/realms/aidp/protocol/cas/serviceValidate
CAS logout URL:     http://<EIP-or-node-ip>:30080/realms/aidp/protocol/cas/logout
```

## Visual CAS Client Demo

Deploy the demo:

```bash
kubectl apply -f tests/iam-auth/cas-client-demo.k8s.yaml
kubectl -n aidp-iam rollout status deployment/cas-client-demo --timeout=120s
kubectl -n aidp-iam port-forward svc/cas-client-demo 18080:18080
```

Open:

```text
http://localhost:18080
```

Click `Login with Keycloak CAS`, then sign in:

```text
normal-user / NormalUser@123
```

The callback page displays the CAS ticket, parsed `cas:user`, attributes, and
raw `/serviceValidate` XML.

Automated CAS smoke tests:

```bash
./tests/iam-auth/cas-login-smoke.sh
```

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File tests\iam-auth\cas-login-smoke.ps1
```

## Cleanup

```bash
kubectl delete -f tests/iam-auth/cas-client-demo.k8s.yaml --ignore-not-found
helm uninstall aidp-iam -n aidp-iam
helm uninstall aidp-gateway -n aidp-gateway
```

CRDs are cluster-scoped and are not deleted by Helm uninstall.
