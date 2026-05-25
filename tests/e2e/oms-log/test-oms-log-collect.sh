#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

GW_NS="${GW_NS:-aidp-gateway}"
GW_DEPLOY="${GW_DEPLOY:-gateway-manager}"
IAM_NS="${IAM_NS:-aidp-iam}"
IAM_DEPLOY="${IAM_DEPLOY:-iam-services}"
IAM_CONTAINER="${IAM_CONTAINER:-aidp-iam-app}"

MOCK_OMS_NS="${MOCK_OMS_NS:-oms-log-test}"
MOCK_OMS_NAME="${MOCK_OMS_NAME:-oms-log-mock}"
MOCK_OMS_IMAGE="${MOCK_OMS_IMAGE:-aidp-iam-app:v1}"
MOCK_OMS_PORT="${MOCK_OMS_PORT:-18090}"
MOCK_REGISTER_URL="http://${MOCK_OMS_NAME}.${MOCK_OMS_NS}.svc.cluster.local:${MOCK_OMS_PORT}/log/type/register/internal"

START_TIME="${START_TIME:-2026-05-12 17:15:00}"
END_TIME="${END_TIME:-2026-05-13 17:15:00}"
LOG_FILE="${LOG_FILE:-${SCRIPT_DIR}/oms-log-collect-test-log.txt}"
TMP_DIR="${TMP_DIR:-/tmp/aidp-oms-log-tests}"
GW_RUNTIME_DIR="${GW_RUNTIME_DIR:-/tmp/gateway-log-collect/runtime}"
GW_WORK_DIR="${GW_WORK_DIR:-/tmp/gateway-log-collect/oms-blackbox-test}"
IAM_WORK_DIR="${IAM_WORK_DIR:-/tmp/oms-iam-blackbox-test}"
GW_UPLOAD_DIR="${GW_UPLOAD_DIR:-${GW_WORK_DIR}/upload}"
IAM_UPLOAD_DIR="${IAM_UPLOAD_DIR:-${IAM_WORK_DIR}/upload}"
GW_PAYLOAD_REMOTE="${GW_PAYLOAD_REMOTE:-${GW_WORK_DIR}/gateway-dispatch.json}"
IAM_PAYLOAD_REMOTE="${IAM_PAYLOAD_REMOTE:-${IAM_WORK_DIR}/iam-dispatch.json}"

PASS=0
FAIL=0
SKIP=0
LAST_OUT=""
LAST_RC=0
GW_POD=""
IAM_POD=""

mkdir -p "$(dirname "${LOG_FILE}")" "${TMP_DIR}"
: > "${LOG_FILE}"
exec > >(tee -a "${LOG_FILE}") 2>&1

print_header() {
  echo
  echo "================================================================================"
  echo "$1"
  echo "================================================================================"
}

run_cmd() {
  local title="$1"
  local cmd="$2"
  echo
  echo "[STEP] ${title}"
  echo "[COMMAND]"
  echo "\$ ${cmd}"
  set +e
  LAST_OUT="$(bash -lc "${cmd}" 2>&1)"
  LAST_RC=$?
  set -u
  echo "[RESULT]"
  if [ -n "${LAST_OUT}" ]; then
    printf '%s\n' "${LAST_OUT}"
  else
    echo "(no output)"
  fi
  echo "[EXIT_CODE] ${LAST_RC}"
}

pass() {
  echo "[ASSERT] PASS - $1"
  PASS=$((PASS + 1))
}

fail() {
  echo "[ASSERT] FAIL - $1"
  FAIL=$((FAIL + 1))
}

skip() {
  echo "[ASSERT] SKIP - $1"
  SKIP=$((SKIP + 1))
}

assert_rc_zero() {
  if [ "${LAST_RC}" -eq 0 ]; then
    pass "$1"
  else
    fail "$1"
  fi
}

assert_contains() {
  local desc="$1"
  local needle="$2"
  if printf '%s' "${LAST_OUT}" | grep -Fq "${needle}"; then
    pass "${desc}"
  else
    fail "${desc}; missing: ${needle}"
  fi
}

