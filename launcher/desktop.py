"""User-scoped XDG login startup; no GTK or service resources on import."""
import configparser
import os
from pathlib import Path
import tempfile


ENTRY_NAME = 'remember-me-launcher.desktop'
OWNER_KEY = 'X-Remember-Me-Launcher'


def _desktop_string(value):
    value = str(value)
    if any(ord(char) < 32 for char in value):
        raise ValueError('Login startup paths cannot contain control characters')
    return value.replace('\\', '\\\\')


def _exec_argument(value):
    # Exec quoting is followed by Desktop Entry string escaping, not a shell.
    value = str(value).replace('%', '%%')
    for char in ('\\', '"', '`', '$'):
        value = value.replace(char, '\\' + char)
    return _desktop_string('"' + value + '"')


class Autostart:
    def __init__(self, executable, data_dir, port=None, *, environment=None):
        env = os.environ if environment is None else environment
        base = Path(env.get('XDG_CONFIG_HOME') or Path(env.get('HOME') or Path.home()) / '.config')
        self.path = base.expanduser().resolve() / 'autostart' / ENTRY_NAME
        self.executable = Path(executable).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.port = port

    def _entry(self):
        config = configparser.ConfigParser(interpolation=None)
        config.optionxform = str
        if self.path.exists():
            config.read_string(self.path.read_text(encoding='utf-8'))
        return dict(config['Desktop Entry']) if config.has_section('Desktop Entry') else {}

    def _command(self):
        # Invoke our existing Bash entry as an argument: GIO rejects literal %
        # in the executable token even when %% escaped. No shell -c is used.
        argv = ['/bin/bash', self.executable, '--data-dir', self.data_dir]
        if self.port is not None:
            argv += ['--port', str(self.port)]
        return ' '.join(_exec_argument(arg) for arg in argv)

    def enabled(self):
        entry = self._entry()
        command = self._command()
        return (entry.get(OWNER_KEY) == 'true' and entry.get('Hidden', 'false').lower() != 'true'
                and entry.get('Exec') == command
                and self.executable.is_file() and os.access(self.executable, os.X_OK))

    def set_enabled(self, enabled):
        entry = self._entry()
        if self.path.exists() and entry.get(OWNER_KEY) != 'true':
            raise ValueError(f'Refusing to replace an unrelated login startup entry: {self.path}')
        if not enabled:
            self.path.unlink(missing_ok=True)
            return
        content = ('[Desktop Entry]\nType=Application\nVersion=1.0\nName=Remember Me\n'
                   'Comment=Start the Remember Me tray launcher\n'
                   f'Exec={self._command()}\nTryExec={_desktop_string(self.executable)}\n'
                   'Terminal=false\nStartupNotify=false\n'
                   f'{OWNER_KEY}=true\n')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=self.path.parent,
                                             prefix='.remember-me-', delete=False) as output:
                temporary = Path(output.name)
                output.write(content)
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
