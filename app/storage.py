import json
import sqlite3
import pickle
import uuid
import threading
import time
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np

logger = logging.getLogger("storage")

DEFAULT_NAMESPACE = "default"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    a_np = np.array(a, dtype=np.float64)
    b_np = np.array(b, dtype=np.float64)
    return float(np.dot(a_np, b_np) / (np.linalg.norm(a_np) * np.linalg.norm(b_np) + 1e-10))


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

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
               metadata: Optional[dict] = None,
               namespace: Optional[str] = None) -> bool: ...

    @abstractmethod
    def delete(self, memory_id: str) -> bool: ...

    @abstractmethod
    def clear(self, namespace: Optional[str] = None) -> int: ...

    @abstractmethod
    def merge(self, source_storage: "MemoryStorage",
              namespace_prefix: str = "") -> dict[str, int]: ...

    @abstractmethod
    def get_stats(self) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# JSON Storage
# ---------------------------------------------------------------------------

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

    def list_memories(self, namespace=None, limit=100, offset=0):
        entries = list(self._data.values())
        if namespace:
            entries = [e for e in entries if e["namespace"] == namespace]
        entries.sort(key=lambda e: e["created_at"], reverse=True)
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


# ---------------------------------------------------------------------------
# SQLite Storage
# ---------------------------------------------------------------------------

