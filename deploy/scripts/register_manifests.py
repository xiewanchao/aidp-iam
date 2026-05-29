#!/usr/bin/env python3
"""Register KnowledgeBase, MemoryStore, DataAgent manifests into the running cluster."""
import subprocess, json, urllib.request, sys

BASE_URL = "http://localhost:30085"
REALM = "aidp"

def get_client_secret():
    r = subprocess.run(
        ["kubectl", "-n", "aidp-iam", "get", "secret", "keycloak-aidp-client",
         "-o", "jsonpath={.data.client-secret}"],
        capture_output=True, text=True)
    import base64
    return base64.b64decode(r.stdout.strip()).decode()

def get_admin_token(secret):
    data = f"client_id=aidp-client&grant_type=password&username=admin&password=Admin@123&client_secret={secret}"
    req = urllib.request.Request(
        f"{BASE_URL}/realms/{REALM}/protocol/openid-connect/token",
        data=data.encode(), method="POST")
    resp = json.loads(urllib.request.urlopen(req).read())
    return resp["access_token"]

def put_manifest(token, namespace, manifest):
    body = json.dumps(manifest).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/AccessManager/Tenants/System/AppManifests/{namespace}",
        data=body, method="PUT",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req)
        print(f"  {namespace}: {resp.status}")
    except urllib.error.HTTPError as e:
        print(f"  {namespace}: ERROR {e.code} {e.read().decode()[:200]}")

KB_MANIFEST = {
  "namespace": "KnowledgeBase",
  "display_name": "Knowledge Base",
  "base_url": "http://mock-kb.mock-kb.svc.cluster.local:8080",
  "resources": [
    {
      "type": "KnowledgeBases",
      "path_pattern": "/KnowledgeBase/Tenants/{tenantId}/KnowledgeBases/{kbId}",
      "methods": ["GET", "POST", "PUT", "DELETE"],
      "actions": [],
      "default_acl": [
        {"user_template": "AccessManager/Tenants/{tenantId}/Groups/all-users",
         "object_template": "KnowledgeBase/Tenants/{tenantId}/KnowledgeBases",
         "role_path": "AccessManager/Tenants/System/Roles/Contributor"}
      ],
      "children": [
        {"type": "Mappings", "path_pattern": "/KnowledgeBase/Tenants/{tenantId}/KnowledgeBases/{kbId}/Mappings/{mappingId}",
         "methods": ["GET", "POST", "DELETE"], "actions": [], "default_acl": [], "children": []},
        {"type": "Files", "path_pattern": "/KnowledgeBase/Tenants/{tenantId}/KnowledgeBases/{kbId}/Files/{fileId}",
         "methods": ["GET", "POST", "DELETE"], "actions": [], "default_acl": [], "children": []}
      ]
    },
    {
      "type": "Conversations",
      "path_pattern": "/KnowledgeBase/Tenants/{tenantId}/Conversations/{threadId}",
      "methods": ["GET", "POST", "DELETE"],
      "actions": [
        {"name": "Stop", "path_suffix": "/Stop", "http_method": "POST",
         "required_role": "AccessManager/Tenants/System/Roles/Owner"}
      ],
      "default_acl": [
        {"user_template": "AccessManager/Tenants/{tenantId}/Groups/all-users",
         "object_template": "KnowledgeBase/Tenants/{tenantId}/Conversations",
         "role_path": "AccessManager/Tenants/System/Roles/Contributor"}
      ],
      "children": []
    },
    {
      "type": "ModelConfigs",
      "path_pattern": "/KnowledgeBase/Tenants/System/ModelConfigs/{modelId}",
      "methods": ["GET", "POST", "PUT", "DELETE"],
      "actions": [], "default_acl": [], "children": []
    },
    {
      "type": "Prompts",
      "path_pattern": "/KnowledgeBase/Tenants/System/Prompts/{promptId}",
      "methods": ["GET", "POST", "PUT", "DELETE"],
      "actions": [], "default_acl": [], "children": []
    },
    {
      "type": "JargonLibraries",
      "path_pattern": "/KnowledgeBase/Tenants/{tenantId}/JargonLibraries/{libName}",
      "methods": ["GET", "POST", "DELETE"],
      "actions": [], "default_acl": [],
      "children": [
        {"type": "Jargons",
         "path_pattern": "/KnowledgeBase/Tenants/{tenantId}/JargonLibraries/{libName}/Jargons/{jargonName}",
         "methods": ["GET", "POST", "PUT", "DELETE"], "actions": [], "default_acl": [], "children": []}
      ]
    }
  ]
}

