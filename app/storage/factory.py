"""Storage assembly; concrete backends are imported only when requested."""
import os

from .base import MemoryStorage, resolve_replica_path


def create_storage(backend: str = "sqlite", **kwargs) -> MemoryStorage:
    if backend == "sqlite":
        from app.storage.sqlite import SQLiteStorage
        return SQLiteStorage(
            path=kwargs.get("path", "data/memories.db"),
            embedding_dtype=kwargs.get("embedding_dtype", "float16"),
            embedding_spec=kwargs.get("embedding_spec"),
            read_only=bool(kwargs.get("read_only", False)),
            writer_lock=not bool(kwargs.get("read_only", False)),
        )
    if backend == "dual":
        from app.storage.ndjson import NDJSONReplica
        from app.storage.sqlite import SQLiteStorage
        from app.storage.dual import DualStorage
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
                health_check_interval=int(kwargs.get("health_check_interval", 24 * 60 * 60)),
                start_background=bool(kwargs.get("start_background", True)),
                audit_journal=kwargs.get("audit_journal"),
            )
        except Exception:
            primary.close()
            raise
    raise ValueError(f"Unknown backend: {backend}")
