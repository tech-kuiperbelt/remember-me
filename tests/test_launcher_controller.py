"""Owned-process and isolation contracts, using a tiny real local child service."""
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request

import pytest

from launcher.controller import (
    LauncherError, ServiceController, clean_environment, default_data_dir,
)


FAKE_SERVICE = r'''
import argparse,json,os,signal,sys
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
parser=argparse.ArgumentParser()
parser.add_argument('--data-dir',type=Path,required=True)
parser.add_argument('--port',type=int)
args=parser.parse_args()
args.data_dir.mkdir(parents=True,exist_ok=True)
config=args.data_dir/'config.jsonc'
port=args.port or (json.loads(config.read_text()).get('port',1314) if config.exists() else 1314)
def stop(sig,frame):
    (args.data_dir/'closed').write_text(str(sig))
    raise SystemExit(0)
signal.signal(signal.SIGINT,stop)
signal.signal(signal.SIGTERM,stop)
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        maintenance=(args.data_dir/'maintenance').exists()
        data={'ready':True} if self.path=='/api/model' else {'state':'maintenance' if maintenance else 'normal'}
        body=json.dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()
        if self.path=='/restart':
            print('INFO: Shutting down',flush=True)
            os.execv(sys.executable,[sys.executable,__file__,*sys.argv[1:]])
        if self.path=='/crash':
            raise SystemExit(7)
server=HTTPServer(('127.0.0.1',port),Handler)
print('INFO: Started server process ['+str(os.getpid())+']',flush=True)
print('INFO: Uvicorn running on http://127.0.0.1:'+str(port),flush=True)
try:server.serve_forever(poll_interval=.05)
finally:server.server_close()
'''


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def wait_for(condition, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = condition()
        if result:
            return result
        time.sleep(.03)
    raise AssertionError('Owned test service did not reach the expected state')


def get(port, path):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f'http://127.0.0.1:{port}{path}', timeout=.3) as response:
        return json.load(response)


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / 'runner with spaces'
    binary = root / 'runtime/python/bin/python3'
    binary.parent.mkdir(parents=True)
    binary.symlink_to(sys.executable)
    script = root / 'fake.py'
    script.write_text(FAKE_SERVICE)
    start = root / 'start.sh'
    start.write_text(f'#!/bin/sh\nexec "{sys.executable}" -u "{script}" "$@"\n')
    start.chmod(0o755)
    runtime = tmp_path / 'session'
    runtime.mkdir()
    env = {**os.environ, 'HOME': str(tmp_path / 'home'),
           'XDG_RUNTIME_DIR': str(runtime), 'XDG_DATA_HOME': str(tmp_path / 'xdg-data')}
    return root, tmp_path / 'data', env


def test_default_and_child_environment_are_independent(setup):
    root, data, env = setup
    assert default_data_dir(env) == Path(env['XDG_DATA_HOME']) / 'remember-me-launcher'
    env.update(MEMORY_DATA_DIR='/existing/source/data', MEMORY_STORAGE_PATH='/existing/db',
               EMBED_PROVIDER='ollama', GI_TYPELIB_PATH='/private/gui',
               LD_LIBRARY_PATH='/private/gui/lib', DISPLAY=':99')
    cleaned = clean_environment(env)
    assert not set(cleaned) & {'MEMORY_DATA_DIR', 'MEMORY_STORAGE_PATH', 'EMBED_PROVIDER',
                               'GI_TYPELIB_PATH', 'LD_LIBRARY_PATH'}
    assert cleaned['DISPLAY'] == ':99'
    controller = ServiceController(root, data, free_port(), environment=env)
    assert controller.poll()['state'] == 'Stopped'
    assert not data.exists()


def test_port_conflict_does_not_touch_data_or_other_service(setup):
    root, data, env = setup
    with socket.socket() as existing:
        existing.bind(('127.0.0.1', 0))
        existing.listen()
        controller = ServiceController(root, data, existing.getsockname()[1], environment=env)
        with pytest.raises(LauncherError, match='Port .* unavailable'):
            controller.start()
        assert existing.getsockname()[1] > 0
        assert controller.poll()['pid'] is None
        assert not data.exists()


def test_existing_writer_is_not_opened_or_stopped(setup):
    root, data, env = setup
    data.mkdir()
    database = data / 'memories.db'
    database.write_bytes(b'unchanged test placeholder')
    with Path(str(database) + '.writer.lock').open('a+') as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        controller = ServiceController(root, data, free_port(), environment=env)
        with pytest.raises(LauncherError, match='active writer'):
            controller.start()
        assert database.read_bytes() == b'unchanged test placeholder'
        assert sorted(p.name for p in data.iterdir()) == ['memories.db', 'memories.db.writer.lock']


def test_singleton_lives_until_owner_close_and_child_is_reaped(setup):
    root, data, env = setup
    first = ServiceController(root, data, free_port(), environment=env)
    second = ServiceController(root, data.parent / 'other-data', free_port(), environment=env)
    try:
        first.start()
        wait_for(lambda: first.poll()['state'] == 'Running')
        with pytest.raises(LauncherError, match='already running'):
            second.start()
        assert not second.data_dir.exists()
        first.stop()
        assert first.poll()['state'] == 'Stopped'
        assert first.poll()['exit_code'] == 0
        assert (data / 'closed').read_text() == str(signal.SIGINT)
        with pytest.raises(LauncherError, match='already running'):
            second.start()
        first.close()
        second.start()
        wait_for(lambda: second.poll()['state'] == 'Running')
    finally:
        first.close()
        second.close()


