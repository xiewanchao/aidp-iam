from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request


router = APIRouter()

SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_.-]+")

IAM_NAMESPACE = os.getenv("IAM_NAMESPACE", "aidp-iam")
KEYCLOAK_NAMESPACE = os.getenv("KEYCLOAK_NAMESPACE", "keycloak")
GATEWAY_NAMESPACE = os.getenv("GATEWAY_NAMESPACE", "aidp-gateway")
LOG_STATUS_CONFIGMAP_NAME = os.getenv("IAM_LOG_STATUS_CONFIGMAP_NAME", "aidp-iam-log-collect-status")
LOG_TMP_DIR = Path(os.getenv("IAM_LOG_TMP_DIR", "/tmp/iam-log-collect"))
LOG_ARCHIVE_RETENTION_SECONDS = int(os.getenv("IAM_LOG_ARCHIVE_RETENTION_SECONDS", "86400"))
LOG_ARCHIVE_MAX_FILES = int(os.getenv("IAM_LOG_ARCHIVE_MAX_FILES", "5"))
OMS_LOG_REGISTRATION_ENABLED = os.getenv("OMS_LOG_REGISTRATION_ENABLED", "false")
OMS_LOG_REGISTER_URL = os.getenv("OMS_LOG_REGISTER_URL", "")
OMS_LOG_REGISTER_AUTH_TOKEN = os.getenv("OMS_LOG_REGISTER_AUTH_TOKEN", "")
OMS_LOG_CALLBACK_BASE_URL = os.getenv("OMS_LOG_CALLBACK_BASE_URL", "")
OMS_LOG_CALLBACK_AUTH_METHOD = os.getenv("OMS_LOG_CALLBACK_AUTH_METHOD", "none")
OMS_LOG_CALLBACK_AUTH_TOKEN = os.getenv("OMS_LOG_CALLBACK_AUTH_TOKEN", "")
OMS_LOG_UPLOAD_PATH = os.getenv("OMS_LOG_UPLOAD_PATH", "")
OMS_LOG_REGISTER_TIMEOUT_SECONDS = int(os.getenv("OMS_LOG_REGISTER_TIMEOUT_SECONDS", "10"))
OMS_LOG_REGISTER_MAX_RETRIES = int(os.getenv("OMS_LOG_REGISTER_MAX_RETRIES", "5"))
OMS_LOG_REGISTER_RETRY_INTERVAL_SECONDS = int(os.getenv("OMS_LOG_REGISTER_RETRY_INTERVAL_SECONDS", "10"))

SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
SA_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
KUBE_HOST = os.getenv("KUBERNETES_SERVICE_HOST")
KUBE_PORT = os.getenv("KUBERNETES_SERVICE_PORT", "443")

COLLECT_INIT = "INIT"
COLLECTING = "COLLECTING"
COLLECT_FINISH = "FINISH"
COLLECT_FAILED = "FAILED"
COLLECT_PART_FAILED = "PART_FAILED"

NODE_INIT = 0
NODE_COLLECTING = 1
NODE_SUCCESS = 2
NODE_FAILED = 3
NODE_PART_FAILED = 4

IAM_LOG_TYPE = "AIDP_IAM_LOG"

