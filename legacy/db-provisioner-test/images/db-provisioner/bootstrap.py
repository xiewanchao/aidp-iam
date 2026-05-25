"""
bootstrap: create the DB super-user Secret on first install.

Idempotent — if the Secret already exists it is preserved so helm upgrade
never rotates the super password accidentally.

Env:
  SUPER_SECRET_NAMESPACE, SUPER_SECRET_NAME, SUPER_USER
"""
from __future__ import annotations

import os
import secrets
import string
import sys

from kubernetes import client as k8s
from kubernetes import config as kconf
from kubernetes.client.exceptions import ApiException


SPECIAL_CHARS = "!@#$%^&*"


def gen_password(length: int = 24) -> str:
    """Meets openGauss password policy (all 4 character classes present)."""
    alphabet = string.ascii_letters + string.digits + SPECIAL_CHARS
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.isupper() for c in pwd)
            and any(c.islower() for c in pwd)
            and any(c.isdigit() for c in pwd)
            and any(c in SPECIAL_CHARS for c in pwd)
        ):
            return pwd


def main() -> None:
    ns = os.environ["SUPER_SECRET_NAMESPACE"]
    name = os.environ["SUPER_SECRET_NAME"]
    user = os.environ.get("SUPER_USER", "gaussdb")

    kconf.load_incluster_config()
    v1 = k8s.CoreV1Api()

    try:
        v1.read_namespaced_secret(name, ns)
        print(f"[bootstrap] {ns}/{name} already exists, keep", flush=True)
        return
    except ApiException as e:
        if e.status != 404:
            raise

    pwd = gen_password()
    body = k8s.V1Secret(
        metadata=k8s.V1ObjectMeta(name=name),
        string_data={"username": user, "password": pwd},
    )
    v1.create_namespaced_secret(ns, body)
    print(f"[bootstrap] {ns}/{name} created (user={user}, pwd_len={len(pwd)})", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        sys.exit(1)
