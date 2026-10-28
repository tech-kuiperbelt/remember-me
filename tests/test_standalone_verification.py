"""Ensure standalone opt-in does not weaken the production verifier's isolation."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def verifier():
    return load_script("verify_cpu_runtime", "bin/verify_cpu_runtime.py")


def test_default_verifier_still_requires_venv(verifier, monkeypatch):
    monkeypatch.setattr(verifier, "sys", SimpleNamespace(prefix="/system", base_prefix="/system"))
    with pytest.raises(SystemExit, match="isolated virtual environment"):
        verifier.verify_prefix()
    verifier.sys.prefix = "/private-venv"
    assert verifier.verify_prefix() == ROOT


def private_runtime(verifier, monkeypatch, tmp_path):
    runtime = tmp_path / "runtime/python"
    executable = runtime / "bin/python3"
    executable.parent.mkdir(parents=True)
    executable.touch()
    (tmp_path / "app").mkdir()
    (tmp_path / "app/server.py").touch()
    monkeypatch.setattr(verifier, "sys", SimpleNamespace(prefix=str(runtime), base_prefix=str(runtime),
                                                       executable=str(executable)))
    monkeypatch.setattr(verifier, "site", SimpleNamespace(ENABLE_USER_SITE=False))
    monkeypatch.setattr(verifier.sysconfig, "get_path", lambda key: str(runtime / "lib/site-packages"))
    return runtime


def test_standalone_rejects_external_interpreter(verifier, monkeypatch, tmp_path):
    private_runtime(verifier, monkeypatch, tmp_path)
    assert verifier.verify_prefix(tmp_path) == tmp_path
    verifier.sys.executable = "/usr/bin/python3"
    with pytest.raises(SystemExit, match="bundled Python executable"):
        verifier.verify_prefix(tmp_path)


def test_standalone_rejects_user_site_and_external_packages(verifier, monkeypatch, tmp_path):
    private_runtime(verifier, monkeypatch, tmp_path)
    verifier.site.ENABLE_USER_SITE = True
    with pytest.raises(SystemExit, match="Disable user site"):
        verifier.verify_prefix(tmp_path)
    verifier.site.ENABLE_USER_SITE = False
    monkeypatch.setattr(verifier.sysconfig, "get_path", lambda key: "/usr/lib/site-packages")
    with pytest.raises(SystemExit, match="escaped its private runtime"):
        verifier.verify_prefix(tmp_path)


def test_standalone_does_not_accept_nested_venv(verifier, monkeypatch, tmp_path):
    private_runtime(verifier, monkeypatch, tmp_path)
    verifier.sys.base_prefix = "/usr"
    with pytest.raises(SystemExit, match="private runtime/python prefix"):
        verifier.verify_prefix(tmp_path)
