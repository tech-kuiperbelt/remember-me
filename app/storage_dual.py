"""Dual storage: SQLite primary + secondary replica (outbox pattern)."""

import threading
import time
import logging

logger = logging.getLogger("storage_dual")

from .storage import MemoryStorage, DEFAULT_NAMESPACE


class DualStorage(MemoryStorage):
    """Dual-write via Outbox pattern.

    SQLite is the authoritative primary. All writes go to SQLite with
    synced=0. A background thread periodically polls for unsynced records
    (synced=0) and batch-syncs them to the JSON secondary. On success,
    records are marked synced=1.

    This eliminates the need for periodic full rebuilds — the outbox
    pattern guarantees every record is synced at-least-once.

    Configuration
    -------------
    sync_interval : int
        Seconds between sync cycles (default 300 = 5 min).
    """

    def __init__(self, primary: MemoryStorage, secondary: MemoryStorage,
                 sync_interval: int = 300):
        self.primary = primary
        self.secondary = secondary
        self.sync_interval = sync_interval

        # Sync state
        self._last_sync_mono = time.monotonic()              # timing for stats
        self._sync_count = 0
        self._lock = threading.RLock()
        self._sync_mutex = threading.Lock()

        # Background sync thread
        self._stop = threading.Event()
        self._job = threading.Thread(target=self._sync_loop, daemon=True)
        self._job.start()
        logger.info("DualStorage started: sync_interval=%ds secondary=%s",
                     sync_interval, getattr(secondary, 'path', '?'))

    # -- background sync job --

    def _sync_loop(self):
        while not self._stop.wait(self.sync_interval):
            try:
                self._sync_count += 1
                with self._sync_mutex:
                    self._incremental_sync()
            except Exception:
                logger.error("background sync failed", exc_info=True)

    def _incremental_sync(self):
        """Batch-sync unsynced records to secondary, mark as synced."""
        synced = 0
        while True:
            batch = self.primary._get_unsynced(limit=500)
            if not batch:
                break
            ids = []
            for m in batch:
                try:
                    if m.get("deleted"):
                        self.secondary.delete(m["id"])
                    else:
                        existing = self.secondary.get(m["id"])
                        if existing:
                            self.secondary.update(
                                m["id"], text=m["text"], embedding=m["embedding"],
                                metadata=m["metadata"], namespace=m["namespace"],
                                summary=m.get("summary"))
                        else:
                            self.secondary._add_with_id(
                                m["id"], m["text"], m["embedding"],
                                m["metadata"], m["namespace"],
                                m["created_at"], m["updated_at"],
                                m.get("summary"))
                    ids.append(m["id"])
                    synced += 1
                except Exception:
                    logger.warning("incremental sync: failed to sync id=%s", m["id"], exc_info=True)
            if ids:
                self.primary._mark_synced_batch(ids)
        if synced:
            self._last_sync_mono = time.monotonic()
            logger.info("incremental sync: %d synced", synced)

    # -- public API: writes → primary only (synced=0 set by SQLiteStorage) --

    def add(self, text, embedding, metadata=None, namespace=DEFAULT_NAMESPACE, summary=None):
        with self._lock:
            return self.primary.add(text, embedding, metadata, namespace, summary)

    def update(self, memory_id, text=None, embedding=None, metadata=None, namespace=None, summary=None):
        with self._lock:
            return self.primary.update(memory_id, text, embedding, metadata, namespace, summary)

    def update_summary(self, memory_id, summary):
        with self._lock:
            return self.primary.update_summary(memory_id, summary)

    def update_metadata(self, memory_id, metadata):
        with self._lock:
            return self.primary.update_metadata(memory_id, metadata)

    def get_pending_summaries(self, *args, **kwargs):
        return self.primary.get_pending_summaries(*args, **kwargs)

    def delete(self, memory_id):
        with self._lock:
            return self.primary.delete(memory_id)

    def clear(self, namespace=None):
        with self._lock:
            return self.primary.clear(namespace)

    def merge(self, source_storage, namespace_prefix=""):
        with self._lock:
            return self.primary.merge(source_storage, namespace_prefix)

    def rebuild_keyword_index(self):
        """Rebuild the keyword index on the primary (SQLite only)."""
        with self._lock:
            return self.primary.rebuild_keyword_index()

    def get_keyword_vocabulary(self, namespace: str | None = None) -> list[str]:
        """Keyword vocabulary from the primary (SQLite index)."""
        with self._lock:
            return self.primary.get_keyword_vocabulary(namespace=namespace)

    # -- public API: reads → primary only --

    def search(self, *args, **kwargs):
        return self.primary.search(*args, **kwargs)

    def list_memories(self, *args, **kwargs):
        return self.primary.list_memories(*args, **kwargs)

    def get(self, *args, **kwargs):
        return self.primary.get(*args, **kwargs)

    def get_details(self, *args, **kwargs):
        return self.primary.get_details(*args, **kwargs)

    # -- operations --

    def force_sync(self) -> dict:
        """Manually trigger a full rebuild of secondary from primary.
        Uses quick_sync (clear + paginated rebuild) and marks synced
        only for the records that were actually copied.
        """
        with self._sync_mutex:
            from .recovery import quick_sync
            result = quick_sync(self)
            if result.get("ids"):
                self.primary._mark_synced_batch(result["ids"])
            self._last_sync_mono = time.monotonic()
            return result

    def get_stats(self) -> dict:
        p = self.primary.get_stats()
        try:
            s = self.secondary.get_stats()
        except Exception:
            s = {"total": -1, "backend": "json", "path": "?", "error": "unavailable"}
        pending = self.primary._count_unsynced()
        return {
            "total": p["total"],
            "backend": "dual",
            "in_sync": pending == 0 and s.get("total", -1) >= 0,
            "pending": pending,
            "sync_count": self._sync_count,
            "sync_interval_s": self.sync_interval,
            "namespaces": p.get("namespaces", []),
            "namespace_counts": p.get("namespace_counts", {}),
            "keyword_counts": p.get("keyword_counts", {}),
            "primary": p,
            "secondary": s,
        }

    def stop(self):
        """Gracefully stop the background sync thread."""
        self._stop.set()
