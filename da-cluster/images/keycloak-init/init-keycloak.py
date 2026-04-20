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
            INSERT INTO apps (app_name, path_prefix, display_name, description, admin_group, enabled) VALUES
                ('knowledgebase', '/kb/',       '知识库',               '知识库管理系统（知识库/文件/问答/模型/提示词/黑话）', 'kb-admins',    true),
                ('rubik',         '/rubik/',    '智能问数 (RubikSQL)',  '自然语言转SQL查询平台（数据库/知识库/会话/查询）',     'rubik-admins', true),
                ('memory',        '/memory/',   '记忆库',               'Memory service',                                     'memory-admins', true),
                ('httpbin',       '/anything/', 'HTTPBin Echo',         'Test backend',                                       NULL,            true)
            ON CONFLICT (app_name) DO UPDATE SET
                path_prefix = EXCLUDED.path_prefix,
                display_name = EXCLUDED.display_name,
                description = EXCLUDED.description,
                admin_group = EXCLUDED.admin_group
        """)

        # ---- Resource Patterns ----
        cur.execute("""
            INSERT INTO resource_patterns (app_name, resource_prefix, resource_type, id_source, id_field) VALUES
                ('knowledgebase', '/knowledge_bases', 'kb',       'body', 'KDSID'),
                ('rubik',         '/api/databases',   'database', 'path', 'id'),
                ('rubik',         '/api/metadata',    'database', 'path', 'id'),          -- 元数据复用 database 鉴权
                ('rubik',         '/api/query',       'database', 'body', 'database_id'), -- NL2SQL 对 body.database_id 鉴权
                ('rubik',         '/api/sessions',    'session',  'path', 'id'),          -- 会话为独立资源类型
                ('memory',        '/v1/memories',     'memory',   'path', 'id'),
                ('httpbin',       '/items',           'item',     'path', 'id')
            ON CONFLICT (app_name, resource_prefix) DO UPDATE SET
                id_source = EXCLUDED.id_source, id_field = EXCLUDED.id_field
        """)

        # ---- Resource Actions (non-standard RESTful) ----
        # 默认 method→permission 映射：GET→viewer, POST(create)→none, PUT/PATCH→contributor, DELETE→owner
        # 这里只写"偏离默认"的规则。
        cur.execute("""
            INSERT INTO resource_actions (app_name, resource_prefix, method, path_suffix, action, success_status, min_permission) VALUES
                -- KB：/add 是 create（新资源，none）；/remove 是 delete（owner）
                ('knowledgebase', '/knowledge_bases', 'POST',   '/add',    'create', 201,  'none'),
                ('knowledgebase', '/knowledge_bases', 'POST',   '/remove', 'delete', NULL, 'owner'),

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

        # ---- Path Rules + Groups (多对多) ----
        # 格式: (path_prefix, method, group, description)
        # 策略 Default Deny：
        #   - kb-admins / rubik-admins 等 app-admin 组由 Rego 直接放行其应用全部路径，
        #     这里只写 all-users（普通登录用户）放行清单，以及 rubik-admins 全局配置专属路径。
        #   - 未命中规则 → 403

        # === KB ===
        # KB 所有写操作都用 POST，且是敏感操作（知识库/模型/提示词/黑话维护）
        # 历史上完全限定为 kb-admins。新设计下：kb-admins 通过 Rego app-admin 旁路全放行。
        # 普通 all-users 可读全部路径 + 创建自己的知识库（POST /knowledge_bases/add）
        kb_rules = [
            # 读：all-users 可 GET 任何 /kb/* 路径（资源级 ACL 仍生效）
            ('/kb/', 'GET', 'all-users', '读 KB 任何路径（由 resource_acl 过滤）'),
            # 写：仅允许 all-users 创建新知识库；其余写操作由 kb-admins 旁路处理
            ('/kb/knowledge_bases/add', 'POST', 'all-users', '创建知识库（创建者成为 owner）'),
            # /kb/knowledge_bases/ 下的 modify/remove/mappings/files 等写操作：
            # 需要业务侧后续在 resource_actions 中定义 min_permission=contributor/owner，
            # 再放开 all-users 的 POST。本期保持 kb-admins 旁路独占写。
        ]

        # === Rubik ===
        # 哲学：
        #   - Database、Session 是用户可创建/拥有的资源：path 层放 all-users，由 resource_acl 过滤
        #   - 全局配置（/api/config/*、/api/databases/config/*、knowledge/special）：rubik-admins 专属
        rubik_rules = [
            # ---- 读接口 → all-users ----
            ('/rubik/api/databases',      'GET',  'all-users', '数据库列表（X-Allowed-Ids 过滤）'),
            ('/rubik/api/databases/',     'GET',  'all-users', '数据库详情及子路径读取'),
            ('/rubik/api/metadata/',      'GET',  'all-users', '元数据读取'),
            ('/rubik/api/sessions',       'GET',  'all-users', '会话列表（X-Allowed-Ids 过滤）'),
            ('/rubik/api/sessions/',      'GET',  'all-users', '会话详情及子路径'),
            ('/rubik/api/config',         'GET',  'all-users', '读总配置'),
            ('/rubik/api/config/',        'GET',  'all-users', '读配置子项（models/language/llm-providers 等）'),

            # ---- 写接口 → all-users (资源级 ACL 把关 owner/contributor) ----
            ('/rubik/api/databases',      'POST', 'all-users', '创建数据库（创建者 owner）'),
            ('/rubik/api/databases/',     'POST',   'all-users', '数据库写操作（build/sync/knowledge/skill 等，resource_actions 指定最低权限）'),
            ('/rubik/api/databases/',     'PUT',    'all-users', '数据库修改（默认 contributor）'),
            ('/rubik/api/databases/',     'DELETE', 'all-users', '数据库/子资源删除（默认 owner）'),
            ('/rubik/api/metadata/',      'PUT',    'all-users', '元数据描述更新'),
            ('/rubik/api/query',          'POST', 'all-users', 'NL2SQL 查询（对 body.database_id 做 ACL 检查）'),
            ('/rubik/api/sessions',       'POST', 'all-users', '创建会话（创建者 owner）'),
            ('/rubik/api/sessions/',      'POST',   'all-users', '会话回放/反馈（owner 才能操作）'),
            ('/rubik/api/sessions/',      'DELETE', 'all-users', '删除会话（owner）'),

            # ---- 全局配置 → rubik-admins 专属（其实 rubik-admins 已被 Rego 旁路放行，这里显式写入是给 UI 展示）
            ('/rubik/api/databases/config/data-dir',   'GET',  'rubik-admins', '数据目录（敏感）'),
            ('/rubik/api/databases/knowledge/special', 'POST', 'rubik-admins', '特殊知识（跨库全局）'),
            ('/rubik/api/config/models/',              'PUT',  'rubik-admins', '更新模型预设'),
            ('/rubik/api/config/database-providers/',  'PUT',  'rubik-admins', '更新数据库提供者'),
            ('/rubik/api/config/language',             'PUT',  'rubik-admins', '设置语言'),
            ('/rubik/api/config/languages',            'PUT',  'rubik-admins', '分别设置 app/query 语言'),
            ('/rubik/api/config/app/',                 'PUT',  'rubik-admins', '设置应用配置'),
            ('/rubik/api/config/reload',               'POST', 'rubik-admins', '重载配置'),
            ('/rubik/api/config/setup',                'POST', 'rubik-admins', '初始化/重置配置'),
            ('/rubik/api/config/open-path',            'POST', 'rubik-admins', '打开路径（敏感）'),
            ('/rubik/api/config/llm-providers',        'POST', 'rubik-admins', '创建 LLM 提供者'),
            ('/rubik/api/config/llm-providers/',       'PUT',    'rubik-admins', '更新 LLM 提供者'),
            ('/rubik/api/config/llm-providers/',       'DELETE', 'rubik-admins', '删除 LLM 提供者'),
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
