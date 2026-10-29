"""Owned Linux service process for the portable tray launcher; no GUI imports."""
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import threading
import urllib.error
import urllib.request

from app.config.configuration import Configuration


class LauncherError(RuntimeError):
    """A user-facing startup failure; external instances are never taken over."""


def default_data_dir(environment=None):
    env = os.environ if environment is None else environment
    return (Path(env.get('XDG_DATA_HOME') or Path(env.get('HOME') or Path.home()) /
                 '.local/share').expanduser() / 'remember-me-launcher').resolve()


def clean_environment(environment=None):
    """Keep desktop integration but discard inherited service/GUI overrides."""
    env = dict(os.environ if environment is None else environment)
    prefixes = ('MEMORY_', 'EMBED_', 'FASTEMBED_', 'SENTENCE_TRANSFORMERS_',
                'OLLAMA_', 'HF_', 'HUGGINGFACE_', 'TRANSFORMERS_')
    for name in tuple(env):
        if name.startswith(prefixes) or name in (
                'PYTHONHOME', 'PYTHONPATH', 'PYTHONUSERBASE', 'VIRTUAL_ENV',
                'LD_LIBRARY_PATH', 'LD_PRELOAD', 'GI_TYPELIB_PATH', 'GI_OVERRIDES_PATH',
                'GTK_PATH', 'GTK_EXE_PREFIX', 'GTK_DATA_PREFIX', 'TORCH_HOME',
                'REMEMBER_ME_INSTALL_METHOD'):
            env.pop(name, None)
    return env


