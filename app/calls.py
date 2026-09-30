"""Bounded async-to-sync bridge for blocking business operations.

The MCP/HTTP transports are async, but embedding, SQLite, JSONL and file work
are synchronous and can take seconds. Running them directly on the event loop
freezes every other request (including the maintenance/config endpoints needed
to recover from a slow or stuck provider).

This module moves that work onto a small, Runtime-owned thread pool while
preserving the existing admission and audit semantics:

- one business pool with a fixed number of workers bounds concurrent work;
- an independent single-worker control pool keeps config/model endpoints
  responsive even when inference saturates the business pool;
- pools are created lazily on first use and only accept work while enabled;
- the admission context manager is entered *inside* the worker, so a task that
  was queued before maintenance started is re-checked when it actually runs;
- caller cancellation cancels only tasks that have not started; running work
  finishes and its admission is released normally;
- shutdown stops accepting work, cancels queued tasks and waits for running
  tasks before the owned resources are released.
"""

import asyncio
import contextvars
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("calls")

BUSINESS_WORKERS = 4


class ServiceClosing(RuntimeError):
    """No new work is accepted because the runtime is shutting down."""


class AsyncCallAdapter:
    """Runtime-owned executor bridge for synchronous business calls."""

    def __init__(self, *, business_workers: int = BUSINESS_WORKERS):
        self._business_workers = business_workers
        self._business = None
        self._control = None
        self._enabled = False
        self._closed = False
        self._state_lock = threading.Lock()

    def enable(self):
        """Allow submissions. Called once the runtime has started."""
        with self._state_lock:
            if not self._closed:
                self._enabled = True

    def _submit(self, work, control):
        with self._state_lock:
            if self._closed or not self._enabled:
                raise ServiceClosing("service is closing; no new work accepted")
            if control:
                if self._control is None:
                    self._control = ThreadPoolExecutor(
                        max_workers=1, thread_name_prefix="memory-control")
                pool = self._control
            else:
                if self._business is None:
                    self._business = ThreadPoolExecutor(
                        max_workers=self._business_workers, thread_name_prefix="memory-call")
                pool = self._business
            return pool.submit(work)

    async def run(self, work, *, control=False, admission=None, wait_on_cancel=False):
        """Run synchronous ``work`` off the event loop and await its result.

        ``admission`` is entered inside the worker thread so a queued call
        cannot bypass admission state that changed after it was submitted.
        Caller cancellation cancels only work that has not started yet; a
        running call is left to finish so its resources and audit are complete.
        """
        context = contextvars.copy_context()

        def execute():
            with self._state_lock:
                if not self._enabled:
                    raise ServiceClosing("service is closing; no new work accepted")
            if admission is None:
                return context.run(work)
            with admission:
                return context.run(work)

        future = self._submit(execute, control)
        loop = asyncio.get_running_loop()
        wrapped = asyncio.wrap_future(future, loop=loop)
        wrapped.add_done_callback(_retrieve_exception)
        try:
            return await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            # Best effort: False when the task already started, in which case it
            # keeps running to completion and releases admission itself.
            future.cancel()
            if wait_on_cancel:
                # HTTP handlers own admission, files and maintenance actions.
                # Keep their finally blocks suspended until the worker is done,
                # including when the caller sends repeated cancellations.
                while not wrapped.done():
                    try:
                        await asyncio.shield(wrapped)
                    except asyncio.CancelledError:
                        continue
                    except BaseException:
                        break
            raise

    def stop_accepting(self):
        """Reject submissions and cancel both queues without waiting for work."""
        with self._state_lock:
            self._enabled = False
            self._closed = True
            pools = (self._business, self._control)
        for pool in pools:
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)

    def close(self):
        """Reject new work, cancel queued tasks and wait for running ones."""
        self.stop_accepting()
        with self._state_lock:
            business, control = self._business, self._control
        for pool in (business, control):
            if pool is not None:
                pool.shutdown(wait=True, cancel_futures=True)


def _retrieve_exception(future):
    # On the cancellation path the awaiting coroutine never observes the result;
    # consume it so asyncio does not log "exception was never retrieved".
    if not future.cancelled():
        future.exception()


class InlineCallRunner:
    """Runs work inline; used when no Runtime owns a call adapter (tests/dev)."""

    async def run(self, work, *, control=False, admission=None, wait_on_cancel=False):
        if admission is None:
            return work()
        with admission:
            return work()


INLINE_RUNNER = InlineCallRunner()
