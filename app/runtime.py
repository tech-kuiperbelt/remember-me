"""Resource ownership and lifecycle for one server process."""
import logging
import os
import tempfile
import threading
from pathlib import Path

from . import audit, log_config
from .embed import create_embedder
from .service import MemoryService
from .storage import create_storage
from .storage_dual import DualStorage
from .maintenance import MaintenanceGate

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
        self.audit = None
        self._handlers = []
        self._lock = threading.RLock()
        self.restart_requested = False
        self.shutdown_callback = None
        self.maintenance = MaintenanceGate()
        self.recover_jobs = {}

    def start(self):
        with self._lock:
            if self.state == "running":
                return self
            if self.state != "created":
                raise RuntimeError(f"cannot start runtime in state {self.state}")
            self.state = "starting"
            try:
                if self.configure_logging:
                    self._handlers = log_config.configure(self.log_dir or self.settings.LOG_DIR)
                self.audit = audit.AuditJournal(self.settings.environment)
                self.provider = self.provider_factory(environment=self.settings.environment)
                s = self.settings
                self.storage = self.storage_factory(
                    s.STORAGE_BACKEND, path=s.STORAGE_PATH, primary_path=s.STORAGE_PATH,
                    secondary_path=s.REPLICA_PATH, embedding_dtype=s.EMBED_DTYPE,
                    embedding_spec=self.provider.spec.as_dict(), sync_interval=s.SYNC_INTERVAL,
                    compact_interval=s.COMPACT_INTERVAL, compact_min_bytes=s.COMPACT_MIN_BYTES,
                    compact_min_ratio=s.COMPACT_MIN_RATIO, health_check_interval=s.HEALTH_CHECK_INTERVAL,
                    start_background=False, audit_journal=self.audit,
                )
                completeness = self.storage.index_embedding_completeness()
                if not completeness["complete"]:
                    raise RuntimeError("active embedding profile is missing keyword/category vectors; "
                                       "stop the service and run embedding_cli reindex-index-values")
                self.service = MemoryService(s, self.storage, self.provider, audit_journal=self.audit)
                self.service.warmup_embedding_provider()
                self._finish_interrupted_recovery()
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

    def _finish_interrupted_recovery(self):
        """An atomic primary restore may have finished before its replica sync."""
        marker = Path(f"{self.settings.STORAGE_PATH}.online-recover")
        if marker.exists():
            if isinstance(self.storage, DualStorage):
                self.storage.force_sync()
                self._verify_recovered_replica()
            marker.unlink()

    def _verify_recovered_replica(self):
        status = self.storage.get_stats()
        if not status.get("in_sync"):
            raise RuntimeError("recovered JSONL replica failed exact health check")

    def enter_maintenance(self):
        self.maintenance.enter(
            self.storage.pause_background if isinstance(self.storage, DualStorage) else None)

    def exit_maintenance(self):
        self.maintenance.exit()
        if isinstance(self.storage, DualStorage):
            self.storage.resume_background()

    def start_recover(self, upload_path, filename, *, reserved=False):
        """Run uploaded JSONL recovery inside this runtime's writer-lock lifetime."""
        if self.settings.STORAGE_PATH == ":memory:":
            raise RuntimeError("online recover requires a file-backed SQLite primary")
        if not reserved:
            self.maintenance.start_action("recover")
        job_id = "rc_" + os.urandom(8).hex()
        job = {"id": job_id, "filename": filename, "status": "queued",
               "phase": "queued", "completed": 0, "total": 0, "result": None,
               "error": None}
        self.recover_jobs[job_id] = job
        while len(self.recover_jobs) > 20:
            self.recover_jobs.pop(next(iter(self.recover_jobs)))

        def run():
            from .recovery import build_recovery_database, install_recovery_database
            from .jsonl_transfer import unlink_quietly
            from .storage_sqlite import SQLiteStorage
            target = self.settings.STORAGE_PATH
            temp_path = None
            marker = None
            result = None
            failure = None
            try:
                fd, temp_path = tempfile.mkstemp(prefix=".recover-", suffix=".db",
                                                  dir=str(Path(target).resolve().parent))
                os.close(fd)
                job.update(status="running", phase="building")
                spec = self.provider.spec.as_dict()

                def progress(done, total):
                    job.update(completed=done, total=total)

                count = build_recovery_database(
                    upload_path, temp_path, self.provider.embed_document,
                    embedding_spec=spec, embedding_dtype=self.settings.EMBED_DTYPE,
                    embed_documents=self.provider.embed_documents,
                    max_records=self.settings.IMPORT_MAX_RECORDS, progress=progress)
                primary = self.storage.primary if isinstance(self.storage, DualStorage) else self.storage
                if not isinstance(primary, SQLiteStorage):
                    raise RuntimeError("online recover requires SQLite primary")
                job["phase"] = "switching"
                with self.maintenance.switching():
                    marker = install_recovery_database(temp_path, target, primary)
                    if primary.get_stats()["embedding_profile"]["active"] != spec["profile_id"]:
                        raise RuntimeError("recovered database profile does not match provider")
                if isinstance(self.storage, DualStorage):
                    job["phase"] = "syncing_replica"
                    self.storage.force_sync()
                    self._verify_recovered_replica()
                if marker:
                    marker.unlink()
                result = {"recovered": count, "errors": 0}
                self.audit.record("online_recover", {"filename": filename}, result, 0)
            except BaseException as exc:
                logger.exception("online recovery failed")
                failure = exc
                self.audit.record("online_recover", {"filename": filename}, None, 0,
                                  error={"type": type(exc).__name__, "message": str(exc)})
            finally:
                unlink_quietly(upload_path)
                if temp_path:
                    unlink_quietly(temp_path)
                    unlink_quietly(temp_path + "-wal")
                    unlink_quietly(temp_path + "-shm")
                    unlink_quietly(temp_path + ".writer.lock")
                if failure is None:
                    job.update(status="completed", phase="completed", result=result)
                    self.maintenance.finish_action(result=result)
                else:
                    job.update(status="failed", phase="failed", error=str(failure))
                    self.maintenance.finish_action(
                        error=failure, sticky=Path(f"{target}.online-recover").exists())

        try:
            threading.Thread(target=run, daemon=True, name=f"recover-{job_id}").start()
        except BaseException:
            self.maintenance.finish_action()
            raise
        return {"id": job_id, "status": "queued"}

    def request_shutdown(self, *, restart=False):
        with self._lock:
            if self.state != "running":
                return
            self.restart_requested = restart
            self.state = "closing"
            if self.shutdown_callback is not None:
                self.shutdown_callback()

    def close(self):
        self.maintenance.wait_idle()
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
                            if self.audit is not None:
                                self.audit.close()
                                self.audit = None
                        finally:
                            log_config.close(self._handlers)
                            self._handlers = []
                            self.state = "closed"


def create_runtime(settings, **kwargs):
    """Construct without acquiring resources. Caller owns start/close."""
    return Runtime(settings, **kwargs)
