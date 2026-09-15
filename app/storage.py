import secrets
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np

logger = logging.getLogger("storage")

DEFAULT_NAMESPACE = "default"


def resolve_replica_path(primary_path: str, configured: str | None = None,
                         legacy: str | None = None) -> str:
    """Resolve the NDJSON path, preferring the new env name over its legacy alias."""
    if configured:
        if legacy:
            logger.warning(
                "MEMORY_STORAGE_PATH_DUAL is deprecated and ignored because "
                "MEMORY_REPLICA_PATH is set"
            )
        return configured
    if legacy:
        logger.warning("MEMORY_STORAGE_PATH_DUAL is deprecated; use MEMORY_REPLICA_PATH")
        return legacy
    if primary_path == ":memory:":
        return "data/memories.jsonl"
    return os.path.splitext(primary_path)[0] + ".jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    """Generate a short memory ID: 16 hex chars (64 bits of randomness).

    UUID v4 was 36 chars and too long for LLM-facing APIs. 64-bit random hex
    keeps collision probability negligible (~1e-10 at a million records) while
    staying short. Callers that persist IDs should check for uniqueness and
    retry on collision (see migrate script).
    """
    return secrets.token_hex(8)


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
            namespace: str = DEFAULT_NAMESPACE, summary: Optional[str] = None,
            index_embeddings: Optional[dict] = None) -> dict[str, Any]:
        """Insert a memory. Returns {"id": ..., "created": bool}.

        Dedup rule (content + namespace): if an active (non-deleted) record
        with the same ``text`` already exists in the same ``namespace``,
        no insert happens and the existing record's id is returned with
        ``created=False``. Otherwise a new id is generated and returned with
        ``created=True``.
        """
        raise NotImplementedError

    @abstractmethod
    def search(self, query_embedding: list[float], k: int = 5, threshold: float = 0.0,
               namespace: Optional[str] = None,
               categories: Optional[list[str]] = None,
               keywords: Optional[list[str]] = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    def list_memories(self, namespace: Optional[str] = None, limit: int = 100,
                      offset: int = 0, sort_by: str = "created_at",
                      updated_after: Optional[str] = None,
                      updated_before: Optional[str] = None,
                      keywords: Optional[list[str]] = None,
                      categories: Optional[list[str]] = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    def get(self, memory_id: str) -> Optional[dict[str, Any]]: ...

    @abstractmethod
    def update(self, memory_id: str, text: Optional[str] = None,
               embedding: Optional[list[float]] = None,
               metadata: Optional[dict] = None,
               namespace: Optional[str] = None,
               summary: Optional[str] = None,
               index_embeddings: Optional[dict] = None) -> bool: ...

    @abstractmethod
    def get_details(self, memory_ids: list[str]) -> dict[str, Any]:
        """Fetch full records by ID list. Returns {"found": [...], "missing": [...]}."""
        ...

    @abstractmethod
    def delete(self, memory_id: str) -> bool: ...

    @abstractmethod
    def clear(self, namespace: Optional[str] = None) -> int: ...

    @abstractmethod
    def export_records(self, namespace: Optional[str] = None) -> list[dict[str, Any]]:
        """Return one consistent, vector-free snapshot of live records."""
        raise NotImplementedError

    @abstractmethod
    def import_records(self, records, memory_embeddings, index_embeddings,
                       expected_profile: str) -> dict[str, Any]:
        """Atomically merge prepared records and target-profile vectors."""
        raise NotImplementedError

    @abstractmethod
    def get_stats(self) -> dict[str, Any]: ...


class ReplicaStorage(ABC):
    """Narrow contract for a vector-free disaster-recovery replica."""

    @abstractmethod
    def upsert(self, record: dict) -> None: ...

    @abstractmethod
    def append_tombstone(self, memory_id: str, updated_at: str) -> None: ...

    @abstractmethod
    def replace_all(self, memories: list[dict]) -> None: ...

    @abstractmethod
    def list_records(self, limit: int = 100, offset: int = 0) -> list[dict]: ...

    @abstractmethod
    def compaction_metrics(self) -> dict[str, Any]: ...

    @abstractmethod
    def compact(self) -> dict[str, Any]: ...

    @abstractmethod
    def get_stats(self) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_storage(backend: str = "sqlite", **kwargs) -> MemoryStorage:
    if backend == "sqlite":
        from .storage_sqlite import SQLiteStorage
        return SQLiteStorage(
            path=kwargs.get("path", "data/memories.db"),
            embedding_dtype=kwargs.get("embedding_dtype", "float16"),
            embedding_spec=kwargs.get("embedding_spec"),
            read_only=bool(kwargs.get("read_only", False)),
            writer_lock=not bool(kwargs.get("read_only", False)),
        )
    if backend == "dual":
        from .storage_ndjson import NDJSONReplica
        from .storage_sqlite import SQLiteStorage
        from .storage_dual import DualStorage
        primary_path = kwargs.get("primary_path", kwargs.get("path", "data/memories.db"))
        default_replica = resolve_replica_path(primary_path)
        secondary_path = kwargs.get("secondary_path", default_replica)
        sync_interval = int(kwargs.get("sync_interval", 300))
        if not secondary_path.lower().endswith((".jsonl", ".ndjson")):
            raise ValueError(
                "dual replica path must end with .jsonl or .ndjson; JSON replicas were removed"
            )
        if primary_path != ":memory:" and os.path.realpath(primary_path) == os.path.realpath(secondary_path):
            raise ValueError("dual primary and replica paths must be different")
        primary = SQLiteStorage(
                path=primary_path,
                embedding_dtype=kwargs.get("embedding_dtype", "float16"),
                embedding_spec=kwargs.get("embedding_spec"),
                writer_lock=True,
            )
        try:
            return DualStorage(
                primary=primary,
                secondary=NDJSONReplica(path=secondary_path),
                sync_interval=sync_interval,
                compact_interval=int(kwargs.get("compact_interval", 24 * 60 * 60)),
                compact_min_bytes=int(kwargs.get("compact_min_bytes", 1024 * 1024)),
                compact_min_ratio=float(kwargs.get("compact_min_ratio", 0.25)),
                start_background=bool(kwargs.get("start_background", True)),
            )
        except Exception:
            primary.close()
            raise
    raise ValueError(f"Unknown backend: {backend}")
