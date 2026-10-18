"""Shared-connection concurrency regression tests for SQLiteStorage (plan 50.5.7).

Two layers guard the shared ``sqlite3`` connection:

* A deterministic lock guard injects an instrumented RLock plus a connection
  proxy and asserts that every online connection access happens while the
  storage lock is held. This is the primary regression gate.
* Subprocess workers reproduce the real cross-thread stall with a hard timeout
  (child dumps all thread stacks at 20s, parent kills at 30s), so a regression
  can never hang the whole pytest run.
"""

import subprocess
import sys
import threading
from pathlib import Path

import pytest

from app.embedding.embed import EmbeddingSpec
from app.storage.sqlite import SQLiteStorage

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_SPEC = EmbeddingSpec("test", "smoke", 3).as_dict()
VECTORS = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


# -- deterministic lock guard -------------------------------------------------

class TrackedLock:
    """RLock-compatible wrapper that records the owning thread (test-only)."""

    def __init__(self):
        self._lock = threading.RLock()
        self._owner = None
        self._depth = 0

    def acquire(self, blocking=True, timeout=-1):
        if timeout == -1:
            ok = self._lock.acquire(blocking)
        else:
            ok = self._lock.acquire(blocking, timeout)
        if ok:
            self._owner = threading.get_ident()
            self._depth += 1
        return ok

    def release(self):
        self._lock.release()
        self._depth -= 1
        if self._depth == 0:
            self._owner = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False

    def owned_by_current_thread(self) -> bool:
        return self._owner == threading.get_ident()


def _assert_locked(lock):
    if not lock.owned_by_current_thread():
        raise AssertionError(
            "SQLite connection used without holding the storage lock")


class _GuardedCursor:
    """Cursor proxy asserting the storage lock is held on every fetch."""

    def __init__(self, cursor, lock):
        self._cursor = cursor
        self._lock = lock

    def fetchone(self):
        _assert_locked(self._lock)
        return self._cursor.fetchone()

    def fetchall(self):
        _assert_locked(self._lock)
        return self._cursor.fetchall()

    def fetchmany(self, *args):
        _assert_locked(self._lock)
        return self._cursor.fetchmany(*args)

    def __iter__(self):
        _assert_locked(self._lock)
        return iter(self._cursor)

    def __getattr__(self, name):
        # Result attributes (e.g. rowcount) are not connection access.
        return getattr(self._cursor, name)


class _GuardedConnection:
    """Connection proxy asserting the storage lock is held on every call."""

    def __init__(self, conn, lock):
        self._conn = conn
        self._lock = lock

    def execute(self, *args, **kwargs):
        _assert_locked(self._lock)
        return _GuardedCursor(self._conn.execute(*args, **kwargs), self._lock)

    def executemany(self, *args, **kwargs):
        _assert_locked(self._lock)
        return _GuardedCursor(self._conn.executemany(*args, **kwargs), self._lock)

    def executescript(self, *args, **kwargs):
        _assert_locked(self._lock)
        return self._conn.executescript(*args, **kwargs)

    def commit(self):
        _assert_locked(self._lock)
        return self._conn.commit()

    def rollback(self):
        _assert_locked(self._lock)
        return self._conn.rollback()

    def close(self):
        _assert_locked(self._lock)
        return self._conn.close()

    def cursor(self, *args, **kwargs):
        _assert_locked(self._lock)
        return _GuardedCursor(self._conn.cursor(*args, **kwargs), self._lock)

    def __getattr__(self, name):
        # Non-statement attributes delegate unchanged.
        return getattr(self._conn, name)


def _profiled_storage():
    storage = SQLiteStorage(":memory:", embedding_spec=PROFILE_SPEC)
    for i in range(3):
        storage.add(f"record {i}", VECTORS[i],
                    metadata={"keywords": [f"kw{i}"], "anchors": [f"anchor-{i}"]})
    return storage


def _install_guard(storage):
    lock = TrackedLock()
    storage._lock = lock
    storage.conn = _GuardedConnection(storage.conn, lock)
    return lock


def test_lock_guard_detects_unlocked_connection_access():
    """The guard must actually fail when a connection call skips the lock."""
    storage = _profiled_storage()
    lock = _install_guard(storage)
    with pytest.raises(AssertionError):
        storage.conn.execute("SELECT 1")
    with lock:
        cursor = storage.conn.execute("SELECT 1")
    with pytest.raises(AssertionError):
        cursor.fetchone()
    storage.close()


