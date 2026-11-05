"""Lifecycle contract tests with a real loopback HTTP service and fake Win32 IDs.

These tests do not claim Windows GUI, mutex, job or DLL validation on Linux.
"""
import importlib.util
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'packaging/windows-beta/tray_launcher.py'
spec = importlib.util.spec_from_file_location('windows_beta_tray_test', SOURCE)
tray = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tray)


class FakeWindows:
    def __init__(self):
        self.active = {101}
        self.listener = 101
        self.closed = []
        self.codes = {101: 0, 202: 0}
        self.duplicate = False

    def acquire(self, kind):
        if self.duplicate:
            raise tray.LauncherError('already running')
        return kind

    def close(self, handle):
        self.closed.append(handle)

    def pids(self, job):
        return self.active.copy()

    def listener_pid(self, port):
        return self.listener

    def job(self):
        return 'job'

    def assign(self, job, process):
        self.assigned = True

    def watch(self, pid):
        return pid

    def exit_code(self, handle):
        return self.codes[handle]


class FakeProcess:
    def __init__(self):
        self.stdin = io.BytesIO()
        self.returncode = 0

    def poll(self):
        return self.returncode

    def wait(self):
        return self.returncode


@pytest.fixture
def controller(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.delenv('REMEMBER_ME_BETA_DATA_DIR', raising=False)
    api = FakeWindows()
    value = tray.Controller(SOURCE.parents[2], api)
    value.job, value.instance, value.process = 'job', 'Launcher', FakeProcess()
    return value


@pytest.fixture
def service(controller):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(('GET', self.path))
            self.send_response(200)
            self.end_headers()
            value = ({'state': self.server.maintenance, 'action': self.server.action}
                     if self.path == '/api/maintenance' else self.server.model)
            self.wfile.write(json.dumps(value).encode())

        def do_POST(self):
            requests.append(('POST', self.path))
            self.rfile.read(int(self.headers.get('Content-Length', 0)))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{}')
            if self.path == '/api/maintenance/enter':
                self.server.maintenance = 'maintenance'
            if self.path == '/api/shutdown':
                controller.api.active.clear()

        def log_message(self, *args):
            pass

    http = HTTPServer(('127.0.0.1', 0), Handler)
    http.model = {'ready': True, 'status': 'ready'}
    http.maintenance, http.action = 'normal', None
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    controller.port = http.server_port
    try:
        yield http, requests
    finally:
        http.shutdown()
        thread.join()
        http.server_close()


def test_real_loopback_open_exit_and_release(controller, service):
    opened = []
    controller.browser = opened.append
    assert controller.poll() == 'Running'
    assert controller.open()
    assert opened == [f'http://127.0.0.1:{controller.port}/admin/console']
    controller.stop()
    assert controller.poll() == 'Stopping'
    assert [item for item in service[1] if item[0] == 'POST'] == [
        ('POST', '/api/maintenance/enter'), ('POST', '/api/shutdown')]
    assert controller.poll() == 'Stopped'
    controller.close()
    assert 'Launcher' in controller.api.closed


def test_unknown_listener_never_receives_control(controller, service):
    controller.api.listener = 999
    controller.stop()
    assert controller.poll() == 'Stopping'
    with pytest.raises(tray.LauncherError, match='not an owned'):
        controller.request('/api/shutdown', 'POST')
    assert service[1] == []
    assert not controller.open()


def test_restart_new_pid_is_owned_and_abnormal_exit_reported(controller, service):
    assert controller.poll() == 'Running'
    # Windows exec starts another process; the original Popen already exited 0.
    controller.api.active = {202}
    controller.api.listener = 202
    assert controller.poll() == 'Running'
    controller.api.codes[202] = 126
    controller.api.active.clear()
    assert controller.poll() == 'Error'
    assert '(126)' in controller.error


def test_web_shutdown_removes_running_state(controller, service):
    assert controller.poll() == 'Running'
    controller.api.active.clear()
    assert controller.poll() == 'Stopped'


def test_model_failure_keeps_diagnostics_available(controller, service):
    service[0].model = {'ready': False, 'status': 'failed', 'error': 'c10.dll unavailable'}
    assert controller.poll() == 'Maintenance'
    assert 'c10.dll' in controller.error
    controller.browser = lambda _: None
    assert controller.open()


def test_exit_already_in_maintenance_skips_non_idempotent_enter(controller, service):
    service[0].maintenance = 'maintenance'
    controller.stop()
    controller.poll()
    assert ('POST', '/api/maintenance/enter') not in service[1]
    assert ('POST', '/api/shutdown') in service[1]


def test_exit_waits_for_active_action_then_shuts_down(controller, service):
    service[0].maintenance, service[0].action = 'maintenance', 'import'
    controller.stop()
    assert controller.poll() == 'Stopping'
    assert not any(method == 'POST' for method, _ in service[1])
    service[0].action = None
    controller.poll()
    assert ('POST', '/api/shutdown') in service[1]


def test_enter_response_timeout_retries_using_actual_maintenance(controller, service):
    request = controller.request
    failed = False

    def delayed(path, method='GET'):
        nonlocal failed
        value = request(path, method)
        if path == '/api/maintenance/enter' and not failed:
            failed = True
            raise TimeoutError('response delayed after server entered maintenance')
        return value

    controller.request = delayed
    controller.stop()
    with pytest.raises(TimeoutError):
        controller.poll()
    controller.poll()
    assert service[1].count(('POST', '/api/maintenance/enter')) == 1
    assert ('POST', '/api/shutdown') in service[1]


def test_initial_child_failure_is_error(controller):
    controller.api.active.clear()
    controller.process.returncode = 126
    assert controller.poll() == 'Error'
    assert '126' in controller.error


def test_requested_exit_does_not_hide_nonzero_child_failure(controller):
    controller.stop()
    controller.api.active.clear()
    controller.process.returncode = 126
    assert controller.poll() == 'Error'
    assert '126' in controller.error


def test_restart_refreshes_changed_config_port(controller, service):
    assert controller.poll() == 'Running'
    new_port = controller.port + 1 if controller.port < 65535 else 1314
    controller.data.mkdir(parents=True)
    (controller.data / 'config.jsonc').write_text(json.dumps({'port': new_port}), encoding='utf-8')
    controller.api.active = {202}
    controller.api.listener_pid = lambda port: 202 if port == new_port else None
    controller.request = lambda *args: {'ready': True}
    assert controller.poll() == 'Running'
    assert controller.port == new_port


def test_observed_restart_fails_before_listening_is_not_clean_shutdown(controller, service):
    assert controller.poll() == 'Running'
    controller.api.active, controller.api.listener = {202}, None
    assert controller.poll() == 'Starting'
    controller.api.active.clear()
    assert controller.poll() == 'Error'


def test_close_refuses_to_release_running_instance(controller):
    with pytest.raises(tray.LauncherError, match='still running'):
        controller.close()
    assert not controller.api.closed


def test_duplicate_start_never_spawns(controller):
    controller.api.duplicate = True
    controller.popen = lambda *a, **kw: pytest.fail('duplicate must not spawn')
    with pytest.raises(tray.LauncherError, match='already running'):
        controller.start()


def test_start_handshake_after_assignment_and_private_python(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.delenv('REMEMBER_ME_BETA_DATA_DIR', raising=False)
    data = tmp_path / 'remember-me-beta'
    data.mkdir()
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    (data / 'config.jsonc').write_text(json.dumps({'port': port}), encoding='utf-8')
    api = FakeWindows()

    class Input(io.BytesIO):
        def write(self, value):
            assert api.assigned
            assert value == b'G'
            return super().write(value)

    process = FakeProcess()
    process.stdin = Input()
    captured = {}

    def popen(command, **kwargs):
        captured.update(command=command, **kwargs)
        return process

    value = tray.Controller(SOURCE.parents[2], api, popen=popen)
    value.start()
    assert captured['command'][0].endswith('runtime/python/python.exe')
    assert captured['creationflags'] == 0x08000000
    assert captured['stdin'] == subprocess.PIPE
    assert value.port == port
    api.active.clear()
    value.close()


def test_data_override_absolute_and_no_implicit_migration(tmp_path):
    custom = tmp_path / '中文 data'
    assert tray.data_directory({'REMEMBER_ME_BETA_DATA_DIR': str(custom)}) == custom
    with pytest.raises(tray.LauncherError, match='absolute'):
        tray.data_directory({'REMEMBER_ME_BETA_DATA_DIR': 'relative'})


@pytest.mark.parametrize('existing,installer,expected', [
    (True, False, 'already running'), (False, True, 'installation or removal'),
])
def test_actual_mutex_guard_rejects_duplicate_and_installer(monkeypatch, existing, installer, expected):
    # Call production guard logic; Win32 return values are simulated explicitly.
    api = tray.Windows.__new__(tray.Windows)
    api.sid = 'S-1-5-21-test'
    errors = {'value': 0}

    class Kernel:
        def CreateMutexW(self, security, owned, name):
            assert name == 'Global\\RememberMeBetaLauncher-v1-S-1-5-21-test'
            assert not owned
            errors['value'] = 183 if existing else 0
            return 10

        def OpenMutexW(self, access, inherit, name):
            assert name == 'Global\\RememberMeBetaInstall-v1-S-1-5-21-test'
            errors['value'] = 0 if installer else 2
            return 20 if installer else 0

        def CloseHandle(self, handle):
            closed.append(handle)

    closed = []
    api.kernel = Kernel()
    monkeypatch.setattr(tray.ctypes, 'set_last_error', lambda value: errors.update(value=value), raising=False)
    monkeypatch.setattr(tray.ctypes, 'get_last_error', lambda: errors['value'], raising=False)
    with pytest.raises(tray.LauncherError, match=expected):
        api.acquire('Launcher')
    assert 10 in closed
    if installer:
        assert 20 in closed


def test_isolated_python_entry_imports_sibling_before_gui(tmp_path):
    # Execute the real main() with -I and replacement GUI/API boundary, proving
    # that sibling imports are available without PYTHONPATH or a system package.
    (tmp_path / 'tray_launcher.py').write_text(SOURCE.read_text(encoding='utf-8'), encoding='utf-8')
    (tmp_path / 'windows_tray.py').write_text('class Tray: pass\ndef alert(x): pass\n', encoding='utf-8')
    (tmp_path / 'app').mkdir()
    (tmp_path / 'app/__init__.py').write_text('', encoding='utf-8')
    (tmp_path / 'app/startup_timing.py').write_text((SOURCE.parents[2] / 'app/startup_timing.py').read_text(encoding='utf-8'), encoding='utf-8')
    script = '''
import importlib.util,sys
spec=importlib.util.spec_from_file_location('entry',sys.argv[1])
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
module.sys.platform='win32'
class Controller:
    job=None
    def __init__(self,*a): pass
    def close(self): pass
module.Windows=lambda: object()
module.Controller=Controller
try: module.main()
except TypeError as exc:
    assert "Tray() takes no arguments" in str(exc),str(exc)
else: raise AssertionError('fixture GUI should reject init after sibling import')
'''
    result = subprocess.run([sys.executable, '-I', '-c', script, str(tmp_path / 'tray_launcher.py')],
                            capture_output=True, text=True, env=dict(__import__('os').environ,
                            LOCALAPPDATA=str(tmp_path)))
    assert result.returncode == 0, result.stderr
