"""Background summary worker.

When memory_add / memory_update receive no usable caller summary, the record
is stored immediately and a (memory_id, text) task is enqueued here. A daemon
thread generates the fallback via Ollama and writes it only if that exact text
still has no summary.

Priority: fresh tasks (from add/update) are processed before bootstrap
tasks (recovery of pre-existing records without summary), so a new memory
gets its summary quickly even when the backlog is large.

Recovery: on start(), pending records (summary IS NULL) are re-enqueued
so a restart never loses work. Stale tasks never overwrite a caller-provided
summary or a summary for newer text.
"""

import threading
import queue
import logging
import time

logger = logging.getLogger("summary_worker")

from .summarize import summarize
from .storage import MemoryStorage
from . import audit

# Injected by service.py after storage creation (avoids circular import).
_storage: MemoryStorage | None = None

_FRESH = queue.Queue(maxsize=10000)      # high priority: add/update tasks
_BOOTSTRAP = queue.Queue(maxsize=100000)  # low priority: recovery backlog
_running = threading.Event()
_thread = None


def set_storage(storage):
    """Bind the storage backend (called once by service.py at import time)."""
    global _storage
    _storage = storage


def start():
    """Start the background worker thread (idempotent)."""
    global _thread
    if _storage is None:
        raise RuntimeError("summary_worker.set_storage() must be called before start()")
    if _thread and _thread.is_alive():
        return
    _running.set()
    _thread = threading.Thread(target=_loop, daemon=True, name="summary-worker")
    _thread.start()
    logger.info("summary worker started")


def enqueue(memory_id: str, text: str):
    """Schedule summary generation for a memory (high priority). Never raises."""
    try:
        _FRESH.put_nowait((memory_id, text))
    except queue.Full:
        logger.warning("summary worker fresh queue full, dropping id=%s", memory_id)


def _bootstrap():
    """Re-enqueue records that still have no summary (after restart)."""
    try:
        pending = _storage.get_pending_summaries(limit=100000)
    except Exception:
        logger.error("summary worker bootstrap failed", exc_info=True)
        return
    for m in pending:
        if not m.get("summary"):
            try:
                _BOOTSTRAP.put_nowait((m["id"], m["text"]))
            except queue.Full:
                logger.warning("summary worker bootstrap queue full, stopping fill")
                break
    if pending:
        logger.info("summary worker bootstrap: %d backlog re-enqueued", len(pending))


def _next_task():
    """Fresh tasks first; fall back to the backlog.

    Returns (queue, (memory_id, text)) or (None, None) when both are empty.
    """
    try:
        return _FRESH, _FRESH.get_nowait()
    except queue.Empty:
        pass
    try:
        return _BOOTSTRAP, _BOOTSTRAP.get_nowait()
    except queue.Empty:
        return None, None


def _process_task(memory_id: str, text: str) -> bool:
    """Generate and conditionally store one fallback summary.

    The checks before and during the write make queued work safe when a caller
    supplies a summary or changes the memory while Ollama is generating.
    """
    started = time.perf_counter()
    current = _storage.get(memory_id)
    namespace = current.get("namespace") if current else None
    if not current:
        logger.info("summary worker: id=%s stale, skipped", memory_id)
        audit.record("summary_fallback",
                     {"memory_id": memory_id, "reason": "missing"},
                     {"outcome": "stale_skipped"},
                     (time.perf_counter() - started) * 1000)
        return False
    if current.get("summary"):
        logger.info("summary worker: id=%s stale, skipped", memory_id)
        audit.record("summary_fallback",
                     {"memory_id": memory_id, "namespace": namespace,
                      "reason": "summary_exists"},
                     {"outcome": "stale_skipped"},
                     (time.perf_counter() - started) * 1000)
        return False
    if current.get("text") != text:
        logger.info("summary worker: id=%s stale, skipped", memory_id)
        audit.record("summary_fallback",
                     {"memory_id": memory_id, "namespace": namespace,
                      "reason": "text_changed"},
                     {"outcome": "stale_skipped"},
                     (time.perf_counter() - started) * 1000)
        return False
    summary = summarize(text)
    if not summary:
        logger.warning("summary worker: id=%s empty response", memory_id)
        audit.record("summary_fallback",
                     {"memory_id": memory_id, "namespace": namespace},
                     {"outcome": "empty"},
                     (time.perf_counter() - started) * 1000)
        return False
    written = _storage.update_summary_if_pending(memory_id, text, summary)
    if written:
        logger.info("summary worker: id=%s ok len=%d", memory_id, len(summary))
        audit.record("summary_fallback",
                     {"memory_id": memory_id, "namespace": namespace},
                     {"outcome": "generated", "summary_length": len(summary)},
                     (time.perf_counter() - started) * 1000)
    else:
        logger.info("summary worker: id=%s changed during generation, skipped",
                    memory_id)
        audit.record("summary_fallback",
                     {"memory_id": memory_id, "namespace": namespace,
                      "reason": "changed_during_generation"},
                     {"outcome": "stale_skipped"},
                     (time.perf_counter() - started) * 1000)
    return written


def _loop():
    assert _storage is not None, "summary_worker.set_storage() must be called first"
    _bootstrap()
    while _running.is_set():
        src, task = _next_task()
        if task is None:
            # idle: block briefly on fresh, then re-check running flag
            try:
                task = _FRESH.get(timeout=1.0)
                src = _FRESH
            except queue.Empty:
                continue
        memory_id, text = task
        try:
            _process_task(memory_id, text)
        except Exception as exc:
            # Keep the record pending-eligible (summary stays NULL) so it can
            # be retried after a restart. Do not crash the worker loop.
            logger.warning("summary worker: id=%s failed", memory_id, exc_info=True)
            audit.record("summary_fallback", {"memory_id": memory_id}, None, 0,
                         error={"type": type(exc).__name__,
                                "message": str(exc)})
        finally:
            src.task_done()


def pending_count() -> int:
    return _FRESH.qsize() + _BOOTSTRAP.qsize()


def shutdown():
    """Signal the worker to stop (does not block on the current task)."""
    _running.clear()
    if _thread:
        _thread.join(timeout=5)
    logger.info("summary worker stopped")
