"""
gRPC ext_proc server for Envoy External Processing on port 8082.

Implements the Envoy ext_proc v3 bidirectional streaming protocol to
intercept HTTP requests and responses flowing through Envoy Gateway.

New design (v2.0): URL-based unified authorization.
All URLs follow: /<Namespace>/Tenants/<TenantID>/<TypeA>/<IDA>[/<TypeB>/<IDB>...][/<Action>]

Request phase (request_headers):
  - Collection GET (path ends at TypeA level): query resource_acl for
    allowed IDs, inject X-Allowed-Ids and X-Allowed-Total headers.
  - PUT (create): record creator and object_path for response phase.
  - DELETE: record object_path for cascade ACL deletion on success.

Response phase (response_headers):
  - PUT 2xx: buffer response body to extract resource ID (if server-generated).
  - DELETE 2xx: cascade-delete all ACL entries for object_path prefix.

Response body phase (response_body, only for PUT creates):
  - Extract resource ID from response body (field "id" by default).
  - Write resource_acl: creator → Owner.
  - On failure: queue to pending_acl for retry.
"""

import json
import logging
from urllib.parse import parse_qs, urlparse

import grpc

from ext_proc_pb2 import (  # type: ignore[import]
    BodyMutation,
    BodyResponse,
    CommonResponse,
    HeaderMutation,
    HeadersResponse,
    HeaderValue,
    HeaderValueOption,
    ProcessingMode,
    ProcessingResponse,
    StreamedResponse,
    TrailersResponse,
)
from ext_proc_pb2_grpc import (  # type: ignore[import]
    ExternalProcessorServicer,
    add_ExternalProcessorServicer_to_server,
)

from . import db

logger = logging.getLogger(__name__)

OWNER_ROLE = "AccessManager/Tenants/System/Roles/Owner"
ADMIN_GROUPS = {"master-admins", "tenant-admins"}


# ---------------------------------------------------------------------------
# URL parsing helpers
# ---------------------------------------------------------------------------

def parse_unified_url(path: str) -> dict | None:
    """
    Parse a unified URL path into its components.

    Format: /<NS>/Tenants/<tid>/<TypeA>/<IDA>[/<TypeB>/<IDB>...][/<Action>]

    Returns a dict with:
      namespace    : str   e.g. "MemoryStore"
      tenant_id    : str   e.g. "t-001"
      object_path  : str   full path without leading slash, e.g.
                           "MemoryStore/Tenants/t-001/MemoryStores/ms-001"
      is_collection: bool  True when path ends at a TypeX level (no ID)
      action_name  : str   non-empty when last segment is an Action (POST)

    Returns None when the path does not match the unified format.
    """
    parts = path.lstrip("/").split("/")
    # Minimum: NS/Tenants/tid/Type  (4 parts)
    if len(parts) < 4 or parts[1] != "Tenants":
        return None

    namespace = parts[0]
    tenant_id = parts[2]

    # Determine if the last segment is an Action (POST verb suffix) or a
    # resource ID. We use a simple heuristic: if the total number of
    # resource segments (after NS/Tenants/tid) is odd, the last segment
    # is an ID; if even, it's either a Type (collection) or an Action.
    #
    # Segment count after NS/Tenants/tid:
    #   1 → Type                  (collection)
    #   2 → Type/ID               (instance)
    #   3 → Type/ID/SubType       (sub-collection) or Type/ID/Action
    #   4 → Type/ID/SubType/SubID (sub-instance)
    #   ...
    resource_parts = parts[3:]  # everything after NS/Tenants/tid
    n = len(resource_parts)

    # Even count → last segment is a Type or Action name (no ID)
    # Odd count  → last segment is an ID
    is_collection = (n % 2 == 1)  # odd resource parts → last is Type (collection)
    action_name = ""

    # Detect Action: POST with an odd number of resource parts where the
    # last segment starts with an uppercase letter (convention).
    # We store action_name but leave object_path pointing to the parent.
    if is_collection and n >= 3:
        candidate = resource_parts[-1]
        # Action names are CamelCase; resource types are also CamelCase.
        # We distinguish by context in the caller (method == POST).
        action_name = candidate

    object_path = "/".join(parts)
    return {
        "namespace": namespace,
        "tenant_id": tenant_id,
        "object_path": object_path,
        "is_collection": is_collection,
        "action_name": action_name,
        "resource_parts": resource_parts,
    }