class SQLiteStorage(MemoryStorage):
    def __init__(self, path: str = "data/memories.db"):
        self.path = path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        self.conn.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;

            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                embedding BLOB,
                metadata TEXT NOT NULL DEFAULT '{}',
                namespace TEXT NOT NULL DEFAULT 'default',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_memories_namespace ON memories(namespace);
            CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at);
            CREATE INDEX IF NOT EXISTS idx_memories_updated_at ON memories(updated_at);
        """)
        # Migration for existing DBs without `deleted` column
        try:
            self.conn.execute("ALTER TABLE memories ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # column already exists
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

    # -- internal sync protocol --

    def _add_with_id(self, memory_id, text, embedding, metadata, namespace,
                     created_at, updated_at):
        """Insert/replace with explicit ID. Internal use only (sync/recovery).
        Uses INSERT OR REPLACE so that soft-deleted records can be restored.
        """
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO memories "
                "(id, text, embedding, metadata, namespace, created_at, updated_at, deleted) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                (memory_id, text, self._serialize(embedding),
                 json.dumps(metadata or {}), namespace, created_at, updated_at),
            )
            self.conn.commit()

    def _get_updated_since(self, iso_timestamp: str) -> list[dict]:
        """Return all memories (including deleted) with updated_at > timestamp.
        Internal use only (DualStorage sync job).
        """
        rows = self.conn.execute(
            "SELECT * FROM memories WHERE updated_at > ? ORDER BY updated_at ASC",
            (iso_timestamp,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # -- public API --

    def add(self, text, embedding, metadata=None, namespace=DEFAULT_NAMESPACE):
        eid = _new_id()
        now = _now()
        with self._lock:
            self.conn.execute(
                "INSERT INTO memories (id, text, embedding, metadata, namespace, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (eid, text, self._serialize(embedding), json.dumps(metadata or {}), namespace, now, now),
            )
            self.conn.commit()
        return eid

    def search(self, query_embedding, k=5, threshold=0.0, namespace=None):
        with self._lock:
            if namespace:
                rows = self.conn.execute(
                    "SELECT * FROM memories WHERE namespace = ? AND deleted = 0", (namespace,)
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM memories WHERE deleted = 0"
                ).fetchall()
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
        with self._lock:
            if namespace:
                rows = self.conn.execute(
                    "SELECT * FROM memories WHERE namespace = ? AND deleted = 0 "
                    "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (namespace, limit, offset),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM memories WHERE deleted = 0 "
                    "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get(self, memory_id):
        row = self.conn.execute(
            "SELECT * FROM memories WHERE id = ? AND deleted = 0", (memory_id,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def update(self, memory_id, text=None, embedding=None, metadata=None, namespace=None):
        with self._lock:
            existing = self.get(memory_id)
            if not existing:
                return False
            now = _now()
            text = text if text is not None else existing["text"]
            emb = embedding if embedding is not None else existing["embedding"]
            meta = {**existing["metadata"], **(metadata or {})}
            ns = namespace if namespace is not None else existing["namespace"]
            self.conn.execute(
                "UPDATE memories SET text = ?, embedding = ?, metadata = ?, "
                "namespace = ?, updated_at = ? WHERE id = ?",
                (text, self._serialize(emb), json.dumps(meta), ns, now, memory_id),
            )
            self.conn.commit()
        return True

    def delete(self, memory_id):
        """Soft-delete: mark as deleted instead of removing."""
        with self._lock:
            c = self.conn.execute(
                "UPDATE memories SET deleted = 1, updated_at = ? WHERE id = ? AND deleted = 0",
                (_now(), memory_id),
            )
            self.conn.commit()
        return c.rowcount > 0

    def clear(self, namespace=None):
        """Soft-clear: mark all as deleted instead of removing."""
        with self._lock:
            if namespace:
                n = self.conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE namespace = ? AND deleted = 0",
                    (namespace,),
                ).fetchone()[0]
                self.conn.execute(
                    "UPDATE memories SET deleted = 1, updated_at = ? WHERE namespace = ? AND deleted = 0",
                    (_now(), namespace),
                )
            else:
                n = self.conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE deleted = 0"
                ).fetchone()[0]
                self.conn.execute(
                    "UPDATE memories SET deleted = 1, updated_at = ? WHERE deleted = 0",
                    (_now(),),
                )
            self.conn.commit()
        return n

    def merge(self, source_storage, namespace_prefix=""):
        imported = 0
        renamed = 0
        with self._lock:
            existing_ids = set(
                row[0]
                for row in self.conn.execute("SELECT id FROM memories WHERE deleted = 0").fetchall()
            )
            src_memories = source_storage.list_memories(limit=10 ** 9)
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
        with self._lock:
            total = self.conn.execute(
                "SELECT COUNT(*) FROM memories WHERE deleted = 0"
            ).fetchone()[0]
            ns_rows = self.conn.execute(
                "SELECT namespace, COUNT(*) as cnt FROM memories WHERE deleted = 0 GROUP BY namespace"
            ).fetchall()
        return {
            "total": total,
            "namespaces": [r["namespace"] for r in ns_rows],
            "namespace_counts": {r["namespace"]: r["cnt"] for r in ns_rows},
            "backend": "sqlite",
            "path": self.path,
        }


# ---------------------------------------------------------------------------
# Dual Storage: SQLite primary + JSON async replica (background job)
# ---------------------------------------------------------------------------

class DualStorage(MemoryStorage):
    """Dual-write via background job.

    SQLite is the authoritative primary. A background thread periodically
    syncs changed/deleted records to the JSON secondary using incremental
    sync (via updated_at) with periodic full rebuild for cleanup.

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
        self._dirty = True   # Mark dirty on start so existing data gets synced on first cycle
        self._last_sync_iso = "1970-01-01T00:00:00+00:00"   # ISO UTC watermark
        self._last_sync_mono = time.monotonic()              # monotonic timing
        self._sync_count = 0
        self._lock = threading.RLock()

        # Background sync thread
        self._stop = threading.Event()
        self._job = threading.Thread(target=self._sync_loop, daemon=True)
        self._job.start()
        logger.info("DualStorage started: sync_interval=%ds secondary=%s",
                     sync_interval, getattr(secondary, 'path', '?'))

    # -- background sync job --

    def _sync_loop(self):
        while not self._stop.wait(self.sync_interval):
            if not self._dirty:
                continue
            try:
                self._sync_count += 1
                # Full rebuild every 12 cycles (~1 hour) to clean up zombies
                if self._sync_count % 12 == 0:
                    self._full_sync()
                else:
                    self._incremental_sync()
            except Exception:
                logger.error("background sync failed", exc_info=True)

    def _incremental_sync(self):
        """Delta sync: only records updated since last watermark.

        Dual-watermark fix:
        - Capture sync_start time BEFORE querying.
        - Set watermark = sync_start AFTER syncing.
        - Writes during the sync will have updated_at > sync_start
          and will be picked up next cycle.
        """
        sync_start_iso = _now()
        sync_start_mono = time.monotonic()

        changed = self.primary._get_updated_since(self._last_sync_iso)
        if not changed:
            self._last_sync_iso = sync_start_iso
            self._last_sync_mono = sync_start_mono
            self._dirty = False
            return

        synced = 0
        for m in changed:
            try:
                if m.get("deleted"):
                    # Propagate deletion to secondary
                    self.secondary.delete(m["id"])
                else:
                    existing = self.secondary.get(m["id"])
                    if existing:
                        self.secondary.update(
                            m["id"], text=m["text"], embedding=m["embedding"],
                            metadata=m["metadata"], namespace=m["namespace"])
                    else:
                        self.secondary._add_with_id(
                            m["id"], m["text"], m["embedding"],
                            m["metadata"], m["namespace"],
                            m["created_at"], m["updated_at"])
                synced += 1
            except Exception:
                logger.warning("incremental sync: failed to sync id=%s", m["id"], exc_info=True)

        self._last_sync_iso = sync_start_iso
        self._last_sync_mono = sync_start_mono
        self._dirty = False
        logger.info("incremental sync: %d changed, %d synced", len(changed), synced)

    def _full_sync(self):
        """Full rebuild: clear secondary and copy all from primary (paginated)."""
        from .recovery import quick_sync
        result = quick_sync(self)
        self._dirty = False
        self._last_sync_iso = _now()
        self._last_sync_mono = time.monotonic()
        logger.info("full sync: %s", result)

    # -- public API: writes → primary + dirty --

    def add(self, text, embedding, metadata=None, namespace=DEFAULT_NAMESPACE):
        with self._lock:
            eid = self.primary.add(text, embedding, metadata, namespace)
            self._dirty = True
        return eid

    def update(self, memory_id, text=None, embedding=None, metadata=None, namespace=None):
        with self._lock:
            ok = self.primary.update(memory_id, text, embedding, metadata, namespace)
            if ok:
                self._dirty = True
        return ok

    def delete(self, memory_id):
        with self._lock:
            ok = self.primary.delete(memory_id)
            if ok:
                self._dirty = True
        return ok

    def clear(self, namespace=None):
        with self._lock:
            n = self.primary.clear(namespace)
            if n:
                self._dirty = True
        return n

    def merge(self, source_storage, namespace_prefix=""):
        with self._lock:
            result = self.primary.merge(source_storage, namespace_prefix)
            if result.get("imported", 0):
                self._dirty = True
        return result

    # -- public API: reads → primary only --

    def search(self, *args, **kwargs):
        return self.primary.search(*args, **kwargs)

    def list_memories(self, *args, **kwargs):
        return self.primary.list_memories(*args, **kwargs)

    def get(self, *args, **kwargs):
        return self.primary.get(*args, **kwargs)

    # -- operations --

    def force_sync(self) -> dict:
        """Manually trigger a full rebuild of secondary from primary."""
        self._full_sync()
        return {"synced": True}

    def get_stats(self) -> dict:
        p = self.primary.get_stats()
        try:
            s = self.secondary.get_stats()
        except Exception:
            s = {"total": -1, "backend": "json", "path": "?", "error": "unavailable"}
        return {
            "total": p["total"],
            "backend": "dual",
            "in_sync": p["total"] == s.get("total", -1),
            "dirty": self._dirty,
            "last_sync_ts": self._last_sync_iso,
            "sync_count": self._sync_count,
            "sync_interval_s": self.sync_interval,
            # Bubble up namespace info from primary for UI/API compatibility
            "namespaces": p.get("namespaces", []),
            "namespace_counts": p.get("namespace_counts", {}),
            "primary": p,
            "secondary": s,
        }

    def stop(self):
        """Gracefully stop the background sync thread."""
        self._stop.set()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_storage(backend: str = "sqlite", **kwargs) -> MemoryStorage:
    if backend == "json":
        return JSONStorage(path=kwargs.get("path", "data/memories.json"))
    if backend == "sqlite":
        return SQLiteStorage(path=kwargs.get("path", "data/memories.db"))
    if backend == "dual":
        primary_path = kwargs.get("primary_path", "data/memories.db")
        secondary_path = kwargs.get("secondary_path",
                                    kwargs.get("path", "").replace(".db", ".json")
                                    if kwargs.get("path") else "data/memories.json")
        sync_interval = int(kwargs.get("sync_interval", 300))
        return DualStorage(
            primary=SQLiteStorage(path=primary_path),
            secondary=JSONStorage(path=secondary_path),
            sync_interval=sync_interval,
        )
    raise ValueError(f"Unknown backend: {backend}")
