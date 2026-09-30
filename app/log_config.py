"""Logging is initialized explicitly; importing this module does not open files."""
import sys
import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler


def configure(log_dir=None):
    directory = Path(log_dir) if log_dir is not None else Path(__file__).resolve().parent.parent / "data" / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    handlers = []
    try:
        handlers.append(logging.StreamHandler(sys.stderr))
        handlers.append(RotatingFileHandler(directory / "memory-server.log", maxBytes=10 * 1024 * 1024,
                                           backupCount=5, encoding="utf-8"))
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        for handler in handlers:
            handler.setFormatter(formatter)
            root.addHandler(handler)
        return handlers
    except BaseException:
        close(handlers)
        raise


def close(handlers):
    root = logging.getLogger()
    for handler in handlers:
        root.removeHandler(handler)
        handler.close()
