"""
gRPC ext_proc server for Envoy External Processing on port 8082.

Implements the Envoy ext_proc v3 bidirectional streaming protocol to
intercept HTTP requests and responses flowing through Envoy Gateway.

Request phase (request_headers):
  - Collection GET: query resource_acl for allowed IDs, inject
    X-Allowed-Ids and X-Allowed-Total headers.
  - Other methods: match against resource_actions (or DEFAULT_ACTIONS
    fallback) to classify the request.  For delete with id_source='body'
    the request body is buffered to extract the resource_id early.

Response phase (response_headers):
  - create action with matching success_status: flag that we need to
    buffer the response body to extract the new resource ID.
  - delete action with matching success_status (or any 2xx if NULL):
    delete all ACL entries for the resource.
  - Otherwise: pass through.

Response body phase (response_body, only for create actions):
  - Extract the new resource_id using the pattern's id_source/id_field.
  - Write resource_acl with permission=owner.
  - On failure: queue to pending_acl for retry.

The classification is driven by the resource_actions table; when the
table has no rows for a given (app_name, resource_prefix) the built-in
DEFAULT_ACTIONS rules produce the same behaviour as the previous
hardcoded logic (POST->create@201, DELETE->delete@2xx, etc.).
"""

import json
import logging
from urllib.parse import parse_qs, urlparse

import grpc

# Generated stubs - built during Docker image build:
#   python -m grpc_tools.protoc -I/app/proto \
#       --python_out=/app --grpc_python_out=/app /app/proto/ext_proc.proto
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

# These are populated at startup by main.py and shared with this module.
apps: dict[str, dict] = {}               # app_name -> {path_prefix, enabled}
resource_patterns: list[dict] = []        # [{app_name, resource_prefix, resource_type,
                                          #   id_source, id_field, id_query_param}]
resource_actions: list[dict] = []         # [{id, app_name, resource_prefix, action,
                                          #   method, path_suffix, success_status,
                                          #   min_permission}]


