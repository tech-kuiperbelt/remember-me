"""SQLite storage — authoritative primary with Outbox sync protocol."""

import json
import sqlite3
import pickle
import threading
import logging

logger = logging.getLogger("storage_sqlite")

from .storage import MemoryStorage, _now, _new_id, _cosine_similarity, DEFAULT_NAMESPACE


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
        # Migration: outbox synced column (format-agnostic, not tied to JSON)
        try:
            self.conn.execute("ALTER TABLE memories ADD COLUMN synced INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # column already exists
        # Composite index for outbox polling: WHERE synced=0 ORDER BY updated_at
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_synced_updated "
            "ON memories(synced, updated_at)"
        )
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
        Sets synced=1 because data is already in (or being restored from) secondary.
        Uses INSERT OR REPLACE so that soft-deleted records can be restored.
        """
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO memories "
                "(id, text, embedding, metadata, namespace, created_at, updated_at, deleted, synced) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1)",
                (memory_id, text, self._serialize(embedding),
                 json.dumps(metadata or {}), namespace, created_at, updated_at),
            )
            self.conn.commit()

    # -- internal sync protocol (outbox pattern) --

    def _get_unsynced(self, limit=500):
        """Return records with synced=0 (including deleted), ordered by updated_at.
        Internal use only (DualStorage outbox sync job).
        """
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM memories WHERE synced = 0 ORDER BY updated_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def _mark_synced_batch(self, memory_ids):
        """Batch-mark records as synced. Internal use only."""
        if not memory_ids:
            return
        with self._lock:
            placeholders = ",".join(["?"] * len(memory_ids))
            self.conn.execute(
                f"UPDATE memories SET synced = 1 WHERE id IN ({placeholders})",
                memory_ids,
            )
            self.conn.commit()

    def _count_unsynced(self) -> int:
        """Count active records awaiting sync. Internal use only (stats)."""
        with self._lock:
            return self.conn.execute(
                "SELECT COUNT(*) FROM memories WHERE synced = 0 AND deleted = 0"
            ).fetchone()[0]

    # -- public API --

    def add(self, text, embedding, metadata=None, namespace=DEFAULT_NAMESPACE):
        eid = _new_id()
        now = _now()
        with self._lock:
            self.conn.execute(
                "INSERT INTO memories (id, text, embedding, metadata, namespace, created_at, updated_at, synced) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
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
                "namespace = ?, updated_at = ?, synced = 0 WHERE id = ?",
                (text, self._serialize(emb), json.dumps(meta), ns, now, memory_id),
            )
            self.conn.commit()
        return True

    def delete(self, memory_id):
        """Soft-delete: mark as deleted and unsynced."""
        with self._lock:
            c = self.conn.execute(
                "UPDATE memories SET deleted = 1, synced = 0, updated_at = ? WHERE id = ? AND deleted = 0",
                (_now(), memory_id),
            )
            self.conn.commit()
        return c.rowcount > 0

    def clear(self, namespace=None):
        """Soft-clear: mark all as deleted and unsynced."""
        with self._lock:
            if namespace:
                n = self.conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE namespace = ? AND deleted = 0",
                    (namespace,),
                ).fetchone()[0]
                self.conn.execute(
                    "UPDATE memories SET deleted = 1, synced = 0, updated_at = ? WHERE namespace = ? AND deleted = 0",
                    (_now(), namespace),
                )
            else:
                n = self.conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE deleted = 0"
                ).fetchone()[0]
                self.conn.execute(
                    "UPDATE memories SET deleted = 1, synced = 0, updated_at = ? WHERE deleted = 0",
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
                    "INSERT INTO memories (id, text, embedding, metadata, namespace, created_at, updated_at, synced) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
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
