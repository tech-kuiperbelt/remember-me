"""Cache-first provider loading and explicit model download network policy."""
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
import requests

from app.configuration import Configuration, PROXY_VARIABLES
from app.embed import EmbeddingError, OllamaEmbedder, create_embedder
from app.config_migrate import migrate
from tests.test_configuration import legacy_layout


@pytest.mark.parametrize("values", [
    {"auto_download_model": "true"}, {"proxy": False},
    {"proxy": {"enabled": 1}}, {"proxy": {"enabled": True}},
    {"proxy": {"url": "socks5://localhost:7890"}},
    {"proxy": {"url": "http://localhost:99999"}},
    {"proxy": {"url": "http://localhost/path"}},
    {"proxy": {"other": True}},
])
def test_invalid_policy_is_rejected(tmp_path, values):
    (tmp_path / "config.jsonc").write_text(json.dumps(values))
    with pytest.raises(ValueError):
        Configuration.load(tmp_path, environment={})


def test_defaults_ignore_host_proxy_and_legacy_offline_flags(tmp_path):
    config = Configuration.load(tmp_path, environment={})
    inherited = {key: "http://host-proxy:7777" for key in PROXY_VARIABLES}
    inherited.update(SENTENCE_TRANSFORMERS_OFFLINE="true", HF_HUB_OFFLINE="1")
    env = config.runtime_environment(inherited)
    assert env["MEMORY_AUTO_DOWNLOAD_MODEL"] == "true"
    assert env["SENTENCE_TRANSFORMERS_OFFLINE"] == "false"
    assert env["HF_HUB_OFFLINE"] == "0"
    assert env["HF_HUB_DISABLE_XET"] == "1"
    assert not any(key in env for key in PROXY_VARIABLES if key != "NO_PROXY")
    assert inherited["HTTP_PROXY"] == "http://host-proxy:7777"


def test_proxy_activation_removes_stale_process_values_and_redacts(tmp_path):
    url = "http://user:secret@127.0.0.1:7890"
    (tmp_path / "config.jsonc").write_text(json.dumps({"proxy": {"enabled": True, "url": url}}))
    config = Configuration.load(tmp_path, environment={})
    assert "secret" not in json.dumps(config.as_dict(redact=True))
    assert config.as_dict()["proxy"]["url"] == url
    # A new entrypoint process, including already-set lowercase/ALL_PROXY values.
    script = """
import os, sys, requests, httpx
from app.configuration import Configuration, activate_environment
config = Configuration.load(sys.argv[1])
env = config.runtime_environment({key: value for key, value in os.environ.items()
                                  if key.lower().endswith('_proxy')})
activate_environment(env)
assert 'all_proxy' not in os.environ and 'https_proxy' not in os.environ
assert requests.utils.get_environ_proxies('https://example.invalid')['https'] == config.proxy_url
assert requests.utils.get_environ_proxies('http://127.0.0.1:11434') == {}
from huggingface_hub import constants
assert constants.HF_HUB_DISABLE_XET and not constants.HF_HUB_OFFLINE
from huggingface_hub.utils import get_session, close_session
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
class Proxy(BaseHTTPRequestHandler):
    def do_GET(self):
        assert self.path == 'http://model.example.invalid/weights'
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'model bytes')
    def log_message(self, *args):
        pass
server = ThreadingHTTPServer(('127.0.0.1', 0), Proxy)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
os.environ['HTTP_PROXY'] = 'http://127.0.0.1:' + str(server.server_port)
client = get_session()
assert client.get('http://model.example.invalid/weights').content == b'model bytes'
assert requests.get('http://model.example.invalid/weights', timeout=5).content == b'model bytes'
close_session()
server.shutdown()
server.server_close()
"""
    env = dict(os.environ, all_proxy="http://stale:9999", https_proxy="http://stale:9999")
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("provider", ["fastembed", "sentence_transformers"])
@pytest.mark.parametrize("cached,automatic,manual,expected", [
    (True, True, False, [True]), (False, True, False, [True, False]),
    (False, False, False, [True]), (False, False, True, [True, False]),
])
def test_provider_cache_first_and_manual_download_override(
        monkeypatch, tmp_path, provider, cached, automatic, manual, expected):
    attempts = []

    class Model:
        @classmethod
        def list_supported_models(cls):
            return [{"model": "test/model", "dim": 3}]

        def __init__(self, model_name, **kwargs):
            attempts.append(kwargs["local_files_only"])
            if kwargs["local_files_only"] and not cached:
                if provider == "fastembed":
                    raise ValueError("Could not load model test/model from any source.")
                raise OSError("couldn't find them in the cached files")

        def get_sentence_embedding_dimension(self):
            return 3

    monkeypatch.setitem(sys.modules, provider, SimpleNamespace(
        **{"TextEmbedding" if provider == "fastembed" else "SentenceTransformer": Model}))
    (tmp_path / "config.jsonc").write_text(json.dumps({"auto_download_model": automatic}))
    env = Configuration.load(tmp_path).runtime_environment({
        "EMBED_PROVIDER": provider, "FASTEMBED_MODEL": "test/model",
        "SENTENCE_TRANSFORMERS_MODEL": "test/model", "FASTEMBED_OFFLINE": "true",
        "SENTENCE_TRANSFORMERS_OFFLINE": "true",
    })
    if not cached and not automatic and not manual:
        with pytest.raises(EmbeddingError, match="bin/manage embedding download-model"):
            create_embedder(environment=env)
    else:
        result = create_embedder(environment=env, allow_download=manual)
        assert result.spec.model == "test/model"
    assert attempts == expected


