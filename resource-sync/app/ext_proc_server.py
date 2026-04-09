"""
gRPC ext_proc server for Envoy External Processing on port 8082.

Implements the Envoy ext_proc v3 bidirectional streaming protocol to
intercept HTTP requests and responses flowing through Envoy Gateway.

Request phase (request_headers):
  - Collection GET: query resource_acl for allowed IDs, inject
    X-Allowed-Ids and X-Allowed-Total headers.
  - Other methods: store context for response phase, continue.

Response phase (response_headers):
  - POST + 201: request response body buffering (BUFFERED mode).
  - DELETE + 2xx: delete all ACL entries for the resource.
  - Otherwise: pass through.

Response body phase (response_body, only for POST + 201):
  - Parse JSON body, extract "id" field.
  - Write resource_acl with permission=owner.
  - On failure: queue to pending_acl for retry.
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
resource_patterns: list[dict] = []        # [{app_name, resource_prefix, resource_type}]


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


def _match_path(path: str) -> tuple[str, str, str | None]:
    """
    Match a request path against loaded apps + resource_patterns.

    Returns (app_name, resource_type, resource_id_or_None).
    resource_id is None when the path is a collection endpoint (no trailing ID).

    Match logic:
      1. Find the app whose path_prefix is a prefix of the full path.
      2. Strip the app path_prefix, then match the remaining path against
         resource_patterns by resource_prefix.
      3. If there is a path segment after the resource_prefix, treat it
         as the resource_id.
    """
    for app_name, app_info in apps.items():
        prefix = app_info["path_prefix"]
        if not path.startswith(prefix):
            continue

        # Strip the app-level prefix to get the sub-path
        sub_path = path[len(prefix):]
        if sub_path and not sub_path.startswith("/"):
            sub_path = "/" + sub_path

        for rp in resource_patterns:
            if rp["app_name"] != app_name:
                continue
            rp_prefix = rp["resource_prefix"]
            if not sub_path.startswith(rp_prefix):
                continue

            remainder = sub_path[len(rp_prefix):].strip("/")
            resource_id = remainder.split("/")[0] if remainder else None
            return app_name, rp["resource_type"], resource_id

    return "", "", None


def _make_continue_response() -> ProcessingResponse:
    """Build a simple CONTINUE response with no mutations."""
    return ProcessingResponse(
        request_headers=HeadersResponse(
            response=CommonResponse(status=CommonResponse.CONTINUE)
        )
    )


def _make_header_value_option(key: str, value: str) -> HeaderValueOption:
    """Build a HeaderValueOption that overwrites or adds a header."""
    return HeaderValueOption(
        header=HeaderValue(key=key, value=value),
        append_action=0,  # APPEND_IF_EXISTS_OR_ADD
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
    """Build a CONTINUE BodyResponse using StreamedResponse (required by AgentGateway).

    AgentGateway only supports BodyMutation.StreamedResponse — standard
    BodyMutation.body is silently dropped.  See agentgateway#724.
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
            "user_id": "",
            "tenant_id": "",
            "groups": [],
            "app_name": "",
            "resource_type": "",
            "resource_id": None,
            "need_response_body": False,
            "response_status": "",
        }

        async for request in request_iterator:
            msg_type = request.WhichOneof("request")
            logger.debug("ext_proc: received message type: %s", msg_type)

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
                # Pass through request body using StreamedResponse (AgentGateway requirement)
                rb = request.request_body
                body_data = rb.body if rb.body else b""
                eos = rb.end_of_stream
                yield ProcessingResponse(
                    request_body=BodyResponse(
                        response=CommonResponse(
                            status=CommonResponse.CONTINUE,
                            body_mutation=BodyMutation(
                                streamed_response=StreamedResponse(body=body_data, end_of_stream=eos)
                            ),
                        )
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
                    # Pass through response body using StreamedResponse
                    yield ProcessingResponse(
                        response_body=BodyResponse(
                            response=CommonResponse(
                                status=CommonResponse.CONTINUE,
                                body_mutation=BodyMutation(
                                    streamed_response=StreamedResponse(body=body_data, end_of_stream=eos)
                                ),
                            )
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

        For collection GETs: query resource_acl and inject X-Allowed-Ids.
        For all others: store context and continue.
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

        app_name, resource_type, resource_id = _match_path(path)

        # Populate stream context for response phase
        ctx["method"] = method
        ctx["path"] = path
        ctx["user_id"] = user_id
        ctx["tenant_id"] = tenant_id
        ctx["groups"] = groups
        ctx["app_name"] = app_name
        ctx["resource_type"] = resource_type
        ctx["resource_id"] = resource_id

        logger.info(
            "ext_proc request: method=%s path=%s app=%s type=%s id=%s user=%s tenant=%s",
            method, path, app_name, resource_type, resource_id, user_id, tenant_id,
        )

        # Collection GET — path matches resource_pattern prefix with no resource_id
        if method == "GET" and app_name and resource_type and resource_id is None:
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
                return _make_headers_continue(
                    "request_headers",
                    header_mutations=[
                        _make_header_value_option("X-Allowed-Ids", ids_str),
                        _make_header_value_option("X-Allowed-Total", str(total)),
                    ],
                )
            except Exception as exc:
                logger.error("ext_proc: failed to query allowed IDs: %s", exc)
                # On failure, continue without injection — the backend can
                # degrade gracefully.
                return _make_headers_continue("request_headers")

        # Non-collection request — just continue
        return _make_headers_continue("request_headers")

    async def _handle_response_headers(self, http_headers, ctx: dict) -> ProcessingResponse:
        """
        Process response headers from the backend.

        - POST + 201: request body buffering so we can extract the resource ID.
        - DELETE + 2xx: delete all ACL entries for the resource.
        - Otherwise: pass through.
        """
        hdrs = _headers_to_dict(http_headers.headers)
        status_str = hdrs.get(":status", "200")
        ctx["response_status"] = status_str

        method = ctx["method"]
        app_name = ctx["app_name"]
        resource_type = ctx["resource_type"]
        resource_id = ctx["resource_id"]

        logger.info(
            "ext_proc response: method=%s status=%s app=%s type=%s id=%s",
            method, status_str, app_name, resource_type, resource_id,
        )

        # POST + 201: flag that we want to process response body chunks
        # to extract the new resource ID for ACL creation.
        # Note: In streaming mode, body arrives as chunks, not buffered.
        if method == "POST" and status_str == "201" and app_name and resource_type:
            ctx["need_response_body"] = True

        # DELETE + 2xx: delete all ACL entries for this resource
        if method == "DELETE" and status_str.startswith("2") and resource_id:
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

        For POST + 201: parse JSON, extract "id", write owner ACL.
        """
        body_bytes = http_body.body if http_body.body else b""

        if not ctx.get("need_response_body"):
            return _make_body_continue(body_bytes)

        app_name = ctx["app_name"]
        resource_type = ctx["resource_type"]
        user_id = ctx["user_id"]
        tenant_id = ctx["tenant_id"]

        # Try to parse the JSON response body and extract the resource ID
        resource_id = None
        try:
            body_json = json.loads(body_bytes)
            resource_id = str(body_json.get("id", ""))
        except (json.JSONDecodeError, AttributeError) as exc:
            logger.warning(
                "ext_proc: could not parse response body as JSON: %s", exc,
            )

        if resource_id and app_name and resource_type and user_id and tenant_id:
            try:
                inserted = await db.write_acl(
                    tenant_id, app_name, resource_type, resource_id,
                    "user", user_id, "owner",
                )
                logger.info(
                    "ext_proc: wrote owner ACL for %s/%s/%s user=%s inserted=%s",
                    app_name, resource_type, resource_id, user_id, inserted,
                )
            except Exception as exc:
                logger.error(
                    "ext_proc: failed to write ACL for %s/%s/%s: %s",
                    app_name, resource_type, resource_id, exc,
                )
                # Queue for retry via pending_acl
                try:
                    await db.write_pending_acl(
                        tenant_id, app_name, resource_type, resource_id,
                        "user", user_id, "owner", "create", str(exc),
                    )
                except Exception as pexc:
                    logger.error("ext_proc: failed to queue pending create: %s", pexc)
        else:
            logger.warning(
                "ext_proc: skipping ACL write — missing data: "
                "resource_id=%s app=%s type=%s user=%s tenant=%s",
                resource_id, app_name, resource_type, user_id, tenant_id,
            )

        # Always pass through the original body unchanged
        return _make_body_continue(body_bytes)


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
        server = grpc.aio.server()
        add_ExternalProcessorServicer_to_server(ExtProcService(), server)
        listen_addr = "[::]:8082"
        server.add_insecure_port(listen_addr)
        await server.start()
        logger.info("gRPC ext_proc server listening on %s", listen_addr)
        await server.wait_for_termination()
    except Exception as exc:
        logger.error("gRPC ext_proc server failed: %s", exc, exc_info=True)
        raise
