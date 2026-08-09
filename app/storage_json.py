"""JSON array storage — full file rewrite on every write."""

import json
import threading
import logging

logger = logging.getLogger("storage_json")

from .storage import MemoryStorage, _now, _new_id, _cosine_similarity, DEFAULT_NAMESPACE


class JSONStorage(MemoryStorage):
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
                     created_at, updated_at):
        """Insert with explicit ID. Internal use only (sync/recovery)."""
        with self._lock:
            if memory_id in self._data:
                raise ValueError(f"Memory id {memory_id} already exists in JSON storage")
            self._data[memory_id] = {
                "id": memory_id, "text": text, "embedding": embedding,
                "metadata": metadata or {}, "namespace": namespace,
                "created_at": created_at, "updated_at": updated_at,
            }
            self._save()

    # -- public API --

    def add(self, text, embedding, metadata=None, namespace=DEFAULT_NAMESPACE):
        eid = _new_id()
        now = _now()
        entry = {
            "id": eid, "text": text, "embedding": embedding,
            "metadata": metadata or {}, "namespace": namespace,
            "created_at": now, "updated_at": now,
        }
        with self._lock:
            self._data[eid] = entry
            self._save()
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

    def update(self, memory_id, text=None, embedding=None, metadata=None, namespace=None):
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
            e["updated_at"] = _now()
            self._save()
        return True

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
                }
                self._data[mid] = entry
                imported += 1
            self._save()
        return {"imported": imported, "renamed": renamed, "total": len(src_memories)}

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
