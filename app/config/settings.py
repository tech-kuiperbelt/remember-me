"""Explicit, immutable configuration snapshot shared by one service runtime."""
import os
from pathlib import Path
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Mapping

from app.storage import resolve_replica_path
from app.domain.namespace_policy import normalize_namespace, validate_namespace_name
from app.config.configuration import Configuration, PROJECT_ROOT, default_data_dir, rooted_path


@dataclass(frozen=True)
class Settings:
    environment: Mapping[str, str]
    STORAGE_BACKEND: str
    STORAGE_PATH: str
    REPLICA_PATH: str
    SYNC_INTERVAL: int
    COMPACT_INTERVAL: int
    COMPACT_MIN_BYTES: int
    COMPACT_MIN_RATIO: float
    HEALTH_CHECK_INTERVAL: int
    EMBED_DTYPE: str
    EMBED_PRELOAD: bool
    DEFAULT_NAMESPACE: str
    INDEX_SEMANTIC_TOP_K: int
    INDEX_SEMANTIC_MIN_SCORE: float
    INDEX_SEMANTIC_MAX_GAP: float
    IMPORT_MAX_BYTES: int
    IMPORT_MAX_RECORDS: int
    OLLAMA_BASE_URL: str
    AUDIT_PATH: str
    AUDIT_MAX_BYTES: int
    AUDIT_MAX_FILES: int
    AUDIT_MAX_EVENTS: int
    DATA_DIR: str
    TEMP_DIR: str
    LOG_DIR: str
    PORT: int
    LANGUAGE: str
    LOCAL_ONLY: bool
    CONFIG_PATH: str | None
    UI_DEBUG: bool = False

    @property
    def DOCKER_MODE(self):
        return self.environment.get("MEMORY_DEPLOYMENT_MODE") == "docker"

    @classmethod
    def from_config(cls, data_dir=None, *, model_dir=None, create=False, environment=None):
        config = Configuration.load(data_dir, create=create, environment=environment)
        env = config.runtime_environment(environment, model_dir=model_dir)
        return replace(cls.from_env(env), UI_DEBUG=config.debug)

    @classmethod
    def from_env(cls, environment=None):
        env = dict(os.environ if environment is None else environment)
        backend = env.get("MEMORY_STORAGE", "sqlite")
        if backend not in ("sqlite", "dual"):
            raise RuntimeError(f"unknown MEMORY_STORAGE={backend}; use 'sqlite' or 'dual'")
        # Low-level construction has no side effects but must root relative
        # paths under the same data directory the service entrypoints use, so
        # defaults never fall back into the repository's data/ directory.
        selected = env.get("MEMORY_DATA_DIR")
        if selected:
            root = Path(selected).expanduser()
            if not root.is_absolute():
                root = PROJECT_ROOT / root
        else:
            root = default_data_dir(env)
        root = root.resolve()

        def rooted(value):
            return str(rooted_path(root, value))

        primary = env.get("MEMORY_STORAGE_PATH") or "memories.db"
        path = primary if primary == ":memory:" else rooted(primary)
        replica = rooted(resolve_replica_path(path, env.get("MEMORY_REPLICA_PATH"),
                                               env.get("MEMORY_STORAGE_PATH_DUAL")))
        audit_path = rooted(env.get("MEMORY_AUDIT_PATH", "audit.jsonl"))
        temp_dir = rooted(env.get("MEMORY_TEMP_DIR") or "tmp")
        log_dir = rooted(env.get("MEMORY_LOG_DIR") or "logs")
        env.update(MEMORY_DATA_DIR=str(root), MEMORY_STORAGE_PATH=path,
                   MEMORY_REPLICA_PATH=replica, MEMORY_AUDIT_PATH=audit_path,
                   MEMORY_TEMP_DIR=temp_dir, MEMORY_LOG_DIR=log_dir)
        dtype = env.get("EMBED_DTYPE", "float16")
        if dtype not in ("float16", "float32"):
            raise ValueError("EMBED_DTYPE must be float16 or float32")
        namespace = normalize_namespace(env.get("MEMORY_DEFAULT_NAMESPACE", "default")) or "default"
        validate_namespace_name(namespace)
        return cls(
            environment=MappingProxyType(env), STORAGE_BACKEND=backend, STORAGE_PATH=path,
            REPLICA_PATH=replica, EMBED_DTYPE=dtype, DEFAULT_NAMESPACE=namespace,
            EMBED_PRELOAD=env.get("EMBED_PRELOAD", "true").lower() in ("1", "true", "yes"),
            SYNC_INTERVAL=int(env.get("MEMORY_SYNC_INTERVAL", "300")),
            COMPACT_INTERVAL=int(env.get("MEMORY_COMPACT_INTERVAL", "86400")),
            COMPACT_MIN_BYTES=int(env.get("MEMORY_COMPACT_MIN_BYTES", "1048576")),
            COMPACT_MIN_RATIO=float(env.get("MEMORY_COMPACT_MIN_RATIO", "0.25")),
            HEALTH_CHECK_INTERVAL=int(env.get("MEMORY_HEALTH_CHECK_INTERVAL", "86400")),
            INDEX_SEMANTIC_TOP_K=int(env.get("MEMORY_INDEX_SEMANTIC_TOP_K", "3")),
            INDEX_SEMANTIC_MIN_SCORE=float(env.get("MEMORY_INDEX_SEMANTIC_MIN_SCORE", "0.65")),
            INDEX_SEMANTIC_MAX_GAP=float(env.get("MEMORY_INDEX_SEMANTIC_MAX_GAP", "0.05")),
            IMPORT_MAX_BYTES=int(env.get("MEMORY_IMPORT_MAX_BYTES", "67108864")),
            IMPORT_MAX_RECORDS=int(env.get("MEMORY_IMPORT_MAX_RECORDS", "10000")),
            OLLAMA_BASE_URL=env.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            AUDIT_PATH=audit_path,
            AUDIT_MAX_BYTES=int(env.get("MEMORY_AUDIT_MAX_BYTES", "10485760")),
            AUDIT_MAX_FILES=max(1, int(env.get("MEMORY_AUDIT_MAX_FILES", "10"))),
            AUDIT_MAX_EVENTS=int(env.get("MEMORY_AUDIT_MAX_EVENTS", "0")),
            DATA_DIR=str(root), TEMP_DIR=temp_dir, LOG_DIR=log_dir,
            PORT=int(env.get("MEMORY_PORT", "1314")),
            LANGUAGE=env.get("MEMORY_LANGUAGE", "zh-CN"),
            LOCAL_ONLY=env.get("MEMORY_LOCAL_ONLY", "true").strip().lower() not in
            ("false", "0", "no", "off"),
            CONFIG_PATH=str(root / "config.jsonc"),
        )
