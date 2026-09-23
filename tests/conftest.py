"""Shared fixtures for the remember-me test suite.

All services are explicitly constructed with temporary stores and fake providers.
The environment override remains a guard for CLI and subprocess tests.
"""

import os
import sys
import tempfile
import pytest


def pytest_configure(config):
    """Runs before any test module import — pin storage to in-memory SQLite."""
    config._test_environment = dict(os.environ)
    config._test_directory = tempfile.TemporaryDirectory(prefix="remember-me-tests-")
    for key in list(os.environ):
        if key.startswith(("MEMORY_", "EMBED_", "FASTEMBED_", "SENTENCE_TRANSFORMERS_", "OLLAMA_")):
            os.environ.pop(key)
    os.environ.update({
        "MEMORY_STORAGE": "sqlite", "MEMORY_STORAGE_PATH": ":memory:",
        "MEMORY_DEFAULT_NAMESPACE": "default",
        "MEMORY_AUDIT_PATH": os.path.join(config._test_directory.name, "audit.jsonl"),
        "EMBED_PROVIDER": "ollama", "EMBED_MODEL": "bge-m3", "EMBED_DIM": "1024",
        "EMBED_PRELOAD": "false", "OLLAMA_BASE_URL": "http://127.0.0.1:1",
    })

    from app import audit
    audit.configure(os.environ)


def pytest_unconfigure(config):
    audit = sys.modules.get("app.audit")
    if audit is not None and audit._file is not None:
        audit._file.close()
        audit._file = None
    directory = getattr(config, "_test_directory", None)
    if directory is not None:
        directory.cleanup()
    original = getattr(config, "_test_environment", None)
    if original is not None:
        for key in list(os.environ):
            if key.startswith(("MEMORY_", "EMBED_", "FASTEMBED_", "SENTENCE_TRANSFORMERS_", "OLLAMA_")):
                os.environ.pop(key)
        for key, value in original.items():
            if key.startswith(("MEMORY_", "EMBED_", "FASTEMBED_", "SENTENCE_TRANSFORMERS_", "OLLAMA_")):
                os.environ[key] = value


FAKE_EMB = [0.1, 0.2, 0.3, 0.4]


@pytest.fixture
def make_storage(tmp_path):
    """Factory that creates a fresh SQLite business store."""

    def _make(tag: str):
        from app.storage_sqlite import SQLiteStorage
        return SQLiteStorage(":memory:")

    return _make


@pytest.fixture
def storage(make_storage):
    """A fresh destination storage instance (parameterized over backends)."""
    return make_storage("dst")


@pytest.fixture
def fake_emb():
    """A fake embedding vector — dedup logic never inspects its contents."""
    return list(FAKE_EMB)


class FakeProvider:
    def __init__(self):
        from app.embed import EmbeddingSpec
        self.spec = EmbeddingSpec("ollama", "test", 3)

    def embed_document(self, text):
        return [0.1, 0.2, 0.3]

    def embed_query(self, text):
        return self.embed_document(text)

    def embed_documents(self, texts):
        return [self.embed_document(text) for text in texts]

    def warmup(self):
        pass


@pytest.fixture
def service_factory():
    from app.service import MemoryService
    from app.settings import Settings
    from app.storage_sqlite import SQLiteStorage
    services = []
    stores = []
    def make(storage=None, provider=None, settings=None):
        if storage is None:
            storage = SQLiteStorage(":memory:")
            stores.append(storage)
        result = MemoryService(settings or Settings.from_env(), storage, provider or FakeProvider())
        services.append(result)
        return result
    yield make
    for service in services:
        service.close()
    for store in stores:
        store.close()


@pytest.fixture
def service(service_factory):
    return service_factory()


@pytest.fixture
def admin(service):
    from app.admin import AdminAPI
    return AdminAPI(service)


def server_namespace(service=None):
    from app import server
    result = vars(server).copy()
    # Tool schemas/helpers do not access resources; constructing a fake service
    # is unnecessary for those tests.
    from unittest.mock import MagicMock
    api = server.create_mcp_server(service if service is not None else MagicMock())
    result.update({name: getattr(api, name) for name in
                   ('handle_call_tool','handle_list_tools','handle_read_resource','handle_list_resources')})
    return result