def _type_prefix_from_url(parsed: dict) -> str:
    """
    Return the type-level prefix for a collection GET.

    e.g. parsed object_path = "MemoryStore/Tenants/t-001/MemoryStores"
    → returns "MemoryStore/Tenants/t-001/MemoryStores"
    """
    return parsed["object_path"]


def _is_admin(groups: list[str], tenant_id: str, namespace: str = "") -> bool:
    """Return True if any group bypasses X-Allowed-Ids injection.
    - tenant-admins: bypass for all resources within the tenant
    - {namespace}-admins: bypass for resources of that specific application
    master-admins does NOT bypass per-resource ACL on tenant resources."""
    bypass = {f"AccessManager/Tenants/{tenant_id}/Groups/tenant-admins"}
    if namespace:
        bypass.add(f"AccessManager/Tenants/{tenant_id}/Groups/{namespace}-admins")
    return bool(bypass & set(groups))


# ---------------------------------------------------------------------------
# gRPC response builders (unchanged from v1)
# ---------------------------------------------------------------------------

def _headers_to_dict(header_map) -> dict[str, str]:
    result: dict[str, str] = {}
    if header_map and header_map.headers:
        for hv in header_map.headers:
            if hv.value:
                result[hv.key.lower()] = hv.value
            elif hv.raw_value:
                result[hv.key.lower()] = hv.raw_value.decode("utf-8", errors="replace")
            else:
                result[hv.key.lower()] = ""
    return result


def _make_continue_response() -> ProcessingResponse:
    return ProcessingResponse(
        request_headers=HeadersResponse(
            response=CommonResponse(status=CommonResponse.CONTINUE)
        )
    )


def _make_header_value_option(key: str, value: str) -> HeaderValueOption:
    return HeaderValueOption(
        header=HeaderValue(
            key=key,
            value=value,
            raw_value=value.encode("utf-8") if value else b"",
        ),
        append_action=2,  # OVERWRITE_IF_EXISTS_OR_ADD
    )


def _make_headers_continue(
    field: str,
    header_mutations: list[HeaderValueOption] | None = None,
) -> ProcessingResponse:
    mutation = None
    if header_mutations:
        mutation = HeaderMutation(set_headers=header_mutations)
    resp = CommonResponse(status=CommonResponse.CONTINUE, header_mutation=mutation)
    hr = HeadersResponse(response=resp)
    if field == "request_headers":
        return ProcessingResponse(request_headers=hr)
    return ProcessingResponse(response_headers=hr)


def _make_response_headers_buffer() -> ProcessingResponse:
    resp = CommonResponse(status=CommonResponse.CONTINUE)
    return ProcessingResponse(
        response_headers=HeadersResponse(response=resp),
        mode_override=ProcessingMode(
            response_body_mode=ProcessingMode.BUFFERED,
        ),
    )


def _make_response_headers_skip_body() -> ProcessingResponse:
    """Return response_headers continue with body processing disabled.
    Used for DELETE responses which have no body to process."""
    resp = CommonResponse(status=CommonResponse.CONTINUE)
    return ProcessingResponse(
        response_headers=HeadersResponse(response=resp),
        mode_override=ProcessingMode(
            response_body_mode=ProcessingMode.NONE,
        ),
    )


def _make_body_continue(body_bytes: bytes | None = None) -> ProcessingResponse:
    mutation = BodyMutation(
        streamed_response=StreamedResponse(
            body=body_bytes if body_bytes is not None else b"",
            end_of_stream=True,
        )
    )
    resp = CommonResponse(status=CommonResponse.CONTINUE, body_mutation=mutation)
    return ProcessingResponse(response_body=BodyResponse(response=resp))


