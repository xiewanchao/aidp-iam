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
#     POST /kb/knowledge_bases/mappings/remove     — needs contributor; does NOT
#                                                    cascade-delete parent KB's ACL
#                                                    (ext_proc only cascades when
#                                                    min_permission='owner')
#     POST /kb/knowledge_bases/files/upload        — multipart, none (path-level only)
#     POST /kb/knowledge_bases/files/remove        — POST body, contributor; no cascade
#
# Cascade contract (post-fix): owner-level deletes (POST /remove) cascade-clean
# the resource_acl; contributor-level child removes (mappings/remove,
# files/remove) preserve the parent KB's ACL. Tests assert this contract.
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

# ── (D) Mappings lifecycle on KB_ADMIN — full add+remove on same KB ────────
# After IAM ext_proc fix (v1.4.1): contributor-level child removes (mappings/
# remove, files/remove) no longer cascade-delete the parent KB's ACL, so we
# can freely test child operations on the SAME KB without sacrificing it.
section "POST /kb/knowledge_bases/mappings/{add,remove}"

# normal-user → 403 (not owner of KB_ADMIN)
assert_eq "normal-user adds mapping to OTHER's KB → 403" "403" \
  "$(NPOSTC "$T_USER" "$GATEWAY/kb/knowledge_bases/mappings/add" \
    "{\"kbs_id\":\"$KB_ADMIN\",\"src_dir\":\"/h\",\"fs_id\":\"f\",\"fs_name\":\"l\",\"channel_name\":\"c\"}")"

# kb-admin add → 201, ext_proc writes child mapping ACL
RESP_M=$(NPOST "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/mappings/add" \
  "{\"kbs_id\":\"$KB_ADMIN\",\"src_dir\":\"/d\",\"fs_id\":\"f1\",\"fs_name\":\"local\",\"channel_name\":\"c1\"}")
CODE_M=$(NPOSTC "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/mappings/add" \
  "{\"kbs_id\":\"$KB_ADMIN\",\"src_dir\":\"/d2\",\"fs_id\":\"f2\",\"fs_name\":\"local\",\"channel_name\":\"c2\"}")
assert_in "kb-admin adds mapping → 201" "$CODE_M" "200" "201"
DM_ID=$(echo "$RESP_M" | json_get data.CHANNELID)
assert_match "response has data.CHANNELID" '^[A-Za-z0-9-]{4,}$' "$DM_ID"

# kb-admin remove mapping — should NOT cascade KB_ADMIN's ACL after fix
assert_eq "kb-admin removes mapping → 200" "200" \
  "$(NPOSTC "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/mappings/remove" \
    "{\"kbs_id\":\"$KB_ADMIN\",\"kbs_dm_id\":\"$DM_ID\"}")"
sleep 1
# Verify the parent KB's ACL is intact (this is the cascade-fix regression test)
assert_match "parent KB ACL still intact after mappings/remove (no cascade)" '^[1-9][0-9]*$' \
  "$(acl_count knowledgebase "$KB_ADMIN")"

# ── (E) Files lifecycle on KB_ADMIN — same KB, ACL preserved ───────────────
section "POST /kb/knowledge_bases/files/{upload,remove}"

# files/upload — multipart via stdin (avoids Windows/Git Bash path-conversion
# issue with curl's @file syntax). min_permission=none (multipart can't be
# parsed by pep-proxy's body extractor); path-level kb_edit + KB backend's
# kbs_id check are the security boundary.
UPC=$(printf 'test upload content\n' \
  | curl -sS --max-time 10 -X POST -H "Authorization: Bearer $T_KBADMIN" \
    -F "kbs_id=$KB_ADMIN" -F "files=@-;filename=upload.txt;type=text/plain" \
    -o /dev/null -w "%{http_code}" "$GATEWAY/kb/knowledge_bases/files/upload")
assert_in "kb-admin upload → 201" "$UPC" "200" "201"

# files/remove — should NOT cascade KB_ADMIN's ACL after fix
assert_in "kb-admin removes files → 200" \
  "$(NPOSTC "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/files/remove" \
    "{\"kbs_id\":\"$KB_ADMIN\",\"file_ids\":[]}")" "200" "201"
sleep 1
assert_match "parent KB ACL still intact after files/remove (no cascade)" '^[1-9][0-9]*$' \
  "$(acl_count knowledgebase "$KB_ADMIN")"

# ── (F) /remove — owner-level true delete cascades ACL ─────────────────────
section "POST /kb/knowledge_bases/remove"
assert_eq "no-token → 401" "401" "$(http_code -X POST "$GATEWAY/kb/knowledge_bases/remove")"

# normal-user removes kb-admin's KB → 403 (not owner)
assert_eq "normal-user removes kb-admin's KB → 403" "403" \
  "$(NPOSTC "$T_USER" "$GATEWAY/kb/knowledge_bases/remove" "{\"kbs_id\":\"$KB_ADMIN\"}")"

# kb-admin removes own KB — owner-level delete DOES cascade
assert_eq "kb-admin removes own KB → 200" "200" \
  "$(NPOSTC "$T_KBADMIN" "$GATEWAY/kb/knowledge_bases/remove" "{\"kbs_id\":\"$KB_ADMIN\"}")"
sleep 2
assert_eq "ACL row gone for kb-admin's KB (owner cascade)" "0" \
  "$(acl_count knowledgebase "$KB_ADMIN")"

# normal-user removes own KB
assert_eq "normal-user removes own KB → 200" "200" \
  "$(NPOSTC "$T_USER" "$GATEWAY/kb/knowledge_bases/remove" "{\"kbs_id\":\"$KB_USER\"}")"
sleep 2
assert_eq "ACL row gone for normal-user's KB (owner cascade)" "0" \
  "$(acl_count knowledgebase "$KB_USER")"

print_summary
exit "$FAIL"
