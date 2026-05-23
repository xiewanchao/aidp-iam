# Keycloak with custom providers and theme
FROM quay.io/keycloak/keycloak:26.5.2

# Copy the pre-built script mapper JAR
COPY data-agent-mapper.jar /opt/keycloak/providers/

# Copy custom login theme JAR (Keycloakify React + styles)
COPY keycloak-theme.jar /opt/keycloak/providers/

# Copy CAS protocol provider
COPY keycloak-protocol-cas-26.5.2.jar /opt/keycloak/providers/

RUN /opt/keycloak/bin/kc.sh build --features=scripts