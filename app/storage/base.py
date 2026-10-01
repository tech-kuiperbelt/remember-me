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
        return "memories.jsonl"
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


class StorageCapabilityError(RuntimeError):
    """The selected backend does not implement an optional storage capability."""


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
    def search_lexical(self, query: str, k: int = 10,
                       namespace: Optional[str] = None) -> list[dict[str, Any]]:
        """Content-only FTS5/BM25 search, ordered best-first.

        Score is ``-raw_bm25``; callers must not compare it with cosine scores.
        """
        ...

    @abstractmethod
    def search_anchor(self, query: str, k: int = 10,
                      namespace: Optional[str] = None) -> list[dict[str, Any]]:
        """Anchor-only FTS5/BM25 search, ordered best-first.

        Matches ``metadata.anchors`` (identity signals), not memory content.
        Score is ``-raw_bm25``; callers must not compare it with cosine scores.
        """
        ...

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
    def list_namespaces(self) -> list[str]:
        """Return distinct namespaces of live records, sorted.

        Cheap existence list for namespace validation — unlike ``get_stats``
        it computes no counts or embedding metadata.
        """
        raise NotImplementedError

    @abstractmethod
    def live_memory_ids(self) -> list[str]:
        """Return the ids of live records only.

        Cheap existence set for replica health checks — unlike
        ``export_records`` it never loads text, metadata or vectors.
        """
        raise NotImplementedError

    @abstractmethod
    def get_stats(self) -> dict[str, Any]: ...

    # -- business index and lifecycle surface used by Service/IndexValueResolver --

    @abstractmethod
    def stop(self) -> None:
        """Release this storage's resources. Safe to call more than once."""
        ...

    @abstractmethod
    def active_embedding_profile(self) -> Optional[str]:
        """Active embedding profile id, or None when the store has none.

        A cheap accessor: unlike ``get_stats`` it never inspects a replica.
        """
        ...

    @abstractmethod
    def keyword_counts(self, namespace: Optional[str] = None) -> dict[str, int]: ...

    @abstractmethod
    def exact_index_values(self, dimension: str, values: list[str],
                           namespace: Optional[str] = None) -> set[str]: ...

    @abstractmethod
    def semantic_index_values(self, dimension: str, query_embedding: list[float],
                              namespace: Optional[str] = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    def missing_index_value_embeddings(self, metadata: Optional[dict]) -> list[tuple[str, str]]: ...

    @abstractmethod
    def index_embedding_completeness(self, profile_id: Optional[str] = None) -> dict[str, Any]: ...

    @abstractmethod
    def backfill_missing_index_value_embeddings(self, embed_documents) -> int: ...

    @abstractmethod
    def update_metadata(self, memory_id: str, metadata: dict,
                        index_embeddings: Optional[dict] = None) -> bool: ...

    @abstractmethod
    def update_anchors_batch(self, entries) -> int: ...

    @abstractmethod
    def rebuild_keyword_index(self) -> dict[str, Any]: ...

    @abstractmethod
    def rebuild_category_index(self) -> dict[str, Any]: ...

    @abstractmethod
    def get_keyword_vocabulary(self, namespace: Optional[str] = None) -> list[str]: ...

    @abstractmethod
    def storage_paths(self) -> dict[str, Any]:
        """Operator-facing storage description for the admin UI.

        Always reports ``backend``; replica-backed backends also report their
        ``primary_path``/``secondary_path``. A single SQLite backend reports
        only ``backend`` (matching ``MemoryService.memory_storage_paths``).
        """
        ...

    # -- replica-coordination capabilities (not every backend has a replica) --

    def force_sync(self) -> dict[str, Any]:
        """Rebuild the replica from the primary. Backends without a replica
        raise :class:`StorageCapabilityError`."""
        raise StorageCapabilityError("storage backend does not support sync")

    def export_full_snapshot(self, destination: str) -> dict[str, Any]:
        """Full-sync, verify and copy an immutable download snapshot. Backends
        without a replica raise :class:`StorageCapabilityError`."""
        raise StorageCapabilityError("Full Export requires MEMORY_STORAGE=dual")


class SQLitePrimaryStorage(MemoryStorage):
    """The SQLite primary's capabilities: outbox, connection swap and
    embedding-profile maintenance.

    These deliberately do not live on :class:`ReplicaStorage`: they are primary
    database concerns, not part of the vector-free JSONL replica contract.
    """

    @abstractmethod
    def count_unsynced(self) -> int: ...

    @abstractmethod
    def get_unsynced_records(self, limit: int = 500): ...

    @abstractmethod
    def mark_synced_versions(self, entries) -> int: ...

    @abstractmethod
    def mark_synced_batch(self, memory_ids) -> int: ...

    @abstractmethod
    def close_connection(self) -> None:
        """Close the SQLite connection while keeping any lifetime writer lock."""

    @abstractmethod
    def reopen_connection(self) -> None: ...

    @abstractmethod
    def configure_embedding_profile(self, spec: dict) -> dict[str, Any]: ...

    @abstractmethod
    def reencode_active_embeddings(self, dtype: str) -> dict[str, Any]: ...

    @abstractmethod
    def reindex_embeddings(self, spec: dict, embed_documents, batch_size: int = 50,
                           progress_callback=None) -> dict[str, Any]: ...

    @abstractmethod
    def reindex_index_values(self, profile_id: str, embed_documents,
                             batch_size: int = 50, progress_callback=None) -> dict[str, Any]: ...

    @abstractmethod
    def activate_embedding_profile(self, profile_id: str) -> dict[str, Any]: ...


class ReplicaStorage(ABC):
    """Narrow contract for a vector-free disaster-recovery replica."""

    @abstractmethod
    def acquire_writer_lock(self) -> None: ...

    @abstractmethod
    def release_writer_lock(self) -> None: ...

    @abstractmethod
    def upsert(self, record: dict) -> None: ...

    @abstractmethod
    def append_tombstone(self, memory_id: str, updated_at: str) -> None: ...

    @abstractmethod
    def append_batch(self, records: list[dict]) -> None:
        """Durably append one outbox batch before its primary is acknowledged."""
        ...

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
