package com.dataagent.keycloak.requiredaction;

import java.net.URI;
import java.util.Map;
import java.util.concurrent.TimeUnit;

import jakarta.ws.rs.core.MultivaluedMap;
import jakarta.ws.rs.core.Response;
import jakarta.ws.rs.core.UriBuilder;

import org.jboss.logging.Logger;
import org.keycloak.Config;
import org.keycloak.authentication.AuthenticatorUtil;
import org.keycloak.authentication.InitiatedActionSupport;
import org.keycloak.authentication.RequiredActionContext;
import org.keycloak.authentication.RequiredActionFactory;
import org.keycloak.authentication.RequiredActionProvider;
import org.keycloak.common.util.Time;
import org.keycloak.credential.CredentialModel;
import org.keycloak.credential.CredentialProvider;
import org.keycloak.credential.PasswordCredentialProvider;
import org.keycloak.credential.PasswordCredentialProviderFactory;
import org.keycloak.events.Details;
import org.keycloak.events.Errors;
import org.keycloak.events.EventBuilder;
import org.keycloak.events.EventType;
import org.keycloak.models.Constants;
import org.keycloak.models.KeycloakContext;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.KeycloakSessionFactory;
import org.keycloak.models.ModelException;
import org.keycloak.models.RealmModel;
import org.keycloak.models.UserCredentialModel;
import org.keycloak.models.UserModel;
import org.keycloak.models.credential.PasswordCredentialModel;
import org.keycloak.models.utils.FormMessage;
import org.keycloak.protocol.oidc.OIDCLoginProtocol;
import org.keycloak.services.Urls;
import org.keycloak.services.messages.Messages;
import org.keycloak.services.validation.Validation;
import org.keycloak.sessions.AuthenticationSessionModel;

/**
 * Overrides Keycloak's built-in UPDATE_PASSWORD required action.
 *
 * The validation and password update behavior stays aligned with the stock
 * provider. The difference is the successful path: after the password is
 * changed, the current authentication flow is not completed. The browser is
 * redirected to a fresh login request with prompt=login instead.
 */
public class ReloginUpdatePassword implements RequiredActionProvider, RequiredActionFactory {
    private static final Logger logger = Logger.getLogger(ReloginUpdatePassword.class);

    @Override
    public InitiatedActionSupport initiatedActionSupport() {
        return InitiatedActionSupport.SUPPORTED;
    }

    @Override
    public void evaluateTriggers(RequiredActionContext context) {
        if (!AuthenticatorUtil.isPasswordValidated(context.getAuthenticationSession())) {
            return;
        }

        int daysToExpirePassword = context.getRealm().getPasswordPolicy().getDaysToExpirePassword();
        if (daysToExpirePassword == -1) {
            return;
        }

        PasswordCredentialProvider passwordProvider = (PasswordCredentialProvider) context.getSession()
                .getProvider(CredentialProvider.class, PasswordCredentialProviderFactory.PROVIDER_ID);
        CredentialModel password = passwordProvider.getPassword(context.getRealm(), context.getUser());
        if (password == null) {
            return;
        }

        if (password.getCreatedDate() == null) {
            context.getUser().addRequiredAction(UserModel.RequiredAction.UPDATE_PASSWORD);
            logger.debug("User is required to update password");
            return;
        }

        long timeElapsed = Time.toMillis(Time.currentTime()) - password.getCreatedDate();
        long timeToExpire = TimeUnit.DAYS.toMillis(daysToExpirePassword);
        if (timeElapsed > timeToExpire) {
            context.getUser().addRequiredAction(UserModel.RequiredAction.UPDATE_PASSWORD);
            logger.debug("User is required to update password");
        }
    }

    @Override
    public void requiredActionChallenge(RequiredActionContext context) {
        Response challenge = context.form()
                .setAttribute("username", context.getAuthenticationSession().getAuthenticatedUser().getUsername())
                .createResponse(UserModel.RequiredAction.UPDATE_PASSWORD);
        context.challenge(challenge);
    }

