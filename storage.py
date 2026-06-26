import json
import sqlite3
import pickle
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np

DEFAULT_NAMESPACE = "default"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    a_np = np.array(a, dtype=np.float64)
    b_np = np.array(b, dtype=np.float64)
    return float(np.dot(a_np, b_np) / (np.linalg.norm(a_np) * np.linalg.norm(b_np) + 1e-10))


class MemoryStorage(ABC):
    @abstractmethod
    def add(self, text: str, embedding: list[float], metadata: Optional[dict] = None,
            namespace: str = DEFAULT_NAMESPACE) -> str: ...

    @abstractmethod
    def search(self, query_embedding: list[float], k: int = 5, threshold: float = 0.0,
               namespace: Optional[str] = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    def list_memories(self, namespace: Optional[str] = None, limit: int = 100,
                      offset: int = 0) -> list[dict[str, Any]]: ...

    @abstractmethod
    def get(self, memory_id: str) -> Optional[dict[str, Any]]: ...

    @abstractmethod
    def update(self, memory_id: str, text: Optional[str] = None,
               embedding: Optional[list[float]] = None,
               metadata: Optional[dict] = None) -> bool: ...

    @abstractmethod
    def delete(self, memory_id: str) -> bool: ...

    @abstractmethod
    def clear(self, namespace: Optional[str] = None) -> int: ...

    @abstractmethod
    def merge(self, source_storage: "MemoryStorage",
              namespace_prefix: str = "") -> dict[str, int]: ...

    @abstractmethod
    def get_stats(self) -> dict[str, Any]: ...


class JSONStorage(MemoryStorage):
    def __init__(self, path: str = "memories.json"):
        self.path = path
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

    def add(self, text, embedding, metadata=None, namespace=DEFAULT_NAMESPACE):
        eid = _new_id()
        now = _now()
        entry = {
            "id": eid, "text": text, "embedding": embedding,
            "metadata": metadata or {}, "namespace": namespace,
            "created_at": now, "updated_at": now,
        }
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

    def list_memories(self, namespace=None, limit=100, offset=0):
        entries = list(self._data.values())
        if namespace:
            entries = [e for e in entries if e["namespace"] == namespace]
        entries.sort(key=lambda e: e["created_at"], reverse=True)
        return entries[offset:offset + limit]

    def get(self, memory_id):
        return self._data.get(memory_id)

    def update(self, memory_id, text=None, embedding=None, metadata=None):
        e = self._data.get(memory_id)
        if not e:
            return False
        if text is not None:
            e["text"] = text
        if embedding is not None:
            e["embedding"] = embedding
        if metadata is not None:
            e["metadata"] = {**e["metadata"], **metadata}
        e["updated_at"] = _now()
        self._save()
        return True

    def delete(self, memory_id):
        if memory_id in self._data:
            del self._data[memory_id]
            self._save()
            return True
        return False

    def clear(self, namespace=None):
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
        src_memories = source_storage.list_memories(limit=10**9)
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
        return {"total": len(self._data), "namespaces": list(namespaces), "backend": "json", "path": self.path}


class SQLiteStorage(MemoryStorage):
    def __init__(self, path: str = "memories.db"):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                embedding BLOB,
                metadata TEXT NOT NULL DEFAULT '{}',
                namespace TEXT NOT NULL DEFAULT 'default',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_memories_namespace ON memories(namespace);
            CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at);
        """)
        self.conn.commit()

    def _serialize(self, emb: list[float]) -> bytes:
        return pickle.dumps(emb)

    def _deserialize(self, blob: bytes) -> list[float]:
        return pickle.loads(blob) if blob else []

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        d["embedding"] = self._deserialize(d["embedding"])
        d["metadata"] = json.loads(d["metadata"])
        return d

    def add(self, text, embedding, metadata=None, namespace=DEFAULT_NAMESPACE):
        eid = _new_id()
        now = _now()
        self.conn.execute(
            "INSERT INTO memories (id, text, embedding, metadata, namespace, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (eid, text, self._serialize(embedding), json.dumps(metadata or {}), namespace, now, now),
        )
        self.conn.commit()
        return eid

    def search(self, query_embedding, k=5, threshold=0.0, namespace=None):
        if namespace:
            rows = self.conn.execute(
                "SELECT * FROM memories WHERE namespace = ?", (namespace,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM memories").fetchall()
        scored = []
        for row in rows:
            emb = self._deserialize(row["embedding"])
            score = _cosine_similarity(query_embedding, emb)
            if score >= threshold:
                d = dict(row)
                d["embedding"] = emb
                d["metadata"] = json.loads(d["metadata"])
                scored.append((score, d))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{**d, "score": round(s, 6)} for s, d in scored[:k]]

    def list_memories(self, namespace=None, limit=100, offset=0):
        if namespace:
            rows = self.conn.execute(
                "SELECT * FROM memories WHERE namespace = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (namespace, limit, offset),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM memories ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get(self, memory_id):
        row = self.conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def update(self, memory_id, text=None, embedding=None, metadata=None):
        existing = self.get(memory_id)
        if not existing:
            return False
        now = _now()
        text = text if text is not None else existing["text"]
        emb = embedding if embedding is not None else existing["embedding"]
        meta = {**existing["metadata"], **(metadata or {})}
        self.conn.execute(
            "UPDATE memories SET text = ?, embedding = ?, metadata = ?, updated_at = ? WHERE id = ?",
            (text, self._serialize(emb), json.dumps(meta), now, memory_id),
        )
        self.conn.commit()
        return True

    def delete(self, memory_id):
        c = self.conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self.conn.commit()
        return c.rowcount > 0

    def clear(self, namespace=None):
        if namespace:
            n = self.conn.execute("SELECT COUNT(*) FROM memories WHERE namespace = ?", (namespace,)).fetchone()[0]
            self.conn.execute("DELETE FROM memories WHERE namespace = ?", (namespace,))
        else:
            n = self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            self.conn.execute("DELETE FROM memories")
        self.conn.commit()
        return n

    def merge(self, source_storage, namespace_prefix=""):
        imported = 0
        renamed = 0
        existing_ids = set(
            row[0] for row in self.conn.execute("SELECT id FROM memories").fetchall()
        )
        src_memories = source_storage.list_memories(limit=10**9)
        for m in src_memories:
            mid = m["id"]
            if mid in existing_ids:
                mid = _new_id()
                renamed += 1
            existing_ids.add(mid)
            ns = namespace_prefix + m["namespace"]
            self.conn.execute(
                "INSERT INTO memories (id, text, embedding, metadata, namespace, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (mid, m["text"], self._serialize(m["embedding"]),
                 json.dumps(m["metadata"]), ns, m["created_at"], m["updated_at"]),
            )
            imported += 1
        self.conn.commit()
        return {"imported": imported, "renamed": renamed, "total": len(src_memories)}

    def get_stats(self):
        total = self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        ns_rows = self.conn.execute("SELECT DISTINCT namespace FROM memories").fetchall()
        return {
            "total": total,
            "namespaces": [r[0] for r in ns_rows],
            "backend": "sqlite",
            "path": self.path,
        }


def create_storage(backend: str = "sqlite", **kwargs) -> MemoryStorage:
    if backend == "json":
        return JSONStorage(**kwargs)
    if backend == "sqlite":
        return SQLiteStorage(**kwargs)
    raise ValueError(f"Unknown backend: {backend}")
