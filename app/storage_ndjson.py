"""NDJSON / JSON Lines storage — append-only, crash-safe."""

import json
import os
import threading
import logging

logger = logging.getLogger("storage_ndjson")

from .storage import MemoryStorage, _now, _new_id, _cosine_similarity, DEFAULT_NAMESPACE


class NDJSONStorage(MemoryStorage):
    """Line-delimited JSON storage (.jsonl).

    Each record is one JSON object per line. Append-only writes:
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
                    existing = records.get(rid)
                    if not existing or m["updated_at"] >= existing["updated_at"]:
                        records[rid] = m
        self._data = records
        self._tombstones = tombstones

    def _append_line(self, record: dict):
        """Append one JSON line to the file. Caller must hold _lock."""
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with open(self.path, "a") as f:
            f.write(line)

    def _compact_file(self):
        """Rewrite file from _data dict, atomically, removing tombstones/dupes."""
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            for m in self._data.values():
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
        os.replace(tmp, self.path)
        self._tombstones = 0

    # -- public API --

    def add(self, text, embedding, metadata=None, namespace=DEFAULT_NAMESPACE, summary=None):
        eid = _new_id()
        now = _now()
        entry = {
            "id": eid, "text": text, "embedding": embedding,
            "metadata": metadata or {}, "namespace": namespace,
            "created_at": now, "updated_at": now,
            "summary": summary,
        }
        with self._lock:
            self._data[eid] = entry
            self._append_line(entry)
        return eid

    def search(self, query_embedding, k=5, threshold=0.0, namespace=None):
        scored = []
        for e in self._data.values():
            if namespace and e["namespace"] != namespace:
                continue
            score = _cosine_similarity(query_embedding, e["embedding"])
            if score >= threshold:
                scored.append((score, e))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{**e, "score": round(s, 6)} for s, e in scored[:k]]

    def list_memories(self, namespace=None, limit=100, offset=0, sort_by="created_at",
                      updated_after=None, updated_before=None):
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
            if embedding is not None:
                e["embedding"] = embedding
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
        imported_ids = []
        src_memories = source_storage.list_memories(limit=10 ** 9)
        with self._lock:
            for m in src_memories:
                mid = m["id"]
                if mid in self._data:
                    mid = _new_id()
                    renamed += 1
                ns = namespace_prefix + m["namespace"]
                entry = {
                    "id": mid, "text": m["text"], "embedding": m["embedding"],
                    "metadata": m["metadata"], "namespace": ns,
                    "created_at": m["created_at"], "updated_at": m["updated_at"],
                    "summary": m.get("summary"),
                }
                self._data[mid] = entry
                self._append_line(entry)
                imported += 1
                imported_ids.append(mid)
            self._compact_file()
        return {"imported": imported, "renamed": renamed, "total": len(src_memories),
                "imported_ids": imported_ids}

    def _add_with_id(self, memory_id, text, embedding, metadata, namespace,
                     created_at, updated_at, summary=None):
        """Insert/update with explicit ID. Internal use only (sync/recovery)."""
        with self._lock:
            entry = {
                "id": memory_id, "text": text, "embedding": embedding,
                "metadata": metadata or {}, "namespace": namespace,
                "created_at": created_at, "updated_at": updated_at,
                "summary": summary,
            }
            self._data[memory_id] = entry
            self._append_line(entry)

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