def test_load_errors_do_not_download_and_download_failure_is_actionable(monkeypatch, tmp_path):
    attempts = []
    error = PermissionError("cache permission denied")

    class Model:
        def __init__(self, model, **kwargs):
            attempts.append(kwargs["local_files_only"])
            if kwargs["local_files_only"]:
                raise error
            raise ConnectionError("proxy unreachable")

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=Model))
    env = Configuration.load(tmp_path).runtime_environment({"EMBED_PROVIDER": "sentence_transformers"})
    with pytest.raises(PermissionError):
        create_embedder(environment=env)
    assert attempts == [True]
    attempts.clear()
    error = OSError("couldn't find them in the cached files")
    with pytest.raises(EmbeddingError, match="config.jsonc proxy"):
        create_embedder(environment=env)
    assert attempts == [True, False]


def test_loopback_ollama_bypasses_all_proxy_and_missing_model_help(monkeypatch):
    for key in PROXY_VARIABLES:
        monkeypatch.setenv(key, "http://broken:9999")
    seen = {}

    def post(url, **kwargs):
        seen.update(requests.Session().merge_environment_settings(url, kwargs["proxies"], False, None, None))
        return SimpleNamespace(ok=False, status_code=404, json=lambda: {"error": "model missing"})

    monkeypatch.setattr("app.embed.requests.post", post)
    with pytest.raises(EmbeddingError, match="ollama pull bge-m3"):
        OllamaEmbedder().embed_query("hello")
    assert not any(seen["proxies"].get(key) for key in ("http", "https", "all"))


def test_migration_keeps_proxy_and_download_policy(tmp_path):
    project, source, env = legacy_layout(tmp_path)
    values = {"port": 1314, "language": "en", "auto_download_model": False,
              "proxy": {"enabled": True, "url": "https://proxy.example:8443"}}
    (source / "config.jsonc").write_text(json.dumps(values))
    target = tmp_path / "new-data"
    migrate(target, source, environment=env, project_root=project)
    assert json.loads((target / "config.jsonc").read_text()) == values


def test_download_cli_explicitly_allows_download(monkeypatch, capsys):
    from app import embedding_cli
    calls = []
    provider = SimpleNamespace(spec=SimpleNamespace(as_dict=lambda: {"model": "test/model"}),
                               warmup=lambda: calls.append("warmup"))
    def factory(**kwargs):
        calls.append(kwargs)
        return provider
    monkeypatch.setattr(embedding_cli, "create_embedder", factory)
    monkeypatch.setattr(sys, "argv", ["embedding_cli", "download-model"])
    embedding_cli._main(None)
    assert calls == [{"allow_download": True}, "warmup"]
    assert json.loads(capsys.readouterr().out)["model"] == "test/model"
