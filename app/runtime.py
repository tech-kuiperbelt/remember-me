"""Resource ownership and lifecycle for one server process."""
import logging
import threading

from . import audit, log_config
from .embed import create_embedder
from .service import MemoryService
from .storage import create_storage
from .storage_dual import DualStorage

logger = logging.getLogger("runtime")


class Runtime:
    def __init__(self, settings, *, provider_factory=create_embedder, storage_factory=create_storage,
                 configure_logging=True, log_dir=None):
        self.settings = settings
        self.provider_factory = provider_factory
        self.storage_factory = storage_factory
        self.configure_logging = configure_logging
        self.log_dir = log_dir
        self.state = "created"
        self.provider = self.storage = self.service = None
        self._handlers = []
        self._audit_owned = False
        self._lock = threading.RLock()
        self.restart_requested = False
        self.shutdown_callback = None

    def start(self):
        with self._lock:
            if self.state == "running":
                return self
            if self.state != "created":
                raise RuntimeError(f"cannot start runtime in state {self.state}")
            self.state = "starting"
            try:
                if self.configure_logging:
                    self._handlers = log_config.configure(self.log_dir)
                audit.configure(self.settings.environment)
                self._audit_owned = True
                self.provider = self.provider_factory(environment=self.settings.environment)
                s = self.settings
                self.storage = self.storage_factory(
                    s.STORAGE_BACKEND, path=s.STORAGE_PATH, primary_path=s.STORAGE_PATH,
                    secondary_path=s.REPLICA_PATH, embedding_dtype=s.EMBED_DTYPE,
                    embedding_spec=self.provider.spec.as_dict(), sync_interval=s.SYNC_INTERVAL,
                    compact_interval=s.COMPACT_INTERVAL, compact_min_bytes=s.COMPACT_MIN_BYTES,
                    compact_min_ratio=s.COMPACT_MIN_RATIO, health_check_interval=s.HEALTH_CHECK_INTERVAL,
                    start_background=False,
                )
                completeness = self.storage.index_embedding_completeness()
                if not completeness["complete"]:
                    raise RuntimeError("active embedding profile is missing keyword/category vectors; "
                                       "stop the service and run embedding_cli reindex-index-values")
                self.service = MemoryService(s, self.storage, self.provider)
                self.service.warmup_embedding_provider()
                if isinstance(self.storage, DualStorage):
                    self.storage.start()
                self.state = "running"
                return self
            except BaseException:
                try:
                    self.close()
                except BaseException:
                    logger.exception("cleanup after startup failure failed")
                raise

    def request_shutdown(self, *, restart=False):
        with self._lock:
            if self.state != "running":
                return
            self.restart_requested = restart
            self.state = "closing"
            if self.shutdown_callback is not None:
                self.shutdown_callback()

    def close(self):
        with self._lock:
            if self.state == "closed":
                return
            self.state = "closing"
            try:
                if self.service is not None:
                    self.service.close()
            finally:
                try:
                    if self.storage is not None:
                        self.storage.stop()
                finally:
                    try:
                        close = getattr(self.provider, "close", None)
                        if callable(close):
                            close()
                        self.provider = None
                        if self.service is not None:
                            self.service.embedding_provider = None
                    finally:
                        try:
                            if self._audit_owned:
                                audit.close()
                                self._audit_owned = False
                        finally:
                            log_config.close(self._handlers)
                            self._handlers = []
                            self.state = "closed"


def create_runtime(settings, **kwargs):
    """Construct without acquiring resources. Caller owns start/close."""
    return Runtime(settings, **kwargs)
