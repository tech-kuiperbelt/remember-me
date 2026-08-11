"""Main-case tests for the service layer (memory_add dedup behavior).

``app.service`` is imported lazily inside the fixture. ``pytest_configure``
(conftest.py) pins env vars to in-memory SQLite BEFORE import, so importing
the module creates no real files. Within tests we mock the module-level
globals (storage, embed, summary_worker.enqueue) so no Ollama call and no
background summary work ever happens.
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def service():
    """Import app.service once (env already pinned to :memory:)."""
    import app.service
    return app.service


@pytest.fixture
def mocked(service, monkeypatch):
    """Replace storage / embed / summary_worker with mocks."""
    storage = MagicMock()
    enqueue = MagicMock()
    monkeypatch.setattr(service, "storage", storage)
    monkeypatch.setattr(service, "embed", lambda text: [0.1, 0.2, 0.3])
    monkeypatch.setattr(service.summary_worker, "enqueue", enqueue)
    return {"storage": storage, "enqueue": enqueue}


def test_memory_add_new_enqueues_summary(service, mocked):
    """created=True → returns {id, created} and schedules background summary."""
    mocked["storage"].add.return_value = {"id": "abc123", "created": True}

    result = service.memory_add("some text", namespace="ns1")

    assert result == {"id": "abc123", "created": True}
    mocked["enqueue"].assert_called_once_with("abc123", "some text")
    mocked["storage"].add.assert_called_once()


def test_memory_add_duplicate_skips_summary(service, mocked):
    """created=False (duplicate) → returns existing id, NO summary enqueue."""
    mocked["storage"].add.return_value = {"id": "abc123", "created": False}

    result = service.memory_add("some text", namespace="ns1")

    assert result == {"id": "abc123", "created": False}
    mocked["enqueue"].assert_not_called()


def test_memory_add_default_namespace(service, mocked):
    """Namespace falls back to the module default when not provided."""
    mocked["storage"].add.return_value = {"id": "x", "created": True}
    service.memory_add("text")
    _, kwargs = mocked["storage"].add.call_args
    assert kwargs.get("namespace") == service.DEFAULT_NAMESPACE


def test_memory_add_passes_namespace_through(service, mocked):
    """Explicit namespace is forwarded to storage.add."""
    mocked["storage"].add.return_value = {"id": "x", "created": True}
    service.memory_add("text", namespace="custom-ns")
    _, kwargs = mocked["storage"].add.call_args
    assert kwargs.get("namespace") == "custom-ns"


def test_memory_add_storage_error_propagates(service, mocked):
    """embed/storage exceptions are not swallowed."""
    mocked["storage"].add.side_effect = RuntimeError("disk full")
    with pytest.raises(RuntimeError):
        service.memory_add("text", namespace="ns1")
