import json
import sys
import secrets
import asyncio
import argparse
import logging
import weakref
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import (
    TransportSecuritySettings,
    TransportSecurityMiddleware,
)
import mcp.types as types
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
import uvicorn

from app.service import _prepare_for_llm
from app import audit
from app.operations.calls import INLINE_RUNNER
from app.operations.maintenance import MaintenanceRejected
from app.transport.report_feedback import SearchReceiptRegistry
from app.transport.tools import ToolContext, _ok, execute_tool, list_tool_definitions, tool_spec_for

logger = logging.getLogger("server")


class MCPApplication:
    def __init__(self, service=None, *, runtime=None):
        if service is None and runtime is None:
            raise ValueError("service or runtime is required")
        self._service = service
        self.runtime = runtime
        self._session_ids = weakref.WeakKeyDictionary()
        self._search_receipts = SearchReceiptRegistry()
        self.server = Server("memory-server")
        self.server.list_tools()(self.handle_list_tools)
        self.server.call_tool()(self.handle_call_tool)
        self.server.list_resources()(self.handle_list_resources)
        self.server.read_resource()(self.handle_read_resource)

    @property
    def service(self):
        return self._service if self._service is not None else self.runtime.service

    @property
    def audit(self):
        return self.runtime.audit if self.runtime is not None else self.service.audit

    def _audit_context(self):
        try:
            request = self.server.request_context
        except LookupError:
            context = {"source": "mcp", "request_id": "rq_" + secrets.token_hex(8)}
        else:
            session = request.session
            if session not in self._session_ids:
                self._session_ids[session] = "ss_" + secrets.token_hex(8)
            client = session.client_params.clientInfo if session.client_params else None
            context = {"source": "mcp", "request_id": str(request.request_id)[:128],
                       "session_id": self._session_ids[session],
                       "client_name": str(client.name)[:128] if client else None,
                       "client_version": str(client.version)[:128] if client else None}
        # Streamable HTTP requests carry a server-generated HTTP correlation id.
        # It is merged here explicitly because an explicit context wins over the
        # journal's request_audit_context fallback; it never replaces source=mcp
        # or the JSON-RPC request_id.
        transport_context = audit.http_transport_context.get()
        if transport_context:
            context["transport"] = transport_context.get("transport")
            context["http_request_id"] = transport_context.get("http_request_id")
        return context

    async def handle_list_tools(self) -> types.ListToolsResult:
        return types.ListToolsResult(tools=list_tool_definitions())

    def _call_runner(self):
        return self.runtime.calls if self.runtime is not None else INLINE_RUNNER

    async def handle_call_tool(self, name: str, arguments: dict) -> types.CallToolResult:
        # Read the SDK/transport context on the event loop, then carry it as a
        # value into the worker; workers never touch the SDK's async context.
        context = self._audit_context()
        spec = tool_spec_for(name)
        admission = (self.runtime.maintenance.access(write=bool(spec and spec.write))
                     if self.runtime is not None else None)
        tool_context = ToolContext(service=self.service, audit=self.audit,
                                   receipts=self._search_receipts,
                                   runtime=self.runtime, context=context)
        try:
            return await self._call_runner().run(
                lambda: execute_tool(tool_context, name, arguments), admission=admission)
        except MaintenanceRejected as exc:
            data = {"response_code": exc.code, "detail": str(exc)}
            hint = ("Model is not ready. Open /admin/console to configure proxy, view download progress and retry; "
                    "exit maintenance only after the model is ready." if exc.code == "model_not_ready"
                    else "Maintenance is in progress. Retry this operation later.")
            self.audit.record(name, arguments, data, 0.0, response_code=exc.code,
                              context=context)
            return _ok(data, hint)

    async def handle_list_resources(self) -> types.ListResourcesResult:
        return types.ListResourcesResult(resources=[
            types.Resource(
                uri="memories://stats",
                name="Memory Stats",
                description="Storage statistics",
                mimeType="application/json",
            ),
            types.Resource(
                uri="memories://list",
                name="All Memories",
                description="All stored memories (no embeddings)",
                mimeType="application/json",
            ),
        ])

    async def handle_read_resource(self, uri: str) -> types.ReadResourceResult:
        admission = self.runtime.maintenance.access() if self.runtime is not None else None
        return await self._call_runner().run(
            lambda: self._read_resource_sync(uri), admission=admission)

    def _read_resource_sync(self, uri: str) -> types.ReadResourceResult:
        logger.info("Resource read: %s", uri)
        if uri == "memories://stats":
            return types.ReadResourceResult(contents=[
                types.TextResourceContents(uri=uri, mimeType="application/json",
                                           text=json.dumps(self.service.memory_stats(), ensure_ascii=False)),
            ])
        if uri == "memories://list":
            return types.ReadResourceResult(contents=[
                types.TextResourceContents(uri=uri, mimeType="application/json",
                                           text=json.dumps(_prepare_for_llm(self.service.memory_list(limit=1000)),
                                                           ensure_ascii=False, default=str)),
            ])
        raise ValueError(f"Unknown resource: {uri}")

