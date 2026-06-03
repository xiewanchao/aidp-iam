# KMS/KMC Local Debug Guide

This guide covers the local debug path before the real KMC environment is
available.

## Modes

`keycloak-init` supports these modes:

| Variable | Value | Purpose |
|---|---|---|
| `SECRET_BACKEND` | `plain` | Existing behavior. Store plain values in K8s Secret. |
| `SECRET_BACKEND` | `mock` | Store keyId to plaintext in a local JSON file. No KMC required. |
| `SECRET_BACKEND` | `oms-kms` | Call OMS KMS HTTP APIs. |
| `MACHINE_TOKEN_PROVIDER` | `static` | Read token from `KMS_AUTH_TOKEN`. |
| `MACHINE_TOKEN_PROVIDER` | `mock-kmc` | Exercise platform.conf parsing without importing KMC. |
| `MACHINE_TOKEN_PROVIDER` | `kmc` | Read platform Secret/ConfigMap and call KMC. |

Production target:

```text
SECRET_BACKEND=oms-kms
MACHINE_TOKEN_PROVIDER=kmc
KMS_BASE_URL=https://omsservice.agentinfra.svc.cluster.local:18082
KMS_API_PREFIX=/framework/v1
PROTECT_CLIENT_SECRET=false
```

Keep `PROTECT_CLIENT_SECRET=false` until all runtime consumers can resolve
`client-secret-key-id`. The default preserves the existing
`keycloak-aidp-client/client-secret` contract used by IAM services.

Local development target:

```text
SECRET_BACKEND=mock
MACHINE_TOKEN_PROVIDER=static
KMS_AUTH_TOKEN=dev-token
```

## Step 1: Unit Test Mock Secret Backend

```bash
python3 tests/e2e/kms-mock/test-secret-backend.py
```

Expected:

```text
PASS mock secret backend
```

## Step 2: Run HTTP Mock OMS KMS

```bash
python3 tests/fixtures/mock-oms-kms/mock_oms_kms.py
```

In another shell:

```bash
BASE_URL=http://127.0.0.1:18082 TOKEN=dev-token \
  bash tests/e2e/kms-mock/test-http-mock.sh
```

Expected:

```text
PASS HTTP mock OMS KMS
```

## Step 3: Deploy Mock OMS KMS in Kind

```bash
CLUSTER_NAME=da-cluster bash tests/e2e/kms-mock/deploy-kind-mock.sh
```

This creates:

```text
agentinfra/cube-paas-tomcat
agentinfra/cube-security-priv
agentinfra/mock-oms-kms
```

## Step 4: Use Mock OMS KMS from keycloak-init

Use these Helm overrides for local Kind testing:

```bash
--set keycloak.keycloakInit.security.secretBackend=oms-kms \
--set keycloak.keycloakInit.security.machineTokenProvider=static \
--set keycloak.keycloakInit.security.kmsBaseUrl=http://mock-oms-kms.agentinfra.svc.cluster.local:18082 \
--set keycloak.keycloakInit.security.kmsApiPrefix=/framework/v1 \
--set keycloak.keycloakInit.security.kmsAuthToken=dev-token
```

If rendering the keycloak subchart directly, drop the leading `keycloak.`.

## Step 5: Company Environment Switch

After KMC is available, switch only the provider:

```bash
--set keycloak.keycloakInit.security.secretBackend=oms-kms \
--set keycloak.keycloakInit.security.machineTokenProvider=kmc \
--set keycloak.keycloakInit.security.kmsBaseUrl=https://omsservice.agentinfra.svc.cluster.local:18082 \
--set keycloak.keycloakInit.security.kmsApiPrefix=/framework/v1 \
--set keycloak.keycloakInit.security.machineTokenNamespace=agentinfra
```

Do not print full tokens or decrypted passwords in logs.
