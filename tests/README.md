# Test Assets

This directory keeps test strategy documents, fixtures, mock services, and scripts
grouped by requirement area.

## Gateway

- `gateway-routing/`: Gateway install, initialization, routing, policy binding,
  ReferenceGrant, path allowlist, and source IP allowlist tests.
- `gateway-business-ip/`: Gateway business IP and container network frontend
  integration tests.

Current Gateway test plans:

- `gateway/docs/gateway-test-design-xmind.txt`
- `gateway-routing/docs/gateway-routing-test-plan.md`
- `gateway-business-ip/docs/gateway-business-ip-test-plan.md`

## IAM

IAM test assets are intentionally separated for later expansion:

- `iam-auth/`: authentication and IdP federation.
- `iam-authz/`: path-level and resource-level authorization.
- `iam-acl-sync/`: ACL auto-sync and permission management.
- `iam-list-filter/`: resource list filtering.
- `iam-apikey/`: API key lifecycle and authentication.

Current IAM smoke tests:

- `iam-auth/cas-login-smoke.ps1`: creates/updates a CAS protocol client,
  simulates Keycloak form login, extracts a CAS service ticket, and validates it
  with `/serviceValidate`.
- `iam-auth/cas-login-smoke.sh`: Linux/macOS equivalent of the CAS login smoke
  test, driven by environment variables.
- `iam-auth/cas-client-demo.py`: minimal browser-based CAS Client demo that
  redirects to Keycloak, receives `ticket`, calls `/serviceValidate`, and shows
  the parsed CAS response.

## Deploy

- `deploy/`: one-click deployment, upgrade, certificate, observability, and
  offline deployment validation assets.