def test_maintenance_and_same_pid_exec_follow_configured_port(setup):
    root, data, env = setup
    data.mkdir()
    old_port, new_port = free_port(), free_port()
    config = data / 'config.jsonc'
    config.write_text(json.dumps({'port': old_port}))
    controller = ServiceController(root, data, environment=env)
    try:
        pid = controller.start()['pid']
        wait_for(lambda: controller.poll()['state'] == 'Running')
        (data / 'maintenance').touch()
        assert controller.poll()['state'] == 'Maintenance'
        (data / 'maintenance').unlink()
        config.write_text(json.dumps({'port': new_port}))
        get(old_port, '/restart')
        wait_for(lambda: controller.poll()['state'] == 'Running' and
                 controller.poll()['port'] == new_port)
        assert controller.poll()['pid'] == pid
        assert controller.poll()['url'] == f'http://127.0.0.1:{new_port}'
    finally:
        controller.close()


def test_unexpected_exit_is_reported_without_respawn(setup):
    root, data, env = setup
    port = free_port()
    controller = ServiceController(root, data, port, environment=env)
    try:
        pid = controller.start()['pid']
        wait_for(lambda: controller.poll()['state'] == 'Running')
        get(port, '/crash')
        wait_for(lambda: controller.poll()['state'] == 'Error')
        assert controller.poll()['exit_code'] == 7
        assert controller.poll()['pid'] == pid
    finally:
        controller.close()


def test_start_from_worker_is_rejected_before_acquiring_resources(setup):
    root, data, env = setup
    controller = ServiceController(root, data, free_port(), environment=env)
    errors = []
    def attempt():
        try:
            controller.start()
        except LauncherError as exc:
            errors.append(str(exc))
    worker = threading.Thread(target=attempt)
    worker.start()
    worker.join()
    assert errors and 'main thread' in errors[0]
    assert not data.exists()


def test_reader_startup_failure_cleans_process_and_releases_lock(setup, monkeypatch):
    root, data, env = setup
    controller = ServiceController(root, data, free_port(), environment=env)
    original = threading.Thread.start
    def fail_after_thread_started(thread):
        original(thread)
        if thread.name == 'launcher-service-output':
            raise RuntimeError('test reader startup failure')
    with monkeypatch.context() as patch:
        patch.setattr(threading.Thread, 'start', fail_after_thread_started)
        with pytest.raises(LauncherError, match='reader startup failure'):
            controller.start()
    assert controller.poll()['state'] == 'Error'
    assert controller.poll()['exit_code'] is not None
    assert 'reader startup failure' in controller.poll()['error']
    successor = ServiceController(root, data, controller.port, environment=env)
    try:
        successor.start()
        wait_for(lambda: successor.poll()['state'] == 'Running')
    finally:
        successor.close()


def test_child_rejects_a_parent_that_changed_before_notification(setup):
    root, data, env = setup
    child = Path(__file__).resolve().parents[1] / 'launcher/child.py'
    proc = subprocess.run([sys.executable, '-I', '-B', str(child),
                           str(os.getpid() + 100000), str(root / 'start.sh'),
                           '--data-dir', str(data)], capture_output=True, text=True, env=env)
    assert proc.returncode != 0
    assert 'Owning Launcher exited' in proc.stderr
    assert not data.exists()


def test_parent_death_notifies_child_after_exec_and_does_not_leak_lock(setup):
    root, data, env = setup
    port = free_port()
    source = Path(__file__).resolve().parents[1]
    owner_code = (
        'import json,time; from launcher.controller import ServiceController; '
        f'c=ServiceController({str(root)!r},{str(data)!r},{port}); '
        'print(json.dumps(c.start()),flush=True); time.sleep(300)')
    owner = subprocess.Popen([sys.executable, '-u', '-c', owner_code], cwd=source,
                             env={**env, 'PYTHONPATH': str(source)},
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    child_pid = None
    try:
        snapshot = json.loads(owner.stdout.readline())
        child_pid = snapshot['pid']
        def ready():
            try:
                return get(port, '/api/model')['ready']
            except OSError:
                return False
        wait_for(ready)
        get(port, '/restart')
        wait_for(ready)
        owner.kill()
        owner.wait(timeout=5)
        wait_for(lambda: (data / 'closed').exists())
        assert (data / 'closed').read_text() == str(signal.SIGTERM)
        def exited():
            try:
                return Path(f'/proc/{child_pid}/stat').read_text().split()[2] == 'Z'
            except FileNotFoundError:
                return True
        wait_for(exited)
        # The service must not inherit and retain its Launcher's singleton FD.
        successor = ServiceController(root, data.parent / 'successor', port, environment=env)
        try:
            successor.start()
            wait_for(lambda: successor.poll()['state'] == 'Running')
        finally:
            successor.close()
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.wait(timeout=5)
        owner.stdout.close()
        owner.stderr.close()
