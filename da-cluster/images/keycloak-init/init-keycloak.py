#!/usr/bin/env python3
"""
Keycloak Init Job — single-tenant model (per diagrams/ui-wireframes.md).

Provisions a single `aidp` realm with:
  - groups: `admins` (everything-admin), `all-users` (default group)
  - users: `admin` (in admins + all-users), `normal-user` (in all-users)
  - confidential client: `aidp-client`
      * serviceAccountsEnabled  → backend-to-backend client_credentials
      * directAccessGrants      → user password grant for tests
      * service account is in `admins` (full bypass via OPA)
      * realm-management / realm-admin role for Keycloak admin-API calls
  - JWT mappers (groups + group_ids) via the structured-group-mapper SPI
  - seeds iam DB with default apps + resource_patterns

The Keycloak built-in `master` realm is left untouched (Keycloak operations only).
"""
import os
import time
import base64
import requests
from kubernetes import client, config
from kubernetes.client.rest import ApiException

# ===================== Configuration =====================
KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://keycloak:8080")
KEYCLOAK_HEALTH_URL = os.getenv("KEYCLOAK_HEALTH_URL", "http://keycloak:9000")
KC_ADMIN_USER = os.getenv("ADMIN_USER", "admin")
KC_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

REALM = os.getenv("AIDP_REALM", "aidp")
CLIENT_ID = os.getenv("AIDP_CLIENT_ID", "aidp-client")

ADMIN_USERNAME = os.getenv("AIDP_ADMIN_USER", "admin")
ADMIN_INIT_PASSWORD = os.getenv("AIDP_ADMIN_PASSWORD", "Admin@123")
NORMAL_USERNAME = os.getenv("AIDP_NORMAL_USER", "normal-user")
NORMAL_INIT_PASSWORD = os.getenv("AIDP_NORMAL_PASSWORD", "NormalUser@123")

K8S_SECRET_NAME = os.getenv("K8S_SECRET_NAME", "keycloak-aidp-client")
K8S_NAMESPACE = os.getenv("K8S_NAMESPACE", "keycloak")
RELEASE_REVISION = os.getenv("AIDP_RELEASE_REVISION", "")

IAM_DB_URL = os.getenv("IAM_DB_URL", "postgresql://keycloak:keycloak@postgres:5432/iam")

TOTAL_STEPS = 8


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
        data={"username": KC_ADMIN_USER, "password": KC_ADMIN_PASSWORD,
              "grant_type": "password", "client_id": "admin-cli"},
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
                api_version="v1", kind="Secret",
                metadata=client.V1ObjectMeta(
                    name=name, namespace=K8S_NAMESPACE,
                    labels={"app": "keycloak", "component": label_component},
                    annotations=annotations,
                ),
                type="Opaque", data=enc,
            ),
        )
        print(f"  Created K8s Secret: {name}", flush=True)


# ===================== Keycloak helpers =====================
def ensure_realm(token, realm):
    r = requests.get(f"{KEYCLOAK_URL}/admin/realms/{realm}", headers=H(token), timeout=10)
    if r.status_code == 200:
        print(f"  Realm '{realm}' already exists", flush=True)
        return
    body = {
        "realm": realm,
        "displayName": "AIDP IAM",
        "enabled": True,
        "registrationAllowed": False,
        "loginWithEmailAllowed": True,
        "duplicateEmailsAllowed": False,
        "resetPasswordAllowed": True,
        "editUsernameAllowed": False,
        "bruteForceProtected": True,
    }
    r = requests.post(f"{KEYCLOAK_URL}/admin/realms", json=body, headers=H(token), timeout=10)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Failed to create realm '{realm}': {r.status_code} {r.text}")
    print(f"  Created realm '{realm}'", flush=True)


