# AIDP Keycloak Theme

Custom Keycloak login theme built with Keycloakify. It covers login, register, reset password, update password, SAML handoff, MFA, email verification, and error pages.

## Development

```bash
cd apps/keycloak-theme
npm install
npm run storybook
```

## Build

```bash
cd apps/keycloak-theme
npm run build-keycloak-theme
```

The local deployment script packages the generated theme jar into `build/docker/keycloak-custom/keycloak-theme.jar` before building `keycloak-custom:26.5.2`.
