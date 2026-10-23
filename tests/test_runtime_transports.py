"""Wire-level smoke with fake embeddings and isolated subprocess resources."""
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client


@pytest.fixture
def process_config(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runner = tmp_path / 'runner.py'
    runner.write_text('''import asyncio, json, os, sys
from pathlib import Path
from app.runtime import Runtime
import app.runtime as runtime_module
from tests.conftest import FakeProvider, mock_version_client
import app.operations.version_update as version_update
version_update.create_http_client = mock_version_client
from app.server import main
runtime_module.create_runtime = lambda settings: Runtime(settings,
    provider_factory=lambda **kwargs: FakeProvider(), configure_logging=False)
def exec_probe(executable, argv):
    # Inspect cleanup at the exec boundary without replacing our fake-provider runner.
    from app.storage import create_storage
    store = create_storage('dual', primary_path=os.environ['MEMORY_STORAGE_PATH'],
        secondary_path=os.environ['MEMORY_REPLICA_PATH'], start_background=False)
    store.stop()
    Path(os.environ['EXEC_PROBE']).write_text(json.dumps(argv))
os.execv = exec_probe
asyncio.run(main())
''')
    config = tmp_path / 'config.jsonc'
    config.write_text(json.dumps({'port': 1314, 'language': 'zh-CN'}))
    env = {**os.environ, 'PYTHONPATH': str(root), 'MEMORY_STORAGE': 'dual',
           'MEMORY_DATA_DIR': str(tmp_path),
           'MEMORY_STORAGE_PATH': str(tmp_path / 'memories.db'),
           'MEMORY_REPLICA_PATH': str(tmp_path / 'memories.jsonl'),
           'MEMORY_AUDIT_PATH': str(tmp_path / 'audit.jsonl'),
           'EXEC_PROBE': str(tmp_path / 'exec.json'), 'EMBED_PRELOAD': 'false'}
    return runner, env


async def check_protocol(streams):
    async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=5)) as session:
        await session.initialize()
        tools = await session.list_tools()
        assert len(tools.tools) == 11
        result = await session.call_tool('stats', {})
        assert not result.isError
        assert json.loads(result.content[0].text)['total'] == 0


def test_stdio_protocol_and_eof(process_config):
    runner, env = process_config
    async def exercise():
        params = StdioServerParameters(command=sys.executable, args=[str(runner), '--stdio'], env=env)
        async with stdio_client(params) as streams:
            await check_protocol(streams)
    asyncio.run(exercise())
    assert not Path(env['EXEC_PROBE']).exists()
    recorded = [json.loads(line) for line in Path(env['MEMORY_AUDIT_PATH']).read_text().splitlines()]
    stats = next(event for event in recorded if event['tool'] == 'stats')
    assert stats['context']['source'] == 'mcp'
    assert stats['context']['session_id'].startswith('ss_')
    assert stats['context']['request_id'] is not None
    assert stats['context']['client_name']
    # EOF released the writer lock.
    from app.storage import create_storage
    store = create_storage('sqlite', path=env['MEMORY_STORAGE_PATH'])
    store.stop()


@pytest.mark.parametrize('action', ['shutdown', 'restart', 'signal'])
def test_sse_protocol_and_process_exit_with_open_connection(process_config, action):
    runner, env = process_config
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    args = [sys.executable, str(runner), '--port', str(port)]
    log_path = runner.parent / 'server.log'
    with log_path.open('w+') as log:
        proc = subprocess.Popen(args, env=env, stdout=log, stderr=log)
        try:
            base = f'http://127.0.0.1:{port}'
            deadline = time.monotonic() + 15
            with httpx.Client(timeout=2, trust_env=False) as client:
                while True:
                    try:
                        if client.get(base + '/api/stats').status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    assert proc.poll() is None, log_path.read_text()
                    assert time.monotonic() < deadline, log_path.read_text()
                    time.sleep(.05)
            async def exercise():
                if action == 'shutdown':
                    factory = lambda **kw: httpx.AsyncClient(trust_env=False, **kw)
                    async with sse_client(base + '/sse', httpx_client_factory=factory) as first:
                        async with sse_client(base + '/sse', httpx_client_factory=factory) as second:
                            async with ClientSession(*first) as one, ClientSession(*second) as two:
                                await one.initialize()
                                await two.initialize()
                                await one.call_tool('stats', {})
                                await two.call_tool('stats', {})
                async with sse_client(base + '/sse', httpx_client_factory=lambda **kw: httpx.AsyncClient(trust_env=False, **kw)) as streams:
                    await check_protocol(streams)
                async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                    # Keep a second SSE connection open through process shutdown.
                    async with client.stream('GET', base + '/sse') as response:
                        assert response.status_code == 200
                        lines = response.aiter_lines()
                        assert (await anext(lines)).startswith('event: endpoint')
                        if action == 'signal':
                            proc.terminate()
                        else:
                            assert (await client.post(base + '/api/maintenance/enter')).status_code == 200
                            result = await client.post(base + '/api/' + action)
                            assert result.status_code == 200
                            assert result.json()['status'] == ('stopping' if action == 'shutdown' else 'restarting')
                        await asyncio.to_thread(proc.wait, 10)
            asyncio.run(exercise())
            assert proc.returncode == ( -15 if action == 'signal' else 0), log_path.read_text()
            output = log_path.read_text()
            assert 'Exception in ASGI application' not in output, output
            if action == 'shutdown':
                logged = [json.loads(line) for line in Path(env['MEMORY_AUDIT_PATH']).read_text().splitlines()]
                sessions = {event['context']['session_id'] for event in logged
                            if event['tool'] == 'stats' and event['context']['source'] == 'mcp'}
                assert len(sessions) >= 2
            if action == 'restart':
                assert json.loads(Path(env['EXEC_PROBE']).read_text()) == [sys.executable, '-m', 'app.server', '--port', str(port)]
            else:
                assert not Path(env['EXEC_PROBE']).exists()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(5)


