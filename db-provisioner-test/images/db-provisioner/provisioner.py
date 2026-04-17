"""
db-provisioner: idempotently create per-app databases, users, and K8s Secrets.

Reads /config/databases.yaml. For each entry:
  1. If a Secret with a password already exists in the target namespace,
     reuse that password (helm upgrade idempotency).
  2. Otherwise generate a fresh strong password.
  3. CREATE USER (or ALTER USER ... PASSWORD) so the DB matches the Secret.
  4. CREATE DATABASE if missing.
  5. GRANT ALL ON SCHEMA public TO the owner (needed on PG15+ / openGauss).
  6. Create/patch the Secret with host/port/database/username/password.

Env:
  DB_HOST, DB_PORT, DB_SUPER_USER, DB_SUPER_PASSWORD
"""
from __future__ import annotations

import base64
import os
import secrets
import string
import sys
import time
from typing import Optional

import psycopg2
import yaml
from kubernetes import client as k8s
from kubernetes import config as kconf
from kubernetes.client.exceptions import ApiException
from psycopg2 import sql


SPECIAL_CHARS = "!@#$%^&*"


def log(msg: str) -> None:
    print(f"[prov] {msg}", flush=True)


def gen_password(length: int = 24) -> str:
    """Strong password meeting openGauss complexity:
    length >= 8 and at least 3 of 4 classes (upper/lower/digit/special)."""
    alphabet = string.ascii_letters + string.digits + SPECIAL_CHARS
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        classes = (
            any(c.isupper() for c in pwd)
            + any(c.islower() for c in pwd)
            + any(c.isdigit() for c in pwd)
            + any(c in SPECIAL_CHARS for c in pwd)
        )
        if classes >= 3:
            return pwd


def connect(host: str, port: int, user: str, password: str, dbname: str = "postgres"):
    conn = psycopg2.connect(
        host=host, port=port, user=user, password=password,
        dbname=dbname, connect_timeout=5,
    )
    conn.autocommit = True
    return conn


def wait_db(host: str, port: int, user: str, password: str, attempts: int = 60):
    last_err: Optional[Exception] = None
    for i in range(1, attempts + 1):
        try:
            conn = connect(host, port, user, password)
            log(f"connected to {host}:{port} as {user}")
            return conn
        except psycopg2.OperationalError as e:
            last_err = e
            log(f"db not ready ({i}/{attempts}): {e}".strip())
            time.sleep(3)
    raise RuntimeError(f"database never became reachable: {last_err}")


def user_exists(conn, username: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (username,))
        return cur.fetchone() is not None


def database_exists(conn, dbname: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
        return cur.fetchone() is not None


def ensure_user(conn, username: str, password: str, force_alter: bool) -> None:
    if not user_exists(conn, username):
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("CREATE USER {} WITH PASSWORD %s").format(sql.Identifier(username)),
                (password,),
            )
        log(f"CREATE USER {username}")
        return
    if force_alter:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("ALTER USER {} WITH PASSWORD %s").format(sql.Identifier(username)),
                (password,),
            )
        log(f"ALTER USER {username} (password synced to Secret)")
    else:
        log(f"user {username} already exists, password unchanged")


def ensure_database(conn, dbname: str, owner: str) -> None:
    if database_exists(conn, dbname):
        log(f"database {dbname} already exists, skip")
        return
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(dbname), sql.Identifier(owner)
            )
        )
    log(f"CREATE DATABASE {dbname} OWNER {owner}")


def grant_schema_public(host, port, super_user, super_password, dbname: str, owner: str) -> None:
    # Must connect to the target DB to GRANT on its schema.
    conn = connect(host, port, super_user, super_password, dbname=dbname)
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("GRANT ALL ON SCHEMA public TO {}").format(sql.Identifier(owner))
            )
        log(f"GRANT ALL ON SCHEMA public TO {owner} IN {dbname}")
    finally:
        conn.close()


def read_secret_password(v1: k8s.CoreV1Api, namespace: str, name: str) -> Optional[str]:
    try:
        s = v1.read_namespaced_secret(name, namespace)
    except ApiException as e:
        if e.status == 404:
            return None
        raise
    data = s.data or {}
    enc = data.get("password")
    if not enc:
        return None
    return base64.b64decode(enc).decode()


def upsert_secret(
    v1: k8s.CoreV1Api,
    namespace: str,
    name: str,
    *,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
) -> None:
    body = k8s.V1Secret(
        metadata=k8s.V1ObjectMeta(name=name),
        string_data={
            "host": host,
            "port": str(port),
            "database": database,
            "username": username,
            "password": password,
        },
    )
    try:
        v1.read_namespaced_secret(name, namespace)
        v1.patch_namespaced_secret(name, namespace, body)
        log(f"Secret {namespace}/{name} patched")
    except ApiException as e:
        if e.status != 404:
            raise
        v1.create_namespaced_secret(namespace, body)
        log(f"Secret {namespace}/{name} created")


def main() -> None:
    host = os.environ["DB_HOST"]
    port = int(os.environ.get("DB_PORT", "5432"))
    super_user = os.environ["DB_SUPER_USER"]
    super_password = os.environ["DB_SUPER_PASSWORD"]

    with open("/config/databases.yaml", encoding="utf-8") as f:
        spec = yaml.safe_load(f) or {}

    kconf.load_incluster_config()
    v1 = k8s.CoreV1Api()

    conn = wait_db(host, port, super_user, super_password)

    try:
        for entry in spec.get("databases", []):
            name = entry["name"]
            user = entry.get("user", name)
            ns = entry["secretNamespace"]
            secret_name = entry["secretName"]

            log(f"---- {name} / user={user} → {ns}/{secret_name} ----")

            existing_pwd = read_secret_password(v1, ns, secret_name)
            pwd = existing_pwd or gen_password()
            # If the Secret is missing we must (re)align the DB user to the fresh password.
            force_alter = existing_pwd is None

            ensure_user(conn, user, pwd, force_alter=force_alter)
            ensure_database(conn, name, owner=user)
            grant_schema_public(host, port, super_user, super_password, dbname=name, owner=user)
            upsert_secret(
                v1, ns, secret_name,
                host=host, port=port, database=name,
                username=user, password=pwd,
            )
    finally:
        conn.close()

    log("done")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        sys.exit(1)