class ServiceController:
    """Only signal/reap the Popen created here, never a PID found by its port."""

    def __init__(self, root, data_dir, port=None, *, environment=None):
        self.root = Path(root).resolve()
        data = Path(data_dir).expanduser()
        self.data_dir = (data if data.is_absolute() else self.root / data).resolve()
        if port is not None and (type(port) is not int or not 1 <= port <= 65535):
            raise LauncherError('Port must be an integer from 1 to 65535.')
        self.port_override = port
        self.port = port or 1314
        self.environment = clean_environment(environment)
        self.log_path = self.data_dir / 'logs/launcher-service.log'
        self._lock = threading.RLock()
        self._stop_lock = threading.Lock()
        self._instance = None
        self._process = None
        self._reader = None
        self._handler = None
        self._logger = logging.getLogger(f'remember-me.launcher.service.{id(self)}')
        self._logger.propagate = False
        self._logger.setLevel(logging.INFO)
        self._state = 'Stopped'
        self._error = None
        self._last_error_line = None
        self._bound = False
        self._stop_requested = False

    def _acquire_instance(self):
        env = self.environment
        runtime = env.get('XDG_RUNTIME_DIR')
        if runtime and Path(runtime).is_dir():
            location = Path(runtime) / 'remember-me-launcher.lock'
        else:
            home = Path(env.get('HOME') or Path.home())
            state = Path(env.get('XDG_STATE_HOME') or home / '.local/state')
            location = state / 'remember-me-launcher/instance.lock'
        location.parent.mkdir(parents=True, exist_ok=True)
        handle = location.open('a+', encoding='utf-8')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise LauncherError('Remember Me Launcher is already running. '
                                'Use its tray menu; no second service was started.') from exc
        self._instance = handle

    def _preflight(self):
        # Reading config does not create/adopt legacy config or open a database.
        config = Configuration.load(self.data_dir, create=False,
                                    environment=self.environment)
        self.port = self.port_override if self.port_override is not None else config.port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            # Match Uvicorn: previous HTTP connections in TIME_WAIT must not
            # block a normal relaunch; an active listener still rejects bind.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(('127.0.0.1', self.port))
            except OSError as exc:
                raise LauncherError(f'Port {self.port} is unavailable. Choose another --port; '
                                    'the existing service was not changed.') from exc
        # start.sh uses the default primary and discards inherited path overrides.
        primary = (self.data_dir / 'memories.db').resolve()
        writer = Path(str(primary) + '.writer.lock')
        if writer.exists():
            with writer.open('a+', encoding='utf-8') as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise LauncherError(f'Data directory already has an active writer: '
                                        f'{self.data_dir}. No service was taken over.') from exc
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def start(self):
        if threading.current_thread() is not threading.main_thread():
            raise LauncherError('Service must be started on the Launcher main thread.')
        with self._lock:
            if self._process is not None:
                raise LauncherError('This Launcher already started its service.')
        try:
            self._acquire_instance()
            self._preflight()
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._handler = RotatingFileHandler(self.log_path, maxBytes=2 * 1024 * 1024,
                                                backupCount=1, encoding='utf-8')
            self._handler.setFormatter(logging.Formatter('%(message)s'))
            self._logger.addHandler(self._handler)
            with self._lock:
                self._state = 'Starting'
            command = [str(self.root / 'runtime/python/bin/python3'), '-I', '-B',
                       str(Path(__file__).with_name('child.py')), str(os.getpid()),
                       str(self.root / 'start.sh'), '--data-dir', str(self.data_dir)]
            if self.port_override is not None:
                command += ['--port', str(self.port_override)]
            self._logger.info('Launcher starting owned service with data directory %s',
                              self.data_dir)
            self._process = subprocess.Popen(
                command, cwd=self.root, env=self.environment, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding='utf-8', errors='replace', start_new_session=True,
                close_fds=True, bufsize=1)
            self._reader = threading.Thread(target=self._read_output,
                                            name='launcher-service-output', daemon=True)
            self._reader.start()
        except Exception as exc:
            # Never wait/join while holding the state lock needed by the reader.
            with self._lock:
                self._error = str(exc)
            self.close()
            with self._lock:
                self._state = 'Error'
            if isinstance(exc, LauncherError):
                raise
            raise LauncherError(f'Cannot start Remember Me: {exc}') from exc
        with self._lock:
            return self._snapshot()

    def _read_output(self):
        try:
            for line in self._process.stdout:
                self._logger.info(line.rstrip('\n'))
                with self._lock:
                    if 'ERROR' in line or 'Traceback' in line:
                        self._last_error_line = line.strip()
                    if ('Shutting down' in line or 'Started server process' in line or
                            'Waiting for application startup' in line):
                        self._bound = False
                        if not self._stop_requested:
                            self._state = 'Starting'
                    match = re.search(r'Uvicorn running on https?://[^\s:]+:(\d+)', line)
                    if match:
                        self.port = int(match.group(1))
                        self._bound = True
        finally:
            self._process.stdout.close()

    def _snapshot(self):
        return {'state': self._state, 'url': f'http://127.0.0.1:{self.port}',
                'pid': self._process.pid if self._process is not None else None,
                'error': self._error, 'log_path': str(self.log_path),
                'exit_code': self._process.returncode if self._process is not None else None,
                'data_dir': str(self.data_dir), 'port': self.port}

    def poll(self):
        """Refresh with bounded loopback reads; call outside the GUI event loop."""
        with self._lock:
            process = self._process
            if process is None:
                return self._snapshot()
            code = process.poll()
            if code is not None:
                self._bound = False
                if self._state == 'Error':
                    return self._snapshot()
                if self._stop_requested or code == 0:
                    self._state = 'Stopped'
                else:
                    self._state = 'Error'
                    self._error = (self._last_error_line or
                                   f'Remember Me exited unexpectedly ({code}); see {self.log_path}')
                return self._snapshot()
            if self._stop_requested or not self._bound:
                return self._snapshot()
            port = self.port
        try:
            # Do not inherit an HTTP proxy when reading localhost status.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def read(endpoint):
                with opener.open(f'http://127.0.0.1:{port}{endpoint}', timeout=0.5) as response:
                    return json.load(response)
            model = read('/api/model')
            maintenance = read('/api/maintenance')
            if not isinstance(model, dict) or not isinstance(maintenance, dict):
                raise ValueError('Unexpected service status response')
            with self._lock:
                if (not self._stop_requested and self._bound and self.port == port and
                        process.poll() is None):
                    self._state = ('Running' if model.get('ready') and
                                   maintenance.get('state') == 'normal' else 'Maintenance')
                    self._error = maintenance.get('error')
        except (OSError, ValueError, urllib.error.URLError):
            with self._lock:
                if not self._stop_requested:
                    self._state = 'Starting'
        with self._lock:
            return self._snapshot()

    def stop(self):
        """Ask only the owned child to close, then wait and reap without force kill."""
        with self._stop_lock:
            with self._lock:
                process = self._process
                if process is None:
                    return
                self._stop_requested = True
                if process.poll() is None:
                    self._state = 'Stopping'
                    try:
                        process.send_signal(signal.SIGINT)
                    except ProcessLookupError:
                        pass
            process.wait()
            if self._reader is not None and self._reader.ident is not None:
                self._reader.join()
            elif process.stdout is not None:
                process.stdout.close()
            with self._lock:
                self._state = 'Stopped'
                self._bound = False

    def close(self):
        """Release singleton only after the owned process is completely stopped."""
        self.stop()
        with self._lock:
            if self._handler is not None:
                self._logger.removeHandler(self._handler)
                self._handler.close()
                self._handler = None
            if self._instance is not None:
                fcntl.flock(self._instance, fcntl.LOCK_UN)
                self._instance.close()
                self._instance = None