LOG_TYPES = [
    {
        "serverName": "AIDP-IAM",
        "logType": IAM_LOG_TYPE,
        "nodeType": "AIDP_IAM_KEYCLOAK_PROXY",
        "logTypeName": "IAM Keycloak Proxy Log",
        "logTypeNameZh": "IAM Keycloak Proxy 日志",
    },
    {
        "serverName": "AIDP-IAM",
        "logType": IAM_LOG_TYPE,
        "nodeType": "AIDP_IAM_PEP_PROXY",
        "logTypeName": "IAM PEP Proxy Log",
        "logTypeNameZh": "IAM PEP Proxy 日志",
    },
    {
        "serverName": "AIDP-IAM",
        "logType": IAM_LOG_TYPE,
        "nodeType": "AIDP_IAM_BUNDLE_SERVER",
        "logTypeName": "IAM Bundle Server Log",
        "logTypeNameZh": "IAM Bundle Server 日志",
    },
    {
        "serverName": "AIDP-IAM",
        "logType": IAM_LOG_TYPE,
        "nodeType": "AIDP_IAM_RESOURCE_SYNC",
        "logTypeName": "IAM Resource Sync Log",
        "logTypeNameZh": "IAM Resource Sync 日志",
    },
    {
        "serverName": "AIDP-IAM",
        "logType": IAM_LOG_TYPE,
        "nodeType": "AIDP_IAM_SUPERVISOR",
        "logTypeName": "IAM Supervisor Log",
        "logTypeNameZh": "IAM Supervisor 日志",
    },
    {
        "serverName": "AIDP-IAM",
        "logType": IAM_LOG_TYPE,
        "nodeType": "AIDP_IAM_OPA",
        "logTypeName": "IAM OPA Log",
        "logTypeNameZh": "IAM OPA 日志",
    },
    {
        "serverName": "AIDP-IAM",
        "logType": IAM_LOG_TYPE,
        "nodeType": "AIDP_IAM_KEYCLOAK",
        "logTypeName": "IAM Keycloak Log",
        "logTypeNameZh": "IAM Keycloak 日志",
    },
    {
        "serverName": "AIDP-IAM",
        "logType": IAM_LOG_TYPE,
        "nodeType": "AIDP_IAM_POSTGRES",
        "logTypeName": "IAM PostgreSQL Log",
        "logTypeNameZh": "IAM PostgreSQL 日志",
    },
    {
        "serverName": "AIDP-IAM",
        "logType": IAM_LOG_TYPE,
        "nodeType": "AIDP_IAM_RESOURCE",
        "logTypeName": "IAM Kubernetes Resource YAML",
        "logTypeNameZh": "IAM Kubernetes 资源配置",
    },
    {
        "serverName": "AIDP-IAM",
        "logType": IAM_LOG_TYPE,
        "nodeType": "AIDP_IAM_EVENT",
        "logTypeName": "IAM Kubernetes Event",
        "logTypeNameZh": "IAM Kubernetes 事件",
    },
]
NODE_TYPE_MAP = {item["nodeType"]: item for item in LOG_TYPES}
NODE_ARCHIVE_NAMES = {
    "AIDP_IAM_KEYCLOAK_PROXY": "iam-keycloak-proxy",
    "AIDP_IAM_PEP_PROXY": "iam-pep-proxy",
    "AIDP_IAM_BUNDLE_SERVER": "iam-bundle-server",
    "AIDP_IAM_RESOURCE_SYNC": "iam-resource-sync",
    "AIDP_IAM_SUPERVISOR": "iam-supervisor",
    "AIDP_IAM_OPA": "iam-opa",
    "AIDP_IAM_KEYCLOAK": "iam-keycloak",
    "AIDP_IAM_POSTGRES": "iam-postgres",
    "AIDP_IAM_RESOURCE": "iam-resource",
    "AIDP_IAM_EVENT": "iam-event",
}
NODE_OUTPUT_DIRS = {
    "AIDP_IAM_KEYCLOAK_PROXY": "keycloak-proxy",
    "AIDP_IAM_PEP_PROXY": "pep-proxy",
    "AIDP_IAM_BUNDLE_SERVER": "bundle-server",
    "AIDP_IAM_RESOURCE_SYNC": "resource-sync",
    "AIDP_IAM_SUPERVISOR": "supervisor",
    "AIDP_IAM_OPA": "opa",
    "AIDP_IAM_KEYCLOAK": "keycloak",
    "AIDP_IAM_POSTGRES": "postgres",
    "AIDP_IAM_RESOURCE": "resources",
    "AIDP_IAM_EVENT": "events",
}
LEGACY_LOG_TYPE_TO_NODE_TYPE = {
    "IAM_KEYCLOAK_PROXY_LOG": "AIDP_IAM_KEYCLOAK_PROXY",
    "IAM_PEP_PROXY_LOG": "AIDP_IAM_PEP_PROXY",
    "IAM_BUNDLE_SERVER_LOG": "AIDP_IAM_BUNDLE_SERVER",
    "IAM_RESOURCE_SYNC_LOG": "AIDP_IAM_RESOURCE_SYNC",
    "IAM_SUPERVISOR_LOG": "AIDP_IAM_SUPERVISOR",
    "IAM_OPA_LOG": "AIDP_IAM_OPA",
    "IAM_KEYCLOAK_LOG": "AIDP_IAM_KEYCLOAK",
    "IAM_POSTGRES_LOG": "AIDP_IAM_POSTGRES",
    "IAM_RESOURCE_YAML": "AIDP_IAM_RESOURCE",
    "IAM_EVENT": "AIDP_IAM_EVENT",
}

FILE_LOG_SPECS = {
    "AIDP_IAM_KEYCLOAK_PROXY": ("keycloak-proxy", ["keycloak-proxy.log", "keycloak-proxy.err"]),
    "AIDP_IAM_PEP_PROXY": ("pep-proxy", ["pep-proxy.log", "pep-proxy.err"]),
    "AIDP_IAM_BUNDLE_SERVER": ("bundle-server", ["bundle-server.log", "bundle-server.err"]),
    "AIDP_IAM_RESOURCE_SYNC": ("resource-sync", ["resource-sync.log", "resource-sync.err"]),
    "AIDP_IAM_SUPERVISOR": ("supervisor", ["supervisord.log"]),
}

_STATE_LOCK = threading.Lock()
_MEMORY_STATE: dict[str, Any] = {"currentCollectId": "", "tasks": {}}


def recover_interrupted_log_collect_tasks() -> None:
    try:
        state = load_collect_state()
        changed = False
        for task in state.get("tasks", {}).values():
            if task.get("collectStatus") in {COLLECT_INIT, COLLECTING}:
                task["collectStatus"] = COLLECT_FAILED
                task["progress"] = 100
                task["describe"] = "iam-services restarted during log collection"
                task["errorCode"] = "IAM_LOG_COLLECT_INTERRUPTED"
                task["errorMsg"] = "iam-services restarted before the log collect task completed"
                for node in task.get("nodeInfos", []):
                    if node.get("collectState") in {NODE_INIT, NODE_COLLECTING}:
                        node["collectState"] = NODE_FAILED
                        node["progress"] = 100
                changed = True
        if changed:
            save_collect_state(state)
        cleanup_log_tmp_dir(state)
    except Exception:
        return


@router.get("/AccessManager/Tenants/System/LogCollect/Nodes")
def get_log_collect_nodes(page: int = Query(1, ge=1), limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    nodes = discover_log_nodes()
    start = (page - 1) * limit
    end = start + limit
    return {"items": nodes[start:end], "total": len(nodes), "page": page, "limit": limit}


@router.get("/AccessManager/Tenants/System/LogCollect/Progress")
def get_log_collect_status(collectId: str | None = None) -> dict[str, Any]:
    state = load_collect_state()
    task_id = collectId or state.get("currentCollectId")
    if not task_id:
        return {
            "code": 0,
            "data": {
                "basicInfo": {
                    "collectStatus": COLLECT_FINISH,
                    "startTime": "",
                    "endTime": "",
                    "progress": 100,
                    "describe": "no log collect task",
                },
                "nodeInfos": [],
                "user": "",
            },
            "message": "success",
        }
    task = state.get("tasks", {}).get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"log collect task not found: {task_id}")
    return {"code": 0, "data": task_to_response(task), "message": "success"}


