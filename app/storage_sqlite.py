"""SQLite storage — authoritative primary with Outbox sync protocol."""

import json
import os
import sqlite3
import threading
import logging
from pathlib import Path

logger = logging.getLogger("storage_sqlite")

from .storage import SQLitePrimaryStorage, _now, _new_id, _cosine_similarity, DEFAULT_NAMESPACE
from .categories import MAX_CATEGORIES
from .embedding_codec import decode_embedding, encode_embedding, inspect_embedding_blob
from .summary_policy import summary_for_add, summary_for_update, normalize_whitespace
from .database_lock import DatabaseWriterLock
from .lexical import LEXICAL_INDEX_VERSION, build_match, normalize_k, DEFAULT_K
from .anchors import MAX_ANCHORS, clean_anchors, anchor_fts_text


# storage_state key holding the single derived lexical-index version.
_LEXICAL_INDEX_STATE_KEY = "lexical_index_version"


# Both dimensions are persisted in memories.metadata and indexed through one
# generic value + many-to-many physical model.  Adding another exact metadata
# index requires only a registry entry and its domain normalization rules.
# ``requires_embedding=False`` dimensions (anchors) reuse only the generic
# relation tables and are excluded from the embedding lifecycle entirely.
_INDEX_DIMENSIONS = {
    "keywords": {
        "max_values": None,
        "requires_embedding": True,
    },
    "categories": {
        "max_values": MAX_CATEGORIES,
        "requires_embedding": True,
    },
    "anchors": {
        "max_values": MAX_ANCHORS,
        "requires_embedding": False,
    },
}
_EMBEDDING_DIMENSIONS = tuple(
    name for name, spec in _INDEX_DIMENSIONS.items() if spec["requires_embedding"]
)


def _materially_changes_text(existing_text: str, new_text) -> bool:
    """True when a body update changes more than whitespace-only differences."""
    return (new_text is not None
            and normalize_whitespace(new_text) != normalize_whitespace(existing_text))


