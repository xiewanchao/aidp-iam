import base64
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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from cryptography.hazmat.primitives.serialization import pkcs12
from fastapi import BackgroundTasks, File, Form, HTTPException, Query, Request, UploadFile
from fastapi import FastAPI


app = FastAPI(title="AIDP Gateway Manager", version="1.1.0")

ALIAS_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,52}[a-z0-9])?$")
SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
PEM_CERT_RE = re.compile(
    rb"-----BEGIN CERTIFICATE-----\s+.*?\s+-----END CERTIFICATE-----",
    re.DOTALL,
)

SECRET_NAMESPACE = os.getenv("GATEWAY_CERT_NAMESPACE", "aidp-gateway")
GATEWAY_NAMESPACE = os.getenv("GATEWAY_NAMESPACE", "aidp-gateway")
GATEWAY_NAME = os.getenv("GATEWAY_NAME", "eg")
SECRET_PREFIX = os.getenv("GATEWAY_CERT_SECRET_PREFIX", "gw-cert-")
GATEWAY_MANAGER_NAMESPACE = os.getenv("GATEWAY_MANAGER_NAMESPACE", GATEWAY_NAMESPACE)
LOG_STATUS_CONFIGMAP_NAME = os.getenv("LOG_STATUS_CONFIGMAP_NAME", "aidp-gateway-log-collect-status")
LOG_TMP_DIR = Path(os.getenv("LOG_TMP_DIR", "/tmp/gateway-log-collect"))
LOG_ARCHIVE_FORMAT = os.getenv("LOG_ARCHIVE_FORMAT", "zip").lower()
LOG_ARCHIVE_RETENTION_SECONDS = int(os.getenv("LOG_ARCHIVE_RETENTION_SECONDS", "86400"))
LOG_ARCHIVE_MAX_FILES = int(os.getenv("LOG_ARCHIVE_MAX_FILES", "5"))
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

GATEWAY_LOG_TYPE = "AIDP_GATEWAY_LOG"

LOG_TYPES = [
    {
        "serverName": "AIDP-Gateway",
        "logType": GATEWAY_LOG_TYPE,
        "nodeType": "AIDP_GATEWAY_CONTROLLER",
        "logTypeName": "Gateway Controller Log",
        "logTypeNameZh": "Gateway 控制面日志",
    },
    {
        "serverName": "AIDP-Gateway",
        "logType": GATEWAY_LOG_TYPE,
        "nodeType": "AIDP_GATEWAY_PROXY",
        "logTypeName": "Gateway Proxy Log",
        "logTypeNameZh": "Gateway 数据面日志",
    },
    {
        "serverName": "AIDP-Gateway",
        "logType": GATEWAY_LOG_TYPE,
        "nodeType": "AIDP_GATEWAY_MANAGER",
        "logTypeName": "Gateway Manager Log",
        "logTypeNameZh": "Gateway 管理面日志",
    },
    {
        "serverName": "AIDP-Gateway",
        "logType": GATEWAY_LOG_TYPE,
        "nodeType": "AIDP_GATEWAY_RESOURCE",
        "logTypeName": "Gateway Resource YAML",
        "logTypeNameZh": "Gateway 资源配置",
    },
    {
        "serverName": "AIDP-Gateway",
        "logType": GATEWAY_LOG_TYPE,
        "nodeType": "AIDP_GATEWAY_EVENT",
        "logTypeName": "Gateway Kubernetes Event",
        "logTypeNameZh": "Gateway 事件",
    },
]
NODE_TYPE_MAP = {item["nodeType"]: item for item in LOG_TYPES}
NODE_ARCHIVE_NAMES = {
    "AIDP_GATEWAY_CONTROLLER": "gateway-controller",
    "AIDP_GATEWAY_PROXY": "gateway-proxy",
    "AIDP_GATEWAY_MANAGER": "gateway-manager",
    "AIDP_GATEWAY_RESOURCE": "gateway-resource",
    "AIDP_GATEWAY_EVENT": "gateway-event",
}
NODE_OUTPUT_DIRS = {
    "AIDP_GATEWAY_CONTROLLER": "controller",
    "AIDP_GATEWAY_PROXY": "proxy",
    "AIDP_GATEWAY_MANAGER": "manager",
    "AIDP_GATEWAY_RESOURCE": "resources",
    "AIDP_GATEWAY_EVENT": "events",
}
LEGACY_LOG_TYPE_TO_NODE_TYPE = {
    "GATEWAY_CONTROLLER_LOG": "AIDP_GATEWAY_CONTROLLER",
    "GATEWAY_PROXY_LOG": "AIDP_GATEWAY_PROXY",
    "GATEWAY_MANAGER_LOG": "AIDP_GATEWAY_MANAGER",
    "GATEWAY_RESOURCE_YAML": "AIDP_GATEWAY_RESOURCE",
    "GATEWAY_EVENT": "AIDP_GATEWAY_EVENT",
}

