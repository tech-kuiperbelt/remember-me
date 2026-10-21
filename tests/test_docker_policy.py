"""Official Docker policy uses the existing configuration and lifecycle."""
import json
import time
from unittest.mock import Mock, patch

import pytest
from starlette.testclient import TestClient

from app.config.configuration import Configuration
from app.config.settings import Settings
from app.embedding.embed import ModelCacheMissing, ModelCacheUnusable
from app.runtime import Runtime
from app.server import create_http_app
from tests.conftest import FakeProvider


@pytest.mark.parametrize('docker', [False, True])
def test_docker_effective_policy_preserves_file(tmp_path, docker):
    original = '// preserved\n{"local_only":false,"auto_download_model":true}'
    path = tmp_path / 'config.jsonc'
    path.write_text(original)
    environment = {'MEMORY_DEPLOYMENT_MODE': 'docker'} if docker else {}
    settings = Settings.from_config(tmp_path, environment=environment)
    assert settings.LOCAL_ONLY is docker
    assert settings.DOCKER_MODE is docker
    assert settings.environment['MEMORY_AUTO_DOWNLOAD_MODEL'] == str(not docker).lower()
    assert settings.environment['HF_HUB_OFFLINE'] == ('1' if docker else '0')
    assert settings.environment['TRANSFORMERS_OFFLINE'] == ('1' if docker else '0')
    assert path.read_text() == original
    assert Configuration.load(tmp_path).local_only is False


@pytest.mark.parametrize('failure', [ModelCacheMissing, ModelCacheUnusable])
def test_docker_retry_loads_locally_and_recovers_without_download(tmp_path, failure, monkeypatch):
    config = tmp_path / 'config.jsonc'
    config.write_text('// retained\n{"local_only":false,"auto_download_model":true}')
    settings = Settings.from_config(tmp_path, environment={
        'MEMORY_DEPLOYMENT_MODE': 'docker', 'MEMORY_STORAGE': 'dual',
        'EMBED_PROVIDER': 'sentence_transformers',
    })
    calls, restored = [], False
    provider = FakeProvider()
    provider.warmup = Mock()

    def factory(**kwargs):
        calls.append(kwargs)
        assert kwargs.get('cache_only') is True
        assert not kwargs.get('allow_download')
        if not restored:
            raise failure('unavailable internal /opt/model/path')
        return provider

    forbidden = Mock(side_effect=AssertionError('Docker must not download'))
    monkeypatch.setattr('app.runtime.prepare_model_in_process', forbidden)
    monkeypatch.setattr('app.runtime.download_progress', forbidden)
    runtime = Runtime(settings, provider_factory=factory, configure_logging=False)

    def wait_worker():
        deadline = time.monotonic() + 5
        while runtime._model_worker is not None:
            assert time.monotonic() < deadline
            time.sleep(.01)

    with patch.dict('os.environ'), TestClient(create_http_app(runtime)) as client:
        assert len(calls) == 1  # no automatic download despite the old file
        assert not client.get('/api/model').json()['ready']
        assert client.post('/api/model/retry').status_code == 202
        wait_worker()
        assert client.get('/api/model').json()['status'] == 'failed'
        assert 'data-deployment-mode="docker"' in client.get('/admin/console').text
        saved = client.post('/api/config', json={'language': 'en', 'debug': True})
        assert saved.status_code == 200
        assert saved.json()['auto_download_model'] is True
        assert saved.json()['local_only'] is False
        restored = True
        assert client.post('/api/model/retry').status_code == 202
        wait_worker()
        assert client.get('/api/model').json()['ready'] is True
        assert client.get('/api/maintenance').json()['state'] == 'maintenance'
        assert client.post('/api/maintenance/exit').status_code == 200
        provider.warmup.assert_called_once()
        forbidden.assert_not_called()
    assert config.read_text().startswith('// retained')
    assert json.loads(config.read_text().split('\n', 1)[1])['local_only'] is False
