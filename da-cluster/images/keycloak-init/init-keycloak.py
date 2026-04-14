#!/usr/bin/env python3
"""
Keycloak Init Job — v2.0 (Groups Model)

Switches from roles-based to groups-based model:
  - master realm: master-admins group
  - tenant realms: tenant-admins, all-users (default group)
  - JWT includes groups + group_ids claims via Group Membership mapper
  - Seeds iam DB with apps, resource_patterns
"""
import os
import time
import json
import requests
import base64
from kubernetes import client, config
from kubernetes.client.rest import ApiException

# ===================== Configuration =====================
KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://keycloak:8080")
KEYCLOAK_HEALTH_URL = os.getenv("KEYCLOAK_HEALTH_URL", "http://keycloak:9000")
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

SUPER_ADMIN_USER = os.getenv("SUPER_ADMIN_USER", "super-admin")
SUPER_ADMIN_INIT_PASSWORD = os.getenv("SUPER_ADMIN_INIT_PASSWORD", "SuperInit@123")

IDB_PROXY_CLIENT_ID = os.getenv("IDB_PROXY_CLIENT_ID", "idb-proxy-client")
K8S_SECRET_NAME = os.getenv("K8S_SECRET_NAME", "keycloak-idb-proxy-client")
K8S_NAMESPACE = os.getenv("K8S_NAMESPACE", "keycloak")

# Default tenant configuration
DEFAULT_TENANT_REALM = os.getenv("DEFAULT_TENANT_REALM", "data-agent")
DEFAULT_TENANT_ADMIN_USER = os.getenv("DEFAULT_TENANT_ADMIN_USER", "tenant-admin")
DEFAULT_TENANT_ADMIN_PASSWORD = os.getenv("DEFAULT_TENANT_ADMIN_PASSWORD", "TenantAdmin@123")
DEFAULT_TENANT_NORMAL_USER = os.getenv("DEFAULT_TENANT_NORMAL_USER", "normal-user")
DEFAULT_TENANT_NORMAL_PASSWORD = os.getenv("DEFAULT_TENANT_NORMAL_PASSWORD", "NormalUser@123")

# IAM DB for seeding
IAM_DB_URL = os.getenv("IAM_DB_URL", "postgresql://keycloak:keycloak@postgres:5432/iam")

TOTAL_STEPS = 9

# ===================== Utility: Wait for Keycloak =====================
def wait_for_keycloak():
    health_url = f"{KEYCLOAK_HEALTH_URL}/health/ready"
    print(f"[Step 1/{TOTAL_STEPS}] Waiting for Keycloak: {health_url}", flush=True)
    max_retries = 50
    for i in range(max_retries):
        try:
            resp = requests.get(health_url, timeout=5)
            if resp.status_code == 200:
                print(f"[Step 1/{TOTAL_STEPS}] Keycloak is ready!", flush=True)
                return True
            print(f"[Step 1/{TOTAL_STEPS}] Health check returned {resp.status_code}, waiting... ({i+1}/{max_retries})", flush=True)
        except requests.exceptions.ConnectionError:
            print(f"[Step 1/{TOTAL_STEPS}] Keycloak not up yet, waiting... ({i+1}/{max_retries})", flush=True)
        except Exception as e:
            print(f"[Step 1/{TOTAL_STEPS}] Health check error: {e}, waiting... ({i+1}/{max_retries})", flush=True)
        time.sleep(5)
    raise Exception(f"Keycloak not ready after {max_retries*5}s")

# ===================== Utility: Get Admin Token =====================
def get_keycloak_token():
    print(f"[Step 2/{TOTAL_STEPS}] Getting Keycloak Admin Token...", flush=True)
    url = f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token"
    data = {
        "username": ADMIN_USER,
        "password": ADMIN_PASSWORD,
        "grant_type": "password",
        "client_id": "admin-cli"
    }
    resp = requests.post(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=10)
    resp.raise_for_status()
    token = resp.json()["access_token"]
    print(f"[Step 2/{TOTAL_STEPS}] Got admin token (expires in {resp.json()['expires_in']}s)", flush=True)
    return token

# ===================== Utility: K8s Client =====================
def init_k8s_client():
    config.load_incluster_config()
    return client.CoreV1Api()

