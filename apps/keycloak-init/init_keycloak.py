#!/usr/bin/env python3
"""
Keycloak Init Job — single-tenant model (per diagrams/ui-wireframes.md).

Provisions a single `aidp` realm with:
  - groups: `master-admins` (IAM admin), `tenant-admins` (tenant admin), `all-users` (default group)
  - users: `admin` (in master-admins + tenant-admins + all-users), `normal-user` (in all-users)
  - confidential client: `aidp-client`
      * serviceAccountsEnabled  → backend-to-backend client_credentials
      * directAccessGrants      → user password grant for tests
      * service account is in `master-admins` (full bypass via OPA)
      * realm-management / realm-admin role for Keycloak admin-API calls
  - public client: `aidp-web`
      * no client authentication (publicClient=True)
      * no authorization (serviceAccountsEnabled=False)
      * for browser-based frontends (OIDC authorization code flow)
  - JWT mappers (groups + group_ids) via the structured-group-mapper SPI
  - configures Keycloak realm, users, groups, clients, and mappers

The Keycloak built-in `master` realm is left untouched (Keycloak operations only).
"""

import os
import time
import base64
import requests
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from secret_backend import secret_store


def csv_env(name, default):
    raw = os.getenv(name)
    if raw is None:
        return list(default)
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or list(default)


# ===================== Configuration =====================
KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://keycloak:8080")
KEYCLOAK_HEALTH_URL = os.getenv("KEYCLOAK_HEALTH_URL", "http://keycloak:9000")
KC_ADMIN_USER = os.getenv("ADMIN_USER", "admin")
KC_ADMIN_PASSWORD = secret_store().resolve(
    env_name="ADMIN_PASSWORD",
    key_id_env_name="ADMIN_PASSWORD_KEY_ID",
    default="",
)

REALM = os.getenv("AIDP_REALM", "aidp")
CLIENT_ID = os.getenv("AIDP_CLIENT_ID", "aidp-client")
WEB_CLIENT_ID = os.getenv("AIDP_WEB_CLIENT_ID", "aidp-web")
CAS_CLIENT_ID = os.getenv("AIDP_CAS_CLIENT_ID", "oms-cas")
CAS_REDIRECT_URIS = csv_env("AIDP_CAS_REDIRECT_URIS", ["*"])
LOGIN_THEME = os.getenv("AIDP_LOGIN_THEME", "password-reset-confirm").strip()
WEB_REDIRECT_URIS = csv_env("AIDP_WEB_REDIRECT_URIS", ["/auth/login/callback"])
WEB_POST_LOGOUT_REDIRECT_URIS = csv_env(
    "AIDP_WEB_POST_LOGOUT_REDIRECT_URIS", ["/auth/login"]
)
WEB_WEB_ORIGINS = csv_env("AIDP_WEB_WEB_ORIGINS", ["+"])

ADMIN_USERNAME = os.getenv("AIDP_ADMIN_USER", "admin")
ADMIN_INIT_PASSWORD = secret_store().resolve(
    env_name="AIDP_ADMIN_PASSWORD",
    key_id_env_name="AIDP_ADMIN_PASSWORD_KEY_ID",
    default="Admin@123",
)
NORMAL_USERNAME = os.getenv("AIDP_NORMAL_USER", "normal-user")
NORMAL_INIT_PASSWORD = secret_store().resolve(
    env_name="AIDP_NORMAL_PASSWORD",
    key_id_env_name="AIDP_NORMAL_PASSWORD_KEY_ID",
    default="NormalUser@123",
)

K8S_SECRET_NAME = os.getenv("K8S_SECRET_NAME", "keycloak-aidp-client")
K8S_NAMESPACE = os.getenv("K8S_NAMESPACE", "keycloak")
RELEASE_REVISION = os.getenv("AIDP_RELEASE_REVISION", "")


# Email / SMTP settings (all optional; skip email config if SMTP_HOST is empty)
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_FROM = os.getenv("SMTP_FROM", "")
SMTP_FROM_DISPLAY = os.getenv("SMTP_FROM_DISPLAY", "AIDP IAM")
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = secret_store().resolve(
    env_name="SMTP_PASSWORD",
    key_id_env_name="SMTP_PASSWORD_KEY_ID",
    default="",
)
SMTP_SSL = os.getenv("SMTP_SSL", "true").lower() == "true"
SMTP_STARTTLS = os.getenv("SMTP_STARTTLS", "false").lower() == "true"
PROTECT_CLIENT_SECRET = (
    os.getenv("PROTECT_CLIENT_SECRET", "false").strip().lower()
    in {"1", "true", "yes", "y", "on"}
)

