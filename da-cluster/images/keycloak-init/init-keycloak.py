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
    try:
        existing = v1.read_namespaced_secret(name, K8S_NAMESPACE)
        existing.data = enc
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


def ensure_user(token, realm, username, password, first="", last="", email=""):
    if not email:
        email = f"{username}@{realm}.local"
    existing = find_user(token, realm, username)
    if existing:
        uid = existing["id"]
        print(f"  User '{username}' already exists", flush=True)
    else:
        r = requests.post(
            f"{KEYCLOAK_URL}/admin/realms/{realm}/users",
            json={"username": username, "enabled": True, "emailVerified": True,
                  "firstName": first, "lastName": last, "email": email},
            headers=H(token), timeout=10,
        )
        if r.status_code not in (200, 201):
            raise RuntimeError(f"Failed to create user '{username}': {r.text}")
        uid = r.headers["Location"].split("/")[-1]
        print(f"  Created user '{username}' (id: {uid})", flush=True)
    # set/refresh password
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
                enabled BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW())
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS resource_patterns (
                app_name VARCHAR(128) NOT NULL REFERENCES apps(app_name),
                resource_prefix VARCHAR(256) NOT NULL,
                resource_type VARCHAR(128) NOT NULL,
                PRIMARY KEY (app_name, resource_prefix))
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS path_rules (
                id SERIAL PRIMARY KEY,
                path_prefix VARCHAR(256) NOT NULL,
                method VARCHAR(10),
                required_group VARCHAR(128) NOT NULL DEFAULT '',
                description VARCHAR(512),
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                UNIQUE (path_prefix, method))
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS path_rule_groups (
                rule_id INTEGER REFERENCES path_rules(id) ON DELETE CASCADE,
                group_name VARCHAR(128) NOT NULL,
                PRIMARY KEY (rule_id, group_name))
        """)

        # ---- Apps ----
        cur.execute("""
            INSERT INTO apps (app_name, path_prefix, display_name, description, enabled) VALUES
                ('knowledgebase', '/kb/',       '知识库',               '知识库管理系统（知识库/文件/问答/模型/提示词/黑话）', true),
                ('rubik',         '/rubik/',    '智能问数 (RubikSQL)',  '自然语言转SQL查询平台（数据库/知识库/会话/查询）', true),
                ('memory',        '/memory/',   '记忆库',               'Memory service', true),
                ('httpbin',       '/anything/', 'HTTPBin Echo',         'Test backend', true)
            ON CONFLICT (app_name) DO UPDATE SET
                path_prefix = EXCLUDED.path_prefix,
                display_name = EXCLUDED.display_name,
                description = EXCLUDED.description
        """)

        # ---- Resource Patterns ----
        cur.execute("""
            INSERT INTO resource_patterns (app_name, resource_prefix, resource_type, id_source, id_field) VALUES
                ('knowledgebase', '/knowledge_bases', 'kb',       'body', 'KDSID'),
                ('rubik',         '/api/databases',   'database', 'path', 'id'),
                ('memory',        '/v1/memories',     'memory',   'path', 'id'),
                ('httpbin',       '/items',           'item',     'path', 'id')
            ON CONFLICT (app_name, resource_prefix) DO UPDATE SET
                id_source = EXCLUDED.id_source, id_field = EXCLUDED.id_field
        """)

        # ---- Resource Actions (non-standard RESTful) ----
        cur.execute("""
            INSERT INTO resource_actions (app_name, resource_prefix, method, path_suffix, action, success_status, min_permission) VALUES
                ('knowledgebase', '/knowledge_bases', 'POST',   '/add',    'create', 201,  'none'),
                ('knowledgebase', '/knowledge_bases', 'POST',   '/remove', 'delete', NULL, 'owner'),
                ('rubik',         '/api/databases',   'POST',   NULL,      'create', 201,  'none'),
                ('rubik',         '/api/databases',   'DELETE', '/{id}',   'delete', NULL, 'owner')
            ON CONFLICT DO NOTHING
        """)

        # ---- Path Rules + Groups (多对多) ----
        # 格式: (path_prefix, method, group, description)
        # method=None 表示匹配所有 HTTP 方法
        # KB 写操作 → kb-admins (KB 全部用 POST)
        kb_rules = [
            ('/kb/knowledge_bases/add',       'POST', 'kb-admins', '知识库创建'),
            ('/kb/knowledge_bases/modify',    'POST', 'kb-admins', '知识库修改'),
            ('/kb/knowledge_bases/remove',    'POST', 'kb-admins', '知识库删除'),
            ('/kb/knowledge_bases/mappings/add',    'POST', 'kb-admins', '目录映射创建'),
            ('/kb/knowledge_bases/mappings/remove', 'POST', 'kb-admins', '目录映射删除'),
            ('/kb/knowledge_bases/files/upload',           'POST', 'kb-admins', '文件上传'),
            ('/kb/knowledge_bases/files/remove',           'POST', 'kb-admins', '文件删除'),
            ('/kb/knowledge_bases/files/filesystem/add',   'POST', 'kb-admins', '文件系统创建'),
            ('/kb/knowledge_bases/files/filesystem/remove', 'POST', 'kb-admins', '文件系统删除'),
            ('/kb/models/config/add',    'POST', 'kb-admins', '模型配置新增'),
            ('/kb/models/config/modify', 'POST', 'kb-admins', '模型配置修改'),
            ('/kb/models/config/remove', 'POST', 'kb-admins', '模型配置删除'),
            ('/kb/models/config/set',    'POST', 'kb-admins', '模型配置启用'),
            ('/kb/prompts/add',    'POST', 'kb-admins', '提示词创建'),
            ('/kb/prompts/modify', 'POST', 'kb-admins', '提示词修改'),
            ('/kb/prompts/remove', 'POST', 'kb-admins', '提示词删除'),
            ('/kb/jargon_groups/add',                    'POST', 'kb-admins', '黑话库创建'),
            ('/kb/jargon_groups/remove',                 'POST', 'kb-admins', '黑话库删除'),
            ('/kb/jargon_groups/knowledge_bases/add',    'POST', 'kb-admins', '黑话库绑定知识库'),
            ('/kb/jargon_groups/knowledge_bases/remove', 'POST', 'kb-admins', '黑话库解绑知识库'),
            ('/kb/jargons/add',    'POST', 'kb-admins', '黑话创建'),
            ('/kb/jargons/modify', 'POST', 'kb-admins', '黑话修改'),
            ('/kb/jargons/remove', 'POST', 'kb-admins', '黑话删除'),
            # 兜底：/kb/knowledge_bases/{id}/... 子路径的写操作
            ('/kb/knowledge_bases/', 'POST',   'kb-admins', '知识库资源写操作'),
            ('/kb/knowledge_bases/', 'PUT',    'kb-admins', '知识库资源修改'),
            ('/kb/knowledge_bases/', 'DELETE', 'kb-admins', '知识库资源删除'),
        ]
        # Rubik 配置操作 → rubik-admins
        rubik_rules = [
            ('/rubik/api/config/models/',           'PUT',  'rubik-admins', '更新模型预设'),
            ('/rubik/api/config/database-providers/', 'PUT', 'rubik-admins', '更新数据库提供者'),
            ('/rubik/api/config/language',           'PUT',  'rubik-admins', '设置语言'),
            ('/rubik/api/config/languages',          'PUT',  'rubik-admins', '分别设置语言'),
            ('/rubik/api/config/app/',               'PUT',  'rubik-admins', '设置应用配置'),
            ('/rubik/api/config/reload',             'POST', 'rubik-admins', '重载配置'),
            ('/rubik/api/config/setup',              'POST', 'rubik-admins', '初始化配置'),
            ('/rubik/api/config/llm-providers',      None,   'rubik-admins', 'LLM提供者管理'),
            ('/rubik/api/databases/knowledge/special', 'POST', 'rubik-admins', '添加特殊知识'),
            # 兜底：/rubik/api/databases/{id}/... 子路径的写操作
            ('/rubik/api/databases/', 'POST',   'rubik-admins', '数据库创建'),
            ('/rubik/api/databases/', 'PUT',    'rubik-admins', '数据库修改'),
            ('/rubik/api/databases/', 'DELETE', 'rubik-admins', '数据库删除'),
        ]
        for path, method, group, desc in kb_rules + rubik_rules:
            cur.execute(
                "INSERT INTO path_rules (path_prefix, method, required_group, description) VALUES (%s, %s, %s, %s) ON CONFLICT (path_prefix, method) DO NOTHING RETURNING id",
                (path, method, group, desc))
            row = cur.fetchone()
            if row:
                cur.execute(
                    "INSERT INTO path_rule_groups (rule_id, group_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (row[0], group))

        cur.close()
        print(f"  IAM DB seeded ({len(kb_rules)} KB rules, {len(rubik_rules)} Rubik rules)", flush=True)
    finally:
        conn.close()


# ===================== Main =====================
def main():
    wait_for_keycloak()
    token = get_admin_token()

    # Step 3: realm
    print(f"[Step 3/{TOTAL_STEPS}] Ensuring realm '{REALM}'", flush=True)
    ensure_realm(token, REALM)

    # Step 4: groups + default group + app-admin groups
    print(f"[Step 4/{TOTAL_STEPS}] Setting up groups (admins, all-users, app-admins)", flush=True)
    admins_group = ensure_group(token, REALM, "admins")
    all_users_group = ensure_group(token, REALM, "all-users")
    ensure_group(token, REALM, "kb-admins")
    ensure_group(token, REALM, "rubik-admins")
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
    admin_uid = ensure_user(token, REALM, ADMIN_USERNAME, ADMIN_INIT_PASSWORD,
                            first="Aidp", last="Admin")
    if admins_group:
        add_user_to_group(token, REALM, admin_uid, admins_group["id"])
    if all_users_group:
        add_user_to_group(token, REALM, admin_uid, all_users_group["id"])
    normal_uid = ensure_user(token, REALM, NORMAL_USERNAME, NORMAL_INIT_PASSWORD,
                             first="Normal", last="User")
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
    print(f"  Admin user: {ADMIN_USERNAME} (groups: admins, all-users)", flush=True)
    print(f"  Normal user: {NORMAL_USERNAME} (groups: all-users)", flush=True)
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
