"""Streamable HTTP (/mcp) and /read-me regressions."""
import asyncio
import contextlib
import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.runtime import Runtime
from app.config.configuration import Configuration
from app.config.settings import Settings
from app.server import (_read_me_base, create_http_app, HTTP_BIND_HOSTS,
                        resolve_bind_host, resolve_http_host)
from tests.conftest import FakeProvider

HOST = "127.0.0.1:1314"
BASE = "http://" + HOST


def _settings(tmp_path):
    return Settings.from_env({
        "MEMORY_STORAGE": "dual",
        "MEMORY_DATA_DIR": str(tmp_path),
        "MEMORY_STORAGE_PATH": str(tmp_path / "test.db"),
        "MEMORY_AUDIT_PATH": str(tmp_path / "audit.jsonl"),
        "EMBED_PRELOAD": "false",
    })


def _runtime(tmp_path):
    return Runtime(_settings(tmp_path), provider_factory=lambda **_: FakeProvider(),
                   configure_logging=False)


@contextlib.asynccontextmanager
async def _running_app(tmp_path, port=1314, local_only=None):
    rt = _runtime(tmp_path)
    app = create_http_app(rt, port=port, local_only=local_only)
    async with app.router.lifespan_context(app):
        yield rt, app


def _factory(app):
    def factory(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 headers=headers, timeout=timeout, auth=auth)
    return factory


@contextlib.asynccontextmanager
async def _session(streams):
    read_stream, write_stream, _ = streams
    async with ClientSession(read_stream, write_stream,
                             read_timeout_seconds=timedelta(seconds=5)) as session:
        yield session


def _audit_events(tmp_path, tool):
    path = Path(tmp_path / "audit.jsonl")
    events = [json.loads(line) for line in path.read_text().splitlines()]
    return [event for event in events if event["tool"] == tool]


def test_streamable_http_protocol(tmp_path):
    async def exercise():
        async with _running_app(tmp_path) as (rt, app):
            async with streamablehttp_client(BASE + "/mcp",
                                             httpx_client_factory=_factory(app)) as streams:
                async with _session(streams) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    assert len(tools.tools) == 11
                    result = await session.call_tool("stats", {})
                    assert not result.isError
                    assert json.loads(result.content[0].text)["total"] == 0
    asyncio.run(exercise())
    events = _audit_events(tmp_path, "stats")
    assert events
    context = events[-1]["context"]
    assert context["source"] == "mcp"
    assert context["transport"] == "streamable_http"
    assert context["http_request_id"].startswith("rq_")
    # stateless: no cross-request clientInfo is claimed.
    assert context["client_name"] is None


def test_streamable_http_concurrent_clients_correlate_separately(tmp_path):
    async def exercise():
        async with _running_app(tmp_path) as (rt, app):
            factory = _factory(app)
            async with streamablehttp_client(BASE + "/mcp", httpx_client_factory=factory) as a, \
                    streamablehttp_client(BASE + "/mcp", httpx_client_factory=factory) as b:
                async with _session(a) as one, _session(b) as two:
                    await one.initialize()
                    await two.initialize()
                    # Both sessions issue JSON-RPC id 1 for the first call.
                    await asyncio.gather(one.call_tool("stats", {}),
                                         two.call_tool("stats", {}))
    asyncio.run(exercise())
    events = _audit_events(tmp_path, "stats")
    assert len(events) >= 2
    http_ids = {event["context"]["http_request_id"] for event in events}
    assert len(http_ids) == len(events)
    assert all(event["context"]["transport"] == "streamable_http" for event in events)


