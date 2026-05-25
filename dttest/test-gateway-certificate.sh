#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

GW_NS="${GW_NS:-aidp-gateway}"
GATEWAY_NAME="${GATEWAY_NAME:-eg}"
CERT_ALIAS="${CERT_ALIAS:-bootstrap}"
TLS_SECRET="${TLS_SECRET:-gw-cert-bootstrap}"
MANAGER_SERVICE="${MANAGER_SERVICE:-gateway-manager}"
TLS_HOST="${TLS_HOST:-localhost}"
LOCAL_MANAGER_PORT="${LOCAL_MANAGER_PORT:-18080}"
LOCAL_HTTPS_PORT="${LOCAL_HTTPS_PORT:-18443}"
LOG_FILE="${LOG_FILE:-${SCRIPT_DIR}/gateway-certificate-test-log.txt}"
TMP_DIR="${TMP_DIR:-/tmp/aidp-gateway-cert-tests}"

PASS=0
FAIL=0
SKIP=0
LAST_OUT=""
LAST_RC=0
ENVOY_SERVICE=""
PF_PIDS=()

mkdir -p "$(dirname "${LOG_FILE}")" "${TMP_DIR}"
: > "${LOG_FILE}"
exec > >(tee -a "${LOG_FILE}") 2>&1

cleanup() {
  for pid in "${PF_PIDS[@]:-}"; do
    kill "${pid}" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT

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

assert_equals() {
  local desc="$1"
  local expected="$2"
  local actual="$3"
  if [ "${expected}" = "${actual}" ]; then
    pass "${desc}: ${actual}"
  else
    fail "${desc}; expected=${expected}; actual=${actual}"
  fi
}

require_tool() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing command: $1"
    exit 1
  fi
}

extract_fingerprint() {
  printf '%s' "$1" |
    sed -n 's/.*Fingerprint=//Ip' |
    head -n 1 |
    tr -d ':\r\n ' |
    tr 'A-F' 'a-f'
}

start_port_forward() {
  local title="$1"
  local cmd="$2"
  local log_path="$3"
  echo
  echo "[STEP] ${title}"
  echo "[COMMAND]"
  echo "\$ ${cmd} > ${log_path} 2>&1 &"
  set +e
  bash -lc "${cmd}" > "${log_path}" 2>&1 &
  local pid=$!
  PF_PIDS+=("${pid}")
  sleep 1
  kill -0 "${pid}" >/dev/null 2>&1
  LAST_RC=$?
  set -u
  echo "[RESULT]"
  if [ "${LAST_RC}" -eq 0 ]; then
    echo "started pid=${pid}"
  else
    cat "${log_path}" 2>/dev/null || true
  fi
  echo "[EXIT_CODE] ${LAST_RC}"
}

wait_http_ready() {
  run_cmd "Wait gateway-manager port-forward ready" \
    "for i in \$(seq 1 60); do curl -sS 'http://127.0.0.1:${LOCAL_MANAGER_PORT}/healthz' && exit 0; sleep 1; done; cat '${TMP_DIR}/gateway-manager-port-forward.log'; exit 1"
  assert_contains "gateway-manager health endpoint is reachable" '"status":"ok"'
}

wait_https_ready() {
  run_cmd "Wait Gateway HTTPS port-forward ready" \
    "for i in \$(seq 1 60); do curl -k -sS -o /dev/null -w 'HTTP %{http_code}\\n' 'https://${TLS_HOST}:${LOCAL_HTTPS_PORT}/' && exit 0; sleep 1; done; cat '${TMP_DIR}/gateway-https-port-forward.log'; exit 1"
  assert_regex "Gateway HTTPS endpoint responds" '^HTTP [0-9]{3}$'
}

secret_fingerprint_cmd() {
  printf "kubectl -n '%s' get secret '%s' -o jsonpath='{.data.tls\\.crt}' | base64 -d | openssl x509 -noout -fingerprint -sha256 -subject -serial" "${GW_NS}" "${TLS_SECRET}"
}

