"""Dual storage: SQLite primary + secondary replica (outbox pattern)."""

import threading
import time
import logging
import os
import shutil
from datetime import datetime, timezone

logger = logging.getLogger("storage_dual")

from .storage import MemoryStorage, ReplicaStorage, DEFAULT_NAMESPACE
from . import audit


_COMPACT_STATE_KEY = "ndjson_compact"
_COMPACT_RETRY_SECONDS = 60 * 60


class DualStorage(MemoryStorage):
    """Dual-write via Outbox pattern.

    SQLite is the authoritative primary. All writes go to SQLite with
    synced=0. A background thread periodically polls for unsynced records
    (synced=0) and batch-syncs them to the NDJSON replica. On success,
    records are marked synced=1.

    This eliminates the need for periodic full rebuilds — the outbox
    pattern guarantees every record is synced at-least-once.

    Configuration
    -------------
    sync_interval : int
        Seconds between sync cycles (default 300 = 5 min).
    """

    def __init__(self, primary: MemoryStorage, secondary: ReplicaStorage,
                 sync_interval: int = 300, compact_interval: int = 24 * 60 * 60,
                 compact_min_bytes: int = 1024 * 1024,
                 compact_min_ratio: float = 0.25, start_background: bool = True):
        self.primary = primary
        self.secondary = secondary
        self.sync_interval = sync_interval
        self.compact_interval = max(1, compact_interval)
        self.compact_min_bytes = max(0, compact_min_bytes)
        self.compact_min_ratio = min(1.0, max(0.0, compact_min_ratio))

        # Sync state
        self._last_sync_mono = time.monotonic()              # timing for stats
        self._sync_count = 0
        self._lock = threading.RLock()
        self._sync_mutex = threading.Lock()
        acquire = getattr(type(secondary), "acquire_writer_lock", None)
        if callable(acquire):
            secondary.acquire_writer_lock()

        # Background sync thread
        self._stop = threading.Event()
        self._job = None
        if start_background:
            self._job = threading.Thread(target=self._sync_loop, daemon=True)
            self._job.start()
        logger.info("DualStorage started: sync_interval=%ds replica=%s",
                    sync_interval, getattr(secondary, 'path', '?'))

    # -- background sync job --

    def _sync_loop(self):
        self._run_due_compact("startup")
        while not self._stop.wait(self.sync_interval):
            try:
                self._sync_count += 1
                with self._sync_mutex:
                    self._incremental_sync()
            except Exception:
                logger.error("background sync failed", exc_info=True)
            self._run_due_compact("scheduled")

    @staticmethod
    def _iso_timestamp(value):
        if not value:
            return None
        return datetime.fromtimestamp(value, timezone.utc).isoformat()

    def _compact_state(self) -> dict:
        state = self.primary._get_storage_state(_COMPACT_STATE_KEY, {})
        return state if isinstance(state, dict) else {}

    def _save_compact_state(self, state: dict) -> None:
        self.primary._set_storage_state(_COMPACT_STATE_KEY, state)

    def _run_due_compact(self, trigger: str) -> None:
        """Run one due NDJSON maintenance check without blocking sync writes."""
        now = time.time()
        state = self._compact_state()
        last_check = state.get("last_check_at")
        retry_after = (_COMPACT_RETRY_SECONDS
                       if state.get("last_result") == "error"
                       else self.compact_interval)
        if last_check and now < last_check + retry_after:
            return

        started = time.perf_counter()
        try:
            with self._sync_mutex:
                # Re-read after acquiring the mutex: another worker may have
                # completed a check while this thread was waiting.
                state = self._compact_state()
                last_check = state.get("last_check_at")
                retry_after = (_COMPACT_RETRY_SECONDS
                               if state.get("last_result") == "error"
                               else self.compact_interval)
                if last_check and time.time() < last_check + retry_after:
                    return
                metrics = self.secondary.compaction_metrics()
                should_compact = (
                    metrics["reclaimable_bytes"] >= self.compact_min_bytes
                    or metrics["reclaimable_ratio"] >= self.compact_min_ratio
                )
                checked_at = time.time()
                if should_compact:
                    outcome = self.secondary.compact()
                    state.update({
                        "last_compact_at": checked_at,
                        "last_result": "compacted",
                        "last_error": None,
                        "last_before_bytes": outcome["file_bytes"],
                        "last_after_bytes": outcome["after_bytes"],
                        "last_reclaimed_bytes": outcome["reclaimed_bytes"],
                        "last_reclaimable_ratio": metrics["reclaimable_ratio"],
                    })
                    result = {"outcome": "compacted", **outcome}
                    logger.info("NDJSON compact: reclaimed=%d before=%d after=%d",
                                outcome["reclaimed_bytes"], outcome["file_bytes"],
                                outcome["after_bytes"])
                else:
                    state.update({
                        "last_result": "skipped",
                        "last_error": None,
                        "last_before_bytes": metrics["file_bytes"],
                        "last_after_bytes": metrics["file_bytes"],
                        "last_reclaimed_bytes": metrics["reclaimable_bytes"],
                        "last_reclaimable_ratio": metrics["reclaimable_ratio"],
                    })
                    result = {"outcome": "skipped", **metrics}
                    logger.info("NDJSON compact: skipped reclaimable=%d ratio=%.3f",
                                metrics["reclaimable_bytes"], metrics["reclaimable_ratio"])
                state["last_check_at"] = checked_at
                state["last_trigger"] = trigger
                self._save_compact_state(state)
            audit.record(
                "ndjson_compact", {"trigger": trigger}, result,
                (time.perf_counter() - started) * 1000,
            )
        except Exception as exc:
            state.update({
                "last_check_at": time.time(),
                "last_result": "error",
                "last_error": str(exc),
                "last_trigger": trigger,
            })
            try:
                self._save_compact_state(state)
            except Exception:
                logger.error("NDJSON compact: could not persist failure state", exc_info=True)
            logger.error("NDJSON compact failed", exc_info=True)
            audit.record(
                "ndjson_compact", {"trigger": trigger}, None,
                (time.perf_counter() - started) * 1000,
                error={"type": type(exc).__name__, "message": str(exc)},
            )

    def _record_full_sync_compaction(self, before_bytes: int, after_bytes: int) -> None:
        """Treat a successful Full Sync as a successful compact in Stats."""
        now = time.time()
        self._save_compact_state({
            "last_check_at": now,
            "last_compact_at": now,
            "last_result": "full_sync",
            "last_error": None,
            "last_trigger": "full_sync",
            "last_before_bytes": before_bytes,
            "last_after_bytes": after_bytes,
            "last_reclaimed_bytes": max(0, before_bytes - after_bytes),
            "last_reclaimable_ratio": ((before_bytes - after_bytes) / before_bytes
                                        if before_bytes else 0.0),
        })

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
                        self.secondary.append_tombstone(m["id"], m.get("updated_at"))
                    else:
                        self.secondary.upsert(m)
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

    def add(self, text, embedding, metadata=None, namespace=DEFAULT_NAMESPACE, summary=None,
            index_embeddings=None):
        with self._lock:
            return self.primary.add(text, embedding, metadata, namespace, summary,
                                    index_embeddings=index_embeddings)

    def update(self, memory_id, text=None, embedding=None, metadata=None, namespace=None, summary=None,
               index_embeddings=None):
        with self._lock:
            return self.primary.update(memory_id, text, embedding, metadata, namespace, summary,
                                       index_embeddings=index_embeddings)

    def missing_index_value_embeddings(self, *args, **kwargs):
        return self.primary.missing_index_value_embeddings(*args, **kwargs)

    def exact_index_values(self, *args, **kwargs):
        return self.primary.exact_index_values(*args, **kwargs)

    def semantic_index_values(self, *args, **kwargs):
        return self.primary.semantic_index_values(*args, **kwargs)

    def index_embedding_completeness(self, *args, **kwargs):
        return self.primary.index_embedding_completeness(*args, **kwargs)

    def backfill_missing_index_value_embeddings(self, *args, **kwargs):
        return self.primary.backfill_missing_index_value_embeddings(*args, **kwargs)

    def update_metadata(self, memory_id, metadata, index_embeddings=None):
        with self._lock:
            return self.primary.update_metadata(
                memory_id, metadata, index_embeddings=index_embeddings
            )

    def delete(self, memory_id):
        with self._lock:
            return self.primary.delete(memory_id)

    def clear(self, namespace=None):
        with self._lock:
            return self.primary.clear(namespace)

    def export_records(self, namespace=None):
        with self._lock:
            return self.primary.export_records(namespace)

    def import_records(self, records, memory_embeddings, index_embeddings, expected_profile):
        with self._lock:
            return self.primary.import_records(
                records, memory_embeddings, index_embeddings, expected_profile
            )

    def rebuild_keyword_index(self):
        """Rebuild the keyword index on the primary (SQLite only)."""
        with self._lock:
            return self.primary.rebuild_keyword_index()

    def get_keyword_vocabulary(self, namespace: str | None = None) -> list[str]:
        """Keyword vocabulary from the primary (SQLite index)."""
        with self._lock:
            return self.primary.get_keyword_vocabulary(namespace=namespace)

    def rebuild_category_index(self):
        """Rebuild the category index on the primary (SQLite only)."""
        with self._lock:
            return self.primary.rebuild_category_index()

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
        Uses quick_sync (one SQLite snapshot + atomic replacement) and marks synced
        only for the records that were actually copied.
        """
        with self._lock, self._sync_mutex:
            from .recovery import quick_sync
            before_bytes = self.secondary.get_stats().get("file_bytes", 0)
            result = quick_sync(self)
            after_bytes = self.secondary.get_stats().get("file_bytes", 0)
            self._record_full_sync_compaction(before_bytes, after_bytes)
            if result.get("ids"):
                self.primary._mark_synced_batch(result["ids"])
            self._last_sync_mono = time.monotonic()
            return result

    def export_full_snapshot(self, destination: str) -> dict:
        """Full-sync, verify, then copy an immutable download snapshot."""
        with self._lock, self._sync_mutex:
            from .recovery import quick_sync
            from .jsonl_transfer import load_jsonl, sha256_file
            before_bytes = self.secondary.get_stats().get("file_bytes", 0)
            result = quick_sync(self)
            primary_records = self.primary.export_records()
            replica_records = load_jsonl(self.secondary.path)["records"]
            primary_by_id = {item["id"]: item for item in primary_records}
            replica_by_id = {item["id"]: item for item in replica_records}
            if primary_by_id != replica_by_id:
                missing = sorted(set(primary_by_id) - set(replica_by_id))
                extra = sorted(set(replica_by_id) - set(primary_by_id))
                mismatched = sum(
                    primary_by_id[memory_id] != replica_by_id[memory_id]
                    for memory_id in set(primary_by_id) & set(replica_by_id)
                )
                raise RuntimeError(
                    "replica integrity check failed: "
                    f"missing={len(missing)}, extra={len(extra)}, mismatched={mismatched}"
                )
            shutil.copyfile(self.secondary.path, destination)
            after_bytes = os.path.getsize(self.secondary.path)
            self._record_full_sync_compaction(before_bytes, after_bytes)
            self.primary._mark_synced_batch(result.get("ids", []))
            self._last_sync_mono = time.monotonic()
            return {"records": len(primary_records), "bytes": os.path.getsize(destination),
                    "sha256": sha256_file(destination), "path": destination,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "source_path": self.secondary.path,
                    "integrity": {"missing": 0, "extra": 0, "mismatched": 0,
                                  "corrupt_lines": 0}}

    def get_stats(self) -> dict:
        p = self.primary.get_stats()
        try:
            s = self.secondary.get_stats()
        except Exception:
            s = {"total": -1, "backend": "json", "path": "?", "error": "unavailable"}
        pending = self.primary._count_unsynced()
        compact = self._compact_state()
        if compact:
            last_check = compact.get("last_check_at")
            retry_after = (_COMPACT_RETRY_SECONDS
                           if compact.get("last_result") == "error"
                           else self.compact_interval)
            compact = {
                **compact,
                "last_check_at": self._iso_timestamp(last_check),
                "last_compact_at": self._iso_timestamp(compact.get("last_compact_at")),
                "next_due_at": self._iso_timestamp(last_check + retry_after) if last_check else None,
                "interval_s": self.compact_interval,
                "min_reclaimable_bytes": self.compact_min_bytes,
                "min_reclaimable_ratio": self.compact_min_ratio,
            }
        primary_ids = {item["id"] for item in self.primary.export_records()}
        inspected_ids = s.pop("_integrity_ids", None)
        secondary_ids = (set(inspected_ids) if inspected_ids is not None else
                         {item["id"] for item in self.secondary.list_records(limit=10 ** 9)})
        secondary_integrity = s.get("integrity", {})
        corrupt_lines = secondary_integrity.get("corrupt_lines", 0)
        missing = primary_ids - secondary_ids
        extra = secondary_ids - primary_ids
        return {
            "total": p["total"],
            "backend": "dual",
            "in_sync": pending == 0 and not missing and not extra and corrupt_lines == 0,
            "pending": pending,
            "replica_integrity": {
                "missing": len(missing), "extra": len(extra),
                "corrupt_lines": corrupt_lines,
                "checked_at": secondary_integrity.get("checked_at"),
                "checked": True,
            },
            "sync_count": self._sync_count,
            "sync_interval_s": self.sync_interval,
            "namespaces": p.get("namespaces", []),
            "namespace_counts": p.get("namespace_counts", {}),
            "keyword_counts": p.get("keyword_counts", {}),
            "category_counts": p.get("category_counts", {}),
            "embedding_storage": p.get("embedding_storage", {}),
            "embedding_profile": p.get("embedding_profile", {}),
            "primary": p,
            "secondary": s,
            "ndjson_compact": compact,
        }

    def stop(self):
        """Gracefully stop the background sync thread."""
        self._stop.set()
        if self._job is not None and self._job.is_alive():
            self._job.join(timeout=max(1, min(self.sync_interval + 1, 10)))
        try:
            release = getattr(type(self.secondary), "release_writer_lock", None)
            if callable(release) and (self._job is None or not self._job.is_alive()):
                self.secondary.release_writer_lock()
        finally:
            stop_primary = getattr(self.primary, "stop", None)
            if callable(stop_primary):
                stop_primary()