def get_group(token, realm, name):
    r = requests.get(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/groups",
        headers=H(token), params={"search": name, "exact": "true"}, timeout=10,
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
        json={"name": name}, headers=H(token), timeout=10,
    )
    if r.status_code not in (200, 201, 409):
        raise RuntimeError(f"Failed to create group '{name}': {r.text}")
    print(f"  Created group '{name}'", flush=True)
    return get_group(token, realm, name)


def set_default_groups(token, realm, group_ids):
    for gid in group_ids:
        r = requests.put(
            f"{KEYCLOAK_URL}/admin/realms/{realm}/default-groups/{gid}",
            headers=H(token), timeout=10,
        )
        if r.status_code not in (200, 204):
            print(f"  Warning: set default group {gid} returned {r.status_code}", flush=True)


def find_user(token, realm, username):
    r = requests.get(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/users",
        headers=H(token), params={"username": username, "exact": "true"}, timeout=10,
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
        ],
        "unmanagedAttributePolicy": "ADMIN_EDIT",
    }
    r = requests.put(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/users/profile",
        json=config, headers=H(token), timeout=10,
    )
    if r.status_code not in (200, 204):
        raise RuntimeError(f"Failed to set user-profile for '{realm}': {r.status_code} {r.text}")
    print(f"  User profile applied (realm '{realm}'): only username required; "
          f"email/firstName/lastName kept optional", flush=True)


def ensure_user(token, realm, username, password):
    existing = find_user(token, realm, username)
    if existing:
        uid = existing["id"]
        print(f"  User '{username}' already exists", flush=True)
    else:
        r = requests.post(
            f"{KEYCLOAK_URL}/admin/realms/{realm}/users",
            json={"username": username, "enabled": True},
            headers=H(token), timeout=10,
        )
        if r.status_code not in (200, 201):
            raise RuntimeError(f"Failed to create user '{username}': {r.text}")
        uid = r.headers["Location"].split("/")[-1]
        print(f"  Created user '{username}' (id: {uid})", flush=True)
    # set/refresh password (temporary=False so password-grant works immediately)
    requests.put(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/users/{uid}/reset-password",
        json={"type": "password", "value": password, "temporary": False},
        headers=H(token), timeout=10,
    ).raise_for_status()
    return uid


def add_user_to_group(token, realm, user_id, group_id):
    r = requests.put(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/users/{user_id}/groups/{group_id}",
        headers=H(token), timeout=10,
    )
    if r.status_code not in (200, 204):
        print(f"  Warning: add user-to-group returned {r.status_code}: {r.text}", flush=True)


def ensure_client(token, realm, client_id):
    """Create a confidential client supporting both client_credentials and password grants."""
    r = requests.get(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/clients",
        headers=H(token), params={"clientId": client_id}, timeout=10,
    )
    r.raise_for_status()
    if r.json():
        cid = r.json()[0]["id"]
        print(f"  Client '{client_id}' already exists", flush=True)
    else:
        body = {
            "clientId": client_id,
            "name": f"{client_id} (auto-created)",
            "enabled": True,
            "clientAuthenticatorType": "client-secret",
            "redirectUris": ["*"], "webOrigins": ["*"],
            "serviceAccountsEnabled": True,
            "directAccessGrantsEnabled": True,
            "standardFlowEnabled": True,
            "publicClient": False,
            "bearerOnly": False,
        }
        r = requests.post(
            f"{KEYCLOAK_URL}/admin/realms/{realm}/clients",
            json=body, headers=H(token), timeout=10,
        )
        if r.status_code not in (200, 201):
            raise RuntimeError(f"Failed to create client '{client_id}': {r.text}")
        cid = r.headers["Location"].split("/")[-1]
        print(f"  Created client '{client_id}' (id: {cid})", flush=True)
        # generate secret
        requests.post(
            f"{KEYCLOAK_URL}/admin/realms/{realm}/clients/{cid}/client-secret",
            headers=H(token), timeout=10,
        )
    sec = requests.get(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/clients/{cid}/client-secret",
        headers=H(token), timeout=10,
    )
    sec.raise_for_status()
    return cid, sec.json()["value"]


