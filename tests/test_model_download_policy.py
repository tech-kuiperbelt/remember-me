"""Cache-first provider loading and explicit model download network policy."""
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
import requests

from app.config.configuration import Configuration, PROXY_VARIABLES
from app.embedding.embed import EmbeddingError, ModelCacheMissing, OllamaEmbedder, create_embedder
from app.cli.config_migrate import migrate
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
from app.config.configuration import Configuration, activate_environment
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


@pytest.mark.parametrize("automatic,manual,cache_only,expected", [
    (True, False, False, [True, False]),
    (False, False, False, [True]),
    (False, True, False, [True, False]),
    (True, False, True, [True]),
])
def test_partial_weight_cache_obeys_download_policy(
        monkeypatch, tmp_path, automatic, manual, cache_only, expected):
    attempts = []

    class Model:
        def __init__(self, model, **kwargs):
            attempts.append(kwargs["local_files_only"])
            if kwargs["local_files_only"]:
                raise OSError(
                    f"{model} does not appear to have a file named "
                    "pytorch_model.bin or model.safetensors."
                )

        def get_sentence_embedding_dimension(self):
            return 1024

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=Model))
    (tmp_path / "config.jsonc").write_text(json.dumps({"auto_download_model": automatic}))
    env = Configuration.load(tmp_path).runtime_environment({"EMBED_PROVIDER": "sentence_transformers"})
    if expected == [True]:
        with pytest.raises(ModelCacheMissing):
            create_embedder(environment=env, allow_download=manual, cache_only=cache_only)
    else:
        assert create_embedder(environment=env, allow_download=manual).spec.dimension == 1024
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

    monkeypatch.setattr("app.embedding.embed.requests.post", post)
    with pytest.raises(EmbeddingError, match="ollama pull bge-m3"):
        OllamaEmbedder().embed_query("hello")
    assert not any(seen["proxies"].get(key) for key in ("http", "https", "all"))


@pytest.mark.parametrize("debug", [None, False, True])
def test_migration_keeps_proxy_and_download_policy(tmp_path, debug):
    project, source, env = legacy_layout(tmp_path)
    values = {"port": 1314, "language": "en", "auto_download_model": False,
              "local_only": True,
              "proxy": {"enabled": True, "url": "https://proxy.example:8443"}}
    if debug is not None:
        values["debug"] = debug
    (source / "config.jsonc").write_text(json.dumps(values))
    target = tmp_path / "new-data"
    migrate(target, source, environment=env, project_root=project)
    assert json.loads((target / "config.jsonc").read_text()) == {"debug": False, **values}


def test_download_cli_explicitly_allows_download(monkeypatch, capsys):
    from app.cli import embedding_cli
    calls = []
    provider = SimpleNamespace(spec=SimpleNamespace(as_dict=lambda: {"model": "test/model"}),
                               warmup=lambda: calls.append("warmup"))
    def factory(**kwargs):
        calls.append(kwargs)
        return provider
    monkeypatch.setattr(embedding_cli, "create_embedder", factory)
    monkeypatch.setattr(sys, "argv", ["embedding_cli", "download-model"])
    monkeypatch.delenv("EMBED_PROVIDER", raising=False)
    embedding_cli._main(None)
    assert calls[0]["allow_download"] is True
    # The resolver adopts the new-database default when no provider/profile exists.
    assert calls[0]["environment"]["EMBED_PROVIDER"] == "sentence_transformers"
    assert calls[1] == "warmup"
    assert json.loads(capsys.readouterr().out)["model"] == "test/model"


def test_incomplete_local_cache_is_classified_and_repaired(monkeypatch, tmp_path):
    from app.embedding.embed import ModelCacheUnusable

    attempts = []

    class Model:
        def __init__(self, model, **kwargs):
            attempts.append(kwargs.get("local_files_only"))
            if kwargs.get("local_files_only"):
                raise ValueError(
                    "Unrecognized processing class in test/model. Can't instantiate a processor, "
                    "a tokenizer, an image processor, a video processor or a feature extractor.")

        def get_sentence_embedding_dimension(self):
            return 3

    monkeypatch.setitem(sys.modules, "sentence_transformers",
                        SimpleNamespace(SentenceTransformer=Model))
    base = {"EMBED_PROVIDER": "sentence_transformers", "SENTENCE_TRANSFORMERS_MODEL": "test/model"}

    with pytest.raises(ModelCacheUnusable, match="incomplete or unusable"):
        create_embedder(environment=dict(base), cache_only=True)
    assert attempts == [True]

    attempts.clear()
    provider = create_embedder(environment=dict(base), allow_download=True)
    assert provider.spec.dimension == 3
    assert attempts == [True, False]


def test_unusable_cache_detector_matches_partial_snapshot_errors():
    from app.embedding.embed import _corrupt_cached_model, _unusable_cached_model

    assert _unusable_cached_model(ValueError("Unrecognized processing class in BAAI/bge-m3"))
    chained = RuntimeError("could not load the model")
    chained.__cause__ = ValueError("error while deserializing header")
    assert _corrupt_cached_model(chained)
    assert not _unusable_cached_model(ValueError("some unrelated failure"))
    assert not _corrupt_cached_model(ValueError("some unrelated failure"))


def test_corrupt_local_cache_forces_redownload(monkeypatch, tmp_path):
    calls = []

    class Model:
        def __init__(self, model, **kwargs):
            calls.append(kwargs)
            if kwargs.get("local_files_only"):
                raise RuntimeError("Error while deserializing header: PytorchStreamReader failed")

        def get_sentence_embedding_dimension(self):
            return 3

    monkeypatch.setitem(sys.modules, "sentence_transformers",
                        SimpleNamespace(SentenceTransformer=Model))
    provider = create_embedder(environment={
        "EMBED_PROVIDER": "sentence_transformers", "SENTENCE_TRANSFORMERS_MODEL": "test/model",
    }, allow_download=True)
    assert provider.spec.dimension == 3
    assert calls[0]["local_files_only"] is True
    # A corrupt blob must be re-fetched, not silently reused.
    assert calls[1]["model_kwargs"]["force_download"] is True
    assert calls[1]["processor_kwargs"]["force_download"] is True


def test_missing_dependency_is_reported_not_treated_as_cache_damage(monkeypatch, tmp_path):
    class Model:
        def __init__(self, model, **kwargs):
            if kwargs.get("local_files_only"):
                raise ValueError(
                    "SentencePieceExtractor requires the SentencePiece library but it was not found")

        def get_sentence_embedding_dimension(self):
            return 3

    monkeypatch.setitem(sys.modules, "sentence_transformers",
                        SimpleNamespace(SentenceTransformer=Model))
    with pytest.raises(EmbeddingError, match="required dependency is missing"):
        create_embedder(environment={
            "EMBED_PROVIDER": "sentence_transformers", "SENTENCE_TRANSFORMERS_MODEL": "test/model",
        }, cache_only=True)