def create_or_update_k8s_secret(secret_data):
    print(f"[Tool] Saving K8s Secret: {K8S_SECRET_NAME} (ns: {K8S_NAMESPACE})", flush=True)
    v1_api = init_k8s_client()
    encoded_data = {k: base64.b64encode(v.encode("utf-8")).decode("utf-8") for k, v in secret_data.items()}
    try:
        existing = v1_api.read_namespaced_secret(K8S_SECRET_NAME, K8S_NAMESPACE)
        existing.data = encoded_data
        v1_api.patch_namespaced_secret(K8S_SECRET_NAME, K8S_NAMESPACE, existing)
        print(f"[Tool] Updated K8s Secret: {K8S_SECRET_NAME}", flush=True)
    except ApiException as e:
        if e.status == 404:
            secret = client.V1Secret(
                api_version="v1", kind="Secret",
                metadata=client.V1ObjectMeta(name=K8S_SECRET_NAME, namespace=K8S_NAMESPACE,
                    labels={"app": "keycloak", "component": "idb-proxy-client"}),
                type="Opaque", data=encoded_data
            )
            v1_api.create_namespaced_secret(K8S_NAMESPACE, secret)
            print(f"[Tool] Created K8s Secret: {K8S_SECRET_NAME}", flush=True)
        else:
            raise

# ===================== Keycloak API helpers =====================
def kc_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

def find_user(token, realm, username):
    url = f"{KEYCLOAK_URL}/admin/realms/{realm}/users?username={username}&exact=true"
    resp = requests.get(url, headers=kc_headers(token), timeout=10)
    resp.raise_for_status()
    users = resp.json()
    return users[0] if users else None

def create_user(token, realm, username, password, first_name="", last_name="",
                email="", temporary_password=False):
    if not email:
        email = f"{username}@{realm}.local"
    existing = find_user(token, realm, username)
    if existing:
        user_id = existing["id"]
        print(f"  User '{username}' already exists (id: {user_id})", flush=True)
        if not existing.get("email"):
            update_url = f"{KEYCLOAK_URL}/admin/realms/{realm}/users/{user_id}"
            requests.put(update_url, json={"email": email, "emailVerified": True},
                         headers=kc_headers(token), timeout=10)
    else:
        url = f"{KEYCLOAK_URL}/admin/realms/{realm}/users"
        user_data = {
            "username": username, "enabled": True, "emailVerified": True,
            "firstName": first_name, "lastName": last_name,
            "email": email
        }
        resp = requests.post(url, json=user_data, headers=kc_headers(token), timeout=10)
        if resp.status_code not in [201, 200]:
            raise Exception(f"Failed to create user '{username}': {resp.text}")
        user_id = resp.headers["Location"].split("/")[-1]
        print(f"  Created user '{username}' (id: {user_id})", flush=True)

    # Set password
    pwd_url = f"{KEYCLOAK_URL}/admin/realms/{realm}/users/{user_id}/reset-password"
    pwd_data = {"type": "password", "value": password, "temporary": temporary_password}
    resp = requests.put(pwd_url, json=pwd_data, headers=kc_headers(token), timeout=10)
    if resp.status_code not in [204, 200]:
        raise Exception(f"Failed to set password for '{username}': {resp.text}")
    return user_id

# ===================== Group helpers (v2.0) =====================

def get_group_by_name(token, realm, group_name):
    """Find a top-level group by name. Returns group dict or None."""
    url = f"{KEYCLOAK_URL}/admin/realms/{realm}/groups?search={group_name}&exact=true"
    resp = requests.get(url, headers=kc_headers(token), timeout=10)
    resp.raise_for_status()
    for g in resp.json():
        if g.get("name") == group_name:
            return g
    return None

def create_group(token, realm, group_name):
    """Create a top-level group if it doesn't exist. Returns group dict."""
    existing = get_group_by_name(token, realm, group_name)
    if existing:
        print(f"  Group '{group_name}' already exists in realm '{realm}'", flush=True)
        return existing
    url = f"{KEYCLOAK_URL}/admin/realms/{realm}/groups"
    resp = requests.post(url, json={"name": group_name},
                         headers=kc_headers(token), timeout=10)
    if resp.status_code not in [201, 200, 409]:
        raise Exception(f"Failed to create group '{group_name}': {resp.text}")
    group = get_group_by_name(token, realm, group_name)
    print(f"  Created group '{group_name}' in realm '{realm}'", flush=True)
    return group

