"""Resource ownership and lifecycle for one server process."""
import logging
import os
import tempfile
import threading
from pathlib import Path

from . import audit, log_config
from .calls import AsyncCallAdapter
from .embed import create_embedder, ModelCacheMissing
from .configuration import Configuration, activate_environment
from .settings import Settings
from .model_preparation import ModelProgress, PreparationCancelled, download_progress
from .provider_adapter import wrap_provider
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
        self.calls = AsyncCallAdapter()
        self.recover_jobs = {}
        self.model_progress = ModelProgress()
        self._model_worker = None
        self._model_cancel = threading.Event()
        self._model_retry_requested = False

    def start(self, *, background_model=False):
        with self._lock:
            if self.state == "running":
                return self
            if self.state != "created":
                raise RuntimeError(f"cannot start runtime in state {self.state}")
            self.state = "starting"
            self.calls.enable()
            try:
                if self.configure_logging:
                    self._handlers = log_config.configure(self.log_dir or self.settings.LOG_DIR)
                self.audit = audit.AuditJournal(self.settings.environment)
                try:
                    options = {"environment": self.settings.environment}
                    if background_model and self.provider_factory is create_embedder:
                        options["cache_only"] = True
                    self.provider = wrap_provider(self.provider_factory(**options))
                except ModelCacheMissing:
                    if not background_model:
                        raise
                    self.maintenance.require("model_not_ready")
                    self.model_progress.set("waiting")
                    self.state = "running"
                    if self.settings.environment.get("MEMORY_AUTO_DOWNLOAD_MODEL", "true") == "true":
                        self.retry_model()
                    return self
                self._start_business()
                self.state = "running"
                return self
            except BaseException:
                try:
                    self.close()
                except BaseException:
                    logger.exception("cleanup after startup failure failed")
                raise

    def _start_business(self):
        s = self.settings
        self.storage = self.storage_factory(
            s.STORAGE_BACKEND, path=s.STORAGE_PATH, primary_path=s.STORAGE_PATH,
            secondary_path=s.REPLICA_PATH, embedding_dtype=s.EMBED_DTYPE,
            embedding_spec=self.provider.spec.as_dict(), sync_interval=s.SYNC_INTERVAL,
            compact_interval=s.COMPACT_INTERVAL, compact_min_bytes=s.COMPACT_MIN_BYTES,
            compact_min_ratio=s.COMPACT_MIN_RATIO, health_check_interval=s.HEALTH_CHECK_INTERVAL,
            start_background=False, audit_journal=self.audit,
        )
        if not self.storage.index_embedding_completeness()["complete"]:
            raise RuntimeError("active embedding profile is missing keyword/category vectors; "
                               "stop the service and run embedding_cli reindex-index-values")
        self.service = MemoryService(s, self.storage, self.provider, audit_journal=self.audit)
        self.service.warmup_embedding_provider()
        self._finish_interrupted_recovery()
        if isinstance(self.storage, DualStorage):
            self.storage.start()
            if self.maintenance.state == "maintenance":
                self.storage.pause_background()

    def model_status(self):
        return {**self.model_progress.snapshot(), "ready": self.service is not None
                and self.maintenance.required_reason is None}

    def model_config(self):
        return Configuration.load(self.settings.DATA_DIR).as_dict(redact=True)

    def save_model_config(self, changes):
        from .maintenance import MaintenanceRejected
        with self._lock:
            if self.state != "running":
                raise MaintenanceRejected("maintenance_busy", "Service is closing; configuration cannot be saved")
            if not isinstance(changes, dict) or set(changes) - {"port", "language", "proxy", "auto_download_model"}:
                raise ValueError("Only port, language, proxy and auto_download_model may be changed here")
            config = Configuration.load(self.settings.DATA_DIR)
            values = config.as_dict()
            if "proxy" in changes:
                if not isinstance(changes["proxy"], dict):
                    raise ValueError("proxy must be an object")
                values["proxy"] = {**values["proxy"], **changes["proxy"]}
            for key in ("port", "language", "auto_download_model"):
                if key in changes:
                    values[key] = changes[key]
            validated = Configuration.from_values(config.path, config.data_dir, values)
            if "***@" in validated.proxy_url:
                raise ValueError("Enter the actual proxy URL; leave URL unchanged to preserve credentials")
            from dataclasses import replace
            replace(validated, source_text=config.source_text).save()
            self.audit.record("model_config", {}, validated.as_dict(redact=True), 0)
            return validated.as_dict(redact=True)

    def retry_model(self):
        from .maintenance import MaintenanceRejected
        with self._lock:
            if self.state != "running" or not self.maintenance.required_reason:
                raise MaintenanceRejected("maintenance_busy", "Model preparation is not required")
            if self._model_worker and self._model_worker.is_alive():
                self._model_retry_requested = True
                self._model_cancel.set()
                self.model_progress.set("cancelling")
            else:
                self._model_cancel.clear()
                self._model_worker = threading.Thread(target=self._prepare_model,
                                                      name="model-preparation", daemon=True)
                self._model_worker.start()
            result = self.model_status()
            self.audit.record("model_retry", {}, result, 0)
            return result

    def _prepare_model(self):
        while True:
            try:
                with self._lock:
                    if self.state != "running":
                        return
                    config = Configuration.load(self.settings.DATA_DIR)
                    self.settings = Settings.from_env(config.runtime_environment(self.settings.environment))
                    activate_environment(self.settings.environment)
                    self._model_cancel.clear()
                    self._model_retry_requested = False
                    self.model_progress.set("downloading")
                provider_name = self.settings.environment.get("EMBED_PROVIDER", "ollama").replace("-", "_")
                with download_progress(provider_name, self.model_progress, self._model_cancel):
                    self.provider = wrap_provider(
                        self.provider_factory(environment=self.settings.environment, allow_download=True))
                with self._lock:
                    if self.state != "running" or self._model_cancel.is_set():
                        raise PreparationCancelled()
                    self.model_progress.set("validating")
                    self._start_business()
                    self.maintenance.release_requirement()
                    self.model_progress.set("ready")
                    self.audit.record("model_prepare", {}, self.model_status(), 0)
            except PreparationCancelled:
                self._release_model_business()
            except Exception as exc:
                self._release_model_business()
                message = str(exc)
                if "config" in locals() and config.proxy_url:
                    message = message.replace(config.proxy_url, "[proxy]")
                self.model_progress.set("failed", error=message)
                self.audit.record("model_prepare", {}, self.model_status(), 0,
                                  error={"type": type(exc).__name__, "message": message})
                logger.error("Model preparation failed: %s", message)
            with self._lock:
                if self.state == "running" and self._model_retry_requested:
                    continue
                # Clear ownership under the same lock used by retry_model, so a
                # retry at thread completion cannot be lost.
                self._model_worker = None
                return

    def _release_model_business(self):
        if self.service is not None:
            self.service.close()
        if self.storage is not None:
            self.storage.stop()
        close = getattr(self.provider, "close", None)
        if callable(close):
            close()
        self.service = self.storage = self.provider = None

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
            self.calls.stop_accepting()
            if self.shutdown_callback is not None:
                self.shutdown_callback()

    def close(self):
        with self._lock:
            self.state = "closing" if self.state != "closed" else "closed"
            self.calls.stop_accepting()
            self._model_cancel.set()
            worker = self._model_worker
        if worker is not None and worker is not threading.current_thread():
            worker.join()
        # Reject new work, cancel tasks that never started and wait for running
        # calls to finish (and release their admission) before storage/provider
        # are released. This runs in the closing thread, never the event loop.
        self.calls.close()
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
