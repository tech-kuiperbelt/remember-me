"""Process-lifetime writer locks for SQLite primary databases."""

import fcntl
import os


def canonical_database_path(path: str) -> str:
    """Return the stable absolute path used to identify one SQLite primary."""
    return os.path.realpath(os.path.abspath(path))


class DatabaseWriterLock:
    """An advisory non-blocking exclusive lock for one SQLite primary path."""

    def __init__(self, database_path: str):
        self.database_path = canonical_database_path(database_path)
        self.lock_path = self.database_path + ".writer.lock"
        self._handle = None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        os.makedirs(os.path.dirname(self.lock_path) or ".", exist_ok=True)
        handle = open(self.lock_path, "a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise RuntimeError(
                f"SQLite primary already has an active remember-me writer: "
                f"{self.database_path}; connect clients to the single HTTP service "
                "or stop the current writer before starting another instance"
            ) from exc
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, _type, _value, _traceback):
        self.release()
