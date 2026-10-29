"""XDG startup changes stay scoped and launch the exact requested arguments."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

import pytest

from launcher.desktop import Autostart


def _launcher(tmp_path, directory='runner', name='launch.sh'):
    executable = tmp_path / directory / name
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text('#!/bin/sh\nexit 0\n')
    executable.chmod(0o755)
    return executable


def _autostart(tmp_path, executable=None, data=None, port=None):
    return Autostart(executable or _launcher(tmp_path), data or tmp_path / 'data', port,
                     environment={'XDG_CONFIG_HOME': str(tmp_path / 'config')})


def test_default_off_has_no_filesystem_side_effects(tmp_path):
    startup = _autostart(tmp_path)
    assert startup.enabled() is False
    assert not (tmp_path / 'config').exists()


def test_disable_removes_only_our_entry(tmp_path):
    startup = _autostart(tmp_path)
    startup.set_enabled(True)
    unrelated = startup.path.with_name('another-app.desktop')
    unrelated.write_text('unrelated')
    assert startup.enabled()
    startup.set_enabled(False)
    assert not startup.enabled()
    assert unrelated.read_text() == 'unrelated'


@pytest.mark.parametrize('enabled', (True, False))
def test_foreign_entry_never_replaced_or_removed(tmp_path, enabled):
    startup = _autostart(tmp_path)
    startup.path.parent.mkdir(parents=True)
    original = '[Desktop Entry]\nType=Application\nExec=another-program\n'
    startup.path.write_text(original)
    with pytest.raises(ValueError, match='unrelated'):
        startup.set_enabled(enabled)
    assert startup.path.read_text() == original


def test_move_requires_explicit_reenable_and_retains_data(tmp_path):
    executable = _launcher(tmp_path, 'old runner')
    data = tmp_path / 'separate user data'
    startup = _autostart(tmp_path, executable, data, 5263)
    startup.set_enabled(True)
    moved = tmp_path / 'runner moved'
    executable.parent.rename(moved)
    current = _autostart(tmp_path, moved / executable.name, data, 5263)
    assert not current.enabled()
    current.set_enabled(True)
    assert current.enabled()
    assert str(data) in current.path.read_text()
    assert '--port' in current.path.read_text()
    assert '5263' in current.path.read_text()


def test_write_failure_does_not_create_enabled_entry(tmp_path, monkeypatch):
    startup = _autostart(tmp_path)

    def fail(*args):
        raise OSError('read-only filesystem')
    monkeypatch.setattr(Path, 'replace', fail)
    with pytest.raises(OSError, match='read-only'):
        startup.set_enabled(True)
    assert not startup.enabled()
    assert list(startup.path.parent.iterdir()) == []


@pytest.mark.parametrize('bad', ('line\nbreak', 'line\rbreak', 'tab\tpath', 'nul\x00path'))
def test_control_characters_rejected_before_entry_write(tmp_path, bad):
    with pytest.raises(ValueError):
        startup = _autostart(tmp_path, data=tmp_path / bad)
        startup.set_enabled(True)
    assert not (tmp_path / 'config').exists()


def test_equal_in_executable_does_not_block_normal_launcher(tmp_path):
    startup = _autostart(tmp_path, executable=_launcher(tmp_path, 'runner=copy'))
    assert not startup.enabled()
    startup.set_enabled(True)
    assert startup.enabled()


def test_desktop_disabled_hidden_entry_is_not_checked(tmp_path):
    startup = _autostart(tmp_path)
    startup.set_enabled(True)
    with startup.path.open('a') as entry:
        entry.write('Hidden=true\n')
    assert not startup.enabled()


def test_gio_launches_exact_arguments_with_reserved_path_characters(tmp_path):
    # Exercise the actual Desktop Entry parser when the test host provides it.
    system_python = Path('/usr/bin/python3')
    if not system_python.exists():
        pytest.skip('GIO test helper unavailable')
    check = subprocess.run([str(system_python), '-B', '-c', 'from gi.repository import Gio'],
                           capture_output=True, timeout=10)
    if check.returncode:
        pytest.skip('Host GIO test helper unavailable; launcher does not depend on system Python')
    executable = _launcher(tmp_path, 'runner space "quote" $dollar `tick` %percent \\backslash')
    result = tmp_path / 'actual-argv.json'
    code = ('import json, pathlib, sys; '
            f'pathlib.Path({str(result)!r}).write_text(json.dumps(sys.argv[1:]))')
    executable.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} '
                          f'-c {shlex.quote(code)} "$@"\n')
    data = tmp_path / 'data "quote" $dollar `tick` %percent \\backslash'
    startup = _autostart(tmp_path, executable, data, 5263)
    startup.set_enabled(True)
    launch = subprocess.run([str(system_python), '-B', '-c',
                             'from gi.repository import Gio; import sys; '
                             'app=Gio.DesktopAppInfo.new_from_filename(sys.argv[1]); '
                             'assert app is not None; assert app.launch([], None)',
                             str(startup.path)], capture_output=True, text=True, timeout=10)
    assert launch.returncode == 0, launch.stderr
    deadline = time.monotonic() + 3
    while not result.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert json.loads(result.read_text()) == ['--data-dir', str(data), '--port', '5263']