def test_online_connection_access_holds_storage_lock():
    """Every online path touched here must hold the storage lock."""
    storage = _profiled_storage()
    _install_guard(storage)

    memory_id = storage.list_memories(limit=1)[0]["id"]
    assert storage.get(memory_id)["id"] == memory_id

    storage.list_memories(limit=5, updated_after="2000-01-01T00:00:00Z")
    storage.list_memories_page(limit=10, updated_after="2000-01-01T00:00:00Z")
    storage.search([1.0, 0.0, 0.0], k=2)
    storage.search_lexical("record")
    storage.search_lexical("record", categories=["design"], keywords=["kw1"],
                           updated_after="2000-01-01T00:00:00Z", min_score=0.0)
    storage.search_anchor("anchor-0")
    storage.search_anchor("anchor-0", keywords=["kw0"], min_score=0.0)
    storage.get_unsynced_records(limit=10)
    storage.get_details([memory_id])
    storage.get_stats()
    storage.get_keyword_vocabulary()
    storage.export_records()
    storage.rebuild_keyword_index()
    storage.mark_synced_batch([memory_id])
    storage.count_unsynced()
    storage.update(memory_id, text="record updated", embedding=[0.0, 1.0, 0.0],
                   metadata={"keywords": ["kw1"]})
    storage.add("guarded add", [0.0, 0.0, 1.0],
                metadata={"keywords": ["kw2"], "anchors": ["anchor-x"]})
    storage.delete(memory_id)
    storage.close()


# -- subprocess reproduction --------------------------------------------------

_WORKER_SOURCE = r'''
import faulthandler, sys, threading, traceback

faulthandler.dump_traceback_later(20, exit=True)

from app.embedding.embed import EmbeddingSpec
from app.storage.sqlite import SQLiteStorage

MODE = sys.argv[1]
DB_PATH = sys.argv[2]
SPEC = EmbeddingSpec("test", "smoke", 3).as_dict()


def finish(code):
    faulthandler.cancel_dump_traceback_later()
    sys.exit(code)


storage = SQLiteStorage(DB_PATH, embedding_spec=SPEC)
errors = []
barrier = threading.Barrier(4)


def record_error(exc):
    errors.append(exc)
    traceback.print_exc()


def run_list():
    try:
        barrier.wait()
        for _ in range(300):
            rows = storage.list_memories(
                limit=25, updated_after="2000-01-01T00:00:00Z")
            if len(rows) != 25:
                raise AssertionError("expected 25 rows, got %d" % len(rows))
            for row in rows:
                # Browse/list rows must not load vectors (read-path N+1 fix).
                if row["embedding"] is not None:
                    raise AssertionError("list rows must not carry embeddings")
    except BaseException as exc:
        record_error(exc)


def run_mixed():
    try:
        barrier.wait()
        for _ in range(200):
            storage.get("nonexistent")
            storage.list_memories(
                limit=10, updated_after="2000-01-01T00:00:00Z")
            storage.search_lexical("record")
            storage.search_lexical("record", categories=["design"], keywords=["kw1"],
                                   updated_after="2000-01-01T00:00:00Z", min_score=0.0)
            storage.search_anchor("anchor-0")
            storage.search_anchor("anchor-0", keywords=["kw0"], min_score=0.0)
            storage.get_unsynced_records(limit=10)
            storage.get_stats()
    except BaseException as exc:
        record_error(exc)


target = run_list if MODE == "profile-list" else run_mixed
threads = [threading.Thread(target=target) for _ in range(4)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()

storage.close()
if errors:
    finish(1)
print("WORKER_OK", flush=True)
finish(0)
'''


def _build_worker_db(path, profiled):
    storage = SQLiteStorage(str(path), embedding_spec=PROFILE_SPEC if profiled else None)
    for i in range(200):
        storage.add(f"record {i}", VECTORS[i % 3],
                    metadata={"keywords": [f"kw{i % 10}"], "anchors": [f"anchor-{i % 5}"]})
    storage.close()


def _run_worker(mode, db_path):
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _WORKER_SOURCE, mode, str(db_path)],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"{mode} worker exceeded the 30s hard timeout\n"
                    f"STDOUT:\n{exc.stdout}\nSTDERR:\n{exc.stderr}")
    if proc.returncode != 0:
        pytest.fail(f"{mode} worker failed (rc={proc.returncode})\n"
                    f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    assert "WORKER_OK" in proc.stdout


def test_time_filtered_list_concurrency(tmp_path):
    """4x300 time-filtered list queries with an active embedding profile."""
    db_path = tmp_path / "concurrency-profile.db"
    _build_worker_db(db_path, profiled=True)
    _run_worker("profile-list", db_path)


@pytest.mark.parametrize("mode,profiled", [
    ("profile-mixed", True),
    ("plain-mixed", False),
])
def test_mixed_concurrency(tmp_path, mode, profiled):
    """Interleaved get/list/fts/outbox/stats with and without a profile."""
    db_path = tmp_path / f"concurrency-{mode}.db"
    _build_worker_db(db_path, profiled=profiled)
    _run_worker(mode, db_path)
