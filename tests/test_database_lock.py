"""Process-level ownership tests for the SQLite primary writer lock."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.database_lock import DatabaseWriterLock
from app.recovery import recover_ndjson_to_sqlite
from app.storage import create_storage
from app.storage_ndjson import NDJSONReplica


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


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


def _hold_dual_writer_process(database, replica):
    code = """
import sys
import time
from app.storage import create_storage
storage = create_storage(
    'dual', primary_path=sys.argv[1], secondary_path=sys.argv[2],
    start_background=False,
)
print('ready', flush=True)
time.sleep(30)
storage.stop()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, database, replica],
        cwd=REPOSITORY_ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert process.stdout.readline().strip() == "ready"
    return process


def _run_contender(*args):
    env = {**os.environ, "MEMORY_STORAGE": "sqlite"}
    return subprocess.run(
        [sys.executable, *args], cwd=REPOSITORY_ROOT, env=env,
        capture_output=True, text=True, timeout=10,
    )


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


def test_dual_service_rejects_all_writer_contenders_but_allows_diagnostics(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text('{}')
    monkeypatch.setenv("MEMORY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEMORY_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    database = str(tmp_path / "memory.db")
    replica = str(tmp_path / "memory.jsonl")
    alternate_replica = str(tmp_path / "alternate.jsonl")
    alternate_database = str(tmp_path / "alternate.db")
    recovery_source = str(tmp_path / "recovery.jsonl")
    NDJSONReplica(recovery_source).replace_all([])
    owner = _hold_dual_writer_process(database, replica)
    try:
        primary_conflicts = [
            (
                "second sqlite service",
                ("-c", "import sys; from app.storage import create_storage; "
                 "create_storage('sqlite', path=sys.argv[1])", database),
            ),
            (
                "second dual service",
                ("-c", "import sys; from app.storage import create_storage; "
                 "create_storage('dual', primary_path=sys.argv[1], "
                 "secondary_path=sys.argv[2], start_background=False)",
                 database, alternate_replica),
            ),
            (
                "embedding maintenance",
                ("-m", "app.embedding_cli", "migrate-codec", database),
            ),
            (
                "category migration",
                ("-m", "app.category_migrate_deepseek", "--db", database,
                 "--limit", "0"),
            ),
            (
                "keyword migration",
                ("-m", "app.keyword_migrate_deepseek", "--db", database,
                 "--limit", "0"),
            ),
            (
                "offline recovery",
                ("-c", "import sys; from app.recovery import recover_ndjson_to_sqlite; "
                 "recover_ndjson_to_sqlite(sys.argv[1], sys.argv[2], lambda _text: [1.0])",
                 recovery_source, database),
            ),
        ]
        for label, command in primary_conflicts:
            result = _run_contender(*command)
            assert result.returncode != 0, label
            assert "active remember-me writer" in result.stderr, (label, result.stderr)

        replica_conflict = _run_contender(
            "-c", "import sys; from app.storage import create_storage; "
            "create_storage('dual', primary_path=sys.argv[1], "
            "secondary_path=sys.argv[2], start_background=False)",
            alternate_database, replica,
        )
        assert replica_conflict.returncode != 0
        assert "NDJSON replica already has an active writer" in replica_conflict.stderr

        read_only_commands = [
            ("-m", "app.embedding_cli", "status", database),
            ("-m", "app.category_migrate_deepseek", "--db", database,
             "--limit", "0", "--dry-run"),
            ("-m", "app.keyword_migrate_deepseek", "--db", database,
             "--limit", "0", "--dry-run"),
        ]
        for command in read_only_commands:
            result = _run_contender(*command)
            assert result.returncode == 0, result.stderr
    finally:
        owner.terminate()
        owner.wait(timeout=10)

    replacement = create_storage(
        "dual", primary_path=database, secondary_path=replica,
        start_background=False,
    )
    replacement.stop()