def grant_realm_admin_to_service_account(token, realm, client_internal_id):
    """Give the client's service-account user the realm-admin role from realm-management."""
    sa = requests.get(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/clients/{client_internal_id}/service-account-user",
        headers=H(token), timeout=10,
    )
    sa.raise_for_status()
    sa_uid = sa.json()["id"]

    # find the realm-management client and its realm-admin role
    rm = requests.get(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/clients",
        headers=H(token), params={"clientId": "realm-management"}, timeout=10,
    )
    rm.raise_for_status()
    if not rm.json():
        print("  Warning: realm-management client not found; SA will lack admin rights", flush=True)
        return sa_uid
    rm_id = rm.json()[0]["id"]

    role = requests.get(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/clients/{rm_id}/roles/realm-admin",
        headers=H(token), timeout=10,
    )
    role.raise_for_status()
    requests.post(
        f"{KEYCLOAK_URL}/admin/realms/{realm}/users/{sa_uid}/role-mappings/clients/{rm_id}",
        json=[role.json()], headers=H(token), timeout=10,
    )
    print("  Granted realm-admin role to client service account", flush=True)
    return sa_uid


def configure_groups_mapper(token, realm, client_internal_id):
    """Install the structured-group-mapper SPI on the client; fall back to names-only."""
    headers = H(token)
    base = f"{KEYCLOAK_URL}/admin/realms/{realm}/clients/{client_internal_id}/protocol-mappers/models"
    existing = {m["name"]: m for m in requests.get(base, headers=headers, timeout=10).json()}

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
    print(f"  Warning: SPI mapper failed ({r.status_code}); falling back to oidc-group-membership-mapper", flush=True)
    requests.post(base, json={
        "name": "groups-mapper", "protocol": "openid-connect",
        "protocolMapper": "oidc-group-membership-mapper",
        "config": {"full.path": "false", "id.token.claim": "true",
                   "access.token.claim": "true", "userinfo.token.claim": "true",
                   "claim.name": "groups"},
    }, headers=headers, timeout=10)


