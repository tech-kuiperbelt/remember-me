"""Vector-free NDJSON disaster-recovery replica tests."""

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.storage.recovery import quick_sync, recover_ndjson_to_sqlite
from app.storage.ndjson import NDJSONReplica
from app.storage.sqlite import SQLiteStorage
from app.storage.dual import DualStorage
import app.storage.dual as storage_dual_module


def _lines(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _record(memory_id, text, metadata=None, namespace="default", summary=None,
            created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-01T00:00:00+00:00"):
    return {"id": memory_id, "text": text, "metadata": metadata or {},
            "namespace": namespace, "created_at": created_at,
            "updated_at": updated_at, "summary": summary}


def test_ndjson_omits_embedding_on_write_and_legacy_load(tmp_path, fake_emb):
    path = tmp_path / "memories.jsonl"
    replica = NDJSONReplica(str(path))
    replica.upsert({**_record("one", "body", {"keywords": ["k"]}, summary="short"),
                    "embedding": fake_emb, "synced": 1, "version": 9, "deleted": False})

    assert "embedding" not in _lines(path)[0]
    assert "synced" not in _lines(path)[0]
    assert "version" not in _lines(path)[0]
    assert "deleted" not in _lines(path)[0]
    assert "embedding" not in NDJSONReplica(str(path)).list_records()[0]
    with pytest.raises(AttributeError):
        getattr(replica, "search")

    legacy = {**_lines(path)[0], "embedding": fake_emb}
    path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    loaded = NDJSONReplica(str(path))
    assert "embedding" not in loaded.list_records()[0]
    loaded.replace_all(loaded.list_records(limit=10))
    assert "embedding" not in _lines(path)[0]


def test_ndjson_batch_and_snapshot_fsync_before_publish(tmp_path, monkeypatch):
    path = tmp_path / "memories.jsonl"
    replica = NDJSONReplica(str(path))
    real_fsync = storage_dual_module.os.fsync
    calls = []

    def track_fsync(fd):
        calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr("app.storage.ndjson.os.fsync", track_fsync)
    replica.append_batch([_record("one", "body")])
    replica.replace_all(replica.list_records(limit=10))

    # One fsync for append, then one for the replacement file and directory.
    assert len(calls) == 3


def test_full_sync_publishes_vector_free_snapshot(tmp_path, fake_emb):
    primary = SQLiteStorage(":memory:")
    first = primary.add("first", fake_emb, metadata={"keywords": ["one"]}, namespace="ns")
    second = primary.add("second", fake_emb, metadata={"keywords": ["two"]}, namespace="ns")
    replica_path = tmp_path / "memories.jsonl"
    secondary = NDJSONReplica(str(replica_path))

    result = quick_sync(SimpleNamespace(primary=primary, secondary=secondary))

    assert result["synced"] == 2
    assert set(result["ids"]) == {first["id"], second["id"]}
    assert all("embedding" not in line for line in _lines(replica_path))
    assert secondary.get_stats()["total"] == 2


def test_full_export_syncs_verifies_and_copies_immutable_snapshot(tmp_path, fake_emb):
    primary = SQLiteStorage(":memory:")
    added = primary.add("exported", fake_emb, namespace="ns")
    replica_path = tmp_path / "memories.jsonl"
    export_path = tmp_path / "download.jsonl"
    dual = DualStorage(primary, NDJSONReplica(str(replica_path)), start_background=False)
    try:
        result = dual.export_full_snapshot(str(export_path))
        before = export_path.read_bytes()
        primary.add("later", fake_emb, namespace="ns")

        assert result["records"] == 1
        assert result["integrity"] == {
            "missing": 0, "extra": 0, "mismatched": 0, "corrupt_lines": 0,
        }
        assert result["generated_at"]
        load_ids = {item["id"] for item in NDJSONReplica(
            str(export_path)).list_records(limit=10)}
        assert load_ids == {added["id"]}
        assert export_path.read_bytes() == before
        health = dual.get_stats()["ndjson_health_check"]
        assert health["last_result"] == "healthy"
        assert health["last_trigger"] == "full_export"
    finally:
        dual.stop()


def test_full_sync_keeps_existing_replica_when_primary_read_fails(tmp_path, fake_emb):
    replica_path = tmp_path / "memories.jsonl"
    secondary = NDJSONReplica(str(replica_path))
    secondary.upsert(_record("existing", "existing backup"))

    class FailingPrimary:
        def export_records(self):
            raise RuntimeError("primary read failed")

    with pytest.raises(RuntimeError, match="primary read failed"):
        quick_sync(SimpleNamespace(primary=FailingPrimary(), secondary=secondary), batch_size=1)
    assert NDJSONReplica(str(replica_path)).list_records()[0]["text"] == "existing backup"


def test_offline_recovery_reembeds_and_preserves_original_primary_on_failure(tmp_path, fake_emb):
    source_path = tmp_path / "memories.jsonl"
    source = NDJSONReplica(str(source_path))
    source.upsert(_record("first-id", "first", {"keywords": ["one"]}, "ns", "s1"))
    source.upsert(_record("second-id", "second", {"keywords": ["two"]}, "ns", "s2"))
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
    assert rebuilt.get("first-id")["embedding"] == pytest.approx([0.9, 0.8], abs=0.001)
    assert rebuilt.get("second-id")["metadata"]["keywords"] == ["two"]
    assert rebuilt.get("first-id")["version"] == 0
    assert rebuilt.get("first-id")["synced"] == 1
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
    assert unchanged.get("first-id")["text"] == "first"
    unchanged.conn.close()


def test_due_compact_reclaims_stale_lines_persists_state_and_updates_stats(
        tmp_path, fake_emb, monkeypatch):
    """A due job compacts atomically, then does not repeat after a restart."""
    audit_record = MagicMock()
    monkeypatch.setattr(storage_dual_module.audit, "record", audit_record)
    primary_path = tmp_path / "primary.db"
    replica_path = tmp_path / "memories.jsonl"
    primary = SQLiteStorage(str(primary_path))
    secondary = NDJSONReplica(str(replica_path))
    secondary.upsert(_record("first", "first", namespace="ns"))
    secondary.upsert(_record("second", "second", namespace="ns"))
    secondary.upsert(_record("first", "first", namespace="ns", summary="revised",
                             updated_at="2026-01-02T00:00:00+00:00"))
    secondary.append_tombstone("second", "2026-01-02T00:00:00+00:00")
    before = replica_path.stat().st_size

    dual = DualStorage(primary, secondary, compact_interval=24 * 60 * 60,
                       compact_min_bytes=10 ** 9, compact_min_ratio=0.25,
                       start_background=False)
    dual._run_due_compact("startup")

    lines = _lines(replica_path)
    assert len(lines) == 1
    assert lines[0]["id"] == "first"
    assert lines[0]["summary"] == "revised"
    assert replica_path.stat().st_size < before
    state = dual.get_stats()["ndjson_compact"]
    assert state["last_result"] == "compacted"
    assert state["last_reclaimed_bytes"] > 0
    assert state["next_due_at"]
    assert audit_record.call_args.args[0] == "ndjson_compact"
    assert audit_record.call_args.args[2]["outcome"] == "compacted"

    dual.stop()
    reloaded_primary = SQLiteStorage(str(primary_path))
    reloaded = DualStorage(reloaded_primary, NDJSONReplica(str(replica_path)),
                           compact_interval=24 * 60 * 60, start_background=False)
    compact_mock = MagicMock(wraps=reloaded.secondary.compact)
    reloaded.secondary.compact = compact_mock
    reloaded._run_due_compact("startup")
    assert compact_mock.call_count == 0
    reloaded.stop()


def test_due_compact_records_error_without_touching_replica(tmp_path, fake_emb, monkeypatch):
    audit_record = MagicMock()
    monkeypatch.setattr(storage_dual_module.audit, "record", audit_record)
    primary = SQLiteStorage(":memory:")
    replica_path = tmp_path / "memories.jsonl"
    secondary = NDJSONReplica(str(replica_path))
    secondary.upsert(_record("entry", "body"))
    secondary.upsert(_record("entry", "body", summary="new",
                             updated_at="2026-01-02T00:00:00+00:00"))
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
    dual.stop()


def test_dual_rejects_a_second_writer_for_the_same_replica(tmp_path):
    replica_path = tmp_path / "memories.jsonl"
    first = DualStorage(SQLiteStorage(":memory:"), NDJSONReplica(str(replica_path)),
                        start_background=False)
    try:
        with pytest.raises(RuntimeError, match="active writer"):
            DualStorage(SQLiteStorage(":memory:"), NDJSONReplica(str(replica_path)),
                        start_background=False)
    finally:
        first.stop()


def test_stats_detects_id_drift_and_corrupt_lines(tmp_path, fake_emb):
    primary = SQLiteStorage(":memory:")
    primary.add("primary-only", fake_emb)
    replica_path = tmp_path / "memories.jsonl"
    secondary = NDJSONReplica(str(replica_path))
    secondary.upsert(_record("extra", "replica-only"))
    with replica_path.open("a", encoding="utf-8") as handle:
        handle.write("{broken}\n")
    dual = DualStorage(primary, secondary, start_background=False)
    try:
        stats = dual.get_stats()
        assert stats["in_sync"] is False
        assert stats["replica_integrity"]["missing"] == 1
        assert stats["replica_integrity"]["extra"] == 1
        assert stats["replica_integrity"]["corrupt_lines"] == 1
        assert stats["replica_integrity"]["checked_at"]
    finally:
        dual.stop()


def test_health_check_uses_id_query_not_full_export(tmp_path, fake_emb, monkeypatch):
    """Live health must not load full text/metadata from the primary."""
    primary = SQLiteStorage(":memory:")
    primary.add("only", fake_emb)
    dual = DualStorage(primary, NDJSONReplica(str(tmp_path / "r.jsonl")),
                       start_background=False)
    try:
        dual.force_sync()
        calls = []
        real = primary.export_records
        monkeypatch.setattr(primary, "export_records",
                            lambda *a, **k: calls.append(1) or real(*a, **k))
        stats = dual.get_stats()
        assert stats["in_sync"] is True
        assert stats["pending"] == 0
        assert calls == []
    finally:
        dual.stop()


def test_health_check_detects_drift_with_zero_pending(tmp_path, fake_emb):
    primary = SQLiteStorage(":memory:")
    primary.add("primary-only", fake_emb)
    secondary = NDJSONReplica(str(tmp_path / "r.jsonl"))
    dual = DualStorage(primary, secondary, start_background=False)
    try:
        dual.force_sync()
        assert primary.count_unsynced() == 0
        secondary.upsert(_record("extra", "replica-only"))
        stats = dual.get_stats()
        assert stats["pending"] == 0
        assert stats["in_sync"] is False
        assert stats["replica_integrity"]["extra"] == 1
    finally:
        dual.stop()


def test_background_startup_syncs_pending_before_health_check(
        tmp_path, fake_emb, monkeypatch):
    audit_record = MagicMock()
    monkeypatch.setattr(storage_dual_module.audit, "record", audit_record)
    primary = SQLiteStorage(":memory:")
    memory_id = primary.add("pending at startup", fake_emb)["id"]
    replica_path = tmp_path / "memories.jsonl"
    dual = DualStorage(
        primary, NDJSONReplica(str(replica_path)), sync_interval=3600,
        health_check_interval=86400, start_background=True,
    )
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            state = primary._get_storage_state("ndjson_health_check", {})
            if state.get("last_result") == "healthy":
                break
            time.sleep(0.01)
        else:
            pytest.fail("startup health check did not finish")

        assert primary.get(memory_id)["synced"] == 1
        assert NDJSONReplica(str(replica_path)).list_records(limit=10)[0]["id"] == memory_id
        assert state["pending"] == 0
        assert state["last_trigger"] == "startup"
        health_calls = [call for call in audit_record.call_args_list
                        if call.args[0] == "ndjson_health_check"]
        assert health_calls[-1].args[2]["outcome"] == "healthy"
    finally:
        dual.stop()


def test_health_check_daily_schedule_and_drift_retry_without_repair(
        tmp_path, fake_emb, monkeypatch):
    audit_record = MagicMock()
    monkeypatch.setattr(storage_dual_module.audit, "record", audit_record)
    primary = SQLiteStorage(":memory:")
    primary.add("healthy", fake_emb)
    replica_path = tmp_path / "memories.jsonl"
    dual = DualStorage(
        primary, NDJSONReplica(str(replica_path)), health_check_interval=86400,
        start_background=False,
    )
    try:
        dual._incremental_sync()
        assert dual._run_due_health_check("startup", force=True) is True
        assert dual._run_due_health_check("scheduled") is False

        healthy_state = dual._health_state()
        healthy_state["last_check_at"] = time.time() - 86401
        primary._set_storage_state("ndjson_health_check", healthy_state)
        assert dual._run_due_health_check("scheduled") is True
        assert dual._health_state()["last_result"] == "healthy"

        # External damage is reported but never repaired automatically.
        replica_path.write_text("", encoding="utf-8")
        dual.secondary._load()
        assert dual._run_due_health_check("manual-test", force=True) is True
        drift_state = dual._health_state()
        assert drift_state["last_result"] == "drift"
        assert drift_state["missing"] == 1
        assert replica_path.read_text(encoding="utf-8") == ""
        assert dual._run_due_health_check("scheduled") is False

        drift_state["last_check_at"] = time.time() - 3601
        primary._set_storage_state("ndjson_health_check", drift_state)
        assert dual._run_due_health_check("scheduled") is True
        assert dual._health_state()["last_result"] == "drift"
        assert replica_path.read_text(encoding="utf-8") == ""
    finally:
        dual.stop()


def test_stats_exposes_live_and_automatic_replica_health(tmp_path, fake_emb):
    primary = SQLiteStorage(":memory:")
    primary.add("healthy", fake_emb)
    dual = DualStorage(
        primary, NDJSONReplica(str(tmp_path / "memories.jsonl")),
        start_background=False,
    )
    try:
        dual._incremental_sync()
        dual._run_due_health_check("startup", force=True)
        stats = dual.get_stats()

        assert stats["in_sync"] is True
        assert stats["pending"] == 0
        assert stats["replica_integrity"]["missing"] == 0
        assert stats["replica_integrity"]["checked_at"]
        assert stats["ndjson_health_check"]["last_result"] == "healthy"
        assert stats["ndjson_health_check"]["next_due_at"]
    finally:
        dual.stop()


def test_health_check_error_retries_after_one_hour(tmp_path, fake_emb, monkeypatch):
    primary = SQLiteStorage(":memory:")
    primary.add("healthy", fake_emb)
    secondary = NDJSONReplica(str(tmp_path / "memories.jsonl"))
    dual = DualStorage(primary, secondary, start_background=False)
    try:
        dual._incremental_sync()
        real_get_stats = secondary.get_stats

        def fail_stats():
            raise OSError("replica unreadable")

        monkeypatch.setattr(secondary, "get_stats", fail_stats)
        assert dual._run_due_health_check("scheduled", force=True) is True
        state = dual._health_state()
        assert state["last_result"] == "error"
        assert "replica unreadable" in state["last_error"]
        assert dual._run_due_health_check("scheduled") is False

        monkeypatch.setattr(secondary, "get_stats", real_get_stats)
        state["last_check_at"] = time.time() - 3601
        primary._set_storage_state("ndjson_health_check", state)
        assert dual._run_due_health_check("scheduled") is True
        assert dual._health_state()["last_result"] == "healthy"
    finally:
        dual.stop()


def test_admin_stats_renders_replica_health_fields():
    ui = Path("web/index.html").read_text(encoding="utf-8")

    for label in ("Replica Health", "Recovery Action", "Run Full Export below",
                  "The service will not repair replica drift automatically",
                  "Pending", "Replica Integrity", "Last Automatic Check",
                  "Next Automatic Check"):
        assert label in ui
