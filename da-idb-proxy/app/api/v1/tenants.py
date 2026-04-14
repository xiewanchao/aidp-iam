import os
from typing import List
from fastapi import APIRouter, status, Depends
from app.core.keycloak import kc
from app.schemas.realm import TenantCreate, TenantResponse, TenantListResponse
from app.api.v1.common import skip_master_realm

router = APIRouter(prefix="/tenants", tags=["Tenants"])

# 配置常量
PROTECTED_REALM = os.getenv("KC_REALM", "master")
NEW_CLIENT_ID = os.getenv("KC_NEW_CLIENT_ID", "data-agent")
MAPPER_PROVIDER_NAME = os.getenv("KC_SCRIPT_MAPPER", "Data Agent Mapper")
ADMIN_ROLE = os.getenv("DEFAULT_TENANT_ADMIN_ROLE", "tenant-admin")
ADMIN_USER = os.getenv("DEFAULT_TENANT_ADMIN_NAME", "tenant-admin")


# --- 内部辅助子函数 (减肥部分) ---

def _create_realm(realm: str, display_name: str):
    """1. 创建 Realm"""
    kc.request("POST", "/realms", json={
        "realm": realm, "displayName": display_name, "enabled": True, "sslRequired": "none"
    })


def _create_client_with_mapper(realm: str):
    """2. 创建默认 Client 并配置 Script Mapper"""
    client_payload = {
        "clientId": NEW_CLIENT_ID,
        "publicClient": True,
        "standardFlowEnabled": True,
        "redirectUris": ["*"],
        "webOrigins": ["*"]
    }

    # A. 创建 Client 并从 Response Header 获取 UUID
    resp = kc.request("POST", f"/realms/{realm}/clients", json=client_payload)

    # Keycloak 20+ 创建成功返回 201，并在 Location Header 提供完整 URL
    # 例如: .../admin/realms/my-realm/clients/6f2d00a6-2256-4e21-a4d1-132507765afb
    if resp.status_code == 201:
        location = resp.headers.get("Location")
        if location:
            client_uuid = location.split("/")[-1]  # 提取最后一段 UUID
        else:
            print(f"Warning: No Location header in create client response")
            client_uuid = None

        # B. 构造 Mapper 数组 (符合你找出来的 add-models 接口定义)
        mappers_payload = [
            {
                "name": "tenant-and-roles-injector",
                "protocol": "openid-connect",
                "protocolMapper": MAPPER_PROVIDER_NAME,
                "config": {
                    "access.token.claim": "true",
                    "id.token.claim": "true",
                    "userinfo.token.claim": "true",
                    "jsonType.label": "String"
                }
            },
            {
                "name": "groups",
                "protocol": "openid-connect",
                "protocolMapper": "oidc-group-membership-mapper",
                "config": {
                    "full.path": "false",
                    "id.token.claim": "true",
                    "access.token.claim": "true",
                    "userinfo.token.claim": "true",
                    "claim.name": "groups"
                }
            }
        ]

        # C. 调用 add-models 接口批量添加 (注意路径中是 client_uuid)
        mapper_path = f"/realms/{realm}/clients/{client_uuid}/protocol-mappers/add-models"
        mapper_resp = kc.request("POST", mapper_path, json=mappers_payload)

        if mapper_resp.status_code == 204:
            print(f"Successfully configured script mapper for client {NEW_CLIENT_ID} in {realm}")
    else:
        # 如果创建失败（比如已存在），记录错误或处理
        print(f"Failed to create client: {resp.status_code} - {resp.text}")


def _create_groups(realm: str):
    """3. 创建 tenant-admins 和 all-users 组，all-users 设为默认组"""
    for group_name in ["tenant-admins", "all-users"]:
        try:
            kc.request("POST", f"/realms/{realm}/groups", json={"name": group_name})
        except Exception:
            pass  # 409 Conflict = already exists

    # 将 all-users 设为默认组：新用户自动加入
    groups = kc.request("GET", f"/realms/{realm}/groups", params={"search": "all-users"}).json()
    for g in groups:
        if g["name"] == "all-users":
            kc.request("PUT", f"/realms/{realm}/default-groups/{g['id']}")
            break


def _setup_admin_roles(realm: str):
    """4. 获取管理权限并创建复合角色"""
    # 获取 realm-management 的 UUID
    mgmts = kc.request("GET", f"/realms/{realm}/clients", params={"clientId": "realm-management"}).json()
    mgmt_uuid = mgmts[0]['id']

    # 获取并过滤权限
    all_roles = kc.request("GET", f"/realms/{realm}/clients/{mgmt_uuid}/roles").json()
    targets = ["manage-realm", "manage-identity-providers", "manage-users", "view-users", "query-users"]
    selected = [r for r in all_roles if r['name'] in targets]

    # 创建租户管理员角色并绑定
    kc.request("POST", f"/realms/{realm}/roles", json={"name": ADMIN_ROLE})
    kc.request("POST", f"/realms/{realm}/roles/{ADMIN_ROLE}/composites", json=selected)


def _create_admin_user(realm: str):
    """5 & 6. 创建管理员用户，分配角色，并加入 tenant-admins 组"""
    # 创建用户
    kc.request("POST", f"/realms/{realm}/users", json={"username": ADMIN_USER, "enabled": True})

    # 查询 ID
    users = kc.request("GET", f"/realms/{realm}/users", params={"username": ADMIN_USER}).json()
    if not users: return
    uid = users[0]["id"]

    # 设置初始密码 (与 realm 同名)
    kc.request("PUT", f"/realms/{realm}/users/{uid}/reset-password", json={
        "type": "password", "value": realm, "temporary": True
    })

    # 绑定角色
    role_obj = kc.request("GET", f"/realms/{realm}/roles/{ADMIN_ROLE}").json()
    kc.request("POST", f"/realms/{realm}/users/{uid}/role-mappings/realm", json=[role_obj])

    # 将 tenant-admin 用户加入 tenant-admins 组
    groups = kc.request("GET", f"/realms/{realm}/groups", params={"search": "tenant-admins"}).json()
    for g in groups:
        if g["name"] == "tenant-admins":
            kc.request("PUT", f"/realms/{realm}/users/{uid}/groups/{g['id']}")
            break


def _disable_review_profile(realm: str):
    """7 & 8. 禁用 First Broker Login 的 Review Profile 步骤"""
    path = f"/realms/{realm}/authentication/flows/first%20broker%20login/executions"
    executions = kc.request("GET", path).json()
    for ex in executions:
        if ex.get('providerId') == 'idp-review-profile':
            ex['requirement'] = 'DISABLED'
            kc.request("PUT", path, json=ex)
            break


# --- 主 API 路由 ---

# Single-tenant model (per diagrams/ui-wireframes.md):
#   - `POST /api/v1/tenants` and `DELETE /api/v1/tenants/{realm}` are removed.
#     Only the fixed `aidp` realm exists; mutating realms is not exposed.
#   - `GET /api/v1/tenants` is kept as an informational endpoint that returns
#     the list of realms (always one entry on a fresh deployment), so existing
#     UIs and tests can still discover the realm name.

@router.get("", response_model=List[TenantListResponse])
def list_tenants():
    """
    Single-tenant model: always return the configured aidp realm.
    The aidp-client service account only has admin rights inside `aidp`,
    so we don't go to Keycloak's master `/realms` API.
    """
    realm = os.getenv("KC_REALM", "aidp")
    return [{"realm": realm, "id": realm, "displayName": "AIDP IAM", "enabled": True}]
