"""Opt-in phase timings: no memory text, metadata, vectors or namespace values."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import os
import threading
import time
import uuid

_lock = threading.Lock()


def operation_id():
    return uuid.uuid4().hex if os.environ.get('REMEMBER_ME_PERFORMANCE_LOG') else None


@contextmanager
def phase(stage, *, operation_id=None, **fields):
    path = os.environ.get('REMEMBER_ME_PERFORMANCE_LOG')
    if not path:
        yield
        return
    started = time.perf_counter()
    status = 'passed'
    try:
        yield
    except BaseException:
        status = 'failed'
        raise
    finally:
        event = {'timestamp': datetime.now(timezone.utc).isoformat(), 'pid': os.getpid(),
                 'operation_id': operation_id, 'stage': stage, 'status': status,
                 'duration_ms': round((time.perf_counter()-started)*1000, 3), **fields}
        try:
            with _lock, open(path, 'a', encoding='utf-8') as stream:
                stream.write(json.dumps(event, ensure_ascii=False)+'\n')
        except Exception:
            logging.getLogger(__name__).warning('Could not write performance timing', exc_info=True)