assert_regex() {
  local desc="$1"
  local pattern="$2"
  if printf '%s' "${LAST_OUT}" | grep -Eq "${pattern}"; then
    pass "${desc}"
  else
    fail "${desc}; regex not matched: ${pattern}"
  fi
}

require_tool() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing command: $1"
    exit 1
  fi
}

write_mock_server() {
  cat > "${TMP_DIR}/oms-mock-server.py" <<'PY'
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STORE = "/tmp/oms-registrations.jsonl"

class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {"raw": raw}
        record = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "path": self.path,
            "body": parsed,
        }
        with open(STORE, "a", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._send_json(200, {"code": 0, "data": True, "message": "success"})

    def do_GET(self):
        if self.path.startswith("/registrations"):
            items = []
            if os.path.exists(STORE):
                with open(STORE, "r", encoding="utf-8") as file_obj:
                    for line in file_obj:
                        line = line.strip()
                        if line:
                            items.append(json.loads(line))
            self._send_json(200, {"items": items, "total": len(items)})
            return
        self._send_json(200, {"status": "ok"})

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("MOCK_OMS_PORT", "18090"))), Handler).serve_forever()
PY
}

setup_mock_oms() {
  print_header "PREPARE: start temporary OMS mock service"
  write_mock_server
  run_cmd "Create mock OMS namespace" "kubectl create namespace '${MOCK_OMS_NS}' --dry-run=client -o yaml | kubectl apply -f -"
  assert_rc_zero "mock OMS namespace is ready"

  run_cmd "Create mock OMS server ConfigMap" \
    "kubectl -n '${MOCK_OMS_NS}' create configmap '${MOCK_OMS_NAME}' --from-file=server.py='${TMP_DIR}/oms-mock-server.py' --dry-run=client -o yaml | kubectl apply -f -"
  assert_rc_zero "mock OMS ConfigMap is ready"

  cat > "${TMP_DIR}/oms-mock.yaml" <<YAML
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${MOCK_OMS_NAME}
  namespace: ${MOCK_OMS_NS}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ${MOCK_OMS_NAME}
  template:
    metadata:
      labels:
        app: ${MOCK_OMS_NAME}
    spec:
      containers:
      - name: mock
        image: ${MOCK_OMS_IMAGE}
        imagePullPolicy: IfNotPresent
        command: ["python3", "/opt/oms/server.py"]
        env:
        - name: MOCK_OMS_PORT
          value: "${MOCK_OMS_PORT}"
        ports:
        - containerPort: ${MOCK_OMS_PORT}
        volumeMounts:
        - name: server
          mountPath: /opt/oms
      volumes:
      - name: server
        configMap:
          name: ${MOCK_OMS_NAME}
---
apiVersion: v1
kind: Service
metadata:
  name: ${MOCK_OMS_NAME}
  namespace: ${MOCK_OMS_NS}
spec:
  selector:
    app: ${MOCK_OMS_NAME}
  ports:
  - name: http
    port: ${MOCK_OMS_PORT}
    targetPort: ${MOCK_OMS_PORT}
YAML
  run_cmd "Apply mock OMS Deployment and Service" "kubectl apply -f '${TMP_DIR}/oms-mock.yaml'"
  assert_rc_zero "mock OMS Deployment and Service are applied"
  run_cmd "Wait mock OMS deployment ready" "kubectl -n '${MOCK_OMS_NS}' rollout status deploy/${MOCK_OMS_NAME} --timeout=180s"
  assert_rc_zero "mock OMS Pod is ready"
  run_cmd "Clear mock OMS registration records" "kubectl -n '${MOCK_OMS_NS}' exec deploy/${MOCK_OMS_NAME} -- sh -c 'rm -f /tmp/oms-registrations.jsonl; echo cleared'"
  assert_contains "mock OMS registration records are cleared" "cleared"
}

