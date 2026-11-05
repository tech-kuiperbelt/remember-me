"""Opt-in startup diagnostics; imports acquire no files or runtime resources."""
import json
import logging
import os
import time
from datetime import datetime

_origin = None
_run_id = None
_cmd_time = None
_cmd_gap_ms = None


def begin(origin, cmd_time=None):
    global _origin, _run_id, _cmd_time, _cmd_gap_ms
    if _origin is not None:
        return
    _origin = origin
    _run_id = f'{os.getpid()}-{time.time_ns()}'
    _cmd_time = cmd_time
    if cmd_time:
        try:
            shell = datetime.strptime(cmd_time.strip().replace(',', '.'), '%H:%M:%S.%f')
            entered = datetime.fromtimestamp(time.time() - (time.perf_counter() - origin))
            seconds = lambda d: d.hour * 3600 + d.minute * 60 + d.second + d.microsecond / 1e6
            _cmd_gap_ms = ((seconds(entered) - seconds(shell)) % 86400) * 1000
        except ValueError:
            pass  # Keep the raw shell time if its locale format is unfamiliar.


def mark(stage, *, at=None, since=None, **fields):
    path = os.environ.get('REMEMBER_ME_STARTUP_LOG')
    if not path:
        return
    try:
        now = time.perf_counter()
        point = now if at is None else at
        begin(point)
        event = {'timestamp': datetime.fromtimestamp(time.time() - (now - point)).astimezone().isoformat(timespec='milliseconds'),
                 'pid': os.getpid(), 'run_id': _run_id, 'stage': stage,
                 'elapsed_ms': round((point - _origin) * 1000, 3), **fields}
        if since is not None:
            event['duration_ms'] = round((point - since) * 1000, 3)
        if _cmd_time is not None:
            event['cmd_time'] = _cmd_time
        if _cmd_gap_ms is not None:
            event['cmd_to_python_wall_ms'] = round(_cmd_gap_ms, 3)
            event['since_cmd_wall_ms'] = round(_cmd_gap_ms + (point - _origin) * 1000, 3)
        # The Beta entrypoint creates this directory; regular imports never do.
        with open(path, 'a', encoding='utf-8') as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + '\n')
    except Exception:
        logging.getLogger(__name__).warning('Could not write startup timing', exc_info=True)