    @Override
    public void processAction(RequiredActionContext context) {
        EventBuilder event = context.getEvent();
        AuthenticationSessionModel authSession = context.getAuthenticationSession();
        UserModel user = context.getUser();
        MultivaluedMap<String, String> formData = context.getHttpRequest().getDecodedFormParameters();

        event.event(EventType.UPDATE_CREDENTIAL);
        event.detail(Details.CREDENTIAL_TYPE, PasswordCredentialModel.PASSWORD);
        EventBuilder deprecatedEvent = event.clone().event(EventType.UPDATE_PASSWORD);

        String passwordNew = formData.getFirst("password-new");
        String passwordConfirm = formData.getFirst("password-confirm");

        EventBuilder errorEvent = event.clone().event(EventType.UPDATE_CREDENTIAL_ERROR)
                .client(authSession.getClient())
                .user(authSession.getAuthenticatedUser());
        EventBuilder deprecatedErrorEvent = errorEvent.clone().event(EventType.UPDATE_PASSWORD_ERROR);

        if (Validation.isBlank(passwordNew)) {
            Response challenge = context.form()
                    .setAttribute("username", authSession.getAuthenticatedUser().getUsername())
                    .addError(new FormMessage(Validation.FIELD_PASSWORD, Messages.MISSING_PASSWORD))
                    .createResponse(UserModel.RequiredAction.UPDATE_PASSWORD);
            context.challenge(challenge);
            errorEvent.error(Errors.PASSWORD_MISSING);
            deprecatedErrorEvent.error(Errors.PASSWORD_MISSING);
            return;
        } else if (!passwordNew.equals(passwordConfirm)) {
            Response challenge = context.form()
                    .setAttribute("username", authSession.getAuthenticatedUser().getUsername())
                    .addError(new FormMessage(Validation.FIELD_PASSWORD_CONFIRM, Messages.NOTMATCH_PASSWORD))
                    .createResponse(UserModel.RequiredAction.UPDATE_PASSWORD);
            context.challenge(challenge);
            errorEvent.error(Errors.PASSWORD_CONFIRM_ERROR);
            deprecatedErrorEvent.error(Errors.PASSWORD_CONFIRM_ERROR);
            return;
        }

        if ("on".equals(formData.getFirst("logout-sessions"))) {
            AuthenticatorUtil.logoutOtherSessions(context);
        }

        try {
            user.credentialManager().updateCredential(UserCredentialModel.password(passwordNew, false));
            user.removeRequiredAction(UserModel.RequiredAction.UPDATE_PASSWORD);
            authSession.removeRequiredAction(UserModel.RequiredAction.UPDATE_PASSWORD);
            deprecatedEvent.success();

            Response reloginRedirect = Response.seeOther(buildReloginUri(context, authSession)).build();
            context.challenge(reloginRedirect);
        } catch (ModelException me) {
            errorEvent.detail(Details.REASON, me.getMessage()).error(Errors.PASSWORD_REJECTED);
            deprecatedErrorEvent.detail(Details.REASON, me.getMessage()).error(Errors.PASSWORD_REJECTED);
            Response challenge = context.form()
                    .setAttribute("username", authSession.getAuthenticatedUser().getUsername())
                    .setError(me.getMessage(), me.getParameters())
                    .createResponse(UserModel.RequiredAction.UPDATE_PASSWORD);
            context.challenge(challenge);
        } catch (Exception ape) {
            errorEvent.detail(Details.REASON, ape.getMessage()).error(Errors.PASSWORD_REJECTED);
            deprecatedErrorEvent.detail(Details.REASON, ape.getMessage()).error(Errors.PASSWORD_REJECTED);
            Response challenge = context.form()
                    .setAttribute("username", authSession.getAuthenticatedUser().getUsername())
                    .setError(ape.getMessage())
                    .createResponse(UserModel.RequiredAction.UPDATE_PASSWORD);
            context.challenge(challenge);
        }
    }

    private URI buildReloginUri(RequiredActionContext context, AuthenticationSessionModel authSession) {
        String realmName = context.getRealm().getName();
        URI baseUri = context.getUriInfo().getBaseUri();

        if (!OIDCLoginProtocol.LOGIN_PROTOCOL.equals(authSession.getProtocol())
                || authSession.getClient() == null
                || authSession.getRedirectUri() == null) {
            return Urls.realmLoginPage(baseUri, realmName);
        }

        UriBuilder builder = context.getUriInfo().getBaseUriBuilder()
                .path("realms")
                .path(realmName)
                .path("protocol")
                .path("openid-connect")
                .path("auth");

        for (Map.Entry<String, String> note : authSession.getClientNotes().entrySet()) {
            if (note.getKey() != null && note.getValue() != null) {
                builder.queryParam(note.getKey(), note.getValue());
            }
        }

        builder.replaceQueryParam(OIDCLoginProtocol.CLIENT_ID_PARAM, authSession.getClient().getClientId());
        builder.replaceQueryParam(OIDCLoginProtocol.REDIRECT_URI_PARAM, authSession.getRedirectUri());
        if (authSession.getClientNote(OIDCLoginProtocol.RESPONSE_TYPE_PARAM) == null) {
            builder.queryParam(OIDCLoginProtocol.RESPONSE_TYPE_PARAM, "code");
        }
        if (authSession.getClientNote(OIDCLoginProtocol.SCOPE_PARAM) == null) {
            builder.queryParam(OIDCLoginProtocol.SCOPE_PARAM, "openid");
        }
        builder.replaceQueryParam(OIDCLoginProtocol.PROMPT_PARAM, OIDCLoginProtocol.PROMPT_VALUE_LOGIN);
        builder.replaceQueryParam(OIDCLoginProtocol.MAX_AGE_PARAM, "0");

        return builder.build();
    }

    @Override
    public void close() {
    }

    @Override
    public RequiredActionProvider create(KeycloakSession session) {
        return new ReloginUpdatePassword();
    }

    @Override
    public void init(Config.Scope config) {
    }

    @Override
    public void postInit(KeycloakSessionFactory factory) {
    }

    @Override
    public String getDisplayText() {
        return "Update Password";
    }

    @Override
    public String getId() {
        return UserModel.RequiredAction.UPDATE_PASSWORD.name();
    }

    @Override
    public boolean isOneTimeAction() {
        return true;
    }

    @Override
    public int order() {
        return 100;
    }

    @Override
    public int getMaxAuthAge(KeycloakSession session) {
        if (session == null) {
            return Constants.KC_ACTION_MAX_AGE;
        }

        KeycloakContext keycloakContext = session.getContext();
        RealmModel realm = keycloakContext.getRealm();
        int maxAge = realm.getPasswordPolicy().getMaxAuthAge();
        if (maxAge >= 0) {
            return maxAge;
        }
        return RequiredActionProvider.super.getMaxAuthAge(session);
    }
}