configure_components() {
  print_header "PREPARE: enable Gateway/IAM auto registration to mock OMS"
  run_cmd "Configure and restart Gateway log registration" \
    "MSYS_NO_PATHCONV=1 kubectl -n '${GW_NS}' set env deploy/${GW_DEPLOY} LOG_TMP_DIR='${GW_RUNTIME_DIR}' OMS_LOG_REGISTRATION_ENABLED=true OMS_LOG_REGISTER_URL='${MOCK_REGISTER_URL}' OMS_LOG_CALLBACK_BASE_URL='http://gateway-manager.${GW_NS}.svc.cluster.local:8080' OMS_LOG_REGISTER_MAX_RETRIES=5 OMS_LOG_REGISTER_RETRY_INTERVAL_SECONDS=2 >/dev/null && kubectl -n '${GW_NS}' rollout restart deploy/${GW_DEPLOY} >/dev/null && kubectl -n '${GW_NS}' rollout status deploy/${GW_DEPLOY} --timeout=240s"
  assert_rc_zero "Gateway is restarted with OMS registration settings"

  run_cmd "Configure and restart IAM log registration" \
    "kubectl -n '${IAM_NS}' set env deploy/${IAM_DEPLOY} --containers='${IAM_CONTAINER}' OMS_LOG_REGISTRATION_ENABLED=true OMS_LOG_REGISTER_URL='${MOCK_REGISTER_URL}' OMS_LOG_CALLBACK_BASE_URL='http://keycloak-proxy.${IAM_NS}.svc.cluster.local:8090' OMS_LOG_REGISTER_MAX_RETRIES=5 OMS_LOG_REGISTER_RETRY_INTERVAL_SECONDS=2 >/dev/null && kubectl -n '${IAM_NS}' rollout restart deploy/${IAM_DEPLOY} >/dev/null && kubectl -n '${IAM_NS}' rollout status deploy/${IAM_DEPLOY} --timeout=300s"
  assert_rc_zero "IAM is restarted with OMS registration settings"
}

get_pods() {
  kubectl -n "${GW_NS}" wait pod -l app.kubernetes.io/name=gateway-manager --for=condition=Ready --timeout=180s >/dev/null
  kubectl -n "${IAM_NS}" wait pod -l app=iam-services --for=condition=Ready --timeout=180s >/dev/null
  GW_POD="$(kubectl -n "${GW_NS}" get pod -l app.kubernetes.io/name=gateway-manager --field-selector=status.phase=Running --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}' 2>/dev/null || true)"
  IAM_POD="$(kubectl -n "${IAM_NS}" get pod -l app=iam-services --field-selector=status.phase=Running --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}' 2>/dev/null || true)"
  if [ -z "${GW_POD}" ]; then
    echo "gateway-manager Pod not found"
    exit 1
  fi
  if [ -z "${IAM_POD}" ]; then
    echo "iam-services Pod not found"
    exit 1
  fi
}

write_dispatch_payloads() {
  cat > "${TMP_DIR}/wait-progress.py" <<'PY'
import json
import sys
import time
import urllib.request

url = sys.argv[1]
last = {}
for _ in range(60):
    last = json.loads(urllib.request.urlopen(url, timeout=10).read().decode())
    status = last.get("data", {}).get("basicInfo", {}).get("collectStatus", "")
    if status in ("FINISH", "FAILED", "PART_FAILED"):
        break
    time.sleep(2)

print(json.dumps(last, ensure_ascii=False, indent=2))
raise SystemExit(0 if last.get("data", {}).get("basicInfo", {}).get("collectStatus") == "FINISH" else 1)
PY

  cat > "${TMP_DIR}/gateway-dispatch.json" <<JSON
{
  "collectUser": "oms-test",
  "scene": "oms_blackbox_gateway",
  "startTime": "${START_TIME}",
  "endTime": "${END_TIME}",
  "path": "${GW_UPLOAD_DIR}",
  "targets": [{"opType": "LOCAL"}],
  "nodeList": [
    {"name": "gateway-manager", "nodeIp": "127.0.0.1", "nodeType": "AIDP_GATEWAY_MANAGER", "status": "READY", "product": "AIDP", "logTypes": ["AIDP_GATEWAY_LOG"]},
    {"name": "gateway-resource", "nodeIp": "127.0.0.1", "nodeType": "AIDP_GATEWAY_RESOURCE", "status": "READY", "product": "AIDP", "logTypes": ["AIDP_GATEWAY_LOG"]}
  ]
}
JSON

  cat > "${TMP_DIR}/iam-dispatch.json" <<JSON
{
  "collectUser": "oms-test",
  "scene": "oms_blackbox_iam",
  "startTime": "${START_TIME}",
  "endTime": "${END_TIME}",
  "path": "${IAM_UPLOAD_DIR}",
  "targets": [{"opType": "LOCAL"}],
  "nodeList": [
    {"name": "iam-keycloak-proxy", "nodeIp": "127.0.0.1", "nodeType": "AIDP_IAM_KEYCLOAK_PROXY", "status": "READY", "product": "AIDP", "logTypes": ["AIDP_IAM_LOG"]},
    {"name": "iam-opa", "nodeIp": "127.0.0.1", "nodeType": "AIDP_IAM_OPA", "status": "READY", "product": "AIDP", "logTypes": ["AIDP_IAM_LOG"]}
  ]
}
JSON
}

