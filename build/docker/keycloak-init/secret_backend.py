#!/usr/bin/env python3
"""Secret backend adapters for keycloak-init.

Production uses KMC machine-token retrieval plus OMS KMS encrypt/decrypt.
Development can run without KMC by using static-token and mock KMS modes.
Default `SECRET_BACKEND=plain` keeps existing deployments compatible until the
secure path is explicitly enabled.
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any

import requests

try:
    from kubernetes import client as k8s_client
    from kubernetes import config as k8s_config
except Exception:  # pragma: no cover
    k8s_client = None
    k8s_config = None


TOKEN_REFRESH_INTERVAL = int(os.getenv("MACHINE_TOKEN_CACHE_SECONDS", "3600"))


def bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _write_data_items(directory: str, data: dict[str, str], binary: bool) -> None:
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    for key, value in data.items():
        target = path / key
        if binary:
            target.write_bytes(base64.b64decode(value))
        else:
            target.write_text(str(value), encoding="utf-8")


class MachineTokenProvider:
    def get_token(self) -> str:
        raise NotImplementedError


class StaticMachineTokenProvider(MachineTokenProvider):
    def __init__(self) -> None:
        self.token = os.getenv("KMS_AUTH_TOKEN") or os.getenv("MACHINE_TOKEN") or ""

    def get_token(self) -> str:
        if not self.token:
            raise RuntimeError("KMS_AUTH_TOKEN or MACHINE_TOKEN must be set")
        return self.token


class MockKmcMachineTokenProvider(MachineTokenProvider):
    """Development provider that exercises platform.conf parsing without KMC."""

    def __init__(self) -> None:
        self.token = os.getenv("MOCK_KMC_TOKEN", "dev-token")
        self.platform_conf_dir = os.getenv(
            "PRIV_CONFIG_PATH", "/opt/huawei/fce/runtime/security/priv/"
        )

    def get_token(self) -> str:
        conf_file = Path(self.platform_conf_dir) / "platform.conf"
        if conf_file.exists():
            for line in conf_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("machine_token="):
                    break
        return self.token


class KmcMachineTokenProvider(MachineTokenProvider):
    def __init__(self) -> None:
        self.namespace = os.getenv("MACHINE_TOKEN_NAMESPACE", "agentinfra")
        self.secret_name = os.getenv("MACHINE_TOKEN_SECRET_NAME", "cube-paas-tomcat")
        self.configmap_name = os.getenv(
            "MACHINE_TOKEN_CONFIGMAP_NAME", "cube-security-priv"
        )
        self.tomcat_secret_path = os.getenv(
            "TOMCAT_SECRET_PATH", "/opt/huawei/fce/paas/tomcat/"
        )
        self.priv_config_path = os.getenv(
            "PRIV_CONFIG_PATH", "/opt/huawei/fce/runtime/security/priv/"
        )
        self._cached_token = ""
        self._cached_at = 0.0

    def get_token(self) -> str:
        now = time.time()
        if self._cached_token and now - self._cached_at < TOKEN_REFRESH_INTERVAL:
            return self._cached_token
        self._load_platform_material()
        token = self._decrypt_token_via_kmc()
        if not token:
            raise RuntimeError("KMC returned empty machine token")
        self._cached_token = token
        self._cached_at = now
        return token

    def _load_platform_material(self) -> None:
        if k8s_client is None or k8s_config is None:
            raise RuntimeError("kubernetes Python client is required for KMC mode")
        k8s_config.load_incluster_config()
        api = k8s_client.CoreV1Api()

        secret = api.read_namespaced_secret(self.secret_name, self.namespace)
        if secret and secret.data:
            _write_data_items(self.tomcat_secret_path, secret.data, binary=True)

        cm = api.read_namespaced_config_map(self.configmap_name, self.namespace)
        if cm and cm.data:
            _write_data_items(self.priv_config_path, cm.data, binary=False)

    def _read_platform_config(self) -> str:
        conf_file = Path(self.priv_config_path) / "platform.conf"
        if not conf_file.exists():
            return ""
        for line in conf_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("machine_token="):
                return line.split("=", 1)[1].strip()
        return ""

    def _decrypt_token_via_kmc(self) -> str:
        encrypted = self._read_platform_config()
        if encrypted:
            os.environ["KMC_PYTHON_ENCRYPT_DATA"] = encrypted
        os.environ["KMC_DATA_USER"] = os.getenv("KMC_DATA_USER", "tomcat")
        try:
            import kmc.kmc as kmc_module  # type: ignore
        except ImportError as exc:
            raise RuntimeError("kmc Python module is required for KMC mode") from exc
        return kmc_module.API().decrypt(0)


class KmsClient:
    def encrypt(self, plain: str, key_id: str) -> str:
        raise NotImplementedError

    def decrypt(self, key_id: str) -> str:
        raise NotImplementedError


class MockKmsClient(KmsClient):
    def __init__(self) -> None:
        self.store_path = Path(os.getenv("MOCK_KMS_STORE_PATH", "/tmp/aidp-kms-mock.json"))

    def encrypt(self, plain: str, key_id: str) -> str:
        data = self._read_store()
        data[key_id] = plain
        self._write_store(data)
        return key_id

    def decrypt(self, key_id: str) -> str:
        data = self._read_store()
        if key_id not in data:
            raise RuntimeError(f"mock KMS keyId not found: {key_id}")
        return str(data[key_id])

    def _read_store(self) -> dict[str, Any]:
        if not self.store_path.exists():
            return {}
        return json.loads(self.store_path.read_text(encoding="utf-8"))

    def _write_store(self, data: dict[str, Any]) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.store_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


class OmsKmsClient(KmsClient):
    def __init__(self, token_provider: MachineTokenProvider) -> None:
        self.token_provider = token_provider
        self.base_url = os.getenv(
            "KMS_BASE_URL", "https://omsservice.agentinfra.svc.cluster.local:18082"
        ).rstrip("/")
        self.api_prefix = os.getenv("KMS_API_PREFIX", "/framework/v1").strip().rstrip("/")
        self.username = os.getenv("KMS_USER_NAME", "system")
        self.timeout = int(os.getenv("KMS_TIMEOUT_SECONDS", "30"))
        self.verify_tls = bool_env("KMS_VERIFY_TLS", False)

    def encrypt(self, plain: str, key_id: str) -> str:
        response = self._post(
            f"/crypto/{key_id}/actions/encrypt/internal",
            {"plain": plain},
        )
        result = response.get("data", {}).get("keyId")
        if response.get("code") != "0" or not result:
            raise RuntimeError(f"KMS encrypt failed for keyId={key_id}: {response}")
        return str(result)

    def decrypt(self, key_id: str) -> str:
        response = self._post(f"/crypto/{key_id}/actions/decrypt/internal", {})
        result = response.get("data", {}).get("plain")
        if response.get("code") != "0" or result is None:
            raise RuntimeError(f"KMS decrypt failed for keyId={key_id}: {response}")
        return str(result)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "X-Auth-Token": self.token_provider.get_token(),
            "Content-Type": "application/json",
            "UserName": self.username,
        }
        api_path = f"{self.api_prefix}{path}" if self.api_prefix else path
        response = requests.post(
            f"{self.base_url}{api_path}",
            headers=headers,
            json=payload,
            verify=self.verify_tls,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()


class SecretStore:
    def __init__(self) -> None:
        self.backend = os.getenv("SECRET_BACKEND", "plain").strip().lower()
        self.kms_client = self._build_kms_client()

    def resolve(self, *, env_name: str, key_id_env_name: str, default: str = "") -> str:
        key_id = os.getenv(key_id_env_name, "").strip()
        if key_id:
            return self.kms_client.decrypt(key_id)
        value = os.getenv(env_name)
        if value is not None:
            return value
        return default

    def protect(
        self,
        *,
        plain: str,
        plain_key: str,
        key_id_key: str,
        key_id: str,
    ) -> dict[str, str]:
        if self.backend == "plain":
            return {plain_key: plain}
        protected_key_id = self.kms_client.encrypt(plain, key_id)
        return {key_id_key: protected_key_id}

    def _build_kms_client(self) -> KmsClient:
        if self.backend in {"plain", "mock"}:
            return MockKmsClient()
        if self.backend == "oms-kms":
            return OmsKmsClient(_build_machine_token_provider())
        raise RuntimeError("SECRET_BACKEND must be one of: plain, mock, oms-kms")


def _build_machine_token_provider() -> MachineTokenProvider:
    provider = os.getenv("MACHINE_TOKEN_PROVIDER", "static").strip().lower()
    if provider == "static":
        return StaticMachineTokenProvider()
    if provider == "mock-kmc":
        return MockKmcMachineTokenProvider()
    if provider == "kmc":
        return KmcMachineTokenProvider()
    raise RuntimeError("MACHINE_TOKEN_PROVIDER must be one of: static, mock-kmc, kmc")


_STORE: SecretStore | None = None


def secret_store() -> SecretStore:
    global _STORE
    if _STORE is None:
        _STORE = SecretStore()
    return _STORE