class SQLiteStorage(SQLitePrimaryStorage):
    def __init__(self, path: str = "data/memories.db", embedding_dtype: str = "float16",
                 embedding_spec: dict | None = None, read_only: bool = False,
                 writer_lock: bool = False):
        self.path = path
        self.embedding_dtype = embedding_dtype
        self.embedding_spec = embedding_spec
        self.read_only = read_only
        self._active_profile_id = None
        self._lock = threading.RLock()
        self._writer_lock = (DatabaseWriterLock(path)
                             if writer_lock and not read_only and path != ":memory:"
                             else None)
        self.conn = None
        try:
            if self._writer_lock is not None:
                self._writer_lock.acquire()
            if read_only:
                if path == ":memory:" or not os.path.isfile(path):
                    raise FileNotFoundError(f"SQLite database does not exist: {path}")
                uri = Path(path).resolve().as_uri() + "?mode=ro"
                self.conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            else:
                self.conn = sqlite3.connect(path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            if read_only:
                self._load_active_embedding_profile()
            else:
                self._init_db()
        except Exception:
            if self.conn is not None:
                self.conn.close()
                self.conn = None
            if self._writer_lock is not None:
                self._writer_lock.release()
            raise

    def close(self):
        """Close the SQLite connection and release an optional writer lock."""
        with self._lock:
            self.close_connection()
            if self._writer_lock is not None:
                self._writer_lock.release()

    def close_connection(self):
        """Keep the lifetime writer lock while swapping the database contents."""
        with self._lock:
            if self.conn is not None:
                self.conn.close()
                self.conn = None

    def reopen_connection(self):
        with self._lock:
            if self.conn is not None:
                raise RuntimeError("database connection is already open")
            self.conn = sqlite3.connect(self.path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self._load_active_embedding_profile()

    def stop(self):
        """Storage lifecycle hook used by the server shutdown path."""
        self.close()

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
                deleted INTEGER NOT NULL DEFAULT 0,
                version INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_memories_namespace ON memories(namespace);
            CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at);
            CREATE INDEX IF NOT EXISTS idx_memories_updated_at ON memories(updated_at);

            -- Internal, non-memory state for background maintenance jobs.
            -- This is intentionally not part of the NDJSON disaster-recovery
            -- replica: a recovered primary may safely run maintenance again.
            CREATE TABLE IF NOT EXISTS storage_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
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
        # Monotonic per-memory version used only for conditional outbox ack.
        try:
            self.conn.execute("ALTER TABLE memories ADD COLUMN version INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # column already exists
        # Migration: optional caller-provided or deterministically derived summary.
        try:
            self.conn.execute("ALTER TABLE memories ADD COLUMN summary TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
        # Composite index for outbox polling: WHERE synced=0 ORDER BY updated_at
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_synced_updated "
            "ON memories(synced, updated_at)"
        )
        # Generic, rebuildable metadata index.  The authoritative values remain
        # in memories.metadata and therefore in the NDJSON recovery replica.
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS index_values (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dimension TEXT NOT NULL,
                value TEXT NOT NULL,
                UNIQUE (dimension, value)
            );
            CREATE TABLE IF NOT EXISTS memory_index_values (
                memory_id TEXT NOT NULL,
                index_value_id INTEGER NOT NULL,
                PRIMARY KEY (memory_id, index_value_id)
            );
            CREATE INDEX IF NOT EXISTS idx_memory_index_values_value
                ON memory_index_values(index_value_id);

            CREATE TABLE IF NOT EXISTS embedding_profiles (
                profile_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                dimension INTEGER NOT NULL,
                normalized INTEGER NOT NULL DEFAULT 0,
                query_prefix TEXT NOT NULL DEFAULT '',
                document_prefix TEXT NOT NULL DEFAULT '',
                codec TEXT NOT NULL DEFAULT 'rmev1',
                dtype TEXT NOT NULL DEFAULT 'float16',
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                error TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_embedding_profiles_active
                ON embedding_profiles(status) WHERE status = 'active';
            CREATE TABLE IF NOT EXISTS memory_embeddings (
                memory_id TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                embedding BLOB NOT NULL,
                PRIMARY KEY (memory_id, profile_id),
                FOREIGN KEY (memory_id) REFERENCES memories(id),
                FOREIGN KEY (profile_id) REFERENCES embedding_profiles(profile_id)
            );
            CREATE INDEX IF NOT EXISTS idx_memory_embeddings_profile
                ON memory_embeddings(profile_id, memory_id);
            CREATE TABLE IF NOT EXISTS index_value_embeddings (
                index_value_id INTEGER NOT NULL,
                profile_id TEXT NOT NULL,
                embedding BLOB NOT NULL,
                PRIMARY KEY (index_value_id, profile_id),
                FOREIGN KEY (index_value_id) REFERENCES index_values(id),
                FOREIGN KEY (profile_id) REFERENCES embedding_profiles(profile_id)
            );
            CREATE INDEX IF NOT EXISTS idx_index_value_embeddings_profile
                ON index_value_embeddings(profile_id, index_value_id);
        """)
        self._migrate_legacy_metadata_indexes()
        self._ensure_lexical_index()
        self.conn.commit()
        self._load_active_embedding_profile()
        if self.embedding_spec is not None:
            self.configure_embedding_profile(self.embedding_spec)

    def _load_active_embedding_profile(self) -> None:
        row = self.conn.execute(
            "SELECT profile_id FROM embedding_profiles WHERE status = 'active'"
        ).fetchone()
        self._active_profile_id = row["profile_id"] if row else None

    @staticmethod
    def _profile_record(spec: dict) -> dict:
        required = ("profile_id", "provider", "model", "dimension")
        missing = [key for key in required if key not in spec]
        if missing:
            raise ValueError(f"embedding profile missing fields: {', '.join(missing)}")
        return {
            "profile_id": str(spec["profile_id"]),
            "provider": str(spec["provider"]),
            "model": str(spec["model"]),
            "dimension": int(spec["dimension"]),
            "normalized": bool(spec.get("normalized", False)),
            "query_prefix": str(spec.get("query_prefix", "")),
            "document_prefix": str(spec.get("document_prefix", "")),
        }

    def _insert_profile(self, spec: dict, status: str) -> None:
        record = self._profile_record(spec)
        now = _now()
        self.conn.execute(
            "INSERT INTO embedding_profiles "
            "(profile_id, provider, model, dimension, normalized, query_prefix, "
            "document_prefix, codec, dtype, status, created_at, updated_at, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'rmev1', ?, ?, ?, ?, NULL) "
            "ON CONFLICT(profile_id) DO UPDATE SET provider=excluded.provider, "
            "model=excluded.model, dimension=excluded.dimension, "
            "normalized=excluded.normalized, query_prefix=excluded.query_prefix, "
            "document_prefix=excluded.document_prefix, codec=excluded.codec, "
            "dtype=excluded.dtype, status=excluded.status, updated_at=excluded.updated_at, "
            "error=NULL",
            (record["profile_id"], record["provider"], record["model"],
             record["dimension"], int(record["normalized"]), record["query_prefix"],
             record["document_prefix"], self.embedding_dtype, status, now, now),
        )

    def configure_embedding_profile(self, spec: dict) -> dict:
        """Attach this storage to the configured vector space.

        A legacy database with inline embeddings is migrated transactionally
        into the first profile. A database already using a different active
        vector space refuses startup and requires explicit reindex.
        """
        record = self._profile_record(spec)
        with self._lock:
            self._load_active_embedding_profile()
            if self._active_profile_id and self._active_profile_id != record["profile_id"]:
                active = self.conn.execute(
                    "SELECT provider, model, dimension FROM embedding_profiles "
                    "WHERE profile_id = ?", (self._active_profile_id,),
                ).fetchone()
                raise RuntimeError(
                    "configured embedding profile does not match the active database profile: "
                    f"active={self._active_profile_id} "
                    f"({active['provider']}/{active['model']}/{active['dimension']}), "
                    f"configured={record['profile_id']} "
                    f"({record['provider']}/{record['model']}/{record['dimension']}). "
                    "Run the offline reindex command before starting the service."
                )

            migrated = 0
            recoded = 0
            try:
                self.conn.execute("BEGIN")
                if not self._active_profile_id:
                    self._insert_profile(record, "active")
                    rows = self.conn.execute(
                        "SELECT id, embedding FROM memories WHERE embedding IS NOT NULL"
                    ).fetchall()
                    for row in rows:
                        vector = self._deserialize(row["embedding"])
                        if len(vector) != record["dimension"]:
                            raise ValueError(
                                f"legacy embedding dimension mismatch for {row['id']}: "
                                f"expected {record['dimension']}, got {len(vector)}"
                            )
                        self.conn.execute(
                            "INSERT OR REPLACE INTO memory_embeddings "
                            "(memory_id, profile_id, embedding) VALUES (?, ?, ?)",
                            (row["id"], record["profile_id"], self._serialize(vector)),
                        )
                        migrated += 1
                    self.conn.execute("UPDATE memories SET embedding = NULL")
                    self._active_profile_id = record["profile_id"]
                else:
                    # A stale pre-profile process may have written inline
                    # vectors after the initial migration. Reconcile this
                    # mixed state on startup so every active memory remains
                    # searchable, then clear the legacy column again.
                    inline_rows = self.conn.execute(
                        "SELECT id, embedding FROM memories WHERE embedding IS NOT NULL"
                    ).fetchall()
                    for row in inline_rows:
                        vector = self._deserialize(row["embedding"])
                        if len(vector) != record["dimension"]:
                            raise ValueError(
                                f"legacy embedding dimension mismatch for {row['id']}: "
                                f"expected {record['dimension']}, got {len(vector)}"
                            )
                        self.conn.execute(
                            "INSERT INTO memory_embeddings "
                            "(memory_id, profile_id, embedding) VALUES (?, ?, ?) "
                            "ON CONFLICT(memory_id, profile_id) DO UPDATE SET "
                            "embedding=excluded.embedding",
                            (row["id"], self._active_profile_id, self._serialize(vector)),
                        )
                        migrated += 1
                    if inline_rows:
                        self.conn.execute("UPDATE memories SET embedding = NULL")
                    rows = self.conn.execute(
                        "SELECT memory_id, embedding FROM memory_embeddings WHERE profile_id = ?",
                        (self._active_profile_id,),
                    ).fetchall()
                    for row in rows:
                        info = inspect_embedding_blob(row["embedding"])
                        if info.format != "rmev" or info.dtype != self.embedding_dtype:
                            vector = self._deserialize(row["embedding"])
                            self.conn.execute(
                                "UPDATE memory_embeddings SET embedding = ? "
                                "WHERE memory_id = ? AND profile_id = ?",
                                (self._serialize(vector), row["memory_id"], self._active_profile_id),
                            )
                            recoded += 1
                    index_rows = self.conn.execute(
                        "SELECT index_value_id, embedding FROM index_value_embeddings "
                        "WHERE profile_id = ?", (self._active_profile_id,),
                    ).fetchall()
                    for row in index_rows:
                        info = inspect_embedding_blob(row["embedding"])
                        if info.format != "rmev" or info.dtype != self.embedding_dtype:
                            vector = self._deserialize(row["embedding"])
                            self.conn.execute(
                                "UPDATE index_value_embeddings SET embedding = ? "
                                "WHERE index_value_id = ? AND profile_id = ?",
                                (self._serialize(vector), row["index_value_id"],
                                 self._active_profile_id),
                            )
                            recoded += 1
                    self.conn.execute(
                        "UPDATE embedding_profiles SET codec='rmev1', dtype=?, updated_at=? "
                        "WHERE profile_id=?",
                        (self.embedding_dtype, _now(), self._active_profile_id),
                    )
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                self._load_active_embedding_profile()
                raise
        return {"profile_id": self._active_profile_id, "migrated": migrated, "recoded": recoded}

    def reencode_active_embeddings(self, dtype: str) -> dict:
        """Rewrite the active profile with a different codec dtype only."""
        if dtype not in ("float16", "float32"):
            raise ValueError("embedding dtype must be float16 or float32")
        with self._lock:
            if not self._active_profile_id:
                raise RuntimeError("no active embedding profile")
            previous_dtype = self.embedding_dtype
            rows = self.conn.execute(
                "SELECT memory_id, embedding FROM memory_embeddings WHERE profile_id = ?",
                (self._active_profile_id,),
            ).fetchall()
            index_rows = self.conn.execute(
                "SELECT index_value_id, embedding FROM index_value_embeddings WHERE profile_id = ?",
                (self._active_profile_id,),
            ).fetchall()
            try:
                self.conn.execute("BEGIN")
                self.embedding_dtype = dtype
                for row in rows:
                    self.conn.execute(
                        "UPDATE memory_embeddings SET embedding = ? "
                        "WHERE memory_id = ? AND profile_id = ?",
                        (self._serialize(self._deserialize(row["embedding"])),
                         row["memory_id"], self._active_profile_id),
                    )
                for row in index_rows:
                    self.conn.execute(
                        "UPDATE index_value_embeddings SET embedding = ? "
                        "WHERE index_value_id = ? AND profile_id = ?",
                        (self._serialize(self._deserialize(row["embedding"])),
                         row["index_value_id"], self._active_profile_id),
                    )
                self.conn.execute(
                    "UPDATE embedding_profiles SET dtype=?, codec='rmev1', updated_at=? "
                    "WHERE profile_id=?",
                    (dtype, _now(), self._active_profile_id),
                )
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                self.embedding_dtype = previous_dtype
                raise
        return {"profile_id": self._active_profile_id, "dtype": dtype,
                "reencoded": len(rows), "index_values_reencoded": len(index_rows)}

    def _queryable_index_values(self) -> list[sqlite3.Row]:
        placeholders = ",".join("?" * len(_EMBEDDING_DIMENSIONS))
        return self.conn.execute(
            "SELECT iv.id, iv.dimension, iv.value FROM index_values iv "
            "JOIN memory_index_values link ON link.index_value_id = iv.id "
            "JOIN memories m ON m.id = link.memory_id WHERE m.deleted = 0 "
            f"AND iv.dimension IN ({placeholders}) "
            "GROUP BY iv.id, iv.dimension, iv.value ORDER BY iv.dimension, iv.value",
            _EMBEDDING_DIMENSIONS,
        ).fetchall()

    def index_embedding_completeness(self, profile_id: str | None = None) -> dict:
        profile_id = profile_id or self._active_profile_id
        if not profile_id:
            return {"profile_id": None, "expected": 0, "stored": 0, "complete": True}
        with self._lock:
            expected = len(self._queryable_index_values())
            stored = self.conn.execute(
                "SELECT COUNT(DISTINCT ive.index_value_id) FROM index_value_embeddings ive "
                "JOIN memory_index_values link ON link.index_value_id = ive.index_value_id "
                "JOIN memories m ON m.id = link.memory_id "
                "WHERE ive.profile_id = ? AND m.deleted = 0",
                (profile_id,),
            ).fetchone()[0]
        return {"profile_id": profile_id, "expected": expected, "stored": stored,
                "complete": stored == expected}

    def reindex_index_values(self, profile_id: str, embed_documents,
                             batch_size: int = 50, progress_callback=None) -> dict:
        """Rebuild all live index-value vectors for one existing profile."""
        batch_size = max(1, int(batch_size))
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM embedding_profiles WHERE profile_id = ?", (profile_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown embedding profile: {profile_id}")
            values = self._queryable_index_values()
        prior_spec = self.embedding_spec
        prior_dtype = self.embedding_dtype
        self.embedding_spec = {
            "profile_id": row["profile_id"], "provider": row["provider"],
            "model": row["model"], "dimension": row["dimension"],
            "normalized": bool(row["normalized"]), "query_prefix": row["query_prefix"],
            "document_prefix": row["document_prefix"],
        }
        self.embedding_dtype = row["dtype"]
        completed = 0
        try:
            for offset in range(0, len(values), batch_size):
                batch = values[offset:offset + batch_size]
                vectors = embed_documents([item["value"] for item in batch])
                if len(vectors) != len(batch):
                    raise ValueError(
                        f"provider returned {len(vectors)} index vectors for {len(batch)} values"
                    )
                with self._lock:
                    self.conn.execute("BEGIN")
                    try:
                        for item, vector in zip(batch, vectors):
                            self.conn.execute(
                                "INSERT INTO index_value_embeddings "
                                "(index_value_id, profile_id, embedding) VALUES (?, ?, ?) "
                                "ON CONFLICT(index_value_id, profile_id) DO UPDATE SET "
                                "embedding=excluded.embedding",
                                (item["id"], profile_id, self._serialize(vector)),
                            )
                        self.conn.commit()
                    except Exception:
                        self.conn.rollback()
                        raise
                completed += len(batch)
                if progress_callback:
                    progress_callback(completed, len(values))
            completeness = self.index_embedding_completeness(profile_id)
            if not completeness["complete"]:
                raise RuntimeError(
                    "index-value reindex validation failed: "
                    f"expected {completeness['expected']}, stored {completeness['stored']}"
                )
            return {"profile_id": profile_id, "reindexed": completed,
                    "status": "complete"}
        finally:
            self.embedding_spec = prior_spec
            self.embedding_dtype = prior_dtype

    def reindex_embeddings(self, spec: dict, embed_documents, batch_size: int = 50,
                           progress_callback=None) -> dict:
        """Build a complete shadow profile and atomically activate it.

        The caller must run this offline: writes made while the snapshot is
        being embedded are intentionally not reconciled in this first version.
        """
        record = self._profile_record(spec)
        batch_size = max(1, int(batch_size))
        with self._lock:
            old_profile = self._active_profile_id
            if old_profile == record["profile_id"]:
                return {"profile_id": old_profile, "reindexed": 0,
                        "status": "already_active"}
            memories = self.conn.execute(
                "SELECT id, text FROM memories WHERE deleted = 0 ORDER BY created_at, id"
            ).fetchall()
            self.conn.execute("BEGIN")
            try:
                self._insert_profile(record, "building")
                self.conn.execute(
                    "DELETE FROM memory_embeddings WHERE profile_id = ?",
                    (record["profile_id"],),
                )
                self.conn.execute(
                    "DELETE FROM index_value_embeddings WHERE profile_id = ?",
                    (record["profile_id"],),
                )
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

        prior_spec = self.embedding_spec
        self.embedding_spec = record
        completed = 0
        try:
            for offset in range(0, len(memories), batch_size):
                batch = memories[offset:offset + batch_size]
                vectors = embed_documents([row["text"] for row in batch])
                if len(vectors) != len(batch):
                    raise ValueError(
                        f"provider returned {len(vectors)} vectors for {len(batch)} texts"
                    )
                with self._lock:
                    self.conn.execute("BEGIN")
                    try:
                        for row, vector in zip(batch, vectors):
                            if len(vector) != record["dimension"]:
                                raise ValueError(
                                    f"embedding dimension mismatch for {row['id']}: "
                                    f"expected {record['dimension']}, got {len(vector)}"
                                )
                            self.conn.execute(
                                "INSERT INTO memory_embeddings "
                                "(memory_id, profile_id, embedding) VALUES (?, ?, ?)",
                                (row["id"], record["profile_id"], self._serialize(vector)),
                            )
                        self.conn.execute(
                            "UPDATE embedding_profiles SET updated_at=? WHERE profile_id=?",
                            (_now(), record["profile_id"]),
                        )
                        self.conn.commit()
                    except Exception:
                        self.conn.rollback()
                        raise
                completed += len(batch)
                if progress_callback is not None:
                    progress_callback(completed, len(memories))

            index_result = self.reindex_index_values(
                record["profile_id"], embed_documents, batch_size,
            )

            with self._lock:
                stored = self.conn.execute(
                    "SELECT COUNT(*) FROM memory_embeddings WHERE profile_id = ?",
                    (record["profile_id"],),
                ).fetchone()[0]
                if stored != len(memories):
                    raise RuntimeError(
                        f"reindex validation failed: expected {len(memories)}, stored {stored}"
                    )
                self.conn.execute("BEGIN")
                try:
                    self.conn.execute(
                        "UPDATE embedding_profiles SET status='retired', updated_at=? "
                        "WHERE status='active'", (_now(),),
                    )
                    self.conn.execute(
                        "UPDATE embedding_profiles SET status='active', error=NULL, updated_at=? "
                        "WHERE profile_id=?", (_now(), record["profile_id"]),
                    )
                    self.conn.commit()
                except Exception:
                    self.conn.rollback()
                    raise
                self._active_profile_id = record["profile_id"]
            return {"profile_id": record["profile_id"], "previous_profile": old_profile,
                    "reindexed": completed,
                    "index_values_reindexed": index_result["reindexed"], "status": "active"}
        except Exception as exc:
            with self._lock:
                self.conn.execute(
                    "UPDATE embedding_profiles SET status='failed', error=?, updated_at=? "
                    "WHERE profile_id=? AND status='building'",
                    (str(exc)[:1000], _now(), record["profile_id"]),
                )
                self.conn.commit()
            self.embedding_spec = prior_spec
            self._active_profile_id = old_profile
            raise

    def activate_embedding_profile(self, profile_id: str) -> dict:
        """Atomically activate a complete retained profile for rollback."""
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM embedding_profiles WHERE profile_id=?", (profile_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"unknown embedding profile: {profile_id}")
            active_memories = self.conn.execute(
                "SELECT COUNT(*) FROM memories WHERE deleted = 0"
            ).fetchone()[0]
            stored = self.conn.execute(
                "SELECT COUNT(*) FROM memory_embeddings me JOIN memories m ON m.id=me.memory_id "
                "WHERE me.profile_id=? AND m.deleted=0",
                (profile_id,),
            ).fetchone()[0]
            if stored != active_memories:
                raise RuntimeError(
                    f"profile is incomplete: expected {active_memories}, stored {stored}"
                )
            index_completeness = self.index_embedding_completeness(profile_id)
            if not index_completeness["complete"]:
                raise RuntimeError(
                    "profile index-value embeddings are incomplete: "
                    f"expected {index_completeness['expected']}, "
                    f"stored {index_completeness['stored']}; run reindex-index-values first"
                )
            previous = self._active_profile_id
            self.conn.execute("BEGIN")
            try:
                self.conn.execute(
                    "UPDATE embedding_profiles SET status='retired', updated_at=? "
                    "WHERE status='active'", (_now(),),
                )
                self.conn.execute(
                    "UPDATE embedding_profiles SET status='active', error=NULL, updated_at=? "
                    "WHERE profile_id=?", (_now(), profile_id),
                )
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
            self._active_profile_id = profile_id
            self.embedding_spec = {
                "profile_id": row["profile_id"], "provider": row["provider"],
                "model": row["model"], "dimension": row["dimension"],
                "normalized": bool(row["normalized"]),
                "query_prefix": row["query_prefix"],
                "document_prefix": row["document_prefix"],
            }
        return {"profile_id": profile_id, "previous_profile": previous,
                "status": "active"}

    def _migrate_legacy_metadata_indexes(self) -> None:
        """Replace the old per-dimension tables with the generic index.

        The legacy tables were always rebuildable caches, so migration reads
        the authoritative active records from memories.metadata instead of
        trusting possibly stale associations.  The transaction is committed by
        _init_db only after the generic index is complete and legacy tables are
        removed.
        """
        legacy_tables = {row[0] for row in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name IN ('keywords', 'memory_keywords', 'categories', 'memory_categories')"
        ).fetchall()}
        if not legacy_tables:
            return
        self.conn.execute("DELETE FROM memory_index_values")
        self.conn.execute("DELETE FROM index_values")
        for dimension in _INDEX_DIMENSIONS:
            self._rebuild_metadata_index(dimension)
        for index_name in ("idx_memory_keywords_keyword", "idx_memory_categories_category"):
            self.conn.execute(f"DROP INDEX IF EXISTS {index_name}")
        for table in ("memory_keywords", "memory_categories", "keywords", "categories"):
            self.conn.execute(f"DROP TABLE IF EXISTS {table}")

    # -- internal maintenance state (not part of the MemoryStorage API) --

    def _get_storage_state(self, key: str, default=None):
        """Return a JSON-serializable maintenance value, or ``default``."""
        with self._lock:
            row = self.conn.execute(
                "SELECT value FROM storage_state WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, json.JSONDecodeError):
            logger.warning("invalid storage state key=%s", key)
            return default

    def _set_storage_state(self, key: str, value) -> None:
        """Atomically persist JSON-serializable maintenance state."""
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self.conn.execute(
                "INSERT INTO storage_state(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, encoded),
            )
            self.conn.commit()

    # -- derived lexical (FTS5) indexes -------------------------------------
    #
    # One derived index per retrieval primitive. A single LEXICAL_INDEX_VERSION
    # covers all of them, so adding a table bumps the version and forces a full
    # rebuild of every FTS index.

    _FTS_SCHEMAS = {
        "memory_content_fts": (
            "CREATE VIRTUAL TABLE memory_content_fts "
            "USING fts5(memory_id UNINDEXED, text, tokenize = 'unicode61')"
        ),
        "memory_anchor_fts": (
            "CREATE VIRTUAL TABLE memory_anchor_fts "
            "USING fts5(memory_id UNINDEXED, anchors, tokenize = 'unicode61')"
        ),
    }

    def _fts_tables_present(self) -> bool:
        names = tuple(self._FTS_SCHEMAS)
        placeholders = ",".join("?" * len(names))
        rows = self.conn.execute(
            f"SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ({placeholders})",
            names,
        ).fetchall()
        return {row["name"] for row in rows} == set(names)

    def _create_fts_tables(self) -> None:
        existing = {row["name"] for row in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        for name, schema in self._FTS_SCHEMAS.items():
            if name not in existing:
                self.conn.execute(schema)

    def _ensure_lexical_index(self) -> None:
        """Create/rebuild the derived FTS indexes when a table or the version is stale.

        FTS is derived data. The tables' **pre-existing** presence is checked before
        any CREATE: recreating a table first would leave it present-but-empty and
        make the version check skip the rebuild, silently emptying lexical/anchor
        results for existing memories across restarts. A missing table, a missing
        ``lexical_index_version`` record or a version mismatch all trigger a full
        rebuild from authoritative ``memories``; the rebuild and the version marker
        commit in one transaction and a failure propagates so startup fails before
        the service is published. Read-only instances skip ``_init_db``, so they
        never create or rebuild anything.
        """
        present = self._fts_tables_present()
        current = self._get_storage_state(_LEXICAL_INDEX_STATE_KEY)
        if present and current == LEXICAL_INDEX_VERSION:
            return
        self._create_fts_tables()
        self._rebuild_lexical_index()

    def _rebuild_lexical_index(self) -> None:
        """Rebuild every derived FTS index from live memories and stamp the version."""
        try:
            self.conn.execute("DELETE FROM memory_content_fts")
            self.conn.execute("DELETE FROM memory_anchor_fts")
            content_rows: list[tuple] = []
            anchor_rows: list[tuple] = []
            for row in self.conn.execute(
                    "SELECT id, text, metadata FROM memories WHERE deleted = 0"):
                content_rows.append((row["id"], row["text"]))
                try:
                    metadata = json.loads(row["metadata"] or "{}")
                except json.JSONDecodeError:
                    metadata = {}
                anchors = clean_anchors(metadata.get("anchors"))
                if anchors:
                    anchor_rows.append((row["id"], anchor_fts_text(anchors)))
            self.conn.executemany(
                "INSERT INTO memory_content_fts(memory_id, text) VALUES (?, ?)", content_rows)
            self.conn.executemany(
                "INSERT INTO memory_anchor_fts(memory_id, anchors) VALUES (?, ?)", anchor_rows)
            # The generic anchor relation index is derived from the same
            # authoritative metadata. Rebuilding it here keeps anchor search/FTS
            # and the anchor_* statistics consistent, including when an existing
            # database already carried metadata.anchors before the dimension
            # existed. Runs in the same transaction as the version stamp.
            self._rebuild_metadata_index("anchors")
            self.conn.execute(
                "INSERT INTO storage_state(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (_LEXICAL_INDEX_STATE_KEY, json.dumps(LEXICAL_INDEX_VERSION)),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _sync_content_fts(self, memory_id: str, text: str) -> None:
        """Replace one memory's content FTS row. Must run inside the caller's transaction."""
        self.conn.execute("DELETE FROM memory_content_fts WHERE memory_id = ?", (memory_id,))
        self.conn.execute(
            "INSERT INTO memory_content_fts(memory_id, text) VALUES (?, ?)",
            (memory_id, text),
        )

    def _remove_content_fts(self, memory_id: str) -> None:
        """Remove one memory's content FTS row. Must run inside the caller's transaction."""
        self.conn.execute("DELETE FROM memory_content_fts WHERE memory_id = ?", (memory_id,))

    def _sync_anchor_fts(self, memory_id: str, anchors: list[str]) -> None:
        """Replace one memory's anchor FTS row; no row when there are no anchors.

        Must run inside the caller's transaction.
        """
        self.conn.execute("DELETE FROM memory_anchor_fts WHERE memory_id = ?", (memory_id,))
        if anchors:
            self.conn.execute(
                "INSERT INTO memory_anchor_fts(memory_id, anchors) VALUES (?, ?)",
                (memory_id, anchor_fts_text(anchors)),
            )

    def _remove_anchor_fts(self, memory_id: str) -> None:
        """Remove one memory's anchor FTS row. Must run inside the caller's transaction."""
        self.conn.execute("DELETE FROM memory_anchor_fts WHERE memory_id = ?", (memory_id,))

    def _serialize(self, emb: list[float]) -> bytes:
        if self.embedding_spec is not None:
            expected = int(self.embedding_spec["dimension"])
            try:
                actual = len(emb)
            except TypeError as exc:
                raise ValueError("embedding must be a one-dimensional vector") from exc
            if actual != expected:
                raise ValueError(
                    f"embedding dimension mismatch: expected {expected}, got {actual}"
                )
        normalized = bool((self.embedding_spec or {}).get("normalized", False))
        return encode_embedding(emb, dtype=self.embedding_dtype, normalized=normalized)

    def _deserialize(self, blob: bytes) -> list[float]:
        return decode_embedding(blob)

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        blob = d.pop("_profile_embedding", None)
        if blob is None and self._active_profile_id and d.get("id"):
            profile_row = self.conn.execute(
                "SELECT embedding FROM memory_embeddings "
                "WHERE memory_id = ? AND profile_id = ?",
                (d["id"], self._active_profile_id),
            ).fetchone()
            blob = profile_row["embedding"] if profile_row else None
        if blob is None:
            blob = d.get("embedding")
        d["embedding"] = self._deserialize(blob)
        d["metadata"] = json.loads(d["metadata"])
        return d

    def _store_active_embedding(self, memory_id: str, embedding) -> None:
        if self._active_profile_id:
            self.conn.execute(
                "INSERT INTO memory_embeddings(memory_id, profile_id, embedding) "
                "VALUES (?, ?, ?) ON CONFLICT(memory_id, profile_id) "
                "DO UPDATE SET embedding=excluded.embedding",
                (memory_id, self._active_profile_id, self._serialize(embedding)),
            )
        else:
            self.conn.execute(
                "UPDATE memories SET embedding = ? WHERE id = ?",
                (self._serialize(embedding), memory_id),
            )

    # -- generic metadata indexes (SQLite-only, rebuildable from metadata) --

    @staticmethod
    def _clean_index_values(raw) -> list[str]:
        """Normalize an unbounded metadata index value/filter."""
        if not isinstance(raw, list):
            return []
        result = []
        seen = set()
        for value in raw:
            if not isinstance(value, str):
                continue
            value = value.strip().lower()
            if value and value not in seen:
                seen.add(value)
                result.append(value)
        return result

    @classmethod
    def _extract_index_values(cls, dimension: str, metadata: dict) -> list[str]:
        spec = cls._index_spec(dimension)
        raw = (metadata or {}).get(dimension)
        if dimension == "anchors":
            # Case-preserving and bounded; caller input is strictly validated
            # before it reaches storage, so this path stays lenient for
            # rebuild/import of historical metadata.
            return clean_anchors(raw)
        values = cls._clean_index_values(raw)
        max_values = spec["max_values"]
        return values[:max_values] if max_values is not None else values

    @classmethod
    def _normalize_index_filter(cls, dimension: str, values) -> list[str]:
        cls._index_spec(dimension)  # validate the dimension
        return cls._clean_index_values(values)

    @classmethod
    def _extract_keywords(cls, metadata: dict) -> list[str]:
        """Backward-compatible keyword normalization helper."""
        return cls._extract_index_values("keywords", metadata)

    @staticmethod
    def _index_spec(dimension: str) -> dict:
        try:
            return _INDEX_DIMENSIONS[dimension]
        except KeyError as exc:
            raise ValueError(f"Unknown metadata index dimension: {dimension}") from exc

    def _sync_metadata_index(self, dimension: str, memory_id: str, metadata: dict):
        """Replace one memory's associations for one metadata dimension."""
        self._index_spec(dimension)
        self.conn.execute(
            "DELETE FROM memory_index_values WHERE memory_id = ? "
            "AND index_value_id IN (SELECT id FROM index_values WHERE dimension = ?)",
            (memory_id, dimension),
        )
        for name in self._extract_index_values(dimension, metadata):
            self.conn.execute(
                "INSERT OR IGNORE INTO index_values (dimension, value) VALUES (?, ?)",
                (dimension, name),
            )
            row = self.conn.execute(
                "SELECT id FROM index_values WHERE dimension = ? AND value = ?",
                (dimension, name),
            ).fetchone()
            if row is not None:
                self.conn.execute(
                    "INSERT OR IGNORE INTO memory_index_values "
                    "(memory_id, index_value_id) VALUES (?, ?)",
                    (memory_id, row["id"]),
                )

    def _store_index_value_embeddings(self, index_embeddings: dict | None) -> None:
        """Upsert prepared vectors for the active profile inside the caller transaction."""
        if not self._active_profile_id or not index_embeddings:
            return
        for key, vector in index_embeddings.items():
            dimension, value = key
            if not self._index_spec(dimension)["requires_embedding"]:
                raise RuntimeError(
                    f"dimension '{dimension}' does not store index-value embeddings"
                )
            row = self.conn.execute(
                "SELECT id FROM index_values WHERE dimension = ? AND value = ?",
                (dimension, value),
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    f"index value was not created before storing its embedding: {dimension}/{value}"
                )
            self.conn.execute(
                "INSERT INTO index_value_embeddings(index_value_id, profile_id, embedding) "
                "VALUES (?, ?, ?) ON CONFLICT(index_value_id, profile_id) DO UPDATE SET "
                "embedding=excluded.embedding",
                (row["id"], self._active_profile_id, self._serialize(vector)),
            )

    def missing_index_value_embeddings(self, metadata: dict | None) -> list[tuple[str, str]]:
        """Return metadata index values missing a vector in the active profile."""
        if not self._active_profile_id:
            return []
        missing = []
        with self._lock:
            for dimension in _EMBEDDING_DIMENSIONS:
                for value in self._extract_index_values(dimension, metadata or {}):
                    row = self.conn.execute(
                        "SELECT ive.index_value_id FROM index_values iv "
                        "LEFT JOIN index_value_embeddings ive ON ive.index_value_id = iv.id "
                        "AND ive.profile_id = ? WHERE iv.dimension = ? AND iv.value = ?",
                        (self._active_profile_id, dimension, value),
                    ).fetchone()
                    if row is None or row["index_value_id"] is None:
                        missing.append((dimension, value))
        return missing

    def missing_queryable_index_value_embeddings(self) -> list[tuple[str, str]]:
        """Return all live index values missing a vector in the active profile."""
        if not self._active_profile_id:
            return []
        with self._lock:
            placeholders = ",".join("?" * len(_EMBEDDING_DIMENSIONS))
            rows = self.conn.execute(
                "SELECT iv.dimension, iv.value FROM index_values iv "
                "JOIN memory_index_values link ON link.index_value_id = iv.id "
                "JOIN memories m ON m.id = link.memory_id "
                "LEFT JOIN index_value_embeddings ive ON ive.index_value_id = iv.id "
                "AND ive.profile_id = ? WHERE m.deleted = 0 AND ive.index_value_id IS NULL "
                f"AND iv.dimension IN ({placeholders}) "
                "GROUP BY iv.id, iv.dimension, iv.value ORDER BY iv.dimension, iv.value",
                (self._active_profile_id, *_EMBEDDING_DIMENSIONS),
            ).fetchall()
        return [(row["dimension"], row["value"]) for row in rows]

    def backfill_missing_index_value_embeddings(self, embed_documents) -> int:
        missing = self.missing_queryable_index_value_embeddings()
        if not missing:
            return 0
        vectors = embed_documents([value for _dimension, value in missing])
        if len(vectors) != len(missing):
            raise ValueError(
                f"provider returned {len(vectors)} index vectors for {len(missing)} values"
            )
        with self._lock:
            try:
                self._store_index_value_embeddings(dict(zip(missing, vectors)))
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        return len(missing)

    def keyword_counts(self, namespace=None):
        """All in-use keywords and distinct live memory counts in scope."""
        sql = ("SELECT iv.value, COUNT(DISTINCT m.id) AS n FROM index_values iv "
               "JOIN memory_index_values link ON link.index_value_id = iv.id "
               "JOIN memories m ON m.id = link.memory_id "
               "WHERE iv.dimension = 'keywords' AND m.deleted = 0")
        params = []
        if namespace is not None:
            sql += " AND m.namespace = ?"
            params.append(namespace)
        sql += " GROUP BY iv.value"
        with self._lock:
            return {row["value"]: row["n"] for row in self.conn.execute(sql, params)}

    def exact_index_values(self, dimension: str, values: list[str],
                           namespace: str | None = None) -> set[str]:
        """Return supplied values associated with at least one live memory in scope."""
        self._index_spec(dimension)
        if not values:
            return set()
        placeholders = ",".join(["?"] * len(values))
        sql = (
            "SELECT DISTINCT iv.value FROM index_values iv "
            "JOIN memory_index_values link ON link.index_value_id = iv.id "
            "JOIN memories m ON m.id = link.memory_id "
            f"WHERE iv.dimension = ? AND iv.value IN ({placeholders}) AND m.deleted = 0"
        )
        params = [dimension, *values]
        if namespace:
            sql += " AND m.namespace = ?"
            params.append(namespace)
        with self._lock:
            return {row["value"] for row in self.conn.execute(sql, params).fetchall()}

    def semantic_index_values(self, dimension: str, query_embedding: list[float],
                              namespace: str | None = None) -> list[dict]:
        """Score all live, in-scope index values in one dimension."""
        if not self._index_spec(dimension)["requires_embedding"]:
            raise RuntimeError(
                f"semantic index resolution is not supported for dimension '{dimension}'")
        if not self._active_profile_id:
            raise RuntimeError("semantic index filtering requires an active embedding profile")
        sql = (
            "SELECT DISTINCT iv.id, iv.value, ive.embedding FROM index_values iv "
            "JOIN index_value_embeddings ive ON ive.index_value_id = iv.id "
            "AND ive.profile_id = ? "
            "JOIN memory_index_values link ON link.index_value_id = iv.id "
            "JOIN memories m ON m.id = link.memory_id "
            "WHERE iv.dimension = ? AND m.deleted = 0"
        )
        params = [self._active_profile_id, dimension]
        if namespace:
            sql += " AND m.namespace = ?"
            params.append(namespace)
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        scored = []
        for row in rows:
            vector = self._deserialize(row["embedding"])
            if len(vector) != len(query_embedding):
                raise ValueError(
                    f"index value embedding dimension mismatch for {dimension}/{row['value']}: "
                    f"query={len(query_embedding)}, stored={len(vector)}"
                )
            scored.append({"id": row["id"], "value": row["value"],
                           "score": round(_cosine_similarity(query_embedding, vector), 6)})
        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored

    def _sync_metadata_indexes(self, memory_id: str, metadata: dict,
                               changed_dimensions=None):
        for dimension in _INDEX_DIMENSIONS:
            if changed_dimensions is None or dimension in changed_dimensions:
                self._sync_metadata_index(dimension, memory_id, metadata)

    def _remove_metadata_indexes(self, memory_id: str):
        self.conn.execute(
            "DELETE FROM memory_index_values WHERE memory_id = ?", (memory_id,)
        )

    def _rebuild_metadata_index(self, dimension: str) -> int:
        """Clear and rebuild one dimension from active memories.metadata."""
        self._index_spec(dimension)
        self.conn.execute(
            "DELETE FROM memory_index_values WHERE index_value_id IN "
            "(SELECT id FROM index_values WHERE dimension = ?)", (dimension,)
        )
        # Keep stable value IDs (and their profile vectors). Values with no live
        # associations are harmless: counts and semantic resolution both join
        # through active memories, so stale values remain invisible.
        rows = self.conn.execute(
            "SELECT id, metadata FROM memories WHERE deleted = 0"
        ).fetchall()
        associations = 0
        for row in rows:
            try:
                metadata = json.loads(row["metadata"] or "{}")
            except json.JSONDecodeError:
                continue
            names = self._extract_index_values(dimension, metadata)
            self._sync_metadata_index(dimension, row["id"], metadata)
            associations += len(names)
        return associations

    def _metadata_index_counts(self, dimension: str) -> dict[str, int]:
        self._index_spec(dimension)
        rows = self.conn.execute(
            "SELECT v.value, COUNT(DISTINCT link.memory_id) AS cnt "
            "FROM index_values v "
            "JOIN memory_index_values link ON link.index_value_id = v.id "
            "JOIN memories m ON m.id = link.memory_id "
            "WHERE m.deleted = 0 AND v.dimension = ? "
            "GROUP BY v.value ORDER BY cnt DESC, v.value ASC",
            (dimension,),
        ).fetchall()
        return {r["value"]: r["cnt"] for r in rows}

    def _append_index_filter(self, clauses: list[str], params: list,
                             dimension: str, raw_values) -> None:
        """Add an OR-within-dimension SQL filter; dimensions compose via AND."""
        values = self._normalize_index_filter(dimension, raw_values)
        if not values:
            return
        self._index_spec(dimension)
        placeholders = ",".join(["?"] * len(values))
        clauses.append(
            "id IN (SELECT link.memory_id FROM memory_index_values link "
            "JOIN index_values iv ON iv.id = link.index_value_id "
            f"WHERE iv.dimension = ? AND iv.value IN ({placeholders}))"
        )
        params.append(dimension)
        params.extend(values)

    # Named wrappers remain as stable internal/public integration points.

    def _sync_keyword_index(self, memory_id: str, metadata: dict):
        self._sync_metadata_index("keywords", memory_id, metadata)

    def _remove_keyword_associations(self, memory_id: str):
        self._sync_metadata_index("keywords", memory_id, {})

    def _rebuild_keyword_index(self) -> int:
        return self._rebuild_metadata_index("keywords")

    def rebuild_keyword_index(self) -> dict:
        with self._lock:
            associations = self._rebuild_keyword_index()
            self.conn.commit()
        logger.info("keyword index rebuilt: %d associations", associations)
        return {"keyword_index_rebuilt": associations}

    def _keyword_counts(self) -> dict[str, int]:
        return self._metadata_index_counts("keywords")

    def get_keyword_vocabulary(self, namespace: str | None = None) -> list[str]:
        """Return the full keyword vocabulary (names only), ordered by frequency.

        Used by the deepseek migration to seed the candidate list so new
        records converge toward existing word forms. The index is rebuildable
        from memories.metadata, so this is safe to call at any time.
        """
        if namespace:
            rows = self.conn.execute(
                "SELECT v.value FROM index_values v "
                "JOIN memory_index_values link ON link.index_value_id = v.id "
                "JOIN memories m ON m.id = link.memory_id "
                "WHERE v.dimension = 'keywords' AND m.deleted = 0 AND m.namespace = ? "
                "GROUP BY v.value "
                "ORDER BY COUNT(DISTINCT link.memory_id) DESC, v.value ASC",
                (namespace,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT v.value FROM index_values v "
                "JOIN memory_index_values link ON link.index_value_id = v.id "
                "JOIN memories m ON m.id = link.memory_id "
                "WHERE v.dimension = 'keywords' AND m.deleted = 0 "
                "GROUP BY v.value "
                "ORDER BY COUNT(DISTINCT link.memory_id) DESC, v.value ASC"
            ).fetchall()
        return [r["value"] for r in rows]

    @classmethod
    def _extract_categories(cls, metadata: dict) -> list[str]:
        return cls._extract_index_values("categories", metadata)

    def _sync_category_index(self, memory_id: str, metadata: dict):
        self._sync_metadata_index("categories", memory_id, metadata)

    def _remove_category_associations(self, memory_id: str):
        self._sync_metadata_index("categories", memory_id, {})

    def _rebuild_category_index(self) -> int:
        return self._rebuild_metadata_index("categories")

    def rebuild_category_index(self) -> dict:
        """Public: clear and rebuild the category index from all active memories."""
        with self._lock:
            associations = self._rebuild_category_index()
            self.conn.commit()
        logger.info("category index rebuilt: %d associations", associations)
        return {"category_index_rebuilt": associations}

    def _category_counts(self) -> dict[str, int]:
        return self._metadata_index_counts("categories")

    # -- internal sync protocol --

    def _add_with_id(self, memory_id, text, embedding, metadata, namespace,
                     created_at, updated_at, summary=None):
        """Insert/replace with explicit ID. Internal use only (sync/recovery).
        Sets synced=1 because data is already in (or being restored from) secondary.
        Uses INSERT OR REPLACE so that soft-deleted records can be restored.
        """
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO memories "
                "(id, text, embedding, metadata, namespace, created_at, updated_at, deleted, synced, version, summary) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, 0, ?)",
                (memory_id, text, None if self._active_profile_id else self._serialize(embedding),
                 json.dumps(metadata or {}), namespace, created_at, updated_at, summary),
            )
            if self._active_profile_id:
                self._store_active_embedding(memory_id, embedding)
            self._sync_metadata_indexes(memory_id, metadata or {})
            self._sync_content_fts(memory_id, text)
            self._sync_anchor_fts(memory_id, self._extract_index_values("anchors", metadata or {}))
            self.conn.commit()

    # -- internal sync protocol (outbox pattern) --

    def get_unsynced_records(self, limit=500):
        """Return records with synced=0 (including deleted), ordered by updated_at.
        Internal use only (DualStorage outbox sync job).
        """
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM memories WHERE synced = 0 ORDER BY updated_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def mark_synced_batch(self, memory_ids):
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

    def mark_synced_versions(self, entries):
        """Acknowledge only outbox versions that have not changed since read.

        A concurrent write increments ``version`` and remains pending rather
        than being accidentally acknowledged by an older replica payload.
        """
        if not entries:
            return 0
        with self._lock:
            cursor = self.conn.executemany(
                "UPDATE memories SET synced = 1 "
                "WHERE id = ? AND version = ? AND synced = 0",
                entries,
            )
            self.conn.commit()
        return cursor.rowcount

    def count_unsynced(self) -> int:
        """Count active records awaiting sync. Internal use only (stats)."""
        with self._lock:
            return self.conn.execute(
                "SELECT COUNT(*) FROM memories WHERE synced = 0 AND deleted = 0"
            ).fetchone()[0]

    # -- public API --

    def add(self, text, embedding, metadata=None, namespace=DEFAULT_NAMESPACE, summary=None,
            index_embeddings=None):
        """Insert a memory; dedup on (namespace, text). Returns {"id", "created"}."""
        with self._lock:
            try:
                existing = self.conn.execute(
                    "SELECT id FROM memories WHERE namespace = ? AND text = ? AND deleted = 0 LIMIT 1",
                    (namespace, text),
                ).fetchone()
                if existing:
                    return {"id": existing[0], "created": False}
                eid = _new_id()
                now = _now()
                summary = summary_for_add(text, summary)
                self.conn.execute(
                    "INSERT INTO memories (id, text, embedding, metadata, namespace, created_at, updated_at, synced, version, summary) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?)",
                    (eid, text, None if self._active_profile_id else self._serialize(embedding),
                     json.dumps(metadata or {}), namespace, now, now, summary),
                )
                if self._active_profile_id:
                    self._store_active_embedding(eid, embedding)
                self._sync_metadata_indexes(eid, metadata or {})
                self._store_index_value_embeddings(index_embeddings)
                self._sync_content_fts(eid, text)
                self._sync_anchor_fts(eid, self._extract_index_values("anchors", metadata or {}))
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        return {"id": eid, "created": True}

    def search(self, query_embedding, k=5, threshold=0.0, namespace=None,
               categories=None, keywords=None):
        with self._lock:
            clauses = ["deleted = 0"]
            params = []
            if namespace:
                clauses.append("namespace = ?")
                params.append(namespace)
            self._append_index_filter(clauses, params, "keywords", keywords)
            self._append_index_filter(clauses, params, "categories", categories)
            if self._active_profile_id:
                rows = self.conn.execute(
                    "SELECT memories.*, me.embedding AS _profile_embedding "
                    "FROM memories JOIN memory_embeddings me ON me.memory_id = memories.id "
                    f"AND me.profile_id = ? WHERE {' AND '.join(clauses)}",
                    [self._active_profile_id, *params],
                ).fetchall()
            else:
                rows = self.conn.execute(
                    f"SELECT * FROM memories WHERE {' AND '.join(clauses)}", params
                ).fetchall()
        scored = []
        for row in rows:
            emb = self._deserialize(
                row["_profile_embedding"] if "_profile_embedding" in row.keys()
                else row["embedding"]
            )
            if len(emb) != len(query_embedding):
                raise ValueError(
                    f"embedding dimension mismatch for memory {row['id']}: "
                    f"query={len(query_embedding)}, stored={len(emb)}"
                )
            score = _cosine_similarity(query_embedding, emb)
            if score >= threshold:
                d = dict(row)
                d.pop("_profile_embedding", None)
                d["embedding"] = emb
                d["metadata"] = json.loads(d["metadata"])
                scored.append((score, d))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{**d, "score": round(s, 6)} for s, d in scored[:k]]

    def _search_fts(self, table: str, query, k, namespace, match_type: str):
        """Shared FTS5/BM25 search over one derived index.

        The public score is ``-raw_bm25`` so a higher score is better while the
        SQL ordering follows FTS5's ascending bm25. ``match_info`` echoes the raw
        value and marks the retrieval type; scores are not comparable with
        semantic (cosine) scores.
        """
        if table not in self._FTS_SCHEMAS:
            raise ValueError(f"unknown FTS table: {table}")
        match = build_match(query)
        if match is None:
            return []
        k = normalize_k(k)
        clauses = ["m.deleted = 0"]
        params = [match]
        if namespace:
            clauses.append("m.namespace = ?")
            params.append(namespace)
        params.append(k)
        with self._lock:
            rows = self.conn.execute(
                f"SELECT m.*, bm25({table}) AS _bm25_raw FROM {table} "
                f"JOIN memories m ON m.id = {table}.memory_id "
                f"WHERE {table} MATCH ? AND {' AND '.join(clauses)} "
                f"ORDER BY bm25({table}) LIMIT ?",
                params,
            ).fetchall()
        results = []
        for row in rows:
            raw = float(row["_bm25_raw"])
            record = self._row_to_dict(row)
            record.pop("_bm25_raw", None)
            record["score"] = round(-raw, 6)
            record["match_info"] = {"type": match_type, "score_type": "fts5_bm25",
                                    "raw_bm25": round(raw, 6)}
            results.append(record)
        return results

    def search_lexical(self, query, k=DEFAULT_K, namespace=None):
        """Content-only FTS5/BM25 search."""
        return self._search_fts("memory_content_fts", query, k, namespace, "lexical")

    def search_anchor(self, query, k=DEFAULT_K, namespace=None):
        """Anchor-only FTS5/BM25 search (identity signals, not memory content)."""
        return self._search_fts("memory_anchor_fts", query, k, namespace, "anchor")

    def list_memories(self, namespace=None, limit=100, offset=0, sort_by="created_at",
                      updated_after=None, updated_before=None, keywords=None,
                      categories=None):
        sort_col = sort_by if sort_by in ("created_at", "updated_at") else "created_at"
        clauses = ["deleted = 0"]
        params = []
        if namespace:
            clauses.append("namespace = ?")
            params.append(namespace)
        if updated_after:
            clauses.append("updated_at > ?")
            params.append(updated_after)
        if updated_before:
            clauses.append("updated_at < ?")
            params.append(updated_before)
        self._append_index_filter(clauses, params, "keywords", keywords)
        self._append_index_filter(clauses, params, "categories", categories)
        params.extend([limit, offset])
        with self._lock:
            rows = self.conn.execute(
                f"SELECT * FROM memories WHERE {' AND '.join(clauses)} "
                f"ORDER BY {sort_col} DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_namespaces(self):
        """Distinct namespaces of live records; index-backed and cheap."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT DISTINCT namespace FROM memories WHERE deleted = 0 "
                "ORDER BY namespace"
            ).fetchall()
        return [r["namespace"] for r in rows]

    def live_memory_ids(self):
        """Ids of live records only; never loads text, metadata or vectors."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT id FROM memories WHERE deleted = 0"
            ).fetchall()
        return [r["id"] for r in rows]

    def get(self, memory_id):
        row = self.conn.execute(
            "SELECT * FROM memories WHERE id = ? AND deleted = 0", (memory_id,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def update(self, memory_id, text=None, embedding=None, metadata=None, namespace=None, summary=None,
               index_embeddings=None):
        with self._lock:
            existing = self.get(memory_id)
            if not existing:
                return False
            try:
                now = _now()
                new_text = text
                text = text if text is not None else existing["text"]
                emb = embedding if embedding is not None else existing["embedding"]
                meta = {**existing["metadata"], **(metadata or {})}
                # Anchors follow design §9: an explicit list replaces ([] clears);
                # a materially changed body without anchors clears stale ones; an
                # unchanged or whitespace-equivalent body preserves them.
                explicit_anchors = bool(metadata) and "anchors" in metadata
                anchors_changed = explicit_anchors
                if not explicit_anchors and "anchors" in meta and _materially_changes_text(
                        existing["text"], new_text):
                    meta.pop("anchors", None)
                    anchors_changed = True
                ns = namespace if namespace is not None else existing["namespace"]
                summ = summary_for_update(
                    existing["text"], existing.get("summary"),
                    new_text, summary,
                )
                self.conn.execute(
                    "UPDATE memories SET text = ?, embedding = ?, metadata = ?, "
                    "namespace = ?, updated_at = ?, synced = 0, version = version + 1, summary = ? WHERE id = ?",
                    (text, None if self._active_profile_id else self._serialize(emb),
                     json.dumps(meta), ns, now, summ, memory_id),
                )
                if embedding is not None and self._active_profile_id:
                    self._store_active_embedding(memory_id, embedding)
                # Only dimensions explicitly present in metadata are replaced, plus
                # anchors when this update cleared them.
                changed_dimensions = set(metadata or {})
                if anchors_changed:
                    changed_dimensions.add("anchors")
                self._sync_metadata_indexes(memory_id, meta, changed_dimensions=changed_dimensions)
                self._store_index_value_embeddings(index_embeddings)
                if new_text is not None:
                    self._sync_content_fts(memory_id, text)
                self._sync_anchor_fts(memory_id, self._extract_index_values("anchors", meta))
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        return True

    def update_metadata(self, memory_id, metadata, index_embeddings=None):
        """Merge metadata and re-sync the keyword index — updated_at stays
        unchanged. Used by the keyword-extraction migration so backfilled
        keywords do not perturb time-based list ordering.

        ``metadata`` keys overwrite existing ones (shallow merge). When the
        merged metadata carries a ``keywords`` key, the index is rebuilt for
        this memory; otherwise the keyword index is left untouched.
        """
        with self._lock:
            existing = self.get(memory_id)
            if not existing:
                return False
            merged = {**existing["metadata"], **(metadata or {})}
            missing = set(self.missing_index_value_embeddings(metadata))
            prepared = set((index_embeddings or {}).keys())
            unprepared = missing - prepared
            if unprepared:
                values = ", ".join(
                    f"{dimension}/{value}" for dimension, value in sorted(unprepared)
                )
                raise RuntimeError(
                    "metadata update would create index values without embeddings: " + values
                )
            try:
                self.conn.execute(
                    "UPDATE memories SET metadata = ?, synced = 0, version = version + 1 "
                    "WHERE id = ? AND deleted = 0",
                    (json.dumps(merged), memory_id),
                )
                self._sync_metadata_indexes(memory_id, merged, changed_dimensions=metadata or {})
                self._store_index_value_embeddings(index_embeddings)
                self._sync_anchor_fts(memory_id, self._extract_index_values("anchors", merged))
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        return True

    def update_anchors_batch(self, entries):
        """Atomically replace ``metadata.anchors`` for a whole batch.

        ``entries`` is an iterable of ``(memory_id, anchors)``. Every row is
        updated inside a single SQLite transaction: metadata, the ``anchors``
        relation index and Anchor FTS are synced, outbox versions are bumped and
        ``updated_at`` is preserved. Any error rolls the entire batch back, so a
        partially applied batch is impossible. Returns the number of rows
        updated.
        """
        prepared = [(memory_id, list(anchors or [])) for memory_id, anchors in entries]
        with self._lock:
            try:
                updated = 0
                for memory_id, anchors in prepared:
                    existing = self.get(memory_id)
                    if not existing:
                        raise RuntimeError(f"memory not found or deleted: {memory_id}")
                    merged = {**existing.get("metadata", {}), "anchors": anchors}
                    cursor = self.conn.execute(
                        "UPDATE memories SET metadata = ?, synced = 0, version = version + 1 "
                        "WHERE id = ? AND deleted = 0",
                        (json.dumps(merged), memory_id),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError(
                            f"batch update matched {cursor.rowcount} rows for {memory_id}")
                    self._sync_metadata_index("anchors", memory_id, merged)
                    self._sync_anchor_fts(
                        memory_id, self._extract_index_values("anchors", merged))
                    updated += 1
                self.conn.commit()
                return updated
            except Exception:
                self.conn.rollback()
                raise

    def get_details(self, memory_ids):
        """Fetch full records by ID list. Partial success: missing IDs are reported."""
        found, missing = [], []
        if not memory_ids:
            return {"found": found, "missing": missing}
        with self._lock:
            placeholders = ",".join(["?"] * len(memory_ids))
            rows = self.conn.execute(
                f"SELECT * FROM memories WHERE deleted = 0 AND id IN ({placeholders})",
                memory_ids,
            ).fetchall()
            by_id = {r["id"]: self._row_to_dict(r) for r in rows}
        for mid in memory_ids:
            if mid in by_id:
                found.append(by_id[mid])
            else:
                missing.append(mid)
        return {"found": found, "missing": missing}

    def delete(self, memory_id):
        """Soft-delete: mark as deleted and unsynced."""
        with self._lock:
            c = self.conn.execute(
                "UPDATE memories SET deleted = 1, synced = 0, version = version + 1, "
                "updated_at = ? WHERE id = ? AND deleted = 0",
                (_now(), memory_id),
            )
            if c.rowcount > 0:
                self._remove_metadata_indexes(memory_id)
                self._remove_content_fts(memory_id)
                self._remove_anchor_fts(memory_id)
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
                    "UPDATE memories SET deleted = 1, synced = 0, version = version + 1, "
                    "updated_at = ? WHERE namespace = ? AND deleted = 0",
                    (_now(), namespace),
                )
                self.conn.execute(
                    "DELETE FROM memory_index_values WHERE memory_id IN "
                    "(SELECT id FROM memories WHERE namespace = ?)",
                    (namespace,),
                )
                self.conn.execute(
                    "DELETE FROM memory_content_fts WHERE memory_id IN "
                    "(SELECT id FROM memories WHERE namespace = ?)",
                    (namespace,),
                )
                self.conn.execute(
                    "DELETE FROM memory_anchor_fts WHERE memory_id IN "
                    "(SELECT id FROM memories WHERE namespace = ?)",
                    (namespace,),
                )
            else:
                n = self.conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE deleted = 0"
                ).fetchone()[0]
                self.conn.execute(
                    "UPDATE memories SET deleted = 1, synced = 0, version = version + 1, "
                    "updated_at = ? WHERE deleted = 0",
                    (_now(),),
                )
                self.conn.execute("DELETE FROM memory_index_values")
                self.conn.execute("DELETE FROM memory_content_fts")
                self.conn.execute("DELETE FROM memory_anchor_fts")
            self.conn.commit()
        return n

    def export_records(self, namespace=None):
        """Return a consistent vector-free snapshot in one SQLite statement."""
        sql = ("SELECT id, text, metadata, namespace, created_at, updated_at, summary "
               "FROM memories WHERE deleted = 0")
        params = []
        if namespace is not None:
            sql += " AND namespace = ?"
            params.append(namespace)
        sql += " ORDER BY created_at, id"
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [{
            "id": row["id"], "text": row["text"],
            "metadata": json.loads(row["metadata"]), "namespace": row["namespace"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "summary": row["summary"],
        } for row in rows]

    def import_records(self, records, memory_embeddings, index_embeddings,
                       expected_profile):
        """Atomically merge prepared JSONL records into the active profile."""
        with self._lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                if self._active_profile_id != expected_profile:
                    raise RuntimeError(
                        "active embedding profile changed during import: "
                        f"expected={expected_profile}, active={self._active_profile_id}"
                    )
                existing_ids = {row[0] for row in self.conn.execute(
                    # Soft-deleted rows still occupy the primary key. Imports
                    # must rename collisions with both live and deleted rows.
                    "SELECT id FROM memories"
                ).fetchall()}
                existing_pairs = {(row[0], row[1]) for row in self.conn.execute(
                    "SELECT namespace, text FROM memories WHERE deleted = 0"
                ).fetchall()}
                imported_ids = []
                skipped = renamed = 0
                used_index_keys = set()
                for memory in records:
                    pair = (memory["namespace"], memory["text"])
                    if pair in existing_pairs:
                        skipped += 1
                        continue
                    source_id = memory["id"]
                    memory_id = source_id
                    collided = memory_id in existing_ids
                    while memory_id in existing_ids:
                        memory_id = _new_id()
                    renamed += int(collided)
                    existing_ids.add(memory_id)
                    existing_pairs.add(pair)
                    self.conn.execute(
                        "INSERT INTO memories "
                        "(id, text, embedding, metadata, namespace, created_at, updated_at, "
                        "synced, version, summary) VALUES (?, ?, NULL, ?, ?, ?, ?, 0, 0, ?)",
                        (memory_id, memory["text"], json.dumps(memory.get("metadata") or {}),
                         memory["namespace"], memory["created_at"], memory["updated_at"],
                         memory.get("summary")),
                    )
                    self._store_active_embedding(memory_id, memory_embeddings[source_id])
                    self._sync_metadata_indexes(memory_id, memory.get("metadata") or {})
                    self._sync_content_fts(memory_id, memory["text"])
                    self._sync_anchor_fts(
                        memory_id,
                        self._extract_index_values("anchors", memory.get("metadata") or {}))
                    for dimension in _INDEX_DIMENSIONS:
                        used_index_keys.update(
                            (dimension, value) for value in self._extract_index_values(
                                dimension, memory.get("metadata") or {})
                        )
                    imported_ids.append(memory_id)
                self._store_index_value_embeddings({
                    key: vector for key, vector in index_embeddings.items()
                    if key in used_index_keys
                })
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        return {"imported": len(imported_ids), "skipped": skipped,
                "renamed": renamed, "imported_ids": imported_ids}

    def get_stats(self):
        with self._lock:
            total = self.conn.execute(
                "SELECT COUNT(*) FROM memories WHERE deleted = 0"
            ).fetchone()[0]
            ns_rows = self.conn.execute(
                "SELECT namespace, COUNT(*) as cnt FROM memories WHERE deleted = 0 GROUP BY namespace"
            ).fetchall()
            keyword_counts = self._keyword_counts()
            category_counts = self._category_counts()
            anchor_counts = self._metadata_index_counts("anchors")
            memories_with_anchors = self.conn.execute(
                "SELECT COUNT(DISTINCT link.memory_id) FROM memory_index_values link "
                "JOIN index_values iv ON iv.id = link.index_value_id "
                "JOIN memories m ON m.id = link.memory_id "
                "WHERE iv.dimension = 'anchors' AND m.deleted = 0"
            ).fetchone()[0]
            if self._active_profile_id:
                embedding_rows = self.conn.execute(
                    "SELECT me.embedding FROM memory_embeddings me "
                    "JOIN memories m ON m.id = me.memory_id "
                    "WHERE m.deleted = 0 AND me.profile_id = ?",
                    (self._active_profile_id,),
                ).fetchall()
            else:
                embedding_rows = self.conn.execute(
                    "SELECT embedding FROM memories "
                    "WHERE deleted = 0 AND embedding IS NOT NULL"
                ).fetchall()
            profile_rows = self.conn.execute(
                "SELECT ep.profile_id, ep.provider, ep.model, ep.dimension, ep.normalized, "
                "ep.codec, ep.dtype, ep.status, "
                "(SELECT COUNT(*) FROM memory_embeddings me "
                " JOIN memories m ON m.id = me.memory_id "
                " WHERE me.profile_id = ep.profile_id AND m.deleted = 0) AS vector_count, "
                "(SELECT COUNT(DISTINCT ive.index_value_id) FROM index_value_embeddings ive "
                " JOIN memory_index_values link ON link.index_value_id = ive.index_value_id "
                " JOIN memories im ON im.id = link.memory_id "
                " WHERE ive.profile_id = ep.profile_id AND im.deleted = 0) "
                "AS index_vector_count "
                "FROM embedding_profiles ep ORDER BY ep.created_at"
            ).fetchall()
            embedding_formats = {}
            embedding_bytes = 0
            embedding_dimensions = set()
            for row in embedding_rows:
                info = inspect_embedding_blob(row["embedding"])
                key = f"{info.format}:{info.dtype}"
                embedding_formats[key] = embedding_formats.get(key, 0) + 1
                embedding_bytes += info.byte_length
                if info.dimension:
                    embedding_dimensions.add(info.dimension)
        return {
            "total": total,
            "namespaces": [r["namespace"] for r in ns_rows],
            "namespace_counts": {r["namespace"]: r["cnt"] for r in ns_rows},
            "keyword_counts": keyword_counts,
            "category_counts": category_counts,
            "anchor_counts": anchor_counts,
            "anchor_total": len(anchor_counts),
            "memories_with_anchors": memories_with_anchors,
            "lexical_index_version": LEXICAL_INDEX_VERSION,
            "embedding_storage": {
                "count": len(embedding_rows),
                "bytes": embedding_bytes,
                "formats": embedding_formats,
                "dimensions": sorted(embedding_dimensions),
                "write_dtype": self.embedding_dtype,
            },
            "embedding_profile": {
                "active": self._active_profile_id,
                "profiles": [dict(row) for row in profile_rows],
                "index_values": self.index_embedding_completeness(),
            },
            "backend": "sqlite",
            "path": self.path,
        }

    def active_embedding_profile(self):
        return self._active_profile_id

    def storage_paths(self) -> dict:
        return {"backend": "sqlite"}