wait_registration() {
  local name="$1"
  run_cmd "Wait ${name} appears in mock OMS registration records" \
    "for i in \$(seq 1 60); do kubectl -n '${MOCK_OMS_NS}' exec deploy/${MOCK_OMS_NAME} -- sh -c \"grep -q '${name}' /tmp/oms-registrations.jsonl 2>/dev/null\" && echo 'found ${name}' && exit 0; sleep 2; done; echo 'not found ${name}'; exit 1"
}

show_registrations() {
  run_cmd "Show mock OMS registration summary" \
    "kubectl -n '${MOCK_OMS_NS}' exec deploy/${MOCK_OMS_NAME} -- python3 -c 'import json; p=\"/tmp/oms-registrations.jsonl\"; f=open(p, encoding=\"utf-8\"); [print(json.dumps({\"serverName\": json.loads(line)[\"body\"].get(\"serverName\"), \"logTypeCount\": len(json.loads(line)[\"body\"].get(\"logTypeVos\", []))}, ensure_ascii=False)) for line in f if line.strip()]'"
}

copy_payload_to_pod() {
  local namespace="$1"
  local pod="$2"
  local container="$3"
  local source="$4"
  local target="$5"
  run_cmd "Copy collect payload to ${namespace}/${pod}:${target}" \
    "kubectl -n '${namespace}' exec -i '${pod}' -c '${container}' -- sh -c 'mkdir -p \"\$(dirname \"${target}\")\" && cat > \"${target}\"' < '${source}'"
  assert_rc_zero "collect payload is copied to ${pod}:${target}"
}

post_from_pod() {
  local namespace="$1"
  local pod="$2"
  local container="$3"
  local payload="$4"
  local url="$5"
  run_cmd "Start log collect task from inside component Pod" \
    "kubectl -n '${namespace}' exec '${pod}' -c '${container}' -- python3 -c 'import urllib.request; data=open(\"${payload}\", \"rb\").read(); req=urllib.request.Request(\"${url}\", data=data, headers={\"Content-Type\":\"application/json\"}); print(urllib.request.urlopen(req, timeout=30).read().decode())'"
}

wait_progress_from_pod() {
  local namespace="$1"
  local pod="$2"
  local container="$3"
  local url="$4"
  local label="$5"
  local work_dir="$6"
  local helper="${work_dir}/wait-progress.py"
  run_cmd "Copy progress wait helper to ${namespace}/${pod}:${helper}" \
    "kubectl -n '${namespace}' exec -i '${pod}' -c '${container}' -- sh -c 'mkdir -p \"${work_dir}\" && cat > \"${helper}\"' < '${TMP_DIR}/wait-progress.py'"
  assert_rc_zero "progress wait helper is copied to ${pod}:${helper}"
  run_cmd "Wait ${label} log collect task finished and print progress" \
    "MSYS_NO_PATHCONV=1 kubectl -n '${namespace}' exec '${pod}' -c '${container}' -- python3 '${helper}' '${url}'"
}