_STATE_LOCK = threading.Lock()
_MEMORY_STATE: dict[str, Any] = {"currentCollectId": "", "tasks": {}}


def _mark_interrupted_node(node: dict[str, Any]) -> None:
    if node.get("collectState") in {NODE_INIT, NODE_COLLECTING}:
        node["collectState"] = NODE_FAILED
        node["progress"] = 100


def _mark_interrupted_task(task: dict[str, Any]) -> bool:
    if task.get("collectStatus") not in {COLLECT_INIT, COLLECTING}:
        return False
    task["collectStatus"] = COLLECT_FAILED
    task["progress"] = 100
    task["describe"] = "gateway-manager restarted during log collection"
    task["errorCode"] = "GATEWAY_LOG_COLLECT_INTERRUPTED"
    task["errorMsg"] = "gateway-manager restarted before the log collect task completed"
    for node in task.get("nodeInfos", []):
        _mark_interrupted_node(node)
    return True


@app.on_event("startup")
def recover_interrupted_log_collect_tasks() -> None:
    try:
        state = load_collect_state()
    except Exception as exc:
        print(f"failed to load gateway log collect state during recovery: {exc}", flush=True)
    else:
        changed = any(_mark_interrupted_task(task) for task in state.get("tasks", {}).values())
        if changed:
            save_collect_state(state)
        cleanup_log_tmp_dir(state)
    start_oms_log_type_registration()


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


def reject_sm_certificate_fields(*fields: Any) -> None:
    if any(fields):
        raise HTTPException(
            status_code=400,
            detail="SM dual-certificate fields are not supported by the standard Gateway API TLS Secret path",
        )


def select_certificate_uploads(
    ca_cert_camel: UploadFile | None,
    ca_cert_snake: UploadFile | None,
    private_key_camel: UploadFile | None,
    private_key_snake: UploadFile | None,
) -> tuple[UploadFile | None, UploadFile | None]:
    return ca_cert_camel or ca_cert_snake, private_key_camel or private_key_snake


def build_tls_secret_spec(
    alias: str,
    tls_material: tuple,
    display_name: str | None,
    product_name: str | None,
    is_preset: bool,
) -> "TlsSecretSpec":
    tls_cert_pem, ca_pem, tls_key_pem, leaf_cert = tls_material
    return TlsSecretSpec(
        secret_name=f"{SECRET_PREFIX}{alias}",
        alias=alias,
        tls_cert_pem=tls_cert_pem,
        ca_pem=ca_pem,
        tls_key_pem=tls_key_pem,
        fingerprint=leaf_cert.fingerprint(hashes.SHA256()).hex(),
        not_before=cert_time(leaf_cert, "not_valid_before"),
        not_after=cert_time(leaf_cert, "not_valid_after"),
        display_name=display_name,
        product_name=product_name,
        is_preset=is_preset,
    )


def certificate_response(spec: "TlsSecretSpec", binding: dict[str, Any]) -> dict[str, Any]:
    return {
        "alias": spec.alias,
        "display_name": spec.display_name,
        "product_name": spec.product_name,
        "secret_name": spec.secret_name,
        "secret_namespace": SECRET_NAMESPACE,
        "status": "Ready",
        "gateway_bound": binding["gateway_bound"],
        "gateway_name": binding.get("gateway_name"),
        "listener_name": binding.get("listener_name"),
        "hostname": binding.get("hostname"),
        "not_before": spec.not_before,
        "not_after": spec.not_after,
        "fingerprint_sha256": spec.fingerprint,
        "message": "certificate secret updated",
    }


@app.get("/GatewayManager/Tenants/System/LogCollect/Nodes")
def get_log_collect_nodes(page: int = Query(1, ge=1), limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    nodes = discover_log_nodes()
    start = (page - 1) * limit
    end = start + limit
    return {"items": nodes[start:end], "total": len(nodes), "page": page, "limit": limit}


@app.get("/GatewayManager/Tenants/System/LogCollect/Progress")
def get_log_collect_status(collect_id: str | None = Query(None, alias="collectId")) -> dict[str, Any]:
    state = load_collect_state()
    task_id = collect_id or state.get("currentCollectId")
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
            "message": "成功",
        }
    task = state.get("tasks", {}).get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"log collect task not found: {task_id}")
    return {"code": 0, "data": task_to_response(task), "message": "成功"}


@app.post("/GatewayManager/Tenants/System/LogCollect/Dispatch")
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
    return {"code": 0, "data": True, "message": "成功"}