# Built-in fallback rules applied when resource_actions has no entries
# for a given (app_name, resource_prefix).  These preserve the previous
# hardcoded behaviour for standard RESTful resource endpoints.
DEFAULT_ACTIONS: list[dict] = [
    {
        "action": "create",
        "method": "POST",
        "path_suffix": None,
        "success_status": 201,
        "min_permission": "none",
    },
    {
        "action": "list",
        "method": "GET",
        "path_suffix": None,
        "success_status": None,
        "min_permission": "none",
    },
    {
        "action": "read",
        "method": "GET",
        "path_suffix": "/{id}",
        "success_status": None,
        "min_permission": "viewer",
    },
    {
        "action": "update",
        "method": "PUT",
        "path_suffix": "/{id}",
        "success_status": None,
        "min_permission": "contributor",
    },
    {
        "action": "update",
        "method": "PATCH",
        "path_suffix": "/{id}",
        "success_status": None,
        "min_permission": "contributor",
    },
    {
        "action": "delete",
        "method": "DELETE",
        "path_suffix": "/{id}",
        "success_status": 200,
        "min_permission": "owner",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _headers_to_dict(header_map) -> dict[str, str]:
    """Convert a protobuf HeaderMap into a plain dict.

    Envoy sends header values in the ``value`` (string) field for most
    headers and may also populate ``raw_value`` (bytes) for binary
    headers.  We prefer ``value`` and fall back to decoding
    ``raw_value``.
    """
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


def _match_path(path: str, method: str = "") -> tuple[str, dict | None, str]:
    """
    Match a request path against loaded apps + resource_patterns, preferring
    a resource_pattern row whose method matches the request's method, then
    falling back to a wildcard row (method == '').

    Returns (app_name, pattern_or_None, sub_path).
    - app_name is "" when no app matches.
    - pattern is the matched resource_patterns row (full dict including
      id_source/id_field/id_query_param, share_to_* flags) or None when no
      pattern matches.
    - sub_path is the path with the app prefix stripped, starting with
      '/' (or the original path if no app matched).

    Tie-breaking within matching prefixes:
      1. longer resource_prefix wins
      2. method-specific row wins over wildcard ('') at the same length
    """
    method_up = (method or "").upper()

    for app_name, app_info in apps.items():
        prefix = app_info["path_prefix"]
        if not path.startswith(prefix):
            continue

        sub_path = path[len(prefix):]
        if sub_path and not sub_path.startswith("/"):
            sub_path = "/" + sub_path
        if not sub_path:
            sub_path = "/"

        best: dict | None = None
        best_len = -1
        best_method_specific = False
        for rp in resource_patterns:
            if rp["app_name"] != app_name:
                continue
            rp_prefix = rp["resource_prefix"]
            if not sub_path.startswith(rp_prefix):
                continue
            pat_method = (rp.get("method") or "").upper()
            # Accept either an exact method match or the wildcard '' row.
            if pat_method and pat_method != method_up:
                continue
            is_specific = bool(pat_method)
            rp_len = len(rp_prefix)
            same_prefix_prefer_specific = (
                rp_len == best_len and is_specific and not best_method_specific
            )
            if rp_len > best_len or same_prefix_prefer_specific:
                best = rp
                best_len = rp_len
                best_method_specific = is_specific

        if best is not None:
            return app_name, best, sub_path
        return app_name, None, sub_path

    return "", None, path


def _status_matches(status_code: int, expected: int | None) -> bool:
    """Return True if ``status_code`` satisfies a resource_action's success_status.

    - If ``expected`` is None, any 2xx status matches.
    - Otherwise ``status_code`` must equal ``expected``.
    """
    if expected is None:
        return 200 <= status_code < 300
    return status_code == expected


def _match_default_action(method: str, sub_path: str, action_type: str | None = None) -> dict | None:
    """Match ``sub_path`` against the built-in DEFAULT_ACTIONS rules.

    ``sub_path`` here is the portion of the path *after* the resource
    prefix (e.g. ``""`` or ``"/"`` for collection endpoints, ``"/42"``
    for item endpoints).  The ``/{id}`` placeholder matches whenever
    there is at least one non-empty path segment.
    """
    stripped = sub_path.strip("/") if sub_path else ""
    has_id = bool(stripped)

    for rule in DEFAULT_ACTIONS:
        if rule["method"] != method:
            continue
        if action_type and rule["action"] != action_type:
            continue
        if rule["path_suffix"] == "/{id}":
            if has_id:
                return rule
        elif rule["path_suffix"] is None:
            if not has_id:
                return rule
    return None


def find_action(
    app_name: str,
    resource_prefix: str,
    method: str,
    path: str,
    action_type: str | None = None,
) -> dict | None:
    """Find the resource_action rule matching the current request.

    The search order is:
      1. Rows in ``resource_actions`` for this (app_name, resource_prefix, method),
         filtered by ``action_type`` if supplied.  The first row whose
         ``path_suffix`` matches the tail of ``path`` wins.  A ``None``
         ``path_suffix`` matches when the request is a collection endpoint
         (sub_path empty or just ``/``).
      2. DEFAULT_ACTIONS fallback so legacy RESTful routes continue to
         work without configuration.

    Returns the matched action dict, or None when nothing matches.
    """
    sub_path = path[len(resource_prefix):] if path.startswith(resource_prefix) else path

    candidates = []
    for action in resource_actions:
        if action["app_name"] != app_name:
            continue
        if action["resource_prefix"] != resource_prefix:
            continue
        if action["method"] != method:
            continue
        if action_type is not None and action["action"] != action_type:
            continue
        candidates.append(action)

    for action in candidates:
        suffix = action["path_suffix"]
        if suffix is None:
            if not sub_path or sub_path == "/":
                return action
        else:
            if _suffix_matches_segments(sub_path, suffix):
                return action

    return _match_default_action(method, sub_path, action_type)


def _suffix_matches_segments(sub_path: str, suffix: str) -> bool:
    """Match sub_path against a suffix that may contain "{name}" placeholders
    (each placeholder matches exactly one path segment).

    Literal suffixes (no placeholders) MUST match the entire sub_path
    exactly. Earlier versions used ``endswith`` which incorrectly matched
    nested paths against shallower rules - e.g. sub_path
    "/knowledge_bases/remove" matching suffix "/remove" caused unbind
    operations to be misclassified as the parent's owner-level delete and
    cascade-killed the parent ACL. Exact-match is the right semantic for
    literal suffixes; placeholder suffixes still allow tail-of-path
    matching via the segment loop below.

    Examples:
        _suffix_matches_segments("/s1/replay",            "/{id}/replay")        -> True
        _suffix_matches_segments("/s1/turns/t/feedback",  "/{id}/turns/{t_id}/feedback") -> True
        _suffix_matches_segments("/add",                  "/add")                -> True
        _suffix_matches_segments("/knowledge_bases/remove", "/remove")           -> False  (was True; bug)
    """
    if "{" not in suffix:
        return sub_path == suffix
    sub_parts = sub_path.strip("/").split("/")
    suf_parts = suffix.strip("/").split("/")
    if not suf_parts or len(sub_parts) < len(suf_parts):
        return False
    tail = sub_parts[-len(suf_parts):]
    for s, p in zip(tail, suf_parts):
        if p.startswith("{") and p.endswith("}"):
            if not s:
                return False
            continue
        if s != p:
            return False
    return True


def extract_id_from_request(
    pattern: dict,
    path: str,
    query_params: dict,
    request_body: bytes | None,
) -> str | None:
    """Extract the resource_id from request data based on ``pattern.id_source``.

    Supports id_source values:
      - "path":  read the first segment after ``resource_prefix``.
      - "query": read ``query_params[pattern.id_query_param]``.
      - "body":  parse ``request_body`` as JSON and walk ``id_field``
                 (dot-delimited nested keys supported).
    """
    source = pattern.get("id_source") or "path"

    if source == "path":
        sub = path[len(pattern["resource_prefix"]):] if path.startswith(pattern["resource_prefix"]) else ""
        sub = sub.strip("/")
        return sub.split("/")[0] if sub else None

    if source == "query":
        key = pattern.get("id_query_param") or pattern.get("id_field")
        if not key:
            return None
        value = query_params.get(key)
        if isinstance(value, list):
            return value[0] if value else None
        return value

    if source == "body":
        if not request_body:
            return None
        try:
            obj = json.loads(request_body)
        except (TypeError, ValueError):
            return None
        for key in (pattern.get("id_field") or "id").split("."):
            if isinstance(obj, dict):
                obj = obj.get(key)
            else:
                return None
        return str(obj) if obj not in (None, "") else None

    return None


def extract_id_from_response(pattern: dict, response_body: bytes | None) -> str | None:
    """Extract the new resource_id from a create response body.

    Walks ``response_id_field`` (or ``id_field`` if the override is NULL)
    through the parsed JSON body. Defaults to ``"id"`` when neither is set.
    Dotted keys (e.g. ``data.KDSID``) are supported for nested lookups.

    The optional ``response_id_field`` override exists for apps whose
    request and response field names disagree (e.g. KB: request uses
    ``kbs_id``, response uses ``data.KDSID``).
    """
    if not response_body:
        return None
    try:
        obj = json.loads(response_body)
    except (TypeError, ValueError):
        return None
    field = pattern.get("response_id_field") or pattern.get("id_field") or "id"
    for key in field.split("."):
        if isinstance(obj, dict):
            obj = obj.get(key)
        else:
            return None
    return str(obj) if obj not in (None, "") else None


def _make_continue_response() -> ProcessingResponse:
    """Build a simple CONTINUE response with no mutations."""
    return ProcessingResponse(
        request_headers=HeadersResponse(
            response=CommonResponse(status=CommonResponse.CONTINUE)
        )
    )


def _make_header_value_option(key: str, value: str) -> HeaderValueOption:
    """Build a HeaderValueOption that overwrites or adds a header.

    Populates BOTH ``value`` and ``raw_value`` so we don't trip over
    envoyproxy/envoy#31555 - with
    ``envoy.reloadable_features.send_header_raw_value`` enabled (default in
    recent Envoy versions), mutation_utils.cc reads ``raw_value`` and
    silently produces empty headers if only ``value`` is set.
    """
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
    """Build a CONTINUE HeadersResponse, optionally injecting headers."""
    mutation = None
    if header_mutations:
        mutation = HeaderMutation(set_headers=header_mutations)
    resp = CommonResponse(status=CommonResponse.CONTINUE, header_mutation=mutation)
    hr = HeadersResponse(response=resp)

    if field == "request_headers":
        return ProcessingResponse(request_headers=hr)
    else:
        return ProcessingResponse(response_headers=hr)


def _make_body_continue(body_bytes: bytes | None = None, end_of_stream: bool = True) -> ProcessingResponse:
    """Build a CONTINUE BodyResponse using StreamedResponse.

    Envoy Gateway supports both ``BodyMutation.body`` and
    ``BodyMutation.streamed_response``; we use StreamedResponse here for
    consistency across request/response body phases.
    """
    mutation = None
    if body_bytes is not None:
        mutation = BodyMutation(
            streamed_response=StreamedResponse(body=body_bytes, end_of_stream=end_of_stream)
        )
    else:
        # Pass-through: empty streamed response signals "continue as-is"
        mutation = BodyMutation(
            streamed_response=StreamedResponse(body=b"", end_of_stream=end_of_stream)
        )
    resp = CommonResponse(status=CommonResponse.CONTINUE, body_mutation=mutation)
    return ProcessingResponse(response_body=BodyResponse(response=resp))


def _make_response_headers_buffer() -> ProcessingResponse:
    """
    Respond to response_headers telling Envoy to buffer the response body.

    We use ``mode_override`` on the ProcessingResponse to switch Envoy into
    BUFFERED mode for the response body.  This causes Envoy to collect the
    entire response body and send it as a single ``response_body`` message.
    """
    resp = CommonResponse(status=CommonResponse.CONTINUE)
    return ProcessingResponse(
        response_headers=HeadersResponse(response=resp),
        mode_override=ProcessingMode(
            response_body_mode=ProcessingMode.BUFFERED,
        ),
    )


# ---------------------------------------------------------------------------
# ExternalProcessorServicer
# ---------------------------------------------------------------------------

class ExtProcService(ExternalProcessorServicer):
    """
    Bidirectional streaming ext_proc handler.

    Each gRPC stream corresponds to a single HTTP request/response pair.
    Envoy sends ProcessingRequest messages in order and expects one
    ProcessingResponse for each.
    """

    async def process(self, request_iterator, context):
        """Handle the bidirectional stream for a single HTTP transaction."""

        stream_ctx = self._new_stream_context()

        while True:
            request = await self._read_next_request(request_iterator, stream_ctx)
            if request is None:
                break
            if isinstance(request, ProcessingResponse):
                yield request
                continue

            resp = await self._dispatch_request(request, stream_ctx)
            if resp is not None:
                yield resp

    Process = process

    @staticmethod
    def _new_stream_context() -> dict:
        """Build the per-stream context carried across request/response phases."""
        return {
            "method": "",
            "path": "",
            "query_params": {},
            "user_id": "",
            "tenant_id": "",
            "groups": [],
            "app_name": "",
            "resource_type": "",
            "resource_id": None,
            "pattern": None,
            "create_action": None,   # matched resource_action row when request is a create
            "delete_action": None,   # matched resource_action row when request is a delete
            "need_request_body": False,   # True when we must read request body to get id
            "need_response_body": False,  # True when we must read response body to get id
            "request_body_buffer": b"",
            "response_status": "",
        }

    async def _read_next_request(self, request_iterator, stream_ctx: dict):
        try:
            return await request_iterator.__anext__()
        except StopAsyncIteration:
            return None
        except Exception as exc:
            logger.error(
                "ext_proc: decode error: %s (method=%s path=%s app=%s). "
                "This is likely a proto/wire-format mismatch between Envoy %s "
                "and our simplified proto. Sending CONTINUE and trying next message.",
                exc,
                stream_ctx.get("method"),
                stream_ctx.get("path"),
                stream_ctx.get("app_name"),
                "1.37",
            )
            return _make_continue_response()

    async def _dispatch_request(self, request, stream_ctx: dict) -> ProcessingResponse | None:
        msg_type = request.WhichOneof("request")
        logger.info("ext_proc: received message type: %s", msg_type)

        if msg_type == "request_headers":
            return await self._handle_request_headers(request.request_headers, stream_ctx)
        if msg_type == "response_headers":
            return await self._handle_response_headers(request.response_headers, stream_ctx)
        if msg_type == "request_body":
            return await self._handle_request_body(request.request_body, stream_ctx)
        if msg_type == "response_body":
            return await self._dispatch_response_body(request.response_body, stream_ctx)
        if msg_type == "request_trailers":
            return ProcessingResponse(request_trailers=TrailersResponse())
        if msg_type == "response_trailers":
            return ProcessingResponse(response_trailers=TrailersResponse())

        logger.warning("ext_proc: unknown message type: %s", msg_type)
        return None

    async def _handle_request_body(self, request_body, stream_ctx: dict) -> ProcessingResponse:
        body_data = request_body.body if request_body.body else b""
        if stream_ctx.get("need_request_body"):
            stream_ctx["request_body_buffer"] += body_data
            if request_body.end_of_stream:
                await self._finalize_request_body(stream_ctx)
        return self._make_simple_body_continue("request_body")

    async def _dispatch_response_body(self, response_body, stream_ctx: dict) -> ProcessingResponse:
        if stream_ctx.get("need_response_body"):
            return await self._handle_response_body(response_body, stream_ctx)
        return self._make_simple_body_continue("response_body")

    @staticmethod
    def _make_simple_body_continue(field: str) -> ProcessingResponse:
        body_response = BodyResponse(
            response=CommonResponse(status=CommonResponse.CONTINUE)
        )
        if field == "request_body":
            return ProcessingResponse(request_body=body_response)
        return ProcessingResponse(response_body=body_response)

    # ------------------------------------------------------------------
    # Phase handlers
    # ------------------------------------------------------------------

    async def _handle_request_headers(self, http_headers, ctx: dict) -> ProcessingResponse:
        """
        Process incoming request headers.

        - Match the request to an app + resource_pattern.
        - Classify the request via find_action() (create / read / update /
          delete / list).  Store the matched action on the stream context
          so the response phase can apply the correct ACL side-effects.
        - For delete actions whose id_source is 'body', flag that we need
          the request body so we can extract the id later.
        - For collection GETs: query resource_acl and inject X-Allowed-Ids.
        - Otherwise: continue.
        """
        hdrs = _headers_to_dict(http_headers.headers)
        request_info = self._parse_request_headers(hdrs)
        app_name, pattern, sub_path = _match_path(request_info["path"], request_info["method"])
        resource_type = pattern["resource_type"] if pattern else ""
        resource_id = self._extract_request_resource_id(pattern, sub_path, request_info)
        create_action, delete_action = self._classify_request(
            app_name, pattern, request_info["method"], sub_path,
        )

        self._update_request_context(
            ctx, request_info, app_name, resource_type, pattern, sub_path,
            resource_id, create_action, delete_action,
        )
        self._log_request_context(
            request_info, app_name, resource_type, resource_id, create_action, delete_action,
        )

        if self._is_collection_get(request_info["method"], app_name, resource_type, resource_id, pattern):
            return await self._handle_collection_get(ctx)

        if ctx["need_request_body"]:
            return self._make_request_body_buffer_response()

        return _make_headers_continue("request_headers")

    @staticmethod
    def _parse_request_headers(hdrs: dict[str, str]) -> dict:
        raw_path = hdrs.get(":path", "/")
        parsed = urlparse(raw_path)
        groups_raw = hdrs.get("x-auth-groups", "")
        return {
            "method": hdrs.get(":method", "GET").upper(),
            "path": parsed.path,
            "query_params": parse_qs(parsed.query),
            "user_id": hdrs.get("x-auth-user-id", ""),
            "tenant_id": hdrs.get("x-auth-tenant", ""),
            "groups": [g.strip() for g in groups_raw.split(",") if g.strip()],
        }

    @staticmethod
    def _extract_request_resource_id(
        pattern: dict | None,
        sub_path: str,
        request_info: dict,
    ) -> str | None:
        if pattern is None:
            return None
        return extract_id_from_request(pattern, sub_path, request_info["query_params"], None)

    @staticmethod
    def _classify_request(
        app_name: str,
        pattern: dict | None,
        method: str,
        sub_path: str,
    ) -> tuple[dict | None, dict | None]:
        if pattern is None:
            return None, None
        resource_prefix = pattern["resource_prefix"]
        create_action = find_action(app_name, resource_prefix, method, sub_path, "create")
        delete_action = find_action(app_name, resource_prefix, method, sub_path, "delete")
        return create_action, delete_action

    def _update_request_context(
        self,
        ctx: dict,
        request_info: dict,
        app_name: str,
        resource_type: str,
        pattern: dict | None,
        sub_path: str,
        resource_id: str | None,
        create_action: dict | None,
        delete_action: dict | None,
    ) -> None:
        ctx.update(
            {
                "method": request_info["method"],
                "path": request_info["path"],
                "query_params": request_info["query_params"],
                "user_id": request_info["user_id"],
                "tenant_id": request_info["tenant_id"],
                "groups": request_info["groups"],
                "app_name": app_name,
                "resource_type": resource_type,
                "pattern": pattern,
                "sub_path": sub_path,
                "resource_id": resource_id,
                "create_action": create_action,
                "delete_action": delete_action,
                "need_request_body": self._needs_request_body(
                    delete_action, pattern, resource_id,
                ),
            }
        )

    @staticmethod
    def _needs_request_body(
        delete_action: dict | None,
        pattern: dict | None,
        resource_id: str | None,
    ) -> bool:
        if delete_action is None or pattern is None:
            return False
        return pattern.get("id_source") == "body" and resource_id is None

    @staticmethod
    def _log_request_context(
        request_info: dict,
        app_name: str,
        resource_type: str,
        resource_id: str | None,
        create_action: dict | None,
        delete_action: dict | None,
    ) -> None:
        logger.info(
            "ext_proc request: method=%s path=%s app=%s type=%s id=%s "
            "create_action=%s delete_action=%s user=%s tenant=%s",
            request_info["method"], request_info["path"], app_name,
            resource_type, resource_id,
            create_action["action"] if create_action else None,
            delete_action["action"] if delete_action else None,
            request_info["user_id"], request_info["tenant_id"],
        )

    @staticmethod
    def _is_collection_get(
        method: str,
        app_name: str,
        resource_type: str,
        resource_id: str | None,
        pattern: dict | None,
    ) -> bool:
        has_collection_context = bool(app_name and resource_type and pattern is not None)
        return method == "GET" and has_collection_context and resource_id is None

    async def _handle_collection_get(self, ctx: dict) -> ProcessingResponse:
        if self._is_admin_bypass(ctx["app_name"], ctx["groups"]):
            logger.info(
                "ext_proc: skipping X-Allowed-Ids injection (admin bypass) user=%s groups=%s",
                ctx["user_id"], ctx["groups"],
            )
            return _make_headers_continue("request_headers")

        page = int(ctx["query_params"].get("page", ["1"])[0])
        size = int(ctx["query_params"].get("size", ["20"])[0])
        try:
            return await self._make_allowed_ids_response(ctx, page, size)
        except Exception as exc:
            logger.error("ext_proc: failed to query allowed IDs: %s", exc)
            return _make_headers_continue("request_headers")

    @staticmethod
    def _is_admin_bypass(app_name: str, groups: list[str]) -> bool:
        app_info = apps.get(app_name, {})
        admin_group = app_info.get("admin_group") if app_info else None
        return "admins" in groups or bool(admin_group and admin_group in groups)

    async def _make_allowed_ids_response(
        self,
        ctx: dict,
        page: int,
        size: int,
    ) -> ProcessingResponse:
        allowed_ids, total = await db.get_allowed_resource_ids(
            ctx["tenant_id"], ctx["app_name"], ctx["resource_type"],
            ctx["user_id"], ctx["groups"], page, size,
        )
        logger.info(
            "ext_proc: injecting X-Allowed-Ids count=%d total=%d",
            len(allowed_ids), total,
        )
        return _make_headers_continue(
            "request_headers",
            header_mutations=[
                _make_header_value_option("X-Allowed-Ids", ",".join(allowed_ids)),
                _make_header_value_option("X-Allowed-Total", str(total)),
            ],
        )

    @staticmethod
    def _make_request_body_buffer_response() -> ProcessingResponse:
        return ProcessingResponse(
            request_headers=HeadersResponse(
                response=CommonResponse(status=CommonResponse.CONTINUE)
            ),
            mode_override=ProcessingMode(request_body_mode=ProcessingMode.BUFFERED),
        )

    async def _finalize_request_body(self, ctx: dict) -> None:
        """After the full request body is buffered, extract resource_id.

        Only used for actions whose id_source is 'body' (e.g. a delete
        endpoint that accepts ``{"id": "..."}`` as a POST body).  The
        extracted id is stored on the stream context for use in the
        response phase.
        """
        pattern = ctx.get("pattern")
        if pattern is None:
            return
        body = ctx.get("request_body_buffer") or b""
        rid = extract_id_from_request(
            pattern, ctx.get("sub_path", ""), ctx.get("query_params", {}), body,
        )
        if rid:
            ctx["resource_id"] = rid
            logger.info(
                "ext_proc: resolved resource_id=%s from request body", rid,
            )
        else:
            logger.warning(
                "ext_proc: failed to extract resource_id from request body "
                "(id_field=%s body_len=%d)",
                pattern.get("id_field"), len(body),
            )

    async def _handle_response_headers(self, http_headers, ctx: dict) -> ProcessingResponse:
        """
        Process response headers from the backend.

        - create action whose success_status matches: request response
          body buffering so we can extract the new resource ID.
        - delete action whose success_status matches (or any 2xx when
          success_status is NULL): delete all ACL entries for the
          resource.
        - Otherwise: pass through.
        """
        status_code = self._set_response_status(http_headers, ctx)
        self._log_response_context(ctx)

        if self._is_successful_create(ctx, status_code):
            ctx["need_response_body"] = True
            return _make_response_headers_buffer()

        if self._is_successful_owner_delete(ctx, status_code):
            await self._delete_acl_for_context(ctx)

        return _make_headers_continue("response_headers")

    @staticmethod
    def _set_response_status(http_headers, ctx: dict) -> int:
        hdrs = _headers_to_dict(http_headers.headers)
        status_str = hdrs.get(":status", "200")
        ctx["response_status"] = status_str
        try:
            return int(status_str)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _log_response_context(ctx: dict) -> None:
        create_action = ctx.get("create_action")
        delete_action = ctx.get("delete_action")
        logger.info(
            "ext_proc response: method=%s status=%s app=%s type=%s id=%s "
            "create_action=%s delete_action=%s",
            ctx["method"], ctx["response_status"], ctx["app_name"],
            ctx["resource_type"], ctx["resource_id"],
            create_action["action"] if create_action else None,
            delete_action["action"] if delete_action else None,
        )

    @staticmethod
    def _is_successful_create(ctx: dict, status_code: int) -> bool:
        create_action = ctx.get("create_action")
        has_resource_context = bool(ctx["app_name"] and ctx["resource_type"])
        if create_action is None or not has_resource_context:
            return False
        return _status_matches(status_code, create_action.get("success_status"))

    @staticmethod
    def _is_successful_owner_delete(ctx: dict, status_code: int) -> bool:
        delete_action = ctx.get("delete_action")
        has_resource_context = bool(ctx["app_name"] and ctx["resource_type"])
        if delete_action is None or not has_resource_context:
            return False
        delete_min_perm = delete_action.get("min_permission") or ""
        if delete_min_perm.lower() != "owner":
            return False
        return _status_matches(status_code, delete_action.get("success_status"))

    async def _delete_acl_for_context(self, ctx: dict) -> None:
        app_name = ctx["app_name"]
        resource_type = ctx["resource_type"]
        resource_id = ctx["resource_id"]
        if not resource_id:
            logger.warning(
                "ext_proc: delete matched but resource_id is empty (app=%s type=%s path=%s)",
                app_name, resource_type, ctx.get("path"),
            )
            return
        try:
            deleted = await db.delete_acl_for_resource(app_name, resource_type, resource_id)
            logger.info(
                "ext_proc: DELETE ACL for %s/%s/%s deleted=%s",
                app_name, resource_type, resource_id, deleted,
            )
        except Exception as exc:
            logger.error(
                "ext_proc: failed to delete ACL for %s/%s/%s: %s",
                app_name, resource_type, resource_id, exc,
            )
            await self._queue_pending_delete(ctx, str(exc))

    @staticmethod
    async def _queue_pending_delete(ctx: dict, error: str) -> None:
        try:
            await db.write_pending_acl(
                ctx["tenant_id"], ctx["app_name"], ctx["resource_type"],
                ctx["resource_id"], "user", ctx["user_id"], "", "delete", error,
            )
        except Exception as exc:
            logger.error("ext_proc: failed to queue pending delete: %s", exc)

    async def _handle_response_body(self, http_body, ctx: dict) -> ProcessingResponse:
        """
        Process response body (only received when we requested buffering).

        For matched create actions: parse JSON using the pattern's
        id_field, extract the new resource_id, and write an owner ACL.
        """
        body_bytes = http_body.body if http_body.body else b""

        if not ctx.get("need_response_body"):
            return _make_body_continue(body_bytes)

        resource_id = self._extract_response_resource_id(ctx.get("pattern"), body_bytes)
        if self._has_acl_create_context(ctx, resource_id):
            await self._write_create_acl_plan(ctx, resource_id)
        else:
            self._log_skipped_acl_write(ctx, resource_id)

        return ProcessingResponse(
            response_body=BodyResponse(
                response=CommonResponse(status=CommonResponse.CONTINUE)
            )
        )

    @staticmethod
    def _extract_response_resource_id(pattern: dict | None, body_bytes: bytes) -> str | None:
        if pattern is not None:
            return extract_id_from_response(pattern, body_bytes)
        try:
            body_json = json.loads(body_bytes)
            rid = body_json.get("id") if isinstance(body_json, dict) else None
            return str(rid) if rid not in (None, "") else None
        except (AttributeError, TypeError, ValueError) as exc:
            logger.warning("ext_proc: could not parse response body as JSON: %s", exc)
            return None

    @staticmethod
    def _has_acl_create_context(ctx: dict, resource_id: str | None) -> bool:
        return bool(
            resource_id
            and ctx["app_name"]
            and ctx["resource_type"]
            and ctx["user_id"]
            and ctx["tenant_id"]
        )

    async def _write_create_acl_plan(self, ctx: dict, resource_id: str) -> None:
        for subject_type, subject_id, permission in self._build_create_acl_plan(ctx):
            await self._write_create_acl(ctx, resource_id, subject_type, subject_id, permission)

    @staticmethod
    def _build_create_acl_plan(ctx: dict) -> list[tuple[str, str, str]]:
        pattern = ctx.get("pattern")
        app_info = apps.get(ctx["app_name"], {})
        admin_group = app_info.get("admin_group") if app_info else None
        plan: list[tuple[str, str, str]] = [("user", ctx["user_id"], "owner")]
        if pattern is None:
            return plan
        if pattern.get("share_to_admin_group_on_create") and admin_group:
            plan.append(("group", admin_group, "owner"))
        if pattern.get("share_to_all_users_on_create"):
            plan.append(("group", "all-users", "viewer"))
        return plan

    async def _write_create_acl(
        self,
        ctx: dict,
        resource_id: str,
        subject_type: str,
        subject_id: str,
        permission: str,
    ) -> None:
        try:
            inserted = await db.write_acl(
                ctx["tenant_id"], ctx["app_name"], ctx["resource_type"],
                resource_id, subject_type, subject_id, permission,
            )
            logger.info(
                "ext_proc: wrote ACL %s=%s perm=%s for %s/%s/%s inserted=%s",
                subject_type, subject_id, permission,
                ctx["app_name"], ctx["resource_type"], resource_id, inserted,
            )
        except Exception as exc:
            logger.error(
                "ext_proc: failed to write ACL %s=%s perm=%s for %s/%s/%s: %s",
                subject_type, subject_id, permission,
                ctx["app_name"], ctx["resource_type"], resource_id, exc,
            )
            await self._queue_pending_create(
                ctx, resource_id, subject_type, subject_id, permission, str(exc),
            )

    @staticmethod
    async def _queue_pending_create(
        ctx: dict,
        resource_id: str,
        subject_type: str,
        subject_id: str,
        permission: str,
        error: str,
    ) -> None:
        try:
            await db.write_pending_acl(
                ctx["tenant_id"], ctx["app_name"], ctx["resource_type"], resource_id,
                subject_type, subject_id, permission, "create", error,
            )
        except Exception as exc:
            logger.error("ext_proc: failed to queue pending create: %s", exc)

    @staticmethod
    def _log_skipped_acl_write(ctx: dict, resource_id: str | None) -> None:
        logger.warning(
            "ext_proc: skipping ACL write - missing data: "
            "resource_id=%s app=%s type=%s user=%s tenant=%s",
            resource_id, ctx["app_name"], ctx["resource_type"],
            ctx["user_id"], ctx["tenant_id"],
        )


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

async def serve() -> None:
    """
    Start the gRPC ext_proc server on port 8082.

    Call this once from the FastAPI startup event:
        asyncio.create_task(ext_proc_server.serve())
    """
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