served_fingerprint_cmd() {
  printf "echo | openssl s_client -connect 127.0.0.1:%s -servername '%s' 2>/dev/null | openssl x509 -noout -fingerprint -sha256 -subject -serial" "${LOCAL_HTTPS_PORT}" "${TLS_HOST}"
}

wait_served_fingerprint() {
  local expected="$1"
  run_cmd "Wait Gateway served certificate fingerprint matches updated Secret" \
    "for i in \$(seq 1 60); do fp=\$(echo | openssl s_client -connect 127.0.0.1:${LOCAL_HTTPS_PORT} -servername '${TLS_HOST}' 2>/dev/null | openssl x509 -noout -fingerprint -sha256 2>/dev/null | sed -n 's/.*Fingerprint=//Ip' | tr -d ':\\r\\n ' | tr 'A-F' 'a-f'); echo \"current=\${fp:-empty} expected=${expected}\"; [ \"\${fp}\" = '${expected}' ] && exit 0; sleep 2; done; exit 1"
  assert_rc_zero "Gateway data plane is using the updated certificate"
}

generate_test_certificate() {
  cat > "${TMP_DIR}/openssl.cnf" <<EOF
[req]
distinguished_name = req_distinguished_name
x509_extensions = v3_req
prompt = no

[req_distinguished_name]
CN = ${TLS_HOST}

[v3_req]
subjectAltName = @alt_names

[alt_names]
DNS.1 = ${TLS_HOST}
DNS.2 = aidp-gateway.local
IP.1 = 127.0.0.1
EOF
  run_cmd "Generate temporary replacement TLS certificate" \
    "openssl req -x509 -newkey rsa:2048 -sha256 -days 30 -nodes -keyout '${TMP_DIR}/tls.key' -out '${TMP_DIR}/tls.crt' -config '${TMP_DIR}/openssl.cnf' -extensions v3_req"
  assert_rc_zero "temporary TLS certificate is generated"
}

