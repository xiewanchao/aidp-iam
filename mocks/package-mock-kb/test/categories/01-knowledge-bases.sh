#!/usr/bin/env bash
# ============================================================================
# 01-knowledge-bases.sh — apioption.md (1) 知识库管理
#
# Coverage:
#   读权限 (GET, kb_browse, all-users):
#     GET /kb/knowledge_bases/page                 — paginated list
#     GET /kb/knowledge_bases/count                — total
#     GET /kb/knowledge_bases?kbs_id=...           — single (resource ACL)
#     GET /kb/knowledge_bases/mappings?kbs_id=...
#     GET /kb/knowledge_bases/mappings/count
#     GET /kb/knowledge_bases/files?kbs_id=...      — paginated files
#     GET /kb/knowledge_bases/files/count?kbs_id=...
#     GET /kb/knowledge_bases/files/history?kbs_id=...
#     GET /kb/knowledge_bases/files/filesystem
#     GET /kb/knowledge_bases/jargon_groups
#
#   编辑权限 (POST, kb_edit, all-users + resource ACL):
#     POST /kb/knowledge_bases/add                 — creates KB, owner ACL written
#     POST /kb/knowledge_bases/modify              — needs contributor; owner has it
#     POST /kb/knowledge_bases/remove              — needs owner; ACL cascade-clean
#     POST /kb/knowledge_bases/mappings/add        — needs contributor on parent KB
#     POST /kb/knowledge_bases/mappings/remove     — runs on kb-admin (uses path-level
#                                                    bypass to avoid IAM child-cascade
#                                                    quirk that nukes parent ACL)
#     POST /kb/knowledge_bases/files/upload        — multipart, contributor
#     POST /kb/knowledge_bases/files/remove        — POST body, contributor
#
# Test ordering note: /mappings/remove and /remove on the SAME KB by the SAME
# normal-user trigger a parent-ACL cascade in the current IAM design. Tests
# therefore exercise mapping lifecycle on kb-admin's KB (kb_admin_full path
# bypass) and modify/remove lifecycle on normal-user's KB.
# ============================================================================
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/../lib/common.sh"

banner "[01] KNOWLEDGE BASES — apioption.md (1)"
init_tokens

# ── (A) Create one KB per role we'll use ────────────────────────────────────
section "POST /kb/knowledge_bases/add (create — both kb-admin and normal-user)"

assert_eq "no-token → 401" "401" \
  "$(http_code -X POST "$GATEWAY/kb/knowledge_bases/add")"

# kb-admin creates
RESP_ADMIN=$(NPOST "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/add" '{"name":"e2e-admin-kb"}')
CODE_ADMIN=$(NPOSTC "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/add" '{"name":"e2e-admin-kb-2"}')
assert_in "kb-admin → 201" "$CODE_ADMIN" "200" "201"
KB_ADMIN=$(echo "$RESP_ADMIN" | json_get data.KDSID)
assert_match "response carries data.KDSID" '^[A-Za-z0-9-]{4,}$' "$KB_ADMIN"

# normal-user creates → ext_proc auto-writes owner ACL
RESP_USER=$(NPOST "$T_USER" "$GATEWAY/kb/knowledge_bases/add" '{"name":"e2e-user-kb"}')
CODE_USER=$(NPOSTC "$T_USER" "$GATEWAY/kb/knowledge_bases/add" '{"name":"e2e-user-kb-2"}')
assert_in "normal-user → 201" "$CODE_USER" "200" "201"
KB_USER=$(echo "$RESP_USER" | json_get data.KDSID)
sleep 2
NUID=$(kubectl -n "$KEYCLOAK_NS" exec postgres-0 -c postgres -- \
  psql -U keycloak -d keycloak -tA -c \
  "SELECT id FROM user_entity WHERE username='normal-user' AND realm_id=(SELECT id FROM realm WHERE name='aidp')" 2>/dev/null | tr -d ' \r\n')
assert_eq "ACL owner of normal-user's KB == normal-user UUID" "$NUID" \
  "$(acl_owner knowledgebase "$KB_USER")"

