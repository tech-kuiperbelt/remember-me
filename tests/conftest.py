"""Shared fixtures for the remember-me test suite.

Critical: ``app.service`` reads env vars and creates storage at import time.
``pytest_configure`` runs before any test module is imported, so we pin the
storage to an in-memory SQLite here. That way, importing ``app.service``
inside tests never touches real data files.
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
