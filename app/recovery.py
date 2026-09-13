"""Recovery and sync utilities for DualStorage.

This module is separate from storage.py to avoid ABC pollution.
All methods operate on MemoryStorage instances via public/internal API.

Functions
---------
quick_sync(storage)
    Full rebuild of secondary from primary (paginated).
recover_ndjson_to_sqlite(source_path, target_path, embed_fn, batch_size=500)
    Offline, atomic recovery from a vector-free NDJSON replica.
"""

import logging
import os
from pathlib import Path

from .storage import create_storage

logger = logging.getLogger("recovery")


def quick_sync(storage, batch_size=500):
    """Full rebuild: clear secondary (JSON) and copy all from primary (SQLite).

    Preserves all memory IDs by using _add_with_id() on secondary.
    Filters out soft-deleted records from primary.
    Uses pagination to avoid OOM on large datasets.

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

    # Read the complete primary snapshot before replacing the replica. NDJSON
    # can atomically publish it in one compact write, avoiding a partial backup.
    memories = []
    offset = 0
    while True:
        batch = primary.list_memories(limit=batch_size, offset=offset)
        if not batch:
            break
        memories.extend(batch)
        offset += batch_size

    if hasattr(secondary, "replace_all"):
        secondary.replace_all(memories)
    else:
        secondary.clear()
        for m in memories:
            secondary._add_with_id(
                m["id"], m["text"], m["embedding"], m["metadata"],
                m["namespace"], m["created_at"], m["updated_at"],
                m.get("summary"),
            )

    logger.info("quick_sync: synced=%d", len(memories))
    return {"synced": len(memories), "errors": 0,
            "ids": [m["id"] for m in memories]}


def recover_ndjson_to_sqlite(source_path: str, target_path: str, embed_fn,
                             batch_size: int = 500, embedding_spec: dict | None = None,
                             embedding_dtype: str = "float16") -> dict:
    """Offline, atomic rebuild of SQLite from a vector-free NDJSON backup.

    A temporary SQLite file is built entirely before it replaces ``target_path``.
    Any embedding failure leaves the prior primary untouched.
    """
    source = create_storage("ndjson", path=source_path)
    target = Path(target_path)
    temp = Path(f"{target}.recover.tmp")
    temp_sidecars = [Path(f"{temp}-wal"), Path(f"{temp}-shm")]
    target_sidecars = [Path(f"{target}-wal"), Path(f"{target}-shm")]
    rebuilt = None
    recovered = 0
    try:
        for path in [temp, *temp_sidecars]:
            if path.exists():
                path.unlink()
        rebuilt = create_storage(
            "sqlite", path=str(temp), embedding_spec=embedding_spec,
            embedding_dtype=embedding_dtype,
        )
        offset = 0
        while True:
            batch = source.list_memories(limit=batch_size, offset=offset)
            if not batch:
                break
            for memory in batch:
                embedding = embed_fn(memory["text"])
                if not embedding:
                    raise RuntimeError(f"empty embedding for memory {memory['id']}")
                rebuilt._add_with_id(
                    memory["id"], memory["text"], embedding,
                    memory.get("metadata") or {}, memory["namespace"],
                    memory["created_at"], memory["updated_at"],
                    memory.get("summary"),
                )
                recovered += 1
            offset += batch_size
        rebuilt.rebuild_keyword_index()
        rebuilt.rebuild_category_index()
        rebuilt.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        rebuilt.conn.close()
        rebuilt = None
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
        if rebuilt is not None:
            rebuilt.conn.close()
        for path in [temp, *temp_sidecars]:
            if path.exists():
                path.unlink()
