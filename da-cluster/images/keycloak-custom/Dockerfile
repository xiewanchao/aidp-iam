# Keycloak with custom providers: script mapper, theme, and CAS protocol
FROM quay.io/keycloak/keycloak:26.5.2

# Copy the pre-built script mapper JAR
COPY data-agent-mapper.jar /opt/keycloak/providers/

# Copy custom login theme JAR (built by keycloakify)
COPY keycloak-theme.jar /opt/keycloak/providers/

# Copy CAS protocol extension matching Keycloak 26.5.2
COPY keycloak-protocol-cas-26.5.2.jar /opt/keycloak/providers/

RUN /opt/keycloak/bin/kc.sh build --features=scripts