def add_user_to_group(token, realm, user_id, group_id):
    """Add a user to a group."""
    url = f"{KEYCLOAK_URL}/admin/realms/{realm}/users/{user_id}/groups/{group_id}"
    resp = requests.put(url, headers=kc_headers(token), timeout=10)
    if resp.status_code in [204, 200]:
        print(f"  Added user to group", flush=True)
    else:
        print(f"  Warning: add user to group returned {resp.status_code}: {resp.text}", flush=True)

def set_default_groups(token, realm, group_ids):
    """Set default groups for new users in a realm."""
    # First get current realm config
    url = f"{KEYCLOAK_URL}/admin/realms/{realm}"
    resp = requests.get(url, headers=kc_headers(token), timeout=10)
    resp.raise_for_status()
    realm_config = resp.json()

    # Set default groups via the dedicated endpoint
    for gid in group_ids:
        default_url = f"{KEYCLOAK_URL}/admin/realms/{realm}/default-groups/{gid}"
        resp = requests.put(default_url, headers=kc_headers(token), timeout=10)
        if resp.status_code in [204, 200]:
            print(f"  Set default group {gid} in realm '{realm}'", flush=True)
        else:
            print(f"  Warning: set default group returned {resp.status_code}: {resp.text}", flush=True)

# ===================== Mapper helpers (v2.0) =====================