# ── (B) GET 系列 ────────────────────────────────────────────────────────────
section "GET /kb/knowledge_bases/page (kb_browse, all-users)"
assert_eq "no-token → 401" "401" "$(http_code "$GATEWAY/kb/knowledge_bases/page")"
assert_eq "normal-user → 200"      "200" "$(NHC "$T_USER"    "$GATEWAY/kb/knowledge_bases/page")"
assert_eq "kb-admin → 200"         "200" "$(NHC "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/page")"
BODY=$(NH "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/page")
assert_contains "response has data array" '"data"' "$BODY"

section "GET /kb/knowledge_bases/count"
assert_eq "kb-admin → 200" "200" "$(NHC "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/count")"
COUNT_BODY=$(NH "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/count")
assert_contains "response has count" '"count"' "$COUNT_BODY"

section "GET /kb/knowledge_bases?kbs_id=… (resource-level: needs viewer)"
assert_eq "normal-user own KB → 200" "200" \
  "$(NHC "$T_USER" "$GATEWAY/kb/knowledge_bases?kbs_id=$KB_USER")"
assert_eq "normal-user reads other's KB → 403" "403" \
  "$(NHC "$T_USER" "$GATEWAY/kb/knowledge_bases?kbs_id=$KB_ADMIN")"
assert_eq "GET without kbs_id (list mode) → 200" "200" \
  "$(NHC "$T_USER" "$GATEWAY/kb/knowledge_bases")"

section "GET /kb/knowledge_bases/mappings?kbs_id=…"
assert_eq "kb-admin → 200" "200" \
  "$(NHC "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/mappings?kbs_id=$KB_ADMIN")"

section "GET /kb/knowledge_bases/mappings/count?kbs_id=…"
assert_eq "kb-admin → 200" "200" \
  "$(NHC "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/mappings/count?kbs_id=$KB_ADMIN")"

section "GET /kb/knowledge_bases/files?kbs_id=…"
assert_eq "kb-admin → 200" "200" \
  "$(NHC "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/files?kbs_id=$KB_ADMIN")"
FBODY=$(NH "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/files?kbs_id=$KB_ADMIN")
assert_contains "files response has files key" '"files"' "$FBODY"
assert_contains "files response has total"     '"total"' "$FBODY"

section "GET /kb/knowledge_bases/files/count?kbs_id=…"
assert_eq "kb-admin → 200" "200" \
  "$(NHC "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/files/count?kbs_id=$KB_ADMIN")"

section "GET /kb/knowledge_bases/files/history?kbs_id=…"
assert_eq "kb-admin → 200" "200" \
  "$(NHC "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/files/history?kbs_id=$KB_ADMIN")"
HBODY=$(NH "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/files/history?kbs_id=$KB_ADMIN")
assert_contains "history response has summary" '"summary"' "$HBODY"

section "GET /kb/knowledge_bases/files/filesystem"
assert_eq "kb-admin → 200" "200" \
  "$(NHC "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/files/filesystem")"

section "GET /kb/knowledge_bases/jargon_groups"
assert_eq "kb-admin → 200" "200" \
  "$(NHC "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/jargon_groups?kb_name=test")"

# ── (C) modify on normal-user's KB (owner has contributor) ─────────────────
section "POST /kb/knowledge_bases/modify"
assert_eq "no-token → 401" "401" "$(http_code -X POST "$GATEWAY/kb/knowledge_bases/modify")"
assert_eq "normal-user modifies own KB → 200" "200" \
  "$(NPOSTC "$T_USER" "$GATEWAY/kb/knowledge_bases/modify" \
    "{\"kbs_id\":\"$KB_USER\",\"name\":\"renamed\"}")"
assert_eq "normal-user modifies kb-admin's KB → 403" "403" \
  "$(NPOSTC "$T_USER" "$GATEWAY/kb/knowledge_bases/modify" \
    "{\"kbs_id\":\"$KB_ADMIN\",\"name\":\"hack\"}")"

# ── (D) /remove on KB_ADMIN — clean kb-admin's KB while ACL is intact ──────
section "POST /kb/knowledge_bases/remove (kb-admin's KB)"
assert_eq "kb-admin removes own KB → 200" "200" \
  "$(NPOSTC "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/remove" "{\"kbs_id\":\"$KB_ADMIN\"}")"