main() {
  require_tool kubectl
  require_tool bash
  require_tool curl
  require_tool openssl
  require_tool base64

  print_header "CERT-TC-001 Gateway default certificate, update, and HTTPS access"
  echo "log file: ${LOG_FILE}"
  echo "Gateway namespace: ${GW_NS}"
  echo "Gateway name: ${GATEWAY_NAME}"
  echo "TLS Secret: ${TLS_SECRET}"
  echo "Certificate alias: ${CERT_ALIAS}"

  run_cmd "Find Envoy data-plane Service created by Gateway" \
    "kubectl -n '${GW_NS}' get svc -l 'gateway.envoyproxy.io/owning-gateway-name=${GATEWAY_NAME},gateway.envoyproxy.io/owning-gateway-namespace=${GW_NS}' -o jsonpath='{.items[0].metadata.name}'"
  assert_rc_zero "Envoy data-plane Service is found"
  ENVOY_SERVICE="${LAST_OUT}"
  if [ -z "${ENVOY_SERVICE}" ]; then
    fail "Envoy data-plane Service name is empty"
  else
    pass "Envoy data-plane Service name is ${ENVOY_SERVICE}"
  fi

  run_cmd "Verify default bootstrap TLS Secret exists and has TLS data" \
    "kubectl -n '${GW_NS}' get secret '${TLS_SECRET}' -o jsonpath='{.type}{\"\\n\"}{.metadata.labels.gateway\\.aidp\\.io/certificate-alias}{\"\\n\"}{.data.tls\\.crt}{\"\\n\"}{.data.tls\\.key}{\"\\n\"}'"
  assert_contains "default TLS Secret type is kubernetes.io/tls" "kubernetes.io/tls"
  assert_contains "default TLS Secret alias label is present" "${CERT_ALIAS}"
  assert_regex "default TLS Secret contains tls.crt and tls.key data" '[A-Za-z0-9+/=]{20,}'

  run_cmd "Read default TLS Secret certificate fingerprint" "$(secret_fingerprint_cmd)"
  assert_rc_zero "default TLS Secret certificate can be parsed"
  DEFAULT_SECRET_FP="$(extract_fingerprint "${LAST_OUT}")"
  if [ -n "${DEFAULT_SECRET_FP}" ]; then
    pass "default TLS Secret fingerprint is ${DEFAULT_SECRET_FP}"
  else
    fail "default TLS Secret fingerprint is empty"
  fi

  start_port_forward "Start gateway-manager port-forward" \
    "kubectl -n '${GW_NS}' port-forward svc/${MANAGER_SERVICE} ${LOCAL_MANAGER_PORT}:8080" \
    "${TMP_DIR}/gateway-manager-port-forward.log"
  assert_rc_zero "gateway-manager port-forward process is running"
  wait_http_ready

  start_port_forward "Start Gateway HTTPS port-forward" \
    "kubectl -n '${GW_NS}' port-forward svc/${ENVOY_SERVICE} ${LOCAL_HTTPS_PORT}:443" \
    "${TMP_DIR}/gateway-https-port-forward.log"
  assert_rc_zero "Gateway HTTPS port-forward process is running"
  wait_https_ready

  run_cmd "Read currently served Gateway HTTPS certificate fingerprint" "$(served_fingerprint_cmd)"
  assert_rc_zero "Gateway served certificate can be parsed"
  SERVED_BEFORE_FP="$(extract_fingerprint "${LAST_OUT}")"
  if [ -n "${SERVED_BEFORE_FP}" ]; then
    pass "Gateway initially serves certificate fingerprint ${SERVED_BEFORE_FP}"
  else
    fail "Gateway served certificate fingerprint is empty before update"
  fi

  generate_test_certificate
  run_cmd "Read generated certificate fingerprint" \
    "openssl x509 -in '${TMP_DIR}/tls.crt' -noout -fingerprint -sha256 -subject -serial"
  assert_rc_zero "generated certificate can be parsed"
  NEW_FP="$(extract_fingerprint "${LAST_OUT}")"
  if [ -n "${NEW_FP}" ]; then
    pass "generated certificate fingerprint is ${NEW_FP}"
  else
    fail "generated certificate fingerprint is empty"
  fi

  run_cmd "Upload replacement certificate through Gateway Manager certificate API" \
    "curl -sS -X POST 'http://127.0.0.1:${LOCAL_MANAGER_PORT}/GatewayManager/Tenants/System/Certificates/${CERT_ALIAS}' -F 'cert=@${TMP_DIR}/tls.crt' -F 'privateKey=@${TMP_DIR}/tls.key' -F 'displayName=Gateway Certificate Test' -F 'productName=AIDP'"
  assert_contains "certificate API reports updated Secret" "certificate secret updated"
  assert_contains "certificate API reports Ready status" '"status":"Ready"'
  assert_contains "certificate API reports Gateway binding" '"gateway_bound":true'

  run_cmd "Read updated TLS Secret certificate fingerprint" "$(secret_fingerprint_cmd)"
  assert_rc_zero "updated TLS Secret certificate can be parsed"
  UPDATED_SECRET_FP="$(extract_fingerprint "${LAST_OUT}")"
  assert_equals "updated Secret fingerprint matches generated certificate" "${NEW_FP}" "${UPDATED_SECRET_FP}"

  wait_served_fingerprint "${NEW_FP}"

  run_cmd "Access Gateway through HTTPS after certificate update" \
    "curl -k -sS -o /dev/null -w 'HTTP %{http_code}\\n' 'https://${TLS_HOST}:${LOCAL_HTTPS_PORT}/'"
  assert_regex "Gateway HTTPS remains reachable after certificate update" '^HTTP [0-9]{3}$'

  print_header "Summary"
  echo "PASS=${PASS}"
  echo "FAIL=${FAIL}"
  echo "SKIP=${SKIP}"
  echo "default_secret_fingerprint=${DEFAULT_SECRET_FP:-}"
  echo "served_before_fingerprint=${SERVED_BEFORE_FP:-}"
  echo "updated_fingerprint=${NEW_FP:-}"
  echo "log file: ${LOG_FILE}"
  if [ "${FAIL}" -gt 0 ]; then
    exit 1
  fi
}

main "$@"
