"""Explicit ownership, startup failure, drain order and process-control regressions."""
import asyncio
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import Mock

import pytest
from starlette.testclient import TestClient

from app import audit
from app.runtime import Runtime
from app.settings import Settings
from app.storage import create_storage
from app.storage_dual import DualStorage
from app.server import create_http_app
from tests.conftest import FakeProvider


def settings(tmp_path, **overrides):
    return Settings.from_env({"MEMORY_STORAGE": "dual",
        "MEMORY_STORAGE_PATH": str(tmp_path / "test.db"),
        "MEMORY_AUDIT_PATH": str(tmp_path / "audit.jsonl"),
        "EMBED_PRELOAD": "false", **overrides})


def runtime(tmp_path, **kwargs):
    return Runtime(settings(tmp_path), provider_factory=lambda **_: FakeProvider(),
                   configure_logging=False, **kwargs)


def test_settings_are_a_snapshot(monkeypatch, tmp_path):
    env = {"MEMORY_STORAGE_PATH": str(tmp_path / "one.db"), "MEMORY_DEFAULT_NAMESPACE": "one"}
    snapshot = Settings.from_env(env)
    env["MEMORY_DEFAULT_NAMESPACE"] = "two"
    monkeypatch.setenv("MEMORY_DEFAULT_NAMESPACE", "three")
    assert snapshot.DEFAULT_NAMESPACE == snapshot.environment["MEMORY_DEFAULT_NAMESPACE"] == "one"
    assert snapshot.REPLICA_PATH == str(tmp_path / "one.jsonl")
    with pytest.raises(TypeError):
        snapshot.environment["MEMORY_DEFAULT_NAMESPACE"] = "four"