def _make_body_phase_continue(field: str) -> ProcessingResponse:
    body_response = BodyResponse(response=CommonResponse(status=CommonResponse.CONTINUE))
    if field == "request_body":
        return ProcessingResponse(request_body=body_response)
    return ProcessingResponse(response_body=body_response)


# ---------------------------------------------------------------------------
# ExternalProcessorServicer
# ---------------------------------------------------------------------------

class ExtProcService(ExternalProcessorServicer):

    async def process(self, request_iterator, context):
        request_state: dict = {
            "method": "",
            "path": "",
            "query_params": {},
            "user_id": "",
            "user_path": "",
            "tenant_id": "",
            "groups": [],
            "parsed_url": None,
            "is_create": False,
            "is_delete": False,
            "need_response_body": False,
        }

        while True:
            request, response, stop = await self._next_stream_request(request_iterator, request_state)
            if stop:
                break
            if response is None:
                response = await self._handle_stream_request(request, request_state)
            if response is not None:
                yield response

    Process = process

    async def _next_stream_request(self, request_iterator, request_state: dict) -> tuple:
        try:
            return await request_iterator.__anext__(), None, False
        except StopAsyncIteration:
            return None, None, True
        except Exception as exc:
            logger.error("ext_proc: decode error: %s path=%s", exc, request_state.get("path"))
            return None, _make_continue_response(), False

    async def _handle_stream_request(self, request, request_state: dict) -> ProcessingResponse | None:
        msg_type = request.WhichOneof("request")
        if msg_type == "request_headers":
            return await self._handle_request_headers(request.request_headers, request_state)
        if msg_type == "response_headers":
            return await self._handle_response_headers(request.response_headers, request_state)
        if msg_type == "request_body":
            return _make_body_phase_continue("request_body")
        if msg_type == "response_body":
            if request_state.get("need_response_body"):
                return await self._handle_response_body(request.response_body, request_state)
            return _make_body_phase_continue("response_body")
        if msg_type == "request_trailers":
            return ProcessingResponse(request_trailers=TrailersResponse())
        if msg_type == "response_trailers":
            return ProcessingResponse(response_trailers=TrailersResponse())
        logger.warning("ext_proc: unknown message type: %s", msg_type)
        return None

    # ------------------------------------------------------------------

    async def _handle_request_headers(self, http_headers, request_state: dict) -> ProcessingResponse:
        hdrs = _headers_to_dict(http_headers.headers)

        method = hdrs.get(":method", "GET").upper()
        raw_path = hdrs.get(":path", "/")
        parsed_path = urlparse(raw_path)
        path = parsed_path.path
        query_params = parse_qs(parsed_path.query)

        user_id = hdrs.get("x-auth-user-id", "")
        tenant_id = hdrs.get("x-auth-tenant", "")
        groups_raw = hdrs.get("x-auth-groups", "")
        groups = [g.strip() for g in groups_raw.split(",") if g.strip()]

        parsed = parse_unified_url(path)

        request_state.update({
            "method": method,
            "path": path,
            "query_params": query_params,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "groups": groups,
            "parsed_url": parsed,
            "is_create": False,
            "is_delete": False,
            "need_response_body": False,
        })

        if parsed is None:
            return _make_headers_continue("request_headers")

        user_path = f"AccessManager/Tenants/{tenant_id}/Users/{user_id}" if user_id else ""
        request_state["user_path"] = user_path

        logger.info(
            "ext_proc request: method=%s path=%s tenant=%s user=%s collection=%s",
            method, path, tenant_id, user_id, parsed["is_collection"],
        )

        if method == "GET" and parsed["is_collection"]:
            return await self._handle_collection_get(parsed, request_state)

        if method == "PUT" and parsed["is_collection"]:
            request_state["is_create"] = True
        elif method == "PUT" and not parsed["is_collection"]:
            request_state["is_upsert"] = True

        if method == "DELETE" and not parsed["is_collection"]:
            request_state["is_delete"] = True
        elif method == "DELETE" and parsed["is_collection"]:
            request_state["is_delete"] = await _is_collection_sub_resource_delete(
                parsed["object_path"], tenant_id,
            )

        return _make_headers_continue("request_headers")

    async def _handle_collection_get(
        self, parsed: dict, request_state: dict,
    ) -> ProcessingResponse:
        """Inject X-Allowed-Ids for collection GET unless caller is an admin."""
        tenant_id = request_state["tenant_id"]
        user_id = request_state["user_id"]
        user_path = request_state["user_path"]
        groups = request_state["groups"]
        query_params = request_state["query_params"]
        namespace = parsed["namespace"]

        if _is_admin(groups, tenant_id, namespace):
            logger.info("ext_proc: admin bypass for X-Allowed-Ids user=%s", user_id)
            return _make_headers_continue("request_headers")

        type_prefix = _type_prefix_from_url(parsed)
        list_filter_mode = await db.get_list_filter_mode(type_prefix)
        if list_filter_mode == "app_callback":
            logger.info("ext_proc: app_callback mode for %s, skipping injection", type_prefix)
            return _make_headers_continue("request_headers")

        page = int(query_params.get("page", ["1"])[0])
        size = min(int(query_params.get("page_size", ["200"])[0]), 500)

        try:
            allowed_ids, total = await db.get_allowed_ids(
                tenant_id, user_path, groups, type_prefix, page, size,
            )
            logger.info("ext_proc: injecting X-Allowed-Ids count=%d total=%d", len(allowed_ids), total)
            return _make_headers_continue(
                "request_headers",
                header_mutations=[
                    _make_header_value_option("X-Allowed-Ids", ",".join(allowed_ids)),
                    _make_header_value_option("X-Allowed-Total", str(total)),
                ],
            )
        except Exception as exc:
            logger.error("ext_proc: failed to query allowed IDs: %s", exc)
            return _make_headers_continue("request_headers")

    async def _handle_response_headers(self, http_headers, request_state: dict) -> ProcessingResponse:
        hdrs = _headers_to_dict(http_headers.headers)
        status_str = hdrs.get(":status", "200")

        try:
            status_code = int(status_str)
        except (TypeError, ValueError):
            status_code = 0

        is_2xx = 200 <= status_code < 300
        parsed = request_state.get("parsed_url")

        logger.info(
            "ext_proc response: method=%s status=%s path=%s",
            request_state["method"], status_str, request_state["path"],
        )

        if parsed is None or not is_2xx:
            return _make_headers_continue("response_headers")

        if request_state.get("is_create"):
            request_state["need_response_body"] = True
            return _make_response_headers_buffer()

        if request_state.get("is_upsert") and status_code == 201:
            request_state["need_response_body"] = True
            request_state["is_upsert_create"] = True
            return _make_response_headers_buffer()

        if request_state.get("is_delete"):
            await _cascade_delete_acl(parsed["object_path"])
            return _make_response_headers_skip_body()

        return _make_headers_continue("response_headers")

    async def _handle_response_body(self, http_body, request_state: dict) -> ProcessingResponse:
        body_bytes = http_body.body if http_body.body else b""
        parsed = request_state.get("parsed_url")

        if not request_state.get("need_response_body") or parsed is None:
            return _make_body_continue(body_bytes)

        tenant_id = request_state["tenant_id"]
        user_path = request_state["user_path"]
        object_path = parsed["object_path"]

        if parsed["is_collection"]:
            object_path = await _resolve_create_object_path(
                object_path, tenant_id, body_bytes, request_state["path"],
            )
            if object_path is None:
                return _make_body_continue(body_bytes)
        elif not request_state.get("is_upsert_create"):
            return _make_body_continue(body_bytes)

        await _write_acl_with_retry(tenant_id, user_path, object_path)
        return _make_body_continue(body_bytes)


