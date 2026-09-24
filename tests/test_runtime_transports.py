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
from tests.conftest import FakeProvider
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
    env = {**os.environ, 'PYTHONPATH': str(root), 'MEMORY_STORAGE': 'dual',
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
        params = StdioServerParameters(command=sys.executable, args=[str(runner)], env=env)
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


def test_streamable_http_protocol_and_process_exit(process_config):
    """The new /mcp transport must coexist with SSE and still let the process exit."""
    runner, env = process_config
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    args = [sys.executable, str(runner), '--port', str(port)]
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
    proc = subprocess.Popen([sys.executable, str(runner)], env=env,
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
