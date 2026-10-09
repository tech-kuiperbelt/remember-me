"""The startup flag alone controls Admin UI Debug availability."""
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
    build.assert_called_once_with(runtime, port=1314, local_only=True, debug=debug)
    runtime.close.assert_called_once()
    restart.assert_called_once_with(server.sys.executable,
        [server.sys.executable, '-m', 'app.server', *args])