@pytest.mark.parametrize('launch', ['override', 'config', 'data-dir'])
def test_streamable_http_protocol_and_process_exit(process_config, launch):
    """The new /mcp transport must coexist with SSE and still let the process exit."""
    runner, env = process_config
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    if launch == 'override':
        args = [sys.executable, str(runner), '--port', str(port)]
    else:
        (runner.parent / 'config.jsonc').write_text(json.dumps({'port': port, 'language': 'en'}))
        args = [sys.executable, str(runner)]
        if launch == 'data-dir':
            env['MEMORY_DATA_DIR'] = str(runner.parent / 'unused')
            args.extend(['--data-dir', str(runner.parent)])
    log_path = runner.parent / 'server-mcp.log'
    with log_path.open('w+') as log:
        proc = subprocess.Popen(args, env=env, stdout=log, stderr=log)
        try:
            base = f'http://127.0.0.1:{port}'
            deadline = time.monotonic() + 15
            with httpx.Client(timeout=2, trust_env=False) as client:
                while True:
                    try:
                        if client.get(base + '/api/stats').status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    assert proc.poll() is None, log_path.read_text()
                    assert time.monotonic() < deadline, log_path.read_text()
                    time.sleep(.05)

            async def exercise():
                factory = lambda **kw: httpx.AsyncClient(trust_env=False, **kw)
                async with streamablehttp_client(base + '/mcp',
                                                 httpx_client_factory=factory) as streams:
                    read_stream, write_stream, _ = streams
                    async with ClientSession(read_stream, write_stream,
                                             read_timeout_seconds=timedelta(seconds=5)) as session:
                        await session.initialize()
                        assert len((await session.list_tools()).tools) == 11
                        assert not (await session.call_tool('stats', {})).isError
                # The old SSE transport still works against the same instance.
                async with sse_client(base + '/sse', httpx_client_factory=factory) as streams:
                    await check_protocol(streams)
                async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                    assert (await client.post(base + '/api/maintenance/enter')).status_code == 200
                    result = await client.post(base + '/api/shutdown')
                    assert result.status_code == 200
                await asyncio.to_thread(proc.wait, 10)

            asyncio.run(exercise())
            assert proc.returncode == 0, log_path.read_text()
            assert 'Exception in ASGI application' not in log_path.read_text()
            logged = [json.loads(line) for line in Path(env['MEMORY_AUDIT_PATH']).read_text().splitlines()]
            mcp_events = [event for event in logged if event['tool'] == 'stats'
                          and event['context'].get('transport') == 'streamable_http']
            assert mcp_events, logged
            assert mcp_events[-1]['context']['http_request_id'].startswith('rq_')
            assert mcp_events[-1]['context']['source'] == 'mcp'
            # Shutdown released the writer lock.
            from app.storage import create_storage
            store = create_storage('sqlite', path=env['MEMORY_STORAGE_PATH'])
            store.stop()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(5)


