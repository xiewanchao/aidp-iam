import base64
import hashlib
import os
import re
from datetime import datetime, timezone
from typing import Any

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from cryptography.hazmat.primitives.serialization import pkcs12
from fastapi import FastAPI, File, Form, HTTPException, UploadFile


app = FastAPI(title="AIDP Gateway Certificate Manager", version="1.0.0")

ALIAS_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,52}[a-z0-9])?$")
PEM_CERT_RE = re.compile(
    rb"-----BEGIN CERTIFICATE-----\s+.*?\s+-----END CERTIFICATE-----",
    re.DOTALL,
)

SECRET_NAMESPACE = os.getenv("GATEWAY_CERT_NAMESPACE", "aidp-gateway")
GATEWAY_NAMESPACE = os.getenv("GATEWAY_NAMESPACE", "aidp-gateway")
GATEWAY_NAME = os.getenv("GATEWAY_NAME", "eg")
SECRET_PREFIX = os.getenv("GATEWAY_CERT_SECRET_PREFIX", "gw-cert-")

SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
SA_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
KUBE_HOST = os.getenv("KUBERNETES_SERVICE_HOST")
KUBE_PORT = os.getenv("KUBERNETES_SERVICE_PORT", "443")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


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
                "app.kubernetes.io/name": "gateway-cert-manager",
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


def k8s_request(method: str, path: str, json: dict[str, Any] | None = None, content_type: str | None = None) -> requests.Response:
    if not KUBE_HOST:
        raise HTTPException(status_code=500, detail="KUBERNETES_SERVICE_HOST is not set")
    with open(SA_TOKEN_PATH, "r", encoding="utf-8") as token_file:
        token = token_file.read().strip()
    headers = {"Authorization": f"Bearer {token}"}
    if content_type:
        headers["Content-Type"] = content_type
    url = f"https://{KUBE_HOST}:{KUBE_PORT}{path}"
    return requests.request(method, url, headers=headers, json=json, verify=SA_CA_PATH, timeout=10)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
