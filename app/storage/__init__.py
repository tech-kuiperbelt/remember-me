"""Public storage contracts and lazy backend assembly."""
from .base import (
    DEFAULT_NAMESPACE,
    MemoryStorage,
    ReplicaStorage,
    SQLitePrimaryStorage,
    StorageCapabilityError,
    resolve_replica_path,
)
from .factory import create_storage

__all__ = [
    "DEFAULT_NAMESPACE", "MemoryStorage", "ReplicaStorage", "SQLitePrimaryStorage",
    "StorageCapabilityError", "resolve_replica_path", "create_storage",
]