def create_groups_mapper(token, realm, client_internal_id):
    """
    Configure the client's JWT so that it carries BOTH group names and group UUIDs,
    per diagrams/story-breakdown.md SR01: `groups` + `group_ids`.

    Implemented via the custom `structured-group-mapper` SPI shipped in the
    keycloak-custom image (see da-cluster/images/keycloak-custom/spi/...).
    If the SPI isn't available in the Keycloak image this falls back to the
    built-in oidc-group-membership-mapper (names only).
    """
    headers = kc_headers(token)
    url = f"{KEYCLOAK_URL}/admin/realms/{realm}/clients/{client_internal_id}/protocol-mappers/models"

    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    existing = {m.get("name"): m for m in resp.json()}

    # Remove the legacy oidc-group-membership-mapper so we don't end up with
    # both a built-in names-only mapper AND our SPI writing `groups` twice.
    for stale_name in ("groups-mapper",):
        if stale_name in existing and existing[stale_name].get("protocolMapper") == "oidc-group-membership-mapper":
            del_url = f"{url}/{existing[stale_name]['id']}"
            requests.delete(del_url, headers=headers, timeout=10)
            print(f"  Removed legacy groups-mapper (names-only) in realm '{realm}'", flush=True)
            existing.pop(stale_name, None)

    # Preferred path: custom SPI producing both `groups` + `group_ids`.
    if "groups-structured-mapper" not in existing:
        mapper = {
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
        resp = requests.post(url, json=mapper, headers=headers, timeout=10)
        if resp.status_code in (200, 201):
            print(f"  Created structured-group-mapper (groups + group_ids) in realm '{realm}'", flush=True)
        elif resp.status_code == 400 and "structured-group-mapper" in resp.text:
            # SPI not present in this Keycloak image — fall back to built-in names-only mapper.
            print(f"  Warning: structured-group-mapper SPI unavailable, falling back to names-only mapper", flush=True)
            fallback = {
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
            }
            requests.post(url, json=fallback, headers=headers, timeout=10)
        else:
            print(f"  Warning: structured-group-mapper creation returned {resp.status_code}: {resp.text}", flush=True)
    else:
        print(f"  structured-group-mapper already exists in realm '{realm}'", flush=True)

    # Remove old data-agent-mapper (v1.0 structured-role-mapper artifact)
    for m in requests.get(url, headers=headers, timeout=10).json():
        if m.get("name") == "data-agent-mapper":
            del_url = f"{url}/{m['id']}"
            requests.delete(del_url, headers=headers, timeout=10)
            print(f"  Removed old data-agent-mapper from realm '{realm}'", flush=True)
            break

# ===================== Role helpers (kept for backward compat during transition) =====================

def get_realm_role(token, realm, role_name):
    url = f"{KEYCLOAK_URL}/admin/realms/{realm}/roles/{role_name}"
    resp = requests.get(url, headers=kc_headers(token), timeout=10)
    if resp.status_code == 200:
        return resp.json()
    return None

def assign_realm_roles(token, realm, user_id, roles):
    url = f"{KEYCLOAK_URL}/admin/realms/{realm}/users/{user_id}/role-mappings/realm"
    resp = requests.post(url, json=roles, headers=kc_headers(token), timeout=10)
    if resp.status_code in [204, 200]:
        print(f"  Assigned roles {[r['name'] for r in roles]} to user", flush=True)

# ===================== Step 3: Setup super-admin with groups =====================
def setup_super_admin(token):
    print(f"[Step 3/{TOTAL_STEPS}] Setting up master-admins group and super-admin user...", flush=True)

    # Create master-admins group in master realm
    master_admins = create_group(token, "master", "master-admins")

    # Create super-admin user
    user_id = create_user(token, "master", SUPER_ADMIN_USER, SUPER_ADMIN_INIT_PASSWORD,
                          first_name="Super", last_name="Admin",
                          email=f"{SUPER_ADMIN_USER}@master.local",
                          temporary_password=False)

    # Add to master-admins group
    if master_admins:
        add_user_to_group(token, "master", user_id, master_admins["id"])

    # Also assign create-realm role (needed for realm creation API)
    create_realm_role_obj = get_realm_role(token, "master", "create-realm")
    if create_realm_role_obj:
        assign_realm_roles(token, "master", user_id,
                           [{"id": create_realm_role_obj["id"], "name": "create-realm"}])

    print(f"[Step 3/{TOTAL_STEPS}] Super admin setup complete: {SUPER_ADMIN_USER}", flush=True)
    return user_id

# ===================== Step 4: Create IDB Proxy Client =====================
def create_idb_proxy_client(token):
    print(f"[Step 4/{TOTAL_STEPS}] Creating service account client: {IDB_PROXY_CLIENT_ID}...", flush=True)
    headers = kc_headers(token)

    search_url = f"{KEYCLOAK_URL}/admin/realms/master/clients?clientId={IDB_PROXY_CLIENT_ID}"
    resp = requests.get(search_url, headers=headers, timeout=10)
    resp.raise_for_status()

    existing_client = False
    if resp.json():
        existing_client = True
        client_info = resp.json()[0]
        client_id = client_info["id"]
        print(f"  Client already exists (id: {client_id})", flush=True)
        secret_url = f"{KEYCLOAK_URL}/admin/realms/master/clients/{client_id}/client-secret"
        secret_resp = requests.get(secret_url, headers=headers, timeout=10)
        secret_resp.raise_for_status()
        client_secret = secret_resp.json()["value"]
    else:
        client_url = f"{KEYCLOAK_URL}/admin/realms/master/clients"
        client_data = {
            "clientId": IDB_PROXY_CLIENT_ID,
            "name": "IDB Proxy Client (Auto-created)",
            "enabled": True,
            "clientAuthenticatorType": "client-secret",
            "redirectUris": ["*"], "webOrigins": ["*"],
            "serviceAccountsEnabled": True,
            "directAccessGrantsEnabled": True,
            "standardFlowEnabled": True,
            "implicitFlowEnabled": False,
            "publicClient": False,
            "bearerOnly": False
        }
        resp = requests.post(client_url, json=client_data, headers=headers, timeout=10)
        if resp.status_code not in [201, 200]:
            raise Exception(f"Failed to create client: {resp.text}")
        client_id = resp.headers["Location"].split("/")[-1]
        print(f"  Created client (id: {client_id})", flush=True)

        secret_url = f"{KEYCLOAK_URL}/admin/realms/master/clients/{client_id}/client-secret"
        secret_resp = requests.post(secret_url, headers=headers, timeout=10)
        secret_resp.raise_for_status()
        client_secret = secret_resp.json()["value"]

        # Assign admin role to service account
        sa_url = f"{KEYCLOAK_URL}/admin/realms/master/clients/{client_id}/service-account-user"
        sa_resp = requests.get(sa_url, headers=headers, timeout=10)
        sa_resp.raise_for_status()
        sa_user_id = sa_resp.json()["id"]

        admin_role = get_realm_role(token, "master", "admin")
        sa_roles = []
        if admin_role:
            sa_roles.append({"id": admin_role["id"], "name": "admin"})
        if sa_roles:
            assign_realm_roles(token, "master", sa_user_id, sa_roles)

        # Add service account to master-admins group
        master_admins = get_group_by_name(token, "master", "master-admins")
        if master_admins:
            add_user_to_group(token, "master", sa_user_id, master_admins["id"])

    # For existing clients, ensure service account is in master-admins group
    if existing_client:
        sa_url = f"{KEYCLOAK_URL}/admin/realms/master/clients/{client_id}/service-account-user"
        sa_resp = requests.get(sa_url, headers=headers, timeout=10)
        sa_resp.raise_for_status()
        sa_user_id = sa_resp.json()["id"]
        master_admins = get_group_by_name(token, "master", "master-admins")
        if master_admins:
            add_user_to_group(token, "master", sa_user_id, master_admins["id"])

    # Store in K8s Secret
    create_or_update_k8s_secret({
        "client-id": IDB_PROXY_CLIENT_ID,
        "client-secret": client_secret,
        "keycloak-url": KEYCLOAK_URL,
        "created-at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    })
    print(f"[Step 4/{TOTAL_STEPS}] Client setup complete: {IDB_PROXY_CLIENT_ID}", flush=True)
    return client_id, client_secret

# ===================== Step 5: Create default tenant realm with groups =====================
def create_default_tenant(token):
    realm = DEFAULT_TENANT_REALM
    print(f"[Step 5/{TOTAL_STEPS}] Creating default tenant realm: {realm}...", flush=True)
    headers = kc_headers(token)

    # Check if realm exists
    resp = requests.get(f"{KEYCLOAK_URL}/admin/realms/{realm}", headers=headers, timeout=10)
    if resp.status_code == 200:
        print(f"  Realm '{realm}' already exists, skipping creation", flush=True)
    else:
        realm_data = {
            "realm": realm,
            "displayName": "Data Agent (Default Tenant)",
            "enabled": True,
            "registrationAllowed": False,
            "loginWithEmailAllowed": True,
            "duplicateEmailsAllowed": False,
            "resetPasswordAllowed": True,
            "editUsernameAllowed": False,
            "bruteForceProtected": True
        }
        resp = requests.post(f"{KEYCLOAK_URL}/admin/realms", json=realm_data,
                             headers=headers, timeout=10)
        if resp.status_code not in [201, 200]:
            raise Exception(f"Failed to create realm '{realm}': {resp.text}")
        print(f"  Created realm '{realm}'", flush=True)

    # Create groups (v2.0)
    tenant_admins = create_group(token, realm, "tenant-admins")
    all_users = create_group(token, realm, "all-users")

    # Set all-users as default group
    if all_users:
        set_default_groups(token, realm, [all_users["id"]])

    # Create tenant-admin user
    ta_user_id = create_user(token, realm, DEFAULT_TENANT_ADMIN_USER,
                             DEFAULT_TENANT_ADMIN_PASSWORD,
                             first_name="Tenant", last_name="Admin",
                             temporary_password=False)
    if tenant_admins:
        add_user_to_group(token, realm, ta_user_id, tenant_admins["id"])
    if all_users:
        add_user_to_group(token, realm, ta_user_id, all_users["id"])

    # Create normal user (auto-joined to all-users as default group)
    nu_user_id = create_user(token, realm, DEFAULT_TENANT_NORMAL_USER,
                             DEFAULT_TENANT_NORMAL_PASSWORD,
                             first_name="Normal", last_name="User",
                             temporary_password=False)
    if all_users:
        add_user_to_group(token, realm, nu_user_id, all_users["id"])

    print(f"[Step 5/{TOTAL_STEPS}] Default tenant '{realm}' setup complete", flush=True)

# ===================== Step 6: Create client in tenant realm =====================
def create_tenant_client(token):
    realm = DEFAULT_TENANT_REALM
    client_id_name = realm
    print(f"[Step 6/{TOTAL_STEPS}] Creating client '{client_id_name}' in realm '{realm}'...", flush=True)
    headers = kc_headers(token)

    search_url = f"{KEYCLOAK_URL}/admin/realms/{realm}/clients?clientId={client_id_name}"
    resp = requests.get(search_url, headers=headers, timeout=10)
    resp.raise_for_status()

    if resp.json():
        client_info = resp.json()[0]
        cid = client_info["id"]
        print(f"  Client '{client_id_name}' already exists (id: {cid})", flush=True)
        secret_url = f"{KEYCLOAK_URL}/admin/realms/{realm}/clients/{cid}/client-secret"
        secret_resp = requests.get(secret_url, headers=headers, timeout=10)
        secret_resp.raise_for_status()
        tenant_client_secret = secret_resp.json()["value"]
    else:
        client_data = {
            "clientId": client_id_name,
            "name": f"{realm} Tenant Client",
            "enabled": True,
            "clientAuthenticatorType": "client-secret",
            "redirectUris": ["*"], "webOrigins": ["*"],
            "serviceAccountsEnabled": False,
            "directAccessGrantsEnabled": True,
            "standardFlowEnabled": True,
            "publicClient": True,
            "bearerOnly": False
        }
        resp = requests.post(f"{KEYCLOAK_URL}/admin/realms/{realm}/clients",
                             json=client_data, headers=headers, timeout=10)
        if resp.status_code not in [201, 200]:
            raise Exception(f"Failed to create tenant client: {resp.text}")
        cid = resp.headers["Location"].split("/")[-1]
        print(f"  Created client '{client_id_name}' (id: {cid})", flush=True)

        secret_url = f"{KEYCLOAK_URL}/admin/realms/{realm}/clients/{cid}/client-secret"
        secret_resp = requests.post(secret_url, headers=headers, timeout=10)
        secret_resp.raise_for_status()
        tenant_client_secret = secret_resp.json()["value"]

    # Store tenant client secret in K8s
    tenant_secret_name = f"keycloak-{realm}-client"
    print(f"  Saving tenant client secret to K8s Secret: {tenant_secret_name}", flush=True)
    v1_api = init_k8s_client()
    encoded_data = {
        k: base64.b64encode(v.encode("utf-8")).decode("utf-8") for k, v in {
            "client-id": client_id_name,
            "client-secret": tenant_client_secret,
            "realm": realm,
        }.items()
    }
    try:
        existing = v1_api.read_namespaced_secret(tenant_secret_name, K8S_NAMESPACE)
        existing.data = encoded_data
        v1_api.patch_namespaced_secret(tenant_secret_name, K8S_NAMESPACE, existing)
    except ApiException as e:
        if e.status == 404:
            secret = client.V1Secret(
                api_version="v1", kind="Secret",
                metadata=client.V1ObjectMeta(name=tenant_secret_name, namespace=K8S_NAMESPACE,
                    labels={"app": "keycloak", "component": "tenant-client"}),
                type="Opaque", data=encoded_data
            )
            v1_api.create_namespaced_secret(K8S_NAMESPACE, secret)
        else:
            raise

    print(f"[Step 6/{TOTAL_STEPS}] Tenant client setup complete", flush=True)
    return cid, tenant_client_secret

# ===================== Step 7: Apply Groups Mapper to clients =====================
def setup_groups_mapper(token):
    print(f"[Step 7/{TOTAL_STEPS}] Setting up Groups Protocol Mapper...", flush=True)
    headers = kc_headers(token)

    # Apply to idb-proxy-client in master realm
    search_url = f"{KEYCLOAK_URL}/admin/realms/master/clients?clientId={IDB_PROXY_CLIENT_ID}"
    resp = requests.get(search_url, headers=headers, timeout=10)
    resp.raise_for_status()
    if resp.json():
        master_client_id = resp.json()[0]["id"]
        create_groups_mapper(token, "master", master_client_id)

    # Apply to tenant client in tenant realm
    search_url = f"{KEYCLOAK_URL}/admin/realms/{DEFAULT_TENANT_REALM}/clients?clientId={DEFAULT_TENANT_REALM}"
    resp = requests.get(search_url, headers=headers, timeout=10)
    resp.raise_for_status()
    if resp.json():
        tenant_client_id = resp.json()[0]["id"]
        create_groups_mapper(token, DEFAULT_TENANT_REALM, tenant_client_id)

    print(f"[Step 7/{TOTAL_STEPS}] Groups mapper setup complete", flush=True)

# ===================== Step 8: Wait for IAM DB and seed data =====================
def seed_iam_db():
    print(f"[Step 8/{TOTAL_STEPS}] Seeding IAM database...", flush=True)

    try:
        import psycopg2
    except ImportError:
        print(f"[Step 8/{TOTAL_STEPS}] psycopg2 not available, skipping IAM DB seeding", flush=True)
        return

    # Wait for iam DB to be ready
    max_retries = 30
    conn = None
    for i in range(max_retries):
        try:
            conn = psycopg2.connect(IAM_DB_URL)
            break
        except Exception as e:
            print(f"  IAM DB not ready, waiting... ({i+1}/{max_retries}): {e}", flush=True)
            time.sleep(3)

    if not conn:
        print(f"[Step 8/{TOTAL_STEPS}] WARNING: Could not connect to IAM DB, skipping seeding", flush=True)
        return

    try:
        conn.autocommit = True
        cur = conn.cursor()

        # Ensure tables exist (safety net)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS apps (
                app_name     VARCHAR(128) PRIMARY KEY,
                path_prefix  VARCHAR(256) NOT NULL UNIQUE,
                display_name VARCHAR(256),
                description  VARCHAR(512),
                enabled      BOOLEAN      NOT NULL DEFAULT true,
                created_at   TIMESTAMP    NOT NULL DEFAULT NOW(),
                updated_at   TIMESTAMP    NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS resource_patterns (
                app_name        VARCHAR(128) NOT NULL REFERENCES apps(app_name),
                resource_prefix VARCHAR(256) NOT NULL,
                resource_type   VARCHAR(128) NOT NULL,
                PRIMARY KEY (app_name, resource_prefix)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS path_rules (
                id              SERIAL PRIMARY KEY,
                path_prefix     VARCHAR(256) NOT NULL UNIQUE,
                required_group  VARCHAR(128) NOT NULL,
                description     VARCHAR(512),
                created_at      TIMESTAMP    NOT NULL DEFAULT NOW()
            )
        """)

        # Seed default apps (per diagrams/story-breakdown.md SR08 init-job 流程).
        # path_prefix MUST match the HTTPRoute path that fronts each backend so
        # ext_proc / pep-proxy can resolve apps via the request path.
        cur.execute("""
            INSERT INTO apps (app_name, path_prefix, display_name, description, enabled)
            VALUES
                ('knowledgebase', '/knowledgebase/', '知识库',     'Knowledge base service', true),
                ('memory',        '/memory/',        '记忆库',     'Memory service',         true),
                ('httpbin',       '/anything/',      'HTTPBin Echo', 'Test backend for integration testing', true)
            ON CONFLICT (app_name) DO NOTHING
        """)

        # Seed resource patterns
        cur.execute("""
            INSERT INTO resource_patterns (app_name, resource_prefix, resource_type)
            VALUES
                ('knowledgebase', '/v1/kb',       'kb'),
                ('memory',        '/v1/memories', 'memory'),
                ('httpbin',       '/items',       'item')
            ON CONFLICT (app_name, resource_prefix) DO NOTHING
        """)

        cur.close()
        print(f"[Step 8/{TOTAL_STEPS}] IAM database seeded successfully", flush=True)
    except Exception as e:
        print(f"[Step 8/{TOTAL_STEPS}] WARNING: IAM DB seeding failed: {e}", flush=True)
    finally:
        conn.close()

# ===================== Step 9: Summary =====================
def print_summary():
    print(f"\n[Step 9/{TOTAL_STEPS}] " + "="*60, flush=True)
    print(f"All initialization complete! (v2.0 Groups Model)", flush=True)
    print(f"  Super admin: {SUPER_ADMIN_USER} (group: master-admins)", flush=True)
    print(f"  Service client: {IDB_PROXY_CLIENT_ID} (K8s Secret: {K8S_NAMESPACE}/{K8S_SECRET_NAME})", flush=True)
    print(f"  Default tenant: {DEFAULT_TENANT_REALM}", flush=True)
    print(f"    tenant-admin: {DEFAULT_TENANT_ADMIN_USER} (group: tenant-admins, all-users)", flush=True)
    print(f"    normal-user: {DEFAULT_TENANT_NORMAL_USER} (group: all-users)", flush=True)
    print(f"    client: {DEFAULT_TENANT_REALM} (public)", flush=True)
    print(f"  JWT claims: groups (group names list)", flush=True)
    print("="*60 + "\n", flush=True)

# ===================== Main =====================
def main():
    try:
        wait_for_keycloak()
        token = get_keycloak_token()
        setup_super_admin(token)
        create_idb_proxy_client(token)
        create_default_tenant(token)
        create_tenant_client(token)
        setup_groups_mapper(token)
        seed_iam_db()
        print_summary()
        return 0
    except Exception as e:
        print(f"\nInitialization failed: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit(main())