def test_stdio_sigterm_cleans_up(process_config):
    import select
    runner, env = process_config
    proc = subprocess.Popen([sys.executable, str(runner), '--stdio'], env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                   'params': {'protocolVersion': '2024-11-05', 'capabilities': {},
                              'clientInfo': {'name': 'test', 'version': '1'}}}
        proc.stdin.write((json.dumps(request) + '\n').encode())
        proc.stdin.flush()
        assert select.select([proc.stdout], [], [], 10)[0]
        assert json.loads(proc.stdout.readline())['id'] == 1
        proc.terminate()
        assert proc.wait(10) == 0, proc.stderr.read().decode()
        from app.storage import create_storage
        store = create_storage('sqlite', path=env['MEMORY_STORAGE_PATH'])
        store.stop()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(5)
        proc.stdin.close()
        proc.stdout.close()
        proc.stderr.close()


def _free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _wait_until_ready(proc, base, log_path):
    deadline = time.monotonic() + 15
    with httpx.Client(timeout=2, trust_env=False) as client:
        while True:
            try:
                if client.get(base + '/api/stats').status_code == 200:
                    return
            except httpx.TransportError:
                pass
            assert proc.poll() is None, log_path.read_text()
            assert time.monotonic() < deadline, log_path.read_text()
            time.sleep(.05)


def test_host_override_keeps_mcp_security_and_sse_roundtrip(process_config):
    """Binding 0.0.0.0 must not weaken Host/Origin protection or SSE."""
    runner, env = process_config
    port = _free_port()
    base = f'http://127.0.0.1:{port}'
    log_path = runner.parent / 'server-host.log'
    with log_path.open('w+') as log:
        proc = subprocess.Popen([sys.executable, str(runner), '--host', '0.0.0.0',
                                 '--port', str(port)], env=env, stdout=log, stderr=log)
        try:
            _wait_until_ready(proc, base, log_path)
            bad_host = {'Host': f'10.0.0.5:{port}'}
            bad_origin = {'Origin': 'http://evil.example'}
            portless = {'Host': 'localhost', 'Origin': 'http://localhost'}
            with httpx.Client(timeout=5, trust_env=False) as client:
                assert client.post(base + '/mcp', json={}, headers=bad_host).status_code == 421
                assert client.post(base + '/mcp', json={}, headers=bad_origin).status_code == 403
                assert client.get(base + '/sse', headers=bad_host).status_code == 421
                assert client.get(base + '/sse', headers=bad_origin).status_code == 403
                # Default-port style headers (no :80) remain valid loopback values.
                assert client.post(base + '/mcp', json={},
                                   headers={**portless,
                                            'Accept': 'application/json'}).status_code == 400
                assert client.post(base + '/messages/', json={},
                                   headers=portless).status_code == 400
                assert client.get(base + '/api/stats').status_code == 200

            async def exercise():
                factory = lambda **kw: httpx.AsyncClient(trust_env=False, **kw)
                async with sse_client(base + '/sse', headers=portless,
                                      httpx_client_factory=factory) as streams:
                    await check_protocol(streams)
                async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                    assert (await client.post(base + '/api/maintenance/enter')).status_code == 200
                    assert (await client.post(base + '/api/shutdown')).status_code == 200
                await asyncio.to_thread(proc.wait, 10)

            asyncio.run(exercise())
            assert proc.returncode == 0, log_path.read_text()
            assert 'Exception in ASGI application' not in log_path.read_text()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(5)


@pytest.mark.parametrize('args,message', [
    (['--stdio', '--host', '127.0.0.1'], '--host cannot be used with --stdio'),
    (['--host', '1.2.3.4'], "invalid choice: '1.2.3.4'"),
])
def test_host_argument_validation(process_config, args, message):
    runner, env = process_config
    result = subprocess.run([sys.executable, str(runner), *args], env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 2, result.stderr
    assert message in result.stderr


def test_host_override_survives_restart(process_config):
    runner, env = process_config
    port = _free_port()
    base = f'http://127.0.0.1:{port}'
    log_path = runner.parent / 'server-restart-host.log'
    with log_path.open('w+') as log:
        proc = subprocess.Popen([sys.executable, str(runner), '--host', '127.0.0.1',
                                 '--port', str(port)], env=env, stdout=log, stderr=log)
        try:
            _wait_until_ready(proc, base, log_path)
            with httpx.Client(timeout=5, trust_env=False) as client:
                assert client.post(base + '/api/maintenance/enter').status_code == 200
                assert client.post(base + '/api/restart').status_code == 200
            assert proc.wait(10) == 0, log_path.read_text()
            assert json.loads(Path(env['EXEC_PROBE']).read_text()) == [
                sys.executable, '-m', 'app.server',
                '--host', '127.0.0.1', '--port', str(port)]
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(5)
