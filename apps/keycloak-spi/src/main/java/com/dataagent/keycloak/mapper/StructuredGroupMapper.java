package com.dataagent.keycloak.mapper;

import org.keycloak.models.ClientSessionContext;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.ProtocolMapperModel;
import org.keycloak.models.UserSessionModel;
import org.keycloak.protocol.oidc.mappers.AbstractOIDCProtocolMapper;
import org.keycloak.protocol.oidc.mappers.OIDCAccessTokenMapper;
import org.keycloak.protocol.oidc.mappers.OIDCAttributeMapperHelper;
import org.keycloak.protocol.oidc.mappers.OIDCIDTokenMapper;
import org.keycloak.protocol.oidc.mappers.UserInfoTokenMapper;
import org.keycloak.provider.ProviderConfigProperty;
import org.keycloak.representations.IDToken;

import java.util.ArrayList;
import java.util.List;
import java.util.stream.Collectors;

/**
 * Custom Protocol Mapper that outputs the user's top-level group memberships
 * as two parallel JWT claims per diagrams/story-breakdown.md SR01:
 *
 *   "groups":     ["master-admins", "tenant-admins", "all-users", ...]   (names)
 *   "group_ids":  ["uuid-a",        "uuid-b",        "uuid-c",     ...]  (Keycloak group UUIDs)
 *
 * The claim names are configurable. Order is preserved so groups[i] and group_ids[i]
 * refer to the same Keycloak group.
 *
 * Built on the same pattern as {@link StructuredRoleMapper}.
 */
public class StructuredGroupMapper extends AbstractOIDCProtocolMapper
        implements OIDCAccessTokenMapper, OIDCIDTokenMapper, UserInfoTokenMapper {

    public static final String PROVIDER_ID = "structured-group-mapper";

    private static final String CFG_NAMES_CLAIM = "groups.claim.name";
    private static final String CFG_IDS_CLAIM   = "group.ids.claim.name";

    private static final List<ProviderConfigProperty> CONFIG_PROPERTIES = new ArrayList<>();

    static {
        ProviderConfigProperty namesClaim = new ProviderConfigProperty();
        namesClaim.setName(CFG_NAMES_CLAIM);
        namesClaim.setLabel("Groups Claim Name");
        namesClaim.setType(ProviderConfigProperty.STRING_TYPE);
        namesClaim.setDefaultValue("groups");
        namesClaim.setHelpText("JWT claim name for group names (default: groups).");
        CONFIG_PROPERTIES.add(namesClaim);

        ProviderConfigProperty idsClaim = new ProviderConfigProperty();
        idsClaim.setName(CFG_IDS_CLAIM);
        idsClaim.setLabel("Group IDs Claim Name");
        idsClaim.setType(ProviderConfigProperty.STRING_TYPE);
        idsClaim.setDefaultValue("group_ids");
        idsClaim.setHelpText("JWT claim name for group UUIDs (default: group_ids).");
        CONFIG_PROPERTIES.add(idsClaim);

        OIDCAttributeMapperHelper.addIncludeInTokensConfig(CONFIG_PROPERTIES, StructuredGroupMapper.class);
    }

    @Override public String getId() { return PROVIDER_ID; }

    @Override public String getDisplayType() { return "Structured Group Mapper"; }

    @Override public String getDisplayCategory() { return TOKEN_MAPPER_CATEGORY; }

    @Override public String getHelpText() {
        return "Adds the user's top-level group memberships as two parallel claims: "
             + "groups (names) and group_ids (UUIDs).";
    }

    @Override public List<ProviderConfigProperty> getConfigProperties() { return CONFIG_PROPERTIES; }

    @Override
    protected void setClaim(IDToken token, ProtocolMapperModel mappingModel,
                            UserSessionModel userSession, KeycloakSession keycloakSession,
                            ClientSessionContext clientSessionCtx) {

        String namesClaim = mappingModel.getConfig().getOrDefault(CFG_NAMES_CLAIM, "groups");
        String idsClaim   = mappingModel.getConfig().getOrDefault(CFG_IDS_CLAIM,   "group_ids");

        List<String> names = userSession.getUser().getGroupsStream()
                .map(g -> g.getName())
                .collect(Collectors.toList());

        List<String> ids = userSession.getUser().getGroupsStream()
                .map(g -> g.getId())
                .collect(Collectors.toList());

        token.getOtherClaims().put(namesClaim, names);
        token.getOtherClaims().put(idsClaim,   ids);
    }
}
