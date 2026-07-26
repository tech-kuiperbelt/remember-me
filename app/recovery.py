"""Recovery and sync utilities for DualStorage.

This module is separate from storage.py to avoid ABC pollution.
All methods operate on MemoryStorage instances via public/internal API.

Functions
---------
quick_sync(storage)
    Full rebuild of secondary from primary (paginated).
recover(target, source, batch_size=500)
    Clear target and copy all memories from source.
"""

import logging

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
    dict with keys: synced, errors
    """
    primary = storage.primary
    secondary = storage.secondary

    # Clear secondary
    secondary.clear()

    # Paginated copy from primary
    synced = 0
    errors = 0
    offset = 0

    while True:
        batch = primary.list_memories(limit=batch_size, offset=offset)
        if not batch:
            break
        for m in batch:
            try:
                secondary._add_with_id(
                    m["id"], m["text"], m["embedding"],
                    m["metadata"], m["namespace"],
                    m["created_at"], m["updated_at"],
                )
                synced += 1
            except Exception:
                logger.warning("quick_sync: failed to copy id=%s", m["id"], exc_info=True)
                errors += 1
        offset += batch_size

    logger.info("quick_sync: synced=%d errors=%d", synced, errors)
    return {"synced": synced, "errors": errors}


def recover(target, source, batch_size=500):
    """Recover/rebuild target storage from source storage.

    Clears target completely, then copies all memories from source.
    Preserves memory IDs via _add_with_id().
    Use this for disaster recovery or mirroring between storage files.

    Parameters
    ----------
    target : MemoryStorage
        The storage to rebuild (e.g. SQLiteStorage recovering from JSON).
    source : MemoryStorage
        The authoritative storage to read from (e.g. JSONStorage backup).
    batch_size : int
        Number of records per page.

    Returns
    -------
    dict with keys: recovered, errors
    """
    target.clear()
    recovered = 0
    errors = 0
    offset = 0

    while True:
        batch = source.list_memories(limit=batch_size, offset=offset)
        if not batch:
            break
        for m in batch:
            try:
                target._add_with_id(
                    m["id"], m["text"], m["embedding"],
                    m["metadata"], m["namespace"],
                    m["created_at"], m["updated_at"],
                )
                recovered += 1
            except Exception:
                logger.warning("recover: failed to copy id=%s", m["id"], exc_info=True)
                errors += 1
        offset += batch_size

    logger.info("recover: recovered=%d errors=%d", recovered, errors)
    return {"recovered": recovered, "errors": errors}
