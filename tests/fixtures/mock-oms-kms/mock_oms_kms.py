#!/usr/bin/env python3
"""Tiny OMS KMS mock server for local and Kind integration tests."""

from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote


STORE_PATH = Path(os.getenv("MOCK_KMS_STORE_PATH", "/tmp/mock-oms-kms/store.json"))
REQUIRED_TOKEN = os.getenv("MOCK_KMS_REQUIRED_TOKEN", "dev-token")
HOST = os.getenv("MOCK_KMS_HOST", "0.0.0.0")
PORT = int(os.getenv("MOCK_KMS_PORT", "18082"))

ENCRYPT_PATH = re.compile(
    r"^/framework/v1/crypto/(?P<key_id>.+)/actions/encrypt/internal$"
)
DECRYPT_PATH = re.compile(
    r"^/framework/v1/crypto/(?P<key_id>.+)/actions/decrypt/internal$"
)


def read_store() -> dict[str, str]:
    if not STORE_PATH.exists():
        return {}
    return json.loads(STORE_PATH.read_text(encoding="utf-8"))


def write_store(data: dict[str, str]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "MockOmsKms/1.0"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self.write_json(200, {"code": "0", "msg": "ok", "data": True})
            return
        self.write_json(404, {"code": "404", "msg": "Not Found", "data": None})

    def do_POST(self) -> None:  # noqa: N802
        if REQUIRED_TOKEN and self.headers.get("X-Auth-Token") != REQUIRED_TOKEN:
            self.write_json(401, {"code": "401", "msg": "Unauthorized", "data": None})
            return

        encrypt_match = ENCRYPT_PATH.match(self.path)
        if encrypt_match:
            self.handle_encrypt(unquote(encrypt_match.group("key_id")))
            return

        decrypt_match = DECRYPT_PATH.match(self.path)
        if decrypt_match:
            self.handle_decrypt(unquote(decrypt_match.group("key_id")))
            return

        self.write_json(404, {"code": "404", "msg": "Not Found", "data": None})

    def handle_encrypt(self, key_id: str) -> None:
        body = self.read_json_body()
        plain = body.get("plain")
        if plain is None:
            self.write_json(400, {"code": "400", "msg": "plain is required", "data": None})
            return
        data = read_store()
        data[key_id] = str(plain)
        write_store(data)
        self.write_json(200, {"code": "0", "msg": "Success", "data": {"keyId": key_id}})

    def handle_decrypt(self, key_id: str) -> None:
        data = read_store()
        if key_id not in data:
            self.write_json(404, {"code": "404", "msg": "keyId not found", "data": None})
            return
        self.write_json(200, {"code": "0", "msg": "Success", "data": {"plain": data[key_id]}})

    def read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def write_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"mock OMS KMS listening on {HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
