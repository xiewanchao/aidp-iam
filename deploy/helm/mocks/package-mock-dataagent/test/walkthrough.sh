#!/usr/bin/env bash
# walkthrough.sh — interactive DataAgent API walkthrough
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/lib/common.sh"

TID="aidp"
DB_BASE="$GATEWAY/DataAgent/Tenants/$TID/Databases"
SS_BASE="$GATEWAY/DataAgent/Tenants/$TID/Sessions"

banner "DataAgent API Walkthrough"
init_tokens

echo "=== 1. Create a Database ==="
NH "$T_ADMIN" -X PUT "$DB_BASE/my-db" \
  -H "Content-Type: application/json" \
  -d '{"description":"my database","engine":"postgres"}' | python -m json.tool

echo ""
echo "=== 2. List Databases ==="
NH "$T_ADMIN" "$DB_BASE" | python -m json.tool

echo ""
echo "=== 3. Test database connectivity (type-level action) ==="
NH "$T_ADMIN" -X POST "$DB_BASE/Test" \
  -H "Content-Type: application/json" \
  -d '{"host":"db.example.com","port":5432}' | python -m json.tool

echo ""
echo "=== 4. Build database (instance-level action) ==="
NH "$T_ADMIN" -X POST "$DB_BASE/my-db/Build" \
  -H "Content-Type: application/json" -d '{}' | python -m json.tool

echo ""
echo "=== 5. Create a Session ==="
NH "$T_ADMIN" -X PUT "$SS_BASE/my-session" \
  -H "Content-Type: application/json" \
  -d '{"query":"how many users are there?"}' | python -m json.tool

echo ""
echo "=== 6. Get Session Turns ==="
NH "$T_ADMIN" "$SS_BASE/my-session/Turns" | python -m json.tool

echo ""
echo "=== 7. Create SpecialKL ==="
NH "$T_ADMIN" -X PUT "$DB_BASE/SpecialKL/synonym-001" \
  -H "Content-Type: application/json" \
  -d '{"content":"用户 = user = 客户"}' | python -m json.tool

echo ""
echo "=== Cleanup ==="
NH "$T_ADMIN" -X DELETE "$DB_BASE/my-db" -o /dev/null -w "DELETE Database: %{http_code}\n"
NH "$T_ADMIN" -X DELETE "$SS_BASE/my-session" -o /dev/null -w "DELETE Session: %{http_code}\n"
NH "$T_ADMIN" -X DELETE "$DB_BASE/SpecialKL/synonym-001" -o /dev/null -w "DELETE SpecialKL: %{http_code}\n"