async def _is_collection_sub_resource_delete(object_path: str, tenant_id: str) -> bool:
    """Return True when a DELETE on a collection-level path targets a sub-resource instance.

    SpecialKL and similar resources parse as is_collection=True because their
    URL has an odd segment count, but the last segment is actually a resource ID.
    Walk up the resource_patterns table to detect this case.
    """
    rp = _collection_prefix(object_path, tenant_id, True)
    pat = await _get_resource_pattern_or_none(rp)
    if pat is not None:
        return False
    while "/" in rp:
        rp = rp.rsplit("/", 1)[0]
        pat = await _get_resource_pattern_or_none(rp)
        if pat is not None:
            return True
    return False


async def _get_resource_pattern_or_none(resource_prefix: str, warn: bool = False) -> dict | None:
    try:
        return await db.get_resource_pattern(resource_prefix)
    except Exception as exc:
        if warn:
            logger.warning("ext_proc: failed to query resource_patterns prefix=%s: %s", resource_prefix, exc)
        return None


async def _find_create_pattern(resource_prefix: str) -> tuple[dict | None, bool]:
    pattern = await _get_resource_pattern_or_none(resource_prefix, warn=True)
    if pattern is not None:
        return pattern, False

    parent_prefix = resource_prefix
    while "/" in parent_prefix:
        parent_prefix = parent_prefix.rsplit("/", 1)[0]
        pattern = await _get_resource_pattern_or_none(parent_prefix)
        if pattern is not None:
            return pattern, True
    return None, False


