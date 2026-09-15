"""Shared fixtures for the remember-me test suite.

Critical: ``app.service`` reads env vars and creates storage at import time.
``pytest_configure`` runs before any test module is imported, so we pin the
storage to an in-memory SQLite here. That way, importing ``app.service``
inside tests never touches real data files.
"""

import os
import pytest


def pytest_configure(config):
    """Runs before any test module import — pin storage to in-memory SQLite."""
    os.environ.setdefault("MEMORY_STORAGE", "sqlite")
    os.environ.setdefault("MEMORY_STORAGE_PATH", ":memory:")


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
