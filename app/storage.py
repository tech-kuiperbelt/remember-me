import uuid
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
# Factory
# ---------------------------------------------------------------------------

def create_storage(backend: str = "sqlite", **kwargs) -> MemoryStorage:
    if backend == "json":
        from .storage_json import JSONStorage
        return JSONStorage(path=kwargs.get("path", "data/memories.json"))
    if backend == "ndjson":
        from .storage_ndjson import NDJSONStorage
        return NDJSONStorage(path=kwargs.get("path", "data/memories.jsonl"))
    if backend == "sqlite":
        from .storage_sqlite import SQLiteStorage
        return SQLiteStorage(path=kwargs.get("path", "data/memories.db"))
    if backend == "dual":
        from .storage_json import JSONStorage
        from .storage_ndjson import NDJSONStorage
        from .storage_sqlite import SQLiteStorage
        from .storage_dual import DualStorage
        primary_path = kwargs.get("primary_path", "data/memories.db")
        secondary_path = kwargs.get("secondary_path",
                                    kwargs.get("path", "").replace(".db", ".jsonl")
                                    if kwargs.get("path") else "data/memories.jsonl")
        sync_interval = int(kwargs.get("sync_interval", 300))
        if secondary_path.endswith(".jsonl"):
            secondary_cls = NDJSONStorage
        else:
            secondary_cls = JSONStorage
        return DualStorage(
            primary=SQLiteStorage(path=primary_path),
            secondary=secondary_cls(path=secondary_path),
            sync_interval=sync_interval,
        )
    raise ValueError(f"Unknown backend: {backend}")