MS_MANIFEST = {
  "namespace": "MemoryStore",
  "display_name": "统一记忆管理",
  "base_url": "http://mock-memory.mock-memory.svc.cluster.local:8080",
  "list_filter_mode": "gateway_inject",
  "resources": [
    {
      "type": "Instances", "display_name": "记忆实例",
      "path_pattern": "/MemoryStore/Tenants/{tenantId}/Instances/{instanceName}",
      "methods": ["GET", "PUT", "DELETE"],
      "actions": [],
      "default_acl": [
        {"user_template": "AccessManager/Tenants/{tenantId}/Groups/all-users",
         "object_template": "MemoryStore/Tenants/{tenantId}/Instances",
         "role_path": "AccessManager/Tenants/System/Roles/Contributor"},
        {"user_template": "AccessManager/Tenants/{tenantId}/Groups/tenant-admins",
         "object_template": "MemoryStore/Tenants/{tenantId}/Instances",
         "role_path": "AccessManager/Tenants/System/Roles/Owner"}
      ],
      "children": [
        {
          "type": "Memories", "display_name": "记忆",
          "path_pattern": "/MemoryStore/Tenants/{tenantId}/Instances/{instanceName}/Memories/{memoryId}",
          "methods": ["GET", "PUT", "DELETE"],
          "actions": [{"name": "Query", "path_suffix": "/Query", "http_method": "POST",
                       "required_role": "AccessManager/Tenants/System/Roles/Viewer"}],
          "default_acl": [], "children": []
        },
        {
          "type": "Templates", "display_name": "记忆规则",
          "path_pattern": "/MemoryStore/Tenants/{tenantId}/Instances/{instanceName}/Templates/{templateName}",
          "methods": ["GET", "PUT", "PATCH", "DELETE"],
          "actions": [
            {"name": "Filters", "path_suffix": "/Filters", "http_method": "POST",
             "required_role": "AccessManager/Tenants/System/Roles/Contributor"},
            {"name": "LLMExtraction", "path_suffix": "/LLMExtraction", "http_method": "POST",
             "required_role": "AccessManager/Tenants/System/Roles/Contributor"}
          ],
          "default_acl": [], "children": []
        }
      ]
    }
  ],
  "supported_roles": [
    "AccessManager/Tenants/System/Roles/Owner",
    "AccessManager/Tenants/System/Roles/Contributor",
    "AccessManager/Tenants/System/Roles/Viewer"
  ],
  "custom_roles": []
}

