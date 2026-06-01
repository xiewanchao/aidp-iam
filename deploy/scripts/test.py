#!/usr/bin/env python3
"""
IAM end-to-end test suite implemented with Python standard library helpers.

Host-side dependencies are intentionally limited to:
  - python3
  - kubectl

The legacy test.sh file is still used as the source for the large manifest
JSON heredocs, so the test coverage stays aligned without requiring jq,
base64, curl, grep, lsof, or other host utilities.
"""

from __future__ import print_function

import atexit
import base64
import hashlib
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib import error, parse, request


KEYCLOAK_NS = os.environ.get("KEYCLOAK_NS", "keycloak")
IAM_NS = os.environ.get("IAM_NS", "aidp-iam")
ENVOY_GATEWAY_NS = os.environ.get("ENVOY_GATEWAY_NS", "aidp-gateway")
GATEWAY_RELEASE_NS = (
    os.environ.get("GATEWAY_RELEASE_NS")
    or os.environ.get("GATEWAY_MANAGER_NS")
    or ""
)
GATEWAY_PORT = os.environ.get("GATEWAY_PORT", "30080")
BASE_URL = os.environ.get("BASE_URL", "https://localhost:%s" % GATEWAY_PORT)
GATEWAY_TARGET_PORT = os.environ.get(
    "GATEWAY_TARGET_PORT",
    "443" if parse.urlparse(BASE_URL).scheme == "https" else "80",
)
GATEWAY_MANAGER_PORT = os.environ.get("GATEWAY_MANAGER_PORT", "18081")
GATEWAY_MANAGER_URL = os.environ.get("GATEWAY_MANAGER_URL", "http://localhost:%s" % GATEWAY_MANAGER_PORT)
GATEWAY_LOG_STATUS_CONFIGMAP = os.environ.get(
    "GATEWAY_LOG_STATUS_CONFIGMAP",
    "aidp-gateway-log-collect-status",
)
GATEWAY_CERT_ALIAS = os.environ.get("GATEWAY_CERT_ALIAS", "aidp-gateway")
GATEWAY_TLS_SECRET = os.environ.get("GATEWAY_TLS_SECRET", "gw-cert-aidp-gateway")
IAM_LOG_PORT = os.environ.get("IAM_LOG_PORT", "18082")
IAM_LOG_URL = os.environ.get("IAM_LOG_URL", "http://localhost:%s" % IAM_LOG_PORT)
IAM_LOG_STATUS_CONFIGMAP = os.environ.get(
    "IAM_LOG_STATUS_CONFIGMAP",
    "aidp-iam-log-collect-status",
)

REALM = os.environ.get("REALM", "aidp")
CLIENT_ID = os.environ.get("CLIENT_ID", "aidp-client")
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Admin@123")
NORMAL_USER = os.environ.get("NORMAL_USER", "normal-user")
NORMAL_PASSWORD = os.environ.get("NORMAL_PASSWORD", "NormalUser@123")
TEST_APP = os.environ.get("TEST_APP", "test-app")
TEST_NS = os.environ.get("TEST_NS", "TestApp")
KEYCLOAK_ADMIN_PORT = os.environ.get("KEYCLOAK_ADMIN_PORT", "18080")

USE_COLOR = os.environ.get("NO_COLOR", "") == "" and sys.stdout.isatty()
GREEN = "\033[0;32m" if USE_COLOR else ""
RED = "\033[0;31m" if USE_COLOR else ""
YELLOW = "\033[1;33m" if USE_COLOR else ""
BLUE = "\033[0;34m" if USE_COLOR else ""
NC = "\033[0m" if USE_COLOR else ""

pf_proc = None
gm_pf_proc = None
iam_log_pf_proc = None
kc_pf_proc = None
CS = ""
ADMIN_TOKEN = ""
NORMAL_TOKEN = ""
ADMIN_SUB = ""
HAS_KB_ROUTE = 0
HAS_MEMORY_ROUTE = 0
HAS_DATAAGENT_ROUTE = 0


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def create_insecure_ssl_context():
    protocol = getattr(ssl, "PROTOCOL_TLS_CLIENT", ssl.PROTOCOL_TLS)
    context = ssl.SSLContext(protocol)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


OPENER = request.build_opener(
    NoRedirect,
    request.HTTPSHandler(context=create_insecure_ssl_context()),
)


class Runner(object):
    def __init__(self):
        self.total = 0
        self.passed = 0
        self.failed = 0

    def section(self, title):
        print("\n%s=== %s ===%s" % (BLUE, title, NC))

    def skip(self, desc):
        print("  %sSKIP%s %s" % (YELLOW, NC, desc))

    def equal(self, desc, expected, actual):
        self.total += 1
        expected = "" if expected is None else str(expected)
        actual = "" if actual is None else str(actual)
        if expected == actual:
            self.passed += 1
            print("  %sPASS%s %s" % (GREEN, NC, desc))
        else:
            self.failed += 1
            print("  %sFAIL%s %s (expected=%s, actual=%s)" % (RED, NC, desc, expected, actual))

    def contains(self, desc, needle, haystack):
        self.total += 1
        haystack = "" if haystack is None else str(haystack)
        if str(needle) in haystack:
            self.passed += 1
            print("  %sPASS%s %s" % (GREEN, NC, desc))
        else:
            self.failed += 1
            print("  %sFAIL%s %s (expected to contain '%s')" % (RED, NC, desc, needle))

    def not_contains(self, desc, needle, haystack):
        self.total += 1
        haystack = "" if haystack is None else str(haystack)
        if str(needle) in haystack:
            self.failed += 1
            print("  %sFAIL%s %s (should NOT contain '%s')" % (RED, NC, desc, needle))
        else:
            self.passed += 1
            print("  %sPASS%s %s" % (GREEN, NC, desc))

    def match(self, desc, pattern, actual):
        self.total += 1
        actual = "" if actual is None else str(actual)
        if re.search(pattern, actual):
            self.passed += 1
            print("  %sPASS%s %s" % (GREEN, NC, desc))
        else:
            self.failed += 1
            print("  %sFAIL%s %s (expected to match '%s', got '%s')" % (RED, NC, desc, pattern, actual))

    def summary(self):
        print("")
        print("%s========================================%s" % (BLUE, NC))
        print("%s  Test Summary%s" % (BLUE, NC))
        print("%s========================================%s" % (BLUE, NC))
        print("  Total : %s" % self.total)
        print("  %sPass  : %s%s" % (GREEN, self.passed, NC))
        print("  %sFail  : %s%s" % (RED, self.failed, NC))
        print("%s========================================%s" % (BLUE, NC))


T = Runner()