def test_import_and_help_have_no_resource_side_effects(tmp_path):
    root = str(Path(__file__).resolve().parents[1])
    env = {**os.environ, "PYTHONPATH": root, "MEMORY_STORAGE": "invalid",
           "EMBED_DIM": "invalid", "MEMORY_STORAGE_PATH": str(tmp_path / "sentinel.db")}
    script = '''import logging, threading, sys
before = tuple(logging.getLogger().handlers), tuple(threading.enumerate())
sys.argv = ['anything', '--invalid-argument']
import app.service, app.admin, app.server, app.runtime, app.log_config
assert before == (tuple(logging.getLogger().handlers), tuple(threading.enumerate()))
assert not hasattr(app.service, 'storage')
'''
    for args in (["-c", script], ["-m", "app.server", "--help"]):
        result = subprocess.run([sys.executable, *args], cwd=tmp_path, env=env,
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("stage", ["provider", "storage", "vectors", "warmup", "dual_start"])
def test_start_failure_releases_resources(tmp_path, monkeypatch, stage):
    provider = FakeProvider()
    provider.close = Mock()
    def provider_factory(**kwargs):
        if stage == "provider":
            raise RuntimeError("injected startup failure")
        return provider
    def storage_factory(*args, **kwargs):
        if stage == "storage":
            raise RuntimeError("injected startup failure")
        store = create_storage(*args, **kwargs)
        if stage == "vectors":
            monkeypatch.setattr(store, "index_embedding_completeness", lambda: {"complete": False})
        if stage == "dual_start":
            monkeypatch.setattr(store, "start", lambda: (_ for _ in ()).throw(RuntimeError("injected startup failure")))
        return store
    if stage == "warmup":
        provider.warmup = Mock(side_effect=RuntimeError("injected startup failure"))
    s = settings(tmp_path, EMBED_PRELOAD="true")
    rt = Runtime(s, provider_factory=provider_factory, storage_factory=storage_factory,
                 log_dir=tmp_path / "logs")
    handlers = tuple(logging.getLogger().handlers)
    with pytest.raises(RuntimeError):
        rt.start()
    rt.close()
    assert rt.state == "closed" and audit._file is None
    assert tuple(logging.getLogger().handlers) == handlers
    if stage != "provider":
        provider.close.assert_called_once()
    # Both locks can immediately be obtained by a replacement runtime.
    replacement = runtime(tmp_path)
    replacement.start()
    replacement.close()


def test_start_close_once_and_final_sync(tmp_path, monkeypatch):
    rt = runtime(tmp_path)
    assert not (tmp_path / "test.db").exists()
    rt.start()
    job = rt.storage._job
    assert rt.start() is rt and rt.storage._job is job
    assert rt.storage._sync_count == 1
    memory = rt.service.memory_add("saved before shutdown")
    assert rt.storage.primary._count_unsynced() == 1
    rt.close()
    rt.close()
    assert not job.is_alive()
    from app.jsonl_transfer import load_jsonl
    assert load_jsonl(tmp_path / "test.jsonl")["records"][0]["id"] == memory["id"]


def test_close_waits_for_import_before_storage(tmp_path, monkeypatch):
    rt = runtime(tmp_path).start()
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    original = rt.service._run_import_job
    def blocked(*args):
        entered.set()
        assert release.wait(10)
        original(*args)
    monkeypatch.setattr(rt.service, "_run_import_job", blocked)
    source = tmp_path / "input.jsonl"
    source.write_text(json.dumps({"id":"one", "text":"imported", "metadata":{}, "namespace":"default",
        "created_at":"2026-01-01T00:00:00Z", "updated_at":"2026-01-01T00:00:00Z"}) + '\n')
    rt.service.memory_import_start(str(source), source.name)
    assert entered.wait(2)
    thread = threading.Thread(target=lambda: (rt.close(), closed.set()))
    thread.start()
    try:
        assert not closed.wait(.1)
        assert rt.storage.primary.conn.execute("SELECT 1").fetchone()[0] == 1
        with pytest.raises(RuntimeError, match="closing"):
            rt.service.memory_import_start(str(source), source.name)
    finally:
        release.set()
        thread.join(10)
    assert closed.is_set()
    from app.jsonl_transfer import load_jsonl
    assert load_jsonl(tmp_path / "test.jsonl")["records"][0]["text"] == "imported"


def test_dual_stop_waits_for_periodic_worker(tmp_path, monkeypatch):
    rt = runtime(tmp_path).start()
    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
    # Replace the idle periodic worker with a controlled in-progress cycle.
    rt.storage._stop.set()
    rt.storage._job.join()
    def sync():
        with rt.storage._sync_mutex:
            entered.set()
            release.wait(10)
            rt.storage.primary.conn.execute("SELECT 1")
    worker = threading.Thread(target=sync)
    rt.storage._job = worker
    worker.start()
    assert entered.wait(2)
    closer = threading.Thread(target=lambda: (rt.close(), stopped.set()))
    closer.start()
    try:
        assert not stopped.wait(.1)
        assert rt.storage.primary.conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        release.set()
        closer.join(10)
    assert stopped.is_set() and not worker.is_alive()


@pytest.mark.parametrize("restart", [False, True])
def test_control_responds_before_shutdown_and_blocks_new_work(tmp_path, restart):
    rt = runtime(tmp_path)
    app = create_http_app(rt)
    with TestClient(app) as client:
        rt.shutdown_callback = Mock()
        # Exercise ASGI response ordering independently of TestClient's buffering.
        from app.admin import AdminAPI
        response = AdminAPI(runtime=rt)._shutdown_response(restart)
        assert rt.state == "running"
        events = []
        async def send(event):
            events.append(event)
            assert rt.state == "running"
        asyncio.run(response({"type": "http"}, None, send))
        assert events[-1]["type"] == "http.response.body"
        assert rt.state == "closing" and rt.restart_requested is restart
        rt.shutdown_callback.assert_called_once()
        assert client.post('/api/shutdown').status_code == 503
        assert client.get('/api/stats').status_code == 503
    assert rt.state == "closed"


def test_failed_final_sync_keeps_durable_outbox(tmp_path, monkeypatch):
    rt = runtime(tmp_path).start()
    rt.service.memory_add("still durable")
    monkeypatch.setattr(rt.storage.secondary, 'append_batch', Mock(side_effect=OSError('disk error')))
    rt.close()
    from app.storage_sqlite import SQLiteStorage
    store = SQLiteStorage(str(tmp_path / 'test.db'))
    try:
        assert store._count_unsynced() == 1
    finally:
        store.close()


def test_service_uses_native_provider_query_and_batch_paths(service_factory):
    from app.embed import EmbeddingSpec
    provider = FakeProvider()
    provider.spec = EmbeddingSpec('sentence_transformers', 'fake', 3)
    provider.embed_query = Mock(return_value=[1., 0., 0.])
    provider.embed_documents = Mock(return_value=[[0., 1., 0.]])
    service = service_factory(provider=provider)
    assert service.embed_query('query') == [1., 0., 0.]
    assert service.embed_documents(['document']) == [[0., 1., 0.]]
    provider.embed_query.assert_called_once_with('query')
    provider.embed_documents.assert_called_once_with(['document'])


def test_export_routes_after_service_split(tmp_path):
    rt = runtime(tmp_path)
    with TestClient(create_http_app(rt)) as client:
        rt.service.memory_add('export body')
        for endpoint, body in [('full', {}), ('namespace', {'namespace': 'default'})]:
            response = client.post('/api/export/' + endpoint, json=body)
            assert response.status_code == 200
            assert response.headers['x-memory-count'] == '1'
            assert json.loads(response.text)['text'] == 'export body'
