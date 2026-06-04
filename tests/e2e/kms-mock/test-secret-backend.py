#!/usr/bin/env python3
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
sys.path.insert(0, os.path.join(ROOT, "apps/keycloak-init"))

from secret_backend import MockKmsClient, SecretStore  # noqa: E402


def main():
    with tempfile.TemporaryDirectory() as tmp:
        store_path = os.path.join(tmp, "kms.json")
        os.environ["SECRET_BACKEND"] = "mock"
        os.environ["MOCK_KMS_STORE_PATH"] = store_path
        client = MockKmsClient()

        key_id = client.encrypt("Admin@123", "aidp/test/admin-password")
        assert key_id == "aidp/test/admin-password"
        assert client.decrypt(key_id) == "Admin@123"

        os.environ["AIDP_ADMIN_PASSWORD_KEY_ID"] = key_id
        resolved = SecretStore().resolve(
            env_name="AIDP_ADMIN_PASSWORD",
            key_id_env_name="AIDP_ADMIN_PASSWORD_KEY_ID",
        )
        assert resolved == "Admin@123"

    print("PASS mock secret backend")


if __name__ == "__main__":
    main()

