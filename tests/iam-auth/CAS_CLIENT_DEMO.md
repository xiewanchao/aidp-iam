# Keycloak CAS Client Demo

This directory contains a minimal CAS client for validating
`keycloak-protocol-cas` against the local AIDP IAM deployment.

## Keycloak Admin Console

Open:

```text
https://localhost:30080/admin
```

Use the local bootstrap admin:

```text
admin / admin
```

Select realm `aidp`, then create or verify the CAS client:

```text
Clients -> Create client
Client type: CAS
Client ID: cas-test
Valid redirect URIs: http://localhost:18080/cas/callback
Web origins: http://localhost:18080
```

On Keycloak 26 Admin Console v2, mappers are usually under:

```text
Clients -> cas-test -> Client scopes -> cas-test-dedicated -> Mappers
```

## Local Browser Demo

Run directly with Python:

```bash
python tests/iam-auth/cas-client-demo.py
```

Open:

```text
http://localhost:18080
```

Click `Login with Keycloak CAS`, then sign in with:

```text
normal-user / NormalUser@123
```

The callback page receives `ticket=ST-...`, calls
`/realms/aidp/protocol/cas/serviceValidate`, and renders the parsed CAS XML.

## Container Demo

Build:

```bash
docker build -t cas-client-demo:v1 \
  -f tests/iam-auth/Dockerfile.cas-client-demo \
  tests/iam-auth
```

Run:

```bash
docker run --rm -p 18080:18080 \
  -e KEYCLOAK_BASE_URL=https://host.docker.internal:30080 \
  -e SERVICE_URL=http://localhost:18080/cas/callback \
  cas-client-demo:v1
```

## Kubernetes Demo

Load the image into kind:

```bash
kind load docker-image cas-client-demo:v1 --name da-cluster
```

Deploy:

```bash
kubectl apply -f tests/iam-auth/cas-client-demo.k8s.yaml
kubectl -n aidp-iam rollout status deployment/cas-client-demo --timeout=120s
kubectl -n aidp-iam port-forward svc/cas-client-demo 18080:18080
```

Open:

```text
http://localhost:18080
```

Cleanup:

```bash
kubectl delete -f tests/iam-auth/cas-client-demo.k8s.yaml
```

## Smoke Tests

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File tests\iam-auth\cas-login-smoke.ps1
```

Linux/macOS:

```bash
./tests/iam-auth/cas-login-smoke.sh
```
