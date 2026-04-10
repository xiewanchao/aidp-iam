#!/usr/bin/env bash
# IAM v2.0 Performance Benchmark
# Usage: PORT=9091 bash scripts/bench.sh
set -euo pipefail

PORT="${PORT:-9091}"
BASE="http://localhost:$PORT"
CONCURRENCY="${CONCURRENCY:-100}"
REQUESTS="${REQUESTS:-1000}"

log() { echo -e "\033[0;32m[BENCH]\033[0m $*"; }

# Get fresh JWT token
log "Getting JWT token..."
TOKEN=$(curl -s "$BASE/realms/data-agent/protocol/openid-connect/token" \
  -d "grant_type=password&client_id=data-agent&username=tenant-admin&password=TenantAdmin@123" \
  | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
[ -z "$TOKEN" ] && { echo "Failed to get token"; exit 1; }
S=$(curl -so /dev/null -w "%{http_code}" "$BASE/api/v1/tenants" -H "Authorization: Bearer $TOKEN")
[ "$S" != "200" ] && { echo "Token verify failed: $S"; exit 1; }
log "Token OK (length=${#TOKEN})"

RESULTS_FILE=$(mktemp)

run_bench() {
  local label="$1" n="$2" c="$3"
  shift 3
  # remaining args are the curl command pieces
  local curl_args=("$@")
  local tmpdir=$(mktemp -d)

  log "Running: $label ($n reqs, $c conc)..."
  local start_time=$(date +%s%N)

  for w in $(seq 1 "$c"); do
    (
      local rpw=$((n / c))
      for i in $(seq 1 "$rpw"); do
        local t0=$(date +%s%N)
        local code
        code=$(curl -so /dev/null -w "%{http_code}" --max-time 10 "${curl_args[@]}" 2>/dev/null) || code=000
        local t1=$(date +%s%N)
        echo "$code $(( (t1 - t0) / 1000000 ))" >> "$tmpdir/w${w}.log"
      done
    ) &
  done
  wait

  local end_time=$(date +%s%N)
  local elapsed=$(( (end_time - start_time) / 1000000 ))

  cat "$tmpdir"/*.log > "$tmpdir/all.log" 2>/dev/null || { echo "  No results"; rm -rf "$tmpdir"; return; }
  local total=$(wc -l < "$tmpdir/all.log")
  local ok=$(grep -c "^200 " "$tmpdir/all.log" || true)
  local fail=$((total - ok))
  local rps
  if [ "$elapsed" -gt 0 ]; then rps=$(( total * 1000 / elapsed )); else rps=0; fi

  local latencies
  latencies=$(awk '{print $2}' "$tmpdir/all.log" | sort -n)
  local avg=$(awk '{s+=$2}END{printf "%.0f", s/NR}' "$tmpdir/all.log")
  local min_l=$(echo "$latencies" | head -1)
  local max_l=$(echo "$latencies" | tail -1)
  local p50=$(echo "$latencies" | awk "NR==int($total*0.5){print}")
  local p90=$(echo "$latencies" | awk "NR==int($total*0.9){print}")
  local p95=$(echo "$latencies" | awk "NR==int($total*0.95){print}")
  local p99=$(echo "$latencies" | awk "NR==int($total*0.99){print}")

  printf "  %-46s %5s %5s %4s %5s %6s %6s %6s %6s %6s\n" \
    "$label" "$total" "$ok" "$fail" "$rps" "${avg}" "${p50}" "${p90}" "${p95}" "${p99}"
  echo "$label|$total|$ok|$fail|$rps|$avg|$p50|$p90|$p95|$p99|$min_l|$max_l" >> "$RESULTS_FILE"

  rm -rf "$tmpdir"
}

echo ""
echo "================================================================"
echo "  IAM v2.0 Performance Benchmark"
echo "  Concurrency=$CONCURRENCY  Requests/scenario=$REQUESTS"
echo "  Data: 6 apps, 300K resource_acl, 100 tenants"
echo "================================================================"
echo ""

log "Baseline:"
docker stats da-cluster-v2-control-plane --no-stream --format "  CPU={{.CPUPerc}} MEM={{.MemUsage}}" 2>/dev/null || true
echo ""

printf "  %-46s %5s %5s %4s %5s %6s %6s %6s %6s %6s\n" \
  "Scenario" "Total" "OK" "Fail" "RPS" "Avg" "P50" "P90" "P95" "P99"
printf "  %-46s %5s %5s %4s %5s %6s %6s %6s %6s %6s\n" \
  "──────────────────────────────────────────────" "─────" "─────" "────" "─────" "──────" "──────" "──────" "──────" "──────"

# S1: OIDC Discovery (no auth, baseline)
run_bench "S1: OIDC Discovery (no auth)" \
  "$REQUESTS" "$CONCURRENCY" \
  "$BASE/realms/data-agent/.well-known/openid-configuration"

# S2: Keycloak Token (POST, no gateway auth)
run_bench "S2: Token Endpoint (Keycloak direct)" \
  500 50 \
  -X POST "$BASE/realms/data-agent/protocol/openid-connect/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-raw "grant_type=password&client_id=data-agent&username=tenant-admin&password=TenantAdmin@123"

# S3: Management API (ext_authz only)
run_bench "S3: Mgmt API (JWT + ext_authz)" \
  "$REQUESTS" "$CONCURRENCY" \
  "$BASE/api/v1/tenants" \
  -H "Authorization: Bearer $TOKEN"

# S4: Business route (ext_authz + ext_proc)
run_bench "S4: Business Route (ext_authz + ext_proc)" \
  "$REQUESTS" "$CONCURRENCY" \
  "$BASE/anything" \
  -H "Authorization: Bearer $TOKEN"

# S5: Keycloak static (no auth, no ext_authz)
run_bench "S5: Keycloak Realms (static, no auth)" \
  "$REQUESTS" "$CONCURRENCY" \
  "$BASE/realms/data-agent"

echo ""
log "Peak resource:"
docker stats da-cluster-v2-control-plane --no-stream --format "  CPU={{.CPUPerc}} MEM={{.MemUsage}}" 2>/dev/null || true

echo ""
log "Done."
rm -f "$RESULTS_FILE"
