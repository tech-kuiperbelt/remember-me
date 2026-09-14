"""JSON array storage — full file rewrite on every write."""

import json
import threading
import logging

logger = logging.getLogger("storage_json")

from .storage import MemoryStorage, _now, _new_id, _cosine_similarity, DEFAULT_NAMESPACE


class JSONStorage(MemoryStorage):
    """Deprecated legacy backend retained for import and compatibility only."""
    def __init__(self, path: str = "data/memories.json"):
        self.path = path
        self._lock = threading.RLock()
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                raw = json.load(f)
            for item in raw:
                self._data[item["id"]] = item
        except (FileNotFoundError, json.JSONDecodeError):
            self._data = {}

    def _save(self):
        with open(self.path, "w") as f:
            json.dump(list(self._data.values()), f, ensure_ascii=False, indent=2)

    # -- internal sync protocol --

    def _add_with_id(self, memory_id, text, embedding, metadata, namespace,
                     created_at, updated_at, summary=None):
        """Insert with explicit ID. Internal use only (sync/recovery)."""
        with self._lock:
            if memory_id in self._data:
                raise ValueError(f"Memory id {memory_id} already exists in JSON storage")
            self._data[memory_id] = {
                "id": memory_id, "text": text, "embedding": embedding,
                "metadata": metadata or {}, "namespace": namespace,
                "created_at": created_at, "updated_at": updated_at,
                "summary": summary,
            }
            self._save()

    # -- public API --

    def add(self, text, embedding, metadata=None, namespace=DEFAULT_NAMESPACE, summary=None,
            index_embeddings=None):
        """Insert a memory; dedup on (namespace, text). Returns {"id", "created"}."""
        with self._lock:
            for e in self._data.values():
                if e["namespace"] == namespace and e["text"] == text:
                    return {"id": e["id"], "created": False}
            eid = _new_id()
            now = _now()
            entry = {
                "id": eid, "text": text, "embedding": embedding,
                "metadata": metadata or {}, "namespace": namespace,
                "created_at": now, "updated_at": now,
                "summary": summary,
            }
            self._data[eid] = entry
            self._save()
        return {"id": eid, "created": True}

    def search(self, query_embedding, k=5, threshold=0.0, namespace=None,
               categories=None, keywords=None):
        # Metadata-index filtering is SQLite-only; accepted for interface
        # compatibility and ignored by this legacy backend.
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
                      updated_after=None, updated_before=None, keywords=None,
                      categories=None):
        # keywords/categories filtering is SQLite-only (rebuildable indexes);
        # JSON replicas accept the parameters for interface compatibility and
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

    def update(self, memory_id, text=None, embedding=None, metadata=None, namespace=None, summary=None,
               index_embeddings=None):
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
            self._save()
        return True

    def update_summary(self, memory_id, summary):
        """Update only the summary field — updated_at stays unchanged."""
        with self._lock:
            e = self._data.get(memory_id)
            if not e:
                return False
            e["summary"] = summary
            self._save()
        return True

    def update_summary_if_pending(self, memory_id, expected_text, summary):
        """Atomically fill a summary only for the text the worker processed."""
        with self._lock:
            e = self._data.get(memory_id)
            if not e or e.get("text") != expected_text or e.get("summary"):
                return False
            e["summary"] = summary
            self._save()
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
            if memory_id in self._data:
                del self._data[memory_id]
                self._save()
                return True
            return False

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
            self._save()
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
                    "id": mid, "text": m["text"], "embedding": m["embedding"],
                    "metadata": m["metadata"], "namespace": ns,
                    "created_at": m["created_at"], "updated_at": m["updated_at"],
                    "summary": m.get("summary"),
                }
                self._data[mid] = entry
                imported += 1
                imported_ids.append(mid)
            self._save()
        return {"imported": imported, "skipped": skipped, "renamed": renamed,
                "total": len(src_memories), "imported_ids": imported_ids}

    def get_stats(self):
        namespaces = set(e["namespace"] for e in self._data.values())
        ns_counts = {}
        for ns in namespaces:
            ns_counts[ns] = sum(1 for e in self._data.values() if e["namespace"] == ns)
        return {
            "total": len(self._data),
            "namespaces": list(namespaces),
            "namespace_counts": ns_counts,
            "backend": "json",
            "path": self.path,
        }
    def exact_index_values(self, dimension, values, namespace=None):
        wanted = set(values or [])
        found = set()
        for memory in self.list_memories(namespace=namespace, limit=10 ** 9):
            found.update(wanted & set((memory.get("metadata") or {}).get(dimension) or []))
        return found