@app.post("/GatewayManager/Tenants/System/Certificates/{alias}")
async def post_gateway_certificate(
    alias: str,
    alias_form: str | None = Form(None, alias="alias"),
    cert: UploadFile | None = File(None),
    ca_cert_camel: UploadFile | None = File(None, alias="caCert"),
    ca_cert_snake: UploadFile | None = File(None, alias="ca_cert"),
    private_key_camel: UploadFile | None = File(None, alias="privateKey"),
    private_key_snake: UploadFile | None = File(None, alias="private_key"),
    password: str | None = Form(None),
    enc_cert: UploadFile | None = File(None, alias="encCert"),
    enc_ca_cert: UploadFile | None = File(None, alias="encCaCert"),
    enc_private_key: UploadFile | None = File(None, alias="encPrivateKey"),
    enc_password: str | None = Form(None, alias="encPassword"),
    is_preset: bool = Form(False, alias="isPreset"),
    display_name: str | None = Form(None, alias="displayName"),
    product_name: str | None = Form(None, alias="productName"),
    is_confirmed: bool = Form(False, alias="isConfirmed"),
) -> dict[str, Any]:
    validate_alias(alias)
    if alias_form and alias_form != alias:
        raise HTTPException(status_code=400, detail="form alias must match path alias")

    reject_sm_certificate_fields(enc_cert, enc_ca_cert, enc_private_key, enc_password)
    ca_file, private_key_file = select_certificate_uploads(
        ca_cert_camel,
        ca_cert_snake,
        private_key_camel,
        private_key_snake,
    )

    cert_bytes = await read_upload(cert, "cert")
    ca_bytes = await read_optional_upload(ca_file)
    key_bytes = await read_optional_upload(private_key_file)

    tls_material = build_tls_material(
        cert_bytes=cert_bytes,
        ca_bytes=ca_bytes,
        key_bytes=key_bytes,
        password=password,
        is_confirmed=is_confirmed,
    )
    spec = build_tls_secret_spec(alias, tls_material, display_name, product_name, is_preset)
    write_tls_secret(spec)
    return certificate_response(spec, get_gateway_binding(spec.secret_name))


def _node_log_values(node: dict[str, Any]) -> Any:
    values = node.get("logTypes")
    return node.get("logInfo") if values is None else values


def _resolve_requested_log_type(node_type: str, log_type: str) -> str:
    if log_type == GATEWAY_LOG_TYPE and node_type in NODE_TYPE_MAP:
        return node_type
    if log_type in LEGACY_LOG_TYPE_TO_NODE_TYPE:
        return LEGACY_LOG_TYPE_TO_NODE_TYPE[log_type]
    if log_type in NODE_TYPE_MAP:
        return log_type
    raise HTTPException(status_code=400, detail=f"unsupported log type: {log_type}")


def _extend_requested_node_types(requested: list[str], node: dict[str, Any]) -> None:
    node_type = str(node.get("nodeType") or "")
    values = _node_log_values(node)
    if isinstance(values, list):
        for item in values:
            log_type = str(item or "")
            if log_type:
                requested.append(_resolve_requested_log_type(node_type, log_type))
    elif node_type in NODE_TYPE_MAP:
        requested.append(node_type)


def _unique_node_types(requested: list[str]) -> list[str]:
    unique = []
    for item in requested:
        if item not in NODE_TYPE_MAP:
            raise HTTPException(status_code=400, detail=f"unsupported nodeType: {item}")
        if item not in unique:
            unique.append(item)
    return unique


def parse_requested_node_types(payload: dict[str, Any]) -> list[str]:
    requested: list[str] = []
    for node in payload.get("nodeList") or []:
        if isinstance(node, dict):
            _extend_requested_node_types(requested, node)

    if not requested:
        return [item["nodeType"] for item in LOG_TYPES]
    return _unique_node_types(requested)


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
        "serverName": "AIDP-Gateway",
        "dispatchCallbackUrl": f"{base_url}/GatewayManager/Tenants/System/LogCollect/Dispatch",
        "queryProgressCallbackUrl": f"{base_url}/GatewayManager/Tenants/System/LogCollect/Progress",
        "queryNodesCallbackUrl": f"{base_url}/GatewayManager/Tenants/System/LogCollect/Nodes",
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
        base_url = f"http://gateway-manager.{GATEWAY_MANAGER_NAMESPACE}.svc.cluster.local:8080"
    return base_url.rstrip("/")


def env_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def build_collect_id(collect_user: str) -> str:
    safe_user = SAFE_ID_RE.sub("-", collect_user).strip("-") or "unknown"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{timestamp}-{safe_user}"


