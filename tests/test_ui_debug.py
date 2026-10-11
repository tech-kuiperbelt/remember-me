"""Persistent configuration and startup Debug share one effective UI state."""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from app import server
from app.config import configuration
from app.config.settings import Settings
from app import runtime as runtime_module
from app.transport.admin import create_admin_routes


@pytest.mark.parametrize('debug', [False, True])
def test_console_debug_flag_ignores_request_override(admin, debug):
    client = TestClient(Starlette(routes=create_admin_routes(admin.service, debug=debug)))
    response = client.get('/admin/console?debug=true')
    assert response.status_code == 200
    assert f'data-ui-debug="{str(debug).lower()}"' in response.text
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('debug', [False, True])
def test_cli_debug_reaches_http_app_and_survives_restart(monkeypatch, debug):
    settings = SimpleNamespace(PORT=1314, LOCAL_ONLY=True, environment={})
    runtime = SimpleNamespace(restart_requested=True, close=Mock())
    app = SimpleNamespace(state=SimpleNamespace(close_connections=Mock()))
    build = Mock(return_value=app)
    monkeypatch.setattr(Settings, 'from_config', lambda *args, **kwargs: settings)
    monkeypatch.setattr(configuration, 'activate_environment', lambda _: None)
    monkeypatch.setattr(runtime_module, 'create_runtime', lambda _: runtime)
    monkeypatch.setattr(server, 'create_http_app', build)
    monkeypatch.setattr(server.uvicorn, 'Config', Mock())

    class FakeServer:
        def __init__(self, config):
            pass

        async def serve(self):
            pass

    monkeypatch.setattr(server.uvicorn, 'Server', FakeServer)
    import os
    restart = Mock()
    monkeypatch.setattr(os, 'execv', restart)
    args = ['--port', '1314'] + (['--debug'] if debug else [])
    asyncio.run(server.main(args))
    build.assert_called_once_with(runtime, port=1314, local_only=True, debug=True if debug else None)
    runtime.close.assert_called_once()
    restart.assert_called_once_with(server.sys.executable,
        [server.sys.executable, '-m', 'app.server', *args])

@pytest.mark.parametrize('configured, flag', [(False, None), (True, None), (False, True)])
def test_config_debug_roundtrip_and_fresh_html(tmp_path, configured, flag):
    from app.runtime import Runtime
    from tests.conftest import FakeProvider
    from app.config.jsonc import loads
    path = tmp_path / 'config.jsonc'
    path.write_text('// Keep this comment\n{"debug": ' + str(configured).lower() + '}')
    settings = Settings.from_config(tmp_path, environment={'MEMORY_STORAGE': 'sqlite', 'MEMORY_STORAGE_PATH': ':memory:', 'EMBED_PRELOAD': 'false'})
    rt = Runtime(settings, provider_factory=lambda **_: FakeProvider(), configure_logging=False)
    with TestClient(server.create_http_app(rt, debug=flag)) as client:
        initial = configured or flag is True
        config = client.get('/api/config').json()
        assert config['debug'] is configured and config['debug_effective'] is initial
        assert f'data-ui-debug="{str(initial).lower()}"' in client.get('/admin/console').text
        # Saving another setting does not override a startup flag.
        assert client.post('/api/config', json={'language': 'en'}).json()['debug_effective'] is initial
        for target in (False, True, False):
            result = client.post('/api/config', json={'debug': target})
            assert result.status_code == 200
            assert result.json()['debug'] is target
            assert result.json()['debug_effective'] is target
            assert client.get('/api/config/model').json()['debug_effective'] is target
            assert f'data-ui-debug="{str(target).lower()}"' in client.get('/admin/console').text
            assert loads(path.read_text())['debug'] is target
            assert 'Keep this comment' in path.read_text()
        assert 'debug_effective' not in loads(path.read_text())
    assert not Settings.from_config(tmp_path, environment={}).UI_DEBUG


def test_debug_save_failure_keeps_effective_state(tmp_path, monkeypatch):
    from app.runtime import Runtime
    from tests.conftest import FakeProvider
    settings = Settings.from_config(tmp_path, create=True, environment={'MEMORY_STORAGE': 'sqlite', 'MEMORY_STORAGE_PATH': ':memory:', 'EMBED_PRELOAD': 'false'})
    rt = Runtime(settings, provider_factory=lambda **_: FakeProvider(), configure_logging=False)
    with TestClient(server.create_http_app(rt, debug=True)) as client:
        for value in ('true', 1, None, []):
            assert client.post('/api/config', json={'debug': value}).status_code == 400
            assert rt.debug_effective is True
        assert client.post('/api/config', json={'debug_effective': False}).status_code == 400
        def fail_save(self):
            raise OSError('disk error')
        monkeypatch.setattr(configuration.Configuration, 'save', fail_save)
        assert client.post('/api/config', json={'debug': False}).status_code == 400
        assert client.get('/api/config').json()['debug_effective'] is True
        assert configuration.Configuration.load(tmp_path).debug is False


def test_legacy_config_defaults_debug_off_without_rewrite(tmp_path):
    path = tmp_path / 'config.jsonc'
    path.write_text('{"port":1314}')
    assert configuration.Configuration.load(tmp_path).debug is False
    assert Settings.from_config(tmp_path, environment={}).UI_DEBUG is False
    assert path.read_text() == '{"port":1314}'