DA_MANIFEST = {
  "namespace": "DataAgent",
  "display_name": "智能问数",
  "base_url": "http://mock-dataagent.mock-dataagent.svc.cluster.local:8080",
  "resources": [
    {
      "type": "Databases", "display_name": "数据库", "list_filter_mode": "gateway_inject",
      "admin_bypass": False,
      "path_pattern": "/DataAgent/Tenants/{tenantId}/Databases/{db_id}",
      "methods": ["GET", "PUT", "DELETE"],
      "actions": [
        {"name": "Test",          "path_suffix": "/Test",          "http_method": "POST", "required_role": "AccessManager/Tenants/System/Roles/Contributor"},
        {"name": "Check",         "path_suffix": "/Check",         "http_method": "POST", "required_role": "AccessManager/Tenants/System/Roles/Contributor"},
        {"name": "PrettifySql",   "path_suffix": "/PrettifySql",   "http_method": "POST", "required_role": "AccessManager/Tenants/System/Roles/Contributor"},
        {"name": "ExecuteSql",    "path_suffix": "/ExecuteSql",    "http_method": "POST", "required_role": "AccessManager/Tenants/System/Roles/Contributor"},
        {"name": "Build",         "path_suffix": "/Build",         "http_method": "POST", "required_role": "AccessManager/Tenants/System/Roles/Contributor"},
        {"name": "StreamBuild",   "path_suffix": "/StreamBuild",   "http_method": "POST", "required_role": "AccessManager/Tenants/System/Roles/Contributor"},
        {"name": "Cancel",        "path_suffix": "/Cancel",        "http_method": "POST", "required_role": "AccessManager/Tenants/System/Roles/Contributor"},
        {"name": "CheckRefresh",  "path_suffix": "/CheckRefresh",  "http_method": "POST", "required_role": "AccessManager/Tenants/System/Roles/Contributor"},
        {"name": "StreamRefresh", "path_suffix": "/StreamRefresh", "http_method": "POST", "required_role": "AccessManager/Tenants/System/Roles/Contributor"}
      ],
      "default_acl": [],
      "children": [
        {"type": "Metadata",       "display_name": "元数据",    "list_filter_mode": "gateway_inject", "path_pattern": "/DataAgent/Tenants/{tenantId}/Databases/{db_id}/Metadata",                         "methods": ["GET"],                       "actions": [{"name":"Init","path_suffix":"/Init","http_method":"POST","required_role":"AccessManager/Tenants/System/Roles/Contributor"},{"name":"Process","path_suffix":"/Process","http_method":"POST","required_role":"AccessManager/Tenants/System/Roles/Contributor"}], "default_acl": [], "children": []},
        {"type": "Schema",         "display_name": "结构信息",  "list_filter_mode": "gateway_inject", "path_pattern": "/DataAgent/Tenants/{tenantId}/Databases/{db_id}/Schema",                            "methods": ["GET"],                       "actions": [], "default_acl": [], "children": []},
        {"type": "Columns",        "display_name": "列信息",    "list_filter_mode": "gateway_inject", "path_pattern": "/DataAgent/Tenants/{tenantId}/Databases/{db_id}/Columns",                           "methods": ["GET"],                       "actions": [], "default_acl": [], "children": []},
        {"type": "Tables",         "display_name": "表信息",    "list_filter_mode": "gateway_inject", "path_pattern": "/DataAgent/Tenants/{tenantId}/Databases/{db_id}/Tables",                            "methods": ["GET"],                       "actions": [], "default_acl": [], "children": []},
        {"type": "Knowledge",      "display_name": "知识",      "list_filter_mode": "gateway_inject", "path_pattern": "/DataAgent/Tenants/{tenantId}/Databases/{db_id}/Knowledge/{item_id_str}",           "methods": ["GET","PUT","PATCH","DELETE"], "actions": [{"name":"GetTypes","path_suffix":"/GetTypes","http_method":"POST","required_role":"AccessManager/Tenants/System/Roles/Viewer"},{"name":"Import","path_suffix":"/Import","http_method":"POST","required_role":"AccessManager/Tenants/System/Roles/Contributor"},{"name":"Export","path_suffix":"/Export","http_method":"POST","required_role":"AccessManager/Tenants/System/Roles/Viewer"}], "default_acl": [], "children": []},
        {"type": "TaxonomyKL",     "display_name": "同义词知识","list_filter_mode": "gateway_inject", "path_pattern": "/DataAgent/Tenants/{tenantId}/Databases/{db_id}/TaxonomyKL/{item_id_str}",          "methods": ["PUT"],                       "actions": [], "default_acl": [], "children": []},
        {"type": "CustomKL",       "display_name": "自定义知识","list_filter_mode": "gateway_inject", "path_pattern": "/DataAgent/Tenants/{tenantId}/Databases/{db_id}/CustomKL/{item_id_str}",            "methods": ["PUT"],                       "actions": [], "default_acl": [], "children": []},
        {"type": "ExperienceKL",   "display_name": "经验知识",  "list_filter_mode": "gateway_inject", "path_pattern": "/DataAgent/Tenants/{tenantId}/Databases/{db_id}/ExperienceKL/{item_id_str}",        "methods": ["PUT"],                       "actions": [], "default_acl": [], "children": []},
        {"type": "Skill",          "display_name": "技能知识",  "list_filter_mode": "gateway_inject", "path_pattern": "/DataAgent/Tenants/{tenantId}/Databases/{db_id}/Skill/{item_id_str}",               "methods": ["PUT","PATCH"],               "actions": [], "default_acl": [], "children": []},
        {"type": "LogicalColumnKL","display_name": "逻辑列知识","list_filter_mode": "gateway_inject", "path_pattern": "/DataAgent/Tenants/{tenantId}/Databases/{db_id}/LogicalColumnKL/{item_id_str}",     "methods": ["PUT","PATCH"],               "actions": [], "default_acl": [], "children": []}
      ]
    },
    {
      "type": "SpecialKL", "display_name": "特殊知识", "list_filter_mode": "gateway_inject",
      "admin_bypass": False,
      "path_pattern": "/DataAgent/Tenants/{tenantId}/Databases/SpecialKL/{item_id_str}",
      "methods": ["GET", "PUT", "PATCH", "DELETE"], "actions": [], "default_acl": [], "children": []
    },
    {
      "type": "Sessions", "display_name": "会话", "list_filter_mode": "gateway_inject",
      "path_pattern": "/DataAgent/Tenants/{tenantId}/Sessions/{session_id}",
      "methods": ["GET", "PUT", "DELETE"],
      "actions": [{"name": "Replay", "path_suffix": "/Replay", "http_method": "POST",
                   "required_role": "AccessManager/Tenants/System/Roles/Contributor"}],
      "default_acl": [
        {"user_template": "AccessManager/Tenants/{tenantId}/Groups/all-users",
         "object_template": "DataAgent/Tenants/{tenantId}/Sessions",
         "role_path": "AccessManager/Tenants/System/Roles/Contributor"},
        {"user_template": "AccessManager/Tenants/{tenantId}/Groups/tenant-admins",
         "object_template": "DataAgent/Tenants/{tenantId}/Sessions",
         "role_path": "AccessManager/Tenants/System/Roles/Owner"}
      ],
      "children": [
        {"type": "Turns", "display_name": "会话轮次", "list_filter_mode": "gateway_inject",
         "path_pattern": "/DataAgent/Tenants/{tenantId}/Sessions/{session_id}/Turns",
         "methods": ["GET"], "actions": [], "default_acl": [], "children": []}
      ]
    },
    {
      "type": "Dashboards", "display_name": "Dashboard",
      "path_pattern": "/DataAgent/Tenants/{tenantId}/Dashboards/{dashboard_id}",
      "methods": ["GET", "PUT", "PATCH", "DELETE"],
      "actions": [
        {"name": "DraftSession",   "path_suffix": "/DraftSession",   "http_method": "POST", "required_role": "AccessManager/Tenants/System/Roles/Owner"},
        {"name": "AddToDashboard", "path_suffix": "/AddToDashboard", "http_method": "POST", "required_role": "AccessManager/Tenants/System/Roles/Owner"},
        {"name": "Find",           "path_suffix": "/Find",           "http_method": "POST", "required_role": "AccessManager/Tenants/System/Roles/Owner"},
        {"name": "Import",         "path_suffix": "/Import",         "http_method": "POST", "required_role": "AccessManager/Tenants/System/Roles/Owner"}
      ],
      "default_acl": [],
      "children": [
        {"type": "Summary",  "display_name": "Dashboard摘要",    "path_pattern": "/DataAgent/Tenants/{tenantId}/Dashboards/{dashboard_id}/Summary/{summary_id}",   "methods": ["GET","PUT"],           "actions": [], "default_acl": [], "children": []},
        {"type": "Guidance", "display_name": "Dashboard引导摘要","path_pattern": "/DataAgent/Tenants/{tenantId}/Dashboards/{dashboard_id}/Guidance/{guidance_id}", "methods": ["PUT"],                 "actions": [], "default_acl": [], "children": []},
        {"type": "Share",    "display_name": "分享链接",          "path_pattern": "/DataAgent/Tenants/{tenantId}/Dashboards/{dashboard_id}/Share/{share_id}",       "methods": ["GET","PUT","DELETE"],  "actions": [], "default_acl": [], "children": []},
        {"type": "Charts",   "display_name": "Charts",            "path_pattern": "/DataAgent/Tenants/{tenantId}/Dashboards/{dashboard_id}/Charts/{chart_id}",      "methods": ["GET"],                 "actions": [], "default_acl": [], "children": []}
      ]
    }
  ],
  "supported_roles": [
    "AccessManager/Tenants/System/Roles/Owner",
    "AccessManager/Tenants/System/Roles/Contributor",
    "AccessManager/Tenants/System/Roles/Viewer"
  ],
  "custom_roles": []
}

if __name__ == "__main__":
    print("Getting admin token...")
    secret = get_client_secret()
    token = get_admin_token(secret)
    print(f"Token obtained (len={len(token)})")

    print("Registering manifests:")
    put_manifest(token, "KnowledgeBase", KB_MANIFEST)
    put_manifest(token, "MemoryStore", MS_MANIFEST)
    put_manifest(token, "DataAgent", DA_MANIFEST)

    print("\nVerifying registered manifests:")
    req = urllib.request.Request(
        f"{BASE_URL}/AccessManager/Tenants/System/AppManifests",
        headers={"Authorization": f"Bearer {token}"})
    resp = json.loads(urllib.request.urlopen(req).read())
    items = resp if isinstance(resp, list) else resp.get("manifests", resp.get("items", []))
    for m in items:
        print(f"  - {m.get('namespace')}")
