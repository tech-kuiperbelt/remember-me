"""Online maintenance admission and complete JSONL restore behavior."""

import asyncio
import json
import threading
import time

from starlette.testclient import TestClient

from app.storage.jsonl_transfer import write_clean_jsonl, load_jsonl
from app.operations.maintenance import MaintenanceGate, MaintenanceRejected
from app.server import create_http_app, create_mcp_server
from app.runtime import Runtime
from tests.conftest import FakeProvider
from tests.test_runtime import runtime, settings


def test_maintenance_gates_writes_and_ops_but_keeps_reads(tmp_path):
    rt = runtime(tmp_path)
    with TestClient(create_http_app(rt)) as client:
        assert client.post('/api/export/namespace', json={'namespace': 'default'}).status_code == 409
        assert client.post('/api/memories', json={'text': 'first'}).status_code == 201
        assert client.post('/api/maintenance/enter').status_code == 200
        blocked = client.post('/api/memories', json={'text': 'blocked'})
        assert blocked.status_code == 503
        assert blocked.json()['response_code'] == 'maintenance_mode'
        assert client.get('/api/memories').status_code == 200
        assert client.get('/api/stats').status_code == 200
        assert client.post('/api/export/namespace', json={'namespace': 'default'}).status_code == 200
        assert client.post('/api/maintenance/exit').status_code == 200
        assert client.post('/api/memories', json={'text': 'after'}).status_code == 201


def test_online_recover_replaces_primary_and_replica(tmp_path):
    rt = runtime(tmp_path)
    with TestClient(create_http_app(rt)) as client:
        first = client.post('/api/memories', json={'text': 'keep'}).json()['id']
        backup = tmp_path / 'backup.jsonl'
        write_clean_jsonl(rt.storage.primary.export_records(), backup)
        removed = client.post('/api/memories', json={'text': 'remove'}).json()['id']
        assert client.post('/api/maintenance/enter').status_code == 200
        with backup.open('rb') as source:
            accepted = client.post('/api/recover', files={'file': ('backup.jsonl', source)})
        assert accepted.status_code == 202
        job_id = accepted.json()['id']
        deadline = time.monotonic() + 10
        while True:
            job = client.get('/api/recover/' + job_id).json()
            if job['status'] in ('completed', 'failed'):
                break
            assert time.monotonic() < deadline
            time.sleep(.02)
        assert job['status'] == 'completed', job
        assert client.post('/api/maintenance/exit').status_code == 200
        assert rt.storage.primary.get(first) is not None
        assert rt.storage.primary.get(removed) is None
        replica = load_jsonl(rt.settings.REPLICA_PATH)['records']
        assert [item['id'] for item in replica] == [first]


def test_maintenance_write_is_rejected_before_body_validation(tmp_path):
    """Deferred writes keep the pre-parse rejection contract in maintenance."""
    rt = runtime(tmp_path)
    with TestClient(create_http_app(rt)) as client:
        assert client.post('/api/maintenance/enter').status_code == 200
        response = client.post('/api/memories', json={})
        assert response.status_code == 503
        assert response.json()['response_code'] == 'maintenance_mode'
        assert client.post('/api/maintenance/exit').status_code == 200
        # Outside maintenance the same malformed body is a normal 400.
        assert client.post('/api/memories', json={}).status_code == 400


def test_import_requires_maintenance_with_standard_envelope(tmp_path):
    rt = runtime(tmp_path)
    with TestClient(create_http_app(rt)) as client:
        response = client.post('/api/import', files={'file': ('in.jsonl', b'')})
        assert response.status_code == 409
        assert response.json()['response_code'] == 'maintenance_required'


def test_mcp_write_is_rejected_but_search_remains_available(tmp_path):
    rt = runtime(tmp_path).start()
    try:
        mcp = create_mcp_server(runtime=rt)
        rt.enter_maintenance()
        blocked = json.loads(asyncio.run(mcp.handle_call_tool(
            'add', {'text': 'blocked'})).content[0].text)
        assert blocked['response_code'] == 'maintenance_mode'
        stats = json.loads(asyncio.run(mcp.handle_call_tool(
            'stats', {})).content[0].text)
        assert 'total' in stats
        rt.exit_maintenance()
    finally:
        rt.close()


