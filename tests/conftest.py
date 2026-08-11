"""Shared fixtures for the remember-me test suite.

Critical: ``app.service`` reads env vars and creates storage + starts the
summary worker thread at import time. ``pytest_configure`` runs before any
test module is imported, so we pin the storage to an in-memory SQLite here.
That way, importing ``app.service`` inside tests never touches real data
files and never hits Ollama.
"""

import os
import pytest


def pytest_configure(config):
    """Runs before any test module import — pin storage to in-memory SQLite."""
    os.environ.setdefault("MEMORY_STORAGE", "sqlite")
    os.environ.setdefault("MEMORY_STORAGE_PATH", ":memory:")


FAKE_EMB = [0.1, 0.2, 0.3, 0.4]


@pytest.fixture(params=["sqlite", "json", "ndjson"])
def backend(request):
    """Parameterized storage backend name."""
    return request.param


@pytest.fixture
def make_storage(backend, tmp_path):
    """Factory that creates a fresh, isolated storage instance per backend.

    SQLite uses ``:memory:`` (each connection is an independent DB); JSON and
    NDJSON use distinct files under pytest's tmp_path. Nothing touches the
    project's real ``data/`` directory.
    """

    def _make(tag: str):
        if backend == "sqlite":
            from app.storage_sqlite import SQLiteStorage
            return SQLiteStorage(":memory:")
        if backend == "json":
            from app.storage_json import JSONStorage
            return JSONStorage(str(tmp_path / f"{tag}.json"))
        from app.storage_ndjson import NDJSONStorage
        return NDJSONStorage(str(tmp_path / f"{tag}.jsonl"))

    return _make


@pytest.fixture
def storage(make_storage):
    """A fresh destination storage instance (parameterized over backends)."""
    return make_storage("dst")


@pytest.fixture
def fake_emb():
    """A fake embedding vector — dedup logic never inspects its contents."""
    return list(FAKE_EMB)
