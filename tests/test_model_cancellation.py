"""Model-download cancellation must release provider and dependency resources."""
import sys
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.embedding import embed as embed_module
from app.embedding.embed import EmbeddingError, create_embedder
from app.operations.model_preparation import PreparationCancelled
from tests.test_model_bootstrap import preserve_process_environment  # noqa: F401 (autouse)


class _Model:
    """Stand-in for a loaded model that must be closed when construction fails."""

    instances: list = []

    def __init__(self, model, **kwargs):
        self.closed = 0
        type(self).instances.append(self)

    def get_sentence_embedding_dimension(self):
        raise NotImplementedError

    def close(self):
        self.closed += 1


def _install_model(monkeypatch, model_class):
    model_class.instances = []
    monkeypatch.setitem(sys.modules, "sentence_transformers",
                        SimpleNamespace(SentenceTransformer=model_class))
    return model_class


def test_constructor_closes_model_when_later_step_fails(monkeypatch):
    class MissingDimension(_Model):
        def get_sentence_embedding_dimension(self):
            return None

    installed = _install_model(monkeypatch, MissingDimension)
    with pytest.raises(EmbeddingError, match="no embedding dimension"):
        create_embedder(environment={"EMBED_PROVIDER": "sentence_transformers"}, allow_download=True)
    assert installed.instances and installed.instances[0].closed == 1


def test_close_is_idempotent(monkeypatch):
    class Working(_Model):
        def get_sentence_embedding_dimension(self):
            return 3

    installed = _install_model(monkeypatch, Working)
    provider = create_embedder(environment={"EMBED_PROVIDER": "sentence_transformers"}, allow_download=True)
    provider.close()
    provider.close()
    assert installed.instances[0].closed == 1


def test_interrupted_download_resets_session_and_propagates(monkeypatch):
    import huggingface_hub.utils._http as http

    calls = []
    monkeypatch.setattr(http, "close_session", lambda: calls.append("close"), raising=False)
    monkeypatch.setattr(http, "reset_sessions", lambda: calls.append("reset"), raising=False)

    def load(local_only):
        if local_only:
            raise OSError("couldn't find them in the cached files")
        raise PreparationCancelled()

    with pytest.raises(PreparationCancelled):
        embed_module._load_local_model(load, "test/model", "/tmp/cache", False, True)
    assert calls == ["close"]


def test_failed_download_resets_session_and_wraps_error(monkeypatch):
    import huggingface_hub.utils._http as http

    calls = []
    monkeypatch.setattr(http, "close_session", lambda: calls.append("close"), raising=False)

    def load(local_only):
        if local_only:
            raise OSError("couldn't find them in the cached files")
        raise ConnectionError("proxy unreachable")

    with pytest.raises(EmbeddingError, match="Could not download/load"):
        embed_module._load_local_model(load, "test/model", "/tmp/cache", False, True)
    assert calls == ["close"]


def test_runtime_closes_provider_when_construction_is_cancelled(tmp_path, monkeypatch):
    from app.config.settings import Settings
    from app.embedding.embed import ModelCacheMissing
    from app.runtime import Runtime
    from tests.conftest import FakeProvider

    (tmp_path / "config.jsonc").write_text('{"auto_download_model": true}')
    provider = FakeProvider()
    provider.close = Mock()

    def factory(**kwargs):
        if kwargs.get("allow_download"):
            return provider
        raise ModelCacheMissing("missing cache")

    @contextmanager
    def cancelling_context(provider_name, progress, cancelled):
        # Cancel right after the factory returns, before ownership is adopted.
        yield
        raise PreparationCancelled()

    monkeypatch.setattr("app.runtime.download_progress", cancelling_context)
    settings = Settings.from_env({
        "MEMORY_STORAGE": "sqlite", "MEMORY_DATA_DIR": str(tmp_path),
        "MEMORY_STORAGE_PATH": str(tmp_path / "m.db"),
        "MEMORY_AUDIT_PATH": str(tmp_path / "audit.jsonl"),
        "EMBED_PRELOAD": "false", "EMBED_PROVIDER": "ollama", "EMBED_MODEL": "bge-m3",
    })
    runtime = Runtime(settings, provider_factory=factory, configure_logging=False)
    try:
        runtime.start(background_model=True)
        deadline = time.monotonic() + 5
        while runtime._model_worker is not None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert runtime._model_worker is None
        assert runtime.provider is None
        provider.close.assert_called_once()
    finally:
        runtime.close()


def test_runtime_closes_provider_when_transfer_is_interrupted(tmp_path, monkeypatch):
    """A non-``PreparationCancelled`` BaseException from the download context
    must not orphan the constructed provider. Previously ``self.provider`` was
    assigned inside the context, so this failure class left it set and unclosed
    until ``Runtime.close()``."""
    from app.config.settings import Settings
    from app.embedding.embed import ModelCacheMissing
    from app.runtime import Runtime
    from tests.conftest import FakeProvider

    (tmp_path / "config.jsonc").write_text('{"auto_download_model": true}')
    provider = FakeProvider()
    provider.close = Mock()

    def factory(**kwargs):
        if kwargs.get("allow_download"):
            return provider
        raise ModelCacheMissing("missing cache")

    class WorkerStop(BaseException):
        pass

    @contextmanager
    def interrupting_context(provider_name, progress, cancelled):
        yield
        raise WorkerStop()

    monkeypatch.setattr("app.runtime.download_progress", interrupting_context)
    # The uncaught BaseException terminates the worker; silence its traceback.
    monkeypatch.setattr(threading, "excepthook", lambda args: None)
    settings = Settings.from_env({
        "MEMORY_STORAGE": "sqlite", "MEMORY_DATA_DIR": str(tmp_path),
        "MEMORY_STORAGE_PATH": str(tmp_path / "m.db"),
        "MEMORY_AUDIT_PATH": str(tmp_path / "audit.jsonl"),
        "EMBED_PRELOAD": "false", "EMBED_PROVIDER": "ollama", "EMBED_MODEL": "bge-m3",
    })
    runtime = Runtime(settings, provider_factory=factory, configure_logging=False)
    try:
        runtime.start(background_model=True)
        deadline = time.monotonic() + 5
        while not provider.close.called and time.monotonic() < deadline:
            time.sleep(0.01)
        # Closed by _prepare_model itself, before Runtime.close() runs.
        provider.close.assert_called_once()
    finally:
        runtime.close()