def test_recover_replica_failure_keeps_maintenance_and_repairs_on_restart(tmp_path, monkeypatch):
    rt = runtime(tmp_path)
    with TestClient(create_http_app(rt)) as client:
        first = client.post('/api/memories', json={'text': 'keep'}).json()['id']
        backup = tmp_path / 'backup.jsonl'
        write_clean_jsonl(rt.storage.primary.export_records(), backup)
        client.post('/api/memories', json={'text': 'remove'})
        assert client.post('/api/maintenance/enter').status_code == 200

        def fail_sync():
            raise RuntimeError('injected replica failure')

        monkeypatch.setattr(rt.storage, 'force_sync', fail_sync)
        with backup.open('rb') as source:
            job_id = client.post('/api/recover', files={
                'file': ('backup.jsonl', source)}).json()['id']
        deadline = time.monotonic() + 10
        while True:
            job = client.get('/api/recover/' + job_id).json()
            if job['status'] == 'failed':
                break
            assert time.monotonic() < deadline
            time.sleep(.02)
        assert client.post('/api/maintenance/exit').status_code == 409
        assert rt.storage.primary.get(first) is not None

    assert (tmp_path / 'test.db.online-recover').exists()
    restarted = runtime(tmp_path).start()
    try:
        assert not (tmp_path / 'test.db.online-recover').exists()
        assert [row['id'] for row in load_jsonl(restarted.settings.REPLICA_PATH)['records']] == [first]
    finally:
        restarted.close()


def test_enter_drains_existing_writer_and_actions_are_exclusive():
    gate = MaintenanceGate()
    writer_started = threading.Event()
    release_writer = threading.Event()

    def write():
        with gate.access(write=True):
            writer_started.set()
            assert release_writer.wait(5)

    writer = threading.Thread(target=write)
    writer.start()
    assert writer_started.wait(5)
    entering = threading.Thread(target=gate.enter)
    entering.start()
    deadline = time.monotonic() + 5
    while gate.snapshot()['state'] != 'entering':
        assert time.monotonic() < deadline
        time.sleep(.01)
    with gate.access():
        pass
    try:
        with gate.access(write=True):
            raise AssertionError('new write should have been rejected')
    except MaintenanceRejected as exc:
        assert exc.code == 'maintenance_mode'
    release_writer.set()
    writer.join(5)
    entering.join(5)
    assert gate.snapshot()['state'] == 'maintenance'
    gate.start_action('recover')
    try:
        gate.start_action('sync')
    except MaintenanceRejected as exc:
        assert exc.code == 'maintenance_busy'
    else:
        raise AssertionError('second action should have been rejected')
    gate.finish_action()
    gate.exit()


def test_invalid_recover_upload_releases_action_slot(tmp_path):
    rt = runtime(tmp_path)
    with TestClient(create_http_app(rt)) as client:
        assert client.post('/api/maintenance/enter').status_code == 200
        response = client.post('/api/recover', files={
            'file': ('bad.txt', b'not jsonl')})
        assert response.status_code == 400
        assert client.get('/api/maintenance').json()['action'] is None
        assert client.post('/api/maintenance/exit').status_code == 200


def test_online_recover_in_sqlite_mode(tmp_path):
    rt = Runtime(settings(tmp_path, MEMORY_STORAGE='sqlite'),
                 provider_factory=lambda **_: FakeProvider(), configure_logging=False)
    with TestClient(create_http_app(rt)) as client:
        first = client.post('/api/memories', json={'text': 'keep'}).json()['id']
        backup = tmp_path / 'backup.jsonl'
        write_clean_jsonl(rt.storage.export_records(), backup)
        removed = client.post('/api/memories', json={'text': 'remove'}).json()['id']
        assert client.post('/api/maintenance/enter').status_code == 200
        with backup.open('rb') as source:
            job_id = client.post('/api/recover', files={
                'file': ('backup.jsonl', source)}).json()['id']
        deadline = time.monotonic() + 10
        while client.get('/api/recover/' + job_id).json()['status'] == 'queued':
            assert time.monotonic() < deadline
            time.sleep(.02)
        while True:
            job = client.get('/api/recover/' + job_id).json()
            if job['status'] in ('completed', 'failed'):
                break
            assert time.monotonic() < deadline
            time.sleep(.02)
        assert job['status'] == 'completed', job
        assert rt.storage.get(first) is not None
        assert rt.storage.get(removed) is None
        assert client.post('/api/maintenance/exit').status_code == 200
