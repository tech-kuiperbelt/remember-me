"""Main-case tests for the service layer (memory_add dedup behavior).

``app.service`` is imported lazily inside the fixture. ``pytest_configure``
(conftest.py) pins env vars to in-memory SQLite BEFORE import, so importing
the module creates no real files. Within tests we mock the module-level
globals (storage, embed, summary_worker.enqueue) so no Ollama call and no
background summary work ever happens.
"""

import json
import time
from unittest.mock import MagicMock

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


def test_memory_add_supplied_summary_skips_worker(service, mocked):
    mocked["storage"].add.return_value = {"id": "abc123", "created": True}

    result = service.memory_add("some text", namespace="ns1",
                                summary="  caller summary  ")

    assert result == {"id": "abc123", "created": True}
    mocked["storage"].add.assert_called_once_with(
        "some text", [0.1, 0.2, 0.3], metadata=None, namespace="ns1",
        summary="caller summary",
    )
    mocked["enqueue"].assert_not_called()


def test_memory_add_blank_summary_uses_worker(service, mocked):
    mocked["storage"].add.return_value = {"id": "abc123", "created": True}

    service.memory_add("some text", summary="  \n ")

    _, kwargs = mocked["storage"].add.call_args
    assert kwargs["summary"] is None
    mocked["enqueue"].assert_called_once_with("abc123", "some text")


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


def test_memory_update_text_with_summary_skips_worker(service, mocked):
    mocked["storage"].update.return_value = True

    result = service.memory_update("abc", text="new text",
                                   summary="  new summary  ")

    assert result == {"success": True}
    mocked["storage"].update.assert_called_once_with(
        "abc", text="new text", embedding=[0.1, 0.2, 0.3], metadata=None,
        namespace=None, summary="new summary",
    )
    mocked["enqueue"].assert_not_called()


@pytest.mark.parametrize("summary", [None, "", "  \n "])
def test_memory_update_text_without_summary_uses_worker(service, mocked, summary):
    mocked["storage"].update.return_value = True

    service.memory_update("abc", text="new text", summary=summary)

    _, kwargs = mocked["storage"].update.call_args
    assert kwargs["summary"] == ""
    mocked["enqueue"].assert_called_once_with("abc", "new text")


def test_memory_update_summary_only(service, mocked):
    mocked["storage"].update.return_value = True

    service.memory_update("abc", summary=" corrected ")

    mocked["storage"].update.assert_called_once_with(
        "abc", text=None, embedding=None, metadata=None, namespace=None,
        summary="corrected",
    )
    mocked["enqueue"].assert_not_called()


def test_memory_update_metadata_only_preserves_summary(service, mocked):
    mocked["storage"].update.return_value = True

    service.memory_update("abc", metadata={"category": "rule"})

    _, kwargs = mocked["storage"].update.call_args
    assert kwargs["summary"] is None
    mocked["enqueue"].assert_not_called()


def test_storage_paths_dual(service, monkeypatch):
    """Dual mode exposes primary (sqlite) and secondary (jsonl) paths."""
    storage = MagicMock()
    storage.get_stats.return_value = {"backend": "dual"}
    storage.primary.path = "data/memories.db"
    storage.secondary.path = "data/memories.jsonl"
    monkeypatch.setattr(service, "storage", storage)

    result = service.memory_storage_paths()
    assert result["backend"] == "dual"
    assert result["primary_path"] == "data/memories.db"
    assert result["secondary_path"] == "data/memories.jsonl"


def test_storage_paths_single_backend(service, monkeypatch):
    """Single-backend mode reports backend only, no replica paths."""
    storage = MagicMock()
    storage.get_stats.return_value = {"backend": "sqlite"}
    # MagicMock auto-creates attributes; remove them to simulate a
    # single-backend storage that has no primary/secondary.
    del storage.primary
    del storage.secondary
    monkeypatch.setattr(service, "storage", storage)

    result = service.memory_storage_paths()
    assert result["backend"] == "sqlite"
    assert "primary_path" not in result


def test_memory_import_accepts_only_jsonl(service, tmp_path):
    path = tmp_path / "legacy.sqlite"
    path.write_bytes(b"SQLite format 3\0")
    with pytest.raises(ValueError, match="jsonl or .ndjson"):
        service.memory_import_start(str(path), "legacy.sqlite")


def test_memory_import_background_job_preserves_missing_summary_without_worker(
        service, mocked, monkeypatch, tmp_path):
    path = tmp_path / "backup.jsonl"
    path.write_text(json.dumps({
        "id": "source-id", "text": "body", "metadata": {}, "namespace": "old",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00", "summary": None,
    }) + "\n", encoding="utf-8")
    mocked["storage"].export_records.return_value = []
    mocked["storage"].get_stats.return_value = {
        "embedding_profile": {"active": "profile"}
    }
    mocked["storage"].missing_index_value_embeddings.return_value = []
    mocked["storage"].import_records.return_value = {
        "imported": 1, "skipped": 0, "renamed": 0, "imported_ids": ["source-id"],
    }
    monkeypatch.setattr(service, "embed_documents", lambda texts: [[0.1] for _ in texts])
    monkeypatch.setattr(service.audit, "record", MagicMock())

    started = service.memory_import_start(str(path), "backup.jsonl", "archive-")
    deadline = time.time() + 2
    while time.time() < deadline:
        job = service.memory_import_status(started["import_id"])
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(0.01)

    assert job["status"] == "completed"
    assert job["result"]["imported"] == 1
    records = mocked["storage"].import_records.call_args.args[0]
    assert records[0]["namespace"] == "archive-old"
    assert records[0]["summary"] is None
    mocked["enqueue"].assert_not_called()
    assert not path.exists()


def test_memory_import_rejects_concurrent_job(service, monkeypatch, tmp_path):
    path = tmp_path / "backup.jsonl"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(service, "_active_import_id", "im_running")

    with pytest.raises(RuntimeError, match="already running"):
        service.memory_import_start(str(path), "backup.jsonl")

    assert path.exists()  # ownership stays with the rejected caller


@pytest.mark.parametrize("backend", ["json", "ndjson", "jsonl", "unknown"])
def test_removed_backends_are_rejected(backend):
    """Removed business backends must fail rather than silently falling back.

    Tested in a subprocess so the running pytest process's app.service module
    state is never mutated.
    """
    import subprocess
    import sys

    code = (
        f"import os; os.environ['MEMORY_STORAGE']='{backend}'; "
        "os.environ['MEMORY_STORAGE_PATH']='/tmp/never-created'; "
        "import app.service"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "MEMORY_STORAGE" in proc.stderr


def test_replica_path_prefers_new_name_and_derives_default(service, caplog):
    assert service.resolve_replica_path("data/main.sqlite", None, None) == "data/main.jsonl"
    assert service.resolve_replica_path("data/main.db", "new.ndjson", "old.jsonl") == "new.ndjson"
    assert "deprecated and ignored" in caplog.text


def test_replica_path_accepts_legacy_alias_with_warning(service, caplog):
    assert service.resolve_replica_path("data/main.db", None, "old.jsonl") == "old.jsonl"
    assert "deprecated" in caplog.text
