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


def _autostart(tmp_path, executable=None, data=None, port=None, *, appimage=False):
    return Autostart(executable or _launcher(tmp_path), data or tmp_path / 'data', port,
                     environment={'XDG_CONFIG_HOME': str(tmp_path / 'config')}, appimage=appimage)


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


def test_moved_appimage_requires_rebinding(tmp_path):
    executable = _launcher(tmp_path, 'old image', 'Remember Me%.AppImage')
    startup = _autostart(tmp_path, executable, port=5263, appimage=True)
    startup.set_enabled(True)
    moved = tmp_path / 'new image'
    executable.parent.rename(moved)
    current = _autostart(tmp_path, moved / executable.name, port=5263, appimage=True)
    assert not current.enabled()
    current.set_enabled(True)
    assert current.enabled()
    assert '/usr/bin/env' in current.path.read_text()


def test_appimage_rejects_relative_data_before_gui_or_service(monkeypatch, tmp_path, capsys):
    from launcher import main
    monkeypatch.setenv('APPIMAGE', str(tmp_path / 'Remember Me.AppImage'))
    monkeypatch.setenv('APPDIR', str(tmp_path / 'mounted'))
    monkeypatch.setenv('REMEMBER_ME_APPIMAGE', '1')
    monkeypatch.setattr(main, 'ROOT', tmp_path / 'mounted' / 'payload')
    monkeypatch.setattr(main, '_load_gui', lambda: pytest.fail('GUI should not start'))
    with pytest.raises(SystemExit) as exc:
        main.main(['--data-dir', 'relative'])
    assert exc.value.code == 2
    assert 'AppImage --data-dir must be an absolute path' in capsys.readouterr().err
    assert not (tmp_path / 'relative').exists()


def test_standalone_relative_data_still_reaches_gui(monkeypatch, capsys):
    from launcher import main
    monkeypatch.setattr(sys, 'path', sys.path.copy())
    monkeypatch.delenv('APPIMAGE', raising=False)
    def unavailable():
        raise RuntimeError('GUI probe reached')
    monkeypatch.setattr(main, '_load_gui', unavailable)
    assert main.main(['--data-dir', 'relative']) == 1
    assert 'GUI probe reached' in capsys.readouterr().err


def test_unrelated_inherited_appimage_does_not_change_standalone(monkeypatch, tmp_path, capsys):
    from launcher import main
    monkeypatch.setattr(sys, 'path', sys.path.copy())
    monkeypatch.setenv('APPIMAGE', str(tmp_path / 'unrelated.AppImage'))
    monkeypatch.setenv('APPDIR', str(tmp_path / 'unrelated-mount'))
    monkeypatch.setenv('REMEMBER_ME_APPIMAGE', '1')
    def unavailable():
        raise RuntimeError('GUI probe reached')
    monkeypatch.setattr(main, '_load_gui', unavailable)
    assert main.main(['--data-dir', 'relative']) == 1
    assert 'GUI probe reached' in capsys.readouterr().err


def test_extracted_image_without_original_cannot_enable_login(tmp_path):
    startup = Autostart(None, tmp_path / 'data', appimage=True,
                        environment={'XDG_CONFIG_HOME': str(tmp_path / 'config')})
    assert not startup.enabled()
    with pytest.raises(ValueError, match='original executable AppImage'):
        startup.set_enabled(True)
    assert not startup.path.exists()


def test_extracted_image_still_rejects_relative_data(monkeypatch, tmp_path, capsys):
    from launcher import main
    monkeypatch.delenv('APPIMAGE', raising=False)
    monkeypatch.setenv('APPDIR', str(tmp_path / 'extracted'))
    monkeypatch.setenv('REMEMBER_ME_APPIMAGE', '1')
    monkeypatch.setattr(main, 'ROOT', tmp_path / 'extracted' / 'payload')
    monkeypatch.setattr(main, '_load_gui', lambda: pytest.fail('GUI should not start'))
    with pytest.raises(SystemExit) as exc:
        main.main(['--data-dir', 'relative'])
    assert exc.value.code == 2
    assert 'AppImage --data-dir must be an absolute path' in capsys.readouterr().err


@pytest.mark.parametrize('has_original', (True, False))
def test_appimage_main_selects_external_file_or_disables_autostart(monkeypatch, tmp_path, has_original):
    from launcher import main
    monkeypatch.setattr(sys, 'path', sys.path.copy())
    mount = tmp_path / 'mount'
    original = _launcher(tmp_path, 'external', 'Remember Me.AppImage')
    monkeypatch.setenv('APPDIR', str(mount))
    monkeypatch.setenv('REMEMBER_ME_APPIMAGE', '1')
    if has_original:
        monkeypatch.setenv('APPIMAGE', str(original))
    else:
        monkeypatch.delenv('APPIMAGE', raising=False)
    monkeypatch.setattr(main, 'ROOT', mount / 'payload')
    monkeypatch.setattr(main, '_load_gui', lambda: ())
    seen = {}
    class ProbeUI:
        def __init__(self, controller, autostart, gui):
            seen['autostart'] = autostart
        def run(self):
            pass
    monkeypatch.setattr(main, 'LauncherUI', ProbeUI)
    assert main.main(['--data-dir', str(tmp_path / 'data')]) == 0
    assert seen['autostart'].appimage
    assert seen['autostart'].executable == (original.resolve() if has_original else None)


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


@pytest.mark.parametrize('appimage', (False, True))
def test_gio_launches_exact_arguments_with_reserved_path_characters(tmp_path, appimage):
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
    startup = _autostart(tmp_path, executable, data, 5263, appimage=appimage)
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
