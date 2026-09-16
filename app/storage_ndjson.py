"""NDJSON / JSON Lines disaster-recovery replica — append-only, crash-safe."""

import json
import os
import threading
import logging
import fcntl
from datetime import datetime, timezone

logger = logging.getLogger("storage_ndjson")

from .storage import ReplicaStorage, _now


class NDJSONReplica(ReplicaStorage):
    """Line-delimited JSON disaster-recovery replica (.jsonl/.ndjson).

    Each record is one JSON object per line and deliberately omits embeddings.
    The SQLite primary is authoritative; disaster recovery recreates embeddings
    from each record's text using the currently configured model. Append-only writes:
      upsert           → append full record line
      append_tombstone → append tombstone line

    Compact via force_sync (_compact_file): rewrite file from _data dict,
    removing tombstones and old versions.

    Incremental batches are flushed and fsynced before SQLite acknowledges
    them. Snapshot replacements fsync both the temporary file and directory.
    Read tolerates corrupt lines (skipped with warning).
    """

    def __init__(self, path: str = "data/memories.jsonl"):
        self.path = path
        self._lock = threading.RLock()
        self._data: dict[str, dict] = {}
        self._tombstones = 0
        self._writer_lock_file = None
        self._load()

    def acquire_writer_lock(self):
        """Hold a lifetime single-writer lock for one DualStorage instance."""
        if self._writer_lock_file is not None:
            return
        lock_path = self.path + ".writer.lock"
        os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
        handle = open(lock_path, "a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise RuntimeError(
                f"NDJSON replica already has an active writer: {self.path}; "
                "connect clients to the single HTTP service instead of starting another dual instance"
            ) from exc
        self._writer_lock_file = handle

    def release_writer_lock(self):
        handle = self._writer_lock_file
        self._writer_lock_file = None
        if handle is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    # -- I/O internals --

    def _load(self):
        if not os.path.exists(self.path):
            self._data = {}
            self._tombstones = 0
            return
        if not os.path.getsize(self.path):
            self._data = {}
            self._tombstones = 0
            return
        self._scan_lines()

    def _scan_lines(self):
        records: dict[str, dict] = {}
        tombstones = 0
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                    if not isinstance(m, dict):
                        raise ValueError("record is not an object")
                    rid = m.get("id")
                    if not isinstance(rid, str) or not rid:
                        raise ValueError("record has no id")
                    if m.get("deleted") is not True and not isinstance(
                            m.get("updated_at"), str):
                        raise ValueError("live record has no updated_at")
                except (json.JSONDecodeError, ValueError):
                    logger.warning("NDJSONReplica: corrupt line in %s, skipped", self.path)
                    continue
                if m.get("deleted"):
                    records.pop(rid, None)
                    tombstones += 1
                else:
                    # Older replicas may carry SQLite-only fields. Ignore them
                    # so the next compact/full sync rewrites the portable form.
                    m = self._without_embedding(m)
                    existing = records.get(rid)
                    if not existing or m["updated_at"] >= existing["updated_at"]:
                        records[rid] = m
        self._data = records
        self._tombstones = tombstones

    @staticmethod
    def _fsync_directory(path: str) -> None:
        """Persist a preceding rename in the containing local directory."""
        directory = os.path.dirname(os.path.abspath(path)) or "."
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        fd = os.open(directory, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def _without_embedding(record: dict) -> dict:
        """Return a replica event without SQLite-only bookkeeping fields."""
        return {
            key: value for key, value in record.items()
            if key not in {"embedding", "synced", "version"}
            and not (key == "deleted" and value is not True)
        }

    def _write_snapshot(self, records) -> None:
        """Atomically publish a durable snapshot. Caller must hold _lock."""
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(self._without_embedding(record), ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
            self._fsync_directory(self.path)
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                logger.warning("NDJSONReplica: could not remove failed temp file %s", tmp)
            raise

    def _compact_file(self):
        """Rewrite file from _data dict, atomically, removing tombstones/dupes."""
        self._write_snapshot(self._data.values())
        self._tombstones = 0

    def compaction_metrics(self) -> dict:
        """Measure recoverable NDJSON redundancy without changing the file."""
        with self._lock:
            try:
                file_bytes = os.path.getsize(self.path)
            except OSError:
                file_bytes = 0
            compacted_bytes = sum(
                len((json.dumps(self._without_embedding(m), ensure_ascii=False) + "\n").encode("utf-8"))
                for m in self._data.values()
            )
            reclaimable_bytes = max(0, file_bytes - compacted_bytes)
            return {
                "file_bytes": file_bytes,
                "compacted_bytes": compacted_bytes,
                "reclaimable_bytes": reclaimable_bytes,
                "reclaimable_ratio": reclaimable_bytes / file_bytes if file_bytes else 0.0,
                "active_records": len(self._data),
                "tombstones": self._tombstones,
            }

    def compact(self) -> dict:
        """Atomically remove stale NDJSON lines and return before/after metrics."""
        with self._lock:
            before = self.compaction_metrics()
            self._compact_file()
            after = self.compaction_metrics()
        return {
            **before,
            "after_bytes": after["file_bytes"],
            "reclaimed_bytes": before["file_bytes"] - after["file_bytes"],
        }

    # -- replica API --

    def append_batch(self, records: list[dict]) -> None:
        """Append and durably publish one outbox batch as an all-or-retry unit."""
        if not records:
            return
        with self._lock:
            entries = [self._without_embedding(record) for record in records]
            with open(self.path, "a", encoding="utf-8") as handle:
                for entry in entries:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            for entry in entries:
                if entry.get("deleted") is True:
                    self._data.pop(entry["id"], None)
                    self._tombstones += 1
                else:
                    self._data[entry["id"]] = entry

    def upsert(self, record: dict) -> None:
        self.append_batch([record])

    def append_tombstone(self, memory_id: str, updated_at: str) -> None:
        self.append_batch([{
            "id": memory_id, "deleted": True,
            "updated_at": updated_at or _now(),
        }])

    def list_records(self, limit=100, offset=0):
        with self._lock:
            entries = sorted(
                self._data.values(), key=lambda item: (item.get("created_at", ""), item["id"])
            )
            return [dict(item) for item in entries[offset:offset + limit]]

    def replace_all(self, memories: list[dict]):
        """Atomically replace this replica with active primary records.

        Used by manual full Sync. Build the complete in-memory snapshot first,
        then compact once so an interrupted rebuild never leaves a partial file.
        """
        with self._lock:
            replacement = {
                m["id"]: {
                    "id": m["id"], "text": m["text"],
                    "metadata": m.get("metadata") or {},
                    "namespace": m["namespace"],
                    "created_at": m["created_at"], "updated_at": m["updated_at"],
                    "summary": m.get("summary"),
                }
                for m in memories
            }
            self._write_snapshot(replacement.values())
            self._data = replacement
            self._tombstones = 0

    def get_stats(self):
        from .jsonl_transfer import inspect_jsonl
        try:
            integrity = inspect_jsonl(self.path)
        except FileNotFoundError:
            integrity = {"records": [], "corrupt_lines": 0, "errors": []}
        except (OSError, UnicodeError) as exc:
            integrity = {"records": [], "corrupt_lines": 1, "errors": [str(exc)]}
        try:
            fsize = os.path.getsize(self.path)
        except OSError:
            fsize = 0
        namespaces = set(e["namespace"] for e in self._data.values())
        ns_counts = {}
        for ns in namespaces:
            ns_counts[ns] = sum(1 for e in self._data.values() if e["namespace"] == ns)
        return {
            "total": len(self._data),
            "file_bytes": fsize,
            "tombstones": self._tombstones,
            "namespaces": list(namespaces),
            "namespace_counts": ns_counts,
            "integrity": {
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "records": len(integrity["records"]),
                "corrupt_lines": integrity["corrupt_lines"],
                "errors": integrity["errors"],
            },
            "_integrity_ids": [record["id"] for record in integrity["records"]],
            "backend": "ndjson",
            "path": self.path,
        }
