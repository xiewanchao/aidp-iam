import base64
import json
import os
import re
import shutil
import subprocess
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from cryptography import x509
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

LOG_TYPES = [
    {
        "serverName": "AIDP-Gateway",
        "logType": "GATEWAY_CONTROLLER_LOG",
        "nodeType": "AIDP_GATEWAY_CONTROLLER",
        "name": "Gateway Controller Log",
        "nameZh": "Gateway 控制面日志",
    },
    {
        "serverName": "AIDP-Gateway",
        "logType": "GATEWAY_PROXY_LOG",
        "nodeType": "AIDP_GATEWAY_PROXY",
        "name": "Gateway Proxy Log",
        "nameZh": "Gateway 数据面日志",
    },
    {
        "serverName": "AIDP-Gateway",
        "logType": "GATEWAY_MANAGER_LOG",
        "nodeType": "AIDP_GATEWAY_MANAGER",
        "name": "Gateway Manager Log",
        "nameZh": "Gateway 管理面日志",
    },
    {
        "serverName": "AIDP-Gateway",
        "logType": "GATEWAY_RESOURCE_YAML",
        "nodeType": "AIDP_GATEWAY_RESOURCE",
        "name": "Gateway Resource YAML",
        "nameZh": "Gateway 资源配置",
    },
    {
        "serverName": "AIDP-Gateway",
        "logType": "GATEWAY_EVENT",
        "nodeType": "AIDP_GATEWAY_EVENT",
        "name": "Gateway Kubernetes Event",
        "nameZh": "Gateway 事件",
    },
]
LOG_TYPE_MAP = {item["logType"]: item for item in LOG_TYPES}

_STATE_LOCK = threading.Lock()
_MEMORY_STATE: dict[str, Any] = {"currentCollectId": "", "tasks": {}}


@app.on_event("startup")
def recover_interrupted_log_collect_tasks() -> None:
    try:
        state = load_collect_state()
        changed = False
        for task in state.get("tasks", {}).values():
            if task.get("collectStatus") in {COLLECT_INIT, COLLECTING}:
                task["collectStatus"] = COLLECT_FAILED
                task["progress"] = 100
                task["describe"] = "gateway-manager restarted during log collection"
                task["errorCode"] = "GATEWAY_LOG_COLLECT_INTERRUPTED"
                task["errorMsg"] = "gateway-manager restarted before the log collect task completed"
                for node in task.get("nodeInfos", []):
                    if node.get("collectState") in {NODE_INIT, NODE_COLLECTING}:
                        node["collectState"] = NODE_FAILED
                        node["progress"] = 100
                changed = True
        if changed:
            save_collect_state(state)
        cleanup_log_tmp_dir(state)
    except Exception:
        # Startup must not block certificate management if log state recovery fails.
        return


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/log/types")
def get_log_types() -> dict[str, Any]:
    return {"code": 0, "data": LOG_TYPES, "message": "成功"}


@app.get("/log/nodes")
def get_log_nodes(page: int = Query(1, ge=1), limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    nodes = discover_log_nodes()
    start = (page - 1) * limit
    end = start + limit
    return {"items": nodes[start:end], "total": len(nodes), "page": page, "limit": limit}


@app.get("/log/logCollect")
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
            "message": "成功",
        }
    task = state.get("tasks", {}).get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"log collect task not found: {task_id}")
    return {"code": 0, "data": task_to_response(task), "message": "成功"}


