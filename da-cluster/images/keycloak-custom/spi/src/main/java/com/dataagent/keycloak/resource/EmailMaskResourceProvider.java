package com.dataagent.keycloak.resource;

import org.keycloak.models.KeycloakSession;
import org.keycloak.services.resource.RealmResourceProvider;

public class EmailMaskResourceProvider implements RealmResourceProvider {

    private final KeycloakSession session;

    public EmailMaskResourceProvider(KeycloakSession session) {
        this.session = session;
    }

    @Override
    public Object getResource() {
        return new EmailMaskResource(session);
    }

    @Override
    public void close() {
    }
}