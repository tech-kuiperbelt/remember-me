"""Fake-clock scheduling and HTTP ownership; never wait for a real day."""
import asyncio
import threading
from unittest.mock import Mock

import httpx
import pytest
from starlette.testclient import TestClient

from app.operations.version_update import API_URL, CHECK_INTERVAL, CheckRejected
from app.config.settings import Settings
from app.runtime import Runtime
from app.server import create_http_app
from tests.conftest import FakeProvider
from tests.version_update_helpers import Github, Clock, check


def test_start_daily_and_manual_does_not_reset_schedule():
    async def run():
        gh, clock = Github(), Clock()
        manager = gh.manager(**clock.options())
        try:
            manager.start()
            manager.start()
            await manager._task
            await asyncio.sleep(0)
            assert len(gh.seen) == 2
            await clock.advance(12 * 3600)
            assert len(gh.seen) == 2
            await check(manager)
            assert len(gh.seen) == 4
            await clock.advance(12 * 3600 - 1)
            assert len(gh.seen) == 4
            await clock.advance(1)
            await manager._task
            assert len(gh.seen) == 6
            await clock.advance(CHECK_INTERVAL)
            await manager._task
            assert len(gh.seen) == 8
        finally:
            await manager.aclose()
        assert all(client.is_closed for client in gh.clients)
        assert manager.snapshot()["check"]["checking"] is False
        with pytest.raises(CheckRejected) as exc:
            manager.request_check()
        assert exc.value.status == 503
    asyncio.run(run())


def test_concurrent_start_manual_and_automatic_singleflight():
    async def run():
        gh, clock = Github(), Clock()
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(request):
            entered.set()
            await release.wait()
            from tests.version_update_helpers import meta
            return httpx.Response(200, json=meta())
        gh.overrides[API_URL + "/latest"] = delayed
        manager = gh.manager(**clock.options())
        try:
            manager.start()
            await entered.wait()
            first = manager._task
            for _ in range(100):
                assert manager.request_check()["check"]["checking"]
                assert manager._task is first
            await clock.advance(CHECK_INTERVAL)
            assert manager._task is first and len(gh.seen) == 1
            release.set()
            await first
            gh.overrides.clear()
            await check(manager)
            assert len(gh.seen) == 4
        finally:
            await manager.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("stage", ["target", "history"])
def test_total_timeout_preserves_only_valid_target(stage):
    async def run():
        from tests.version_update_helpers import local
        gh = Github()
        cancelled = asyncio.Event()
        async def stuck(request):
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
        url = API_URL + ("/latest" if stage == "target" else "?per_page=100&page=1")
        gh.overrides[url] = stuck
        manager = gh.manager(local("1.0.0"), timeout=.01)
        try:
            result = await check(manager)
            assert cancelled.is_set()
            if stage == "target":
                assert result["check"]["status"] == "UNAVAILABLE" and result["latest"] is None
            else:
                assert result["check"]["status"] == "UPDATE_AVAILABLE"
                assert result["latest"] and not result["notes_complete"]
                assert result["notes_error_code"] == "release_history_timeout"
        finally:
            await manager.aclose()
    asyncio.run(run())


def test_shutdown_cancels_network_closes_client_before_provider(tmp_path):
    gh = Github()
    entered, cancelled = threading.Event(), threading.Event()
    async def stuck(request):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()
    gh.overrides[API_URL + "/latest"] = stuck
    provider = FakeProvider()
    def closed():
        assert cancelled.is_set() and all(client.is_closed for client in gh.clients)
    provider.close = Mock(side_effect=closed)
    runtime = Runtime(Settings.from_env({"MEMORY_DATA_DIR": str(tmp_path),
        "MEMORY_STORAGE_PATH": ":memory:", "EMBED_PRELOAD": "false"}),
        provider_factory=lambda **_: provider, configure_logging=False)
    factory = lambda current, settings, **kw: gh.manager(current, settings=settings, **kw)
    with TestClient(create_http_app(runtime, version_update_factory=factory)) as client:
        assert entered.wait(2)
        # Pending external I/O doesn't occupy business/control workers.
        assert client.get('/api/stats').status_code == 200
        assert client.get('/api/config').status_code == 200
        assert client.get('/api/version-update').json()['check']['checking']
        assert client.post('/api/maintenance/enter').status_code == 200
        assert client.post('/api/shutdown').status_code == 200
        assert client.get('/api/version-update').status_code == 503
    assert runtime.state == "closed"
    provider.close.assert_called_once()


def test_direct_runtime_and_cli_have_no_checker(tmp_path, monkeypatch):
    forbidden = Mock(side_effect=AssertionError('stdio/offline runtime must not create a check client'))
    monkeypatch.setattr('app.operations.version_update.create_http_client', forbidden)
    runtime = Runtime(Settings.from_env({"MEMORY_DATA_DIR": str(tmp_path),
        "MEMORY_STORAGE_PATH": ":memory:", "EMBED_PRELOAD": "false"}),
        provider_factory=lambda **_: FakeProvider(), configure_logging=False)
    try:
        runtime.start()
        assert runtime.version_updates is None
        assert runtime.release_info.display_version == 'Unknown'
        forbidden.assert_not_called()
    finally:
        runtime.close()


def test_fresh_http_lifespan_has_fresh_manager_and_local_snapshot(tmp_path):
    gh = Github()
    seen = []
    def factory(current, settings, **kw):
        manager = gh.manager(current, settings=settings, **kw)
        seen.append(manager)
        return manager
    for index in range(2):
        runtime = Runtime(Settings.from_env({"MEMORY_DATA_DIR": str(tmp_path / str(index)),
            "MEMORY_STORAGE_PATH": ":memory:", "EMBED_PRELOAD": "false"}),
            provider_factory=lambda **_: FakeProvider(), configure_logging=False)
        with TestClient(create_http_app(runtime, version_update_factory=factory)) as client:
            async def done():
                await runtime.version_updates._task
            client.portal.call(done)
            assert client.get('/api/version-update').json()['check']['revision'] == 2
        assert runtime.state == 'closed' and all(c.is_closed for c in gh.clients)
    assert seen[0] is not seen[1] and len(gh.seen) == 4
