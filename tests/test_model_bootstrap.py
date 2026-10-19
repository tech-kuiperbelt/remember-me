"""HTTP model bootstrap remains configurable and forcibly in maintenance."""
import asyncio
from contextlib import contextmanager
import json
import os
import threading
import time
from unittest.mock import Mock, patch

import pytest
from starlette.testclient import TestClient

from app.config.configuration import Configuration
from app.embedding.embed import ModelCacheMissing
from app.operations.model_preparation import ModelProgress, PreparationCancelled, download_progress
from app.runtime import Runtime
from app.server import create_http_app, create_mcp_server
from app.config.settings import Settings
from tests.conftest import FakeProvider


@pytest.fixture(autouse=True)
def preserve_process_environment():
    # Bootstrap owns process environment, just like a real startup entrypoint.
    import tempfile
    original_tempdir = tempfile.tempdir
    with patch.dict(os.environ, dict(os.environ)):
        yield
    tempfile.tempdir = original_tempdir


def make_runtime(tmp_path, factory, *, automatic=True):
    (tmp_path / 'config.jsonc').write_text(json.dumps({'auto_download_model': automatic}))
    settings = Settings.from_config(tmp_path, environment={
        'MEMORY_STORAGE': 'dual', 'EMBED_PROVIDER': 'sentence_transformers',
        'EMBED_PRELOAD': 'true',
    })
    return Runtime(settings, provider_factory=factory, configure_logging=False)


def wait_for(rt, status):
    deadline = time.monotonic() + 5
    while rt.model_status()['status'] != status:
        assert time.monotonic() < deadline, rt.model_status()
        time.sleep(.01)


@pytest.fixture
def progress_context(monkeypatch):
    active = {}
    @contextmanager
    def context(provider, progress, cancelled):
        active.update(progress=progress, cancelled=cancelled)
        yield
        if cancelled.is_set():
            raise PreparationCancelled()
    monkeypatch.setattr('app.runtime.download_progress', context)
    return active


def test_missing_model_http_mcp_progress_and_ready_stays_in_maintenance(tmp_path, progress_context):
    entered, release = threading.Event(), threading.Event()
    provider = FakeProvider()
    provider.warmup = Mock()
    def factory(**kwargs):
        if not kwargs.get('allow_download'):
            raise ModelCacheMissing('missing cache')
        progress_context['progress'].bytes(40, 100, 'weights.bin')
        entered.set()
        assert release.wait(5)
        return provider
    rt = make_runtime(tmp_path, factory)
    with TestClient(create_http_app(rt)) as client:
        try:
            assert entered.wait(2)
            assert client.get('/admin/console').status_code == 200
            status = client.get('/api/maintenance').json()
            assert status['state'] == 'maintenance'
            assert status['required_reason'] == 'model_not_ready'
            assert status['model']['percent'] == 40
            assert client.get('/api/config/model').status_code == 200
            assert client.get('/api/storage-paths').status_code == 200
            for path in ('/api/stats', '/api/memories', '/api/import/missing'):
                result = client.get(path)
                assert result.status_code == 503
                assert result.json()['response_code'] == 'model_not_ready'
            assert client.post('/api/maintenance/exit').json()['response_code'] == 'model_not_ready'
            assert client.post('/api/sync').json()['response_code'] == 'model_not_ready'
            mcp = create_mcp_server(runtime=rt)
            assert len(asyncio.run(mcp.handle_list_tools()).tools) == 11
            response = asyncio.run(mcp.handle_call_tool('stats', {}))
            envelope = json.loads(response.content[0].text)
            assert envelope['response_code'] == 'model_not_ready'
            assert '/admin/console' in envelope['hint']
            release.set()
            wait_for(rt, 'ready')
            provider.warmup.assert_called_once()
            assert client.get('/api/stats').status_code == 200
            assert client.get('/api/maintenance').json()['state'] == 'maintenance'
            assert client.post('/api/memories', json={'text':'blocked'}).status_code == 503
            assert client.post('/api/maintenance/exit').status_code == 200
            assert client.post('/api/memories', json={'text':'ready'}).status_code == 201
        finally:
            release.set()
    assert rt.state == 'closed'