def test_streamable_http_methods_and_security(tmp_path):
    async def exercise():
        async with _running_app(tmp_path) as (rt, app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
                for method in ("get", "delete", "put", "options"):
                    response = await getattr(client, method)("/mcp")
                    assert response.status_code == 405, method
                    assert response.headers["allow"] == "POST"
                head = await client.head("/mcp")
                assert head.status_code == 405
                assert head.headers["allow"] == "POST"
                assert head.content == b""
                # Illegal Origin is rejected before the method/route logic.
                response = await client.post("/mcp", json={},
                                             headers={"Origin": "http://evil.example"})
                assert response.status_code == 403
                response = await client.get("/mcp",
                                            headers={"Origin": "http://evil.example"})
                assert response.status_code == 403
                # A legal Origin passes security and reaches the SDK. In JSON mode
                # the SDK also requires Accept: application/json; a malformed body
                # is then rejected as a JSON-RPC validation error (400).
                response = await client.post("/mcp", json={},
                                             headers={"Origin": "http://127.0.0.1:1314",
                                                      "Accept": "application/json"})
                assert response.status_code == 400
            # Illegal Host (LAN IP) is rejected with 421.
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://10.0.0.5:1314") as client:
                response = await client.post("/mcp", json={})
                assert response.status_code == 421
    asyncio.run(exercise())


def test_resolve_bind_host_defaults_and_lan():
    from types import SimpleNamespace
    assert resolve_bind_host(SimpleNamespace(LOCAL_ONLY=True)) == "127.0.0.1"
    assert resolve_bind_host(SimpleNamespace(LOCAL_ONLY=False)) == "0.0.0.0"
    assert resolve_bind_host(SimpleNamespace()) == "127.0.0.1"
    assert resolve_bind_host(None) == "127.0.0.1"


def test_resolve_http_host_override_never_touches_security_mode():
    from types import SimpleNamespace
    assert set(HTTP_BIND_HOSTS) == {"127.0.0.1", "0.0.0.0"}
    assert resolve_http_host(SimpleNamespace(LOCAL_ONLY=True)) == "127.0.0.1"
    assert resolve_http_host(SimpleNamespace(LOCAL_ONLY=False)) == "0.0.0.0"
    # An override changes only this launch's bind address.
    assert resolve_http_host(SimpleNamespace(LOCAL_ONLY=True), "0.0.0.0") == "0.0.0.0"
    assert resolve_http_host(SimpleNamespace(LOCAL_ONLY=False), "127.0.0.1") == "127.0.0.1"
    assert resolve_http_host(None, "0.0.0.0") == "0.0.0.0"
    assert resolve_http_host(None) == "127.0.0.1"


def test_config_and_api_have_no_host_field(tmp_path):
    assert "host" not in Configuration.load(tmp_path).as_dict()
    rt = _runtime(tmp_path)
    with TestClient(create_http_app(rt, port=1314)) as client:
        payload = client.get("/api/config").json()
    assert "host" not in payload
    assert payload["local_only"] is True


def test_sse_rejects_invalid_host_and_origin(tmp_path):
    rt = _runtime(tmp_path)
    with TestClient(create_http_app(rt, port=1314), base_url=BASE) as client:
        # A rejected connect must be a clean 421/403: connect_sse() would raise
        # after sending these responses, so the endpoint pre-validates instead.
        assert client.get("/sse", headers={"Host": "10.0.0.5:1314"}).status_code == 421
        assert client.get("/sse", headers={"Origin": "http://evil.example"}).status_code == 403


def test_sse_messages_post_security(tmp_path):
    rt = _runtime(tmp_path)
    allowed = {"Origin": "http://127.0.0.1:1314"}
    with TestClient(create_http_app(rt, port=1314), base_url=BASE) as client:
        assert client.post("/messages/", json={},
                           headers={**allowed, "Host": "10.0.0.5:1314"}).status_code == 421
        assert client.post("/messages/", json={},
                           headers={"Origin": "http://evil.example"}).status_code == 403
        wrong_type = client.post("/messages/", content=b"{}", headers=allowed)
        assert wrong_type.status_code == 400
        assert "Invalid Content-Type header" in wrong_type.text
        # A valid Host/Origin/JSON request reaches session lookup.
        no_session = client.post("/messages/", json={}, headers=allowed)
        assert no_session.status_code == 400
        assert "session_id is required" in no_session.text


def test_portless_loopback_headers_accepted_on_default_port(tmp_path):
    """Port 80 clients omit :80 from Host/Origin; loopback must still pass."""
    rt = _runtime(tmp_path)
    with TestClient(create_http_app(rt, port=80), base_url="http://localhost") as client:
        # httpx omits the default port, so Host is "localhost". Reaching the
        # protocol/session layer (400) proves the security pre-check passed.
        assert client.post("/mcp", json={},
                           headers={"Origin": "http://localhost",
                                    "Accept": "application/json"}).status_code == 400
        assert client.post("/messages/", json={},
                           headers={"Origin": "http://localhost"}).status_code == 400
        # Explicit portless 127.0.0.1 values are accepted as well.
        assert client.post("/messages/", json={},
                           headers={"Host": "127.0.0.1",
                                    "Origin": "http://127.0.0.1"}).status_code == 400
        # Non-loopback values are still rejected. A positive /sse connect needs a
        # real socket (covered by the process test), because TestClient buffers
        # the never-ending SSE body.
        assert client.get("/sse", headers={"Host": "10.0.0.5"}).status_code == 421
        assert client.get("/sse", headers={"Origin": "http://evil.example"}).status_code == 403


def test_lan_mode_keeps_sse_unprotected(tmp_path):
    async def exercise():
        async with _running_app(tmp_path, local_only=False) as (rt, app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://10.0.0.5:1314") as client:
                # LAN mode disables Host/Origin checks for the SSE message path
                # too; the request then fails on the missing session id.
                response = await client.post("/messages/", json={},
                                             headers={"Origin": "http://evil.example"})
                assert response.status_code == 400
                assert "session_id is required" in response.text
    asyncio.run(exercise())


def test_lan_mode_disables_host_and_origin_checks(tmp_path):
    async def exercise():
        async with _running_app(tmp_path, local_only=False) as (rt, app):
            transport = httpx.ASGITransport(app=app)
            # LAN mode no longer rejects remote Host/Origin (421/403).
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://10.0.0.5:1314") as client:
                response = await client.post("/mcp", json={},
                                             headers={"Origin": "http://evil.example"})
                assert response.status_code not in (421, 403)
            # POST-only method restriction is preserved.
            async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
                response = await client.get("/mcp")
                assert response.status_code == 405
                assert response.headers["allow"] == "POST"
    asyncio.run(exercise())


@pytest.mark.parametrize("base", ["http://localhost:5263", "http://localhost:5263/"])
@pytest.mark.parametrize("headers,target", [
    ({"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}, "/admin/console"),
    ({"Sec-Fetch-Mode": "navigate"}, "/admin/console"),
    ({"Accept": "application/xhtml+xml"}, "/admin/console"),
    ({}, "/readme"),
    ({"Accept": "application/json"}, "/readme"),
    ({"Accept": "text/markdown"}, "/readme"),
])
def test_root_routes_browser_and_other_clients(tmp_path, base, headers, target):
    rt = _runtime(tmp_path)
    with TestClient(create_http_app(rt, port=5263)) as client:
        response = client.get(base, headers=headers, follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == target
        assert response.headers["vary"] == "Accept, Sec-Fetch-Mode"
        assert response.headers["cache-control"] == "no-store"
        response = client.get(base, headers=headers)
        assert response.status_code == 200
        assert response.url.port == 5263
        assert response.url.path == target
        assert client.post(base, follow_redirects=False).status_code == 405


def test_readme_alias_matches_existing_guide(tmp_path):
    rt = _runtime(tmp_path)
    with TestClient(create_http_app(rt, port=5263)) as client:
        response = client.get("/readme")
        assert response.status_code == 200
        assert response.text == client.get("/read-me").text
        assert "http://127.0.0.1:5263/mcp" in response.text


def test_read_me_is_single_page_and_self_references(tmp_path):
    rt = _runtime(tmp_path)
    with TestClient(create_http_app(rt, port=1314)) as client:
        response = client.get("/read-me")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/markdown")
        body = response.text
        assert "{base}" not in body
        assert "http://127.0.0.1:1314/mcp" in body
        assert "http://127.0.0.1:1314/sse" in body
        assert "Streamable HTTP" in body and "stdio" in body
        assert "无法支持多个客户端同时连接" in body
        for tool in ("add", "search_semantic", "search_meta", "get_details",
                     "update", "delete", "stats", "report", "list_keywords"):
            assert tool in body


def test_read_me_port_resolution(tmp_path):
    assert _read_me_base(1314, {}) == "http://127.0.0.1:1314"
    assert _read_me_base(None, {"server": ("127.0.0.1", 4321)}) == "http://127.0.0.1:4321"
    assert _read_me_base(0, {"server": ("127.0.0.1", 4321)}) == "http://127.0.0.1:4321"
    assert _read_me_base(0, {}) is None
    assert _read_me_base(None, {"server": ("127.0.0.1", 0)}) is None


def test_read_me_falls_back_to_the_serving_port(tmp_path):
    rt = _runtime(tmp_path)
    with TestClient(create_http_app(rt, port=None),
                    base_url="http://127.0.0.1:4321") as client:
        body = client.get("/read-me").text
        assert "http://127.0.0.1:4321/mcp" in body
        # Only {base} is substituted; config-block braces stay intact.
        assert '"type": "remote"' in body and "[mcp_servers.memory-server]" in body


def test_closing_service_rejects_mcp_and_read_me(tmp_path):
    rt = _runtime(tmp_path)
    with TestClient(create_http_app(rt, port=1314)) as client:
        rt.request_shutdown()
        assert client.get("/read-me").status_code == 503
        assert client.post("/mcp", json={}).status_code == 503