def node_archive_name(node_type: str) -> str:
    return NODE_ARCHIVE_NAMES.get(node_type, SAFE_ID_RE.sub("-", node_type.lower()).strip("-") or "gateway-log")


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
    node_name = safe_filename_part(node_archive_name(node_type), "gateway-log")
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


def _collect_gateway_resource_node(work_dir: Path, payload: dict[str, Any]) -> None:
    collect_gateway_resources(work_dir / "resources")


def _collect_gateway_controller_node(work_dir: Path, payload: dict[str, Any]) -> None:
    collect_controller_logs(work_dir / "controller", payload)


def _collect_gateway_proxy_node(work_dir: Path, payload: dict[str, Any]) -> None:
    collect_proxy_logs(work_dir / "proxy", payload)


def _collect_gateway_manager_node(work_dir: Path, payload: dict[str, Any]) -> None:
    collect_manager_logs(work_dir / "manager", payload)


def _collect_gateway_event_node(work_dir: Path, payload: dict[str, Any]) -> None:
    collect_gateway_events(work_dir / "events")


_NODE_COLLECTORS = {
    "AIDP_GATEWAY_RESOURCE": (_collect_gateway_resource_node, 35, "gateway resources collected"),
    "AIDP_GATEWAY_CONTROLLER": (_collect_gateway_controller_node, 50, "controller logs collected"),
    "AIDP_GATEWAY_PROXY": (_collect_gateway_proxy_node, 65, "proxy logs collected"),
    "AIDP_GATEWAY_MANAGER": (_collect_gateway_manager_node, 75, "manager logs collected"),
    "AIDP_GATEWAY_EVENT": (_collect_gateway_event_node, 85, "gateway events collected"),
}


def _collect_requested_nodes(
    collect_id: str, work_dir: "Path", payload: dict, node_types: list[str],
) -> None:
    for node_type, (collector_fn, progress, describe) in _NODE_COLLECTORS.items():
        if node_type not in node_types:
            continue
        mark_node(collect_id, node_type, NODE_COLLECTING, 20)
        collector_fn(work_dir, payload)
        mark_node(collect_id, node_type, NODE_SUCCESS, 100)
        update_task_progress(collect_id, progress, describe)


def _finalize_collect_task(
    collect_id: str, work_dir: "Path", archive_paths: list, upload_results: list,
) -> None:
    upload_success = not upload_results or all(r.get("success") for r in upload_results)
    final_status = COLLECT_FINISH if upload_success else COLLECT_PART_FAILED
    describe = (
        "log collect finished"
        if final_status == COLLECT_FINISH
        else "log collect finished with upload failures"
    )
    update_task_fields(collect_id, {
        "collectStatus": final_status,
        "progress": 100,
        "describe": describe,
        "uploadResults": upload_results,
    })
    if final_status == COLLECT_FINISH:
        shutil.rmtree(work_dir, ignore_errors=True)
        if upload_results:
            for archive_path in archive_paths:
                archive_path.unlink(missing_ok=True)


def _archive_file_names(archive_paths: list[Path]) -> set[str]:
    return {path.name for path in archive_paths}


def run_log_collect_task(collect_id: str, payload: dict[str, Any], node_types: list[str]) -> None:
    work_dir = LOG_TMP_DIR / collect_id
    archive_paths: list[Path] = []
    try:
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        LOG_TMP_DIR.mkdir(parents=True, exist_ok=True)

        write_json_file(work_dir / "metadata.json", {
            "serverName": "AIDP-Gateway",
            "collectId": collect_id,
            "collectUser": payload.get("collectUser"),
            "scene": payload.get("scene"),
            "startTime": payload.get("startTime"),
            "endTime": payload.get("endTime"),
            "gatewayNamespace": GATEWAY_NAMESPACE,
            "gatewayName": GATEWAY_NAME,
            "logTypes": [GATEWAY_LOG_TYPE],
            "nodeTypes": node_types,
        })
        update_task_progress(collect_id, 5, "metadata generated")

        _collect_requested_nodes(collect_id, work_dir, payload, node_types)

        archive_paths = make_node_archives(work_dir, payload, node_types)
        update_task_fields(collect_id, {
            "archiveFile": ",".join(str(p) for p in archive_paths),
            "archiveFiles": [str(p) for p in archive_paths],
            "progress": 90,
            "describe": "node archives generated",
        })

        upload_results = upload_archives(archive_paths, payload)
        _finalize_collect_task(collect_id, work_dir, archive_paths, upload_results)
        cleanup_log_tmp_dir(load_collect_state(), preserve_files=_archive_file_names(archive_paths))
    except Exception as exc:
        update_task_fields(collect_id, {
            "collectStatus": COLLECT_FAILED,
            "progress": 100,
            "describe": "log collect failed",
            "errorCode": "GATEWAY_LOG_COLLECT_FAILED",
            "errorMsg": str(exc),
        })
        cleanup_log_tmp_dir(load_collect_state(), preserve_files=_archive_file_names(archive_paths))


