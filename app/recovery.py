"""Recovery and sync utilities for DualStorage.

This module is separate from storage.py to avoid ABC pollution.
All methods operate on MemoryStorage instances via public/internal API.

Functions
---------
quick_sync(storage)
    Full rebuild of secondary from one primary snapshot.
recover_ndjson_to_sqlite(source_path, target_path, embed_fn, batch_size=500)
    Offline, atomic recovery from a vector-free NDJSON replica.
"""

import logging
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from .storage import create_storage
from .jsonl_transfer import load_jsonl
from .database_lock import DatabaseWriterLock

logger = logging.getLogger("recovery")


def quick_sync(storage, batch_size=500):
    """Atomically replace the NDJSON replica from the SQLite primary.

    Preserves all memory IDs in one atomic replica snapshot.
    Filters out soft-deleted records from primary.
    Reads through the SQLite snapshot export contract.

    Parameters
    ----------
    storage : DualStorage
    batch_size : int

    Returns
    -------
    dict with keys: synced, errors, ids
    """
    primary = storage.primary
    secondary = storage.secondary

    memories = primary.export_records()

    secondary.replace_all(memories)

    logger.info("quick_sync: synced=%d", len(memories))
    return {"synced": len(memories), "errors": 0,
            "ids": [m["id"] for m in memories]}


def recover_ndjson_to_sqlite(source_path: str, target_path: str, embed_fn,
                             batch_size: int = 500, embedding_spec: dict | None = None,
                             embedding_dtype: str = "float16",
                             embed_documents=None) -> dict:
    """Offline, atomic rebuild of SQLite from a vector-free NDJSON backup.

    A temporary SQLite file is built entirely before it replaces ``target_path``.
    Any embedding failure leaves the prior primary untouched.
    """
    target = Path(target_path)
    temp = Path(f"{target}.recover.tmp")
    temp_sidecars = [Path(f"{temp}-wal"), Path(f"{temp}-shm")]
    target_sidecars = [Path(f"{target}-wal"), Path(f"{target}-shm")]
    with DatabaseWriterLock(str(target)):
        try:
            for path in [temp, *temp_sidecars]:
                if path.exists():
                    path.unlink()
            recovered = build_recovery_database(
                source_path, str(temp), embed_fn, batch_size=batch_size,
                embedding_spec=embedding_spec, embedding_dtype=embedding_dtype,
                embed_documents=embed_documents)
            os.replace(temp, target)
            for path in target_sidecars:
                try:
                    if path.exists():
                        path.unlink()
                except OSError:
                    logger.warning("offline recover: could not remove stale sidecar %s", path,
                                   exc_info=True)
            logger.info("offline recover: rebuilt=%d target=%s", recovered, target)
            return {"recovered": recovered, "errors": 0}
        finally:
            for path in [temp, *temp_sidecars]:
                if path.exists():
                    path.unlink()


def build_recovery_database(source_path, temp_path, embed_fn, *, batch_size=500,
                            embedding_spec=None, embedding_dtype="float16",
                            embed_documents=None, max_records=None, progress=None):
    """Build and verify a complete temporary primary without touching the live DB."""
    source_records = load_jsonl(source_path, max_records=max_records)["records"]
    rebuilt = create_storage("sqlite", path=temp_path, embedding_spec=embedding_spec,
                             embedding_dtype=embedding_dtype)
    recovered = 0
    try:
        for offset in range(0, len(source_records), batch_size):
            for memory in source_records[offset:offset + batch_size]:
                embedding = embed_fn(memory["text"])
                if not embedding:
                    raise RuntimeError(f"empty embedding for memory {memory['id']}")
                rebuilt._add_with_id(memory["id"], memory["text"], embedding,
                                     memory.get("metadata") or {}, memory["namespace"],
                                     memory["created_at"], memory["updated_at"],
                                     memory.get("summary"))
                recovered += 1
                if progress:
                    progress(recovered, len(source_records))
        rebuilt.rebuild_keyword_index()
        rebuilt.rebuild_category_index()
        if embedding_spec:
            batch_embed = embed_documents or (lambda values: [embed_fn(value) for value in values])
            rebuilt.reindex_index_values(embedding_spec["profile_id"], batch_embed,
                                         batch_size=batch_size)
        if rebuilt.conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("rebuilt database failed integrity_check")
        if not rebuilt.index_embedding_completeness()["complete"]:
            raise RuntimeError("rebuilt database has incomplete index embeddings")
        rebuilt.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return recovered
    finally:
        rebuilt.close()


def install_recovery_database(temp_path, target_path, primary):
    """SQLite backup commits a complete replacement under the existing writer lock."""
    marker = Path(f"{target_path}.online-recover")
    with open(marker, "w", encoding="utf-8") as handle:
        handle.write("replica sync required\n")
        handle.flush()
        os.fsync(handle.fileno())
    directory_fd = os.open(str(marker.parent), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    primary.close_connection()
    try:
        with closing(sqlite3.connect(temp_path)) as source, \
                closing(sqlite3.connect(target_path)) as target:
            source.backup(target)
    finally:
        primary.reopen_connection()
    return marker