@app.post("/log/logCollect")
async def post_log_collect(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")

    log_types = parse_requested_log_types(payload)
    collect_user = str(payload.get("collectUser") or "unknown")
    collect_id = build_collect_id(collect_user)

    with _STATE_LOCK:
        state = load_collect_state()
        current_id = state.get("currentCollectId")
        current_task = state.get("tasks", {}).get(current_id) if current_id else None
        if current_task and current_task.get("collectStatus") in {COLLECT_INIT, COLLECTING}:
            raise HTTPException(status_code=409, detail=f"log collect task is already running: {current_id}")

        task = build_initial_task(collect_id, collect_user, payload, log_types)
        state.setdefault("tasks", {})[collect_id] = task
        state["currentCollectId"] = collect_id
        save_collect_state(state)

    background_tasks.add_task(run_log_collect_task, collect_id, payload, log_types)
    return {"code": 0, "data": "log collect accepted", "message": "成功"}


@app.put("/GatewayManager/Tenants/System/Certificates/{alias}")
async def put_gateway_certificate(
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

    if enc_cert or enc_ca_cert or enc_private_key or enc_password:
        raise HTTPException(
            status_code=400,
            detail="SM dual-certificate fields are not supported by the standard Gateway API TLS Secret path",
        )

    ca_file = ca_cert_camel or ca_cert_snake
    private_key_file = private_key_camel or private_key_snake

    cert_bytes = await read_upload(cert, "cert")
    ca_bytes = await read_optional_upload(ca_file)
    key_bytes = await read_optional_upload(private_key_file)

    tls_cert_pem, ca_pem, tls_key_pem, leaf_cert = build_tls_material(
        cert_bytes=cert_bytes,
        ca_bytes=ca_bytes,
        key_bytes=key_bytes,
        password=password,
        is_confirmed=is_confirmed,
    )

    secret_name = f"{SECRET_PREFIX}{alias}"
    fingerprint = leaf_cert.fingerprint(hashes.SHA256()).hex()
    not_before = cert_time(leaf_cert, "not_valid_before")
    not_after = cert_time(leaf_cert, "not_valid_after")

    write_tls_secret(
        secret_name=secret_name,
        alias=alias,
        tls_cert_pem=tls_cert_pem,
        ca_pem=ca_pem,
        tls_key_pem=tls_key_pem,
        display_name=display_name,
        product_name=product_name,
        is_preset=is_preset,
        fingerprint=fingerprint,
        not_before=not_before,
        not_after=not_after,
    )

    binding = get_gateway_binding(secret_name)
    return {
        "alias": alias,
        "display_name": display_name,
        "product_name": product_name,
        "secret_name": secret_name,
        "secret_namespace": SECRET_NAMESPACE,
        "status": "Ready",
        "gateway_bound": binding["gateway_bound"],
        "gateway_name": binding.get("gateway_name"),
        "listener_name": binding.get("listener_name"),
        "hostname": binding.get("hostname"),
        "not_before": not_before,
        "not_after": not_after,
        "fingerprint_sha256": fingerprint,
        "message": "certificate secret updated",
    }


def parse_requested_log_types(payload: dict[str, Any]) -> list[str]:
    requested: list[str] = []
    for node in payload.get("nodeList") or []:
        if not isinstance(node, dict):
            continue
        values = node.get("logTypes")
        if values is None:
            values = node.get("logInfo")
        if isinstance(values, list):
            requested.extend(str(item) for item in values if item)

    if not requested:
        return [item["logType"] for item in LOG_TYPES]

    unique = []
    for item in requested:
        if item not in LOG_TYPE_MAP:
            raise HTTPException(status_code=400, detail=f"unsupported log type: {item}")
        if item not in unique:
            unique.append(item)
    return unique


def build_collect_id(collect_user: str) -> str:
    safe_user = SAFE_ID_RE.sub("-", collect_user).strip("-") or "unknown"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{timestamp}-{safe_user}"


def build_initial_task(
    collect_id: str,
    collect_user: str,
    payload: dict[str, Any],
    log_types: list[str],
) -> dict[str, Any]:
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
                "name": LOG_TYPE_MAP[log_type]["name"],
                "nodeIp": "",
                "nodeType": LOG_TYPE_MAP[log_type]["nodeType"],
                "progress": 0,
                "collectState": NODE_INIT,
                "fileName": "",
                "errorCode": "",
                "errorMes": [],
            }
            for log_type in log_types
        ],
    }


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