def create_mcp_server(service=None, *, runtime=None):
    return MCPApplication(service, runtime=runtime)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

# Streamable HTTP transport security: loopback-only Host/Origin whitelist.
# The SDK disables DNS-rebinding protection when settings are omitted, so it is
# passed explicitly to both the adapter pre-check and the session manager.
STREAMABLE_HTTP_SECURITY = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=["127.0.0.1:*", "localhost:*"],
    allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
)

# LAN mode (local_only=false) explicitly disables the SDK DNS-rebinding
# Host/Origin protection so any LAN host may reach /mcp. The service has no
# authentication, so this is only safe on a fully trusted network; the config
# UI and docs warn about the exposure.
LAN_HTTP_SECURITY = TransportSecuritySettings(enable_dns_rebinding_protection=False)


def resolve_bind_host(settings) -> str:
    """HTTP bind address for the snapshot: loopback unless local_only is off."""
    return "127.0.0.1" if getattr(settings, "LOCAL_ONLY", True) else "0.0.0.0"

_READ_ME_TEMPLATE_PATH = Path(__file__).with_name("read_me.md")


def _read_me_base(port, scope):
    """Resolve the self-referenced base URL for /read-me.

    Host is fixed to loopback by design; only the port is resolved at runtime so
    a changed --port is reflected. Falls back to the ASGI server port when the
    configured port is missing or 0 (e.g. an ephemeral test port).
    """
    resolved = port
    if not resolved:
        server = scope.get("server") or ()
        if len(server) == 2 and server[1]:
            resolved = server[1]
    if not resolved:
        return None
    return f"http://127.0.0.1:{resolved}"


