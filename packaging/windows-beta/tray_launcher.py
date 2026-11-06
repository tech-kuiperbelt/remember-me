"""Windows Beta tray and owned service lifecycle, using only the standard library.

Run with the bundled pythonw.exe -I -B. Win32 imports acquire no resources until
main(); database writer locks remain independently owned by the application.
"""
import time

_LAUNCHER_ENTRY = time.perf_counter()

from contextlib import contextmanager
import ctypes
from ctypes import wintypes as W
import http.client
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import threading
import webbrowser


_LAUNCHER_IMPORT_DONE = time.perf_counter()


class LauncherError(RuntimeError):
    pass


def data_directory(environment=None):
    env = os.environ if environment is None else environment
    selected = env.get('REMEMBER_ME_BETA_DATA_DIR')
    if not selected and not env.get('LOCALAPPDATA'):
        raise LauncherError('LOCALAPPDATA is missing; configure REMEMBER_ME_BETA_DATA_DIR.')
    data = Path(selected) if selected else Path(env['LOCALAPPDATA']) / 'remember-me-beta'
    if not data.is_absolute():
        raise LauncherError('Beta data directory must be absolute.')
    return data.resolve()


class Windows:
    """Small explicit Win32 boundary: token, mutexes, jobs and listener identity."""

    def __init__(self):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.advapi = ctypes.WinDLL('advapi32', use_last_error=True)
        k = self.kernel
        signatures = {
            'GetCurrentProcess': ([], W.HANDLE),
            'CloseHandle': ([W.HANDLE], W.BOOL),
            'OpenProcess': ([W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
            'GetExitCodeProcess': ([W.HANDLE, ctypes.POINTER(W.DWORD)], W.BOOL),
            'LocalFree': ([W.HANDLE], W.HANDLE),
            'CreateMutexW': ([ctypes.c_void_p, W.BOOL, W.LPCWSTR], W.HANDLE),
            'OpenMutexW': ([W.DWORD, W.BOOL, W.LPCWSTR], W.HANDLE),
            'CreateJobObjectW': ([ctypes.c_void_p, W.LPCWSTR], W.HANDLE),
            'AssignProcessToJobObject': ([W.HANDLE, W.HANDLE], W.BOOL),
            'QueryInformationJobObject': ([W.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                           W.DWORD, ctypes.POINTER(W.DWORD)], W.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(k, name)
            function.argtypes, function.restype = args, result
        self.advapi.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, ctypes.POINTER(W.HANDLE)]
        self.advapi.OpenProcessToken.restype = W.BOOL
        self.advapi.GetTokenInformation.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                   W.DWORD, ctypes.POINTER(W.DWORD)]
        self.advapi.GetTokenInformation.restype = W.BOOL
        self.advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(W.LPWSTR)]
        self.advapi.ConvertSidToStringSidW.restype = W.BOOL
        self.sid = self._sid()

    def _sid(self):
        token = W.HANDLE()
        if not self.advapi.OpenProcessToken(self.kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            size = W.DWORD()
            self.advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            buffer = ctypes.create_string_buffer(size.value)
            if not self.advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
            text = W.LPWSTR()
            if not self.advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                return text.value
            finally:
                self.kernel.LocalFree(ctypes.cast(text, W.HANDLE))
        finally:
            self.close(token)

    def name(self, kind):
        return 'Global\\RememberMeBeta' + kind + '-v1-' + self.sid

    def acquire(self, kind):
        ctypes.set_last_error(0)
        handle = self.kernel.CreateMutexW(None, False, self.name(kind))
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:
            self.close(handle)
            raise LauncherError('Remember Me is already running. Use its tray or Web interface.')
        # Hold our presence before checking installer presence: both sides fail
        # closed if they race. Existence, not thread ownership, is the contract.
        installer = self.kernel.OpenMutexW(0x100000, False, self.name('Install'))
        error = ctypes.get_last_error()
        if installer:
            self.close(installer)
            self.close(handle)
            raise LauncherError('Remember Me installation or removal is in progress.')
        if error not in (0, 2):
            self.close(handle)
            raise ctypes.WinError(error)
        return handle

    def close(self, handle):
        if handle:
            self.kernel.CloseHandle(handle)

    def watch(self, pid):
        handle = self.kernel.OpenProcess(0x1000 | 0x100000, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def exit_code(self, handle):
        value = W.DWORD()
        if not self.kernel.GetExitCodeProcess(handle, ctypes.byref(value)):
            raise ctypes.WinError(ctypes.get_last_error())
        return value.value

    def job(self):
        handle = self.kernel.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        # Deliberately no KILL_ON_JOB_CLOSE: a Launcher failure must not abruptly
        # terminate business writes. The service's own guard and writer locks stay.
        return handle

    def assign(self, job, process):
        if not self.kernel.AssignProcessToJobObject(job, W.HANDLE(int(process._handle))):
            raise ctypes.WinError(ctypes.get_last_error())

    def pids(self, job):
        count = 64
        while count <= 65536:
            buffer = ctypes.create_string_buffer(8 + count * ctypes.sizeof(ctypes.c_size_t))
            if self.kernel.QueryInformationJobObject(job, 3, buffer, len(buffer), None):
                length = struct.unpack_from('=II', buffer)[1]
                return set((ctypes.c_size_t * length).from_buffer(buffer, 8))
            if ctypes.get_last_error() != 234:
                raise ctypes.WinError(ctypes.get_last_error())
            count *= 2
        raise LauncherError('Too many owned processes to inspect safely.')

    def listener_pid(self, port):
        function = ctypes.WinDLL('iphlpapi', use_last_error=True).GetExtendedTcpTable
        function.argtypes = [ctypes.c_void_p, ctypes.POINTER(W.DWORD), W.BOOL,
                             W.DWORD, ctypes.c_int, W.DWORD]
        function.restype = W.DWORD
        size = W.DWORD()
        result = function(None, ctypes.byref(size), False, 2, 3, 0)
        for _ in range(4):
            if result != 122:
                raise ctypes.WinError(result)
            buffer = ctypes.create_string_buffer(size.value)
            result = function(buffer, ctypes.byref(size), False, 2, 3, 0)
            if result == 0:
                break
        else:
            raise LauncherError('TCP listener table changed too quickly to inspect.')
        rows = struct.unpack_from('=I', buffer)[0]
        for offset in range(4, 4 + rows * 24, 24):
            _, address, local_port, _, _, pid = struct.unpack_from('=6I', buffer, offset)
            if socket.ntohs(local_port & 65535) == port and address == 0x0100007f:
                return pid
        return None


@contextmanager
def service_guard():
    """Independent lifetime presence for direct Beta launches and installation."""
    api = Windows()
    handle = api.acquire('Service')
    try:
        yield
    finally:
        api.close(handle)


class Controller:
    def __init__(self, root, api, *, popen=subprocess.Popen, browser=webbrowser.open):
        self.root, self.api = Path(root).resolve(), api
        self.data = data_directory()
        self.log_path = self.data / 'logs/launcher-service.log'
        self.popen, self.browser = popen, browser
        self.instance = self.job = self.process = self.stream = None
        self.state, self.error, self.port = 'Starting', None, 1314
        self.language = 'zh-CN'
        self.autostart = None
        self.autostart_enabled = False
        self.stopping = False
        self._shutdown_sent = False
        self._reported_model_error = False
        self._listener_handles = {}
        self._last_listener = None
        self._restart_pending = False

    def start(self):
        self.instance = self.api.acquire('Launcher')
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        # Read-only configuration; no model import, DB access or migration.
        sys.path.insert(0, str(self.root))
        from app.config.configuration import Configuration
        from app.startup_timing import mark
        mark("launcher_service_start_begin")
        config = Configuration.load(self.data, create=False)
        self.port, self.language = config.port, config.language
        if not self.port:
            raise LauncherError('Tray requires a configured port from 1 to 65535.')
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', self.port))
            except OSError as exc:
                raise LauncherError(f'Port {self.port} is in use. The existing service was not changed.') from exc
        self.stream = self.log_path.open('ab', buffering=0)
        self.job = self.api.job()
        environment = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
        command = [str(self.root / 'runtime/python/python.exe'), '-I', '-B',
                   str(self.root / 'tray_launcher.py'), '--service-child']
        mark("launcher_child_spawn_begin")
        self.process = self.popen(command, cwd=self.root, env=environment,
                                  stdin=subprocess.PIPE, stdout=self.stream, stderr=subprocess.STDOUT,
                                  creationflags=0x08000000, close_fds=True)
        try:
            # Child waits on stdin before importing business code, so all restart
            # descendants belong to this job before any service can be started.
            self.api.assign(self.job, self.process)
            self.process.stdin.write(b'G')
            self.process.stdin.flush()
            mark('launcher_child_spawn_done')
        finally:
            self.process.stdin.close()

    def _owned_listener(self):
        pid = self.api.listener_pid(self.port)
        if pid not in self.api.pids(self.job):
            return False
        if pid not in self._listener_handles:
            self._listener_handles[pid] = self.api.watch(pid)
        self._last_listener = pid
        return True

    def request(self, path, method='GET'):
        if not self._owned_listener():
            raise LauncherError('The listener is not an owned Remember Me process; no control request sent.')
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=1)
        try:
            connection.connect()
            if not self._owned_listener():
                raise LauncherError('Listener ownership changed before request; no control request sent.')
            # No environment proxy; standard loopback Host satisfies existing policy.
            connection.request(method, path, body=b'{}' if method == 'POST' else None,
                               headers={'Content-Type': 'application/json'})
            response = connection.getresponse()
            data = json.loads(response.read())
            if response.status >= 400:
                raise LauncherError(f'{path} returned HTTP {response.status}: {data}')
            return data
        finally:
            connection.close()

    def poll(self):
        if not self.job:
            return self.state
        pids = self.api.pids(self.job)
        if not pids:
            code = (self.api.exit_code(self._listener_handles[self._last_listener])
                    if self._last_listener is not None else self.process.poll())
            if self._restart_pending:
                code = None  # Observed restart descendants never reached listening.
            self.state = 'Stopped' if code == 0 else 'Error'
            if self.state == 'Error':
                self.error = f'Service exited unexpectedly ({code}); see {self.log_path}'
            return self.state
        # A Windows os.execv restart changes PID. Job membership survives it;
        # don't mistake the original Popen exit for the service's final exit.
        owned = self._owned_listener()
        if not owned:
            # Config changes take effect on Web Restart, including its port.
            # Read-only load never creates config, migrates data or opens SQLite.
            from app.config.configuration import Configuration
            candidate = Configuration.load(self.data, create=False).port
            if candidate and candidate != self.port:
                self.port = candidate
                owned = self._owned_listener()
        if not owned:
            if self._last_listener is not None and self._last_listener not in pids:
                self._restart_pending = True
            self.state = 'Stopping' if self.stopping else 'Starting'
            return self.state
        self._restart_pending = False
        if self.state == 'Starting':
            from app.startup_timing import mark
            mark('launcher_http_observed', service_pid=self._last_listener, port=self.port)
        if self.stopping:
            self.state = 'Stopping'
            if not self._shutdown_sent:
                maintenance = self.request('/api/maintenance')
                if maintenance.get('state') == 'normal':
                    self.request('/api/maintenance/enter', 'POST')
                    maintenance = self.request('/api/maintenance')
                if maintenance.get('state') != 'maintenance' or maintenance.get('action'):
                    return self.state  # Wait for entered maintenance / active work.
                self.request('/api/shutdown', 'POST')
                self._shutdown_sent = True
            return self.state
        model = self.request('/api/model')
        maintenance = self.request('/api/maintenance')
        self.state = 'Running' if model.get('ready') and maintenance.get('state') == 'normal' else 'Maintenance'
        if model.get('status') == 'failed':
            self.error = f'Model initialization failed: {model.get("error")}; see {self.log_path}'
        return self.state

    def refresh_preferences(self):
        from app.config.configuration import Configuration
        try:
            self.language = Configuration.load(self.data, create=False).language
        except (OSError, ValueError):
            logging.exception('Could not refresh tray language; keeping previous setting')
        try:
            if self.autostart is None:
                from windows_autostart import Autostart
                self.autostart = Autostart(self.root, self.data)
            self.autostart_enabled = self.autostart.enabled()
        except (OSError, RuntimeError, ImportError):
            logging.exception('Could not read login startup setting')
            self.autostart_enabled = False

    def toggle_autostart(self):
        if self.stopping:
            return
        if self.autostart is None:
            from windows_autostart import Autostart
            self.autostart = Autostart(self.root, self.data)
        self.autostart.set_enabled(not self.autostart.enabled())
        self.autostart_enabled = self.autostart.enabled()

    def open(self, fragment=''):
        if fragment not in ('', '#memories', '#stats', '#ops', '#help'):
            raise LauncherError('Unknown Remember Me page.')
        if not self.stopping and self.job and self._owned_listener():
            url = f'http://127.0.0.1:{self.port}/admin/console' + fragment
            if self.browser(url) is False:
                raise LauncherError(f'Could not open the default browser. Open this URL: {url}')
            return True
        return False

    def stop(self):
        self.stopping = True
        self.state = 'Stopping'

    def close(self):
        if self.job and self.api.pids(self.job):
            raise LauncherError('Owned service is still running; wait for graceful shutdown.')
        if self.process:
            self.process.wait()
        if self.stream:
            self.stream.close()
        for handle in self._listener_handles.values():
            self.api.close(handle)
        self._listener_handles.clear()
        self.api.close(self.job)
        self.api.close(self.instance)
        self.job = self.instance = None


def _service_child():
    if sys.stdin.buffer.read(1) != b'G':
        raise LauncherError('Launcher did not grant the owned process start handshake.')
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import beta_start
    # Bind restriction is explicit even if a user previously saved LAN settings.
    sys.argv = [str(Path(__file__).with_name('beta_start.py')), '--host', '127.0.0.1']
    beta_start.main()


def main():
    if sys.platform != 'win32':
        raise LauncherError('The tray launcher requires Windows.')
    if sys.argv[1:] == ['--service-child']:
        _service_child()
        return
    import argparse
    parser = argparse.ArgumentParser(description='Remember Me Windows tray launcher')
    parser.add_argument('--data-dir', help='Absolute existing data-root selection; never migrates data')
    args = parser.parse_args()
    if args.data_dir is not None:
        selected = data_directory({'REMEMBER_ME_BETA_DATA_DIR': args.data_dir})
        os.environ['REMEMBER_ME_BETA_DATA_DIR'] = str(selected)
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root))
    logs = data_directory() / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(logs / 'launcher.log', maxBytes=2*1024*1024,
                                  backupCount=2, encoding='utf-8')
    os.environ['REMEMBER_ME_STARTUP_LOG'] = str(logs / 'startup-timing.jsonl')
    from app.startup_timing import begin, mark
    begin(_LAUNCHER_ENTRY)
    mark('launcher_entry', at=_LAUNCHER_ENTRY)
    mark('launcher_import_done', at=_LAUNCHER_IMPORT_DONE, since=_LAUNCHER_ENTRY)
    logging.basicConfig(handlers=[handler], level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    from windows_tray import Tray, alert
    controller = Controller(root, Windows())
    tray = Tray(controller)
    try:
        tray.run()
    except Exception as exc:
        logging.exception('Launcher failed')
        alert(f'{exc}\n\nLogs: {logs}')
        # Never abruptly kill a live business child after a GUI failure.
        if controller.job:
            controller.stop()
            while controller.api.pids(controller.job):
                try:
                    controller.poll()
                except Exception:
                    logging.exception('Waiting for safe shutdown; use Web maintenance/shutdown if needed')
                time.sleep(1)
    finally:
        controller.close()
        handler.close()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # pythonw has no stderr; failures before logging is configured still need
        # a visible explanation (and use the default temp log as last resort).
        import tempfile
        import traceback
        path = Path(tempfile.gettempdir()) / 'remember-me-beta-launcher-error.log'
        with path.open('a', encoding='utf-8') as stream:
            traceback.print_exc(file=stream)
        if sys.platform == 'win32':
            message = ctypes.WinDLL('user32').MessageBoxW
            message.argtypes = [W.HWND, W.LPCWSTR, W.LPCWSTR, W.UINT]
            message.restype = ctypes.c_int
            message(None, f'{traceback.format_exc()}\nLog: {path}', 'Remember Me Beta', 0x10)
        raise
