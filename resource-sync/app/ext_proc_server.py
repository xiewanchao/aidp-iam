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

# Generated stubs — built during Docker image build:
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
    {"action": "create", "method": "POST",   "path_suffix": None,    "success_status": 201, "min_permission": "none"},
    {"action": "list",   "method": "GET",    "path_suffix": None,    "success_status": None, "min_permission": "none"},
    {"action": "read",   "method": "GET",    "path_suffix": "/{id}", "success_status": None, "min_permission": "viewer"},
    {"action": "update", "method": "PUT",    "path_suffix": "/{id}", "success_status": None, "min_permission": "contributor"},
    {"action": "update", "method": "PATCH",  "path_suffix": "/{id}", "success_status": None, "min_permission": "contributor"},
    {"action": "delete", "method": "DELETE", "path_suffix": "/{id}", "success_status": 200, "min_permission": "owner"},
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
            if rp_len > best_len or (
                rp_len == best_len and is_specific and not best_method_specific
            ):
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

    candidates = [
        a for a in resource_actions
        if a["app_name"] == app_name
        and a["resource_prefix"] == resource_prefix
        and a["method"] == method
        and (action_type is None or a["action"] == action_type)
    ]

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
    (each placeholder matches exactly one path segment). Falls back to literal
    equality / endswith when no placeholder is present.

    Examples:
        _suffix_matches_segments("/s1/replay",            "/{id}/replay")       -> True
        _suffix_matches_segments("/s1/turns/t/feedback",  "/{id}/turns/{t_id}/feedback") -> True
        _suffix_matches_segments("/add",                  "/add")              -> True
    """
    if "{" not in suffix:
        return sub_path == suffix or sub_path.endswith(suffix)
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
        except (json.JSONDecodeError, TypeError, ValueError):
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
    except (json.JSONDecodeError, TypeError, ValueError):
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
    envoyproxy/envoy#31555 — with
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

    async def Process(self, request_iterator, context):
        """Handle the bidirectional stream for a single HTTP transaction."""

        # Per-stream context carried across request -> response phases.
        stream_ctx: dict = {
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

        while True:
            try:
                request = await request_iterator.__anext__()
            except StopAsyncIteration:
                break
            except Exception as e:
                logger.error("ext_proc: decode error: %s (method=%s path=%s app=%s). "
                             "This is likely a proto/wire-format mismatch between Envoy %s and our simplified proto. "
                             "Sending CONTINUE and trying next message.",
                             e, stream_ctx.get("method"), stream_ctx.get("path"),
                             stream_ctx.get("app_name"), "1.37")
                # After a decode failure we still must yield a response so
                # Envoy doesn't hang.  For create/delete requests we lose
                # the response-body interception, so ACL won't be auto-written.
                # The pending_acl retry worker or manual ACL API can compensate.
                yield _make_continue_response()
                continue
            msg_type = request.WhichOneof("request")
            logger.info("ext_proc: received message type: %s", msg_type)

            if msg_type == "request_headers":
                resp = await self._handle_request_headers(
                    request.request_headers, stream_ctx
                )
                yield resp

            elif msg_type == "response_headers":
                resp = await self._handle_response_headers(
                    request.response_headers, stream_ctx
                )
                yield resp

            elif msg_type == "request_body":
                rb = request.request_body
                body_data = rb.body if rb.body else b""
                eos = rb.end_of_stream

                if stream_ctx.get("need_request_body"):
                    stream_ctx["request_body_buffer"] += body_data
                    if eos:
                        await self._finalize_request_body(stream_ctx)

                # Simple CONTINUE without body mutation — pass through as-is
                yield ProcessingResponse(
                    request_body=BodyResponse(
                        response=CommonResponse(status=CommonResponse.CONTINUE)
                    )
                )

            elif msg_type == "response_body":
                rb = request.response_body
                body_data = rb.body if rb.body else b""
                eos = rb.end_of_stream
                if stream_ctx.get("need_response_body"):
                    resp = await self._handle_response_body(rb, stream_ctx)
                    yield resp
                else:
                    # Simple CONTINUE — pass through response body as-is
                    yield ProcessingResponse(
                        response_body=BodyResponse(
                            response=CommonResponse(status=CommonResponse.CONTINUE)
                        )
                    )

            elif msg_type == "request_trailers":
                yield ProcessingResponse(request_trailers=TrailersResponse())

            elif msg_type == "response_trailers":
                yield ProcessingResponse(response_trailers=TrailersResponse())

            else:
                logger.warning("ext_proc: unknown message type: %s", msg_type)

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

        method = hdrs.get(":method", "GET").upper()
        raw_path = hdrs.get(":path", "/")
        parsed = urlparse(raw_path)
        path = parsed.path
        query_params = parse_qs(parsed.query)

        user_id = hdrs.get("x-auth-user-id", "")
        tenant_id = hdrs.get("x-auth-tenant", "")
        groups_raw = hdrs.get("x-auth-groups", "")
        groups = [g.strip() for g in groups_raw.split(",") if g.strip()]

        app_name, pattern, sub_path = _match_path(path, method)
        resource_type = pattern["resource_type"] if pattern else ""

        # Populate stream context for response phase
        ctx["method"] = method
        ctx["path"] = path
        ctx["query_params"] = query_params
        ctx["user_id"] = user_id
        ctx["tenant_id"] = tenant_id
        ctx["groups"] = groups
        ctx["app_name"] = app_name
        ctx["resource_type"] = resource_type
        ctx["pattern"] = pattern
        # sub_path here is the path with the app prefix stripped; the
        # find_action helper expects a path that still starts with the
        # resource_prefix so we can feed it sub_path directly.
        ctx["sub_path"] = sub_path

        # Pre-compute an id from path/query so the response phase can
        # use it even for delete endpoints without needing the body.
        resource_id = None
        if pattern is not None:
            resource_id = extract_id_from_request(pattern, sub_path, query_params, None)
        ctx["resource_id"] = resource_id

        # Classify the request against resource_actions / DEFAULT_ACTIONS
        create_action = None
        delete_action = None
        if pattern is not None:
            create_action = find_action(
                app_name, pattern["resource_prefix"], method, sub_path, "create",
            )
            delete_action = find_action(
                app_name, pattern["resource_prefix"], method, sub_path, "delete",
            )
        ctx["create_action"] = create_action
        ctx["delete_action"] = delete_action

        # If this is a delete whose id_source requires the request body,
        # switch the processing mode to buffer the request body so we
        # can parse the id before the response arrives.
        need_request_body = False
        if delete_action is not None and pattern is not None:
            if pattern.get("id_source") == "body" and resource_id is None:
                need_request_body = True
        ctx["need_request_body"] = need_request_body

        logger.info(
            "ext_proc request: method=%s path=%s app=%s type=%s id=%s "
            "create_action=%s delete_action=%s user=%s tenant=%s",
            method, path, app_name, resource_type, resource_id,
            create_action["action"] if create_action else None,
            delete_action["action"] if delete_action else None,
            user_id, tenant_id,
        )

        # Collection GET — path matches resource_pattern prefix with no resource_id.
        # We still gate on resource_id being None so that item GETs don't trigger
        # the list-injection logic.
        if method == "GET" and app_name and resource_type and resource_id is None and pattern is not None:
            # admins / app-admins bypass: these groups can see everything, so
            # we skip injection entirely. Absence of the X-Allowed-Ids header
            # on the backend side is the contractual signal for "no filter".
            app_info = apps.get(app_name, {})
            admin_group = app_info.get("admin_group") if app_info else None
            if "admins" in groups or (admin_group and admin_group in groups):
                logger.info(
                    "ext_proc: skipping X-Allowed-Ids injection (admin bypass) "
                    "user=%s groups=%s", user_id, groups,
                )
                return _make_headers_continue("request_headers")

            page = int(query_params.get("page", ["1"])[0])
            size = int(query_params.get("size", ["20"])[0])

            try:
                allowed_ids, total = await db.get_allowed_resource_ids(
                    tenant_id, app_name, resource_type, user_id, groups, page, size,
                )
                ids_str = ",".join(allowed_ids)
                logger.info(
                    "ext_proc: injecting X-Allowed-Ids count=%d total=%d",
                    len(allowed_ids), total,
                )
                resp = _make_headers_continue(
                    "request_headers",
                    header_mutations=[
                        _make_header_value_option("X-Allowed-Ids", ids_str),
                        _make_header_value_option("X-Allowed-Total", str(total)),
                    ],
                )
                return resp
            except Exception as exc:
                logger.error("ext_proc: failed to query allowed IDs: %s", exc)
                # On failure, continue without injection — the backend can
                # degrade gracefully.
                return _make_headers_continue("request_headers")

        # When we need to parse the request body to extract a resource_id,
        # ask Envoy to deliver the full buffered body to us.
        if need_request_body:
            return ProcessingResponse(
                request_headers=HeadersResponse(
                    response=CommonResponse(status=CommonResponse.CONTINUE)
                ),
                mode_override=ProcessingMode(
                    request_body_mode=ProcessingMode.BUFFERED,
                ),
            )

        # Non-collection request — just continue
        return _make_headers_continue("request_headers")

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
        hdrs = _headers_to_dict(http_headers.headers)
        status_str = hdrs.get(":status", "200")
        ctx["response_status"] = status_str

        try:
            status_code = int(status_str)
        except (TypeError, ValueError):
            status_code = 0

        method = ctx["method"]
        app_name = ctx["app_name"]
        resource_type = ctx["resource_type"]
        resource_id = ctx["resource_id"]
        create_action = ctx.get("create_action")
        delete_action = ctx.get("delete_action")

        logger.info(
            "ext_proc response: method=%s status=%s app=%s type=%s id=%s "
            "create_action=%s delete_action=%s",
            method, status_str, app_name, resource_type, resource_id,
            create_action["action"] if create_action else None,
            delete_action["action"] if delete_action else None,
        )

        # --- Create action -----------------------------------------------
        if (
            create_action is not None
            and app_name
            and resource_type
            and _status_matches(status_code, create_action.get("success_status"))
        ):
            ctx["need_response_body"] = True
            return _make_response_headers_buffer()

        # --- Delete action -----------------------------------------------
        if (
            delete_action is not None
            and app_name
            and resource_type
            and _status_matches(status_code, delete_action.get("success_status"))
        ):
            if not resource_id:
                logger.warning(
                    "ext_proc: delete matched but resource_id is empty "
                    "(app=%s type=%s path=%s)",
                    app_name, resource_type, ctx.get("path"),
                )
            else:
                try:
                    deleted = await db.delete_acl_for_resource(
                        app_name, resource_type, resource_id,
                    )
                    logger.info(
                        "ext_proc: DELETE ACL for %s/%s/%s deleted=%s",
                        app_name, resource_type, resource_id, deleted,
                    )
                except Exception as exc:
                    logger.error(
                        "ext_proc: failed to delete ACL for %s/%s/%s: %s",
                        app_name, resource_type, resource_id, exc,
                    )
                    # Queue for retry
                    try:
                        await db.write_pending_acl(
                            ctx["tenant_id"], app_name, resource_type, resource_id,
                            "user", ctx["user_id"], "", "delete", str(exc),
                        )
                    except Exception as pexc:
                        logger.error("ext_proc: failed to queue pending delete: %s", pexc)

        return _make_headers_continue("response_headers")

    async def _handle_response_body(self, http_body, ctx: dict) -> ProcessingResponse:
        """
        Process response body (only received when we requested buffering).

        For matched create actions: parse JSON using the pattern's
        id_field, extract the new resource_id, and write an owner ACL.
        """
        body_bytes = http_body.body if http_body.body else b""

        if not ctx.get("need_response_body"):
            return _make_body_continue(body_bytes)

        app_name = ctx["app_name"]
        resource_type = ctx["resource_type"]
        user_id = ctx["user_id"]
        tenant_id = ctx["tenant_id"]
        pattern = ctx.get("pattern")

        # Extract the resource_id using the pattern's id_field.  Fall
        # back to a plain top-level "id" lookup when no pattern is
        # available (should not normally happen since create_action is
        # gated on a matched pattern).
        resource_id: str | None = None
        if pattern is not None:
            resource_id = extract_id_from_response(pattern, body_bytes)
        else:
            try:
                body_json = json.loads(body_bytes)
                rid = body_json.get("id") if isinstance(body_json, dict) else None
                resource_id = str(rid) if rid not in (None, "") else None
            except (json.JSONDecodeError, AttributeError, TypeError, ValueError) as exc:
                logger.warning(
                    "ext_proc: could not parse response body as JSON: %s", exc,
                )

        if resource_id and app_name and resource_type and user_id and tenant_id:
            app_info = apps.get(app_name, {})
            admin_group = app_info.get("admin_group") if app_info else None

            # Build the 3-step ACL plan:
            # 1) creator owner   (always)
            # 2) admin_group owner   (if pattern.share_to_admin_group_on_create and app.admin_group present)
            # 3) all-users viewer    (if pattern.share_to_all_users_on_create)
            plan: list[tuple[str, str, str]] = [("user", user_id, "owner")]
            if pattern is not None:
                if pattern.get("share_to_admin_group_on_create") and admin_group:
                    plan.append(("group", admin_group, "owner"))
                if pattern.get("share_to_all_users_on_create"):
                    plan.append(("group", "all-users", "viewer"))

            for subject_type, subject_id, permission in plan:
                try:
                    inserted = await db.write_acl(
                        tenant_id, app_name, resource_type, resource_id,
                        subject_type, subject_id, permission,
                    )
                    logger.info(
                        "ext_proc: wrote ACL %s=%s perm=%s for %s/%s/%s inserted=%s",
                        subject_type, subject_id, permission,
                        app_name, resource_type, resource_id, inserted,
                    )
                except Exception as exc:
                    logger.error(
                        "ext_proc: failed to write ACL %s=%s perm=%s for %s/%s/%s: %s",
                        subject_type, subject_id, permission,
                        app_name, resource_type, resource_id, exc,
                    )
                    try:
                        await db.write_pending_acl(
                            tenant_id, app_name, resource_type, resource_id,
                            subject_type, subject_id, permission, "create", str(exc),
                        )
                    except Exception as pexc:
                        logger.error("ext_proc: failed to queue pending create: %s", pexc)
        else:
            logger.warning(
                "ext_proc: skipping ACL write — missing data: "
                "resource_id=%s app=%s type=%s user=%s tenant=%s",
                resource_id, app_name, resource_type, user_id, tenant_id,
            )

        # Pass through the original body unchanged — no mutation
        return ProcessingResponse(
            response_body=BodyResponse(
                response=CommonResponse(status=CommonResponse.CONTINUE)
            )
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
