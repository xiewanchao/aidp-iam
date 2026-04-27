#!/usr/bin/env bash
# ============================================================================
# run-all.sh — orchestrates all KB per-category tests in apioption.md order.
#
# Each category script is self-contained: sources lib/common.sh, calls
# init_tokens, runs its own assertions, exits non-zero on FAIL.
#
# This runner:
#   - prints a banner per category
#   - aggregates PASS/FAIL counts across all categories
#   - exits non-zero if any category failed
#
# Categories (per diagrams/api-specs/knowledgebase/apioption.md):
#   00-prereq          — pod ready, public OIDC, IAM app registered, no-token rejects
#   01-knowledge-bases — KB / mappings / files lifecycle
#   02-models-config   — model configuration CRUD (kb-admins only)
#   03-prompts         — prompt CRUD (read all-users, edit kb-admins)
#   04-jargons         — jargon library + entries (kb-admins only)
#   05-conversations   — chat lifecycle (all-users)
#   06-retrieval       — fusion search (all-users)
#   07-kb-jargon-bind  — KB-jargon binding (kb-admins)
# ============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GREEN='\033[0;32m'; RED='\033[0;31m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'

echo -e "${CYAN}════════════════════════════════════════════════════════════════════════${NC}"
echo -e "${CYAN} KB e2e test suite — per apioption.md categories${NC}"
echo -e "${CYAN}════════════════════════════════════════════════════════════════════════${NC}"
echo "  Gateway:  ${GATEWAY:-http://localhost:30080}"
echo "  Categories dir: $SCRIPT_DIR/categories/"
echo

TOTAL_PASS=0; TOTAL_FAIL=0; FAILED_CATEGORIES=()

for f in "$SCRIPT_DIR"/categories/*.sh; do
  [ -f "$f" ] || continue
  name="$(basename "$f" .sh)"
  echo
  echo -e "${YELLOW}>>> $name <<<${NC}"
  bash "$f"
  rc=$?
  # Each script ends with PASS/FAIL counts on the last meaningful lines.
  # We re-count by re-running grep; cheaper to capture stdout above.
  if [ "$rc" -ne 0 ]; then
    FAILED_CATEGORIES+=("$name")
    TOTAL_FAIL=$((TOTAL_FAIL + rc))
  fi
done

echo
echo -e "${CYAN}════════════════════════════════════════════════════════════════════════${NC}"
echo -e "${CYAN} Aggregate result${NC}"
echo -e "${CYAN}════════════════════════════════════════════════════════════════════════${NC}"
if [ "${#FAILED_CATEGORIES[@]}" -eq 0 ]; then
  echo -e "${GREEN}ALL CATEGORIES PASSED${NC}"
  exit 0
else
  echo -e "${RED}FAILED CATEGORIES (${#FAILED_CATEGORIES[@]}):${NC}"
  for c in "${FAILED_CATEGORIES[@]}"; do echo -e "  ${RED}- $c${NC}"; done
  exit 1
fi
