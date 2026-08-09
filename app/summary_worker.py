"""Background summary worker.

memory_add / memory_update no longer wait for LLM summary generation
(~30s per record with qwen3). Instead the record is stored immediately
with summary=None and a (memory_id, text) task is enqueued here. A daemon
thread picks tasks up, generates the summary via Ollama, and writes it
back with storage.update_summary() (which keeps updated_at unchanged and
is thread-safe via RLock).

Priority: fresh tasks (from add/update) are processed before bootstrap
tasks (recovery of pre-existing records without summary), so a new memory
gets its summary quickly even when the backlog is large.

Recovery: on start(), pending records (summary IS NULL) are re-enqueued
so a restart never loses work. Generation is idempotent — re-writing an
existing summary is harmless.
"""

import threading
import queue
import logging

logger = logging.getLogger("summary_worker")

from .summarize import summarize
from .storage import MemoryStorage

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
            summary = summarize(text)
            if summary:
                _storage.update_summary(memory_id, summary)
                logger.info("summary worker: id=%s ok len=%d", memory_id, len(summary))
            else:
                logger.warning("summary worker: id=%s empty response", memory_id)
        except Exception:
            # Keep the record pending-eligible (summary stays NULL) so it can
            # be retried after a restart. Do not crash the worker loop.
            logger.warning("summary worker: id=%s failed", memory_id, exc_info=True)
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
