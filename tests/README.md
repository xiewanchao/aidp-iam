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

## Deploy

- `deploy/`: one-click deployment, upgrade, certificate, observability, and
  offline deployment validation assets.