def run_log_collect_task(collect_id: str, payload: dict[str, Any], log_types: list[str]) -> None:
    work_dir = LOG_TMP_DIR / collect_id
    archive_path = LOG_TMP_DIR / f"{collect_id}.zip"
    try:
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        LOG_TMP_DIR.mkdir(parents=True, exist_ok=True)

        write_json_file(
            work_dir / "metadata.json",
            {
                "serverName": "AIDP-Gateway",
                "collectId": collect_id,
                "collectUser": payload.get("collectUser"),
                "scene": payload.get("scene"),
                "startTime": payload.get("startTime"),
                "endTime": payload.get("endTime"),
                "gatewayNamespace": GATEWAY_NAMESPACE,
                "gatewayName": GATEWAY_NAME,
                "logTypes": log_types,
            },
        )
        update_task_progress(collect_id, 5, "metadata generated")

        if "GATEWAY_RESOURCE_YAML" in log_types:
            mark_node(collect_id, "AIDP_GATEWAY_RESOURCE", NODE_COLLECTING, 10)
            collect_gateway_resources(work_dir / "resources")
            mark_node(collect_id, "AIDP_GATEWAY_RESOURCE", NODE_SUCCESS, 100, "resources/resources.json")
        update_task_progress(collect_id, 35, "gateway resources collected")

        if "GATEWAY_CONTROLLER_LOG" in log_types:
            mark_node(collect_id, "AIDP_GATEWAY_CONTROLLER", NODE_COLLECTING, 20)
            collect_controller_logs(work_dir / "controller", payload)
            mark_node(collect_id, "AIDP_GATEWAY_CONTROLLER", NODE_SUCCESS, 100, "controller/")
        update_task_progress(collect_id, 50, "controller logs collected")

        if "GATEWAY_PROXY_LOG" in log_types:
            mark_node(collect_id, "AIDP_GATEWAY_PROXY", NODE_COLLECTING, 20)
            collect_proxy_logs(work_dir / "proxy", payload)
            mark_node(collect_id, "AIDP_GATEWAY_PROXY", NODE_SUCCESS, 100, "proxy/")
        update_task_progress(collect_id, 65, "proxy logs collected")

        if "GATEWAY_MANAGER_LOG" in log_types:
            mark_node(collect_id, "AIDP_GATEWAY_MANAGER", NODE_COLLECTING, 20)
            collect_manager_logs(work_dir / "manager", payload)
            mark_node(collect_id, "AIDP_GATEWAY_MANAGER", NODE_SUCCESS, 100, "manager/")
        update_task_progress(collect_id, 75, "manager logs collected")

        if "GATEWAY_EVENT" in log_types:
            mark_node(collect_id, "AIDP_GATEWAY_EVENT", NODE_COLLECTING, 20)
            collect_gateway_events(work_dir / "events")
            mark_node(collect_id, "AIDP_GATEWAY_EVENT", NODE_SUCCESS, 100, "events/events.json")
        update_task_progress(collect_id, 85, "gateway events collected")

        make_zip(work_dir, archive_path)
        update_task_fields(collect_id, {"archiveFile": str(archive_path), "progress": 90, "describe": "archive generated"})

        upload_results = upload_archive(archive_path, payload)
        final_status = COLLECT_FINISH if all(item.get("success") for item in upload_results) else COLLECT_PART_FAILED
        if not upload_results:
            final_status = COLLECT_FINISH
        update_task_fields(
            collect_id,
            {
                "collectStatus": final_status,
                "progress": 100,
                "describe": "log collect finished" if final_status == COLLECT_FINISH else "log collect finished with upload failures",
                "uploadResults": upload_results,
            },
        )

        if final_status == COLLECT_FINISH:
            shutil.rmtree(work_dir, ignore_errors=True)
            if upload_results:
                archive_path.unlink(missing_ok=True)
        cleanup_log_tmp_dir(load_collect_state(), preserve_files={archive_path.name})
    except Exception as exc:
        update_task_fields(
            collect_id,
            {
                "collectStatus": COLLECT_FAILED,
                "progress": 100,
                "describe": "log collect failed",
                "errorCode": "GATEWAY_LOG_COLLECT_FAILED",
                "errorMsg": str(exc),
            },
        )
        cleanup_log_tmp_dir(load_collect_state(), preserve_files={archive_path.name})


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
    except Exception:
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
            nodes.append(
                {
                    "name": display_name,
                    "status": "OFFLINE" if kube_available() else "UNKNOWN",
                    "nodeType": node_type,
                    "product": "AIDP",
                    "nodeIp": "",
                }
            )
            continue
        for pod in pods:
            metadata = pod.get("metadata", {})
            status = pod.get("status", {})
            nodes.append(
                {
                    "name": metadata.get("name", display_name),
                    "status": "READY" if is_pod_ready(pod) else status.get("phase", "UNKNOWN"),
                    "nodeType": node_type,
                    "product": "AIDP",
                    "nodeIp": status.get("podIP", ""),
                }
            )
    return nodes


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
        return {
            "success": False,
            "opType": "SSH",
            "reason": "password based SCP requires paramiko or sshpass; no extra dependency is bundled in gateway-manager",
        }
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