def test_failed_download_can_configure_proxy_and_retry_without_restart(tmp_path, progress_context):
    attempts = []
    def factory(**kwargs):
        if not kwargs.get('allow_download'):
            raise ModelCacheMissing('missing cache')
        env = kwargs['environment']
        attempts.append(env.get('HTTPS_PROXY'))
        if not env.get('HTTPS_PROXY'):
            raise ConnectionError('proxy is required')
        return FakeProvider()
    rt = make_runtime(tmp_path, factory)
    with TestClient(create_http_app(rt)) as client:
        wait_for(rt, 'failed')
        assert client.get('/api/model').json()['error'] == 'proxy is required'
        result = client.post('/api/config/model', json={
            'proxy': {'enabled': True, 'url': 'http://user:secret@127.0.0.1:7890'},
            'auto_download_model': False,
        })
        assert result.status_code == 200 and 'secret' not in result.text
        # Empty URL field is omitted by the UI, preserving stored credentials.
        assert client.post('/api/config/model', json={'proxy': {'enabled': True}}).status_code == 200
        assert client.post('/api/model/retry').status_code == 202
        wait_for(rt, 'ready')
        assert attempts == [None, 'http://user:secret@127.0.0.1:7890']
        assert json.loads((tmp_path / 'config.jsonc').read_text())['auto_download_model'] is False
        assert client.post('/api/maintenance/exit').status_code == 200
        assert client.post('/api/config/model', json={'proxy': {'enabled': False}}).status_code == 200


def test_config_change_cancels_old_job_then_retries_once(tmp_path, progress_context):
    first_started, first_exited = threading.Event(), threading.Event()
    attempts = []
    live = 0
    def factory(**kwargs):
        nonlocal live
        if not kwargs.get('allow_download'):
            raise ModelCacheMissing('missing cache')
        live += 1
        assert live == 1
        try:
            attempts.append(kwargs['environment'].get('HTTP_PROXY'))
            if len(attempts) == 1:
                first_started.set()
                assert progress_context['cancelled'].wait(5)
                first_exited.set()
                raise PreparationCancelled()
            assert first_exited.is_set()
            return FakeProvider()
        finally:
            live -= 1
    rt = make_runtime(tmp_path, factory)
    with TestClient(create_http_app(rt)) as client:
        assert first_started.wait(2)
        assert client.post('/api/config/model', json={
            'proxy': {'enabled': True, 'url': 'http://127.0.0.1:7890'}}).status_code == 200
        assert client.post('/api/model/retry').status_code == 202
        wait_for(rt, 'ready')
        assert attempts == [None, 'http://127.0.0.1:7890']


def test_disabled_download_waits_and_shutdown_cancels_worker(tmp_path, progress_context):
    entered, exited = threading.Event(), threading.Event()
    def factory(**kwargs):
        if not kwargs.get('allow_download'):
            raise ModelCacheMissing('missing cache')
        entered.set()
        assert progress_context['cancelled'].wait(5)
        exited.set()
        raise PreparationCancelled()
    rt = make_runtime(tmp_path, factory, automatic=False)
    with TestClient(create_http_app(rt)) as client:
        assert client.get('/api/model').json()['status'] == 'waiting'
        assert rt._model_worker is None
        assert client.post('/api/model/retry').status_code == 202
        assert entered.wait(2)
        assert client.post('/api/shutdown').status_code == 200
    assert exited.is_set() and rt.state == 'closed' and rt.audit is None
    assert not (tmp_path / 'memories.db').exists()


def test_background_validation_failure_releases_db_and_keeps_ui(tmp_path, progress_context):
    provider = FakeProvider()
    provider.warmup = Mock(side_effect=RuntimeError('warmup failed'))
    provider.close = Mock()
    def factory(**kwargs):
        if not kwargs.get('allow_download'):
            raise ModelCacheMissing('missing cache')
        return provider
    rt = make_runtime(tmp_path, factory)
    with TestClient(create_http_app(rt)) as client:
        wait_for(rt, 'failed')
        assert rt.provider is None and rt.storage is None and rt.service is None
        assert client.get('/admin/console').status_code == 200
        assert client.post('/api/maintenance/exit').status_code == 503
        from app.storage.database_lock import DatabaseWriterLock
        with DatabaseWriterLock(str(tmp_path / 'memories.db')):
            pass
    provider.close.assert_called_once()


def test_real_hf_progress_adapter_and_cancellation_restore_patches(monkeypatch):
    from app.config.configuration import PROXY_VARIABLES
    for key in PROXY_VARIABLES:
        monkeypatch.delenv(key, raising=False)
    import importlib
    module = importlib.import_module('huggingface_hub.utils.tqdm')
    original = module.tqdm
    progress = ModelProgress()
    progress.set('downloading')
    cancel = threading.Event()
    with download_progress('sentence_transformers', progress, cancel):
        bar = module.tqdm(total=100, initial=20, unit='B', desc='weights.bin', disable=True)
        bar.update(30)
        assert progress.snapshot()['percent'] == 50
        bar.close()
        cancel.set()
        with pytest.raises(PreparationCancelled):
            bar.update(1)
        cancel.clear()
    assert module.tqdm is original