check_archives() {
  local namespace="$1"
  local pod="$2"
  local container="$3"
  local dir="$4"
  local expected_a="$5"
  local expected_b="$6"
  local label="$7"
  run_cmd "Check ${label} zip archives in simulated OMS archive directory" \
    "kubectl -n '${namespace}' exec '${pod}' -c '${container}' -- python3 -c 'import glob, os, zipfile; paths=sorted(glob.glob(\"${dir}/*.zip\")); print(\"archives=\" + \",\".join(os.path.basename(p) for p in paths)); assert any(os.path.basename(p).startswith(\"${expected_a}_\") for p in paths); assert any(os.path.basename(p).startswith(\"${expected_b}_\") for p in paths); assert not any(\" \" in os.path.basename(p) for p in paths); [zipfile.ZipFile(p).testzip() for p in paths]; print(\"zip check ok\")'"
}

case_log_tc_001() {
  print_header "LOG-TC-001 Gateway log objects appear in OMS"
  wait_registration "AIDP-Gateway"
  assert_contains "Gateway log object appears in OMS records" "found AIDP-Gateway"
  show_registrations
  assert_contains "Gateway registration summary is visible" "AIDP-Gateway"
}

case_log_tc_002() {
  print_header "LOG-TC-002 Gateway log collect uploads separate zip archives"
  copy_payload_to_pod "${GW_NS}" "${GW_POD}" "gateway-manager" "${TMP_DIR}/gateway-dispatch.json" "${GW_PAYLOAD_REMOTE}"
  run_cmd "Clear Gateway simulated OMS archive directory" "kubectl -n '${GW_NS}' exec '${GW_POD}' -c gateway-manager -- sh -c 'rm -rf \"${GW_UPLOAD_DIR}\" && mkdir -p \"${GW_UPLOAD_DIR}\" && echo cleared'"
  assert_contains "Gateway archive directory is cleared" "cleared"
  post_from_pod "${GW_NS}" "${GW_POD}" "gateway-manager" "${GW_PAYLOAD_REMOTE}" "http://localhost:8080/GatewayManager/Tenants/System/LogCollect/Dispatch"
  assert_regex "Gateway log collect task is accepted" '"data"[[:space:]]*:[[:space:]]*true'
  wait_progress_from_pod "${GW_NS}" "${GW_POD}" "gateway-manager" "http://localhost:8080/GatewayManager/Tenants/System/LogCollect/Progress" "Gateway" "${GW_WORK_DIR}"
  assert_rc_zero "Gateway log collect task finished"
  check_archives "${GW_NS}" "${GW_POD}" "gateway-manager" "${GW_UPLOAD_DIR}" "gateway-manager" "gateway-resource" "Gateway"
  assert_contains "Gateway zip archives are readable" "zip check ok"
}

case_log_tc_003() {
  print_header "LOG-TC-003 Gateway progress is displayable by OMS"
  run_cmd "Read Gateway log collect progress and validate display fields" \
    "kubectl -n '${GW_NS}' exec '${GW_POD}' -c gateway-manager -- python3 -c 'import json,urllib.request; data=json.loads(urllib.request.urlopen(\"http://localhost:8080/GatewayManager/Tenants/System/LogCollect/Progress\", timeout=10).read().decode()); print(json.dumps(data, ensure_ascii=False, indent=2)); nodes=data.get(\"data\",{}).get(\"nodeInfos\",[]); assert data.get(\"data\",{}).get(\"basicInfo\",{}).get(\"collectStatus\") == \"FINISH\"; assert all(\" \" not in n.get(\"name\", \"\") for n in nodes); assert all(n.get(\"fileName\", \"\").endswith(\".zip\") for n in nodes); assert all(n.get(\"nodeIp\") for n in nodes)'"
  assert_rc_zero "Gateway progress is successful and file names are valid"
  assert_contains "Gateway progress shows FINISH" '"collectStatus": "FINISH"'
  assert_regex "Gateway progress contains zip file names" 'gateway-(manager|resource)_.*\.zip'
}

