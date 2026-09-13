"""Vector-free NDJSON disaster-recovery replica tests."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.recovery import quick_sync, recover_ndjson_to_sqlite
from app.storage_ndjson import NDJSONStorage
from app.storage_sqlite import SQLiteStorage
from app.storage_dual import DualStorage
import app.storage_dual as storage_dual_module


def _lines(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_ndjson_omits_embedding_on_write_and_legacy_load(tmp_path, fake_emb):
    path = tmp_path / "memories.jsonl"
    replica = NDJSONStorage(str(path))
    result = replica.add("body", fake_emb, metadata={"keywords": ["k"]}, summary="short")

    assert "embedding" not in _lines(path)[0]
    assert "embedding" not in NDJSONStorage(str(path)).get(result["id"])
    with pytest.raises(RuntimeError, match="does not retain embeddings"):
        replica.search(fake_emb)

    legacy = {**_lines(path)[0], "embedding": fake_emb}
    path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    loaded = NDJSONStorage(str(path))
    assert "embedding" not in loaded.get(result["id"])
    loaded.replace_all(loaded.list_memories(limit=10))
    assert "embedding" not in _lines(path)[0]


def test_full_sync_publishes_vector_free_snapshot(tmp_path, fake_emb):
    primary = SQLiteStorage(":memory:")
    first = primary.add("first", fake_emb, metadata={"keywords": ["one"]}, namespace="ns")
    second = primary.add("second", fake_emb, metadata={"keywords": ["two"]}, namespace="ns")
    replica_path = tmp_path / "memories.jsonl"
    secondary = NDJSONStorage(str(replica_path))

    result = quick_sync(SimpleNamespace(primary=primary, secondary=secondary))

    assert result["synced"] == 2
    assert set(result["ids"]) == {first["id"], second["id"]}
    assert all("embedding" not in line for line in _lines(replica_path))
    assert secondary.get_stats()["total"] == 2


def test_full_sync_keeps_existing_replica_when_primary_read_fails(tmp_path, fake_emb):
    replica_path = tmp_path / "memories.jsonl"
    secondary = NDJSONStorage(str(replica_path))
    existing = secondary.add("existing backup", fake_emb)

    class FailingPrimary:
        def list_memories(self, limit, offset):
            if offset == 0:
                return [{"id": "new", "text": "new", "embedding": fake_emb,
                         "metadata": {}, "namespace": "ns", "created_at": "1",
                         "updated_at": "1", "summary": None}]
            raise RuntimeError("primary read failed")

    with pytest.raises(RuntimeError, match="primary read failed"):
        quick_sync(SimpleNamespace(primary=FailingPrimary(), secondary=secondary), batch_size=1)
    assert NDJSONStorage(str(replica_path)).get(existing["id"])["text"] == "existing backup"


def test_offline_recovery_reembeds_and_preserves_original_primary_on_failure(tmp_path, fake_emb):
    source_path = tmp_path / "memories.jsonl"
    source = NDJSONStorage(str(source_path))
    first = source.add("first", fake_emb, metadata={"keywords": ["one"]}, namespace="ns", summary="s1")
    second = source.add("second", fake_emb, metadata={"keywords": ["two"]}, namespace="ns", summary="s2")
    target_path = tmp_path / "memories.db"
    original = SQLiteStorage(str(target_path))
    original.add("old primary", fake_emb, namespace="old")
    original.conn.close()  # Recovery is offline; no process holds the target.

    calls = []

    def embed_ok(text):
        calls.append(text)
        return [0.9, 0.8]

    result = recover_ndjson_to_sqlite(str(source_path), str(target_path), embed_ok)
    rebuilt = SQLiteStorage(str(target_path))
    assert result == {"recovered": 2, "errors": 0}
    assert set(calls) == {"first", "second"}
    assert rebuilt.get(first["id"])["embedding"] == pytest.approx([0.9, 0.8], abs=0.001)
    assert rebuilt.get(second["id"])["metadata"]["keywords"] == ["two"]
    assert rebuilt.get_stats()["total"] == 2
    rebuilt.conn.close()

    stable = SQLiteStorage(str(target_path))
    stable.conn.close()

    def embed_fails(text):
        raise RuntimeError("ollama unavailable")

    with pytest.raises(RuntimeError, match="ollama unavailable"):
        recover_ndjson_to_sqlite(str(source_path), str(target_path), embed_fails)
    unchanged = SQLiteStorage(str(target_path))
    assert unchanged.get_stats()["total"] == 2
    assert unchanged.get(first["id"])["text"] == "first"
    unchanged.conn.close()


def test_due_compact_reclaims_stale_lines_persists_state_and_updates_stats(
        tmp_path, fake_emb, monkeypatch):
    """A due job compacts atomically, then does not repeat after a restart."""
    audit_record = MagicMock()
    monkeypatch.setattr(storage_dual_module.audit, "record", audit_record)
    primary_path = tmp_path / "primary.db"
    replica_path = tmp_path / "memories.jsonl"
    primary = SQLiteStorage(str(primary_path))
    secondary = NDJSONStorage(str(replica_path))
    first = secondary.add("first", fake_emb, namespace="ns")
    second = secondary.add("second", fake_emb, namespace="ns")
    secondary.update(first["id"], summary="revised")
    secondary.delete(second["id"])
    before = replica_path.stat().st_size

    dual = DualStorage(primary, secondary, compact_interval=24 * 60 * 60,
                       compact_min_bytes=10 ** 9, compact_min_ratio=0.25,
                       start_background=False)
    dual._run_due_compact("startup")

    lines = _lines(replica_path)
    assert len(lines) == 1
    assert lines[0]["id"] == first["id"]
    assert lines[0]["summary"] == "revised"
    assert replica_path.stat().st_size < before
    state = dual.get_stats()["ndjson_compact"]
    assert state["last_result"] == "compacted"
    assert state["last_reclaimed_bytes"] > 0
    assert state["next_due_at"]
    assert audit_record.call_args.args[0] == "ndjson_compact"
    assert audit_record.call_args.args[2]["outcome"] == "compacted"

    primary.conn.close()
    reloaded_primary = SQLiteStorage(str(primary_path))
    reloaded = DualStorage(reloaded_primary, NDJSONStorage(str(replica_path)),
                           compact_interval=24 * 60 * 60, start_background=False)
    compact_mock = MagicMock(wraps=reloaded.secondary.compact)
    reloaded.secondary.compact = compact_mock
    reloaded._run_due_compact("startup")
    assert compact_mock.call_count == 0
    reloaded_primary.conn.close()


def test_due_compact_records_error_without_touching_replica(tmp_path, fake_emb, monkeypatch):
    audit_record = MagicMock()
    monkeypatch.setattr(storage_dual_module.audit, "record", audit_record)
    primary = SQLiteStorage(":memory:")
    replica_path = tmp_path / "memories.jsonl"
    secondary = NDJSONStorage(str(replica_path))
    entry = secondary.add("body", fake_emb)
    secondary.update(entry["id"], summary="new")
    before = replica_path.read_text(encoding="utf-8")
    dual = DualStorage(primary, secondary, compact_min_bytes=0,
                       compact_min_ratio=1.0, start_background=False)

    def fail_compact():
        raise OSError("disk full")

    monkeypatch.setattr(secondary, "compact", fail_compact)
    dual._run_due_compact("startup")

    assert replica_path.read_text(encoding="utf-8") == before
    state = dual.get_stats()["ndjson_compact"]
    assert state["last_result"] == "error"
    assert "disk full" in state["last_error"]
    assert audit_record.call_args.kwargs["error"]["type"] == "OSError"