async def _resolve_create_object_path(
    object_path: str,
    tenant_id: str,
    body_bytes: bytes,
    request_path: str,
) -> str | None:
    """Resolve the full instance object_path for a PUT-to-collection create.

    Returns the instance path (collection_path/resource_id), or None when the
    ID cannot be determined and the ACL write should be skipped.
    """
    resource_prefix = _collection_prefix(object_path, tenant_id, True)
    pattern, is_sub_resource = await _find_create_pattern(resource_prefix)
    if is_sub_resource:
        resource_id = resource_prefix.rsplit("/", 1)[-1]
        return object_path if resource_id else None

    id_field = pattern.get("response_id_field") or pattern.get("id_field") if pattern else None
    resource_id = _extract_id_from_body(body_bytes, id_field)
    if resource_id:
        return object_path + "/" + resource_id
    logger.warning(
        "ext_proc: PUT to collection but could not extract ID from body path=%s id_field=%s",
        request_path, id_field,
    )
    return None


async def _cascade_delete_acl(object_path: str) -> None:
    """Delete all ACL entries whose object_path starts with the given prefix."""
    try:
        deleted = await db.delete_acl_by_prefix(object_path)
        logger.info("ext_proc: cascade-deleted %d ACL entries for %s", deleted, object_path)
    except Exception as exc:
        logger.error("ext_proc: failed to delete ACL for %s: %s", object_path, exc)
        try:
            await db.write_pending("delete_prefix", object_path, error=str(exc))
        except Exception as pexc:
            logger.error("ext_proc: failed to queue pending delete: %s", pexc)