@router.post("/AccessManager/Tenants/System/LogCollect/Dispatch")
async def dispatch_log_collect(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")

    node_types = parse_requested_node_types(payload)
    collect_user = str(payload.get("collectUser") or "unknown")
    collect_id = build_collect_id(collect_user)

    with _STATE_LOCK:
        state = load_collect_state()
        current_id = state.get("currentCollectId")
        current_task = state.get("tasks", {}).get(current_id) if current_id else None
        if current_task and current_task.get("collectStatus") in {COLLECT_INIT, COLLECTING}:
            raise HTTPException(status_code=409, detail=f"log collect task is already running: {current_id}")

        task = build_initial_task(collect_id, collect_user, payload, node_types)
        state.setdefault("tasks", {})[collect_id] = task
        state["currentCollectId"] = collect_id
        save_collect_state(state)

    background_tasks.add_task(run_log_collect_task, collect_id, payload, node_types)
    return {"code": 0, "data": True, "message": "success"}


def parse_requested_node_types(payload: dict[str, Any]) -> list[str]:
    requested: list[str] = []
    for node in payload.get("nodeList") or []:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("nodeType") or "")
        values = node.get("logTypes")
        if values is None:
            values = node.get("logInfo")
        if isinstance(values, list):
            for item in values:
                log_type = str(item or "")
                if log_type == IAM_LOG_TYPE and node_type in NODE_TYPE_MAP:
                    requested.append(node_type)
                elif log_type in LEGACY_LOG_TYPE_TO_NODE_TYPE:
                    requested.append(LEGACY_LOG_TYPE_TO_NODE_TYPE[log_type])
                elif log_type in NODE_TYPE_MAP:
                    requested.append(log_type)
                elif log_type:
                    raise HTTPException(status_code=400, detail=f"unsupported log type: {log_type}")
        elif node_type in NODE_TYPE_MAP:
            requested.append(node_type)

    if not requested:
        return [item["nodeType"] for item in LOG_TYPES]

    unique = []
    for item in requested:
        if item not in NODE_TYPE_MAP:
            raise HTTPException(status_code=400, detail=f"unsupported nodeType: {item}")
        if item not in unique:
            unique.append(item)
    return unique


def start_oms_log_type_registration() -> None:
    if not env_bool(OMS_LOG_REGISTRATION_ENABLED) and not OMS_LOG_REGISTER_URL:
        return
    if not OMS_LOG_REGISTER_URL:
        print("OMS log type registration skipped: OMS_LOG_REGISTER_URL is empty", flush=True)
        return
    thread = threading.Thread(target=register_oms_log_types_with_retry, name="oms-log-register", daemon=True)
    thread.start()


def register_oms_log_types_with_retry() -> None:
    payload = build_oms_log_type_registration_payload()
    headers = {"Content-Type": "application/json"}
    if OMS_LOG_REGISTER_AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {OMS_LOG_REGISTER_AUTH_TOKEN}"

    last_error = ""
    max_retries = max(1, OMS_LOG_REGISTER_MAX_RETRIES)
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(
                OMS_LOG_REGISTER_URL,
                json=payload,
                headers=headers,
                timeout=OMS_LOG_REGISTER_TIMEOUT_SECONDS,
            )
            if response.status_code < 300:
                print(f"OMS log type registration succeeded: {response.status_code}", flush=True)
                return
            last_error = f"HTTP {response.status_code}: {response.text[:500]}"
        except Exception as exc:
            last_error = str(exc)
        print(f"OMS log type registration attempt {attempt}/{max_retries} failed: {last_error}", flush=True)
        if attempt < max_retries:
            time.sleep(max(1, OMS_LOG_REGISTER_RETRY_INTERVAL_SECONDS))
    print(f"OMS log type registration failed after {max_retries} attempts: {last_error}", flush=True)


def build_oms_log_type_registration_payload() -> dict[str, Any]:
    base_url = normalized_callback_base_url()
    payload: dict[str, Any] = {
        "serverName": "AIDP-IAM",
        "dispatchCallbackUrl": f"{base_url}/AccessManager/Tenants/System/LogCollect/Dispatch",
        "queryProgressCallbackUrl": f"{base_url}/AccessManager/Tenants/System/LogCollect/Progress",
        "queryNodesCallbackUrl": f"{base_url}/AccessManager/Tenants/System/LogCollect/Nodes",
        "callbackAuthMethod": OMS_LOG_CALLBACK_AUTH_METHOD or "none",
        "logTypeVos": [
            {
                "logType": item["logType"],
                "nodeType": item["nodeType"],
                "logTypeName": item["logTypeName"],
                "logTypeNameZh": item["logTypeNameZh"],
            }
            for item in LOG_TYPES
        ],
    }
    if OMS_LOG_CALLBACK_AUTH_TOKEN:
        payload["callbackAuthToken"] = OMS_LOG_CALLBACK_AUTH_TOKEN
    if OMS_LOG_UPLOAD_PATH:
        payload["path"] = OMS_LOG_UPLOAD_PATH
    return payload


def normalized_callback_base_url() -> str:
    base_url = OMS_LOG_CALLBACK_BASE_URL.strip()
    if not base_url:
        base_url = f"http://keycloak-proxy.{IAM_NAMESPACE}.svc.cluster.local:8090"
    return base_url.rstrip("/")


def env_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def build_collect_id(collect_user: str) -> str:
    safe_user = SAFE_ID_RE.sub("-", collect_user).strip("-") or "unknown"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{timestamp}-{safe_user}"


def node_archive_name(node_type: str) -> str:
    return NODE_ARCHIVE_NAMES.get(node_type, SAFE_ID_RE.sub("-", node_type.lower()).strip("-") or "iam-log")


def safe_filename_part(value: Any, fallback: str) -> str:
    cleaned = SAFE_ID_RE.sub("-", str(value or "").strip()).strip("-._")
    return cleaned or fallback


