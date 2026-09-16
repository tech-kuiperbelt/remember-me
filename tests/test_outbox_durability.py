"""Failure and concurrent-write boundaries of the SQLite -> NDJSON outbox."""

import subprocess
import sys
import threading
from pathlib import Path

from app.storage_dual import DualStorage
from app.storage_ndjson import NDJSONReplica
from app.storage_sqlite import SQLiteStorage
import app.storage_ndjson as storage_ndjson_module


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class _BlockingReplica(NDJSONReplica):
    def __init__(self, path):
        super().__init__(path)
        self.written = threading.Event()
        self.release = threading.Event()

    def append_batch(self, records):
        super().append_batch(records)
        self.written.set()
        assert self.release.wait(timeout=5)


def test_concurrent_update_remains_pending_after_old_version_published(tmp_path, fake_emb):
    primary = SQLiteStorage(":memory:")
    replica = _BlockingReplica(str(tmp_path / "memories.jsonl"))
    dual = DualStorage(primary, replica, start_background=False)
    try:
        memory_id = dual.add("old text", fake_emb, namespace="ns")["id"]
        real_get_unsynced = primary._get_unsynced
        calls = 0

        def one_batch_only(*args, **kwargs):
            nonlocal calls
            calls += 1
            return real_get_unsynced(*args, **kwargs) if calls == 1 else []

        primary._get_unsynced = one_batch_only
        worker = threading.Thread(target=dual._incremental_sync)
        worker.start()
        assert replica.written.wait(timeout=5)

        assert dual.update(memory_id, text="new text", embedding=fake_emb)
        replica.release.set()
        worker.join(timeout=5)
        assert not worker.is_alive()

        pending = primary.get(memory_id)
        assert pending["version"] == 1
        assert pending["synced"] == 0
        assert replica.list_records(limit=10)[0]["text"] == "old text"

        primary._get_unsynced = real_get_unsynced
        dual._incremental_sync()
        assert primary.get(memory_id)["synced"] == 1
        assert replica.list_records(limit=10)[0]["text"] == "new text"
    finally:
        dual.stop()


def test_fsync_failure_leaves_outbox_pending_for_retry(tmp_path, fake_emb, monkeypatch):
    primary = SQLiteStorage(":memory:")
    replica = NDJSONReplica(str(tmp_path / "memories.jsonl"))
    dual = DualStorage(primary, replica, start_background=False)
    real_fsync = storage_ndjson_module.os.fsync
    try:
        memory_id = dual.add("durable memory", fake_emb, namespace="ns")["id"]

        def fail_fsync(_fd):
            raise OSError("simulated fsync failure")

        monkeypatch.setattr(storage_ndjson_module.os, "fsync", fail_fsync)
        dual._incremental_sync()
        assert primary.get(memory_id)["synced"] == 0

        monkeypatch.setattr(storage_ndjson_module.os, "fsync", real_fsync)
        dual._incremental_sync()
        assert primary.get(memory_id)["synced"] == 1
        assert replica.list_records(limit=10)[0]["text"] == "durable memory"
    finally:
        dual.stop()


def test_process_crash_after_fsync_retries_without_data_loss(tmp_path, fake_emb):
    """An abrupt exit after durable append must leave SQLite pending."""
    database = tmp_path / "memories.db"
    replica_path = tmp_path / "memories.jsonl"
    primary = SQLiteStorage(str(database))
    memory_id = primary.add("survives crash", fake_emb, namespace="ns")["id"]
    primary.close()

    crash_script = """
import os
import sys
from app.storage_dual import DualStorage
from app.storage_ndjson import NDJSONReplica
from app.storage_sqlite import SQLiteStorage

class CrashAfterDurableAppend(NDJSONReplica):
    def append_batch(self, records):
        super().append_batch(records)
        os._exit(91)

primary = SQLiteStorage(sys.argv[1], writer_lock=True)
dual = DualStorage(
    primary, CrashAfterDurableAppend(sys.argv[2]), start_background=False
)
dual._incremental_sync()
raise AssertionError("sync unexpectedly returned")
"""
    crashed = subprocess.run(
        [sys.executable, "-c", crash_script, str(database), str(replica_path)],
        cwd=REPOSITORY_ROOT, capture_output=True, text=True, timeout=10,
    )
    assert crashed.returncode == 91, crashed.stderr

    after_crash = SQLiteStorage(str(database))
    try:
        assert after_crash.get(memory_id)["synced"] == 0
    finally:
        after_crash.close()
    assert NDJSONReplica(str(replica_path)).list_records(limit=10)[0]["id"] == memory_id
    assert len(replica_path.read_text(encoding="utf-8").splitlines()) == 1

    # The crash released both OS locks. A replacement instance retries the
    # pending version, which is intentionally at-least-once in the journal.
    recovered = DualStorage(
        SQLiteStorage(str(database), writer_lock=True),
        NDJSONReplica(str(replica_path)), start_background=False,
    )
    try:
        recovered._incremental_sync()
        assert recovered.primary.get(memory_id)["synced"] == 1
        assert recovered.secondary.list_records(limit=10)[0]["text"] == "survives crash"
        assert len(replica_path.read_text(encoding="utf-8").splitlines()) == 2

        recovered.secondary.compact()
        assert len(replica_path.read_text(encoding="utf-8").splitlines()) == 1
    finally:
        recovered.stop()