def create_http_app(runtime, *, port=None, local_only=None):
    """Own runtime lifespan; all transports bind the same explicit service."""
    from contextlib import asynccontextmanager
    from app.transport.admin import create_admin_routes
    import anyio
    if local_only is None:
        local_only = getattr(getattr(runtime, "settings", None), "LOCAL_ONLY", True)
    security_settings = STREAMABLE_HTTP_SECURITY if local_only else LAN_HTTP_SECURITY
    connections = set()
    mcp = create_mcp_server(runtime=runtime)
    sse = SseServerTransport("/messages/")
    security = TransportSecurityMiddleware(security_settings)
    session_manager = StreamableHTTPSessionManager(
        app=mcp.server, stateless=True, json_response=True,
        security_settings=security_settings,
    )

    @asynccontextmanager
    async def lifespan(app):
        try:
            runtime.start(background_model=True)
            # Entered after start() and exited before close() so in-flight work
            # drains before storage/provider are released.
            async with session_manager.run():
                yield
        finally:
            await asyncio.to_thread(runtime.close)

    class SSEEndpoint:
        async def __call__(self, request_scope, receive, send):
            started = finished = False

            async def track_send(message):
                nonlocal started, finished
                await send(message)
                if message["type"] == "http.response.start":
                    started = True
                elif message["type"] == "http.response.body" and not message.get("more_body", False):
                    finished = True

            # Business handlers are synchronous within their async adapters:
            # cancellation cannot interrupt a store/provider operation.
            with anyio.CancelScope() as scope:
                connections.add(scope)
                try:
                    async with sse.connect_sse(request_scope, receive, track_send) as streams:
                        await mcp.server.run(streams[0], streams[1], mcp.server.create_initialization_options())
                finally:
                    connections.discard(scope)
            # The transport already sent the headers. End its body on shutdown;
            # returning another Response here would send duplicate headers.
            if started and not finished:
                await send({"type": "http.response.body", "body": b"", "more_body": False})

    class StreamableHTTPEndpoint:
        """ASGI adapter for the stateless Streamable HTTP transport."""

        async def __call__(self, request_scope, receive, send):
            request = Request(request_scope, receive)
            # Security is validated for every method, including the ones that
            # end in 405, so GET/DELETE cannot bypass the Host/Origin whitelist.
            error = await security.validate_request(request, is_post=False)
            if error is not None:
                await error(request_scope, receive, send)
                return
            if request.method != "POST":
                if request.method == "HEAD":
                    await send({"type": "http.response.start", "status": 405,
                                "headers": [(b"allow", b"POST")]})
                    await send({"type": "http.response.body", "body": b""})
                else:
                    response = Response("Method Not Allowed", status_code=405,
                                        headers={"Allow": "POST"})
                    await response(request_scope, receive, send)
                return
            # Per-request correlation id for audit; a fresh dict per request so
            # concurrent requests never share or mutate one another's context.
            request_id = (request_scope.get("state") or {}).get("audit_request_id")
            if not request_id:
                request_id = "rq_" + secrets.token_hex(8)
            token = audit.http_transport_context.set(
                {"transport": "streamable_http", "http_request_id": request_id})
            try:
                await session_manager.handle_request(request_scope, receive, send)
            finally:
                audit.http_transport_context.reset(token)

    class ReadMeEndpoint:
        """Static, LLM-facing configuration page with a self-referenced base URL."""

        async def __call__(self, request_scope, receive, send):
            base = _read_me_base(port, request_scope)
            if base is None:
                response = Response("cannot determine service port for /read-me",
                                    status_code=500)
                await response(request_scope, receive, send)
                return
            try:
                template = _READ_ME_TEMPLATE_PATH.read_text(encoding="utf-8")
            except OSError:
                logger.exception("failed to read /read-me template")
                response = Response("read-me template unavailable", status_code=500)
                await response(request_scope, receive, send)
                return
            response = Response(template.replace("{base}", base),
                                media_type="text/markdown; charset=utf-8")
            await response(request_scope, receive, send)

    async def root_redirect(request):
        # Browser navigation advertises HTML; CLI/MCP discovery defaults to the guide.
        accepts_html = any(part.split(";", 1)[0].strip().lower() in
                           ("text/html", "application/xhtml+xml")
                           for part in request.headers.get("accept", "").split(","))
        browser = request.headers.get("sec-fetch-mode", "").lower() == "navigate" or accepts_html
        return RedirectResponse("/admin/console" if browser else "/readme", status_code=307,
                                headers={"Vary": "Accept, Sec-Fetch-Mode", "Cache-Control": "no-store"})

    app = Starlette(routes=[Route("/", root_redirect, methods=["GET"]),
                            Route("/sse", SSEEndpoint()),
                            Route("/mcp", StreamableHTTPEndpoint()),
                            Route("/readme", ReadMeEndpoint(), methods=["GET"]),
                            Route("/read-me", ReadMeEndpoint(), methods=["GET"]),
                            Mount("/messages/", app=sse.handle_post_message)] +
                    create_admin_routes(runtime=runtime), lifespan=lifespan)
    app.state.runtime = runtime

    def close_connections():
        for scope in tuple(connections):
            scope.cancel()
    app.state.close_connections = close_connections

    class AdmissionMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http" and runtime.state != "running":
                response = Response(json.dumps({"detail": "service is closing"}), status_code=503,
                                    media_type="application/json")
                await response(scope, receive, send)
            else:
                if scope["type"] == "http":
                    request_id = "rq_" + secrets.token_hex(8)
                    scope.setdefault("state", {})["audit_request_id"] = request_id
                    token = audit.request_audit_context.set(
                        {"source": "admin_http", "request_id": request_id})
                    try:
                        await self.app(scope, receive, send)
                    finally:
                        audit.request_audit_context.reset(token)
                else:
                    await self.app(scope, receive, send)
    app.add_middleware(AdmissionMiddleware)
    return app