def test_config_validation_and_no_proxy_credentials_in_status(tmp_path, progress_context):
    def factory(**kwargs):
        raise ModelCacheMissing('missing cache')
    rt = make_runtime(tmp_path, factory, automatic=False)
    with TestClient(create_http_app(rt)) as client:
        for value in (None, 1, 'socks5://localhost:7890'):
            assert client.post('/api/config/model', json={'proxy': {'url': value}}).status_code == 400
        assert client.post('/api/config/model', json={'port': 0}).status_code == 400
        assert client.get('/api/model').json()['ready'] is False


def test_hf_stream_download_reports_real_bytes_without_external_network(monkeypatch):
    import io
    import httpx
    from huggingface_hub import file_download
    from app.config.configuration import PROXY_VARIABLES
    for key in PROXY_VARIABLES:
        monkeypatch.delenv(key, raising=False)
    content = b'weights' * 1000
    response = httpx.Response(200, content=content, headers={'Content-Length': str(len(content))},
                              request=httpx.Request('GET', 'https://model.invalid/weights'))
    @contextmanager
    def stream(**kwargs):
        yield response
    monkeypatch.setattr(file_download, 'http_stream_backoff', stream)
    progress = ModelProgress()
    progress.set('downloading')
    output = io.BytesIO()
    with download_progress('sentence_transformers', progress, threading.Event()):
        file_download.http_get('https://model.invalid/weights', output, expected_size=len(content))
    assert output.getvalue() == content
    assert progress.snapshot()['completed'] == len(content)
    assert progress.snapshot()['total'] == len(content)
    assert progress.snapshot()['percent'] == 100


def test_unusable_cache_keeps_http_ui_and_can_be_repaired(tmp_path):
    from app.embedding.embed import ModelCacheUnusable

    provider = FakeProvider()
    provider.warmup = Mock()

    def factory(**kwargs):
        if not kwargs.get('allow_download'):
            raise ModelCacheUnusable(
                "Cached model BAAI/bge-m3 is incomplete or unusable: Unrecognized processing class")
        return provider

    rt = make_runtime(tmp_path, factory, automatic=False)
    with TestClient(create_http_app(rt)) as client:
        wait_for(rt, 'failed')
        assert rt.provider is None and rt.storage is None and rt.service is None
        assert client.get('/admin/console').status_code == 200
        status = client.get('/api/maintenance').json()
        assert status['state'] == 'maintenance'
        assert status['required_reason'] == 'model_not_ready'
        assert 'incomplete' in client.get('/api/model').json()['error']
        # The UI retry re-attempts the download, which repairs the cache.
        assert client.post('/api/model/retry').status_code == 202
        wait_for(rt, 'ready')
        provider.warmup.assert_called_once()
        assert client.post('/api/maintenance/exit').status_code == 200


def test_model_load_error_keeps_http_ui_without_auto_download(tmp_path):
    from app.embedding.embed import EmbeddingError

    def factory(**kwargs):
        raise EmbeddingError(
            "Sentence Transformers provider requires the optional dependency: pip install ...")

    rt = make_runtime(tmp_path, factory, automatic=False)
    with TestClient(create_http_app(rt)) as client:
        wait_for(rt, 'failed')
        status = client.get('/api/maintenance').json()
        assert status['state'] == 'maintenance'
        assert status['required_reason'] == 'model_not_ready'
        assert 'dependency' in client.get('/api/model').json()['error']
        assert client.get('/admin/console').status_code == 200
    assert rt.state == 'closed'


def test_stdio_still_fails_fast_on_unusable_cache(tmp_path):
    from app.embedding.embed import ModelCacheUnusable

    def factory(**kwargs):
        raise ModelCacheUnusable("Cached model is incomplete")

    rt = make_runtime(tmp_path, factory, automatic=False)
    with pytest.raises(ModelCacheUnusable):
        rt.start()
    rt.close()
    assert rt.state == 'closed'


def test_redact_error_strips_proxy_credentials():
    from app.runtime import Runtime

    env = {"HTTPS_PROXY": "http://user:secret@127.0.0.1:7890"}
    message = "download failed using http://user:secret@127.0.0.1:7890 and http://other:tok@proxy:1"
    redacted = Runtime._redact_error(message, env)
    assert "secret" not in redacted and "tok" not in redacted
    assert "[proxy]" in redacted


def test_warmup_failure_keeps_http_ui(tmp_path):
    provider = FakeProvider()
    provider.warmup = Mock(side_effect=OSError("error while deserializing header"))
    provider.close = Mock()

    def factory(**kwargs):
        return provider

    rt = make_runtime(tmp_path, factory, automatic=False)
    with TestClient(create_http_app(rt)) as client:
        wait_for(rt, 'failed')
        assert rt.provider is None and rt.storage is None and rt.service is None
        assert client.get('/admin/console').status_code == 200
        assert client.get('/api/maintenance').json()['required_reason'] == 'model_not_ready'
        assert 'deserializing' in client.get('/api/model').json()['error']
    provider.close.assert_called_once()
    assert rt.state == 'closed'
