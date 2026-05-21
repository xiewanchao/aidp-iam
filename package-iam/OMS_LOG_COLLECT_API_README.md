# AIDP IAM OMS Log Collect Callback API

IAM follows the OMS component callback model. IAM registers the following callback URLs to OMS, and OMS calls them through the cluster-internal Service:

```text
http://keycloak-proxy.aidp-iam.svc.cluster.local:8090
```

## Automatic OMS Registration

`keycloak-proxy` can register IAM log types to OMS automatically on startup. It is disabled by default so deployments without OMS are not affected. Enable it through Helm:

```bash
helm upgrade aidp-iam package-iam/charts/aidp-iam \
  -n aidp-iam \
  --reuse-values \
  --set iam-app.logCollect.registration.enabled=true \
  --set-string iam-app.logCollect.registration.registerUrl="http://<oms-service>.<oms-namespace>.svc.cluster.local:<port>/log/type/register/internal"
```

The registered callback URLs default to:

```text
http://keycloak-proxy.<iam-namespace>.svc.cluster.local:8090/AccessManager/Tenants/System/LogCollect/Dispatch
http://keycloak-proxy.<iam-namespace>.svc.cluster.local:8090/AccessManager/Tenants/System/LogCollect/Progress
http://keycloak-proxy.<iam-namespace>.svc.cluster.local:8090/AccessManager/Tenants/System/LogCollect/Nodes
```

If the OMS registration endpoint itself requires a bearer token, set:

```bash
--set-string iam-app.logCollect.registration.registerAuthToken="<token>"
```

## Callback URLs

```text
POST /AccessManager/Tenants/System/LogCollect/Dispatch
GET  /AccessManager/Tenants/System/LogCollect/Progress
GET  /AccessManager/Tenants/System/LogCollect/Nodes
```

Example registration payload sent to OMS:

```json
{
  "serverName": "AIDP-IAM",
  "dispatchCallbackUrl": "http://keycloak-proxy.aidp-iam.svc.cluster.local:8090/AccessManager/Tenants/System/LogCollect/Dispatch",
  "queryProgressCallbackUrl": "http://keycloak-proxy.aidp-iam.svc.cluster.local:8090/AccessManager/Tenants/System/LogCollect/Progress",
  "queryNodesCallbackUrl": "http://keycloak-proxy.aidp-iam.svc.cluster.local:8090/AccessManager/Tenants/System/LogCollect/Nodes",
  "callbackAuthMethod": "none",
  "logTypeVos": [
    {
      "logType": "IAM_KEYCLOAK_PROXY_LOG",
      "nodeType": "AIDP_IAM_KEYCLOAK_PROXY",
      "name": "IAM Keycloak Proxy Log",
      "nameZh": "IAM Keycloak Proxy Log"
    }
  ]
}
```

## Log Types

| logType | nodeType | Content |
| --- | --- | --- |
| `IAM_KEYCLOAK_PROXY_LOG` | `AIDP_IAM_KEYCLOAK_PROXY` | `/var/log/supervisor/keycloak-proxy.log` and `.err` |
| `IAM_PEP_PROXY_LOG` | `AIDP_IAM_PEP_PROXY` | `/var/log/supervisor/pep-proxy.log` and `.err` |
| `IAM_BUNDLE_SERVER_LOG` | `AIDP_IAM_BUNDLE_SERVER` | `/var/log/supervisor/bundle-server.log` and `.err` |
| `IAM_RESOURCE_SYNC_LOG` | `AIDP_IAM_RESOURCE_SYNC` | `/var/log/supervisor/resource-sync.log` and `.err` |
| `IAM_SUPERVISOR_LOG` | `AIDP_IAM_SUPERVISOR` | `/var/log/supervisor/supervisord.log` |
| `IAM_OPA_LOG` | `AIDP_IAM_OPA` | `iam-services` OPA sidecar Pod log |
| `IAM_KEYCLOAK_LOG` | `AIDP_IAM_KEYCLOAK` | Keycloak Pod log |
| `IAM_POSTGRES_LOG` | `AIDP_IAM_POSTGRES` | PostgreSQL Pod log |
| `IAM_RESOURCE_YAML` | `AIDP_IAM_RESOURCE` | IAM, Keycloak and Gateway route/policy resource snapshots, excluding Secret data |
| `IAM_EVENT` | `AIDP_IAM_EVENT` | IAM and Keycloak Kubernetes Events and workload status |

## Dispatch

```http
POST /AccessManager/Tenants/System/LogCollect/Dispatch
```

The request follows the OMS `CallBackCollectRequest` format. `nodeList[].logTypes` selects the log types. If it is empty, IAM collects all supported log types.

```json
{
  "startTime": "2026-05-21 10:00:00",
  "endTime": "2026-05-21 11:00:00",
  "scene": "oms_scene",
  "collectUser": "admin",
  "path": "/repo/logCollectAIDPIAM",
  "targets": [
    {
      "ip": "192.168.1.100",
      "port": "22",
      "userName": "manager",
      "password": "xxxxxx",
      "opType": "SSH"
    }
  ],
  "nodeList": [
    {
      "name": "iam-services",
      "status": "READY",
      "nodeType": "AIDP_IAM_KEYCLOAK_PROXY",
      "product": "AIDP",
      "nodeIp": "127.0.0.1",
      "logTypes": ["IAM_KEYCLOAK_PROXY_LOG"]
    }
  ]
}
```

Response:

```json
{"code": 0, "data": true, "message": "success"}
```

When `targets[].opType=SSH` and `targets[].password` is present, IAM uploads the generated zip with `paramiko==5.0.0`. The password is used only in the current request and is not stored.

## Progress

```http
GET /AccessManager/Tenants/System/LogCollect/Progress
```

Optional query parameter: `collectId`.

Response:

```json
{
  "code": 0,
  "data": {
    "basicInfo": {
      "collectStatus": "FINISH",
      "startTime": "2026-05-21 10:00:00",
      "endTime": "2026-05-21 11:00:00",
      "progress": 100,
      "describe": "log collect finished"
    },
    "nodeInfos": [
      {
        "name": "IAM Keycloak Proxy Log",
        "nodeIp": "",
        "nodeType": "AIDP_IAM_KEYCLOAK_PROXY",
        "progress": 100,
        "collectState": 2,
        "fileName": "keycloak-proxy/"
      }
    ],
    "user": "admin"
  },
  "message": "success"
}
```

## Nodes

```http
GET /AccessManager/Tenants/System/LogCollect/Nodes?page=1&limit=100
```

Response fields match the OMS callback document: `items`, `total`, `page`, `limit`.

## State and Cleanup

The current task state is stored in ConfigMap `aidp-iam-log-collect-status` in namespace `aidp-iam`. Temporary archives are stored in the `emptyDir` mounted at `/tmp/iam-log-collect`.

If `iam-services` restarts while a collection is running, startup recovery marks the unfinished task as `FAILED` so the OMS progress page does not stay at `COLLECTING`.

Time filtering is best-effort:

- Kubernetes Pod logs use `startTime` as `pods/log?sinceTime=...`.
- Supervisor file logs are copied as current log files without strict `endTime` line filtering.
