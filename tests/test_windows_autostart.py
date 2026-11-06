"""Current-user startup settings; fake registry never writes actual login state."""
from contextlib import contextmanager
import importlib.util
import hashlib
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('win_startup_test', ROOT/'packaging/windows-beta/windows_autostart.py')
startup=importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


class Registry:
    HKEY_CURRENT_USER='current-user'
    KEY_READ=1
    KEY_SET_VALUE=2
    REG_SZ=1
    REG_BINARY=3

    def __init__(self):
        self.keys={}
        self.fail_run_write=False

    @contextmanager
    def OpenKey(self, hive, path, *args):
        assert hive==self.HKEY_CURRENT_USER
        if path not in self.keys: raise FileNotFoundError(path)
        yield path

    @contextmanager
    def CreateKeyEx(self,hive,path,*args):
        assert hive==self.HKEY_CURRENT_USER
        self.keys.setdefault(path,{})
        yield path

    def QueryValueEx(self,key,name):
        if name not in self.keys[key]: raise FileNotFoundError(name)
        return self.keys[key][name]

    def SetValueEx(self,key,name,unused,kind,value):
        if self.fail_run_write and key==startup.RUN_KEY: raise PermissionError('denied')
        self.keys[key][name]=(value,kind)

    def DeleteValue(self,key,name):
        if name not in self.keys[key]: raise FileNotFoundError(name)
        del self.keys[key][name]


def make_entry(tmp_path, registry, folder='Remember Me Beta'):
    # Keep ordinary cases below Run's real 260-character limit even though
    # pytest gives individual tests long descriptive directory names.
    base=tmp_path.parent/hashlib.sha256(tmp_path.name.encode()).hexdigest()[:6]
    root=base/folder
    for name in ('runtime/python/pythonw.exe','tray_launcher.py'):
        path=root/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('fixture',encoding='utf-8')
    return startup.Autostart(root,base/'中文 data',registry=registry)


def test_default_off_is_read_only_then_toggle_preserves_pinned_data(tmp_path):
    registry=Registry()
    entry=make_entry(tmp_path,registry)
    assert not entry.enabled() and registry.keys=={}
    assert '"' in entry.command and '--data-dir' in entry.command
    assert str(entry.data) in entry.command
    entry.set_enabled(True)
    assert entry.enabled()
    assert registry.keys[startup.RUN_KEY][startup.RUN_VALUE]==(entry.command,registry.REG_SZ)
    entry.set_enabled(False)
    assert not entry.enabled()
    assert not registry.keys[startup.RUN_KEY]
    assert not registry.keys[startup.OWNER_KEY]


@pytest.mark.parametrize('kind',[1,3])
def test_unknown_run_entry_is_never_replaced_or_deleted(tmp_path,kind):
    registry=Registry()
    registry.keys[startup.RUN_KEY]={startup.RUN_VALUE:('unrelated.exe',kind)}
    entry=make_entry(tmp_path,registry)
    for enabled in (True,False):
        with pytest.raises(RuntimeError,match='unrecognized'):
            entry.set_enabled(enabled)
        assert registry.keys[startup.RUN_KEY][startup.RUN_VALUE]==('unrelated.exe',kind)


def test_owned_previous_install_can_be_retargeted(tmp_path):
    registry=Registry()
    previous=make_entry(tmp_path,registry,'old program')
    previous.set_enabled(True)
    current=make_entry(tmp_path,registry,'new program')
    assert not current.enabled()
    current.set_enabled(True)
    assert current.enabled() and not previous.enabled()


def test_registry_failure_restores_previous_owner_marker(tmp_path):
    registry=Registry()
    previous=make_entry(tmp_path,registry,'old program')
    previous.set_enabled(True)
    current=make_entry(tmp_path,registry,'new program')
    registry.fail_run_write=True
    with pytest.raises(PermissionError): current.set_enabled(True)
    assert previous.enabled() and not current.enabled()


def test_too_long_command_does_not_write_registry(tmp_path):
    registry=Registry()
    entry=make_entry(tmp_path,registry,'x'*120)
    assert len(entry.command.encode('utf-16-le'))//2>260
    with pytest.raises(RuntimeError,match='Run limit'):
        entry.set_enabled(True)
    assert registry.keys=={}


def test_missing_private_python_does_not_register_broken_command(tmp_path):
    registry=Registry()
    entry=make_entry(tmp_path,registry)
    (entry.root/'runtime/python/pythonw.exe').unlink()
    with pytest.raises(RuntimeError,match='must exist'):
        entry.set_enabled(True)
    assert registry.keys=={}


def test_program_directory_cannot_become_startup_data_root(tmp_path):
    registry=Registry()
    entry=make_entry(tmp_path,registry)
    entry.data=entry.root/'data'
    with pytest.raises(RuntimeError,match='outside'):
        entry.set_enabled(True)
    assert registry.keys=={}


def test_native_diagnostic_never_registers_real_login_entry(tmp_path,monkeypatch):
    import sys
    registry=Registry()
    registry.keys[startup.RUN_KEY]={startup.RUN_VALUE:('unrelated.exe',registry.REG_SZ)}
    original_run=startup.RUN_KEY
    original_owner=startup.OWNER_KEY
    entry=make_entry(tmp_path,registry)
    touched=[]
    def delete_key(hive,path):
        assert hive==registry.HKEY_CURRENT_USER
        touched.append(path)
        if path not in registry.keys: raise FileNotFoundError(path)
        del registry.keys[path]
    registry.DeleteKey=delete_key
    monkeypatch.setitem(sys.modules,'winreg',registry)
    monkeypatch.setitem(sys.modules,'windows_autostart',startup)
    spec=importlib.util.spec_from_file_location('win_diag_test', ROOT/'packaging/windows-beta/diagnostics.py')
    diagnostic=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(diagnostic)
    assert diagnostic.check_autostart_registry(entry.root,entry.data) is True
    assert startup.RUN_KEY==original_run and startup.OWNER_KEY==original_owner
    assert registry.keys=={original_run:{startup.RUN_VALUE:('unrelated.exe',registry.REG_SZ)}}
    assert all(path.startswith('Software\\RememberMeBeta\\Diagnostics\\') for path in touched)
    assert all('\\\\' not in path for path in touched)
