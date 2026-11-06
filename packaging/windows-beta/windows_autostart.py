"""Current-user login startup with exact ownership checks; no registry on import."""
from pathlib import Path
import subprocess

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_VALUE = 'RememberMeBeta'
OWNER_KEY = r'Software\RememberMeBeta\Launcher'
OWNER_VALUE = 'AutostartCommand'


class Autostart:
    def __init__(self, root, data, *, registry=None):
        if registry is None:
            import winreg
            registry = winreg
        self.registry = registry
        self.root, self.data = Path(root).resolve(), Path(data).resolve()
        self.command = subprocess.list2cmdline([
            str(self.root/'runtime/python/pythonw.exe'), '-I', '-B',
            str(self.root/'tray_launcher.py'), '--data-dir', str(self.data)])

    def _read(self, path, name):
        r = self.registry
        try:
            with r.OpenKey(r.HKEY_CURRENT_USER, path, 0, r.KEY_READ) as key:
                return r.QueryValueEx(key, name)
        except FileNotFoundError:
            return None

    def _write(self, path, name, value):
        r = self.registry
        with r.CreateKeyEx(r.HKEY_CURRENT_USER, path, 0, r.KEY_SET_VALUE) as key:
            r.SetValueEx(key, name, 0, r.REG_SZ, value)

    def _delete(self, path, name):
        r = self.registry
        try:
            with r.OpenKey(r.HKEY_CURRENT_USER, path, 0, r.KEY_SET_VALUE) as key:
                r.DeleteValue(key, name)
        except FileNotFoundError:
            pass

    def enabled(self):
        expected = (self.command, self.registry.REG_SZ)
        return self._read(RUN_KEY, RUN_VALUE) == expected == self._read(OWNER_KEY, OWNER_VALUE)

    def set_enabled(self, enabled):
        current = self._read(RUN_KEY, RUN_VALUE)
        marker = self._read(OWNER_KEY, OWNER_VALUE)
        if marker is not None and marker[1] != self.registry.REG_SZ:
            raise RuntimeError('Refusing to change an unrecognized startup ownership marker.')
        if current is not None and (current != marker or current[1] != self.registry.REG_SZ):
            raise RuntimeError('Refusing to change an unrecognized Remember Me startup entry.')
        if enabled:
            if self.data.is_relative_to(self.root):
                raise RuntimeError('Persistent data must remain outside the program directory.')
            if not (self.root/'runtime/python/pythonw.exe').is_file() or not (self.root/'tray_launcher.py').is_file():
                raise RuntimeError('The installed private Python and launcher must exist.')
            if len(self.command.encode('utf-16-le'))//2 > 260:
                raise RuntimeError('The login startup command exceeds the Windows Run limit. Start manually from the Start menu.')
            self._write(OWNER_KEY, OWNER_VALUE, self.command)
            try:
                self._write(RUN_KEY, RUN_VALUE, self.command)
            except OSError:
                if marker is None:
                    self._delete(OWNER_KEY, OWNER_VALUE)
                else:
                    self._write(OWNER_KEY, OWNER_VALUE, marker[0])
                raise
        else:
            if current is not None:
                self._delete(RUN_KEY, RUN_VALUE)
            if marker == current or marker == (self.command, self.registry.REG_SZ):
                self._delete(OWNER_KEY, OWNER_VALUE)
