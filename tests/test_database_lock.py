"""Process-level ownership tests for the SQLite primary writer lock."""

import subprocess
import sys

import pytest

from app.database_lock import DatabaseWriterLock
from app.recovery import recover_ndjson_to_sqlite
from app.storage import create_storage
from app.storage_ndjson import NDJSONReplica


def _hold_writer_process(database):
    code = """
import sys
import time
from app.storage import create_storage
storage = create_storage('sqlite', path=sys.argv[1])
print('ready', flush=True)
time.sleep(30)
storage.stop()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, database],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert process.stdout.readline().strip() == "ready"
    return process


def test_factory_writer_lock_is_exclusive_across_processes_and_released(tmp_path):
    database = str(tmp_path / "memory.db")
    owner = _hold_writer_process(database)
    try:
        with pytest.raises(RuntimeError, match="active remember-me writer"):
            create_storage("sqlite", path=database)

        # Diagnostic readers do not claim the exclusive writer lock.
        reader = create_storage("sqlite", path=database, read_only=True)
        reader.stop()
    finally:
        owner.terminate()
        owner.wait(timeout=10)

    replacement = create_storage("sqlite", path=database)
    replacement.stop()


def test_dual_construction_failure_releases_primary_lock(tmp_path):
    primary = str(tmp_path / "memory.db")
    replica_path = str(tmp_path / "replica.jsonl")
    occupied_replica = NDJSONReplica(replica_path)
    occupied_replica.acquire_writer_lock()
    try:
        with pytest.raises(RuntimeError, match="NDJSON replica already has an active writer"):
            create_storage(
                "dual", primary_path=primary, secondary_path=replica_path,
                start_background=False,
            )
        # The failed dual setup must not strand the newly acquired primary lock.
        storage = create_storage("sqlite", path=primary)
        storage.stop()
    finally:
        occupied_replica.release_writer_lock()


def test_recovery_refuses_to_replace_an_owned_primary(tmp_path):
    source = tmp_path / "source.jsonl"
    NDJSONReplica(str(source)).upsert({
        "id": "memory-1", "text": "recovered", "metadata": {},
        "namespace": "default", "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00", "summary": None,
    })
    target = tmp_path / "memory.db"
    target.write_bytes(b"original database bytes")

    with DatabaseWriterLock(str(target)):
        with pytest.raises(RuntimeError, match="active remember-me writer"):
            recover_ndjson_to_sqlite(str(source), str(target), lambda _text: [1.0])

    assert target.read_bytes() == b"original database bytes"