def collect_gateway_resources(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    resources = {
        "gatewayclasses": "/apis/gateway.networking.k8s.io/v1/gatewayclasses",
        "gateways-all-namespaces": "/apis/gateway.networking.k8s.io/v1/gateways",
        "httproutes-all-namespaces": "/apis/gateway.networking.k8s.io/v1/httproutes",
        "referencegrants-all-namespaces": "/apis/gateway.networking.k8s.io/v1beta1/referencegrants",
        "envoyproxies-all-namespaces": "/apis/gateway.envoyproxy.io/v1alpha1/envoyproxies",
        "securitypolicies-all-namespaces": "/apis/gateway.envoyproxy.io/v1alpha1/securitypolicies",
        "envoyextensionpolicies-all-namespaces": "/apis/gateway.envoyproxy.io/v1alpha1/envoyextensionpolicies",
        "backendtrafficpolicies-all-namespaces": "/apis/gateway.envoyproxy.io/v1alpha1/backendtrafficpolicies",
        "clienttrafficpolicies-all-namespaces": "/apis/gateway.envoyproxy.io/v1alpha1/clienttrafficpolicies",
    }
    summary: dict[str, Any] = {}
    for name, path in resources.items():
        summary[name] = fetch_k8s_json(path)
        write_json_file(output_dir / f"{name}.json", summary[name])
    write_json_file(output_dir / "resources.json", summary)


def collect_gateway_events(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json_file(output_dir / "events-all-namespaces.json", fetch_k8s_json("/api/v1/events"))
    write_json_file(output_dir / "pods-all-namespaces.json", fetch_k8s_json("/api/v1/pods"))
    write_json_file(output_dir / "services-all-namespaces.json", fetch_k8s_json("/api/v1/services"))
    write_json_file(output_dir / "deployments-all-namespaces.json", fetch_k8s_json("/apis/apps/v1/deployments"))


def collect_controller_logs(output_dir: Path, payload: dict[str, Any]) -> None:
    collect_pod_logs(
        output_dir=output_dir,
        label_selector="control-plane=envoy-gateway",
        container="envoy-gateway",
        payload=payload,
        fallback_file="envoy-gateway-controller.log",
    )


def collect_proxy_logs(output_dir: Path, payload: dict[str, Any]) -> None:
    selector = f"gateway.envoyproxy.io/owning-gateway-name={GATEWAY_NAME}"
    collect_pod_logs(
        output_dir=output_dir,
        label_selector=selector,
        container="envoy",
        payload=payload,
        fallback_file="envoy-data-plane.log",
        all_namespaces=True,
    )


def collect_manager_logs(output_dir: Path, payload: dict[str, Any]) -> None:
    collect_pod_logs(
        output_dir=output_dir,
        label_selector="app.kubernetes.io/name=gateway-manager",
        container="gateway-manager",
        payload=payload,
        fallback_file="gateway-manager.log",
    )


def collect_pod_logs(
    output_dir: Path,
    label_selector: str,
    container: str,
    payload: dict[str, Any],
    fallback_file: str,
    all_namespaces: bool = False,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    pods = list_pods(label_selector, all_namespaces=all_namespaces)
    if not pods:
        write_text_file(output_dir / fallback_file, f"No pods found for selector: {label_selector}\n")
        return

    for pod in pods:
        metadata = pod.get("metadata", {})
        namespace = metadata.get("namespace", GATEWAY_NAMESPACE)
        name = metadata.get("name", "unknown")
        log_text = fetch_pod_log(namespace, name, container, payload)
        write_text_file(output_dir / f"{namespace}_{name}.log", log_text)


def list_pods(label_selector: str, all_namespaces: bool = False) -> list[dict[str, Any]]:
    params = urlencode({"labelSelector": label_selector})
    if all_namespaces:
        path = f"/api/v1/pods?{params}"
    else:
        path = f"/api/v1/namespaces/{GATEWAY_MANAGER_NAMESPACE}/pods?{params}"
    data = fetch_k8s_json(path)
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
    except ValueError:
        return {"raw": response.text, "path": path}


def discover_log_nodes() -> list[dict[str, Any]]:
    nodes = []
    selectors = [
        ("envoy-gateway-controller", "AIDP_GATEWAY_CONTROLLER", "control-plane=envoy-gateway", False),
        ("envoy-data-plane", "AIDP_GATEWAY_PROXY", f"gateway.envoyproxy.io/owning-gateway-name={GATEWAY_NAME}", True),
        ("gateway-manager", "AIDP_GATEWAY_MANAGER", "app.kubernetes.io/name=gateway-manager", False),
    ]
    for display_name, node_type, selector, all_namespaces in selectors:
        pods = list_pods(selector, all_namespaces=all_namespaces)
        if not pods:
            nodes.append(build_log_node(display_name, node_type, None))
            continue
        nodes.append(build_log_node(display_name, node_type, pods[0]))
    nodes.append(build_log_node("gateway-kubernetes-resources", "AIDP_GATEWAY_RESOURCE", None, logical_ready=True))
    nodes.append(build_log_node("gateway-kubernetes-events", "AIDP_GATEWAY_EVENT", None, logical_ready=True))
    return nodes


def build_log_node(
    display_name: str,
    node_type: str,
    pod: dict[str, Any] | None,
    logical_ready: bool = False,
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


def _ssh_mkdir(ssh, dest_path: str) -> dict | None:
    """Create remote directory via SSH. Returns error dict on failure, None on success."""
    _, stdout, stderr = ssh.exec_command(f"mkdir -p -- {shlex.quote(dest_path)}", timeout=30)
    exit_status = stdout.channel.recv_exit_status()
    if exit_status != 0:
        reason = stderr.read().decode("utf-8", errors="replace").strip() or f"mkdir exited {exit_status}"
        return {"success": False, "opType": "SSH", "reason": reason}
    return None


def _scp_send(ssh, archive_path: "Path", remote_file: str) -> dict:
    """Transfer a file over SCP. Returns result dict."""
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
    return {"success": True, "opType": "SSH", "remote": remote_file}


def upload_archive_with_password_scp(
    archive_path: "Path",
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
            hostname=ip, port=port, username=user, password=password,
            look_for_keys=False, allow_agent=False,
            timeout=15, auth_timeout=15, banner_timeout=15,
        )
        mkdir_error = _ssh_mkdir(ssh, dest_path)
        if mkdir_error:
            return mkdir_error
        result = _scp_send(ssh, archive_path, remote_file)
        if result["success"]:
            result["remote"] = f"{user}@{ip}:{remote_file}"
        return result
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


def _write_file_to_zip(zip_file: zipfile.ZipFile, base_dir: Path, path: Path) -> None:
    if path.is_file():
        zip_file.write(path, path.relative_to(base_dir))


def _write_dir_to_zip(zip_file: zipfile.ZipFile, base_dir: Path, path: Path) -> None:
    if not path.is_dir():
        return
    for item in path.rglob("*"):
        _write_file_to_zip(zip_file, base_dir, item)


def make_zip_from_relative_paths(base_dir: Path, archive_path: Path, relative_paths: list[str]) -> None:
    archive_path.unlink(missing_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for relative_path in relative_paths:
            path = base_dir / relative_path
            _write_file_to_zip(zip_file, base_dir, path)
            _write_dir_to_zip(zip_file, base_dir, path)


def update_task_progress(collect_id: str, progress: int, describe: str) -> None:
    update_task_fields(collect_id, {"progress": progress, "describe": describe})


def update_task_fields(collect_id: str, fields: dict[str, Any]) -> None:
    with _STATE_LOCK:
        state = load_collect_state()
        task = state.setdefault("tasks", {}).setdefault(collect_id, {})
        task.update(fields)
        save_collect_state(state)


def _update_node_mark(
    node: dict[str, Any],
    node_type: str,
    state_value: int,
    progress: int,
    file_name: str,
    error: str,
) -> bool:
    if node.get("nodeType") != node_type:
        return False
    node["collectState"] = state_value
    node["progress"] = progress
    if file_name:
        node["fileName"] = file_name
    if error:
        node["errorMes"] = [error]
    return True


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
            if _update_node_mark(node, node_type, state_value, progress, file_name, error):
                break
        save_collect_state(state)


def load_collect_state() -> dict[str, Any]:
    if not kube_available():
        return json.loads(json.dumps(_MEMORY_STATE))
    path = f"/api/v1/namespaces/{GATEWAY_MANAGER_NAMESPACE}/configmaps/{LOG_STATUS_CONFIGMAP_NAME}"
    response = try_k8s_request("GET", path)
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
            "namespace": GATEWAY_MANAGER_NAMESPACE,
            "labels": {"app.kubernetes.io/name": "gateway-manager"},
        },
        "data": {"status.json": json.dumps(state, ensure_ascii=False, sort_keys=True)},
    }
    path = f"/api/v1/namespaces/{GATEWAY_MANAGER_NAMESPACE}/configmaps/{LOG_STATUS_CONFIGMAP_NAME}"
    response = k8s_request("PATCH", path, json_body=body, content_type="application/merge-patch+json")
    if response.status_code == 404:
        response = k8s_request("POST", f"/api/v1/namespaces/{GATEWAY_MANAGER_NAMESPACE}/configmaps", json_body=body)
    if response.status_code >= 300:
        raise HTTPException(status_code=500, detail=f"failed to write log collect status ConfigMap: {response.text}")


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


def write_json_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text_file(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")


def validate_alias(alias: str) -> None:
    if not ALIAS_RE.fullmatch(alias):
        raise HTTPException(
            status_code=400,
            detail="alias must be a DNS-1123 compatible name: lowercase letters, digits, and '-'",
        )


async def read_upload(upload: UploadFile | None, field_name: str) -> bytes:
    if upload is None:
        raise HTTPException(status_code=400, detail=f"{field_name} is required")
    file_bytes = await upload.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail=f"{field_name} is empty")
    return file_bytes


async def read_optional_upload(upload: UploadFile | None) -> bytes:
    if upload is None:
        return b""
    return await upload.read() or b""


def build_tls_material(
    cert_bytes: bytes,
    ca_bytes: bytes,
    key_bytes: bytes,
    password: str | None,
    is_confirmed: bool,
) -> tuple[bytes, bytes, bytes, x509.Certificate]:
    password_bytes = password.encode("utf-8") if password else None

    if not key_bytes:
        try:
            key, cert, ca_chain = pkcs12.load_key_and_certificates(cert_bytes, password_bytes)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail="privateKey is required unless cert is a valid PKCS#12/PFX bundle",
            ) from exc
        if key is None or cert is None:
            raise HTTPException(status_code=400, detail="PKCS#12/PFX bundle must contain certificate and private key")
        certs = [cert]
        ca_certs = list(ca_chain or [])
        if ca_bytes:
            ca_certs.extend(load_certificates(ca_bytes, "caCert"))
        tls_key_pem = serialize_private_key(key)
    else:
        certs = load_certificates(cert_bytes, "cert")
        ca_certs = load_certificates(ca_bytes, "caCert") if ca_bytes else []
        key = load_private_key(key_bytes, password_bytes)
        tls_key_pem = serialize_private_key(key)

    leaf_cert = certs[0]
    validate_certificate_time(leaf_cert, is_confirmed)
    validate_key_matches_cert(leaf_cert, key)

    tls_cert_pem = b"".join(cert.public_bytes(Encoding.PEM) for cert in certs + ca_certs)
    ca_pem = b"".join(cert.public_bytes(Encoding.PEM) for cert in ca_certs)
    return tls_cert_pem, ca_pem, tls_key_pem, leaf_cert


def load_certificates(data: bytes, field_name: str) -> list[x509.Certificate]:
    certs = []
    for block in PEM_CERT_RE.findall(data):
        certs.append(x509.load_pem_x509_certificate(block))
    if certs:
        return certs
    try:
        return [x509.load_der_x509_certificate(data)]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} is not a valid PEM or DER certificate") from exc


def load_private_key(data: bytes, password: bytes | None) -> Any:
    def _try_load(loader) -> Any:
        try:
            return loader(data, password=password)
        except (TypeError, ValueError, UnsupportedAlgorithm):
            return None

    for loader in (serialization.load_pem_private_key, serialization.load_der_private_key):
        key = _try_load(loader)
        if key is not None:
            return key
    raise HTTPException(
        status_code=400,
        detail="privateKey is not a valid PEM or DER private key, or password is wrong",
    )


def serialize_private_key(key: Any) -> bytes:
    return key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )


def validate_key_matches_cert(cert: x509.Certificate, key: Any) -> None:
    cert_public = cert.public_key().public_bytes(Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    key_public = key.public_key().public_bytes(Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if cert_public != key_public:
        raise HTTPException(status_code=400, detail="certificate and private key do not match")


def validate_certificate_time(cert: x509.Certificate, is_confirmed: bool) -> None:
    now = datetime.now(timezone.utc)
    not_before = cert_datetime(cert, "not_valid_before")
    not_after = cert_datetime(cert, "not_valid_after")
    if now < not_before:
        raise HTTPException(status_code=400, detail="certificate is not valid yet")
    if now > not_after and not is_confirmed:
        raise HTTPException(status_code=400, detail="certificate is expired; set isConfirmed=true to force import")


def cert_datetime(cert: x509.Certificate, attr: str) -> datetime:
    utc_attr = f"{attr}_utc"
    value = getattr(cert, utc_attr, None)
    if value is not None:
        return value
    return getattr(cert, attr).replace(tzinfo=timezone.utc)


def cert_time(cert: x509.Certificate, attr: str) -> str:
    return cert_datetime(cert, attr).isoformat().replace("+00:00", "Z")


@dataclass
class TlsSecretSpec:
    secret_name: str
    alias: str
    tls_cert_pem: bytes
    ca_pem: bytes
    tls_key_pem: bytes
    fingerprint: str
    not_before: str
    not_after: str
    display_name: str | None = None
    product_name: str | None = None
    is_preset: bool = False


def write_tls_secret(spec: TlsSecretSpec) -> None:
    patch_body = build_tls_secret_patch(spec)
    path = f"/api/v1/namespaces/{SECRET_NAMESPACE}/secrets/{spec.secret_name}"
    response = k8s_request("PATCH", path, json_body=patch_body, content_type="application/merge-patch+json")
    if response.status_code == 404:
        create_body = build_tls_secret_create_body(spec, patch_body)
        response = k8s_request("POST", f"/api/v1/namespaces/{SECRET_NAMESPACE}/secrets", json_body=create_body)
    if response.status_code >= 300:
        raise HTTPException(status_code=500, detail=f"failed to write Kubernetes Secret: {response.text}")


def build_tls_secret_patch(spec: TlsSecretSpec) -> dict[str, Any]:
    annotations = {
        "gateway.aidp.io/certificate-alias": spec.alias,
        "gateway.aidp.io/fingerprint-sha256": spec.fingerprint,
        "gateway.aidp.io/not-before": spec.not_before,
        "gateway.aidp.io/not-after": spec.not_after,
        "gateway.aidp.io/is-preset": str(spec.is_preset).lower(),
    }
    if spec.display_name:
        annotations["gateway.aidp.io/display-name"] = spec.display_name
    if spec.product_name:
        annotations["gateway.aidp.io/product-name"] = spec.product_name

    patch_body = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": spec.secret_name,
            "namespace": SECRET_NAMESPACE,
            "annotations": annotations,
        },
        "type": "kubernetes.io/tls",
        "data": {
            "tls.crt": b64(spec.tls_cert_pem),
            "tls.key": b64(spec.tls_key_pem),
        },
    }
    if spec.ca_pem:
        patch_body["data"]["ca.crt"] = b64(spec.ca_pem)
    else:
        patch_body["data"]["ca.crt"] = None
    return patch_body


