"""Streamable HTTP (/mcp) and /read-me regressions."""
import asyncio
import contextlib
import json
from datetime import timedelta
from pathlib import Path

import httpx
from starlette.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.runtime import Runtime
from app.settings import Settings
from app.server import _read_me_base, create_http_app
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
async def _running_app(tmp_path, port=1314):
    rt = _runtime(tmp_path)
    app = create_http_app(rt, port=port)
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
