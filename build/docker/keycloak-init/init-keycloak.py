#!/usr/bin/env python3
"""
Keycloak Init Job — single-tenant model (per diagrams/ui-wireframes.md).

Provisions a single `aidp` realm with:
  - groups: `master-admins` (IAM admin), `tenant-admins` (tenant admin), `all-users` (default group)
  - users: `admin` (in master-admins + all-users), `normal-user` (in all-users)
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
KC_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

REALM = os.getenv("AIDP_REALM", "aidp")
CLIENT_ID = os.getenv("AIDP_CLIENT_ID", "aidp-client")
WEB_CLIENT_ID = os.getenv("AIDP_WEB_CLIENT_ID", "aidp-web")
LOGIN_THEME = os.getenv("AIDP_LOGIN_THEME", "password-reset-confirm").strip()
WEB_REDIRECT_URIS = csv_env("AIDP_WEB_REDIRECT_URIS", ["/*"])
WEB_WEB_ORIGINS = csv_env("AIDP_WEB_WEB_ORIGINS", ["+"])

ADMIN_USERNAME = os.getenv("AIDP_ADMIN_USER", "admin")
ADMIN_INIT_PASSWORD = os.getenv("AIDP_ADMIN_PASSWORD", "Admin@123")
NORMAL_USERNAME = os.getenv("AIDP_NORMAL_USER", "normal-user")
NORMAL_INIT_PASSWORD = os.getenv("AIDP_NORMAL_PASSWORD", "NormalUser@123")

K8S_SECRET_NAME = os.getenv("K8S_SECRET_NAME", "keycloak-aidp-client")
K8S_NAMESPACE = os.getenv("K8S_NAMESPACE", "keycloak")
RELEASE_REVISION = os.getenv("AIDP_RELEASE_REVISION", "")


# Email / SMTP settings (all optional; skip email config if SMTP_HOST is empty)
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_FROM = os.getenv("SMTP_FROM", "")
SMTP_FROM_DISPLAY = os.getenv("SMTP_FROM_DISPLAY", "AIDP IAM")
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_SSL = os.getenv("SMTP_SSL", "true").lower() == "true"
SMTP_STARTTLS = os.getenv("SMTP_STARTTLS", "false").lower() == "true"

# Password expiry policy (days; 0 = disabled)
PASSWORD_EXPIRE_DAYS = int(os.getenv("PASSWORD_EXPIRE_DAYS", "0"))

TOTAL_STEPS = 9


# ===================== utilities =====================
def wait_for_keycloak():
    health_url = f"{KEYCLOAK_HEALTH_URL}/health/ready"
    print(f"[Step 1/{TOTAL_STEPS}] Waiting for Keycloak: {health_url}", flush=True)
    for i in range(50):
        try:
            r = requests.get(health_url, timeout=5)
            if r.status_code == 200:
                print(f"[Step 1/{TOTAL_STEPS}] Keycloak is ready", flush=True)
                return
        except Exception:
            pass
        time.sleep(5)
    raise RuntimeError("Keycloak not ready")


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


def H(token):
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
        f"{KEYCLOAK_URL}/admin/realms/{realm}", headers=H(token), timeout=10
    )
    login_settings = {
        "registrationAllowed": False,
        "loginWithEmailAllowed": False,
        "duplicateEmailsAllowed": False,
        "resetPasswordAllowed": True,
        "rememberMe": False,
        "verifyEmail": True,
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
            headers=H(token),
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
        f"{KEYCLOAK_URL}/admin/realms", json=body, headers=H(token), timeout=10
    )
    if r.status_code not in (200, 201):
        raise RuntimeError(
            f"Failed to create realm '{realm}': {r.status_code} {r.text}"
        )
    print(f"  Created realm '{realm}'", flush=True)


def get_group(token, realm, name):
    r = requests.get(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/groups",
        headers=H(token),
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
        headers=H(token),
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
            headers=H(token),
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
        headers=H(token),
        params={"username": username, "exact": "true"},
        timeout=10,
    )
    r.raise_for_status()
    users = r.json()
    return users[0] if users else None


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
    config = {
        "attributes": [
            {
                "name": "username",
                "displayName": "${username}",
                "validations": {
                    "length": {"min": 3, "max": 255},
                    "username-prohibited-characters": {},
                    "up-username-not-idn-homograph": {},
                },
                "permissions": {
                    "view": ["admin", "user"],
                    "edit": ["admin", "user"],
                },
                "multivalued": False,
            },
            # Built-ins kept but NOT required (Keycloak 26 disallows removal)
            {
                "name": "email",
                "displayName": "${email}",
                "validations": {
                    "email": {},
                    "length": {"max": 255},
                },
                "permissions": {
                    "view": ["admin", "user"],
                    "edit": ["admin", "user"],
                },
                "multivalued": False,
            },
            {
                "name": "firstName",
                "displayName": "${firstName}",
                "validations": {
                    "length": {"max": 255},
                    "person-name-prohibited-characters": {},
                },
                "permissions": {
                    "view": ["admin", "user"],
                    "edit": ["admin", "user"],
                },
                "multivalued": False,
            },
            {
                "name": "lastName",
                "displayName": "${lastName}",
                "validations": {
                    "length": {"max": 255},
                    "person-name-prohibited-characters": {},
                },
                "permissions": {
                    "view": ["admin", "user"],
                    "edit": ["admin", "user"],
                },
                "multivalued": False,
            },
            {
                "name": "nickname",
                "displayName": "${profile.nickname}",
                "validations": {
                    "length": {"max": 255},
                },
                "permissions": {
                    "view": ["admin", "user"],
                    "edit": ["admin", "user"],
                },
                "multivalued": False,
            },
        ],
        "unmanagedAttributePolicy": "ADMIN_EDIT",
    }
    r = requests.put(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/users/profile",
        json=config,
        headers=H(token),
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
        # Ensure emailVerified=true so verifyEmail realm setting doesn't block login
        requests.put(
            f"{KEYCLOAK_URL}/admin/realms/{realm}/users/{uid}",
            json={"emailVerified": True},
            headers=H(token),
            timeout=10,
        )
    else:
        r = requests.post(
            f"{KEYCLOAK_URL}/admin/realms/{realm}/users",
            json={"username": username, "enabled": True, "emailVerified": True},
            headers=H(token),
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
        headers=H(token),
        timeout=10,
    ).raise_for_status()
    return uid


def add_user_to_group(token, realm, user_id, group_id):
    r = requests.put(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/users/{user_id}/groups/{group_id}",
        headers=H(token),
        timeout=10,
    )
    if r.status_code not in (200, 204):
        print(
            f"  Warning: add user-to-group returned {r.status_code}: {r.text}",
            flush=True,
        )


def ensure_client(token, realm, client_id):
    """Create or update a confidential client supporting both client_credentials and password grants."""
    r = requests.get(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/clients",
        headers=H(token),
        params={"clientId": client_id},
        timeout=10,
    )
    r.raise_for_status()
    body = {
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
    if r.json():
        cid = r.json()[0]["id"]
        print(
            f"  Client '{client_id}' already exists, updating to confidential client",
            flush=True,
        )
        requests.put(
            f"{KEYCLOAK_URL}/admin/realms/{realm}/clients/{cid}",
            json=body,
            headers=H(token),
            timeout=10,
        ).raise_for_status()
        print(
            f"  Updated client '{client_id}' to confidential (publicClient=False)",
            flush=True,
        )
    else:
        r = requests.post(
            f"{KEYCLOAK_URL}/admin/realms/{realm}/clients",
            json=body,
            headers=H(token),
            timeout=10,
        )
        if r.status_code not in (200, 201):
            raise RuntimeError(f"Failed to create client '{client_id}': {r.text}")
        cid = r.headers["Location"].split("/")[-1]
        print(f"  Created confidential client '{client_id}' (id: {cid})", flush=True)
        requests.post(
            f"{KEYCLOAK_URL}/admin/realms/{realm}/clients/{cid}/client-secret",
            headers=H(token),
            timeout=10,
        )
    sec = requests.get(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/clients/{cid}/client-secret",
        headers=H(token),
        timeout=10,
    )
    sec.raise_for_status()
    return cid, sec.json()["value"]


def ensure_public_client(token, realm, client_id):
    """Create or update a public client (no client authentication, no authorization).
    Used by browser-based frontends for OIDC login via authorization code flow.
    """
    r = requests.get(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/clients",
        headers=H(token),
        params={"clientId": client_id},
        timeout=10,
    )
    r.raise_for_status()
    body = {
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
    }
    if r.json():
        cid = r.json()[0]["id"]
        print(f"  Public client '{client_id}' already exists, updating", flush=True)
        requests.put(
            f"{KEYCLOAK_URL}/admin/realms/{realm}/clients/{cid}",
            json=body,
            headers=H(token),
            timeout=10,
        ).raise_for_status()
        print(
            f"  Updated client '{client_id}' to public (publicClient=True)", flush=True
        )
    else:
        r = requests.post(
            f"{KEYCLOAK_URL}/admin/realms/{realm}/clients",
            json=body,
            headers=H(token),
            timeout=10,
        )
        if r.status_code not in (200, 201):
            raise RuntimeError(
                f"Failed to create public client '{client_id}': {r.text}"
            )
        cid = r.headers["Location"].split("/")[-1]
        print(f"  Created public client '{client_id}' (id: {cid})", flush=True)
    return cid


def grant_realm_admin_to_service_account(token, realm, client_internal_id):
    """Give the client's service-account user the realm-admin role from realm-management."""
    sa = requests.get(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/clients/{client_internal_id}/service-account-user",
        headers=H(token),
        timeout=10,
    )
    sa.raise_for_status()
    sa_uid = sa.json()["id"]

    # find the realm-management client and its realm-admin role
    rm = requests.get(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/clients",
        headers=H(token),
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
        headers=H(token),
        timeout=10,
    )
    role.raise_for_status()
    requests.post(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/users/{sa_uid}/role-mappings/clients/{rm_id}",
        json=[role.json()],
        headers=H(token),
        timeout=10,
    )
    print("  Granted realm-admin role to client service account", flush=True)
    return sa_uid


