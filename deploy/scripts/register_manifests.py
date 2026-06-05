#!/usr/bin/env python3
"""Register MemoryBank, DataAgent manifests into the running cluster."""

import base64
import json
import os
import ssl
import subprocess
import urllib.error
import urllib.request

BASE_URL = os.environ.get("BASE_URL", "https://localhost:30080")
REALM = "aidp"


def build_url_context(base_url: str) -> ssl.SSLContext | None:
    if not base_url.startswith("https://"):
        return None
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


URL_CONTEXT = build_url_context(BASE_URL)


def get_client_secret() -> str:
    result = subprocess.run(
        [
            "kubectl",
            "-n",
            "aidp-iam",
            "get",
            "secret",
            "keycloak-aidp-client",
            "-o",
            "jsonpath={.data.client-secret}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return base64.b64decode(result.stdout.strip()).decode()


def get_admin_token(secret: str) -> str:
    data = (
        "client_id=aidp-client&grant_type=password&username=admin"
        f"&password=Admin@123&client_secret={secret}"
    )
    request = urllib.request.Request(
        f"{BASE_URL}/realms/{REALM}/protocol/openid-connect/token",
        data=data.encode(),
        method="POST",
    )
    response = json.loads(urllib.request.urlopen(request, context=URL_CONTEXT).read())
    return response["access_token"]


def put_manifest(token: str, namespace: str, manifest: dict) -> None:
    body = json.dumps(manifest).encode()
    request = urllib.request.Request(
        f"{BASE_URL}/AccessManager/Tenants/System/AppManifests/{namespace}",
        data=body,
        method="PUT",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        response = urllib.request.urlopen(request, context=URL_CONTEXT)
        print(f"  {namespace}: {response.status}")
    except urllib.error.HTTPError as exc:
        print(f"  {namespace}: ERROR {exc.code} {exc.read().decode()[:200]}")

MS_MANIFEST = {'namespace': 'MemoryBank',
 'display_name': '统一记忆管理',
 'base_url': 'http://mock-memory.mock-memory.svc.cluster.local:8080',
 'list_filter_mode': 'gateway_inject',
 'resources': [{'type': 'TemplatesDefaults',
                'display_name': '系统默认模板',
                'path_pattern': '/MemoryBank/Templates/Defaults/{templateName}',
                'methods': ['GET'],
                'actions': [],
                'default_acl': [{'user_template': 'AccessManager/Tenants/{tenantId}/Groups/all-users',
                                  'object_template': 'MemoryBank/Templates/Defaults',
                                  'role_path': 'AccessManager/Tenants/System/Roles/Viewer'},
                                 {'user_template': 'AccessManager/Tenants/{tenantId}/Groups/tenant-admins',
                                  'object_template': 'MemoryBank/Templates/Defaults',
                                  'role_path': 'AccessManager/Tenants/System/Roles/Owner'}],
                'children': []},
               {'type': 'Instances',
                'display_name': '记忆实例',
                'path_pattern': '/MemoryBank/Tenants/{tenantId}/Instances/{instanceName}',
                'methods': ['GET', 'PUT', 'DELETE'],
                'actions': [],
                'default_acl': [{'user_template': 'AccessManager/Tenants/{tenantId}/Groups/all-users',
                                 'object_template': 'MemoryBank/Tenants/{tenantId}/Instances',
                                 'role_path': 'AccessManager/Tenants/System/Roles/Contributor'},
                                {'user_template': 'AccessManager/Tenants/{tenantId}/Groups/tenant-admins',
                                 'object_template': 'MemoryBank/Tenants/{tenantId}/Instances',
                                 'role_path': 'AccessManager/Tenants/System/Roles/Owner'}],
                'children': [{'type': 'Memories',
                              'display_name': '记忆',
                              'path_pattern': (
                                  '/MemoryBank/Tenants/{tenantId}/Instances'
                                  '/{instanceName}/Memories/{memoryId}'
                              ),
                              'methods': ['GET', 'PUT', 'DELETE'],
                              'actions': [{'name': 'Query',
                                           'path_suffix': '/Query',
                                           'http_method': 'POST',
                                           'required_role': 'AccessManager/Tenants/System/Roles/Viewer'}],
                              'default_acl': [],
                              'children': []},
                             {'type': 'Templates',
                              'display_name': '记忆规则',
                              'path_pattern': (
                                  '/MemoryBank/Tenants/{tenantId}/Instances'
                                  '/{instanceName}/Templates/{templateName}'
                              ),
                              'methods': ['GET', 'PUT', 'PATCH', 'DELETE'],
                              'actions': [{'name': 'Filters',
                                           'path_suffix': '/Filters',
                                           'http_method': 'POST',
                                           'required_role': 'AccessManager/Tenants/System/Roles/Contributor'},
                                          {'name': 'LLMExtraction',
                                           'path_suffix': '/LLMExtraction',
                                           'http_method': 'POST',
                                           'required_role': 'AccessManager/Tenants/System/Roles/Contributor'}],
                              'default_acl': [],
                              'children': []}]}],
 'supported_roles': ['AccessManager/Tenants/System/Roles/Owner',
                     'AccessManager/Tenants/System/Roles/Contributor',
                     'AccessManager/Tenants/System/Roles/Viewer'],
 'custom_roles': []}

DA_MANIFEST = {'namespace': 'DataAgent',
 'display_name': '智能问数',
 'base_url': 'http://mock-dataagent.mock-dataagent.svc.cluster.local:8080',
 'resources': [{'type': 'Databases',
                'display_name': '数据库',
                'list_filter_mode': 'gateway_inject',
                'admin_bypass': False,
                'path_pattern': '/DataAgent/Tenants/{tenantId}/Databases/{db_id}',
                'methods': ['GET', 'PUT', 'DELETE'],
                'actions': [{'name': 'Test',
                             'path_suffix': '/Test',
                             'http_method': 'POST',
                             'required_role': 'AccessManager/Tenants/System/Roles/Contributor'},
                            {'name': 'Check',
                             'path_suffix': '/Check',
                             'http_method': 'POST',
                             'required_role': 'AccessManager/Tenants/System/Roles/Contributor'},
                            {'name': 'PrettifySql',
                             'path_suffix': '/PrettifySql',
                             'http_method': 'POST',
                             'required_role': 'AccessManager/Tenants/System/Roles/Contributor'},
                            {'name': 'ExecuteSql',
                             'path_suffix': '/ExecuteSql',
                             'http_method': 'POST',
                             'required_role': 'AccessManager/Tenants/System/Roles/Contributor'},
                            {'name': 'Build',
                             'path_suffix': '/Build',
                             'http_method': 'POST',
                             'required_role': 'AccessManager/Tenants/System/Roles/Contributor'},
                            {'name': 'StreamBuild',
                             'path_suffix': '/StreamBuild',
                             'http_method': 'POST',
                             'required_role': 'AccessManager/Tenants/System/Roles/Contributor'},
                            {'name': 'Cancel',
                             'path_suffix': '/Cancel',
                             'http_method': 'POST',
                             'required_role': 'AccessManager/Tenants/System/Roles/Contributor'},
                            {'name': 'CheckRefresh',
                             'path_suffix': '/CheckRefresh',
                             'http_method': 'POST',
                             'required_role': 'AccessManager/Tenants/System/Roles/Contributor'},
                            {'name': 'StreamRefresh',
                             'path_suffix': '/StreamRefresh',
                             'http_method': 'POST',
                             'required_role': 'AccessManager/Tenants/System/Roles/Contributor'}],
                'default_acl': [],
                'children': [{'type': 'Metadata',
                              'display_name': '元数据',
                              'list_filter_mode': 'gateway_inject',
                              'path_pattern': '/DataAgent/Tenants/{tenantId}/Databases/{db_id}/Metadata',
                              'methods': ['GET'],
                              'actions': [{'name': 'Init',
                                           'path_suffix': '/Init',
                                           'http_method': 'POST',
                                           'required_role': 'AccessManager/Tenants/System/Roles/Contributor'},
                                          {'name': 'Process',
                                           'path_suffix': '/Process',
                                           'http_method': 'POST',
                                           'required_role': 'AccessManager/Tenants/System/Roles/Contributor'}],
                              'default_acl': [],
                              'children': []},
                             {'type': 'Schema',
                              'display_name': '结构信息',
                              'list_filter_mode': 'gateway_inject',
                              'path_pattern': '/DataAgent/Tenants/{tenantId}/Databases/{db_id}/Schema',
                              'methods': ['GET'],
                              'actions': [],
                              'default_acl': [],
                              'children': []},
                             {'type': 'Columns',
                              'display_name': '列信息',
                              'list_filter_mode': 'gateway_inject',
                              'path_pattern': '/DataAgent/Tenants/{tenantId}/Databases/{db_id}/Columns',
                              'methods': ['GET'],
                              'actions': [],
                              'default_acl': [],
                              'children': []},
                             {'type': 'Tables',
                              'display_name': '表信息',
                              'list_filter_mode': 'gateway_inject',
                              'path_pattern': '/DataAgent/Tenants/{tenantId}/Databases/{db_id}/Tables',
                              'methods': ['GET'],
                              'actions': [],
                              'default_acl': [],
                              'children': []},
                             {'type': 'Knowledge',
                              'display_name': '知识',
                              'list_filter_mode': 'gateway_inject',
                              'path_pattern': '/DataAgent/Tenants/{tenantId}/Databases/{db_id}/Knowledge/{item_id_str}',
                              'methods': ['GET', 'PUT', 'PATCH', 'DELETE'],
                              'actions': [{'name': 'GetTypes',
                                           'path_suffix': '/GetTypes',
                                           'http_method': 'POST',
                                           'required_role': 'AccessManager/Tenants/System/Roles/Viewer'},
                                          {'name': 'Import',
                                           'path_suffix': '/Import',
                                           'http_method': 'POST',
                                           'required_role': 'AccessManager/Tenants/System/Roles/Contributor'},
                                          {'name': 'Export',
                                           'path_suffix': '/Export',
                                           'http_method': 'POST',
                                           'required_role': 'AccessManager/Tenants/System/Roles/Viewer'}],
                              'default_acl': [],
                              'children': []},
                             {'type': 'TaxonomyKL',
                              'display_name': '同义词知识',
                              'list_filter_mode': 'gateway_inject',
                              'path_pattern': (
                                  '/DataAgent/Tenants/{tenantId}/Databases'
                                  '/{db_id}/TaxonomyKL/{item_id_str}'
                              ),
                              'methods': ['PUT'],
                              'actions': [],
                              'default_acl': [],
                              'children': []},
                             {'type': 'CustomKL',
                              'display_name': '自定义知识',
                              'list_filter_mode': 'gateway_inject',
                              'path_pattern': '/DataAgent/Tenants/{tenantId}/Databases/{db_id}/CustomKL/{item_id_str}',
                              'methods': ['PUT'],
                              'actions': [],
                              'default_acl': [],
                              'children': []},
                             {'type': 'ExperienceKL',
                              'display_name': '经验知识',
                              'list_filter_mode': 'gateway_inject',
                              'path_pattern': (
                                  '/DataAgent/Tenants/{tenantId}/Databases'
                                  '/{db_id}/ExperienceKL/{item_id_str}'
                              ),
                              'methods': ['PUT'],
                              'actions': [],
                              'default_acl': [],
                              'children': []},
                             {'type': 'Skill',
                              'display_name': '技能知识',
                              'list_filter_mode': 'gateway_inject',
                              'path_pattern': '/DataAgent/Tenants/{tenantId}/Databases/{db_id}/Skill/{item_id_str}',
                              'methods': ['PUT', 'PATCH'],
                              'actions': [],
                              'default_acl': [],
                              'children': []},
                             {'type': 'LogicalColumnKL',
                              'display_name': '逻辑列知识',
                              'list_filter_mode': 'gateway_inject',
                              'path_pattern': (
                                  '/DataAgent/Tenants/{tenantId}/Databases'
                                  '/{db_id}/LogicalColumnKL/{item_id_str}'
                              ),
                              'methods': ['PUT', 'PATCH'],
                              'actions': [],
                              'default_acl': [],
                              'children': []}]},
               {'type': 'SpecialKL',
                'display_name': '特殊知识',
                'list_filter_mode': 'gateway_inject',
                'admin_bypass': False,
                'path_pattern': '/DataAgent/Tenants/{tenantId}/Databases/SpecialKL/{item_id_str}',
                'methods': ['GET', 'PUT', 'PATCH', 'DELETE'],
                'actions': [],
                'default_acl': [],
                'children': []},
               {'type': 'Sessions',
                'display_name': '会话',
                'list_filter_mode': 'gateway_inject',
                'path_pattern': '/DataAgent/Tenants/{tenantId}/Sessions/{session_id}',
                'methods': ['GET', 'PUT', 'DELETE'],
                'actions': [{'name': 'Replay',
                             'path_suffix': '/Replay',
                             'http_method': 'POST',
                             'required_role': 'AccessManager/Tenants/System/Roles/Contributor'}],
                'default_acl': [{'user_template': 'AccessManager/Tenants/{tenantId}/Groups/all-users',
                                 'object_template': 'DataAgent/Tenants/{tenantId}/Sessions',
                                 'role_path': 'AccessManager/Tenants/System/Roles/Contributor'},
                                {'user_template': 'AccessManager/Tenants/{tenantId}/Groups/tenant-admins',
                                 'object_template': 'DataAgent/Tenants/{tenantId}/Sessions',
                                 'role_path': 'AccessManager/Tenants/System/Roles/Owner'}],
                'children': [{'type': 'Turns',
                              'display_name': '会话轮次',
                              'list_filter_mode': 'gateway_inject',
                              'path_pattern': '/DataAgent/Tenants/{tenantId}/Sessions/{session_id}/Turns',
                              'methods': ['GET'],
                              'actions': [],
                              'default_acl': [],
                              'children': []}]},
               {'type': 'Dashboards',
                'display_name': 'Dashboard',
                'path_pattern': '/DataAgent/Tenants/{tenantId}/Dashboards/{dashboard_id}',
                'methods': ['GET', 'PUT', 'PATCH', 'DELETE'],
                'actions': [{'name': 'DraftSession',
                             'path_suffix': '/DraftSession',
                             'http_method': 'POST',
                             'required_role': 'AccessManager/Tenants/System/Roles/Owner'},
                            {'name': 'AddToDashboard',
                             'path_suffix': '/AddToDashboard',
                             'http_method': 'POST',
                             'required_role': 'AccessManager/Tenants/System/Roles/Owner'},
                            {'name': 'Find',
                             'path_suffix': '/Find',
                             'http_method': 'POST',
                             'required_role': 'AccessManager/Tenants/System/Roles/Owner'},
                            {'name': 'Import',
                             'path_suffix': '/Import',
                             'http_method': 'POST',
                             'required_role': 'AccessManager/Tenants/System/Roles/Owner'}],
                'default_acl': [],
                'children': [{'type': 'Summary',
                              'display_name': 'Dashboard摘要',
                              'path_pattern': (
                                  '/DataAgent/Tenants/{tenantId}/Dashboards'
                                  '/{dashboard_id}/Summary/{summary_id}'
                              ),
                              'methods': ['GET', 'PUT'],
                              'actions': [],
                              'default_acl': [],
                              'children': []},
                             {'type': 'Guidance',
                              'display_name': 'Dashboard引导摘要',
                              'path_pattern': (
                                  '/DataAgent/Tenants/{tenantId}/Dashboards'
                                  '/{dashboard_id}/Guidance/{guidance_id}'
                              ),
                              'methods': ['PUT'],
                              'actions': [],
                              'default_acl': [],
                              'children': []},
                             {'type': 'Share',
                              'display_name': '分享链接',
                              'path_pattern': (
                                  '/DataAgent/Tenants/{tenantId}/Dashboards'
                                  '/{dashboard_id}/Share/{share_id}'
                              ),
                              'methods': ['GET', 'PUT', 'DELETE'],
                              'actions': [],
                              'default_acl': [],
                              'children': []},
                             {'type': 'Charts',
                              'display_name': 'Charts',
                              'path_pattern': (
                                  '/DataAgent/Tenants/{tenantId}/Dashboards'
                                  '/{dashboard_id}/Charts/{chart_id}'
                              ),
                              'methods': ['GET'],
                              'actions': [],
                              'default_acl': [],
                              'children': []}]}],
 'supported_roles': ['AccessManager/Tenants/System/Roles/Owner',
                     'AccessManager/Tenants/System/Roles/Contributor',
                     'AccessManager/Tenants/System/Roles/Viewer'],
 'custom_roles': []}



if __name__ == "__main__":
    print("Getting admin token...")
    secret = get_client_secret()
    token = get_admin_token(secret)
    print(f"Token obtained (len={len(token)})")

    print("Registering manifests:")
    put_manifest(token, "MemoryBank", MS_MANIFEST)
    put_manifest(token, "DataAgent", DA_MANIFEST)

    print("\nVerifying registered manifests:")
    request = urllib.request.Request(
        f"{BASE_URL}/AccessManager/Tenants/System/AppManifests",
        headers={"Authorization": f"Bearer {token}"},
    )
    response = json.loads(urllib.request.urlopen(request, context=URL_CONTEXT).read())
    items = response if isinstance(response, list) else response.get("manifests", response.get("items", []))
    for item in items:
        print(f"  - {item.get('namespace')}")
