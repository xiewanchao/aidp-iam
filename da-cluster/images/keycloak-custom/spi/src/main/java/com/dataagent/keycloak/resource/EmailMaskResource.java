package com.dataagent.keycloak.resource;

import org.keycloak.models.KeycloakSession;
import org.keycloak.models.RealmModel;
import org.keycloak.models.UserModel;
import org.keycloak.services.resource.RealmResourceProvider;

import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.QueryParam;
import jakarta.ws.rs.core.MediaType;
import jakarta.ws.rs.core.Response;

import java.util.List;
import java.util.HashMap;
import java.util.Map;

public class EmailMaskResource implements RealmResourceProvider {

    private final KeycloakSession session;

    public EmailMaskResource(KeycloakSession session) {
        this.session = session;
    }

    @Override
    public Object getResource() {
        return this;
    }

    @Override
    public void close() {
    }

    @GET
    @Path("get-email-masked")
    @Produces(MediaType.APPLICATION_JSON)
    public Response getMaskedEmail(@QueryParam("username") String username) {
        RealmModel realm = session.getContext().getRealm();
        
        if (username == null || username.trim().isEmpty()) {
            Map<String, Object> error = new HashMap<>();
            error.put("error", "Username is required");
            return Response.status(Response.Status.BAD_REQUEST).entity(error).build();
        }

        UserModel user = session.users().getUserByUsername(realm, username.trim());
        
        if (user == null) {
            Map<String, Object> result = new HashMap<>();
            result.put("found", false);
            result.put("hasEmail", false);
            return Response.ok(result).build();
        }

        String email = user.getEmail();

        Map<String, Object> result = new HashMap<>();
        result.put("found", true);
        result.put("userId", user.getId());
        
        if (email == null || email.trim().isEmpty()) {
            result.put("hasEmail", false);
        } else {
            result.put("hasEmail", true);
            result.put("maskedEmail", maskEmail(email));
        }

        return Response.ok(result).build();
    }

    @GET
    @Path("verify-email")
    @Produces(MediaType.APPLICATION_JSON)
    public Response verifyEmail(@QueryParam("username") String username, @QueryParam("email") String email) {
        RealmModel realm = session.getContext().getRealm();
        
        if (username == null || username.trim().isEmpty()) {
            Map<String, Object> error = new HashMap<>();
            error.put("error", "Username is required");
            return Response.status(Response.Status.BAD_REQUEST).entity(error).build();
        }

        if (email == null || email.trim().isEmpty()) {
            Map<String, Object> error = new HashMap<>();
            error.put("error", "Email is required");
            return Response.status(Response.Status.BAD_REQUEST).entity(error).build();
        }

        UserModel user = session.users().getUserByUsername(realm, username.trim());
        
        Map<String, Object> result = new HashMap<>();
        
        if (user == null) {
            result.put("found", false);
            result.put("match", false);
            return Response.ok(result).build();
        }

        String userEmail = user.getEmail();
        result.put("found", true);
        
        if (userEmail == null || userEmail.trim().isEmpty()) {
            result.put("match", false);
            result.put("reason", "noEmail");
        } else {
            result.put("match", userEmail.toLowerCase().equals(email.trim().toLowerCase()));
        }

        return Response.ok(result).build();
    }

    private String maskEmail(String email) {
        if (email == null || !email.contains("@")) {
            return email;
        }
        String[] parts = email.split("@");
        String local = parts[0];
        String domain = parts[1];
        
        if (local.length() <= 1) {
            return email;
        }
        
        return local.substring(0, 1) + "*****@" + domain;
    }
}