def configure_groups_mapper(token, realm, client_internal_id):
    """Install the structured-group-mapper SPI on the client; fall back to names-only."""
    headers = H(token)
    base = f"{KEYCLOAK_URL}/admin/realms/{realm}/clients/{client_internal_id}/protocol-mappers/models"
    existing = {
        m["name"]: m for m in requests.get(base, headers=headers, timeout=10).json()
    }

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
        headers=H(token),
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
        headers=H(token),
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
        headers=H(token),
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
        headers=H(token),
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
        headers=H(token),
        timeout=10,
    )
    if r.status_code not in (200, 204):
        print(
            f"  Warning: password policy returned {r.status_code}: {r.text}", flush=True
        )
    else:
        print(f"  Password policy applied: {policy_str}", flush=True)


# ===================== Main =====================
def main():
    wait_for_keycloak()
    token = get_admin_token()

    # Step 3: realm + user-profile
    print(f"[Step 3/{TOTAL_STEPS}] Ensuring realm '{REALM}'", flush=True)
    ensure_realm(token, REALM)
    ensure_user_profile(token, REALM)

    # Step 4: groups + default group
    print(
        f"[Step 4/{TOTAL_STEPS}] Setting up groups (master-admins, tenant-admins, all-users)",
        flush=True,
    )
    master_admins_group = ensure_group(token, REALM, "master-admins")
    tenant_admins_group = ensure_group(token, REALM, "tenant-admins")
    all_users_group = ensure_group(token, REALM, "all-users")
    if all_users_group:
        set_default_groups(token, REALM, [all_users_group["id"]])

    # Delete the Keycloak built-in 'admins' group if it exists
    admins_group = get_group(token, REALM, "admins")
    if admins_group:
        r = requests.delete(
            f"{KEYCLOAK_URL}/admin/realms/{REALM}/groups/{admins_group['id']}",
            headers=H(token),
            timeout=10,
        )
        if r.status_code in (200, 204):
            print("  Deleted built-in 'admins' group", flush=True)
        else:
            print(
                f"  Warning: could not delete 'admins' group: {r.status_code}",
                flush=True,
            )

    # Step 5: client + service-account into master-admins + realm-admin
    print(f"[Step 5/{TOTAL_STEPS}] Setting up client '{CLIENT_ID}'", flush=True)
    cid, csecret = ensure_client(token, REALM, CLIENT_ID)
    sa_uid = grant_realm_admin_to_service_account(token, REALM, cid)
    if master_admins_group:
        add_user_to_group(token, REALM, sa_uid, master_admins_group["id"])
    upsert_k8s_secret(
        K8S_SECRET_NAME,
        {
            "client-id": CLIENT_ID,
            "client-secret": csecret,
            "realm": REALM,
            "keycloak-url": KEYCLOAK_URL,
        },
        label_component="aidp-client",
    )

    # Step 5.5: public client for browser frontends
    print(
        f"[Step 5.5/{TOTAL_STEPS}] Setting up public client '{WEB_CLIENT_ID}'",
        flush=True,
    )
    web_cid = ensure_public_client(token, REALM, WEB_CLIENT_ID)

    # Step 6: users
    print(f"[Step 6/{TOTAL_STEPS}] Creating users (admin, normal-user)", flush=True)
    admin_uid = ensure_user(token, REALM, ADMIN_USERNAME, ADMIN_INIT_PASSWORD)
    if master_admins_group:
        add_user_to_group(token, REALM, admin_uid, master_admins_group["id"])
    if all_users_group:
        add_user_to_group(token, REALM, admin_uid, all_users_group["id"])
    normal_uid = ensure_user(token, REALM, NORMAL_USERNAME, NORMAL_INIT_PASSWORD)
    if all_users_group:
        add_user_to_group(token, REALM, normal_uid, all_users_group["id"])

    # Step 7: JWT mappers
    print(
        f"[Step 7/{TOTAL_STEPS}] Configuring JWT mappers (groups + group_ids)",
        flush=True,
    )
    configure_groups_mapper(token, REALM, cid)
    configure_groups_mapper(token, REALM, web_cid)

    # Step 8: SMTP + password policy + master admin email
    configure_smtp(token, REALM)
    set_master_admin_email(token)
    configure_password_policy(token, REALM)
    print(
        "IAM database schema and default seeds are initialized by the Postgres init script.",
        flush=True,
    )

    print("\n" + "=" * 60, flush=True)
    print("Single-tenant init complete (realm: aidp)", flush=True)
    print(f"  Realm: {REALM}", flush=True)
    print(
        f"  Admin user:        {ADMIN_USERNAME} / {ADMIN_INIT_PASSWORD}  (groups: master-admins, all-users)",
        flush=True,
    )
    print(
        f"  Normal user:       {NORMAL_USERNAME} / {NORMAL_INIT_PASSWORD}  (groups: all-users)",
        flush=True,
    )
    print(f"  Groups:            master-admins, tenant-admins, all-users", flush=True)
    print(
        f"  Client: {CLIENT_ID} (K8s Secret: {K8S_NAMESPACE}/{K8S_SECRET_NAME})",
        flush=True,
    )
    print(f"  Public client: {WEB_CLIENT_ID} (browser OIDC login)", flush=True)
    print(f"  JWT claims: groups + group_ids", flush=True)
    print("=" * 60 + "\n", flush=True)
    return 0


if __name__ == "__main__":
    try:
        exit(main())
    except Exception as e:
        import traceback

        print(f"Init failed: {e}", flush=True)
        traceback.print_exc()
        exit(1)
