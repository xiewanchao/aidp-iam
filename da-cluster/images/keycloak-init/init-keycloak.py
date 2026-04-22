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
        cur.execute("""
            INSERT INTO apps (app_name, path_prefix, display_name, description, admin_group, enabled) VALUES
                ('knowledgebase', '/kb/',       '知识库',               '知识库管理系统（知识库/文件/问答/模型/提示词/黑话）', 'kb-admins',    true),
                ('rubik',         '/rubik/',    '智能问数 (RubikSQL)',  '自然语言转SQL查询平台（数据库/知识库/会话/查询）',     'rubik-admins', true),
                ('memory',        '/memory/',   '记忆库',               'Memory service',                                     'memory-admins', true)
            ON CONFLICT (app_name) DO UPDATE SET
                path_prefix = EXCLUDED.path_prefix,
                display_name = EXCLUDED.display_name,
                description = EXCLUDED.description,
                admin_group = EXCLUDED.admin_group
        """)

        # ---- Resource Patterns (method-aware) ----
        # 列：app, prefix, method, resource_type, id_source, id_field, admin_group_on_create, all_users_on_create
        # method='' 表示 fallback，适用于所有未被更具体 method 命中的请求
        cur.execute("""
            INSERT INTO resource_patterns
                (app_name, resource_prefix, method, resource_type, id_source, id_field,
                 share_to_admin_group_on_create, share_to_all_users_on_create) VALUES
                -- === KB 个人资产（kb / conversation） ===
                ('knowledgebase', '/knowledge_bases',          'GET',  'kb',           'query', 'KDSID',           false, false),
                ('knowledgebase', '/knowledge_bases',          'POST', 'kb',           'body',  'KDSID',           false, false),
                ('knowledgebase', '/knowledge_bases/mappings', 'GET',  'kb',           'query', 'KDSID',           false, false),
                ('knowledgebase', '/knowledge_bases/mappings', 'POST', 'kb',           'body',  'KDSID',           false, false),
                ('knowledgebase', '/knowledge_bases/files',    'GET',  'kb',           'query', 'kbs_id',          false, false),
                ('knowledgebase', '/knowledge_bases/files',    'POST', 'kb',           'body',  'kbs_id',          false, false),
                ('knowledgebase', '/conversations',            'GET',  'conversation', 'query', 'conv_id',         false, false),
                ('knowledgebase', '/conversations',            'POST', 'conversation', 'body',  'conv_id',         false, false),
                ('knowledgebase', '/conversations/images',     'GET',  'kb',           'query', 'kbs_id',          false, false),
                ('knowledgebase', '/conversations/images',     'POST', 'kb',           'body',  'kbs_id',          false, false),
                ('knowledgebase', '/retrieval',                'POST', 'kb',           'body',  'kbs_id',          false, false),

                -- === KB 团队共享配置（prompt / model / jargon） ===
                -- method='' 通配：GET 走 list 过滤（body 源对 GET 无害，按 None 处理），
                -- POST 走创建/修改并在 2xx 后由 ext_proc 写 3 行 ACL（creator + kb-admins owner）。
                ('knowledgebase', '/prompts',                  '',     'prompt_group', 'body',  'prompt_id',       true,  false),
                ('knowledgebase', '/models/config',            '',     'model_config', 'body',  'ModelAPIID',      true,  false),
                ('knowledgebase', '/jargon_groups',            '',     'jargon_lib',   'body',  'JARGON_LIB_NAME', true,  false),

                -- === Rubik ===
                ('rubik',         '/api/databases',            '',     'database',     'path',  'id',              false, false),
                ('rubik',         '/api/metadata',             '',     'database',     'path',  'id',              false, false),
                ('rubik',         '/api/query',                'POST', 'database',     'body',  'database_id',     false, false),
                ('rubik',         '/api/sessions',             '',     'session',      'path',  'id',              false, false),

                -- === Memory ===
                ('memory',        '/v1/memories',              '',     'memory',       'path',  'id',              false, false)
            ON CONFLICT (app_name, resource_prefix, method) DO UPDATE SET
                resource_type = EXCLUDED.resource_type,
                id_source = EXCLUDED.id_source,
                id_field = EXCLUDED.id_field,
                share_to_admin_group_on_create = EXCLUDED.share_to_admin_group_on_create,
                share_to_all_users_on_create = EXCLUDED.share_to_all_users_on_create
        """)

        # ---- Resource Actions (non-standard RESTful) ----
        # 默认 method→permission 映射：GET→viewer, POST(create)→none, PUT/PATCH→contributor, DELETE→owner
        # 这里只写"偏离默认"的规则。
        cur.execute("""
            INSERT INTO resource_actions (app_name, resource_prefix, method, path_suffix, action, success_status, min_permission) VALUES
                -- === KB 主体 ===
                ('knowledgebase', '/knowledge_bases', 'POST',   '/add',    'create', 201,  'none'),
                ('knowledgebase', '/knowledge_bases', 'POST',   '/modify', 'update', NULL, 'contributor'),
                ('knowledgebase', '/knowledge_bases', 'POST',   '/remove', 'delete', NULL, 'owner'),

                -- === KB 目录映射（父继承：对父 KB 做 contributor 检查） ===
                ('knowledgebase', '/knowledge_bases/mappings', 'POST', '/add',    'create', NULL, 'contributor'),
                ('knowledgebase', '/knowledge_bases/mappings', 'POST', '/remove', 'delete', NULL, 'contributor'),

                -- === KB 文件（父继承） ===
                ('knowledgebase', '/knowledge_bases/files',    'POST', '/upload', 'upload', NULL, 'contributor'),
                ('knowledgebase', '/knowledge_bases/files',    'POST', '/remove', 'delete', NULL, 'contributor'),

                -- === KB 会话（独立资源） ===
                ('knowledgebase', '/conversations', 'POST', '/start',  'create', 201,  'none'),
                ('knowledgebase', '/conversations', 'POST', '/remove', 'delete', NULL, 'owner'),
                ('knowledgebase', '/conversations', 'POST', '/stop',   'update', NULL, 'owner'),

                -- === KB 图片（对 kb 做权限检查，不是 conversation） ===
                ('knowledgebase', '/conversations/images', 'POST', '/generate', 'read', NULL, 'viewer'),
                ('knowledgebase', '/conversations/images', 'GET',  '/download', 'read', NULL, 'viewer'),

                -- === KB 检索（POST 但只读） ===
                ('knowledgebase', '/retrieval',     'POST', '/fusion_search', 'read', NULL, 'viewer'),

                -- === KB 团队共享配置（prompt/model/jargon_groups/jargons） ===
                ('knowledgebase', '/prompts',        'POST', '/add',    'create', NULL, 'none'),
                ('knowledgebase', '/prompts',        'POST', '/modify', 'update', NULL, 'contributor'),
                ('knowledgebase', '/prompts',        'POST', '/remove', 'delete', NULL, 'owner'),
                ('knowledgebase', '/models/config',  'POST', '/add',    'create', NULL, 'none'),
                ('knowledgebase', '/models/config',  'POST', '/modify', 'update', NULL, 'contributor'),
                ('knowledgebase', '/models/config',  'POST', '/remove', 'delete', NULL, 'owner'),
                ('knowledgebase', '/jargon_groups',  'POST', '/add',    'create', NULL, 'none'),
                ('knowledgebase', '/jargon_groups',  'POST', '/remove', 'delete', NULL, 'owner'),

                -- Rubik databases CRUD
                ('rubik',         '/api/databases',   'POST',   NULL,                    'create', 201,  'none'),
                ('rubik',         '/api/databases',   'DELETE', '/{id}',                 'delete', NULL, 'owner'),

                -- Rubik databases：数据库子路径写操作（默认是 create→none，需升级为 contributor）
                ('rubik',         '/api/databases',   'POST',   '/{id}/execute-sql',     'write',  NULL, 'contributor'),
                ('rubik',         '/api/databases',   'POST',   '/{id}/prettify-sql',    'read',   NULL, 'viewer'),
                ('rubik',         '/api/databases',   'POST',   '/{id}/build',           'write',  NULL, 'contributor'),
                ('rubik',         '/api/databases',   'POST',   '/{id}/build/stream',    'write',  NULL, 'contributor'),
                ('rubik',         '/api/databases',   'POST',   '/{id}/build/cancel',    'write',  NULL, 'contributor'),
                ('rubik',         '/api/databases',   'POST',   '/{id}/knowledge/taxonomy',   'write', NULL, 'contributor'),
                ('rubik',         '/api/databases',   'POST',   '/{id}/knowledge/custom',     'write', NULL, 'contributor'),
                ('rubik',         '/api/databases',   'POST',   '/{id}/knowledge/experience', 'write', NULL, 'contributor'),
                ('rubik',         '/api/databases',   'POST',   '/{id}/knowledge/import',     'write', NULL, 'contributor'),
                ('rubik',         '/api/databases',   'POST',   '/{id}/knowledge/export/stream', 'read', NULL, 'viewer'),
                ('rubik',         '/api/databases',   'POST',   '/{id}/skill/custom',    'write',  NULL, 'contributor'),
                ('rubik',         '/api/databases',   'POST',   '/{id}/sync',            'write',  NULL, 'contributor'),
                ('rubik',         '/api/databases',   'POST',   '/{id}/sync/stream',     'write',  NULL, 'contributor'),

                -- Rubik metadata：/init 名义是 GET 但实际会初始化（写），升级为 contributor
                ('rubik',         '/api/metadata',    'GET',    '/{id}/init',            'write',  NULL, 'contributor'),

                -- Rubik query：NL2SQL 对 body.database_id 做 viewer 检查
                ('rubik',         '/api/query',       'POST',   NULL,                    'read',   NULL, 'viewer'),

                -- Rubik sessions：CRUD 及子路径（owner-only，会话是用户私有资源）
                ('rubik',         '/api/sessions',    'POST',   NULL,                    'create', 201,  'none'),
                ('rubik',         '/api/sessions',    'DELETE', '/{id}',                 'delete', NULL, 'owner'),
                ('rubik',         '/api/sessions',    'GET',    '/{id}/replay',          'read',   NULL, 'owner'),
                ('rubik',         '/api/sessions',    'GET',    '/{id}/turns',           'read',   NULL, 'owner'),
                ('rubik',         '/api/sessions',    'POST',   '/{id}/turns/{turn_id}/feedback', 'write', NULL, 'owner')
            ON CONFLICT DO NOTHING
        """)

        # ---- Permission Groups seed ----
        # 统一模型：permission_group 是业务功能点，包含若干 (path, method)，绑定若干 Keycloak 组
        # 格式: (app_name, name, description, [(path_prefix, method), ...], [kc_group1, kc_group2, ...])
        # app_name='' 表示平台级（跨 app，例如 IAM 管理/ACL 分享）
        # method=None 表示匹配所有 HTTP 方法
        # Rego 用 OR 语义：任一 permission_group 命中即放行

        # === 平台级 + 全 app 管理 ===
        system_perm_groups = [
            ('',              'iam_admin',          'IAM 管理 API 全权',
                [('/api/v1/', None)], ['admins']),
            ('',              'acl_access',         'ACL 分享 API（admins + all-users，endpoint 内部再做 owner-only 校验）',
                [('/acl/v1/', None)], ['admins', 'all-users']),
            ('knowledgebase', 'kb_admin_full',      'KB 全应用访问（admins + kb-admins）',
                [('/kb/', None)], ['admins', 'kb-admins']),
            ('rubik',         'rubik_admin_full',   'Rubik 全应用访问（admins + rubik-admins）',
                [('/rubik/', None)], ['admins', 'rubik-admins']),
            ('memory',        'memory_admin_full',  'Memory 全应用访问（admins only — tenant 创建/系统恢复等超管功能）',
                [('/memory/', None)], ['admins']),
        ]

        # === KB 功能点（1 path → 1 permission_group，暂不分类）===
        # 注意 Rego OR 语义，prefix 必须精确，避免覆盖到 kb-admins 专属子路径
        kb_perm_groups = [
            # 读接口 → all-users（资源级 ACL / X-Allowed-Ids 再过滤）
            ('knowledgebase', 'kb_browse_knowledge_bases', 'KB 列表/单查/子路径读（含 mappings/files）',
                [('/kb/knowledge_bases', 'GET')], ['all-users']),
            ('knowledgebase', 'kb_browse_conversations',   '会话列表/查询',
                [('/kb/conversations', 'GET')], ['all-users']),
            ('knowledgebase', 'kb_browse_prompts',         '提示词只读（QA 场景要用）',
                [('/kb/prompts', 'GET')], ['all-users']),
            ('knowledgebase', 'kb_file_download',          '文件下载（父 KB 继承）',
                [('/kb/knowledge_bases/files/download', 'GET')], ['all-users']),
            ('knowledgebase', 'kb_image_download',         '图片访问（父 KB 继承）',
                [('/kb/conversations/images/download', 'GET')], ['all-users']),

            # 写接口 → all-users
            ('knowledgebase', 'kb_create',                 'KB 创建',
                [('/kb/knowledge_bases/add', 'POST')], ['all-users']),
            ('knowledgebase', 'kb_modify',                 'KB 修改（ACL contributor）',
                [('/kb/knowledge_bases/modify', 'POST')], ['all-users']),
            ('knowledgebase', 'kb_remove',                 'KB 删除（ACL owner）',
                [('/kb/knowledge_bases/remove', 'POST')], ['all-users']),
            ('knowledgebase', 'kb_mapping_write',          '目录映射写（/add, /remove）',
                [('/kb/knowledge_bases/mappings/', 'POST')], ['all-users']),
            ('knowledgebase', 'kb_file_upload',            '文件上传',
                [('/kb/knowledge_bases/files/upload', 'POST')], ['all-users']),
            ('knowledgebase', 'kb_file_delete',            '文件删除',
                [('/kb/knowledge_bases/files/remove', 'POST')], ['all-users']),
            ('knowledgebase', 'kb_conv_start',             '发起问答',
                [('/kb/conversations/start', 'POST')], ['all-users']),
            ('knowledgebase', 'kb_conv_stop',              '停止问答',
                [('/kb/conversations/stop', 'GET')], ['all-users']),
            ('knowledgebase', 'kb_conv_remove',            '删除会话',
                [('/kb/conversations/remove', 'POST')], ['all-users']),
            ('knowledgebase', 'kb_image_generate',         '生成图片链接',
                [('/kb/conversations/images/generate', 'POST')], ['all-users']),
            ('knowledgebase', 'kb_conv_query',             '历史对话 batch/single 查询',
                [('/kb/conversations/query/', 'GET')], ['all-users']),
            ('knowledgebase', 'kb_retrieval',              '检索融合搜索',
                [('/kb/retrieval/fusion_search', 'POST')], ['all-users']),

            # 管理员专属
            ('knowledgebase', 'kb_filesystem_manage',      '文件系统管理（容器级，非 KB 级）',
                [('/kb/knowledge_bases/files/filesystem', None)], ['kb-admins']),
            ('knowledgebase', 'kb_model_config_manage',    '模型配置管理',
                [('/kb/models/config', None)], ['kb-admins']),
            ('knowledgebase', 'kb_prompt_manage',          '提示词增删改',
                [('/kb/prompts', 'POST')], ['kb-admins']),
            ('knowledgebase', 'kb_jargon_group_manage',    '黑话库管理',
                [('/kb/jargon_groups', None)], ['kb-admins']),
            ('knowledgebase', 'kb_jargon_manage',          '黑话条目管理',
                [('/kb/jargons', None)], ['kb-admins']),
        ]

        # === Rubik 功能点（按 apioption.md option1-11 + default 聚合）===
        # 敏感子路径（data-dir, /knowledge/special）单独拆分给 rubik-admins
        rubik_perm_groups = [
            # option1: 数据库导入与删除
            ('rubik', 'rubik_db_import', 'option1: 导入/删除数据库',
                [('/rubik/api/databases',  'POST'),     # POST /api/databases
                 ('/rubik/api/databases/', 'DELETE')],  # DELETE /api/databases/{id}
                ['all-users']),

            # option2: 查看数据库（去掉敏感 data-dir）
            ('rubik', 'rubik_db_view', 'option2: 查看数据库',
                [('/rubik/api/databases',  'GET'),
                 ('/rubik/api/databases/', 'GET'),      # 详情 / check / schema / tables-columns / tables/{name}
                 ('/rubik/api/databases/', 'POST')],    # prettify-sql / execute-sql / test
                ['all-users']),

            # option2-sensitive: data-dir（单独给 rubik-admins）
            ('rubik', 'rubik_db_data_dir', 'option2 敏感：数据目录',
                [('/rubik/api/databases/config/data-dir', 'GET')],
                ['rubik-admins']),

            # option3: 知识库构建
            ('rubik', 'rubik_kb_build', 'option3: 知识库构建',
                [('/rubik/api/databases/', 'POST'),     # build / build/stream / build/cancel
                 ('/rubik/api/databases/', 'GET')],     # build/status
                ['all-users']),

            # option4: 知识增删（/knowledge/special 拆出）
            ('rubik', 'rubik_knowledge_edit', 'option4: 知识增删',
                [('/rubik/api/databases/', 'PUT'),      # knowledge/{type}/{id}
                 ('/rubik/api/databases/', 'DELETE'),   # knowledge/{type}/{id}
                 ('/rubik/api/databases/', 'POST')],    # taxonomy/custom/experience/import
                ['all-users']),
            ('rubik', 'rubik_knowledge_special', 'option4 敏感：跨库特殊知识',
                [('/rubik/api/databases/knowledge/special', 'POST')],
                ['rubik-admins']),

            # option5: 知识查看
            ('rubik', 'rubik_knowledge_view', 'option5: 知识查看',
                [('/rubik/api/databases/', 'GET'),      # knowledge/types/list/{type}/{id}/dict
                 ('/rubik/api/databases/', 'POST')],    # sync / sync/stream / knowledge/export/stream
                ['all-users']),

            # option6: 技能管理
            ('rubik', 'rubik_skill_manage', 'option6: 技能管理',
                [('/rubik/api/databases/', 'POST'),     # skill/custom
                 ('/rubik/api/databases/', 'PUT')],     # skill/{id}
                ['all-users']),

            # option7: 数据库元数据管理
            ('rubik', 'rubik_metadata', 'option7: 数据库元数据管理',
                [('/rubik/api/metadata/', 'GET'),       # /{db_id}/init, /{db_id}
                 ('/rubik/api/metadata/', 'PUT')],      # /description
                ['all-users']),

            # option8: 问数/会话
            ('rubik', 'rubik_query', 'option8: 问数/会话',
                [('/rubik/api/query',     'POST'),
                 ('/rubik/api/sessions',  'GET'),
                 ('/rubik/api/sessions',  'POST'),
                 ('/rubik/api/sessions/', 'GET'),       # replay, turns
                 ('/rubik/api/sessions/', 'POST'),      # replay, feedback
                 ('/rubik/api/sessions/', 'DELETE')],
                ['all-users']),

            # option9: 配置管理（读 all-users；写 rubik-admins）
            ('rubik', 'rubik_config_view', 'option9 读：配置查询',
                [('/rubik/api/config',  'GET'),
                 ('/rubik/api/config/', 'GET')],
                ['all-users']),
            ('rubik', 'rubik_config_manage', 'option9 写：配置管理',
                [('/rubik/api/config/', 'PUT'),         # models/{name}, database-providers/{provider}, language, app/{key}
                 ('/rubik/api/config/', 'POST'),        # reload, setup, open-path, llm-providers
                 ('/rubik/api/config/', 'DELETE')],     # llm-providers/{name}
                ['rubik-admins']),

            # option10: 查看仪表盘
            ('rubik', 'rubik_dashboard_view', 'option10: 查看仪表盘',
                [('/rubik/api/dashboards',  'GET'),
                 ('/rubik/api/dashboards/', 'GET')],
                ['all-users']),

            # option11: 增删仪表盘
            ('rubik', 'rubik_dashboard_edit', 'option11: 增删仪表盘',
                [('/rubik/api/dashboards',  'POST'),
                 ('/rubik/api/dashboards/', 'PUT'),
                 ('/rubik/api/dashboards/', 'DELETE')],
                ['all-users']),

            # default: 数据库刷新
            ('rubik', 'rubik_db_refresh', 'default: 数据库刷新',
                [('/rubik/api/refresh/', 'GET'),        # /status
                 ('/rubik/api/refresh/', 'POST')],      # /execute/stream
                ['all-users']),
        ]

        # === Memory 功能点 ===
        # 见 diagrams/api-specs/memory/api.md
        # 三层 RBAC（超级管理员 / 租户管理员 / 最终用户）：
        #   - admins                 → memory_admin_full（/memory/ 全路径，含 POST /tenants、/system/recovery）
        #   - memory-admins (租户管理员) → memory_tenant_admin：/tenants/ 子路径（诱导：trailing slash 排除
        #                                 POST /tenants 这个超管动作）+ /templates* + /memory/ 数据
        #   - all-users (最终用户)      → memory_user_data（/memory/ 数据） + memory_user_templates_read
        #                                 （GET /templates*） + memory_health（GET /health）
        # startswith OR 语义：memory_admin_full 路径留给 admins；tenants 创建、system 恢复等路径无其
        # 他 permission_group 覆盖 → 除 admins 以外 Default Deny。
        memory_perm_groups = [
            # --- 租户管理员（memory-admins）---
            ('memory', 'memory_tenant_admin', '租户管理：/tenants/* 子路径 + /templates* + /memory/* 数据',
                [('/memory/api/v1/tenants/',   None),   # trailing-/ 排除 POST /tenants 这个超管动作
                 ('/memory/api/v1/templates',  None),   # POST/GET/PUT/DELETE + /{id}/filters + /llm-extraction
                 ('/memory/api/v1/memory/',    None)],  # 数据面（add/query/update/delete）
                ['memory-admins']),

            # --- 最终用户（all-users）---
            ('memory', 'memory_user_data', '数据面：记忆增删改查（add/query/update/delete）',
                [('/memory/api/v1/memory/', None)],
                ['all-users']),
            ('memory', 'memory_user_templates_read', '数据面：模板只读（末端用户可查看已生效模板）',
                [('/memory/api/v1/templates', 'GET')],
                ['all-users']),
            ('memory', 'memory_health', '数据面：健康检查',
                [('/memory/api/v1/health', 'GET')],
                ['all-users']),
        ]

        all_perm_groups = system_perm_groups + kb_perm_groups + rubik_perm_groups + memory_perm_groups

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
        print(f"  IAM DB seeded ({len(system_perm_groups)} system + {len(kb_perm_groups)} KB "
              f"+ {len(rubik_perm_groups)} Rubik + {len(memory_perm_groups)} Memory permission_groups)",
              flush=True)
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
    ensure_group(token, REALM, "memory-admins")
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
