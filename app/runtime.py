"""Resource ownership and lifecycle for one server process."""
import logging
import os
import re
import tempfile
import threading
import traceback
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

from app import audit
from app import log_config
from app.operations.calls import AsyncCallAdapter
from app.embedding.embed import (
    create_embedder, EmbeddingError, ModelCacheMissing, ModelCacheUnusable,
)
from app.embedding.provider_resolution import resolve_embedding_environment
from app.config.configuration import Configuration, activate_environment
from app.config.settings import Settings
from app.operations.model_preparation import ModelProgress, PreparationCancelled, download_progress
from app.embedding.provider_adapter import wrap_provider
from app.service import MemoryService
from app.storage import create_storage, SQLitePrimaryStorage
from app.storage.dual import DualStorage
from app.operations.maintenance import MaintenanceGate

logger = logging.getLogger("runtime")


class Runtime:
    def __init__(self, settings, *, provider_factory=create_embedder, storage_factory=create_storage,
                 configure_logging=True, log_dir=None):
        self.settings = settings
        self.debug_effective = settings.UI_DEBUG
        # UI language is a mutable snapshot like debug: saving config applies it to
        # the next page load without requiring a restart.
        self.language_effective = settings.LANGUAGE
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
        self._recover_lock = threading.RLock()
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
                self._resolve_provider_environment()
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
                except (ModelCacheUnusable, EmbeddingError, OSError) as exc:
                    if not background_model:
                        raise
                    # A model that cannot be loaded must not block the HTTP app:
                    # open the maintenance/config UI so the user can retry.
                    message = self._redact_error(str(exc), self.settings.environment)
                    self.maintenance.require("model_not_ready")
                    self.model_progress.set("failed", error=message)
                    self._log_model_error("Model preparation failed", exc)
                    self.state = "running"
                    if (isinstance(exc, ModelCacheUnusable)
                            and self.settings.environment.get(
                                "MEMORY_AUTO_DOWNLOAD_MODEL", "true") == "true"):
                        self.retry_model()
                    return self
                try:
                    self._start_business()
                except (EmbeddingError, OSError) as exc:
                    # Warmup/validation failure: release the partial business
                    # resources and keep the UI available for a retry.
                    if not background_model:
                        raise
                    self._release_model_business()
                    message = self._redact_error(str(exc), self.settings.environment)
                    self.maintenance.require("model_not_ready")
                    self.model_progress.set("failed", error=message)
                    self._log_model_error("Model startup failed", exc)
                    self.state = "running"
                    return self
                self.state = "running"
                return self
            except BaseException:
                try:
                    self.close()
                except BaseException:
                    logger.exception("cleanup after startup failure failed")
                raise

    def _resolve_provider_environment(self):
        """Adopt the database profile (or the new default) for the real provider.

        Injected provider factories (tests) keep the environment unchanged. The
        resolved environment is also activated so in-process helpers that build
        their own provider from ``os.environ`` see the same configuration.
        """
        if self.provider_factory is not create_embedder:
            return
        resolved = resolve_embedding_environment(
            self.settings.environment, db_path=self.settings.STORAGE_PATH)
        if dict(self.settings.environment) != resolved:
            self.settings = replace(self.settings, environment=MappingProxyType(resolved))
        activate_environment(resolved)

    @staticmethod
    def _redact_error(message, environment):
        """Remove proxy credentials before errors reach status, audit or logs."""
        if not message:
            return message
        for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
                    "ALL_PROXY", "all_proxy"):
            value = environment.get(key)
            if value:
                message = message.replace(value, "[proxy]")
        return re.sub(r"(https?://)[^/@\s]+@", r"\1", message)

    def _log_model_error(self, label, exc, environment=None):
        # Format first so neither LogRecord.exc_info nor its cached traceback text
        # can expose the original exception chain to downstream handlers.
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        detail = self._redact_error(
            detail, self.settings.environment if environment is None else environment)
        logger.error("%s:\n%s", label, detail)

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
        with self._lock:
            return {**Configuration.load(self.settings.DATA_DIR).as_dict(redact=True),
                    "debug_effective": self.debug_effective}

    def save_model_config(self, changes):
        from app.operations.maintenance import MaintenanceRejected
        with self._lock:
            if self.state != "running":
                raise MaintenanceRejected("maintenance_busy", "Service is closing; configuration cannot be saved")
            if not isinstance(changes, dict) or set(changes) - {"port", "language", "proxy", "auto_download_model", "local_only", "debug"}:
                raise ValueError("Only port, language, proxy, auto_download_model, local_only and debug may be changed here")
            config = Configuration.load(self.settings.DATA_DIR)
            values = config.as_dict()
            if "proxy" in changes:
                if not isinstance(changes["proxy"], dict):
                    raise ValueError("proxy must be an object")
                values["proxy"] = {**values["proxy"], **changes["proxy"]}
            for key in ("port", "language", "auto_download_model", "local_only", "debug"):
                if key in changes:
                    values[key] = changes[key]
            validated = Configuration.from_values(config.path, config.data_dir, values)
            if "***@" in validated.proxy_url:
                raise ValueError("Enter the actual proxy URL; leave URL unchanged to preserve credentials")
            from dataclasses import replace
            replace(validated, source_text=config.source_text).save()
            if "debug" in changes:
                self.debug_effective = validated.debug
            if "language" in changes:
                self.language_effective = validated.language
            self.audit.record("model_config", {}, validated.as_dict(redact=True), 0)
            return {**validated.as_dict(redact=True), "debug_effective": self.debug_effective}

    def retry_model(self):
        from app.operations.maintenance import MaintenanceRejected
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
                    self.language_effective = self.settings.LANGUAGE
                    activate_environment(self.settings.environment)
                    self._resolve_provider_environment()
                    self._model_cancel.clear()
                    self._model_retry_requested = False
                    self.model_progress.set("downloading")
                provider_name = self.settings.environment.get("EMBED_PROVIDER", "ollama").replace("-", "_")
                provider = None
                try:
                    with download_progress(provider_name, self.model_progress, self._model_cancel):
                        provider = wrap_provider(
                            self.provider_factory(environment=self.settings.environment, allow_download=True))
                    # Ownership transfers to ``self.provider`` here; anything not
                    # yet handed over is closed in the finally, so a cancellation
                    # raised around construction can never orphan a provider.
                    self.provider = provider
                    provider = None
                    with self._lock:
                        if self.state != "running" or self._model_cancel.is_set():
                            raise PreparationCancelled()
                        self.model_progress.set("validating")
                        self._start_business()
                        self.maintenance.release_requirement()
                        self.model_progress.set("ready")
                        self.audit.record("model_prepare", {}, self.model_status(), 0)
                finally:
                    if provider is not None:
                        self._close_provider_quietly(provider)
            except PreparationCancelled:
                self._release_model_business()
            except Exception as exc:
                self._release_model_business()
                error_environment = dict(self.settings.environment)
                if "config" in locals() and config.proxy_url:
                    error_environment["HTTPS_PROXY"] = config.proxy_url
                message = self._redact_error(str(exc), error_environment)
                self.model_progress.set("failed", error=message)
                self.audit.record("model_prepare", {}, self.model_status(), 0,
                                  error={"type": type(exc).__name__, "message": message})
                self._log_model_error("Model preparation failed", exc, error_environment)
            with self._lock:
                if self.state == "running" and self._model_retry_requested:
                    continue
                # Clear ownership under the same lock used by retry_model, so a
                # retry at thread completion cannot be lost.
                self._model_worker = None
                return

    def _release_model_business(self):
        try:
            self._close_business()
        except BaseException:
            logger.exception("model business cleanup failed")

    def _close_provider(self):
        close = getattr(self.provider, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _close_provider_quietly(provider):
        """Release a provider during failure cleanup without masking the cause."""
        close = getattr(provider, "close", None)
        if callable(close):
            try:
                close()
            except BaseException:
                logger.exception("failed to close embedding provider")

    def _close_business(self):
        """Release business resources; every closer runs even if one fails.

        Raises the first failure after attempting all of them, so callers keep
        the original error while nothing is leaked.
        """
        failures = []
        for name, closer in (
            ("service", lambda: self.service.close() if self.service is not None else None),
            ("storage", lambda: self.storage.stop() if self.storage is not None else None),
            ("provider", self._close_provider),
        ):
            try:
                closer()
            except BaseException as exc:
                logger.exception("failed to close %s", name)
                failures.append(exc)
        self.service = self.storage = self.provider = None
        if failures:
            raise failures[0]

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

    def maintenance_import_status(self, import_id):
        """Snapshot of one import job (None when unknown)."""
        return self.service.memory_import_status(import_id)

    def start_maintenance_import(self, file_path, original_name, namespace_prefix="", *, reserved=False):
        """Reserve the import maintenance action and start its background job.

        Completion is owned here: one waiter thread blocks on the job's
        completion event and releases the action afterwards, so the action stays
        occupied until the worker has finished its status, audit and cleanup.
        """
        if not reserved:
            self.maintenance.start_action("import")
        try:
            result = self.service.memory_import_start(file_path, original_name, namespace_prefix)
        except BaseException as exc:
            self.maintenance.finish_action(error=exc)
            raise
        import_id = result["import_id"]
        try:
            threading.Thread(target=self._finish_import, args=(import_id,),
                             daemon=True, name=f"maintenance-import-{import_id}").start()
        except BaseException as exc:
            # The import already owns its input file and can still write. If a
            # waiter cannot be started, this calling worker must drain the job
            # before releasing maintenance or returning to HTTP cleanup.
            logger.exception("cannot start import completion waiter; waiting in caller")
            self._finish_import(import_id)
            if not isinstance(exc, Exception):
                raise
        return result

    def _finish_import(self, import_id):
        try:
            self.service.wait_import(import_id)
        finally:
            self.maintenance.finish_action(result={"import_id": import_id})

    def maintenance_sync(self):
        """Full sync plus recovery-marker reconciliation, owned by the runtime.

        A pending recovery marker is only removed (and its sticky error only
        cleared) after the replica passes the exact health check, preserving the
        old admin behavior.
        """
        result = self.service.memory_sync()
        marker = Path(f"{self.settings.STORAGE_PATH}.online-recover")
        if marker.exists():
            try:
                self._verify_recovered_replica()
            except RuntimeError:
                pass
            else:
                marker.unlink()
                self.maintenance.clear_error()
        return result

    def _new_recover_job(self, job_id, filename):
        with self._recover_lock:
            self.recover_jobs[job_id] = {
                "id": job_id, "filename": filename, "status": "queued",
                "phase": "queued", "completed": 0, "total": 0, "result": None,
                "error": None}
            while len(self.recover_jobs) > 20:
                self.recover_jobs.pop(next(iter(self.recover_jobs)))

    def _update_recover_job(self, job_id, **changes):
        with self._recover_lock:
            job = self.recover_jobs.get(job_id)
            if job is not None:
                job.update(changes)

    def recover_status(self, job_id):
        """Consistent snapshot for HTTP polling; never the shared mutable dict."""
        with self._recover_lock:
            job = self.recover_jobs.get(job_id)
            return dict(job) if job else None

    def start_recover(self, upload_path, filename, *, reserved=False):
        """Run uploaded JSONL recovery inside this runtime's writer-lock lifetime."""
        if self.settings.STORAGE_PATH == ":memory:":
            raise RuntimeError("online recover requires a file-backed SQLite primary")
        if not reserved:
            self.maintenance.start_action("recover")
        job_id = "rc_" + os.urandom(8).hex()
        self._new_recover_job(job_id, filename)

        def run():
            from app.storage.recovery import build_recovery_database, install_recovery_database
            from app.storage.jsonl_transfer import unlink_quietly
            target = self.settings.STORAGE_PATH
            temp_path = None
            marker = None
            result = None
            failure = None
            try:
                fd, temp_path = tempfile.mkstemp(prefix=".recover-", suffix=".db",
                                                  dir=str(Path(target).resolve().parent))
                os.close(fd)
                self._update_recover_job(job_id, status="running", phase="building")
                spec = self.provider.spec.as_dict()

                def progress(done, total):
                    self._update_recover_job(job_id, completed=done, total=total)

                count = build_recovery_database(
                    upload_path, temp_path, self.provider.embed_document,
                    embedding_spec=spec, embedding_dtype=self.settings.EMBED_DTYPE,
                    embed_documents=self.provider.embed_documents,
                    max_records=self.settings.IMPORT_MAX_RECORDS, progress=progress)
                primary = self.storage.primary if isinstance(self.storage, DualStorage) else self.storage
                if not isinstance(primary, SQLitePrimaryStorage):
                    raise RuntimeError("online recover requires SQLite primary")
                self._update_recover_job(job_id, phase="switching")
                with self.maintenance.switching():
                    marker = install_recovery_database(temp_path, target, primary)
                    if primary.active_embedding_profile() != spec["profile_id"]:
                        raise RuntimeError("recovered database profile does not match provider")
                if isinstance(self.storage, DualStorage):
                    self._update_recover_job(job_id, phase="syncing_replica")
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
                    self._update_recover_job(job_id, status="completed", phase="completed", result=result)
                    self.maintenance.finish_action(result=result)
                else:
                    self._update_recover_job(job_id, status="failed", phase="failed", error=str(failure))
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
                self._close_business()
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