async def _write_acl_with_retry(tenant_id: str, user_path: str, object_path: str) -> None:
    """Write Owner ACL for the creator, then execute on_create_acl templates if any."""
    if not (user_path and object_path and tenant_id):
        logger.warning(
            "ext_proc: skipping ACL write — missing data: user_path=%s object_path=%s tenant=%s",
            user_path, object_path, tenant_id,
        )
        return
    try:
        inserted = await db.write_acl_entry(tenant_id, user_path, object_path, OWNER_ROLE, user_path)
        logger.info("ext_proc: wrote Owner ACL user=%s object=%s inserted=%s", user_path, object_path, inserted)
    except Exception as exc:
        logger.error("ext_proc: failed to write ACL user=%s object=%s: %s", user_path, object_path, exc)
        try:
            await db.write_pending(
                "write", object_path,
                tenant_id=tenant_id, user_path=user_path,
                role_path=OWNER_ROLE, created_by=user_path, error=str(exc),
            )
        except Exception as pexc:
            logger.error("ext_proc: failed to queue pending write: %s", pexc)

    # Execute on_create_acl templates declared in the resource's manifest pattern.
    # Each template: {user_template, path_suffix, role_path}
    # {tenantId} → tenant_id, {instanceId} → last segment of object_path
    resource_prefix = _collection_prefix(object_path, tenant_id, False)
    try:
        pattern = await db.get_resource_pattern(resource_prefix)
    except Exception:
        pattern = None

    templates = (pattern or {}).get("on_create_acl", [])
    if not templates:
        return

    instance_name = object_path.rsplit("/", 1)[-1]
    for tmpl in templates:
        try:
            user_tmpl = tmpl.get("user_template", "")
            object_tmpl = tmpl.get("object_template", "")
            role = tmpl.get("role_path", "")
            if not (user_tmpl and object_tmpl and role):
                logger.warning("ext_proc: on_create_acl entry missing fields: %s", tmpl)
                continue
            resolved_user = user_tmpl.replace("{tenantId}", tenant_id).replace("{instanceName}", instance_name)
            resolved_object = object_tmpl.replace("{tenantId}", tenant_id).replace("{instanceName}", instance_name)
            await db.write_acl_entry(tenant_id, resolved_user, resolved_object, role, user_path)
            logger.info(
                "ext_proc: on_create_acl wrote user=%s object=%s role=%s",
                resolved_user, resolved_object, role,
            )
        except Exception as exc:
            logger.error("ext_proc: on_create_acl failed for %s: %s", tmpl, exc)


def _collection_prefix(object_path: str, tenant_id: str, is_collection: bool) -> str:
    """
    Convert a runtime object_path to the resource_prefix key stored in
    resource_patterns (manifest template form with leading slash and
    {tenantId} placeholder).

    Examples:
      "DataAgent/Tenants/t-001/DataAgentDBs"          (collection) →
        "/DataAgent/Tenants/{tenantId}/DataAgentDBs"
      "DataAgent/Tenants/t-001/DataAgentDBs/db-001"   (instance) →
        "/DataAgent/Tenants/{tenantId}/DataAgentDBs"
      "KnowledgeBase/Tenants/System/ModelConfigs"      (system, collection) →
        "/KnowledgeBase/Tenants/System/ModelConfigs"
    """
    path = object_path if is_collection else object_path.rsplit("/", 1)[0]
    if tenant_id and tenant_id != "System":
        path = path.replace(f"/Tenants/{tenant_id}/", "/Tenants/{tenantId}/", 1)
    return "/" + path


def _extract_id_from_body(body_bytes: bytes, id_field: str | None = None) -> str | None:
    """
    Extract resource ID from response body JSON.

    Tries id_field first (supports dot-notation for nested paths, e.g.
    'data.kb_id'), then falls back to the standard 'id'/'ID'/'resourceId'
    fields.
    """
    if not body_bytes:
        return None
    try:
        obj = json.loads(body_bytes)
        if not isinstance(obj, dict):
            return None
        # Try manifest-declared field first (dot-notation supported)
        if id_field:
            val: object = obj
            for part in id_field.split("."):
                val = val.get(part) if isinstance(val, dict) else None
            if val not in (None, ""):
                return str(val)
        # Standard fallback
        rid = obj.get("id") or obj.get("ID") or obj.get("resourceId")
        return str(rid) if rid not in (None, "") else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

async def serve() -> None:
    try:
        server = grpc.aio.server(
            options=[
                ('grpc.max_receive_message_length', 16 * 1024 * 1024),
                ('grpc.max_send_message_length', 16 * 1024 * 1024),
            ],
            compression=grpc.Compression.Gzip,
        )
        add_ExternalProcessorServicer_to_server(ExtProcService(), server)
        listen_addr = "[::]:8082"
        server.add_insecure_port(listen_addr)
        await server.start()
        logger.info("gRPC ext_proc server listening on %s", listen_addr)
        await server.wait_for_termination()
    except Exception as exc:
        logger.error("gRPC ext_proc server failed: %s", exc, exc_info=True)
        raise