def _secret_create_data(patch_body: dict[str, Any]) -> dict[str, str]:
    data = {}
    for key, value in patch_body["data"].items():
        if value is not None:
            data[key] = value
    return data


def build_tls_secret_create_body(spec: TlsSecretSpec, patch_body: dict[str, Any]) -> dict[str, Any]:
    create_body = dict(patch_body)
    create_body["metadata"] = dict(patch_body["metadata"])
    create_body["metadata"]["labels"] = {
        "app.kubernetes.io/name": "gateway-manager",
        "gateway.aidp.io/certificate-alias": spec.alias,
    }
    create_body["data"] = dict(patch_body["data"]) if spec.ca_pem else _secret_create_data(patch_body)
    return create_body


def get_gateway_binding(secret_name: str) -> dict[str, Any]:
    path = f"/apis/gateway.networking.k8s.io/v1/namespaces/{GATEWAY_NAMESPACE}/gateways/{GATEWAY_NAME}"
    response = k8s_request("GET", path)
    if response.status_code == 404:
        return {"gateway_bound": False}
    if response.status_code >= 300:
        return {"gateway_bound": False}

    gateway = response.json()
    for listener in gateway.get("spec", {}).get("listeners", []):
        tls = listener.get("tls") or {}
        for ref in tls.get("certificateRefs") or []:
            if ref.get("name") == secret_name:
                return {
                    "gateway_bound": True,
                    "gateway_name": gateway.get("metadata", {}).get("name"),
                    "listener_name": listener.get("name"),
                    "hostname": listener.get("hostname"),
                }
    return {"gateway_bound": False}


def k8s_request(
    method: str,
    path: str,
    json_body: dict[str, Any] | None = None,
    content_type: str | None = None,
) -> requests.Response:
    if not KUBE_HOST:
        raise HTTPException(status_code=500, detail="KUBERNETES_SERVICE_HOST is not set")
    with open(SA_TOKEN_PATH, "r", encoding="utf-8") as token_file:
        token = token_file.read().strip()
    headers = {"Authorization": f"Bearer {token}"}
    if content_type:
        headers["Content-Type"] = content_type
    url = f"https://{KUBE_HOST}:{KUBE_PORT}{path}"
    return requests.request(method, url, headers=headers, json=json_body, verify=SA_CA_PATH, timeout=30)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