def make_zip(source_dir: Path, archive_path: Path) -> None:
    archive_path.unlink(missing_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for path in source_dir.rglob("*"):
            if path.is_file():
                zip_file.write(path, path.relative_to(source_dir))


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


def load_collect_state() -> dict[str, Any]:
    if not kube_available():
        return json.loads(json.dumps(_MEMORY_STATE))
    response = try_k8s_request("GET", f"/api/v1/namespaces/{GATEWAY_MANAGER_NAMESPACE}/configmaps/{LOG_STATUS_CONFIGMAP_NAME}")
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
    response = k8s_request("PATCH", path, json=body, content_type="application/merge-patch+json")
    if response.status_code == 404:
        response = k8s_request("POST", f"/api/v1/namespaces/{GATEWAY_MANAGER_NAMESPACE}/configmaps", json=body)
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
    return k8s_request(method, path, json=json_body)


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
    data = await upload.read()
    if not data:
        raise HTTPException(status_code=400, detail=f"{field_name} is empty")
    return data


async def read_optional_upload(upload: UploadFile | None) -> bytes:
    if upload is None:
        return b""
    data = await upload.read()
    return data or b""


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
    for loader in (serialization.load_pem_private_key, serialization.load_der_private_key):
        try:
            return loader(data, password=password)
        except Exception:
            continue
    raise HTTPException(status_code=400, detail="privateKey is not a valid PEM or DER private key, or password is wrong")


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


def write_tls_secret(
    secret_name: str,
    alias: str,
    tls_cert_pem: bytes,
    ca_pem: bytes,
    tls_key_pem: bytes,
    display_name: str | None,
    product_name: str | None,
    is_preset: bool,
    fingerprint: str,
    not_before: str,
    not_after: str,
) -> None:
    annotations = {
        "gateway.aidp.io/certificate-alias": alias,
        "gateway.aidp.io/fingerprint-sha256": fingerprint,
        "gateway.aidp.io/not-before": not_before,
        "gateway.aidp.io/not-after": not_after,
        "gateway.aidp.io/is-preset": str(is_preset).lower(),
    }
    if display_name:
        annotations["gateway.aidp.io/display-name"] = display_name
    if product_name:
        annotations["gateway.aidp.io/product-name"] = product_name

    body = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": secret_name,
            "namespace": SECRET_NAMESPACE,
            "labels": {
                "app.kubernetes.io/name": "gateway-manager",
                "gateway.aidp.io/certificate-alias": alias,
            },
            "annotations": annotations,
        },
        "type": "kubernetes.io/tls",
        "data": {
            "tls.crt": b64(tls_cert_pem),
            "tls.key": b64(tls_key_pem),
        },
    }
    if ca_pem:
        body["data"]["ca.crt"] = b64(ca_pem)

    path = f"/api/v1/namespaces/{SECRET_NAMESPACE}/secrets/{secret_name}"
    response = k8s_request("PATCH", path, json=body, content_type="application/merge-patch+json")
    if response.status_code == 404:
        response = k8s_request("POST", f"/api/v1/namespaces/{SECRET_NAMESPACE}/secrets", json=body)
    if response.status_code >= 300:
        raise HTTPException(status_code=500, detail=f"failed to write Kubernetes Secret: {response.text}")


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
    json: dict[str, Any] | None = None,
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
    return requests.request(method, url, headers=headers, json=json, verify=SA_CA_PATH, timeout=30)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