PASSWORD_EXPIRE_DAYS = int(os.getenv("PASSWORD_EXPIRE_DAYS", "0"))

TOTAL_STEPS = 9


# ===================== utilities =====================
def wait_for_keycloak():
    health_url = f"{KEYCLOAK_HEALTH_URL}/health/ready"
    print(f"[Step 1/{TOTAL_STEPS}] Waiting for Keycloak: {health_url}", flush=True)
    last_error = ""
    for _ in range(50):
        try:
            r = requests.get(health_url, timeout=5)
            if r.status_code == 200:
                print(f"[Step 1/{TOTAL_STEPS}] Keycloak is ready", flush=True)
                return
            last_error = f"HTTP {r.status_code}"
        except requests.RequestException as exc:
            last_error = str(exc)
        time.sleep(5)
    suffix = f": {last_error}" if last_error else ""
    raise RuntimeError(f"Keycloak not ready{suffix}")


def get_admin_token():
    print(f"[Step 2/{TOTAL_STEPS}] Getting Keycloak master admin token", flush=True)
    r = requests.post(
        f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
        data={
            "username": KC_ADMIN_USER,
            "password": KC_ADMIN_PASSWORD,
            "grant_type": "password",
            "client_id": "admin-cli",
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def k8s():
    config.load_incluster_config()
    return client.CoreV1Api()


def upsert_k8s_secret(name, data, label_component):
    v1 = k8s()
    enc = {k: base64.b64encode(v.encode()).decode() for k, v in data.items()}
    annotations = {}
    if RELEASE_REVISION:
        annotations["aidp-iam-release-revision"] = RELEASE_REVISION
    try:
        existing = v1.read_namespaced_secret(name, K8S_NAMESPACE)
        existing.data = enc
        existing.metadata.annotations = existing.metadata.annotations or {}
        existing.metadata.annotations.update(annotations)
        v1.patch_namespaced_secret(name, K8S_NAMESPACE, existing)
        print(f"  Updated K8s Secret: {name}", flush=True)
    except ApiException as e:
        if e.status != 404:
            raise
        v1.create_namespaced_secret(
            K8S_NAMESPACE,
            client.V1Secret(
                api_version="v1",
                kind="Secret",
                metadata=client.V1ObjectMeta(
                    name=name,
                    namespace=K8S_NAMESPACE,
                    labels={"app": "keycloak", "component": label_component},
                    annotations=annotations,
                ),
                type="Opaque",
                data=enc,
            ),
        )
        print(f"  Created K8s Secret: {name}", flush=True)


# ===================== Keycloak helpers =====================
def ensure_realm(token, realm):
    r = requests.get(
        f"{KEYCLOAK_URL}/admin/realms/{realm}", headers=auth_headers(token), timeout=10
    )
    login_settings = {
        "registrationAllowed": False,
        "loginWithEmailAllowed": False,
        "duplicateEmailsAllowed": False,
        "resetPasswordAllowed": True,
        "rememberMe": False,
        "verifyEmail": False,
        "editUsernameAllowed": True,
        "bruteForceProtected": True,
        "failureFactor": 3,
        "minimumQuickLoginWaitSeconds": 30,
        "internationalizationEnabled": True,
        "supportedLocales": ["en", "zh-CN"],
        "defaultLocale": "zh-CN",
    }
    if LOGIN_THEME:
        login_settings["loginTheme"] = LOGIN_THEME
    if r.status_code == 200:
        print(f"  Realm '{realm}' already exists, patching login settings", flush=True)
        patch = requests.put(
            f"{KEYCLOAK_URL}/admin/realms/{realm}",
            json=login_settings,
            headers=auth_headers(token),
            timeout=10,
        )
        if patch.status_code not in (200, 204):
            print(
                f"  Warning: patch realm settings returned {patch.status_code}",
                flush=True,
            )
        return
    body = {"realm": realm, "displayName": "AIDP IAM", "enabled": True}
    body.update(login_settings)
    r = requests.post(
        f"{KEYCLOAK_URL}/admin/realms", json=body, headers=auth_headers(token), timeout=10
    )
    if r.status_code not in (200, 201):
        raise RuntimeError(
            f"Failed to create realm '{realm}': {r.status_code} {r.text}"
        )
    print(f"  Created realm '{realm}'", flush=True)


def get_group(token, realm, name):
    r = requests.get(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/groups",
        headers=auth_headers(token),
        params={"search": name, "exact": "true"},
        timeout=10,
    )
    r.raise_for_status()
    for g in r.json():
        if g.get("name") == name:
            return g
    return None


def ensure_group(token, realm, name):
    g = get_group(token, realm, name)
    if g:
        print(f"  Group '{name}' already exists", flush=True)
        return g
    r = requests.post(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/groups",
        json={"name": name},
        headers=auth_headers(token),
        timeout=10,
    )
    if r.status_code not in (200, 201, 409):
        raise RuntimeError(f"Failed to create group '{name}': {r.text}")
    print(f"  Created group '{name}'", flush=True)
    return get_group(token, realm, name)


def set_default_groups(token, realm, group_ids):
    for gid in group_ids:
        r = requests.put(
            f"{KEYCLOAK_URL}/admin/realms/{realm}/default-groups/{gid}",
            headers=auth_headers(token),
            timeout=10,
        )
        if r.status_code not in (200, 204):
            print(
                f"  Warning: set default group {gid} returned {r.status_code}",
                flush=True,
            )


def find_user(token, realm, username):
    r = requests.get(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/users",
        headers=auth_headers(token),
        params={"username": username, "exact": "true"},
        timeout=10,
    )
    r.raise_for_status()
    users = r.json()
    return users[0] if users else None


def user_profile_attribute(name, display_name, validations):
    return {
        "name": name,
        "displayName": display_name,
        "validations": validations,
        "permissions": {
            "view": ["admin", "user"],
            "edit": ["admin", "user"],
        },
        "multivalued": False,
    }


def build_user_profile_config():
    return {
        "attributes": [
            user_profile_attribute(
                "username",
                "${username}",
                {
                    "length": {"min": 3, "max": 255},
                    "username-prohibited-characters": {},
                    "up-username-not-idn-homograph": {},
                },
            ),
            user_profile_attribute(
                "email",
                "${email}",
                {
                    "email": {},
                    "length": {"max": 255},
                },
            ),
            user_profile_attribute(
                "firstName",
                "${firstName}",
                {
                    "length": {"max": 255},
                    "person-name-prohibited-characters": {},
                },
            ),
            user_profile_attribute(
                "lastName",
                "${lastName}",
                {
                    "length": {"max": 255},
                    "person-name-prohibited-characters": {},
                },
            ),
            user_profile_attribute(
                "nickname",
                "${profile.nickname}",
                {
                    "length": {"max": 255},
                },
            ),
        ],
        "unmanagedAttributePolicy": "ADMIN_EDIT",
    }


def ensure_user_profile(token, realm):
    """Install a minimal User Profile: only `username` is required.

    Keycloak 26 forbids removing the built-in `email` / `firstName` /
    `lastName` attributes — they must stay declared. But their default
    config marks them as *required* for the "user" role, which forces
    every password-grant flow to pre-populate those fields. AIDP
    deliberately drops the concept of per-user human-name/email fields
    (users are identified solely by username + Keycloak-assigned UUID as
    `sub`), so we re-declare them **without the `required` block**,
    making them optional. Admins can still stash values via
    unmanagedAttributePolicy=ADMIN_EDIT for custom flows later.
    """
    profile_config = build_user_profile_config()
    r = requests.put(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/users/profile",
        json=profile_config,
        headers=auth_headers(token),
        timeout=10,
    )
    if r.status_code not in (200, 204):
        raise RuntimeError(
            f"Failed to set user-profile for '{realm}': {r.status_code} {r.text}"
        )
    print(
        f"  User profile applied (realm '{realm}'): only username required; "
        f"email/firstName/lastName kept optional",
        flush=True,
    )


def ensure_user(token, realm, username, password):
    existing = find_user(token, realm, username)
    if existing:
        uid = existing["id"]
        print(f"  User '{username}' already exists", flush=True)
        # Keep existing bootstrap users marked verified. Realm-level email
        # verification is disabled by default, so this does not block login.
        requests.put(
            f"{KEYCLOAK_URL}/admin/realms/{realm}/users/{uid}",
            json={"emailVerified": True},
            headers=auth_headers(token),
            timeout=10,
        )
    else:
        r = requests.post(
            f"{KEYCLOAK_URL}/admin/realms/{realm}/users",
            json={"username": username, "enabled": True, "emailVerified": True},
            headers=auth_headers(token),
            timeout=10,
        )
        if r.status_code not in (200, 201):
            raise RuntimeError(f"Failed to create user '{username}': {r.text}")
        uid = r.headers["Location"].split("/")[-1]
        print(f"  Created user '{username}' (id: {uid})", flush=True)
    # set/refresh password (temporary=False so password-grant works immediately)
    requests.put(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/users/{uid}/reset-password",
        json={"type": "password", "value": password, "temporary": False},
        headers=auth_headers(token),
        timeout=10,
    ).raise_for_status()
    return uid


def add_user_to_group(token, realm, user_id, group_id):
    r = requests.put(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/users/{user_id}/groups/{group_id}",
        headers=auth_headers(token),
        timeout=10,
    )
    if r.status_code not in (200, 204):
        print(
            f"  Warning: add user-to-group returned {r.status_code}: {r.text}",
            flush=True,
        )


def find_client_matches(token, realm, client_id):
    r = requests.get(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/clients",
        params={"clientId": client_id},
        headers=auth_headers(token),
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def confidential_client_body(client_id):
    return {
        "clientId": client_id,
        "name": f"{client_id} (auto-created)",
        "enabled": True,
        "clientAuthenticatorType": "client-secret",
        "redirectUris": ["*"],
        "webOrigins": ["*"],
        "serviceAccountsEnabled": True,
        "directAccessGrantsEnabled": True,
        "standardFlowEnabled": True,
        "publicClient": False,
        "bearerOnly": False,
    }


def public_client_body(client_id):
    return {
        "clientId": client_id,
        "name": f"{client_id} (auto-created)",
        "enabled": True,
        "redirectUris": WEB_REDIRECT_URIS,
        "webOrigins": WEB_WEB_ORIGINS,
        "standardFlowEnabled": True,
        "directAccessGrantsEnabled": True,
        "serviceAccountsEnabled": False,
        "publicClient": True,
        "bearerOnly": False,
        "frontchannelLogout": False,
        "attributes": {
            "post.logout.redirect.uris": "##".join(WEB_POST_LOGOUT_REDIRECT_URIS),
            "backchannel.logout.url": "",
            "backchannel.logout.session.required": "false",
            "backchannel.logout.revoke.offline.tokens": "false",
            "logout.confirmation.enabled": "false",
        },
    }


def update_client(token, realm, client_id, client_internal_id, body, client_type):
    print(f"  {client_type} client '{client_id}' already exists, updating", flush=True)
    requests.put(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/clients/{client_internal_id}",
        json=body,
        headers=auth_headers(token),
        timeout=10,
    ).raise_for_status()


def create_client(token, realm, client_id, body, client_type):
    r = requests.post(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/clients",
        json=body,
        headers=auth_headers(token),
        timeout=10,
    )
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Failed to create {client_type} client '{client_id}': {r.text}")
    cid = r.headers["Location"].split("/")[-1]
    print(f"  Created {client_type} client '{client_id}' (id: {cid})", flush=True)
    return cid


def get_client_secret(token, realm, client_internal_id):
    secret = requests.get(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/clients/{client_internal_id}/client-secret",
        headers=auth_headers(token),
        timeout=10,
    )
    secret.raise_for_status()
    return secret.json()["value"]


def ensure_client(token, realm, client_id):
    """Create or update a confidential client supporting client_credentials and password grants."""
    body = confidential_client_body(client_id)
    matches = find_client_matches(token, realm, client_id)
    if matches:
        cid = matches[0]["id"]
        print(
            f"  Client '{client_id}' already exists, updating to confidential client",
            flush=True,
        )
        update_client(token, realm, client_id, cid, body, "Confidential")
        print(
            f"  Updated client '{client_id}' to confidential (publicClient=False)",
            flush=True,
        )
    else:
        cid = create_client(token, realm, client_id, body, "confidential")
        secret_resp = requests.post(
            f"{KEYCLOAK_URL}/admin/realms/{realm}/clients/{cid}/client-secret",
            headers=auth_headers(token),
            timeout=10,
        )
        secret_resp.raise_for_status()
    return cid, get_client_secret(token, realm, cid)


def ensure_public_client(token, realm, client_id):
    """Create or update a public client (no client authentication, no authorization).
    Used by browser-based frontends for OIDC login via authorization code flow.
    """
    body = public_client_body(client_id)
    matches = find_client_matches(token, realm, client_id)
    if matches:
        cid = matches[0]["id"]
        update_client(token, realm, client_id, cid, body, "Public")
        print(
            f"  Updated client '{client_id}' to public (publicClient=True)", flush=True
        )
    else:
        cid = create_client(token, realm, client_id, body, "public")
    return cid


def ensure_cas_client(token, realm, client_id):
    """Create or update the default CAS client used by OMS SSO integration."""
    r = requests.get(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/clients",
        headers=auth_headers(token),
        params={"clientId": client_id},
        timeout=10,
    )
    r.raise_for_status()
    body = {
        "clientId": client_id,
        "name": "OMS CAS Client",
        "protocol": "cas",
        "enabled": True,
        "redirectUris": CAS_REDIRECT_URIS,
        "publicClient": True,
        "bearerOnly": False,
    }
    matches = r.json()
    if matches:
        cid = matches[0]["id"]
        print(f"  CAS client '{client_id}' already exists, updating", flush=True)
        requests.put(
            f"{KEYCLOAK_URL}/admin/realms/{realm}/clients/{cid}",
            json=body,
            headers=auth_headers(token),
            timeout=10,
        ).raise_for_status()
        print(
            f"  Updated CAS client '{client_id}' redirectUris={CAS_REDIRECT_URIS}",
            flush=True,
        )
    else:
        r = requests.post(
            f"{KEYCLOAK_URL}/admin/realms/{realm}/clients",
            json=body,
            headers=auth_headers(token),
            timeout=10,
        )
        if r.status_code not in (200, 201):
            raise RuntimeError(f"Failed to create CAS client '{client_id}': {r.text}")
        cid = r.headers["Location"].split("/")[-1]
        print(
            f"  Created CAS client '{client_id}' redirectUris={CAS_REDIRECT_URIS} (id: {cid})",
            flush=True,
        )
    return cid


def get_client_scope_by_name(token, realm, scope_name):
    r = requests.get(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/client-scopes",
        headers=auth_headers(token),
        timeout=10,
    )
    r.raise_for_status()
    for scope in r.json():
        if scope.get("name") == scope_name:
            return scope
    return None


def grant_realm_admin_to_service_account(token, realm, client_internal_id):
    """Give the client's service-account user the realm-admin role from realm-management."""
    sa = requests.get(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/clients/{client_internal_id}/service-account-user",
        headers=auth_headers(token),
        timeout=10,
    )
    sa.raise_for_status()
    sa_uid = sa.json()["id"]

    # find the realm-management client and its realm-admin role
    rm = requests.get(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/clients",
        headers=auth_headers(token),
        params={"clientId": "realm-management"},
        timeout=10,
    )
    rm.raise_for_status()
    if not rm.json():
        print(
            "  Warning: realm-management client not found; SA will lack admin rights",
            flush=True,
        )
        return sa_uid
    rm_id = rm.json()[0]["id"]

    role = requests.get(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/clients/{rm_id}/roles/realm-admin",
        headers=auth_headers(token),
        timeout=10,
    )
    role.raise_for_status()
    requests.post(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/users/{sa_uid}/role-mappings/clients/{rm_id}",
        json=[role.json()],
        headers=auth_headers(token),
        timeout=10,
    )
    print("  Granted realm-admin role to client service account", flush=True)
    return sa_uid


def configure_groups_mapper(token, realm, client_internal_id):
    """Install the structured-group-mapper SPI on the client; fall back to names-only."""
    headers = auth_headers(token)
    base = f"{KEYCLOAK_URL}/admin/realms/{realm}/clients/{client_internal_id}/protocol-mappers/models"
    existing = {}
    for mapper in requests.get(base, headers=headers, timeout=10).json():
        existing[mapper["name"]] = mapper

    # remove legacy names-only mapper if present (avoid duplicate `groups` claim)
    legacy = existing.get("groups-mapper")
    if legacy and legacy.get("protocolMapper") == "oidc-group-membership-mapper":
        requests.delete(f"{base}/{legacy['id']}", headers=headers, timeout=10)
        existing.pop("groups-mapper", None)

    if "groups-structured-mapper" in existing:
        print("  structured-group-mapper already configured", flush=True)
        return

    spi = {
        "name": "groups-structured-mapper",
        "protocol": "openid-connect",
        "protocolMapper": "structured-group-mapper",
        "config": {
            "id.token.claim": "true",
            "access.token.claim": "true",
            "userinfo.token.claim": "true",
            "groups.claim.name": "groups",
            "group.ids.claim.name": "group_ids",
        },
    }
    r = requests.post(base, json=spi, headers=headers, timeout=10)
    if r.status_code in (200, 201):
        print("  Created structured-group-mapper (groups + group_ids)", flush=True)
        return
    print(
        f"  Warning: SPI mapper failed ({r.status_code}); falling back to oidc-group-membership-mapper",
        flush=True,
    )
    requests.post(
        base,
        json={
            "name": "groups-mapper",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-group-membership-mapper",
            "config": {
                "full.path": "false",
                "id.token.claim": "true",
                "access.token.claim": "true",
                "userinfo.token.claim": "true",
                "claim.name": "groups",
            },
        },
        headers=headers,
        timeout=10,
    )


def cas_groups_mapper_target(token, realm, client_internal_id, client_id):
    dedicated_scope_name = f"{client_id}-dedicated"
    dedicated_scope = get_client_scope_by_name(token, realm, dedicated_scope_name)
    if dedicated_scope:
        return (
            f"{KEYCLOAK_URL}/admin/realms/{realm}/client-scopes/"
            f"{dedicated_scope['id']}/protocol-mappers/models",
            f"client scope '{dedicated_scope_name}'",
        )
    return (
        f"{KEYCLOAK_URL}/admin/realms/{realm}/clients/"
        f"{client_internal_id}/protocol-mappers/models",
        f"CAS client '{client_id}' dedicated mappers",
    )


def upsert_protocol_mapper(base, mapper_body, headers):
    r = requests.get(base, headers=headers, timeout=10)
    r.raise_for_status()
    existing = {mapper["name"]: mapper for mapper in r.json()}

    mapper = existing.get(mapper_body["name"])
    if mapper:
        updated_body = dict(mapper_body)
        updated_body["id"] = mapper["id"]
        r = requests.put(
            f"{base}/{mapper['id']}",
            json=updated_body,
            headers=headers,
            timeout=10,
        )
        r.raise_for_status()
        return "Updated"

    r = requests.post(base, json=mapper_body, headers=headers, timeout=10)
    if r.status_code not in (200, 201):
        raise RuntimeError(
            f"Failed to create protocol mapper {mapper_body['name']}: "
            f"{r.status_code} {r.text}"
        )
    return "Created"


def configure_cas_groups_mapper(token, realm, client_internal_id, client_id):
    """Expose Keycloak groups in the OMS CAS client response as `groups`."""
    headers = auth_headers(token)
    base, target = cas_groups_mapper_target(
        token, realm, client_internal_id, client_id
    )
    mapper_body = {
        "name": "groups-mapper",
        "protocol": "cas",
        "protocolMapper": "cas-group-membership-mapper",
        "config": {
            "full.path": "false",
            "claim.name": "groups",
        },
    }

    action = upsert_protocol_mapper(base, mapper_body, headers)
    print(
        f"  {action} CAS groups mapper in {target} (claim.name=groups)",
        flush=True,
    )


# ===================== Step 8: email / SMTP =====================


def configure_smtp(token, realm):
    """Configure SMTP email settings for the realm. Skipped if SMTP_HOST is empty."""
    if not SMTP_HOST:
        print(
            f"[Step 8/{TOTAL_STEPS}] SMTP_HOST not set, skipping email configuration",
            flush=True,
        )
        return
    print(
        f"[Step 8/{TOTAL_STEPS}] Configuring SMTP for realm '{realm}': {SMTP_HOST}:{SMTP_PORT}",
        flush=True,
    )
    smtp_config = {
        "host": SMTP_HOST,
        "port": str(SMTP_PORT),
        "from": SMTP_FROM,
        "fromDisplayName": SMTP_FROM_DISPLAY,
        "ssl": "true" if SMTP_SSL else "false",
        "starttls": "true" if SMTP_STARTTLS else "false",
        "auth": "true" if SMTP_USER else "false",
        "user": SMTP_USER,
        "password": SMTP_PASSWORD,
    }
    r = requests.put(
        f"{KEYCLOAK_URL}/admin/realms/{realm}",
        json={"smtpServer": smtp_config},
        headers=auth_headers(token),
        timeout=10,
    )
    if r.status_code not in (200, 204):
        print(f"  Warning: SMTP config returned {r.status_code}: {r.text}", flush=True)
    else:
        print(
            f"  SMTP configured (SSL={SMTP_SSL}, auth={'yes' if SMTP_USER else 'no'})",
            flush=True,
        )

    # Also apply to master realm so admin account recovery emails work
    r = requests.put(
        f"{KEYCLOAK_URL}/admin/realms/master",
        json={"smtpServer": smtp_config},
        headers=auth_headers(token),
        timeout=10,
    )
    if r.status_code not in (200, 204):
        print(
            f"  Warning: master realm SMTP config returned {r.status_code}", flush=True
        )
    else:
        print(f"  SMTP also applied to master realm", flush=True)


def set_master_admin_email(token):
    """Set email on the master realm admin user so password-reset emails work."""
    if not SMTP_FROM:
        return
    admin_email = os.getenv("MASTER_ADMIN_EMAIL", "")
    if not admin_email:
        print(
            f"  MASTER_ADMIN_EMAIL not set, skipping master admin email update",
            flush=True,
        )
        return
    r = requests.get(
        f"{KEYCLOAK_URL}/admin/realms/master/users",
        headers=auth_headers(token),
        params={"username": KC_ADMIN_USER, "exact": "true"},
        timeout=10,
    )
    r.raise_for_status()
    users = r.json()
    if not users:
        print(f"  Warning: master admin user '{KC_ADMIN_USER}' not found", flush=True)
        return
    uid = users[0]["id"]
    patch = requests.put(
        f"{KEYCLOAK_URL}/admin/realms/master/users/{uid}",
        json={"email": admin_email, "emailVerified": True},
        headers=auth_headers(token),
        timeout=10,
    )
    if patch.status_code not in (200, 204):
        print(
            f"  Warning: set master admin email returned {patch.status_code}",
            flush=True,
        )
    else:
        print(f"  Master admin email set to {admin_email}", flush=True)


def configure_password_policy(token, realm):
    """Apply password expiry policy to the realm. Skipped if PASSWORD_EXPIRE_DAYS is 0."""
    if PASSWORD_EXPIRE_DAYS <= 0:
        print(f"  PASSWORD_EXPIRE_DAYS=0, skipping password expiry policy", flush=True)
        return
    print(f"  Setting password expiry: {PASSWORD_EXPIRE_DAYS} days", flush=True)
    # Keycloak password policy string format: "forceExpiredPasswordChange(N)"
    policy_str = f"forceExpiredPasswordChange({PASSWORD_EXPIRE_DAYS})"
    r = requests.put(
        f"{KEYCLOAK_URL}/admin/realms/{realm}",
        json={"passwordPolicy": policy_str},
        headers=auth_headers(token),
        timeout=10,
    )
    if r.status_code not in (200, 204):
        print(
            f"  Warning: password policy returned {r.status_code}: {r.text}", flush=True
        )
    else:
        print(f"  Password policy applied: {policy_str}", flush=True)


# ===================== Main =====================
def setup_realm_and_profile(token):
    print(f"[Step 3/{TOTAL_STEPS}] Ensuring realm '{REALM}'", flush=True)
    ensure_realm(token, REALM)
    ensure_user_profile(token, REALM)


def setup_default_groups(token):
    print(
        f"[Step 4/{TOTAL_STEPS}] Setting up groups (master-admins, tenant-admins, all-users)",
        flush=True,
    )
    master_admins_group = ensure_group(token, REALM, "master-admins")
    tenant_admins_group = ensure_group(token, REALM, "tenant-admins")
    all_users_group = ensure_group(token, REALM, "all-users")
    if all_users_group:
        set_default_groups(token, REALM, [all_users_group["id"]])
    return master_admins_group, tenant_admins_group, all_users_group


def delete_builtin_admins_group(token):
    admins_group = get_group(token, REALM, "admins")
    if not admins_group:
        return
    r = requests.delete(
        f"{KEYCLOAK_URL}/admin/realms/{REALM}/groups/{admins_group['id']}",
        headers=auth_headers(token),
        timeout=10,
    )
    if r.status_code in (200, 204):
        print("  Deleted built-in 'admins' group", flush=True)
        return
    print(f"  Warning: could not delete 'admins' group: {r.status_code}", flush=True)


def setup_confidential_client(token, master_admins_group):
    print(f"[Step 5/{TOTAL_STEPS}] Setting up client '{CLIENT_ID}'", flush=True)
    cid, csecret = ensure_client(token, REALM, CLIENT_ID)
    sa_uid = grant_realm_admin_to_service_account(token, REALM, cid)
    if master_admins_group:
        add_user_to_group(token, REALM, sa_uid, master_admins_group["id"])
    if PROTECT_CLIENT_SECRET:
        client_secret_data = secret_store().protect(
            plain=csecret,
            plain_key="client-secret",
            key_id_key="client-secret-key-id",
            key_id=f"aidp-keycloak-{REALM}-{CLIENT_ID}-client-secret",
        )
    else:
        client_secret_data = {"client-secret": csecret}
    upsert_k8s_secret(
        K8S_SECRET_NAME,
        {
            "client-id": CLIENT_ID,
            "realm": REALM,
            "keycloak-url": KEYCLOAK_URL,
            **client_secret_data,
        },
        label_component="aidp-client",
    )
    return cid


def setup_public_and_cas_clients(token):
    print(
        f"[Step 5.5/{TOTAL_STEPS}] Setting up public client '{WEB_CLIENT_ID}'",
        flush=True,
    )
    web_cid = ensure_public_client(token, REALM, WEB_CLIENT_ID)

    # Step 5.6: CAS client for OMS SSO integration
    print(
        f"[Step 5.6/{TOTAL_STEPS}] Setting up CAS client '{CAS_CLIENT_ID}'",
        flush=True,
    )
    cas_cid = ensure_cas_client(token, REALM, CAS_CLIENT_ID)
    return web_cid, cas_cid


def setup_bootstrap_users(token, groups):
    master_admins_group, tenant_admins_group, all_users_group = groups
    print(f"[Step 6/{TOTAL_STEPS}] Creating users (admin, normal-user)", flush=True)
    admin_uid = ensure_user(token, REALM, ADMIN_USERNAME, ADMIN_INIT_PASSWORD)
    if master_admins_group:
        add_user_to_group(token, REALM, admin_uid, master_admins_group["id"])
    if tenant_admins_group:
        add_user_to_group(token, REALM, admin_uid, tenant_admins_group["id"])
    if all_users_group:
        add_user_to_group(token, REALM, admin_uid, all_users_group["id"])
    normal_uid = ensure_user(token, REALM, NORMAL_USERNAME, NORMAL_INIT_PASSWORD)
    if all_users_group:
        add_user_to_group(token, REALM, normal_uid, all_users_group["id"])


def configure_nickname_mapper(token, realm, client_internal_id):
    """Add a User Attribute mapper that puts attributes.nickname into the JWT as 'nickname'."""
    headers = auth_headers(token)
    base = f"{KEYCLOAK_URL}/admin/realms/{realm}/clients/{client_internal_id}/protocol-mappers/models"
    existing_names = {
        m["name"]
        for m in requests.get(base, headers=headers, timeout=10).json()
    }
    if "nickname-mapper" in existing_names:
        print("  nickname-mapper already configured", flush=True)
        return
    r = requests.post(
        base,
        json={
            "name": "nickname-mapper",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-usermodel-attribute-mapper",
            "config": {
                "user.attribute": "nickname",
                "claim.name": "nickname",
                "jsonType.label": "String",
                "id.token.claim": "true",
                "access.token.claim": "true",
                "userinfo.token.claim": "true",
                "multivalued": "false",
            },
        },
        headers=headers,
        timeout=10,
    )
    if r.status_code in (200, 201):
        print("  Created nickname-mapper (attributes.nickname -> JWT claim 'nickname')", flush=True)
    else:
        print(f"  Warning: nickname-mapper creation returned {r.status_code}: {r.text}", flush=True)


def configure_client_mappers(token, cid, web_cid, cas_cid):
    print(
        f"[Step 7/{TOTAL_STEPS}] Configuring client mappers (OIDC + CAS groups + nickname)",
        flush=True,
    )
    configure_groups_mapper(token, REALM, cid)
    configure_groups_mapper(token, REALM, web_cid)
    configure_cas_groups_mapper(token, REALM, cas_cid, CAS_CLIENT_ID)
    configure_nickname_mapper(token, REALM, cid)
    configure_nickname_mapper(token, REALM, web_cid)


def print_init_summary(cas_cid):
    admin_groups = "master-admins, tenant-admins, all-users"
    normal_groups = "all-users"
    admin_summary = (
        f"  Admin user:        {ADMIN_USERNAME} (groups: {admin_groups})"
    )
    normal_summary = (
        f"  Normal user:       {NORMAL_USERNAME} (groups: {normal_groups})"
    )

    print("\n" + "=" * 60, flush=True)
    print("Single-tenant init complete (realm: aidp)", flush=True)
    print(f"  Realm: {REALM}", flush=True)
    print(admin_summary, flush=True)
    print(normal_summary, flush=True)
    print("  Groups:            master-admins, tenant-admins, all-users", flush=True)
    print(
        f"  Client: {CLIENT_ID} (K8s Secret: {K8S_NAMESPACE}/{K8S_SECRET_NAME})",
        flush=True,
    )
    print(f"  Public client: {WEB_CLIENT_ID} (browser OIDC login)", flush=True)
    print(f"  CAS client: {CAS_CLIENT_ID} (id: {cas_cid}, OMS CAS SSO)", flush=True)
    print("  JWT claims: groups + group_ids", flush=True)
    print("  CAS claims: groups", flush=True)
    print("=" * 60 + "\n", flush=True)


def main():
    wait_for_keycloak()
    token = get_admin_token()

    setup_realm_and_profile(token)
    groups = setup_default_groups(token)
    delete_builtin_admins_group(token)
    cid = setup_confidential_client(token, groups[0])
    web_cid, cas_cid = setup_public_and_cas_clients(token)
    setup_bootstrap_users(token, groups)
    configure_client_mappers(token, cid, web_cid, cas_cid)
    configure_smtp(token, REALM)
    set_master_admin_email(token)
    configure_password_policy(token, REALM)
    print(
        "IAM database schema and default seeds are initialized by the Postgres init script.",
        flush=True,
    )
    print_init_summary(cas_cid)
    return 0


if __name__ == "__main__":
    try:
        exit(main())
    except Exception as e:
        import traceback

        print(f"Init failed: {e}", flush=True)
        traceback.print_exc()
        exit(1)