def compact_time_for_filename(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "unknown"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y%m%d%H%M%S")
        except ValueError:
            continue
    compact = re.sub(r"[^0-9A-Za-z]+", "", raw)
    return compact or "unknown"


def node_archive_filename(node_type: str, payload: dict[str, Any], node_ips: dict[str, str] | None = None) -> str:
    node_ips = node_ips or resolve_node_ips(payload)
    node_name = safe_filename_part(node_archive_name(node_type), "iam-log")
    node_ip = safe_filename_part(node_ips.get(node_type), "unknown")
    start_time = compact_time_for_filename(payload.get("startTime"))
    end_time = compact_time_for_filename(payload.get("endTime"))
    return f"{node_name}_{node_ip}_{start_time}-{end_time}.zip"


def build_initial_task(
    collect_id: str,
    collect_user: str,
    payload: dict[str, Any],
    node_types: list[str],
) -> dict[str, Any]:
    node_ips = resolve_node_ips(payload)
    return {
        "collectId": collect_id,
        "collectUser": collect_user,
        "scene": str(payload.get("scene") or ""),
        "startTime": str(payload.get("startTime") or ""),
        "endTime": str(payload.get("endTime") or ""),
        "collectStatus": COLLECTING,
        "progress": 0,
        "describe": "log collect task accepted",
        "archiveFile": "",
        "uploadResults": [],
        "errorCode": "",
        "errorMsg": "",
        "nodeInfos": [
            {
                "name": node_archive_name(node_type),
                "nodeIp": node_ips.get(node_type, ""),
                "nodeType": node_type,
                "progress": 0,
                "collectState": NODE_INIT,
                "fileName": node_archive_filename(node_type, payload, node_ips),
                "errorCode": "",
                "errorMes": [],
            }
            for node_type in node_types
        ],
    }


def resolve_node_ips(payload: dict[str, Any]) -> dict[str, str]:
    node_ips: dict[str, str] = {}
    for node in payload.get("nodeList") or []:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("nodeType") or "")
        node_ip = str(node.get("nodeIp") or node.get("nodeIP") or node.get("ip") or "").strip()
        if node_type and node_ip and node_type not in node_ips:
            node_ips[node_type] = node_ip

    for node in discover_log_nodes():
        node_type = str(node.get("nodeType") or "")
        node_ip = str(node.get("nodeIp") or "").strip()
        if node_type and node_ip and node_type not in node_ips:
            node_ips[node_type] = node_ip
    return node_ips


_POD_LOG_NODES = {
    "AIDP_IAM_OPA":      ("opa",      IAM_NAMESPACE,      "app=iam-services", "opa",      "opa.log"),
    "AIDP_IAM_KEYCLOAK": ("keycloak", KEYCLOAK_NAMESPACE, "app=keycloak",     "keycloak", "keycloak.log"),
    "AIDP_IAM_POSTGRES": ("postgres", KEYCLOAK_NAMESPACE, "app=iam-store",    "postgres", "postgres.log"),
}


def _collect_pod_log_nodes(
    collect_id: str, work_dir: "Path", payload: dict, node_types: list[str],
) -> None:
    for node_type, (output_name, namespace, label, container, filename) in _POD_LOG_NODES.items():
        if node_type in node_types:
            collect_with_node(
                collect_id, node_type, f"{output_name}/",
                collect_pod_logs, work_dir / output_name,
                namespace, label, container, payload, filename,
            )


def task_to_response(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "basicInfo": {
            "collectStatus": task.get("collectStatus", COLLECT_FAILED),
            "startTime": task.get("startTime", ""),
            "endTime": task.get("endTime", ""),
            "progress": int(task.get("progress") or 0),
            "describe": task.get("describe", ""),
        },
        "nodeInfos": task.get("nodeInfos", []),
        "user": task.get("collectUser", ""),
    }


def _finalize_collect_task(
    collect_id: str, work_dir: "Path", archive_paths: list, upload_results: list,
) -> None:
    final_status = COLLECT_FINISH if (not upload_results or all(r.get("success") for r in upload_results)) else COLLECT_PART_FAILED
    update_task_fields(collect_id, {
        "collectStatus": final_status,
        "progress": 100,
        "describe": "log collect finished" if final_status == COLLECT_FINISH else "log collect finished with upload failures",
        "uploadResults": upload_results,
    })
    if final_status == COLLECT_FINISH:
        shutil.rmtree(work_dir, ignore_errors=True)
        if upload_results:
            for archive_path in archive_paths:
                archive_path.unlink(missing_ok=True)


