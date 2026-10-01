"""Owned model preparation status and adapters for dependency byte progress."""
from contextlib import contextmanager
import importlib
import threading


class PreparationCancelled(BaseException):
    """Cooperative cancellation must bypass dependency download retries."""


class ModelProgress:
    def __init__(self):
        self._lock = threading.RLock()
        self._value = {"status": "ready", "phase": "ready", "completed": 0,
                       "total": None, "percent": None, "file": "", "error": None}

    def set(self, status, *, error=None):
        with self._lock:
            self._value.update(status=status, phase=status, completed=0,
                               total=None, percent=None, file="", error=error)

    def bytes(self, completed, total, filename=""):
        with self._lock:
            if self._value["status"] != "downloading":
                return
            self._value.update(completed=max(0, int(completed)),
                               total=int(total) if total else None,
                               percent=min(100, max(0, completed / total * 100)) if total else None,
                               file=str(filename or ""))

    def snapshot(self):
        with self._lock:
            return dict(self._value)


@contextmanager
def download_progress(provider, progress, cancelled):
    """Adapt HF/FastEmbed tqdm counters only for this owned preparation job.

    No estimated percentage is generated. Totals can describe one file or a
    snapshot batch and can grow as the dependency discovers additional files.
    Patches are restored before another job or normal business work starts.
    """
    patches = []

    def check():
        if cancelled.is_set():
            raise PreparationCancelled()

    def replace(module, name, replacement):
        if hasattr(module, name):
            patches.append((module, name, getattr(module, name)))
            setattr(module, name, replacement)

    def tracked(base):
        class ProgressBar(base):
            def __init__(self, *args, **kwargs):
                self._model_count = kwargs.get("initial", 0)
                self._model_bytes = kwargs.get("unit") in ("B", "iB")
                super().__init__(*args, **kwargs)
                self._publish()

            def _publish(self):
                check()
                if self._model_bytes:
                    progress.bytes(self._model_count, getattr(self, "total", None),
                                   getattr(self, "desc", ""))

            def update(self, n=1):
                check()
                self._model_count += n or 0
                result = super().update(n)
                self._publish()
                return result

            def refresh(self, *args, **kwargs):
                result = super().refresh(*args, **kwargs)
                self._publish()
                return result
        return ProgressBar

    try:
        check()
        http = importlib.import_module("huggingface_hub.utils._http")
        reset = getattr(http, "close_session", None) or getattr(http, "reset_sessions", None)
        if reset:
            reset()  # Recreate clients using the current proxy, including retries.
        for module_name, attribute in (
            ("huggingface_hub.utils.tqdm", "tqdm"),
            ("huggingface_hub.file_download", "tqdm"),
            ("huggingface_hub._snapshot_download", "hf_tqdm"),
            ("huggingface_hub._snapshot_download", "tqdm"),
        ):
            module = importlib.import_module(module_name)
            if hasattr(module, attribute):
                replace(module, attribute, tracked(getattr(module, attribute)))
        # Request hooks provide cancellation before metadata requests too.
        session = http.get_session()
        hooks = getattr(session, "event_hooks", None)
        hook = lambda request: check()
        if hooks is not None:
            hooks["request"].append(hook)
        if provider == "fastembed":
            module = importlib.import_module("fastembed.common.model_management")
            replace(module, "tqdm", tracked(module.tqdm))
            original_get = module.requests.get
            def get(*args, **kwargs):
                check()
                kwargs.setdefault("timeout", (10, 10))
                return original_get(*args, **kwargs)
            replace(module.requests, "get", get)
        yield
        check()
    finally:
        for module, name, original in reversed(patches):
            setattr(module, name, original)
        if "hooks" in locals() and hooks is not None and hook in hooks["request"]:
            hooks["request"].remove(hook)
        if "reset" in locals() and reset:
            reset()
