"""Isolate online model preparation so shutdown can interrupt blocked I/O."""
import logging
import multiprocessing
from multiprocessing.connection import wait
import os
import queue
import re
import threading
import traceback

from app.operations.model_preparation import PreparationCancelled

logger = logging.getLogger(__name__)


class ModelProcessStopError(RuntimeError):
    """An OS-level failure to reap the download must prevent exec restart."""


class _Cancellation:
    """A pipe-backed event needs no semaphore cleanup after a parent crash."""

    def __init__(self, connection):
        self.connection = connection

    def is_set(self):
        return self.connection.poll()


def _watch_parent():
    parent = multiprocessing.parent_process()
    if parent is not None:
        wait([parent.sentinel])
        os._exit(1)


def _download_model(environment):
    from app.config.configuration import activate_environment
    from app.embedding.embed import create_embedder
    activate_environment(environment)
    provider = create_embedder(environment=environment, allow_download=True)
    try:
        return None
    finally:
        provider.close()


def _child(environment, progress_pipe, result, cancellation, prepare):
    threading.Thread(target=_watch_parent, name="model-parent-watch", daemon=True).start()
    # Diagnostics travel through the sanitized result channel, never raw stderr.
    logging.disable(logging.CRITICAL)
    from app.operations.model_preparation import ModelProgress, download_progress
    cancelled = _Cancellation(cancellation)
    updates = queue.Queue(maxsize=32)

    def forward_progress():
        try:
            while True:
                progress_pipe.send(updates.get())
        except (OSError, EOFError):
            return

    threading.Thread(target=forward_progress, name="model-progress", daemon=True).start()

    class Progress(ModelProgress):
        def bytes(self, completed, total, filename=""):
            # Keep pipe messages small, including when third-party descriptions
            # contain arbitrary text.
            super().bytes(completed, total, str(filename)[:512])
            try:
                updates.put_nowait(self.snapshot())
            except queue.Full:
                pass

    try:
        progress = Progress()
        progress.set("downloading")
        if prepare is None:
            provider = environment.get("EMBED_PROVIDER", "sentence_transformers").replace("-", "_")
            with download_progress(provider, progress, cancelled):
                _download_model(environment)
        else:
            prepare(environment, progress, cancelled)
        result.send(("ok", None))
    except PreparationCancelled:
        result.send(("cancelled", None))
    except BaseException as exc:
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        for key, value in environment.items():
            if "proxy" in key.lower() and value:
                detail = detail.replace(value, "[proxy]")
        detail = re.sub(r"(https?://)[^/@\s]+@", r"\1", detail)
        result.send(("error", detail[-32768:]))
    finally:
        result.close()
        cancellation.close()
        # The progress forwarder is daemon-owned and may be blocked in send;
        # the OS releases this bounded pipe when the child exits.


def _stop(process, cancelled):
    try:
        cancelled.send_bytes(b"cancel")
    except (BrokenPipeError, OSError):
        pass  # Child has already closed its receiving end.
    if process.is_alive():
        logger.info("Cancelling model preparation process pid=%s", process.pid)
    process.join(1)
    if process.is_alive():
        logger.info("Terminating blocked model preparation process pid=%s", process.pid)
        process.terminate()
        process.join(1)
    if process.is_alive():
        logger.warning("Killing blocked model preparation process pid=%s", process.pid)
        process.kill()
        process.join(1)
    if process.is_alive():
        raise ModelProcessStopError("Model preparation process did not exit after kill")


def prepare_model_in_process(environment, progress, cancelled, *, prepare=None):
    """Prepare cache, forwarding progress; never return with a live child."""
    context = multiprocessing.get_context("spawn")
    updates, progress_sender = context.Pipe(duplex=False)
    receiver, sender = context.Pipe(duplex=False)
    cancellation, child_cancel = context.Pipe(duplex=False)
    process = context.Process(target=_child,
                              args=(dict(environment), progress_sender, sender, cancellation, prepare),
                              name="model-download")
    started = False
    try:
        if cancelled.is_set():
            raise PreparationCancelled()
        process.start()
        started = True
        logger.info("Model preparation process started pid=%s", process.pid)
        sender.close()
        progress_sender.close()
        cancellation.close()
        outcome = None
        while True:
            if cancelled.is_set():
                raise PreparationCancelled()
            # Bound draining so progress cannot starve shutdown checks.
            for _ in range(32):
                if not updates.poll():
                    break
                try:
                    item = updates.recv()
                except EOFError:
                    break
                progress.bytes(item["completed"], item["total"], item["file"])
            if outcome is None and receiver.poll(.05):
                try:
                    outcome = receiver.recv()
                except EOFError:
                    pass
            if not process.is_alive():
                process.join()
                if outcome is None and receiver.poll():
                    try:
                        outcome = receiver.recv()
                    except EOFError:
                        pass
                break
        if cancelled.is_set() or (outcome and outcome[0] == "cancelled"):
            raise PreparationCancelled()
        if process.exitcode != 0 or outcome is None:
            raise RuntimeError(f"Model preparation process exited unexpectedly ({process.exitcode})")
        if outcome[0] != "ok":
            raise RuntimeError(outcome[1])
    finally:
        try:
            if started:
                _stop(process, child_cancel)
        finally:
            if not started or not process.is_alive():
                process.close()
            receiver.close()
            sender.close()
            updates.close()
            progress_sender.close()
            cancellation.close()
            child_cancel.close()
