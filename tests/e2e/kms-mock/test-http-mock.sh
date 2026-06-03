#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:18082}"
TOKEN="${TOKEN:-dev-token}"

echo '$ curl -s -X POST "$BASE_URL/framework/v1/crypto/aidp/test/password/actions/encrypt/internal" ...'
encrypt_response="$(curl -s -X POST \
  "$BASE_URL/framework/v1/crypto/aidp/test/password/actions/encrypt/internal" \
  -H "X-Auth-Token: ${TOKEN}" \
  -H "UserName: system" \
  -H "Content-Type: application/json" \
  -d '{"plain":"Admin@123"}')"
echo "$encrypt_response"

echo '$ curl -s -X POST "$BASE_URL/framework/v1/crypto/aidp/test/password/actions/decrypt/internal" ...'
decrypt_response="$(curl -s -X POST \
  "$BASE_URL/framework/v1/crypto/aidp/test/password/actions/decrypt/internal" \
  -H "X-Auth-Token: ${TOKEN}" \
  -H "UserName: system" \
  -H "Content-Type: application/json" \
  -d '{}')"
echo "$decrypt_response"

python3 - "$decrypt_response" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
assert payload["code"] == "0", payload
assert payload["data"]["plain"] == "Admin@123", payload
print("PASS HTTP mock OMS KMS")
PY
