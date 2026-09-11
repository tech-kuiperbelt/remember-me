"""NDJSON / JSON Lines disaster-recovery replica — append-only, crash-safe."""

import json
import os
import threading
import logging

logger = logging.getLogger("storage_ndjson")

from .storage import MemoryStorage, _now, _new_id, DEFAULT_NAMESPACE


class NDJSONStorage(MemoryStorage):
    """Line-delimited JSON storage (.jsonl).

    Each record is one JSON object per line and deliberately omits embeddings.
    The SQLite primary is authoritative; disaster recovery recreates embeddings
    from each record's text using the currently configured model. Append-only writes:
      add/update     → append full record line
      delete         → append tombstone line  {"id":"x","deleted":true,"updated_at":"..."}

    Compact via force_sync (_compact_file): rewrite file from _data dict,
    removing tombstones and old versions.

    Crash-safe writes use temp file + os.replace().
    Read tolerates corrupt lines (skipped with warning).
    """

    def __init__(self, path: str = "data/memories.jsonl"):
        self.path = path
        self._lock = threading.RLock()
        self._data: dict[str, dict] = {}
        self._tombstones = 0
        self._load()

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
        with open(self.path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("NDJSONStorage: corrupt line in %s, skipped", self.path)
                    continue
                rid = m["id"]
                if m.get("deleted"):
                    records.pop(rid, None)
                    tombstones += 1
                else:
                    # Legacy replicas may contain embeddings. They are ignored
                    # so a future compact/full-sync rewrites the smaller format.
                    m.pop("embedding", None)
                    existing = records.get(rid)
                    if not existing or m["updated_at"] >= existing["updated_at"]:
                        records[rid] = m
        self._data = records
        self._tombstones = tombstones

    def _append_line(self, record: dict):
        """Append one JSON line to the file. Caller must hold _lock."""
        line = json.dumps(self._without_embedding(record), ensure_ascii=False) + "\n"
        with open(self.path, "a") as f:
            f.write(line)

    @staticmethod
    def _without_embedding(record: dict) -> dict:
        """Return a serializable replica record without derived vectors."""
        return {key: value for key, value in record.items() if key != "embedding"}

    def _compact_file(self):
        """Rewrite file from _data dict, atomically, removing tombstones/dupes."""
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w") as f:
                for m in self._data.values():
                    f.write(json.dumps(self._without_embedding(m), ensure_ascii=False) + "\n")
            os.replace(tmp, self.path)
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                logger.warning("NDJSONStorage: could not remove failed temp file %s", tmp)
            raise
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

    # -- public API --

    def add(self, text, embedding, metadata=None, namespace=DEFAULT_NAMESPACE, summary=None):
        """Insert a memory; dedup on (namespace, text). Returns {"id", "created"}."""
        with self._lock:
            for e in self._data.values():
                if e["namespace"] == namespace and e["text"] == text:
                    return {"id": e["id"], "created": False}
            eid = _new_id()
            now = _now()
            entry = {
                "id": eid, "text": text,
                "metadata": metadata or {}, "namespace": namespace,
                "created_at": now, "updated_at": now,
                "summary": summary,
            }
            self._data[eid] = entry
            self._append_line(entry)
        return {"id": eid, "created": True}

    def search(self, query_embedding, k=5, threshold=0.0, namespace=None,
               categories=None, keywords=None):
        raise RuntimeError(
            "NDJSON is a disaster-recovery replica and does not retain embeddings; "
            "semantic search is available only from the SQLite primary"
        )

    def list_memories(self, namespace=None, limit=100, offset=0, sort_by="created_at",
                      updated_after=None, updated_before=None, keywords=None,
                      categories=None):
        # keywords/categories filtering is SQLite-only (rebuildable indexes);
        # JSONL replicas accept the parameters for interface compatibility and
        # ignore them.
        sort_key = sort_by if sort_by in ("created_at", "updated_at") else "created_at"
        entries = list(self._data.values())
        if namespace:
            entries = [e for e in entries if e["namespace"] == namespace]
        if updated_after:
            entries = [e for e in entries if e["updated_at"] > updated_after]
        if updated_before:
            entries = [e for e in entries if e["updated_at"] < updated_before]
        entries.sort(key=lambda e: e[sort_key], reverse=True)
        return entries[offset:offset + limit]

    def get(self, memory_id):
        return self._data.get(memory_id)

    def update(self, memory_id, text=None, embedding=None, metadata=None, namespace=None, summary=None):
        with self._lock:
            e = self._data.get(memory_id)
            if not e:
                return False
            if text is not None:
                e["text"] = text
            if metadata is not None:
                e["metadata"] = {**e["metadata"], **metadata}
            if namespace is not None:
                e["namespace"] = namespace
            if summary is not None:
                e["summary"] = summary
            e["updated_at"] = _now()
            self._append_line(e)
        return True

    def update_summary(self, memory_id, summary):
        """Update only the summary field — updated_at stays unchanged."""
        with self._lock:
            e = self._data.get(memory_id)
            if not e:
                return False
            e["summary"] = summary
            self._append_line(e)
        return True

    def update_summary_if_pending(self, memory_id, expected_text, summary):
        """Atomically fill a summary only for the text the worker processed."""
        with self._lock:
            e = self._data.get(memory_id)
            if not e or e.get("text") != expected_text or e.get("summary"):
                return False
            e["summary"] = summary
            self._append_line(e)
        return True

    def get_pending_summaries(self, limit=1000):
        """Records with empty summary, oldest first (background worker recovery)."""
        with self._lock:
            items = [e for e in self._data.values() if not e.get("summary")]
        items.sort(key=lambda e: e.get("created_at", ""))
        return items[:limit]

    def get_details(self, memory_ids):
        """Fetch full records by ID list. Partial success: missing IDs are reported."""
        found, missing = [], []
        for mid in memory_ids:
            e = self._data.get(mid)
            if e:
                found.append(e)
            else:
                missing.append(mid)
        return {"found": found, "missing": missing}

    def delete(self, memory_id):
        with self._lock:
            if memory_id not in self._data:
                return False
            del self._data[memory_id]
            self._tombstones += 1
            self._append_line({
                "id": memory_id, "deleted": True,
                "updated_at": _now(),
            })
        return True

    def clear(self, namespace=None):
        with self._lock:
            if namespace:
                ids = [eid for eid, e in self._data.items() if e["namespace"] == namespace]
                for i in ids:
                    del self._data[i]
                n = len(ids)
            else:
                n = len(self._data)
                self._data = {}
            self._compact_file()
        return n

    def merge(self, source_storage, namespace_prefix=""):
        imported = 0
        renamed = 0
        skipped = 0
        imported_ids = []
        src_memories = source_storage.list_memories(limit=10 ** 9)
        with self._lock:
            existing_pairs = {(e["namespace"], e["text"]) for e in self._data.values()}
            for m in src_memories:
                ns = namespace_prefix + m["namespace"]
                # Dedup on content + namespace: already present → skip.
                if (ns, m["text"]) in existing_pairs:
                    skipped += 1
                    continue
                mid = m["id"]
                if mid in self._data:
                    mid = _new_id()
                    renamed += 1
                existing_pairs.add((ns, m["text"]))
                entry = {
                    "id": mid, "text": m["text"],
                    "metadata": m["metadata"], "namespace": ns,
                    "created_at": m["created_at"], "updated_at": m["updated_at"],
                    "summary": m.get("summary"),
                }
                self._data[mid] = entry
                self._append_line(entry)
                imported += 1
                imported_ids.append(mid)
            self._compact_file()
        return {"imported": imported, "skipped": skipped, "renamed": renamed,
                "total": len(src_memories), "imported_ids": imported_ids}

    def _add_with_id(self, memory_id, text, embedding, metadata, namespace,
                     created_at, updated_at, summary=None):
        """Insert/update with explicit ID. Internal use only (sync/recovery)."""
        with self._lock:
            entry = {
                "id": memory_id, "text": text,
                "metadata": metadata or {}, "namespace": namespace,
                "created_at": created_at, "updated_at": updated_at,
                "summary": summary,
            }
            self._data[memory_id] = entry
            self._append_line(entry)

    def replace_all(self, memories: list[dict]):
        """Atomically replace this replica with active primary records.

        Used by manual full Sync. Build the complete in-memory snapshot first,
        then compact once so an interrupted rebuild never leaves a partial file.
        """
        with self._lock:
            self._data = {
                m["id"]: {
                    "id": m["id"], "text": m["text"],
                    "metadata": m.get("metadata") or {},
                    "namespace": m["namespace"],
                    "created_at": m["created_at"], "updated_at": m["updated_at"],
                    "summary": m.get("summary"),
                }
                for m in memories
            }
            self._compact_file()

    def get_stats(self):
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
            "backend": "ndjson",
            "path": self.path,
        }
