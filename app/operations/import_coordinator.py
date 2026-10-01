"""Import job coordination owned by one MemoryService.

An import is a background worker that mutates a small job record through its
phases. Callers (MCP/HTTP/Runtime) need a reliable way to know the job is
*finished*, which includes its final status, its input file being removed and
its active slot being released. This coordinator owns that bookkeeping:

- one job record and one completion :class:`threading.Event` per import;
- a bounded history and a single active import at a time;
- exactly one cleanup owner (the worker's outermost ``finally``), so the
  completion event is only set after the final status, file cleanup and active
  slot update are all done;
- ``wait`` blocks on the event and returns a snapshot, so callers never poll.
"""

import logging
import os
import threading
from collections import OrderedDict
from datetime import datetime, timezone

from app.storage.jsonl_transfer import unlink_quietly

logger = logging.getLogger("imports")

MAX_JOBS = 20


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ImportCoordinator:
    def __init__(self, run_job, *, max_jobs: int = MAX_JOBS):
        self._run_job = run_job
        self._max_jobs = max_jobs
        self._lock = threading.RLock()
        self._jobs = OrderedDict()
        self._events = {}
        self._active_id = None
        self._workers = []
        self._closing = False

    def start(self, file_path, original_name, namespace_prefix="", *, job_fields=None):
        """Register and start one import worker. Returns {"import_id", "status"}.

        On thread-start failure the job record and active slot are rolled back
        and the error re-raised, leaving the input file to the caller. Starting
        and registering the worker happens under the same lock ``close`` uses, so
        a concurrent close either sees the worker or rejects the new import.
        """
        with self._lock:
            if self._closing:
                raise RuntimeError("service is closing; no new imports accepted")
            if self._active_id is not None:
                raise RuntimeError("another import is already running")
            import_id = "im_" + os.urandom(8).hex()
            job = {
                "import_id": import_id, "status": "queued", "phase": "queued",
                "filename": os.path.basename(original_name), "namespace_prefix": namespace_prefix,
                "created_at": _now_utc_iso(), "progress": {"completed": 0, "total": 0},
                "result": None, "error": None,
            }
            if job_fields:
                job.update(job_fields)
            self._active_id = import_id
            self._jobs[import_id] = job
            self._events[import_id] = threading.Event()
            while len(self._jobs) > self._max_jobs:
                stale, _ = self._jobs.popitem(last=False)
                self._events.pop(stale, None)
            self._workers = [w for w in self._workers if w.is_alive()]
            worker = threading.Thread(
                target=self._worker,
                args=(import_id, file_path, original_name, namespace_prefix),
                daemon=True, name=f"memory-import-{import_id}",
            )
            try:
                worker.start()
            except BaseException:
                self._jobs.pop(import_id, None)
                self._events.pop(import_id, None)
                if self._active_id == import_id:
                    self._active_id = None
                raise
            self._workers.append(worker)
        return {"import_id": import_id, "status": "queued"}

    def status(self, import_id):
        with self._lock:
            job = self._jobs.get(import_id)
            return dict(job) if job else None

    def wait(self, import_id, timeout=None):
        """Return a cleaned-up job snapshot, or raise TimeoutError on timeout.

        Unknown or expired job IDs return None.
        """
        with self._lock:
            event = self._events.get(import_id)
        if event is not None and not event.wait(timeout):
            raise TimeoutError(f"Import {import_id} has not finished cleanup")
        return self.status(import_id)

    def update(self, import_id, **changes):
        with self._lock:
            job = self._jobs.get(import_id)
            if job is not None:
                job.update(changes)

    def close(self):
        """Reject new imports and drain every worker."""
        with self._lock:
            self._closing = True
            workers = list(self._workers)
        for worker in workers:
            if worker.is_alive():
                logger.info("waiting for import worker %s", worker.name)
                worker.join()

    def _worker(self, import_id, file_path, original_name, namespace_prefix):
        try:
            self._run_job(import_id, file_path, original_name, namespace_prefix)
        except BaseException as exc:
            logger.exception("import worker %s failed unexpectedly", import_id)
            self._finalize_unexpected(import_id, exc)
        finally:
            # Single cleanup owner: file, active slot and final status all settle
            # before the completion event is set.
            unlink_quietly(file_path)
            with self._lock:
                if self._active_id == import_id:
                    self._active_id = None
                event = self._events.get(import_id)
            if event is not None:
                event.set()

    def _finalize_unexpected(self, import_id, exc):
        with self._lock:
            job = self._jobs.get(import_id)
            if job is None or job.get("status") in ("completed", "failed"):
                return
            job.update(status="failed", phase="failed",
                       error={"type": type(exc).__name__, "message": str(exc)},
                       completed_at=_now_utc_iso())