def run_log_collect_task(collect_id: str, payload: dict[str, Any], node_types: list[str]) -> None:
    work_dir = LOG_TMP_DIR / collect_id
    archive_paths: list[Path] = []
    try:
        shutil.rmtree(work_dir, ignore_errors=True)
        LOG_TMP_DIR.mkdir(parents=True, exist_ok=True)
        work_dir.mkdir(parents=True, exist_ok=True)

        write_json_file(work_dir / "metadata.json", {
            "serverName": "AIDP-IAM",
            "collectId": collect_id,
            "collectUser": payload.get("collectUser"),
            "scene": payload.get("scene"),
            "startTime": payload.get("startTime"),
            "endTime": payload.get("endTime"),
            "iamNamespace": IAM_NAMESPACE,
            "keycloakNamespace": KEYCLOAK_NAMESPACE,
            "gatewayNamespace": GATEWAY_NAMESPACE,
            "logTypes": [IAM_LOG_TYPE],
            "nodeTypes": node_types,
            "timeFilter": {
                "podLogs": "startTime is passed to Kubernetes pods/log sinceTime when present",
                "fileLogs": "supervisor file logs are copied as current files without strict line filtering",
            },
        })
        update_task_progress(collect_id, 5, "metadata generated")

        if "AIDP_IAM_RESOURCE" in node_types:
            collect_with_node(collect_id, "AIDP_IAM_RESOURCE", "resources/resources.json", collect_iam_resources, work_dir / "resources")
        update_task_progress(collect_id, 25, "iam resources collected")

        for node_type, (output_name, file_names) in FILE_LOG_SPECS.items():
            if node_type in node_types:
                collect_with_node(
                    collect_id, node_type, f"{output_name}/",
                    collect_supervisor_files, work_dir / output_name, file_names,
                )
        update_task_progress(collect_id, 55, "iam service file logs collected")

        _collect_pod_log_nodes(collect_id, work_dir, payload, node_types)
        update_task_progress(collect_id, 75, "container logs collected")

        if "AIDP_IAM_EVENT" in node_types:
            collect_with_node(collect_id, "AIDP_IAM_EVENT", "events/events.json", collect_iam_events, work_dir / "events")
        update_task_progress(collect_id, 85, "iam events collected")

        archive_paths = make_node_archives(work_dir, payload, node_types)
        update_task_fields(collect_id, {
            "archiveFile": ",".join(str(p) for p in archive_paths),
            "archiveFiles": [str(p) for p in archive_paths],
            "progress": 90,
            "describe": "node archives generated",
        })

        upload_results = upload_archives(archive_paths, payload)
        _finalize_collect_task(collect_id, work_dir, archive_paths, upload_results)
        cleanup_log_tmp_dir(load_collect_state(), preserve_files={p.name for p in archive_paths})
    except Exception as exc:
        fail_running_nodes(collect_id, str(exc))
        update_task_fields(collect_id, {
            "collectStatus": COLLECT_FAILED,
            "progress": 100,
            "describe": "log collect failed",
            "errorCode": "IAM_LOG_COLLECT_FAILED",
            "errorMsg": str(exc),
        })
        cleanup_log_tmp_dir(load_collect_state(), preserve_files={path.name for path in archive_paths})


def collect_with_node(
    collect_id: str,
    node_type: str,
    _file_name: str,
    collect_func: Any,
    *args: Any,
) -> None:
    mark_node(collect_id, node_type, NODE_COLLECTING, 20)
    try:
        collect_func(*args)
        mark_node(collect_id, node_type, NODE_SUCCESS, 100)
    except Exception as exc:
        mark_node(collect_id, node_type, NODE_FAILED, 100, error=str(exc))
        raise