def run_cmd(args, timeout=60, env_extra=None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "", 1
    return proc.stdout.replace("\r", ""), proc.returncode


def kubectl(args, timeout=60):
    return run_cmd(["kubectl"] + list(args), timeout=timeout, env_extra={"MSYS_NO_PATHCONV": "1"})


def url_path(base, path=""):
    base_url = "%s/" % base.rstrip("/")
    rel_path = str(path).lstrip("/")
    return parse.urljoin(base_url, rel_path)


def tenant_url(path=""):
    return url_path(BASE_URL, "AccessManager/Tenants/%s/%s" % (REALM, str(path).lstrip("/")))


def system_url(path=""):
    return url_path(BASE_URL, "AccessManager/Tenants/System/%s" % str(path).lstrip("/"))


def kb_url(path=""):
    return url_path(BASE_URL, "KnowledgeBase/Tenants/%s/%s" % (REALM, str(path).lstrip("/")))


def realm_url(path=""):
    return url_path(BASE_URL, "realms/%s/%s" % (REALM, str(path).lstrip("/")))


def keycloak_admin_url(path=""):
    return "http://localhost:%s/%s" % (KEYCLOAK_ADMIN_PORT, str(path).lstrip("/"))


def resource_path(*parts):
    return "/".join(str(part).strip("/") for part in parts if str(part).strip("/"))


def kubectl_json(args, timeout=60):
    out, code = kubectl(args + ["-o", "json"], timeout=timeout)
    if code != 0 or not out.strip():
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def kubectl_exec(namespace, target, container, cmd, timeout=60):
    args = ["-n", namespace, "exec", target]
    if container:
        args += ["-c", container]
    args += ["--"] + list(cmd)
    out, code = kubectl(args, timeout=timeout)
    if code != 0:
        return ""
    return out.strip()


def resolve_gateway_release_namespace():
    global GATEWAY_RELEASE_NS
    if GATEWAY_RELEASE_NS:
        return GATEWAY_RELEASE_NS

    services = kubectl_json(["get", "svc", "-A"], timeout=30) or {}
    for svc in services.get("items", []) or []:
        metadata = svc.get("metadata", {})
        if metadata.get("name") == "gateway-manager":
            GATEWAY_RELEASE_NS = metadata.get("namespace", "") or ENVOY_GATEWAY_NS
            return GATEWAY_RELEASE_NS

    for svc in services.get("items", []) or []:
        metadata = svc.get("metadata", {})
        labels = metadata.get("labels", {})
        if labels.get("gateway.envoyproxy.io/owning-gateway-name") == "eg":
            GATEWAY_RELEASE_NS = metadata.get("namespace", "") or ENVOY_GATEWAY_NS
            return GATEWAY_RELEASE_NS

    GATEWAY_RELEASE_NS = ENVOY_GATEWAY_NS
    return GATEWAY_RELEASE_NS


def find_gateway_dataplane_service():
    data = kubectl_json(
        [
            "get",
            "svc",
            "-A",
            "-l",
            "gateway.envoyproxy.io/owning-gateway-name=eg",
        ],
        timeout=30,
    ) or {}
    items = data.get("items", []) or []
    if items:
        metadata = items[0].get("metadata", {})
        namespace = metadata.get("namespace", "") or resolve_gateway_release_namespace()
        return namespace, "svc/%s" % metadata.get("name", "envoy-eg")
    return resolve_gateway_release_namespace(), "svc/envoy-eg"


def psql_iam(sql):
    out = kubectl_exec(
        KEYCLOAK_NS,
        "iam-store-0",
        "postgres",
        ["psql", "-U", "keycloak", "-d", "iam", "-tA", "-c", sql],
        timeout=90,
    )
    return out.strip()


def http_request(method, url, headers=None, body=None, form=None, timeout=30):
    headers = dict(headers or {})
    data = None
    if form is not None:
        data = parse.urlencode(form).encode("utf-8")
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif body is not None:
        if isinstance(body, (dict, list)):
            body = json_compact(body)
            headers.setdefault("Content-Type", "application/json")
        if isinstance(body, str):
            data = body.encode("utf-8")
        else:
            data = body
    req = request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.getcode(), raw.decode("utf-8", "replace")
    except error.HTTPError as exc:
        try:
            raw = exc.read()
            text = raw.decode("utf-8", "replace")
        except Exception:
            text = ""
        return exc.code, text
    except Exception:
        return 0, ""


def http_status(method, url, headers=None, body=None, form=None, timeout=30):
    code, _ = http_request(method, url, headers=headers, body=body, form=form, timeout=timeout)
    return str(code).zfill(3) if code == 0 else str(code)


def http_body(method, url, headers=None, body=None, form=None, timeout=30):
    _, text = http_request(method, url, headers=headers, body=body, form=form, timeout=timeout)
    return text


def multipart_body(fields, files):
    boundary = "aidp-test-%s" % uuid.uuid4().hex
    chunks = []
    for name, value in (fields or {}).items():
        chunks.append(("--%s\r\n" % boundary).encode("ascii"))
        chunks.append(('Content-Disposition: form-data; name="%s"\r\n\r\n' % name).encode("ascii"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    for name, meta in (files or {}).items():
        filename, content_type, data = meta
        chunks.append(("--%s\r\n" % boundary).encode("ascii"))
        header = (
            'Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
            "Content-Type: %s\r\n\r\n"
        ) % (name, filename, content_type)
        chunks.append(header.encode("ascii"))
        chunks.append(data if isinstance(data, bytes) else str(data).encode("utf-8"))
        chunks.append(b"\r\n")
    chunks.append(("--%s--\r\n" % boundary).encode("ascii"))
    return "multipart/form-data; boundary=%s" % boundary, b"".join(chunks)


def wait_http_status(method, url, expected, timeout_seconds=30):
    expected_set = set(expected if isinstance(expected, (list, tuple, set)) else [expected])
    deadline = time.time() + timeout_seconds
    last = "000"
    while time.time() < deadline:
        last = http_status(method, url, timeout=5)
        if last in expected_set:
            return True, last
        time.sleep(1)
    return False, last


def find_free_local_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return str(sock.getsockname()[1])


def admin_headers(headers=None):
    merged = {"Authorization": "Bearer %s" % ADMIN_TOKEN}
    if headers:
        merged.update(headers)
    return merged


def token_headers(token, headers=None):
    merged = {"Authorization": "Bearer %s" % token}
    if headers:
        merged.update(headers)
    return merged


def admin_status(method, url, body=None, headers=None, timeout=30):
    return http_status(method, url, headers=admin_headers(headers), body=body, timeout=timeout)


def admin_body(method, url, body=None, headers=None, timeout=30):
    return http_body(method, url, headers=admin_headers(headers), body=body, timeout=timeout)


def token_status(token, method, url, body=None, headers=None, timeout=30):
    return http_status(method, url, headers=token_headers(token, headers), body=body, timeout=timeout)


def token_body(token, method, url, body=None, headers=None, timeout=30):
    return http_body(method, url, headers=token_headers(token, headers), body=body, timeout=timeout)


def json_compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_loads(text, default=None):
    try:
        return json.loads(text)
    except Exception:
        return default


def json_get(text, path):
    value = json_loads(text, {})
    for part in path.split("."):
        if isinstance(value, list):
            try:
                value = value[int(part)]
            except Exception:
                return ""
        elif isinstance(value, dict):
            value = value.get(part, "")
        else:
            return ""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json_compact(value)


def jwt_claim(token, key):
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        data = json.loads(base64.urlsafe_b64decode(part.encode("ascii")).decode("utf-8"))
        value = data.get(key, "")
        if isinstance(value, str):
            return value
        return json_compact(value)
    except Exception:
        return ""


def get_secret_value(namespace, name, key):
    data = kubectl_json(["-n", namespace, "get", "secret", name], timeout=60)
    if not data:
        return ""
    encoded = data.get("data", {}).get(key, "")
    if not encoded:
        return ""
    try:
        return base64.b64decode(encoded).decode("utf-8").strip()
    except Exception:
        return ""


def get_secret_json(namespace, name):
    return kubectl_json(["-n", namespace, "get", "secret", name], timeout=60) or {}


def pem_to_der(pem_text):
    body = re.sub(r"-----BEGIN [^-]+-----|-----END [^-]+-----|\s+", "", pem_text or "")
    if not body:
        return b""
    try:
        return base64.b64decode(body)
    except Exception:
        return b""


def cert_fingerprint_from_pem(pem_text):
    der = pem_to_der(pem_text)
    return hashlib.sha256(der).hexdigest() if der else ""


def secret_cert_fingerprint(namespace, name):
    data = get_secret_json(namespace, name).get("data", {})
    encoded = data.get("tls.crt", "")
    if not encoded:
        return ""
    try:
        return cert_fingerprint_from_pem(base64.b64decode(encoded).decode("utf-8", "replace"))
    except Exception:
        return ""


def served_cert_fingerprint(url):
    parsed = parse.urlparse(url)
    if parsed.scheme != "https":
        return ""
    host = parsed.hostname or "localhost"
    port = parsed.port or 443
    ctx = create_insecure_ssl_context()
    try:
        with socket_connection(host, port, timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                return hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest()
    except Exception:
        return ""


def socket_connection(host, port, timeout=5):
    return socket.create_connection((host, port), timeout=timeout)


def get_client_secret():
    return get_secret_value(IAM_NS, "keycloak-aidp-client", "client-secret")


def request_token(username, password):
    if not CS:
        return ""
    body = http_body(
        "POST",
        "%s/realms/%s/protocol/openid-connect/token" % (BASE_URL, REALM),
        form={
            "client_id": CLIENT_ID,
            "client_secret": CS,
            "grant_type": "password",
            "username": username,
            "password": password,
        },
    )
    return json_get(body, "access_token")


def request_public_token(client_id, username, password):
    body = http_body(
        "POST",
        "%s/realms/%s/protocol/openid-connect/token" % (BASE_URL, REALM),
        form={
            "client_id": client_id,
            "grant_type": "password",
            "username": username,
            "password": password,
        },
    )
    return json_get(body, "access_token")


def refresh_admin_token():
    global ADMIN_TOKEN
    ADMIN_TOKEN = request_token(ADMIN_USER, ADMIN_PASSWORD)
    return ADMIN_TOKEN


def find_user_id(users_body, username):
    data = json_loads(users_body, {})
    users = data if isinstance(data, list) else data.get("users", data.get("items", []))
    for user in users or []:
        if user.get("username") == username:
            return user.get("id", "")
    return ""


def find_first_user_id(users_body):
    data = json_loads(users_body, {})
    users = data if isinstance(data, list) else data.get("users", data.get("items", []))
    return users[0].get("id", "") if users else ""


def find_group_id(groups_body, name):
    data = json_loads(groups_body, {})
    groups = data if isinstance(data, list) else data.get("groups", data.get("items", []))

    def walk(items):
        for group in items or []:
            if group.get("name") == name:
                return group.get("id", "")
            found = walk(group.get("subGroups", []))
            if found:
                return found
        return ""

    return walk(groups)


def manifest_body(name, index=0):
    shell_path = Path(__file__).with_name("test.sh")
    try:
        text = shell_path.read_text(encoding="utf-8")
    except Exception:
        print("ERROR: cannot read %s for embedded manifest JSON" % shell_path, file=sys.stderr)
        sys.exit(2)
    pattern = r'cat > "\$%s" <<\'JSON\'\n(.*?)\nJSON' % re.escape(name)
    blocks = re.findall(pattern, text, flags=re.S)
    if len(blocks) <= index:
        print("ERROR: manifest heredoc %s[%s] not found in %s" % (name, index, shell_path), file=sys.stderr)
        sys.exit(2)
    try:
        json.loads(blocks[index])
    except ValueError as exc:
        print("ERROR: manifest heredoc %s[%s] is invalid JSON: %s" % (name, index, exc), file=sys.stderr)
        sys.exit(2)
    return blocks[index]


def pod_http_status(url):
    script = (
        "import urllib.request\n"
        "try:\n"
        " print(urllib.request.urlopen(%r, timeout=5).status)\n"
        "except Exception:\n"
        " print('000')\n"
    ) % url
    out = kubectl_exec(IAM_NS, "deploy/iam-services", "aidp-iam-app", ["python3", "-c", script], timeout=20)
    return out.strip() or "000"


def pod_http_body(url):
    script = (
        "import urllib.request, sys\n"
        "try:\n"
        " sys.stdout.write(urllib.request.urlopen(%r, timeout=5).read().decode('utf-8', 'replace'))\n"
        "except Exception:\n"
        " pass\n"
    ) % url
    return kubectl_exec(IAM_NS, "deploy/iam-services", "aidp-iam-app", ["python3", "-c", script], timeout=20)


def start_port_forward(namespace, target, mapping):
    try:
        return subprocess.Popen(
            ["kubectl", "-n", namespace, "port-forward", target, mapping],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=dict(os.environ, MSYS_NO_PATHCONV="1"),
        )
    except OSError:
        return None


def setup_gateway_port_forward():
    global pf_proc
    print("%sSetting up port-forward to Envoy Gateway...%s" % (YELLOW, NC))
    gateway_root_url = url_path(BASE_URL)
    code = http_status("GET", gateway_root_url, timeout=5)
    if code in ("200", "301", "302", "404"):
        print("  %sPort %s already forwarded%s" % (GREEN, GATEWAY_PORT, NC))
        return

    gateway_svc_ns, gw_svc = find_gateway_dataplane_service()
    pf_proc = start_port_forward(
        gateway_svc_ns,
        gw_svc,
        "%s:%s" % (GATEWAY_PORT, GATEWAY_TARGET_PORT),
    )
    ok, status = wait_http_status("GET", gateway_root_url, ("200", "301", "302", "404"), timeout_seconds=20)
    if not ok:
        print("  %sGateway port-forward did not become ready (last=%s)%s" % (YELLOW, status, NC))


def setup_gateway_manager_port_forward():
    global gm_pf_proc
    healthz_url = url_path(GATEWAY_MANAGER_URL, "healthz")
    if http_status("GET", healthz_url, timeout=5) == "200":
        return
    gm_pf_proc = start_port_forward(
        resolve_gateway_release_namespace(),
        "svc/gateway-manager",
        "%s:8080" % GATEWAY_MANAGER_PORT,
    )
    ok, status = wait_http_status("GET", healthz_url, "200", timeout_seconds=30)
    if not ok:
        print("  %sGateway manager port-forward did not become ready (last=%s)%s" % (YELLOW, status, NC))


def setup_iam_log_port_forward():
    global iam_log_pf_proc, IAM_LOG_PORT, IAM_LOG_URL
    health_url = url_path(IAM_LOG_URL, "AccessManager/Tenants/Common/Health")
    if http_status("GET", health_url, timeout=5) == "200":
        return
    candidate_ports = [IAM_LOG_PORT, find_free_local_port(), find_free_local_port()]
    for port in candidate_ports:
        IAM_LOG_PORT = port
        IAM_LOG_URL = "http://localhost:%s" % IAM_LOG_PORT
        health_url = url_path(IAM_LOG_URL, "AccessManager/Tenants/Common/Health")
        iam_log_pf_proc = start_port_forward(IAM_NS, "svc/keycloak-proxy", "%s:8090" % IAM_LOG_PORT)
        ok, _ = wait_http_status(
            "GET",
            health_url,
            "200",
            timeout_seconds=45,
        )
        if ok:
            return
        if iam_log_pf_proc:
            iam_log_pf_proc.terminate()
            try:
                iam_log_pf_proc.wait(timeout=3)
            except Exception:
                iam_log_pf_proc.kill()
            iam_log_pf_proc = None


def detect_optional_routes():
    global HAS_KB_ROUTE, HAS_MEMORY_ROUTE, HAS_DATAAGENT_ROUTE
    out, _ = kubectl(["get", "httproute", "-A"], timeout=30)
    lower = out.lower()
    HAS_KB_ROUTE = 1 if re.search(r"mock-kb|knowledgebase", lower) else 0
    HAS_MEMORY_ROUTE = 1 if re.search(r"mock-memory|memorystore", lower) else 0
    HAS_DATAAGENT_ROUTE = 1 if re.search(r"mock-dataagent|dataagent", lower) else 0

    if HAS_KB_ROUTE:
        print("  %smock-kb route detected - KB tests will run%s" % (GREEN, NC))
    else:
        print("  %smock-kb route not found - KB backend tests will be skipped%s" % (YELLOW, NC))
    if HAS_MEMORY_ROUTE:
        print("  %smock-memory route detected - MemoryStore tests will run%s" % (GREEN, NC))
    else:
        print("  %smock-memory route not found - MemoryStore tests will be skipped%s" % (YELLOW, NC))
    if HAS_DATAAGENT_ROUTE:
        print("  %smock-dataagent route detected - DataAgent tests will run%s" % (GREEN, NC))
    else:
        print("  %smock-dataagent route not found - DataAgent tests will be skipped%s" % (YELLOW, NC))


def cleanup():
    for proc in (pf_proc, gm_pf_proc, iam_log_pf_proc, kc_pf_proc):
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception as exc:
                print("  %sFailed to terminate port-forward: %s%s" % (YELLOW, exc, NC))
    _ = psql_iam("DELETE FROM resource_acl WHERE object_path LIKE '%/TestApp/%' OR object_path LIKE '%/test-acl-%';")
    _ = psql_iam("DELETE FROM resource_acl WHERE user_path LIKE '%/alice%' OR user_path LIKE '%/bob%';")
    _ = psql_iam("DELETE FROM app_manifests WHERE namespace='%s';" % TEST_NS)
    _ = psql_iam("DELETE FROM apps WHERE app_name='%s';" % TEST_APP)


def setup_manifests():
    setup_token = request_token(ADMIN_USER, ADMIN_PASSWORD)
    headers = {"Authorization": "Bearer %s" % setup_token, "Content-Type": "application/json"}

    kb_code = http_status(
        "PUT",
        "%s/AccessManager/Tenants/System/AppManifests/KnowledgeBase" % BASE_URL,
        headers=headers,
        body=manifest_body("KB_MANIFEST_FILE", 0),
        timeout=60,
    )
    if kb_code in ("200", "201"):
        print("  [setup] KnowledgeBase manifest registered (%s), waiting for OPA bundle refresh..." % kb_code)
        time.sleep(35)
    else:
        print("  [setup] WARNING: KnowledgeBase manifest PUT returned %s" % kb_code)

    ms_code = http_status(
        "PUT",
        "%s/AccessManager/Tenants/System/AppManifests/MemoryStore" % BASE_URL,
        headers=headers,
        body=manifest_body("MS_MANIFEST_FILE", 0),
        timeout=60,
    )
    if ms_code in ("200", "201"):
        print("  [setup] MemoryStore manifest registered (%s)" % ms_code)
    else:
        print("  [setup] WARNING: MemoryStore manifest PUT returned %s" % ms_code)

    da_code = http_status(
        "PUT",
        "%s/AccessManager/Tenants/System/AppManifests/DataAgent" % BASE_URL,
        headers=headers,
        body=manifest_body("DA_MANIFEST_FILE", 0),
        timeout=60,
    )
    if da_code in ("200", "201"):
        print("  [setup] DataAgent manifest registered (%s), waiting for OPA bundle refresh..." % da_code)
        time.sleep(35)
    else:
        print("  [setup] WARNING: DataAgent manifest PUT returned %s" % da_code)


def section_1_pod_health():
    T.section("Section 1: Pod health")
    T.equal(
        "keycloak-proxy /AccessManager/Tenants/Common/Health",
        "200",
        pod_http_status("http://localhost:8090/AccessManager/Tenants/Common/Health"),
    )
    T.equal("pep-proxy /health", "200", pod_http_status("http://localhost:8000/health"))
    T.equal("resource-sync /health", "200", pod_http_status("http://localhost:8080/health"))
    T.skip("mock-kb /health (mock backends not installed)")

    data = kubectl_json(
        [
            "-n",
            resolve_gateway_release_namespace(),
            "get",
            "deploy",
            "-l",
            "gateway.envoyproxy.io/owning-gateway-name=eg",
        ],
        timeout=30,
    )
    ready = ""
    try:
        ready = str(data.get("items", [])[0].get("status", {}).get("readyReplicas", ""))
    except Exception:
        ready = ""
    T.equal("envoy data plane ready", "1", ready)


def section_2_public_routes():
    T.section("Section 2: Public routes (no auth)")
    T.equal(
        "GET /realms/%s/.well-known/openid-configuration" % REALM,
        "200",
        http_status("GET", realm_url(".well-known/openid-configuration")),
    )
    T.match("GET /admin/", r"^(200|302|303)$", http_status("GET", url_path(BASE_URL, "admin/")))


def gateway_log_collect_state():
    data = kubectl_json(
        ["-n", resolve_gateway_release_namespace(), "get", "configmap", GATEWAY_LOG_STATUS_CONFIGMAP],
        timeout=30,
    )
    raw = (data or {}).get("data", {}).get("status.json", "")
    return json_loads(raw, {}) if raw else {}


def gateway_log_current_task():
    state = gateway_log_collect_state()
    task_id = state.get("currentCollectId", "")
    task = (state.get("tasks") or {}).get(task_id, {})
    return task_id, task


def gateway_log_node(task, node_type):
    for node in task.get("nodeInfos", []) or []:
        if node.get("nodeType") == node_type:
            return node
    return {}


def log_nodes_response_node(body, node_type):
    data = json_loads(body, {})
    for node in data.get("items", []) or []:
        if node.get("nodeType") == node_type:
            return node
    return {}


def wait_gateway_log_collect_finished(timeout_seconds=60):
    deadline = time.time() + timeout_seconds
    last_body = ""
    while time.time() < deadline:
        last_body = http_body(
            "GET",
            url_path(GATEWAY_MANAGER_URL, "GatewayManager/Tenants/System/LogCollect/Progress"),
            timeout=10,
        )
        status = json_get(last_body, "data.basicInfo.collectStatus")
        if status in ("FINISH", "FAILED", "PART_FAILED"):
            return status, last_body
        time.sleep(2)
    return json_get(last_body, "data.basicInfo.collectStatus"), last_body


def assert_gateway_manager_log_nodes():
    T.equal(
        "gateway-manager /healthz",
        "200",
        http_status("GET", url_path(GATEWAY_MANAGER_URL, "healthz"), timeout=10),
    )
    nodes_body = http_body(
        "GET",
        url_path(GATEWAY_MANAGER_URL, "GatewayManager/Tenants/System/LogCollect/Nodes?page=1&limit=100"),
        timeout=10,
    )
    T.contains("log nodes include AIDP_GATEWAY_MANAGER", "AIDP_GATEWAY_MANAGER", nodes_body)
    manager_discovered_node = log_nodes_response_node(nodes_body, "AIDP_GATEWAY_MANAGER")
    T.match(
        "log nodes AIDP_GATEWAY_MANAGER nodeIp is populated",
        r"^\S+$",
        manager_discovered_node.get("nodeIp", ""),
    )


def gateway_manager_log_payload():
    return {
        "collectUser": "test-sh",
        "scene": "e2e_gateway_manager_log",
        "startTime": "",
        "endTime": "",
        "path": "",
        "targets": [],
        "nodeList": [
            {
                "name": "gateway-manager",
                "nodeIp": "127.0.0.1",
                "nodeType": "AIDP_GATEWAY_MANAGER",
                "status": "READY",
                "product": "AIDP",
                "logTypes": ["AIDP_GATEWAY_LOG"],
            }
        ],
    }


def dispatch_gateway_manager_log_task():
    accept_body = http_body(
        "POST",
        url_path(GATEWAY_MANAGER_URL, "GatewayManager/Tenants/System/LogCollect/Dispatch"),
        body=gateway_manager_log_payload(),
        timeout=20,
    )
    T.contains("POST LogCollect/Dispatch accepts manager log task", "true", accept_body)


def assert_gateway_manager_log_result():
    status, status_body = wait_gateway_log_collect_finished(timeout_seconds=60)
    T.equal("manager log collect status FINISH", "FINISH", status)
    T.equal("manager log collect progress 100", "100", json_get(status_body, "data.basicInfo.progress"))
    T.contains("manager log collect response has manager node", "AIDP_GATEWAY_MANAGER", status_body)

    _, task = gateway_log_current_task()
    manager_node = gateway_log_node(task, "AIDP_GATEWAY_MANAGER")
    T.equal("manager log node collectState success", "2", manager_node.get("collectState", ""))
    T.equal("manager log node preserves nodeIp", "127.0.0.1", manager_node.get("nodeIp", ""))
    T.match(
        "manager log node fileName uses node-type archive name",
        r"^gateway-manager_127\.0\.0\.1_.*-.*\.zip$",
        manager_node.get("fileName", ""),
    )

    archive_file = str(task.get("archiveFile") or "")
    T.match("manager log collect archive is zip", r"/tmp/gateway-log-collect/.*\.zip$", archive_file)
    return archive_file


def assert_gateway_manager_archive_exists(archive_file):
    archive_exists = ""
    if archive_file:
        archive_exists = kubectl_exec(
            resolve_gateway_release_namespace(),
            "deploy/gateway-manager",
            "gateway-manager",
            ["python3", "-c", "import os; print('yes' if os.path.isfile(%r) else 'no')" % archive_file],
            timeout=30,
        )
    T.equal("manager log archive exists in gateway-manager pod", "yes", archive_exists)


def section_2b_gateway_manager_log_collect():
    T.section("Section 2b: Gateway manager log collection")
    assert_gateway_manager_log_nodes()
    dispatch_gateway_manager_log_task()
    archive_file = assert_gateway_manager_log_result()
    assert_gateway_manager_archive_exists(archive_file)


def generate_gateway_test_certificate():
    script = r'''
import datetime
import ipaddress
import json
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u"localhost")])
now = datetime.datetime.utcnow()
cert = (
    x509.CertificateBuilder()
    .subject_name(subject)
    .issuer_name(issuer)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now - datetime.timedelta(minutes=5))
    .not_valid_after(now + datetime.timedelta(days=30))
    .add_extension(
        x509.SubjectAlternativeName([
            x509.DNSName(u"localhost"),
            x509.DNSName(u"aidp-gateway.local"),
            x509.IPAddress(ipaddress.ip_address(u"127.0.0.1")),
        ]),
        critical=False,
    )
    .sign(key, hashes.SHA256())
)
print(json.dumps({
    "cert": cert.public_bytes(serialization.Encoding.PEM).decode("utf-8"),
    "key": key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode("utf-8"),
    "fingerprint": cert.fingerprint(hashes.SHA256()).hex(),
}))
'''
    return json_loads(
        kubectl_exec(
            resolve_gateway_release_namespace(),
            "deploy/gateway-manager",
            "gateway-manager",
            ["python3", "-c", script],
            timeout=30,
        ),
        {},
    )


def assert_gateway_bootstrap_certificate():
    secret = get_secret_json(ENVOY_GATEWAY_NS, GATEWAY_TLS_SECRET)
    labels = secret.get("metadata", {}).get("labels", {})
    data = secret.get("data", {})
    T.equal("gateway bootstrap TLS Secret type", "kubernetes.io/tls", secret.get("type", ""))
    T.equal(
        "gateway bootstrap TLS Secret alias label",
        GATEWAY_CERT_ALIAS,
        labels.get("gateway.aidp.io/certificate-alias", ""),
    )
    T.match("gateway bootstrap TLS Secret has tls.crt", r"^[A-Za-z0-9+/=]{20,}$", data.get("tls.crt", ""))
    T.match("gateway bootstrap TLS Secret has tls.key", r"^[A-Za-z0-9+/=]{20,}$", data.get("tls.key", ""))

    default_fp = secret_cert_fingerprint(ENVOY_GATEWAY_NS, GATEWAY_TLS_SECRET)
    T.match("gateway bootstrap TLS Secret fingerprint is sha256", r"^[0-9a-f]{64}$", default_fp)


def assert_gateway_initial_served_certificate():
    served_before = served_cert_fingerprint(BASE_URL)
    if parse.urlparse(BASE_URL).scheme == "https":
        T.match("Gateway HTTPS initially serves a certificate", r"^[0-9a-f]{64}$", served_before)
    else:
        T.skip("Gateway served certificate check (BASE_URL is not https)")


def assert_generated_gateway_certificate(generated):
    new_cert = generated.get("cert", "")
    new_key = generated.get("key", "")
    new_fp = generated.get("fingerprint", "")
    T.match("generated replacement certificate fingerprint is sha256", r"^[0-9a-f]{64}$", new_fp)
    T.contains("generated replacement certificate has PEM cert", "BEGIN CERTIFICATE", new_cert)
    T.contains("generated replacement certificate has PEM key", "BEGIN RSA PRIVATE KEY", new_key)
    return new_cert, new_key, new_fp


def upload_gateway_certificate(new_cert, new_key):
    content_type, body = multipart_body(
        fields={"displayName": "Gateway Certificate Test", "productName": "AIDP"},
        files={
            "cert": ("tls.crt", "application/x-pem-file", new_cert.encode("utf-8")),
            "privateKey": ("tls.key", "application/x-pem-file", new_key.encode("utf-8")),
        },
    )
    upload_body = http_body(
        "POST",
        url_path(GATEWAY_MANAGER_URL, "GatewayManager/Tenants/System/Certificates/%s" % GATEWAY_CERT_ALIAS),
        headers={"Content-Type": content_type},
        body=body,
        timeout=30,
    )
    return json_loads(upload_body, {})


def assert_uploaded_gateway_certificate(upload, new_fp):
    T.equal("certificate API status Ready", "Ready", upload.get("status", ""))
    T.equal("certificate API updated configured Secret", GATEWAY_TLS_SECRET, upload.get("secret_name", ""))
    T.equal("certificate API reports Gateway binding", True, upload.get("gateway_bound", False))
    T.contains("certificate API response message", "certificate secret updated", upload.get("message", ""))

    updated_fp = secret_cert_fingerprint(ENVOY_GATEWAY_NS, GATEWAY_TLS_SECRET)
    T.equal("updated TLS Secret fingerprint matches generated certificate", new_fp, updated_fp)


def wait_served_cert_fingerprint(expected_fp, timeout_seconds=90):
    served_after = ""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        served_after = served_cert_fingerprint(BASE_URL)
        if served_after == expected_fp:
            break
        time.sleep(2)
    return served_after


def assert_gateway_serves_updated_certificate(new_fp):
    if parse.urlparse(BASE_URL).scheme == "https":
        served_after = wait_served_cert_fingerprint(new_fp)
        T.equal("Gateway data plane serves updated certificate", new_fp, served_after)
        T.match(
            "Gateway HTTPS remains reachable after certificate update",
            r"^(200|301|302|404)$",
            http_status("GET", url_path(BASE_URL), timeout=10),
        )


def section_2c_gateway_certificate_api():
    T.section("Section 2c: Gateway certificate API")
    assert_gateway_bootstrap_certificate()
    assert_gateway_initial_served_certificate()
    generated = generate_gateway_test_certificate()
    new_cert, new_key, new_fp = assert_generated_gateway_certificate(generated)
    upload = upload_gateway_certificate(new_cert, new_key)
    assert_uploaded_gateway_certificate(upload, new_fp)
    assert_gateway_serves_updated_certificate(new_fp)


def iam_log_collect_state():
    data = kubectl_json(
        ["-n", IAM_NS, "get", "configmap", IAM_LOG_STATUS_CONFIGMAP],
        timeout=30,
    )
    raw = (data or {}).get("data", {}).get("status.json", "")
    return json_loads(raw, {}) if raw else {}


def iam_log_current_task():
    state = iam_log_collect_state()
    task_id = state.get("currentCollectId", "")
    task = (state.get("tasks") or {}).get(task_id, {})
    return task_id, task


def iam_log_node(task, node_type):
    for node in task.get("nodeInfos", []) or []:
        if node.get("nodeType") == node_type:
            return node
    return {}


def wait_iam_log_collect_finished(timeout_seconds=60):
    deadline = time.time() + timeout_seconds
    last_body = ""
    while time.time() < deadline:
        last_body = http_body(
            "GET",
            url_path(IAM_LOG_URL, "AccessManager/Tenants/System/LogCollect/Progress"),
            timeout=10,
        )
        status = json_get(last_body, "data.basicInfo.collectStatus")
        if status in ("FINISH", "FAILED", "PART_FAILED"):
            return status, last_body
        time.sleep(2)
    return json_get(last_body, "data.basicInfo.collectStatus"), last_body


def assert_iam_log_nodes():
    T.equal(
        "keycloak-proxy /AccessManager/Tenants/Common/Health",
        "200",
        http_status("GET", url_path(IAM_LOG_URL, "AccessManager/Tenants/Common/Health"), timeout=10),
    )

    nodes_body = http_body(
        "GET",
        url_path(IAM_LOG_URL, "AccessManager/Tenants/System/LogCollect/Nodes?page=1&limit=100"),
        timeout=10,
    )
    T.contains("IAM log nodes include AIDP_IAM_KEYCLOAK_PROXY", "AIDP_IAM_KEYCLOAK_PROXY", nodes_body)
    proxy_discovered_node = log_nodes_response_node(nodes_body, "AIDP_IAM_KEYCLOAK_PROXY")
    T.match(
        "IAM log nodes AIDP_IAM_KEYCLOAK_PROXY nodeIp is populated",
        r"^\S+$",
        proxy_discovered_node.get("nodeIp", ""),
    )


def iam_log_payload():
    return {
        "collectUser": "test-sh",
        "scene": "e2e_iam_log",
        "startTime": "",
        "endTime": "",
        "path": "",
        "targets": [],
        "nodeList": [
            {
                "name": "iam-services",
                "nodeIp": "127.0.0.1",
                "nodeType": "AIDP_IAM_KEYCLOAK_PROXY",
                "status": "READY",
                "product": "AIDP",
                "logTypes": ["AIDP_IAM_LOG"],
            }
        ],
    }


def dispatch_iam_log_task():
    accept_body = http_body(
        "POST",
        url_path(IAM_LOG_URL, "AccessManager/Tenants/System/LogCollect/Dispatch"),
        body=iam_log_payload(),
        timeout=20,
    )
    T.contains("POST IAM LogCollect/Dispatch accepts keycloak-proxy task", "true", accept_body)


def assert_iam_log_result():
    status, status_body = wait_iam_log_collect_finished(timeout_seconds=60)
    T.equal("IAM log collect status FINISH", "FINISH", status)
    T.equal("IAM log collect progress 100", "100", json_get(status_body, "data.basicInfo.progress"))
    T.contains("IAM log collect response has keycloak-proxy node", "AIDP_IAM_KEYCLOAK_PROXY", status_body)

    _, task = iam_log_current_task()
    node = iam_log_node(task, "AIDP_IAM_KEYCLOAK_PROXY")
    T.equal("IAM keycloak-proxy log node collectState success", "2", node.get("collectState", ""))
    T.equal("IAM keycloak-proxy log node preserves nodeIp", "127.0.0.1", node.get("nodeIp", ""))
    T.match(
        "IAM keycloak-proxy log node fileName uses node-type archive name",
        r"^iam-keycloak-proxy_127\.0\.0\.1_.*-.*\.zip$",
        node.get("fileName", ""),
    )

    archive_file = str(task.get("archiveFile") or "")
    T.match("IAM log collect archive is zip", r"/tmp/iam-log-collect/.*\.zip$", archive_file)
    return archive_file


def assert_iam_log_archive_exists(archive_file):
    archive_exists = ""
    if archive_file:
        archive_exists = kubectl_exec(
            IAM_NS,
            "deploy/iam-services",
            "aidp-iam-app",
            ["python3", "-c", "import os; print('yes' if os.path.isfile(%r) else 'no')" % archive_file],
            timeout=30,
        )
    T.equal("IAM log archive exists in iam-services pod", "yes", archive_exists)


def section_2c_iam_log_collect():
    T.section("Section 2d: IAM log collection callback")
    assert_iam_log_nodes()
    dispatch_iam_log_task()
    archive_file = assert_iam_log_result()
    assert_iam_log_archive_exists(archive_file)


def section_3_no_token():
    T.section("Section 3: Protected routes reject no-token (401/403)")
    paths = [
        "/AccessManager/Tenants/%s/ACLs" % REALM,
        "/AccessManager/Tenants/System/AppManifests/TestApp",
        "/AccessManager/Tenants/%s/Action/QueryACLs" % REALM,
        "/AccessManager/Tenants",
        "/AccessManager/Tenants/System/AppManifests",
    ]
    if HAS_KB_ROUTE:
        paths.append("/KnowledgeBase/Tenants/%s/KnowledgeBases" % REALM)
    else:
        T.skip("no-token /KnowledgeBase/... (mock-kb route not installed)")
    if HAS_MEMORY_ROUTE:
        paths.append("/MemoryStore/Tenants/%s/Instances" % REALM)
    else:
        T.skip("no-token /MemoryStore/... (mock-memory route not installed)")
    if HAS_DATAAGENT_ROUTE:
        paths.append("/DataAgent/Tenants/%s/Databases" % REALM)
    else:
        T.skip("no-token /DataAgent/... (mock-dataagent route not installed)")

    for path in paths:
        T.match("no-token %s -> 401/403" % path, r"^(401|403)$", http_status("GET", url_path(BASE_URL, path)))


def section_4_admin_token():
    global CS, ADMIN_TOKEN, ADMIN_SUB
    T.section("Section 4: Admin token (aidp-client + admin user, password grant)")
    CS = get_client_secret()
    T.match("aidp-client client-secret present", r"^[A-Za-z0-9]{20,}$", CS)

    ADMIN_TOKEN = request_token(ADMIN_USER, ADMIN_PASSWORD)
    T.equal("admin token issued", "yes", "yes" if ADMIN_TOKEN else "no")
    admin_groups = jwt_claim(ADMIN_TOKEN, "groups")
    T.contains("admin token contains 'master-admins' group", "master-admins", admin_groups)
    T.contains("admin token contains 'all-users' group", "all-users", admin_groups)
    T.contains("admin token iss /realms/%s" % REALM, "realms/%s" % REALM, jwt_claim(ADMIN_TOKEN, "iss"))
    ADMIN_SUB = jwt_claim(ADMIN_TOKEN, "sub")
    T.match("admin token sub is UUID", r"[0-9a-f-]{36}", ADMIN_SUB)

    web_token = request_public_token("aidp-web", ADMIN_USER, ADMIN_PASSWORD)
    T.equal("aidp-web public token issued", "yes", "yes" if web_token else "no")
    web_groups = jwt_claim(web_token, "groups")
    T.contains("aidp-web token contains 'master-admins' group", "master-admins", web_groups)
    T.contains("aidp-web token contains 'all-users' group", "all-users", web_groups)
    T.contains("aidp-web token iss /realms/%s" % REALM, "realms/%s" % REALM, jwt_claim(web_token, "iss"))


def resource_acl_column_name(column):
    sql = (
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='resource_acl' AND column_name='%s';"
    ) % column
    return psql_iam(sql)


def section_5_acl_schema():
    T.section("Section 5: New ACL table schema verification")
    for col in ("user_path", "object_path", "role_path"):
        got = resource_acl_column_name(col)
        T.equal("resource_acl has column %s" % col, col, got)

    for col in ("app_name", "resource_type", "resource_id", "subject_type", "subject_id", "permission"):
        got = resource_acl_column_name(col)
        T.equal("resource_acl does NOT have old column %s" % col, "", got)

    for col in ("id", "tenant_id", "created_at", "created_by"):
        got = resource_acl_column_name(col)
        T.equal("resource_acl has column %s" % col, col, got)


def section_6_manifest_api():
    T.section("Section 6: Manifest API (AppManifests CRUD + default_acl sync)")
    _ = psql_iam("DELETE FROM app_manifests WHERE namespace='%s';" % TEST_NS)
    manifest = {
        "namespace": TEST_NS,
        "display_name": "Test Application",
        "base_url": "http://mock-testapp.example.com",
        "resources": [
            {
                "type": "Resources",
                "display_name": "Generic test resources",
                "path_pattern": "/%s/Tenants/{tenantId}/Resources/{id}" % TEST_NS,
                "methods": ["GET", "PUT", "PATCH", "DELETE"],
                "actions": [],
                "default_acl": [
                    {
                        "user_template": "AccessManager/Tenants/{tenantId}/Groups/all-users",
                        "object_template": "%s/Tenants/{tenantId}/Resources" % TEST_NS,
                        "role_path": "AccessManager/Tenants/System/Roles/Viewer",
                    }
                ],
                "children": [],
            }
        ],
    }
    put_code = admin_status(
        "PUT",
        system_url("AppManifests/%s" % TEST_NS),
        body=manifest,
    )
    T.match("PUT /AccessManager/Tenants/System/AppManifests/%s -> 200/201" % TEST_NS, r"^(200|201)$", put_code)

    get_manifest = admin_body("GET", system_url("AppManifests/%s" % TEST_NS))
    T.contains("GET manifest returns namespace", TEST_NS, get_manifest)
    T.contains("GET manifest returns resource_types", "Resources", get_manifest)

    time.sleep(2)
    default_acl_row = psql_iam(
        "SELECT role_path FROM resource_acl "
        "WHERE object_path='%s/Tenants/%s/Resources' "
        "AND user_path LIKE '%%all-users%%' LIMIT 1;"
        % (TEST_NS, REALM)
    )
    T.contains("default_acl sync wrote Viewer ACL", "Viewer", default_acl_row)

    list_manifests = admin_body("GET", system_url("AppManifests"))
    T.contains("GET AppManifests list contains %s" % TEST_NS, TEST_NS, list_manifests)

    del_code = admin_status("DELETE", system_url("AppManifests/%s" % TEST_NS))
    T.match("DELETE /AccessManager/Tenants/System/AppManifests/%s -> 200/204" % TEST_NS, r"^(200|204)$", del_code)
    get_after = admin_status("GET", system_url("AppManifests/%s" % TEST_NS))
    T.match("GET deleted manifest -> 404", r"^(404)$", get_after)


def section_7_acl_management():
    T.section("Section 7: ACL management API (PUT/GET/DELETE/QueryACLs)")
    acl_obj = "TestNS/Tenants/%s/Resources/acl-api-test-001" % REALM
    acl_user = "AccessManager/Tenants/%s/Users/test-user-acl" % REALM
    acl_role = "AccessManager/Tenants/System/Roles/Contributor"
    _ = psql_iam("DELETE FROM resource_acl WHERE object_path='%s';" % acl_obj)

    put_acl_status = admin_status(
        "PUT",
        tenant_url("ACLs"),
        body={"user_path": acl_user, "object_path": acl_obj, "role_path": acl_role},
    )
    T.match("PUT /AccessManager/Tenants/%s/ACLs -> 200/201" % REALM, r"^(200|201)$", put_acl_status)

    get_acl = admin_body("GET", tenant_url("ACLs?object=%s" % parse.quote(acl_obj, safe="/:")))
    T.contains("GET ACLs returns user_path", "test-user-acl", get_acl)
    T.contains("GET ACLs returns role_path Contributor", "Contributor", get_acl)

    query_resp = admin_body(
        "POST",
        tenant_url("Action/QueryACLs"),
        body={
            "queries": [
                {"user_path": acl_user, "object_path": acl_obj},
                {"user_path": acl_user, "object_path": "TestNS/Tenants/%s/Resources/nonexistent-999" % REALM},
            ]
        },
    )
    T.contains("QueryACLs returns role_path for existing ACL", "Contributor", query_resp)

    del_acl = admin_status(
        "DELETE",
        tenant_url("ACLs"),
        body={"user_path": acl_user, "object_path": acl_obj},
    )
    T.match("DELETE /AccessManager/Tenants/%s/ACLs -> 200/204" % REALM, r"^(200|204)$", del_acl)
    acl_count = psql_iam(
        "SELECT COUNT(*) FROM resource_acl WHERE object_path='%s' AND user_path='%s';"
        % (acl_obj, acl_user)
    )
    T.equal("ACL row removed after DELETE", "0", acl_count)


def section_8_acl_cascade():
    T.section("Section 8: ACL write and cascade delete via AccessManager API")
    obj = "DataAgent/Tenants/%s/DataAgentDBs/s8-test-db-001" % REALM
    child = "DataAgent/Tenants/%s/DataAgentDBs/s8-test-db-001/Tables/tbl-001" % REALM
    user = "AccessManager/Tenants/%s/Users/s8-test-user" % REALM
    role = "AccessManager/Tenants/System/Roles/Owner"
    _ = psql_iam("DELETE FROM resource_acl WHERE object_path LIKE 'DataAgent/Tenants/%s/DataAgentDBs/s8-%%';" % REALM)

    put_code = admin_status("PUT", tenant_url("ACLs"), body={"user_path": user, "object_path": obj, "role_path": role})
    T.match("PUT ACL for DataAgentDB resource -> 200/201", r"^(200|201)$", put_code)
    row = psql_iam(
        "SELECT role_path FROM resource_acl WHERE user_path='%s' AND object_path='%s' LIMIT 1;"
        % (user, obj)
    )
    T.contains("ACL row written to DB with Owner role", "Owner", row)

    _ = psql_iam(
        "INSERT INTO resource_acl (tenant_id, user_path, object_path, role_path, created_by) "
        "VALUES ('%s', '%s', '%s', '%s', 'test') ON CONFLICT DO NOTHING;"
        % (REALM, user, child, role)
    )
    del_code = admin_status("DELETE", tenant_url("ACLs"), body={"user_path": user, "object_path": obj})
    T.match("DELETE ACL for DataAgentDB resource -> 200/204", r"^(200|204)$", del_code)
    count = psql_iam("SELECT COUNT(*) FROM resource_acl WHERE user_path='%s' AND object_path='%s';" % (user, obj))
    T.equal("parent ACL row removed after DELETE", "0", count)


def section_9_acl_delete_exact():
    T.section("Section 9: ACL DELETE removes exact row only (no cascade)")
    parent = "DataAgent/Tenants/%s/DataAgentDBs/s9-test-db-001" % REALM
    child1 = resource_path(parent, "Tables/tbl-001")
    child2 = resource_path(parent, "Tables/tbl-002")
    user = "AccessManager/Tenants/%s/Users/s9-test-user" % REALM
    role = "AccessManager/Tenants/System/Roles/Owner"
    _ = psql_iam("DELETE FROM resource_acl WHERE object_path LIKE 'DataAgent/Tenants/%s/DataAgentDBs/s9-%%';" % REALM)
    _ = psql_iam(
        "INSERT INTO resource_acl (tenant_id, user_path, object_path, role_path, created_by) VALUES "
        "('%s', '%s', '%s', '%s', 'test'),"
        "('%s', '%s', '%s', '%s', 'test'),"
        "('%s', '%s', '%s', '%s', 'test') ON CONFLICT DO NOTHING;"
        % (REALM, user, parent, role, REALM, user, child1, role, REALM, user, child2, role)
    )
    del_code = admin_status("DELETE", tenant_url("ACLs"), body={"user_path": user, "object_path": parent})
    T.match("DELETE parent ACL -> 200/204", r"^(200|204)$", del_code)
    parent_count = psql_iam(
        "SELECT COUNT(*) FROM resource_acl WHERE user_path='%s' AND object_path='%s';"
        % (user, parent)
    )
    T.equal("parent ACL row removed", "0", parent_count)
    child_count = psql_iam(
        "SELECT COUNT(*) FROM resource_acl WHERE user_path='%s' AND object_path LIKE '%s/%%';"
        % (user, parent)
    )
    T.equal("child ACL rows remain (DELETE is exact-match only)", "2", child_count)
    _ = psql_iam("DELETE FROM resource_acl WHERE user_path='%s' AND object_path LIKE '%s%%';" % (user, parent))


def section_10_acl_inheritance():
    T.section("Section 10: ACL prefix matching inheritance")
    parent_obj = "TestNS/Tenants/%s/Resources" % REALM
    child_obj = resource_path(parent_obj, "res-inherit-001")
    alice_path = "AccessManager/Tenants/%s/Users/alice" % REALM
    contrib_role = "AccessManager/Tenants/System/Roles/Contributor"
    _ = psql_iam("DELETE FROM resource_acl WHERE user_path='%s';" % alice_path)

    put_parent = admin_status(
        "PUT",
        tenant_url("ACLs"),
        body={"user_path": alice_path, "object_path": parent_obj, "role_path": contrib_role},
    )
    T.match("PUT parent ACL for alice -> 200/201", r"^(200|201)$", put_parent)
    inherit_resp = admin_body(
        "POST",
        tenant_url("Action/QueryACLs"),
        body={"queries": [{"user_path": alice_path, "object_path": child_obj}]},
    )
    T.contains("child object inherits Contributor from parent prefix", "Contributor", inherit_resp)
    parent_count = psql_iam(
        "SELECT COUNT(*) FROM resource_acl WHERE user_path='%s' AND object_path='%s';"
        % (alice_path, parent_obj)
    )
    child_count = psql_iam(
        "SELECT COUNT(*) FROM resource_acl WHERE user_path='%s' AND object_path='%s';"
        % (alice_path, child_obj)
    )
    T.equal("parent ACL row in DB", "1", parent_count)
    T.equal("child ACL row NOT in DB (inherited, not stored)", "0", child_count)
    _ = admin_status(
        "DELETE",
        tenant_url("ACLs"),
        body={"user_path": alice_path, "object_path": parent_obj},
    )


def put_acl(user_path, object_path, role_path):
    return admin_status(
        "PUT",
        tenant_url("ACLs"),
        body={"user_path": user_path, "object_path": object_path, "role_path": role_path},
    )


def delete_acl(user_path, object_path):
    return admin_status(
        "DELETE",
        tenant_url("ACLs"),
        body={"user_path": user_path, "object_path": object_path},
    )


def query_acl(user_path, object_path):
    return admin_body(
        "POST",
        tenant_url("Action/QueryACLs"),
        body={"queries": [{"user_path": user_path, "object_path": object_path}]},
    )


def assert_role_matrix_kb_access():
    if HAS_KB_ROUTE:
        code = token_status(NORMAL_TOKEN, "GET", kb_url("KnowledgeBases"))
        T.match("normal-user GET /KnowledgeBase/.../KnowledgeBases -> 200 (all-users)", r"^(200)$", code)
    else:
        T.skip("normal-user GET /KnowledgeBase/... (mock-kb route not installed)")


def assert_role_matrix_acl_role(user_path, object_path, role_name):
    role_path = "AccessManager/Tenants/System/Roles/%s" % role_name
    _ = delete_acl(user_path, object_path)
    _ = put_acl(user_path, object_path, role_path)
    qr = query_acl(user_path, object_path)
    T.contains("QueryACLs: %s role stored correctly" % role_name, role_name, qr)


def section_11_role_matrix():
    global NORMAL_TOKEN
    T.section("Section 11: Default role matrix (Owner/Contributor/Viewer)")
    NORMAL_TOKEN = request_token(NORMAL_USER, NORMAL_PASSWORD)
    if not NORMAL_TOKEN:
        T.skip("Section 11 - no normal-user token")
        return

    normal_sub = jwt_claim(NORMAL_TOKEN, "sub")
    role_obj = "DataAgent/Tenants/%s/DataAgentDBs/role-matrix-test-db-001" % REALM
    normal_user_path = "AccessManager/Tenants/%s/Users/%s" % (REALM, normal_sub)
    contrib_role = "AccessManager/Tenants/System/Roles/Contributor"
    _ = psql_iam("DELETE FROM resource_acl WHERE object_path='%s';" % role_obj)

    _ = put_acl(normal_user_path, role_obj, contrib_role)
    qr = query_acl(normal_user_path, role_obj)
    T.contains("QueryACLs: Contributor role stored correctly", "Contributor", qr)
    assert_role_matrix_kb_access()

    code = token_status(NORMAL_TOKEN, "GET", system_url("AppManifests"))
    T.match("normal-user GET /AccessManager/Tenants/System/AppManifests -> 403 (not admin)", r"^(401|403)$", code)

    for role_name in ("Viewer", "Owner"):
        assert_role_matrix_acl_role(normal_user_path, role_obj, role_name)

    _ = delete_acl(normal_user_path, role_obj)


def section_12_allowed_ids():
    T.section("Section 12: X-Allowed-Ids injection on collection GET")
    if not HAS_KB_ROUTE:
        T.skip("Section 12 - mock-kb route not installed")
        return

    kb1_body = admin_body("POST", kb_url("KnowledgeBases"), body={"name": "xi-test-kb-1", "description": "test"})
    kb2_body = admin_body("POST", kb_url("KnowledgeBases"), body={"name": "xi-test-kb-2", "description": "test"})
    kb1 = json_get(kb1_body, "data.id")
    kb2 = json_get(kb2_body, "data.id")
    if not kb1 or not kb2:
        T.skip("Section 12 - could not create test KnowledgeBases")
        return

    admin_path = "AccessManager/Tenants/%s/Users/%s" % (REALM, ADMIN_SUB)
    _ = psql_iam(
        "INSERT INTO resource_acl (tenant_id, user_path, object_path, role_path, created_by) VALUES "
        "('%s', '%s', 'KnowledgeBase/Tenants/%s/KnowledgeBases/%s', "
        "'AccessManager/Tenants/System/Roles/Owner', 'test'),"
        "('%s', '%s', 'KnowledgeBase/Tenants/%s/KnowledgeBases/%s', "
        "'AccessManager/Tenants/System/Roles/Owner', 'test') ON CONFLICT DO NOTHING;"
        % (REALM, admin_path, REALM, kb1, REALM, admin_path, REALM, kb2)
    )
    time.sleep(1)
    body = admin_body("GET", kb_url("KnowledgeBases"))
    T.contains("GET /KnowledgeBase/.../KnowledgeBases body contains KB1", kb1, body)
    T.contains("GET /KnowledgeBase/.../KnowledgeBases body contains KB2", kb2, body)

    _ = psql_iam(
        "DELETE FROM resource_acl WHERE user_path='%s' "
        "AND object_path IN ('KnowledgeBase/Tenants/%s/KnowledgeBases/%s',"
        "'KnowledgeBase/Tenants/%s/KnowledgeBases/%s');"
        % (admin_path, REALM, kb1, REALM, kb2)
    )
    _ = admin_status("DELETE", kb_url("KnowledgeBases/%s" % kb1))
    _ = admin_status("DELETE", kb_url("KnowledgeBases/%s" % kb2))


def section_13_tenant_isolation():
    T.section("Section 13: Tenant isolation (cross-tenant request rejected)")
    T.match(
        "cross-tenant ACL request -> 403",
        r"^(403)$",
        admin_status("GET", url_path(BASE_URL, "AccessManager/Tenants/t-other-tenant/ACLs")),
    )
    T.match(
        "cross-tenant Users request -> 403",
        r"^(403)$",
        admin_status("GET", url_path(BASE_URL, "AccessManager/Tenants/t-other-tenant/Users")),
    )
    same = admin_status("GET", tenant_url("ACLs?object=TestNS/Tenants/%s/Resources/probe" % REALM))
    T.match("same-tenant ACL request -> 200", r"^(200)$", same)


def section_14_opa_authz():
    T.section("Section 14: OPA path-level authz still works (permission_groups)")
    T.equal(
        "admin GET /AccessManager/Tenants -> 200",
        "200",
        admin_status("GET", url_path(BASE_URL, "AccessManager/Tenants")),
    )
    if HAS_KB_ROUTE:
        T.equal(
            "admin GET /KnowledgeBase/... -> 200 (super-bypass)",
            "200",
            admin_status("GET", kb_url("KnowledgeBases")),
        )
    else:
        T.skip("admin GET /KnowledgeBase/... (mock-kb route not installed)")

    code = admin_status("GET", tenant_url("ACLs?object=DataAgent/Tenants/%s/DataAgentDBs/probe" % REALM))
    T.equal("admin GET /AccessManager/... -> 200 (super-bypass)", "200", code)

    opa_rules = pod_http_body("http://localhost:8181/v1/data/path_rules")
    if opa_rules:
        T.contains("OPA path_rules contains /KnowledgeBase/", "/KnowledgeBase/", opa_rules)
        T.contains("OPA path_rules contains /AccessManager/", "/AccessManager/", opa_rules)
    else:
        T.skip("OPA data endpoint not reachable from opa container")

    if NORMAL_TOKEN:
        if HAS_KB_ROUTE:
            code = token_status(NORMAL_TOKEN, "GET", kb_url("KnowledgeBases"))
            T.match("normal-user GET /KnowledgeBase/.../KnowledgeBases -> 200 (all-users)", r"^(200)$", code)
        else:
            T.skip("normal-user GET /KnowledgeBase/... (mock-kb route not installed)")
        code = token_status(NORMAL_TOKEN, "GET", system_url("AppManifests"))
        T.match("normal-user GET /AccessManager/Tenants/System/AppManifests -> 403 (not admin)", r"^(401|403)$", code)
    else:
        T.skip("Section 14 normal-user checks - no token")


def section_15_access_manager_routes():
    T.section("Section 15: /AccessManager/ routes work (v2.0 unified API)")
    for path in (
        "/AccessManager/Tenants",
        "/AccessManager/Tenants/System/Apps",
        "/AccessManager/Tenants/System/AppManifests",
        "/AccessManager/Tenants/%s/Users" % REALM,
        "/AccessManager/Tenants/%s/Groups" % REALM,
        "/AccessManager/Tenants/%s/ApiKeys" % REALM,
    ):
        T.match("v2.0 %s -> 200" % path, r"^(200)$", admin_status("GET", url_path(BASE_URL, path)))

    legacy = admin_status(
        "GET",
        url_path(BASE_URL, "acl/v1/resources/probe-id/permissions?app_name=KnowledgeBase&resource_type=KnowledgeBases"),
    )
    T.match("legacy /acl/v1 -> 200/403/404", r"^(200|403|404)$", legacy)


def assert_identity_list_routes():
    T.match(
        "GET /AccessManager/Tenants/%s/Users -> 200" % REALM,
        r"^(200)$",
        admin_status("GET", tenant_url("Users")),
    )
    T.match(
        "GET /AccessManager/Tenants/%s/Groups -> 200" % REALM,
        r"^(200)$",
        admin_status("GET", tenant_url("Groups")),
    )


def create_identity_test_user():
    create_code = admin_status(
        "PUT",
        tenant_url("Users"),
        body={"username": "test-new-user-v2", "email": "test-new-user-v2@example.com", "password": "Test@12345"},
    )
    T.match("PUT /AccessManager/Tenants/%s/Users -> 200/201" % REALM, r"^(200|201)$", create_code)
    users = admin_body("GET", tenant_url("Users"))
    T.contains("new user appears in list", "test-new-user-v2", users)
    return users, find_user_id(users, "test-new-user-v2")


def assert_password_verify(users):
    normal_uid = find_user_id(users, NORMAL_USER)
    if normal_uid:
        verify_ok = admin_body(
            "POST",
            tenant_url("Users/%s/PasswordVerify" % normal_uid),
            body={"password": NORMAL_PASSWORD},
        )
        T.contains("PasswordVerify accepts current password", '"valid":true', verify_ok)
        verify_bad = admin_body(
            "POST",
            tenant_url("Users/%s/PasswordVerify" % normal_uid),
            body={"password": "DefinitelyWrong@123"},
        )
        T.contains("PasswordVerify rejects wrong password", '"valid":false', verify_bad)
    else:
        T.skip("Section 16 - normal-user not found for PasswordVerify")


def assert_email_mask_routes(new_uid):
    if new_uid:
        mask = http_body("GET", realm_url("email-mask/get-email-masked?username=test-new-user-v2"))
        T.contains("email-mask get-email-masked finds user", '"found":true', mask)
        T.contains("email-mask get-email-masked reports email", '"hasEmail":true', mask)
        T.contains("email-mask get-email-masked masks email", '"maskedEmail":"t*****@example.com"', mask)
        verify_email = http_body(
            "GET",
            realm_url("email-mask/verify-email?username=test-new-user-v2&email=test-new-user-v2@example.com"),
        )
        T.contains("email-mask verify-email accepts matching email", '"match":true', verify_email)
        verify_email_bad = http_body(
            "GET",
            realm_url("email-mask/verify-email?username=test-new-user-v2&email=wrong@example.com"),
        )
        T.contains("email-mask verify-email rejects wrong email", '"match":false', verify_email_bad)
        deleted = admin_status("DELETE", tenant_url("Users/%s" % new_uid))
        T.match("DELETE /AccessManager/Tenants/%s/Users/{id} -> 200/204" % REALM, r"^(200|204)$", deleted)
    else:
        T.skip("Section 16 - could not extract new user ID for cleanup")


def assert_identity_group_routes():
    group_code = admin_status("PUT", tenant_url("Groups"), body={"name": "test-new-group-v2"})
    T.match("PUT /AccessManager/Tenants/%s/Groups -> 200/201" % REALM, r"^(200|201)$", group_code)
    groups = admin_body("GET", tenant_url("Groups"))
    T.contains("new group appears in list", "test-new-group-v2", groups)
    gid = find_group_id(groups, "test-new-group-v2")
    if gid:
        _ = admin_status("DELETE", tenant_url("Groups/%s" % gid))


def section_16_identity_routes():
    T.section("Section 16: New /AccessManager/Tenants/{tid}/ identity routes")
    assert_identity_list_routes()
    users, new_uid = create_identity_test_user()
    assert_password_verify(users)
    assert_email_mask_routes(new_uid)
    assert_identity_group_routes()


def section_16b_group_detail():
    T.section("Section 16b: Group detail - preset and custom groups")

    def get_group_id(name):
        groups = admin_body("GET", tenant_url("Groups"))
        return find_group_id(groups, name)

    for name in ("master-admins", "tenant-admins", "all-users"):
        gid = get_group_id(name)
        if not gid:
            T.skip("Groups/%s not found - skipping detail check" % name)
            continue
        detail = admin_body("GET", tenant_url("Groups/%s" % gid))
        detail_code = admin_status("GET", tenant_url("Groups/%s" % gid))
        T.match("GET Groups/%s detail -> 200" % name, r"^200$", detail_code)
        T.contains("Groups/%s detail has id" % name, gid, detail)
        T.contains("Groups/%s detail has name" % name, name, detail)
        T.match("Groups/%s detail has member_total" % name, r'"member_total"', detail)
        T.match("Groups/%s detail has members array" % name, r'"members"', detail)
        T.match("Groups/%s source is preset" % name, r'"source"\s*:\s*"preset"', detail)

    create = admin_status(
        "PUT",
        tenant_url("Groups"),
        body={"name": "test-detail-group", "description": "detail test group"},
    )
    T.match("PUT test-detail-group -> 200/201", r"^(200|201)$", create)
    detail_gid = get_group_id("test-detail-group")
    if not detail_gid:
        T.skip("Section 16b - could not extract test-detail-group ID")
        return

    detail = admin_body("GET", tenant_url("Groups/%s" % detail_gid))
    detail_code = admin_status("GET", tenant_url("Groups/%s" % detail_gid))
    T.match("GET custom group detail -> 200", r"^200$", detail_code)
    T.contains("custom group detail has id", detail_gid, detail)
    T.contains("custom group detail has name", "test-detail-group", detail)
    T.match("custom group detail has member_total", r'"member_total"', detail)
    T.match("custom group detail has members array", r'"members"', detail)
    T.match("custom group source is custom", r'"source"\s*:\s*"custom"', detail)
    page = admin_body("GET", tenant_url("Groups/%s?first=0&max=1" % detail_gid))
    T.match("custom group detail pagination accepted", r'"members"', page)
    fake_code = admin_status("GET", tenant_url("Groups/00000000-0000-0000-0000-000000000000"))
    T.match("GET non-existent group -> 404", r"^404$", fake_code)
    _ = admin_status("DELETE", tenant_url("Groups/%s" % detail_gid))


def assert_api_key_storage_and_rotation():
    ak = admin_body(
        "POST",
        tenant_url("ApiKeys"),
        body={"app_name": "KnowledgeBase", "description": "test-key", "subject_id": "svc-test"},
    )
    ak_plain = json_get(ak, "api_key")
    ak_id = json_get(ak, "id")
    ak_prefix = json_get(ak, "key_prefix")
    T.match("POST /api-keys returns plaintext", r"^ak_[A-Za-z0-9_-]{20,}$", ak_plain)
    T.match("POST /api-keys returns id", r".+", ak_id)

    db_hash = psql_iam("SELECT api_key_hash FROM api_keys WHERE id='%s';" % ak_id)
    T.not_contains("DB hash != plaintext", ak_plain, db_hash)
    T.match("DB hash is sha256 hex", r"^[a-f0-9]{64}$", db_hash)

    listing = admin_body("GET", tenant_url("ApiKeys"))
    T.contains("GET /api-keys lists prefix", ak_prefix, listing)
    T.not_contains("GET /api-keys does NOT expose plaintext", ak_plain, listing)

    rot = admin_body("POST", tenant_url("ApiKeys/%s/Rotate" % ak_id))
    ak_new = json_get(rot, "api_key")
    T.match("rotate returns new plaintext", r"^ak_[A-Za-z0-9_-]{20,}$", ak_new)
    T.equal("rotate plaintext differs", "yes", "yes" if ak_new != ak_plain else "no")

    _ = admin_body("PUT", tenant_url("ApiKeys/%s" % ak_id), body={"enabled": False})
    T.equal("DB enabled=false after disable", "f", psql_iam("SELECT enabled FROM api_keys WHERE id='%s';" % ak_id))
    _ = admin_body("DELETE", tenant_url("ApiKeys/%s" % ak_id))
    T.equal("DB row removed after DELETE", "0", psql_iam("SELECT COUNT(*) FROM api_keys WHERE id='%s';" % ak_id))


def assert_api_key_auth():
    fresh = admin_body(
        "POST",
        tenant_url("ApiKeys"),
        body={
            "app_name": "KnowledgeBase",
            "description": "auth-test",
            "subject_id": "svc-auth",
            "allowed_paths": ["/KnowledgeBase"],
        },
    )
    fresh_key = json_get(fresh, "api_key")
    fresh_id = json_get(fresh, "id")
    if not fresh_key:
        return
    if HAS_KB_ROUTE:
        url = kb_url("KnowledgeBases")
        code = http_status("GET", url, headers={"X-API-Key": fresh_key})
        T.match("X-API-Key access /KnowledgeBase/... -> 200/403", r"^(200|403)$", code)
        _ = admin_body("PUT", tenant_url("ApiKeys/%s" % fresh_id), body={"enabled": False})
        time.sleep(1)
        disabled = http_status("GET", url, headers={"X-API-Key": fresh_key})
        invalid = http_status("GET", url, headers={"X-API-Key": "ak_invalid_xxx"})
        T.match("disabled API Key -> 401/403", r"^(401|403)$", disabled)
        T.match("invalid API Key -> 401/403", r"^(401|403)$", invalid)
    else:
        T.skip("X-API-Key /KnowledgeBase/... tests (mock-kb route not installed)")

    _ = admin_body("DELETE", tenant_url("ApiKeys/%s" % fresh_id))


def section_17_api_keys():
    T.section("Section 17: API Keys")
    assert_api_key_storage_and_rotation()
    assert_api_key_auth()


def section_18_app_disabled():
    T.section("Section 18: App disabled blocks access (even for admins)")
    _ = psql_iam("UPDATE apps SET enabled=false WHERE app_name='KnowledgeBase';")
    if HAS_KB_ROUTE:
        time.sleep(35)
        T.match(
            "/KnowledgeBase/... with KnowledgeBase disabled -> 403",
            r"^(403)$",
            admin_status("GET", kb_url("KnowledgeBases")),
        )
    else:
        T.skip("/KnowledgeBase/... disabled test (mock-kb route not installed)")

    _ = psql_iam("UPDATE apps SET enabled=true WHERE app_name='KnowledgeBase';")
    if HAS_KB_ROUTE:
        time.sleep(35)
        T.match(
            "/KnowledgeBase/... re-enabled -> 200",
            r"^(200|403)$",
            admin_status("GET", kb_url("KnowledgeBases")),
        )
    else:
        T.skip("/KnowledgeBase/... re-enabled test (mock-kb route not installed)")


def cleanup_dataagent_manifest(da_ns):
    _ = psql_iam("DELETE FROM app_manifests WHERE namespace='%s';" % da_ns)
    _ = psql_iam("DELETE FROM resource_acl WHERE object_path LIKE 'DataAgent/Tenants/%s/%%';" % REALM)


def register_dataagent_manifest(da_ns):
    put_code = admin_status(
        "PUT",
        system_url("AppManifests/%s" % da_ns),
        body=manifest_body("DA_MANIFEST_FILE", 1),
        headers={"Content-Type": "application/json"},
    )
    T.match("PUT DataAgent manifest -> 200/201", r"^(200|201)$", put_code)
    time.sleep(2)


def assert_dataagent_default_acl(all_users, tenant_admins):
    contrib = psql_iam(
        "SELECT role_path FROM resource_acl WHERE tenant_id='%s' "
        "AND object_path='DataAgent/Tenants/%s/DataAgentDBs' "
        "AND user_path='%s' LIMIT 1;" % (REALM, REALM, all_users)
    )
    T.contains("default_acl sync: all-users -> Contributor on DataAgentDBs", "Contributor", contrib)
    owner = psql_iam(
        "SELECT role_path FROM resource_acl WHERE tenant_id='%s' "
        "AND object_path='DataAgent/Tenants/%s/DataAgentDBs' "
        "AND user_path='%s' LIMIT 1;" % (REALM, REALM, tenant_admins)
    )
    T.contains("default_acl sync: tenant-admins -> Owner on DataAgentDBs", "Owner", owner)


def assert_dataagent_inherited_acls(all_users, tenant_admins, da_db_obj, da_table_obj):
    q1 = query_acl(all_users, da_db_obj)
    T.contains("QueryACLs: all-users inherits Contributor on DataAgentDB instance", "Contributor", q1)
    q2 = query_acl(tenant_admins, da_db_obj)
    T.contains("QueryACLs: tenant-admins inherits Owner on DataAgentDB instance", "Owner", q2)
    q3 = query_acl(all_users, da_table_obj)
    T.contains("QueryACLs: Tables inherit Contributor from DataAgentDBs type", "Contributor", q3)


def assert_dataagent_admin_acl(da_db_obj):
    admin_user_path = "AccessManager/Tenants/%s/Users/%s" % (REALM, ADMIN_SUB)
    _ = psql_iam(
        "INSERT INTO resource_acl (tenant_id, user_path, object_path, role_path, created_by) "
        "VALUES ('%s', '%s', '%s', 'AccessManager/Tenants/System/Roles/Owner', 'test-ext-proc') "
        "ON CONFLICT DO NOTHING;"
        % (REALM, admin_user_path, da_db_obj)
    )
    q4 = query_acl(admin_user_path, da_db_obj)
    T.contains("QueryACLs: admin has Owner on specific DataAgentDB instance", "Owner", q4)


def assert_dataagent_manifest_read(da_ns):
    da_get = admin_body("GET", system_url("AppManifests/%s" % da_ns))
    T.contains("GET DataAgent manifest returns namespace", "DataAgent", da_get)
    T.contains("GET DataAgent manifest returns DataAgentDBs", "DataAgentDBs", da_get)


def section_19_dataagent_manifest():
    T.section("Section 19: DataAgent manifest registration + authorization")
    da_ns = "DataAgent"
    da_db_id = "da-test-db-001"
    da_db_obj = "DataAgent/Tenants/%s/DataAgentDBs/%s" % (REALM, da_db_id)
    da_table_obj = resource_path(da_db_obj, "Tables/tbl-001")
    all_users = "AccessManager/Tenants/%s/Groups/all-users" % REALM
    tenant_admins = "AccessManager/Tenants/%s/Groups/tenant-admins" % REALM

    cleanup_dataagent_manifest(da_ns)
    register_dataagent_manifest(da_ns)
    assert_dataagent_default_acl(all_users, tenant_admins)
    assert_dataagent_inherited_acls(all_users, tenant_admins, da_db_obj, da_table_obj)
    assert_dataagent_admin_acl(da_db_obj)
    assert_dataagent_manifest_read(da_ns)
    cleanup_dataagent_manifest(da_ns)


def assert_password_policy_update():
    policy_init = admin_body("GET", tenant_url("PasswordPolicy"))
    T.contains("GET password-policy returns expire_days field", "expire_days", policy_init)

    put = admin_status(
        "PUT",
        tenant_url("PasswordPolicy"),
        body={"expire_days": 90, "min_length": 8, "require_digits": True},
    )
    T.match("PUT password-policy -> 200", r"^200$", put)
    policy = admin_body("GET", tenant_url("PasswordPolicy"))
    T.contains("GET password-policy: expire_days=90", '"expire_days":90', policy)
    T.contains("GET password-policy: min_length=8", '"min_length":8', policy)
    T.contains("GET password-policy: require_digits=true", '"require_digits":true', policy)
    T.contains("GET password-policy: require_uppercase=false", '"require_uppercase":false', policy)

    partial = admin_status("PUT", tenant_url("PasswordPolicy"), body={"require_uppercase": True})
    T.match("PUT password-policy partial update -> 200", r"^200$", partial)
    after = admin_body("GET", tenant_url("PasswordPolicy"))
    T.contains("partial update: expire_days still 90", '"expire_days":90', after)
    T.contains("partial update: require_uppercase now true", '"require_uppercase":true', after)
    T.contains("partial update: require_digits still true", '"require_digits":true', after)


def assert_admin_password_status():
    admin_id = find_first_user_id(admin_body("GET", tenant_url("Users?search=%s" % parse.quote(ADMIN_USER))))
    if admin_id:
        status = admin_body("GET", tenant_url("Users/%s/PasswordStatus" % admin_id))
        for field in ("user_id", "credential_created_at", "is_temporary", "days_remaining", "is_expired"):
            T.contains("GET password-status: has %s" % field, field, status)
        T.contains("GET password-status: expiry_days=90", '"expiry_days":90', status)
        T.contains("GET password-status: is_expired=false", '"is_expired":false', status)
    else:
        T.skip("GET password-status (admin user ID not found)")

    status_404 = admin_status("GET", tenant_url("Users/nonexistent-uuid-000/PasswordStatus"))
    T.match("GET password-status unknown user -> 404", r"^404$", status_404)


def assert_password_reset():
    tmp_user = "pw-test-%s" % int(time.time())
    tmp_resp = admin_body(
        "PUT",
        tenant_url("Users"),
        body={"username": tmp_user, "password": "Init@1234", "temporary_password": False},
    )
    tmp_id = json_get(tmp_resp, "id")
    if tmp_id:
        reset = admin_status(
            "PUT",
            tenant_url("Users/%s/Password" % tmp_id),
            body={"password": "NewPass@5678"},
        )
        T.match("PUT reset password -> 204", r"^204$", reset)
        status_after = admin_body("GET", tenant_url("Users/%s/PasswordStatus" % tmp_id))
        T.contains("password-status after reset: is_temporary=true", '"is_temporary":true', status_after)
        _ = admin_body("DELETE", tenant_url("Users/%s" % tmp_id))
    else:
        T.skip("reset password test (temp user creation failed)")


def reset_password_policy():
    _ = admin_status(
        "PUT",
        tenant_url("PasswordPolicy"),
        body={
            "expire_days": None,
            "min_length": None,
            "require_uppercase": False,
            "require_lowercase": False,
            "require_digits": False,
            "require_special": False,
            "history_count": None,
        },
    )


def section_20_password_policy():
    T.section("Section 20: Password policy + password status APIs")
    _ = refresh_admin_token()
    assert_password_policy_update()
    assert_admin_password_status()
    assert_password_reset()
    reset_password_policy()


def section_21_batch_user_creation():
    T.section("Section 21: Batch user creation (JSON body)")
    _ = refresh_admin_token()
    ts = int(time.time())
    bc_u1 = "bc-user1-%s" % ts
    bc_u2 = "bc-user2-%s" % ts
    resp = admin_body(
        "POST",
        tenant_url("Users/BatchCreate"),
        body={
            "users": [
                {"username": bc_u1, "password": "Test@1234", "temporary_password": True},
                {"username": bc_u2, "password": "Test@5678", "temporary_password": False},
            ]
        },
    )
    T.contains("batch-create 2 users: succeeded=2", '"succeeded":2', resp)
    T.contains("batch-create 2 users: failed=0", '"failed":0', resp)

    u1_id = find_first_user_id(admin_body("GET", tenant_url("Users?search=%s" % parse.quote(bc_u1))))
    u2_id = find_first_user_id(admin_body("GET", tenant_url("Users?search=%s" % parse.quote(bc_u2))))
    T.match("batch-create: user1 exists in Keycloak", r"^[0-9a-f-]{36}$", u1_id)
    T.match("batch-create: user2 exists in Keycloak", r"^[0-9a-f-]{36}$", u2_id)

    dup = admin_body(
        "POST",
        tenant_url("Users/BatchCreate"),
        body={
            "users": [
                {"username": "bc-new-%s" % int(time.time()), "password": "Test@1234"},
                {"username": bc_u1, "password": "Test@1234"},
            ]
        },
    )
    T.contains("batch-create duplicate: succeeded=1", '"succeeded":1', dup)
    T.contains("batch-create duplicate: failed=1", '"failed":1', dup)
    T.contains("batch-create duplicate: errors array present", '"errors":', dup)
    T.contains("batch-create duplicate: error index=1", '"index":1', dup)

    empty = admin_body("POST", tenant_url("Users/BatchCreate"), body={"users": []})
    T.contains("batch-create empty list: succeeded=0", '"succeeded":0', empty)
    T.contains("batch-create empty list: failed=0", '"failed":0', empty)

    for uid in (u1_id, u2_id):
        if uid:
            _ = admin_body("DELETE", tenant_url("Users/%s" % uid))
    bc_new_id = find_first_user_id(admin_body("GET", tenant_url("Users?search=bc-new-")))
    if bc_new_id:
        _ = admin_body("DELETE", tenant_url("Users/%s" % bc_new_id))


def all_users_acl_url():
    return tenant_url("ACLs?user=AccessManager/Tenants/%s/Groups/all-users" % REALM)


def all_users_object_permissions_url():
    return tenant_url("Groups/all-users/ObjectPermissions")


def set_all_users_object_permissions(permissions):
    return admin_status(
        "PUT",
        all_users_object_permissions_url(),
        body={"permissions": permissions},
    )


def assert_app_objects_listing():
    app_objs = admin_body("GET", tenant_url("AppObjects"))
    T.contains("GET AppObjects: has apps array", '"apps"', app_objs)
    T.contains("GET AppObjects: KnowledgeBase present", "KnowledgeBase", app_objs)
    T.contains("GET AppObjects: KnowledgeBases object", "KnowledgeBases", app_objs)
    T.contains("GET AppObjects: object_path has tenant", REALM, app_objs)
    T.contains("GET AppObjects: methods field present", '"methods"', app_objs)
    T.contains("GET AppObjects: actions field present", '"actions"', app_objs)
    T.contains("GET AppObjects: display_name check", "\u67e5\u770b", app_objs)
    T.not_contains("GET AppObjects: Mappings (sub-resource) excluded", '"Mappings"', app_objs)
    T.not_contains("GET AppObjects: Files (sub-resource) excluded", '"Files"', app_objs)


def assert_disabled_app_objects_filter():
    _ = psql_iam("UPDATE apps SET enabled=false WHERE app_name='KnowledgeBase';")
    disabled = admin_body("GET", tenant_url("AppObjects"))
    T.not_contains("GET AppObjects: disabled app excluded", "KnowledgeBase", disabled)
    _ = psql_iam("UPDATE apps SET enabled=true WHERE app_name='KnowledgeBase';")


def assert_group_object_permissions_write():
    put = set_all_users_object_permissions(
        [
            {
                "object_path": "KnowledgeBase/Tenants/%s/KnowledgeBases" % REALM,
                "role_path": "AccessManager/Tenants/System/Roles/Viewer",
            },
            {
                "object_path": "KnowledgeBase/Tenants/%s/Conversations" % REALM,
                "role_path": "AccessManager/Tenants/System/Roles/Contributor",
            },
        ]
    )
    T.match("PUT ObjectPermissions -> 200", r"^200$", put)
    acl_check = admin_body("GET", all_users_acl_url())
    T.contains("ObjectPermissions: KnowledgeBases Viewer written", "KnowledgeBases", acl_check)
    T.contains("ObjectPermissions: Conversations Contributor written", "Conversations", acl_check)
    T.contains("ObjectPermissions: Viewer role present", "Viewer", acl_check)
    T.contains("ObjectPermissions: Contributor role present", "Contributor", acl_check)


def assert_group_object_permissions_revoke():
    revoke = set_all_users_object_permissions(
        [{"object_path": "KnowledgeBase/Tenants/%s/Conversations" % REALM, "role_path": None}]
    )
    T.match("PUT ObjectPermissions revoke -> 200", r"^200$", revoke)
    acl_after = admin_body("GET", all_users_acl_url())
    T.not_contains(
        "ObjectPermissions: Conversations ACL removed",
        "KnowledgeBase/Tenants/%s/Conversations" % REALM,
        acl_after,
    )
    T.contains("ObjectPermissions: KnowledgeBases ACL still present", "KnowledgeBases", acl_after)


def assert_group_object_permissions_authz_and_cleanup():
    normal = NORMAL_TOKEN or ""
    noauth = token_status(normal, "PUT", all_users_object_permissions_url(), body={"permissions": []})
    T.match("PUT ObjectPermissions non-admin -> 403", r"^(403|401)$", noauth)
    _ = set_all_users_object_permissions(
        [{"object_path": "KnowledgeBase/Tenants/%s/KnowledgeBases" % REALM, "role_path": None}]
    )


def section_22_app_objects_group_permissions():
    T.section("Section 22: AppObjects + Group ObjectPermissions APIs")
    assert_app_objects_listing()
    assert_disabled_app_objects_filter()
    assert_group_object_permissions_write()
    assert_group_object_permissions_revoke()
    assert_group_object_permissions_authz_and_cleanup()


def request_keycloak_admin_token(master_pass):
    form = {"client_id": "admin-cli", "grant_type": "password", "username": "admin", "password": master_pass}
    token_body_text = http_body(
        "POST",
        keycloak_admin_url("realms/master/protocol/openid-connect/token"),
        form=form,
    )
    return json_get(token_body_text, "access_token")


def assert_realm_login_settings(headers):
    realm_resp = http_body("GET", keycloak_admin_url("admin/realms/%s" % REALM), headers=headers)
    T.contains("realm: rememberMe=false", '"rememberMe":false', realm_resp)
    T.contains("realm: verifyEmail=false", '"verifyEmail":false', realm_resp)
    T.contains("realm: editUsernameAllowed=true", '"editUsernameAllowed":true', realm_resp)
    T.contains("realm: resetPasswordAllowed=true", '"resetPasswordAllowed":true', realm_resp)
    T.contains("realm: loginWithEmailAllowed=false", '"loginWithEmailAllowed":false', realm_resp)
    T.contains("realm: loginTheme=password-reset-confirm", '"loginTheme":"password-reset-confirm"', realm_resp)


def assert_keycloak_user_profile(headers):
    profile = http_body("GET", keycloak_admin_url("admin/realms/%s/users/profile" % REALM), headers=headers)
    T.contains("user profile: username attribute present", '"username"', profile)
    T.contains("user profile: email attribute present", '"email"', profile)
    T.contains("user profile: nickname attribute present", '"nickname"', profile)


def assert_aidp_web_protocol_mappers(headers, web_client_id):
    mappers = http_body(
        "GET",
        keycloak_admin_url("admin/realms/%s/clients/%s/protocol-mappers/models" % (REALM, web_client_id)),
        headers=headers,
    )
    T.contains("aidp-web has structured groups mapper", '"groups-structured-mapper"', mappers)


def assert_aidp_web_client(headers):
    clients = http_body(
        "GET",
        keycloak_admin_url("admin/realms/%s/clients?clientId=aidp-web" % REALM),
        headers=headers,
    )
    T.contains("aidp-web public client exists", '"clientId":"aidp-web"', clients)
    T.contains("aidp-web publicClient=true", '"publicClient":true', clients)
    clients_json = json_loads(clients, [])
    if clients_json:
        web_client = clients_json[0]
        web_client_id = web_client.get("id", "")
        T.equal("aidp-web redirectUris login callback", ["/auth/login/callback"], web_client.get("redirectUris", []))
        T.equal("aidp-web webOrigins derives from redirect URIs", ["+"], web_client.get("webOrigins", []))
        T.equal("aidp-web frontchannel logout disabled", False, web_client.get("frontchannelLogout", False))
        web_attrs = web_client.get("attributes", {})
        T.equal("aidp-web post logout redirectUris", "/auth/login", web_attrs.get("post.logout.redirect.uris"))
        T.equal("aidp-web backchannel logout url empty", "", web_attrs.get("backchannel.logout.url", ""))
        T.equal(
            "aidp-web backchannel logout session disabled",
            "false",
            web_attrs.get("backchannel.logout.session.required"),
        )
        T.equal(
            "aidp-web backchannel logout offline revoke disabled",
            "false",
            web_attrs.get("backchannel.logout.revoke.offline.tokens"),
        )
        T.equal("aidp-web logout confirmation disabled", "false", web_attrs.get("logout.confirmation.enabled"))
        assert_aidp_web_protocol_mappers(headers, web_client_id)
    else:
        T.skip("Section 23 - aidp-web client not found for mapper check")


def section_23_realm_login_settings():
    global kc_pf_proc
    T.section("Section 23: Realm login settings + user profile (init-keycloak verification)")
    _ = refresh_admin_token()
    master_pass = get_secret_value(KEYCLOAK_NS, "keycloak-credentials", "admin-password")
    if not master_pass:
        T.skip("Section 23 - keycloak-credentials secret not found")
        return

    kc_pf_proc = start_port_forward(KEYCLOAK_NS, "svc/keycloak", "%s:8080" % KEYCLOAK_ADMIN_PORT)
    time.sleep(2)
    kc_admin_token = request_keycloak_admin_token(master_pass)
    if not kc_admin_token:
        T.skip("Section 23 - could not get Keycloak admin token")
        return

    headers = {"Authorization": "Bearer %s" % kc_admin_token}
    assert_realm_login_settings(headers)
    assert_keycloak_user_profile(headers)
    assert_aidp_web_client(headers)


def main():
    global CS
    atexit.register(cleanup)
    setup_gateway_port_forward()
    setup_gateway_manager_port_forward()
    setup_iam_log_port_forward()
    detect_optional_routes()
    section_1_pod_health()
    section_2_public_routes()
    section_2b_gateway_manager_log_collect()
    section_2c_gateway_certificate_api()
    section_2c_iam_log_collect()

    CS = get_client_secret()
    setup_manifests()

    section_3_no_token()
    section_4_admin_token()
    section_5_acl_schema()
    section_6_manifest_api()
    section_7_acl_management()
    section_8_acl_cascade()
    section_9_acl_delete_exact()
    section_10_acl_inheritance()
    section_11_role_matrix()
    section_12_allowed_ids()
    section_13_tenant_isolation()
    section_14_opa_authz()
    section_15_access_manager_routes()
    section_16_identity_routes()
    section_16b_group_detail()
    section_17_api_keys()
    section_18_app_disabled()
    section_19_dataagent_manifest()
    section_20_password_policy()
    section_21_batch_user_creation()
    section_22_app_objects_group_permissions()
    section_23_realm_login_settings()
    T.summary()
    return 1 if T.failed else 0


if __name__ == "__main__":
    sys.exit(main())