async def serve_stdio(runtime):
    import signal
    import os
    import stat

    async def stdin_lines(reader):
        # Chunked reads avoid StreamReader.readline's artificial line limit.
        pending = b""
        while chunk := await reader.read(65536):
            pending += chunk
            lines = pending.split(b"\n")
            pending = lines.pop()
            for line in lines:
                yield line.decode("utf-8", errors="replace")
        if pending:
            yield pending.decode("utf-8", errors="replace")

    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    previous_handler = signal.getsignal(signal.SIGTERM)
    signal_installed = False
    terminated = False
    input_transport = None

    def terminate():
        nonlocal terminated
        terminated = True
        runtime.request_shutdown()
        task.cancel()

    try:
        try:
            loop.add_signal_handler(signal.SIGTERM, terminate)
            signal_installed = True
        except (NotImplementedError, RuntimeError):
            pass  # Non-main-thread/platform embedding; caller owns signals.
        runtime.start()
        mcp = create_mcp_server(runtime=runtime)
        stdin = None
        mode = os.fstat(sys.stdin.fileno()).st_mode
        if stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode) or sys.stdin.isatty():
            # Native pipe reads are cancellable; the SDK's default threaded
            # readline waits indefinitely when a client keeps stdin open.
            reader = asyncio.StreamReader()
            input_transport, _ = await loop.connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)
            stdin = stdin_lines(reader)
        async with stdio_server(stdin=stdin) as (read_stream, write_stream):
            await mcp.server.run(read_stream, write_stream, mcp.server.create_initialization_options())
    except asyncio.CancelledError:
        if not terminated:
            raise
    finally:
        try:
            await asyncio.to_thread(runtime.close)
        finally:
            if input_transport is not None:
                input_transport.close()
            if signal_installed:
                loop.remove_signal_handler(signal.SIGTERM)
                signal.signal(signal.SIGTERM, previous_handler)


async def main(argv=None):
    import os
    from app.config.settings import Settings
    from app.runtime import create_runtime
    from app.config.configuration import activate_environment
    parser = argparse.ArgumentParser(description="Memory MCP Server")
    parser.add_argument("--data-dir", help="Data directory; defaults to the per-user OS data directory "
                                          "and contains config.jsonc")
    parser.add_argument("--model-dir", help="Model root directory; defaults to <data-dir>/models. "
                                           "Relative paths resolve against the project root")
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument("--stdio", action="store_true", help="Use subprocess stdin/stdout instead of HTTP")
    transport.add_argument("--port", type=int, default=None,
                        help="Port for HTTP mode (legacy SSE + Streamable HTTP)")
    args = parser.parse_args(argv)
    if args.port is not None and not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    try:
        settings = Settings.from_config(args.data_dir, model_dir=args.model_dir, create=True)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    activate_environment(settings.environment)
    runtime = create_runtime(settings)
    if args.stdio:
        await serve_stdio(runtime)
    else:
        port = settings.PORT if args.port is None else args.port
        host = resolve_bind_host(settings)
        app = create_http_app(runtime, port=port, local_only=settings.LOCAL_ONLY)
        logger.info("HTTP listening on %s:%s (%s)", host, port,
                    "local-only" if settings.LOCAL_ONLY else "LAN-accessible")
        config = uvicorn.Config(app, host=host, port=port, log_level="info")
        class GracefulServer(uvicorn.Server):
            async def shutdown(self, sockets=None):
                runtime.request_shutdown(restart=runtime.restart_requested)
                app.state.close_connections()
                await super().shutdown(sockets=sockets)

        uv_server = GracefulServer(config)
        runtime.shutdown_callback = lambda: setattr(uv_server, "should_exit", True)
        try:
            await uv_server.serve()
        finally:
            await asyncio.to_thread(runtime.close)
    if runtime.restart_requested:
        os.execv(sys.executable, [sys.executable, "-m", "app.server"] +
                 (sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    asyncio.run(main())