# ===================== Step 8: seed iam DB =====================
def seed_iam_db():
    print(f"[Step 8/{TOTAL_STEPS}] Seeding IAM database", flush=True)
    try:
        import psycopg2
    except ImportError:
        print(f"  psycopg2 unavailable, skipping seeding", flush=True)
        return

    conn = None
    for i in range(30):
        try:
            conn = psycopg2.connect(IAM_DB_URL)
            break
        except Exception as e:
            print(f"  IAM DB not ready, retry {i+1}/30: {e}", flush=True)
            time.sleep(3)
    if not conn:
        print(f"  WARNING: could not reach IAM DB", flush=True)
        return

    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS apps (
                app_name VARCHAR(128) PRIMARY KEY,
                path_prefix VARCHAR(256) NOT NULL UNIQUE,
                display_name VARCHAR(256), description VARCHAR(512),
                admin_group VARCHAR(128),
                enabled BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW())
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS resource_patterns (
                app_name                         VARCHAR(128) NOT NULL REFERENCES apps(app_name),
                resource_prefix                  VARCHAR(256) NOT NULL,
                method                           VARCHAR(10)  NOT NULL DEFAULT '',
                resource_type                    VARCHAR(128) NOT NULL,
                id_source                        VARCHAR(16)  NOT NULL DEFAULT 'path',
                id_field                         VARCHAR(128) NOT NULL DEFAULT 'id',
                id_query_param                   VARCHAR(128) DEFAULT NULL,
                share_to_admin_group_on_create   BOOLEAN      NOT NULL DEFAULT false,
                share_to_all_users_on_create     BOOLEAN      NOT NULL DEFAULT false,
                PRIMARY KEY (app_name, resource_prefix, method))
        """)
        # permission_groups 三张表（业务功能点 → 路径集 → Keycloak 组）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS permission_groups (
                id          SERIAL PRIMARY KEY,
                app_name    VARCHAR(128) NOT NULL DEFAULT '',
                name        VARCHAR(128) NOT NULL,
                description VARCHAR(512),
                created_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
                UNIQUE (app_name, name))
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS permission_group_paths (
                id          SERIAL PRIMARY KEY,
                group_id    INTEGER NOT NULL REFERENCES permission_groups(id) ON DELETE CASCADE,
                path_prefix VARCHAR(256) NOT NULL,
                method      VARCHAR(10),
                UNIQUE (group_id, path_prefix, method))
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS permission_group_bindings (
                group_id      INTEGER      NOT NULL REFERENCES permission_groups(id) ON DELETE CASCADE,
                kc_group_name VARCHAR(128) NOT NULL,
                PRIMARY KEY (group_id, kc_group_name))
        """)

        # ---- Apps ----
        # path_prefix is used by OPA app_disabled check (startswith match).
        # Manifest-registered apps (KnowledgeBase, DataAgent, MemoryStore) use
        # their manifest namespace as path_prefix so OPA can disable them by name.
        cur.execute("""
            INSERT INTO apps (app_name, path_prefix, display_name, description, admin_group, enabled) VALUES
                ('KnowledgeBase', '/KnowledgeBase/', 'Knowledge Base',  'Knowledge base management (manifest-registered)', NULL, true),
                ('DataAgent',     '/DataAgent/',     'Data Agent',      'NL2SQL data query platform (manifest-registered)', NULL, true),
                ('MemoryStore',   '/MemoryStore/',   'Memory Store',    'Memory service (manifest-registered)',              NULL, true)
            ON CONFLICT (app_name) DO UPDATE SET
                path_prefix  = EXCLUDED.path_prefix,
                display_name = EXCLUDED.display_name,
                description  = EXCLUDED.description
        """)

        # resource_patterns and resource_actions for manifest-registered apps
        # (KnowledgeBase, DataAgent, MemoryStore) are no longer seeded here.
        # They are derived from app_manifests at runtime by bundle-server and
        # resource-sync. Only the table structures are created above.

        # ---- Permission Groups seed ----
        # Only system-level groups are seeded here. Application path rules are
        # derived from app_manifests at runtime by bundle-server.
        #
        # Groups model: master-admins (cross-tenant super-admin),
        #               tenant-admins (per-tenant admin, created by tenants.py),
        #               all-users (default group for every logged-in user).
        # "admins" is the Keycloak system group, treated as equivalent to master-admins.
        system_perm_groups = [
            ('', 'iam_admin',  'IAM management API (master-admins only)',
                [('/api/v1/', None)], ['admins', 'master-admins']),
            ('', 'acl_access', 'ACL share API (all logged-in users; endpoint enforces owner-only)',
                [('/acl/v1/', None)], ['admins', 'master-admins', 'tenant-admins', 'all-users']),
            ('', 'access_manager', 'AccessManager API (master-admins + tenant-admins)',
                [('/AccessManager/', None)], ['admins', 'master-admins', 'tenant-admins']),
        ]

        all_perm_groups = system_perm_groups

        for app_name, name, desc, paths, kc_groups in all_perm_groups:
            cur.execute(
                "INSERT INTO permission_groups (app_name, name, description) VALUES (%s, %s, %s) "
                "ON CONFLICT (app_name, name) DO UPDATE SET description = EXCLUDED.description "
                "RETURNING id",
                (app_name, name, desc))
            row = cur.fetchone()
            if not row:
                cur.execute(
                    "SELECT id FROM permission_groups WHERE app_name = %s AND name = %s",
                    (app_name, name))
                row = cur.fetchone()
            gid = row[0]

            for path, method in paths:
                cur.execute(
                    "INSERT INTO permission_group_paths (group_id, path_prefix, method) "
                    "VALUES (%s, %s, %s) "
                    "ON CONFLICT (group_id, path_prefix, method) DO NOTHING",
                    (gid, path, method))

            for g in kc_groups:
                cur.execute(
                    "INSERT INTO permission_group_bindings (group_id, kc_group_name) "
                    "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (gid, g))

        cur.close()
        print(f"  IAM DB seeded ({len(system_perm_groups)} system permission_groups, "
              f"app path_rules derived from app_manifests at runtime)",
              flush=True)
    finally:
        conn.close()


# ===================== Main =====================
def main():
    wait_for_keycloak()
    token = get_admin_token()

    # Step 3: realm + user-profile
    print(f"[Step 3/{TOTAL_STEPS}] Ensuring realm '{REALM}'", flush=True)
    ensure_realm(token, REALM)
    ensure_user_profile(token, REALM)

    # Step 4: groups + default group
    print(f"[Step 4/{TOTAL_STEPS}] Setting up groups (master-admins, admins, all-users)", flush=True)
    master_admins_group = ensure_group(token, REALM, "master-admins")
    admins_group = ensure_group(token, REALM, "admins")
    all_users_group = ensure_group(token, REALM, "all-users")
    if all_users_group:
        set_default_groups(token, REALM, [all_users_group["id"]])

    # Step 5: client + service-account into admins + realm-admin
    print(f"[Step 5/{TOTAL_STEPS}] Setting up client '{CLIENT_ID}'", flush=True)
    cid, csecret = ensure_client(token, REALM, CLIENT_ID)
    sa_uid = grant_realm_admin_to_service_account(token, REALM, cid)
    if admins_group:
        add_user_to_group(token, REALM, sa_uid, admins_group["id"])
    upsert_k8s_secret(K8S_SECRET_NAME, {
        "client-id": CLIENT_ID, "client-secret": csecret,
        "realm": REALM, "keycloak-url": KEYCLOAK_URL,
    }, label_component="aidp-client")

    # Step 6: users
    print(f"[Step 6/{TOTAL_STEPS}] Creating users (admin, normal-user)", flush=True)
    admin_uid = ensure_user(token, REALM, ADMIN_USERNAME, ADMIN_INIT_PASSWORD)
    if admins_group:
        add_user_to_group(token, REALM, admin_uid, admins_group["id"])
    if master_admins_group:
        add_user_to_group(token, REALM, admin_uid, master_admins_group["id"])
    if all_users_group:
        add_user_to_group(token, REALM, admin_uid, all_users_group["id"])
    normal_uid = ensure_user(token, REALM, NORMAL_USERNAME, NORMAL_INIT_PASSWORD)
    if all_users_group:
        add_user_to_group(token, REALM, normal_uid, all_users_group["id"])

    # Step 7: JWT mappers
    print(f"[Step 7/{TOTAL_STEPS}] Configuring JWT mappers (groups + group_ids)", flush=True)
    configure_groups_mapper(token, REALM, cid)

    # Step 8: iam DB
    seed_iam_db()

    print("\n" + "=" * 60, flush=True)
    print("Single-tenant init complete (realm: aidp)", flush=True)
    print(f"  Realm: {REALM}", flush=True)
    print(f"  Admin user:        {ADMIN_USERNAME} / {ADMIN_INIT_PASSWORD}  (groups: admins, all-users)", flush=True)
    print(f"  Normal user:       {NORMAL_USERNAME} / {NORMAL_INIT_PASSWORD}  (groups: all-users)", flush=True)
    print(f"  Normal user:       normal-user / {NORMAL_INIT_PASSWORD}  (groups: all-users)", flush=True)
    print(f"  Client: {CLIENT_ID} (K8s Secret: {K8S_NAMESPACE}/{K8S_SECRET_NAME})", flush=True)
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