def collect_supervisor_files(output_dir: Path, file_names: list[str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for file_name in file_names:
        source = Path("/var/log/supervisor") / file_name
        destination = output_dir / file_name
        if source.exists():
            shutil.copy2(source, destination)
        else:
            write_text_file(destination, f"{source} does not exist\n")


def collect_iam_resources(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    resources = {
        "iam-pods": f"/api/v1/namespaces/{IAM_NAMESPACE}/pods",
        "iam-services": f"/api/v1/namespaces/{IAM_NAMESPACE}/services",
        "iam-configmaps": f"/api/v1/namespaces/{IAM_NAMESPACE}/configmaps",
        "iam-deployments": f"/apis/apps/v1/namespaces/{IAM_NAMESPACE}/deployments",
        "keycloak-pods": f"/api/v1/namespaces/{KEYCLOAK_NAMESPACE}/pods",
        "keycloak-services": f"/api/v1/namespaces/{KEYCLOAK_NAMESPACE}/services",
        "keycloak-configmaps": f"/api/v1/namespaces/{KEYCLOAK_NAMESPACE}/configmaps",
        "keycloak-statefulsets": f"/apis/apps/v1/namespaces/{KEYCLOAK_NAMESPACE}/statefulsets",
        "gateway-httproutes": f"/apis/gateway.networking.k8s.io/v1/namespaces/{GATEWAY_NAMESPACE}/httproutes",
        "gateway-referencegrants": f"/apis/gateway.networking.k8s.io/v1beta1/namespaces/{GATEWAY_NAMESPACE}/referencegrants",
        "gateway-securitypolicies": f"/apis/gateway.envoyproxy.io/v1alpha1/namespaces/{GATEWAY_NAMESPACE}/securitypolicies",
        "gateway-envoyextensionpolicies": f"/apis/gateway.envoyproxy.io/v1alpha1/namespaces/{GATEWAY_NAMESPACE}/envoyextensionpolicies",
    }
    summary: dict[str, Any] = {}
    for name, path in resources.items():
        data = fetch_k8s_json(path)
        summary[name] = data
        write_json_file(output_dir / f"{name}.json", data)
    write_json_file(output_dir / "resources.json", summary)


def collect_iam_events(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    resources = {
        "iam-events": f"/api/v1/namespaces/{IAM_NAMESPACE}/events",
        "keycloak-events": f"/api/v1/namespaces/{KEYCLOAK_NAMESPACE}/events",
        "iam-pods": f"/api/v1/namespaces/{IAM_NAMESPACE}/pods",
        "keycloak-pods": f"/api/v1/namespaces/{KEYCLOAK_NAMESPACE}/pods",
        "iam-deployments": f"/apis/apps/v1/namespaces/{IAM_NAMESPACE}/deployments",
        "keycloak-statefulsets": f"/apis/apps/v1/namespaces/{KEYCLOAK_NAMESPACE}/statefulsets",
    }
    summary: dict[str, Any] = {}
    for name, path in resources.items():
        data = fetch_k8s_json(path)
        summary[name] = data
        write_json_file(output_dir / f"{name}.json", data)
    write_json_file(output_dir / "events.json", summary)


def collect_pod_logs(
    output_dir: Path,
    namespace: str,
    label_selector: str,
    container: str,
    payload: dict[str, Any],
    fallback_file: str,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    pods = list_pods(namespace, label_selector)
    if not pods:
        write_text_file(output_dir / fallback_file, f"No pods found for selector: {label_selector}\n")
        return
    for pod in pods:
        metadata = pod.get("metadata", {})
        name = metadata.get("name", "unknown")
        log_text = fetch_pod_log(namespace, name, container, payload)
        write_text_file(output_dir / f"{namespace}_{name}.log", log_text)


def list_pods(namespace: str, label_selector: str) -> list[dict[str, Any]]:
    params = urlencode({"labelSelector": label_selector})
    data = fetch_k8s_json(f"/api/v1/namespaces/{namespace}/pods?{params}")
    return data.get("items", []) if isinstance(data, dict) else []


def fetch_pod_log(namespace: str, pod_name: str, container: str, payload: dict[str, Any]) -> str:
    params: dict[str, str] = {}
    since_time = request_since_time(payload)
    if since_time:
        params["sinceTime"] = since_time
    if container:
        params["container"] = container
    query = urlencode(params)
    path = f"/api/v1/namespaces/{namespace}/pods/{pod_name}/log"
    if query:
        path = f"{path}?{query}"
    response = try_k8s_request("GET", path)
    if response is not None and response.status_code < 300:
        return response.text

    fallback_path = f"/api/v1/namespaces/{namespace}/pods/{pod_name}/log"
    if since_time:
        fallback_path = f"{fallback_path}?{urlencode({'sinceTime': since_time})}"
    fallback = try_k8s_request("GET", fallback_path)
    if fallback is not None and fallback.status_code < 300:
        return fallback.text
    if response is not None:
        return f"failed to read pod log: HTTP {response.status_code}\n{response.text}\n"
    return "Kubernetes API is not available in this runtime.\n"


def fetch_k8s_json(path: str) -> dict[str, Any]:
    response = try_k8s_request("GET", path)
    if response is None:
        return {"unavailable": True, "reason": "Kubernetes API is not available in this runtime", "path": path}
    if response.status_code == 404:
        return {"notFound": True, "path": path}
    if response.status_code >= 300:
        return {"error": True, "statusCode": response.status_code, "body": response.text, "path": path}
    try:
        return response.json()
    except Exception:
        return {"raw": response.text, "path": path}


def discover_log_nodes() -> list[dict[str, Any]]:
    nodes = []
    iam_nodes = [
        ("keycloak-proxy", "AIDP_IAM_KEYCLOAK_PROXY"),
        ("pep-proxy", "AIDP_IAM_PEP_PROXY"),
        ("bundle-server", "AIDP_IAM_BUNDLE_SERVER"),
        ("resource-sync", "AIDP_IAM_RESOURCE_SYNC"),
        ("supervisor", "AIDP_IAM_SUPERVISOR"),
        ("opa", "AIDP_IAM_OPA"),
    ]
    iam_pods = list_pods(IAM_NAMESPACE, "app=iam-services")
    iam_pod = select_best_pod(iam_pods)
    for suffix, node_type in iam_nodes:
        nodes.append(build_log_node(suffix, node_type, iam_pod, name_suffix=suffix))

    nodes.append(discover_pod_as_node(KEYCLOAK_NAMESPACE, "app=keycloak", "AIDP_IAM_KEYCLOAK", "keycloak"))
    nodes.append(discover_pod_as_node(KEYCLOAK_NAMESPACE, "app=iam-store", "AIDP_IAM_POSTGRES", "postgres"))
    nodes.append(build_log_node("iam-kubernetes-resources", "AIDP_IAM_RESOURCE", None, logical_ready=True))
    nodes.append(build_log_node("iam-kubernetes-events", "AIDP_IAM_EVENT", None, logical_ready=True))
    return nodes


def discover_pod_as_node(namespace: str, selector: str, node_type: str, display_name: str) -> dict[str, Any]:
    pods = list_pods(namespace, selector)
    pod = select_best_pod(pods)
    if not pod:
        return build_log_node(display_name, node_type, None)
    return build_log_node(display_name, node_type, pod)


def select_best_pod(pods: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not pods:
        return None
    ready_with_ip = [
        pod for pod in pods
        if is_pod_ready(pod) and pod.get("status", {}).get("podIP")
    ]
    if ready_with_ip:
        return ready_with_ip[0]
    running_with_ip = [
        pod for pod in pods
        if pod.get("status", {}).get("phase") == "Running" and pod.get("status", {}).get("podIP")
    ]
    if running_with_ip:
        return running_with_ip[0]
    with_ip = [pod for pod in pods if pod.get("status", {}).get("podIP")]
    return with_ip[0] if with_ip else pods[0]


def build_log_node(
    display_name: str,
    node_type: str,
    pod: dict[str, Any] | None,
    logical_ready: bool = False,
    name_suffix: str = "",
) -> dict[str, Any]:
    if pod:
        status = pod.get("status", {})
        return {
            "name": node_archive_name(node_type),
            "status": "READY" if is_pod_ready(pod) else status.get("phase", "UNKNOWN"),
            "nodeType": node_type,
            "product": "AIDP",
            "nodeIp": status.get("podIP", ""),
        }
    return {
        "name": node_archive_name(node_type),
        "status": "READY" if logical_ready and kube_available() else ("OFFLINE" if kube_available() else "UNKNOWN"),
        "nodeType": node_type,
        "product": "AIDP",
        "nodeIp": "",
    }


def is_pod_ready(pod: dict[str, Any]) -> bool:
    for condition in pod.get("status", {}).get("conditions", []) or []:
        if condition.get("type") == "Ready" and condition.get("status") == "True":
            return True
    return False


def request_since_time(payload: dict[str, Any]) -> str:
    start_time = str(payload.get("startTime") or "").strip()
    if not start_time:
        return ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            parsed = datetime.strptime(start_time, fmt)
            return parsed.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            continue
    return ""


def upload_archive(archive_path: Path, payload: dict[str, Any]) -> list[dict[str, Any]]:
    targets = payload.get("targets") or []
    if not isinstance(targets, list) or not targets:
        return []
    results = []
    for target in targets:
        if not isinstance(target, dict):
            results.append({"success": False, "reason": "target must be an object"})
            continue
        op_type = str(target.get("opType") or "SSH").upper()
        if op_type == "LOCAL":
            results.append(copy_archive_to_local_path(archive_path, str(payload.get("path") or "")))
        elif op_type == "SSH":
            results.append(upload_archive_with_scp(archive_path, payload, target))
        else:
            results.append({"success": False, "reason": f"unsupported target opType: {op_type}"})
    return results


def upload_archives(archive_paths: list[Path], payload: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for archive_path in archive_paths:
        for item in upload_archive(archive_path, payload):
            item.setdefault("archive", archive_path.name)
            results.append(item)
    return results


def copy_archive_to_local_path(archive_path: Path, dest_path: str) -> dict[str, Any]:
    if not dest_path:
        return {"success": False, "reason": "path is required for LOCAL upload"}
    destination = Path(dest_path)
    destination.mkdir(parents=True, exist_ok=True)
    copied = destination / archive_path.name
    shutil.copy2(archive_path, copied)
    return {"success": True, "opType": "LOCAL", "path": str(copied)}


def upload_archive_with_scp(archive_path: Path, payload: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    password = str(target.get("password") or "")
    if password:
        return upload_archive_with_password_scp(archive_path, payload, target, password)
    user = str(target.get("userName") or "")
    ip = str(target.get("ip") or "")
    port = str(target.get("port") or "22")
    dest_path = str(payload.get("path") or "")
    if not user or not ip or not dest_path:
        return {"success": False, "opType": "SSH", "reason": "userName, ip and path are required"}
    remote = f"{user}@{ip}:{dest_path.rstrip('/')}/{archive_path.name}"
    try:
        completed = subprocess.run(
            ["scp", "-P", port, "-o", "StrictHostKeyChecking=no", str(archive_path), remote],
            check=False,
            text=True,
            capture_output=True,
            timeout=120,
        )
    except Exception as exc:
        return {"success": False, "opType": "SSH", "reason": str(exc)}
    if completed.returncode != 0:
        return {"success": False, "opType": "SSH", "reason": completed.stderr.strip() or completed.stdout.strip()}
    return {"success": True, "opType": "SSH", "remote": remote}


def upload_archive_with_password_scp(
    archive_path: Path,
    payload: dict[str, Any],
    target: dict[str, Any],
    password: str,
) -> dict[str, Any]:
    user = str(target.get("userName") or "")
    ip = str(target.get("ip") or "")
    port = int(str(target.get("port") or "22"))
    dest_path = str(payload.get("path") or "")
    if not user or not ip or not dest_path:
        return {"success": False, "opType": "SSH", "reason": "userName, ip and path are required"}

    try:
        import paramiko
    except Exception as exc:
        return {"success": False, "opType": "SSH", "reason": f"paramiko is required for password SCP: {exc}"}

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    remote_file = posixpath.join(dest_path.rstrip("/") or "/", archive_path.name)
    try:
        ssh.connect(
            hostname=ip,
            port=port,
            username=user,
            password=password,
            look_for_keys=False,
            allow_agent=False,
            timeout=15,
            auth_timeout=15,
            banner_timeout=15,
        )
        mkdir_command = f"mkdir -p -- {shlex.quote(dest_path)}"
        _, stdout, stderr = ssh.exec_command(mkdir_command, timeout=30)
        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            reason = stderr.read().decode("utf-8", errors="replace").strip() or f"mkdir exited {exit_status}"
            return {"success": False, "opType": "SSH", "reason": reason}

        transport = ssh.get_transport()
        if transport is None:
            return {"success": False, "opType": "SSH", "reason": "SSH transport is not available"}
        channel = transport.open_session()
        channel.settimeout(120)
        channel.exec_command(f"scp -t -- {shlex.quote(remote_file)}")
        send_file_over_scp_channel(channel, archive_path)
        channel.shutdown_write()
        exit_status = channel.recv_exit_status()
        if exit_status != 0:
            return {"success": False, "opType": "SSH", "reason": f"remote scp exited {exit_status}"}
        return {"success": True, "opType": "SSH", "remote": f"{user}@{ip}:{remote_file}"}
    except Exception as exc:
        return {"success": False, "opType": "SSH", "reason": str(exc)}
    finally:
        ssh.close()


def send_file_over_scp_channel(channel: Any, archive_path: Path) -> None:
    wait_scp_ack(channel)
    size = archive_path.stat().st_size
    channel.sendall(f"C0644 {size} {archive_path.name}\n".encode("utf-8"))
    wait_scp_ack(channel)
    with archive_path.open("rb") as file_obj:
        while True:
            chunk = file_obj.read(1024 * 64)
            if not chunk:
                break
            channel.sendall(chunk)
    channel.sendall(b"\x00")
    wait_scp_ack(channel)


def wait_scp_ack(channel: Any) -> None:
    code = channel.recv(1)
    if code == b"\x00":
        return
    if code in {b"\x01", b"\x02"}:
        message = bytearray()
        while True:
            chunk = channel.recv(1)
            if not chunk or chunk == b"\n":
                break
            message.extend(chunk)
        raise RuntimeError(message.decode("utf-8", errors="replace") or "remote scp rejected upload")
    raise RuntimeError(f"unexpected scp response: {code!r}")


def make_zip(source_dir: Path, archive_path: Path) -> None:
    archive_path.unlink(missing_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for path in source_dir.rglob("*"):
            if path.is_file():
                zip_file.write(path, path.relative_to(source_dir))


def make_node_archives(work_dir: Path, payload: dict[str, Any], node_types: list[str]) -> list[Path]:
    archive_paths = []
    node_ips = resolve_node_ips(payload)
    for node_type in node_types:
        output_dir = NODE_OUTPUT_DIRS.get(node_type)
        if not output_dir:
            continue
        archive_path = LOG_TMP_DIR / node_archive_filename(node_type, payload, node_ips)
        make_zip_from_relative_paths(work_dir, archive_path, ["metadata.json", output_dir])
        archive_paths.append(archive_path)
    return archive_paths


def make_zip_from_relative_paths(base_dir: Path, archive_path: Path, relative_paths: list[str]) -> None:
    archive_path.unlink(missing_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for relative_path in relative_paths:
            path = base_dir / relative_path
            if path.is_file():
                zip_file.write(path, path.relative_to(base_dir))
            elif path.is_dir():
                for item in path.rglob("*"):
                    if item.is_file():
                        zip_file.write(item, item.relative_to(base_dir))


def update_task_progress(collect_id: str, progress: int, describe: str) -> None:
    update_task_fields(collect_id, {"progress": progress, "describe": describe})


def update_task_fields(collect_id: str, fields: dict[str, Any]) -> None:
    with _STATE_LOCK:
        state = load_collect_state()
        task = state.setdefault("tasks", {}).setdefault(collect_id, {})
        task.update(fields)
        save_collect_state(state)


def mark_node(
    collect_id: str,
    node_type: str,
    state_value: int,
    progress: int,
    file_name: str = "",
    error: str = "",
) -> None:
    with _STATE_LOCK:
        state = load_collect_state()
        task = state.setdefault("tasks", {}).setdefault(collect_id, {})
        for node in task.get("nodeInfos", []):
            if node.get("nodeType") == node_type:
                node["collectState"] = state_value
                node["progress"] = progress
                if file_name:
                    node["fileName"] = file_name
                if error:
                    node["errorMes"] = [error]
                break
        save_collect_state(state)


def fail_running_nodes(collect_id: str, error: str) -> None:
    with _STATE_LOCK:
        state = load_collect_state()
        task = state.setdefault("tasks", {}).setdefault(collect_id, {})
        for node in task.get("nodeInfos", []):
            if node.get("collectState") in {NODE_INIT, NODE_COLLECTING}:
                node["collectState"] = NODE_FAILED
                node["progress"] = 100
                node["errorMes"] = [error]
        save_collect_state(state)


def load_collect_state() -> dict[str, Any]:
    if not kube_available():
        return json.loads(json.dumps(_MEMORY_STATE))
    response = try_k8s_request("GET", f"/api/v1/namespaces/{IAM_NAMESPACE}/configmaps/{LOG_STATUS_CONFIGMAP_NAME}")
    if response is None or response.status_code == 404:
        return {"currentCollectId": "", "tasks": {}}
    if response.status_code >= 300:
        return {"currentCollectId": "", "tasks": {}}
    raw = response.json().get("data", {}).get("status.json", "")
    if not raw:
        return {"currentCollectId": "", "tasks": {}}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"currentCollectId": "", "tasks": {}}


def save_collect_state(state: dict[str, Any]) -> None:
    if not kube_available():
        _MEMORY_STATE.clear()
        _MEMORY_STATE.update(json.loads(json.dumps(state)))
        return
    body = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": LOG_STATUS_CONFIGMAP_NAME,
            "namespace": IAM_NAMESPACE,
            "labels": {"app.kubernetes.io/name": "iam-services"},
        },
        "data": {"status.json": json.dumps(state, ensure_ascii=False, sort_keys=True)},
    }
    path = f"/api/v1/namespaces/{IAM_NAMESPACE}/configmaps/{LOG_STATUS_CONFIGMAP_NAME}"
    response = k8s_request("PATCH", path, json_body=body, content_type="application/merge-patch+json")
    if response.status_code == 404:
        response = k8s_request("POST", f"/api/v1/namespaces/{IAM_NAMESPACE}/configmaps", json_body=body)
    if response.status_code >= 300:
        raise RuntimeError(f"failed to write log collect status ConfigMap: {response.text}")


def cleanup_log_tmp_dir(state: dict[str, Any], preserve_files: set[str] | None = None) -> None:
    if not LOG_TMP_DIR.exists():
        return
    preserve_files = preserve_files or set()
    active_ids = set(state.get("tasks", {}).keys())
    for path in LOG_TMP_DIR.iterdir():
        if path.is_dir() and path.name not in active_ids:
            shutil.rmtree(path, ignore_errors=True)
    archive_paths = sorted(LOG_TMP_DIR.glob("*.zip"), key=lambda item: item.stat().st_mtime)
    now = datetime.now(timezone.utc).timestamp()
    kept_paths: list[Path] = []
    for path in archive_paths:
        if path.name in preserve_files:
            kept_paths.append(path)
            continue
        age = now - path.stat().st_mtime
        if age > LOG_ARCHIVE_RETENTION_SECONDS:
            path.unlink(missing_ok=True)
        else:
            kept_paths.append(path)
    removable = [path for path in kept_paths if path.name not in preserve_files]
    overflow = len(kept_paths) - LOG_ARCHIVE_MAX_FILES
    if overflow > 0:
        for path in removable[:overflow]:
            path.unlink(missing_ok=True)


def kube_available() -> bool:
    return bool(KUBE_HOST and os.path.exists(SA_TOKEN_PATH) and os.path.exists(SA_CA_PATH))


def try_k8s_request(method: str, path: str, json_body: dict[str, Any] | None = None) -> requests.Response | None:
    if not kube_available():
        return None
    return k8s_request(method, path, json_body=json_body)


def k8s_request(
    method: str,
    path: str,
    json_body: dict[str, Any] | None = None,
    content_type: str | None = None,
) -> requests.Response:
    if not KUBE_HOST:
        raise RuntimeError("KUBERNETES_SERVICE_HOST is not set")
    with open(SA_TOKEN_PATH, "r", encoding="utf-8") as token_file:
        token = token_file.read().strip()
    headers = {"Authorization": f"Bearer {token}"}
    if content_type:
        headers["Content-Type"] = content_type
    url = f"https://{KUBE_HOST}:{KUBE_PORT}{path}"
    return requests.request(method, url, headers=headers, json=json_body, verify=SA_CA_PATH, timeout=30)


def write_json_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text_file(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")