case_log_tc_004() {
  print_header "LOG-TC-004 IAM log objects appear in OMS"
  wait_registration "AIDP-IAM"
  assert_contains "IAM log object appears in OMS records" "found AIDP-IAM"
  show_registrations
  assert_contains "IAM registration summary is visible" "AIDP-IAM"
}

case_log_tc_005() {
  print_header "LOG-TC-005 IAM log collect uploads separate zip archives"
  copy_payload_to_pod "${IAM_NS}" "${IAM_POD}" "${IAM_CONTAINER}" "${TMP_DIR}/iam-dispatch.json" "${IAM_PAYLOAD_REMOTE}"
  run_cmd "Clear IAM simulated OMS archive directory" "kubectl -n '${IAM_NS}' exec '${IAM_POD}' -c '${IAM_CONTAINER}' -- sh -c 'rm -rf \"${IAM_UPLOAD_DIR}\" && mkdir -p \"${IAM_UPLOAD_DIR}\" && echo cleared'"
  assert_contains "IAM archive directory is cleared" "cleared"
  post_from_pod "${IAM_NS}" "${IAM_POD}" "${IAM_CONTAINER}" "${IAM_PAYLOAD_REMOTE}" "http://localhost:8090/AccessManager/Tenants/System/LogCollect/Dispatch"
  assert_regex "IAM log collect task is accepted" '"data"[[:space:]]*:[[:space:]]*true'
  wait_progress_from_pod "${IAM_NS}" "${IAM_POD}" "${IAM_CONTAINER}" "http://localhost:8090/AccessManager/Tenants/System/LogCollect/Progress" "IAM" "${IAM_WORK_DIR}"
  assert_rc_zero "IAM log collect task finished"
  check_archives "${IAM_NS}" "${IAM_POD}" "${IAM_CONTAINER}" "${IAM_UPLOAD_DIR}" "iam-keycloak-proxy" "iam-opa" "IAM"
  assert_contains "IAM zip archives are readable" "zip check ok"
}

case_log_tc_006() {
  print_header "LOG-TC-006 IAM progress is displayable by OMS"
  run_cmd "Read IAM log collect progress and validate display fields" \
    "kubectl -n '${IAM_NS}' exec '${IAM_POD}' -c '${IAM_CONTAINER}' -- python3 -c 'import json,urllib.request; data=json.loads(urllib.request.urlopen(\"http://localhost:8090/AccessManager/Tenants/System/LogCollect/Progress\", timeout=10).read().decode()); print(json.dumps(data, ensure_ascii=False, indent=2)); nodes=data.get(\"data\",{}).get(\"nodeInfos\",[]); assert data.get(\"data\",{}).get(\"basicInfo\",{}).get(\"collectStatus\") == \"FINISH\"; assert all(\" \" not in n.get(\"name\", \"\") for n in nodes); assert all(n.get(\"fileName\", \"\").endswith(\".zip\") for n in nodes); assert all(n.get(\"nodeIp\") for n in nodes)'"
  assert_rc_zero "IAM progress is successful and file names are valid"
  assert_contains "IAM progress shows FINISH" '"collectStatus": "FINISH"'
  assert_regex "IAM progress contains zip file names" 'iam-(keycloak-proxy|opa)_.*\.zip'
}

main() {
  require_tool kubectl
  require_tool bash

  print_header "OMS log collect black-box test"
  echo "log file: ${LOG_FILE}"
  echo "Gateway namespace: ${GW_NS}"
  echo "IAM namespace: ${IAM_NS}"
  echo "mock OMS register URL: ${MOCK_REGISTER_URL}"

  setup_mock_oms
  configure_components
  get_pods
  write_dispatch_payloads

  case_log_tc_001
  case_log_tc_002
  case_log_tc_003
  case_log_tc_004
  case_log_tc_005
  case_log_tc_006

  print_header "Summary"
  echo "PASS=${PASS}"
  echo "FAIL=${FAIL}"
  echo "SKIP=${SKIP}"
  echo "log file: ${LOG_FILE}"
  if [ "${FAIL}" -gt 0 ]; then
    exit 1
  fi
}

main "$@"