sleep 1
assert_eq "ACL row gone for kb-admin's KB" "0" \
  "$(acl_count knowledgebase "$KB_ADMIN")"

# ── (E) Sub-resource lifecycle on sacrificial KB (mappings + files; both
#       trigger IAM child-cascade-deletes-parent-ACL — KB_M is throwaway) ──
section "POST /kb/knowledge_bases/mappings/{add,remove} + files/{upload,remove} (fresh KB)"
RESP_KM=$(NPOST "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/add" '{"name":"e2e-mappings-kb"}')
KB_M=$(echo "$RESP_KM" | json_get data.KDSID)
assert_match "fresh KB for mappings" '^[A-Za-z0-9-]{4,}$' "$KB_M"
sleep 2

# normal-user → 403 (not owner)
assert_eq "normal-user adds mapping to OTHER's KB → 403" "403" \
  "$(NPOSTC "$T_USER" "$GATEWAY/kb/knowledge_bases/mappings/add" \
    "{\"kbs_id\":\"$KB_M\",\"src_dir\":\"/h\",\"fs_id\":\"f\",\"fs_name\":\"l\",\"channel_name\":\"c\"}")"

# kb-admin → 201 (owner)
RESP_M=$(NPOST "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/mappings/add" \
  "{\"kbs_id\":\"$KB_M\",\"src_dir\":\"/d\",\"fs_id\":\"f1\",\"fs_name\":\"local\",\"channel_name\":\"c1\"}")
CODE_M=$(NPOSTC "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/mappings/add" \
  "{\"kbs_id\":\"$KB_M\",\"src_dir\":\"/d2\",\"fs_id\":\"f2\",\"fs_name\":\"local\",\"channel_name\":\"c2\"}")
assert_in "kb-admin adds mapping → 201" "$CODE_M" "200" "201"
DM_ID=$(echo "$RESP_M" | json_get data.CHANNELID)
assert_match "response has data.CHANNELID" '^[A-Za-z0-9-]{4,}$' "$DM_ID"

# mappings/remove first — cascade kills KB_M's ACL but mappings/add was already done
assert_eq "kb-admin removes mapping → 200" "200" \
  "$(NPOSTC "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/mappings/remove" \
    "{\"kbs_id\":\"$KB_M\",\"kbs_dm_id\":\"$DM_ID\"}")"

# files lifecycle on a fresh sacrificial KB (KB_F) — IAM child-cascade quirk
# applies here too, so each child path needs its own KB.
RESP_KF=$(NPOST "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/add" '{"name":"e2e-files-kb"}')
KB_F=$(echo "$RESP_KF" | json_get data.KDSID)
assert_match "fresh KB for files" '^[A-Za-z0-9-]{4,}$' "$KB_F"
sleep 2

# files/upload — multipart via stdin (avoids Windows/Git Bash path-conversion
# issue with curl's @file syntax).
UPC=$(printf 'test upload content\n' \
  | curl -sS --max-time 10 -X POST -H "Authorization: Bearer $T_KBADMIN" \
    -F "kbs_id=$KB_F" -F "files=@-;filename=upload.txt;type=text/plain" \
    -o /dev/null -w "%{http_code}" "$GATEWAY/kb/knowledge_bases/files/upload")
assert_in "kb-admin upload → 201" "$UPC" "200" "201"

# files/remove (cascade kills KB_F's ACL — KB_F is sacrificial)
assert_in "kb-admin removes files → 200" \
  "$(NPOSTC "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/files/remove" \
    "{\"kbs_id\":\"$KB_F\",\"file_ids\":[]}")" "200" "201"

# ── (F) Final cleanup — normal-user's KB ───────────────────────────────────
section "POST /kb/knowledge_bases/remove (normal-user — last)"
assert_eq "no-token → 401" "401" "$(http_code -X POST "$GATEWAY/kb/knowledge_bases/remove")"
assert_eq "normal-user removes own KB → 200" "200" \
  "$(NPOSTC "$T_USER" "$GATEWAY/kb/knowledge_bases/remove" "{\"kbs_id\":\"$KB_USER\"}")"
sleep 2
assert_eq "ACL row gone after delete" "0" \
  "$(acl_count knowledgebase "$KB_USER")"

print_summary
exit "$FAIL"
