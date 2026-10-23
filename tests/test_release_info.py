"""Phase A: shipped metadata, safe Unknown, install declaration and snapshots."""
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import Mock

import pytest

from app import release_info
from app.config import jsonc
from app.domain.releases import (CONTENT_FIELDS, MAX_RELEASE_BYTES,
                                 MAX_RELEASE_ITEM_BYTES, MAX_RELEASE_ITEMS, validate_release)

ROOT = Path(__file__).resolve().parents[1]


def content(version="1.2.3", **changes):
    return {"version": version, **{key: [] for key in CONTENT_FIELDS}, **changes}


def write_release(root, value=None):
    path = root / "release.jsonc"
    path.write_text(json.dumps(content() if value is None else value), encoding="utf-8")
    return path


@pytest.mark.parametrize("method", ["SOURCE", "DOCKER", "UNKNOWN"])
def test_valid_same_metadata_in_every_distribution(tmp_path, method):
    value = content(new=["第一项", "Second\nline"], upgrade=["Backup before upgrade"],
                    extra={"ignored": True})
    path = write_release(tmp_path, value)
    path.write_text('// comment\n/* block */' + path.read_text().replace('}', ',}'))
    snapshot = release_info.read_local_release(tmp_path, environment={
        "REMEMBER_ME_INSTALL_METHOD": method})
    assert snapshot.version == snapshot.display_version == "1.2.3"
    assert snapshot.install_method == method
    assert snapshot.error_code is None
    public = snapshot.as_dict()
    assert public["release"] == {key: value[key] for key in ("version", *CONTENT_FIELDS)}
    assert set(public) == {"version", "install_method", "release", "error_code"}
    public["release"]["new"].append("not in the snapshot")
    assert snapshot.release.new == ("第一项", "Second\nline")
    with pytest.raises(FrozenInstanceError):
        snapshot.release.version = "9.0.0"


@pytest.mark.parametrize("method", ["SOURCE", "DOCKER", "UNKNOWN"])
@pytest.mark.parametrize("failure", ["missing", "unparseable", "missing_version", "invalid_version"])
def test_unknown_failures_do_not_infer_git_or_override(tmp_path, monkeypatch, method, failure):
    if failure == "unparseable":
        (tmp_path / "release.jsonc").write_text('{"version":')
    elif failure == "missing_version":
        write_release(tmp_path, {key: [] for key in CONTENT_FIELDS})
    elif failure == "invalid_version":
        write_release(tmp_path, content("v1.0.0"))
    forbidden = Mock(side_effect=AssertionError("No process/network probing allowed"))
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    import socket
    monkeypatch.setattr(socket, "socket", forbidden)
    snapshot = release_info.read_local_release(tmp_path, environment={
        "REMEMBER_ME_INSTALL_METHOD": method, "REMEMBER_ME_VERSION": "9.0.0",
        "GITHUB_REF": "refs/tags/v9.0.0", "GITHUB_SHA": "a" * 40})
    assert snapshot.version is None and snapshot.release is None
    assert snapshot.display_version == "Unknown"
    assert snapshot.error_code == ("local_release_missing" if failure == "missing"
                                   else "local_release_invalid")
    forbidden.assert_not_called()


@pytest.mark.parametrize("value", [
    [], None, "bad", {"version": "1.0.0"}, content(None), content(new=None),
    content(new="text"), content(new=[1]), content(new=[""]), content(new=[" \n "]),
    content(new=["x"] * (MAX_RELEASE_ITEMS + 1)),
    content(breaking=["x" * (MAX_RELEASE_ITEM_BYTES + 1)]),
    content(upgrade=["中" * (MAX_RELEASE_ITEM_BYTES // 3 + 1)]),
])
def test_invalid_structure_and_limits(value):
    with pytest.raises(ValueError):
        validate_release(value)


def test_exact_content_limits_and_order():
    result = validate_release(content(new=["x"] * MAX_RELEASE_ITEMS,
                                      fixes=["x" * MAX_RELEASE_ITEM_BYTES],
                                      improvements=["  keep whitespace  ", "next"]))
    assert len(result.new) == MAX_RELEASE_ITEMS
    assert result.improvements == ("  keep whitespace  ", "next")


@pytest.mark.parametrize("body", [b'\xff', b'{"version":"1.0.0","version":"2.0.0"}',
                                  b"[" * 2000 + b"]" * 2000,
                                  b" " * (MAX_RELEASE_BYTES + 1)])
def test_bad_jsonc_encoding_duplicate_nesting_or_size_degrades(tmp_path, body):
    (tmp_path / "release.jsonc").write_bytes(body)
    assert release_info.read_local_release(tmp_path).error_code == "local_release_invalid"


def test_file_limit_boundary(tmp_path):
    path = write_release(tmp_path)
    body = path.read_bytes()
    path.write_bytes(body + b" " * (MAX_RELEASE_BYTES - len(body)))
    assert release_info.read_local_release(tmp_path).version == "1.2.3"
    with path.open("ab") as target:
        target.write(b" ")
    assert release_info.read_local_release(tmp_path).display_version == "Unknown"


def test_file_errors_are_safe_and_do_not_leak_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "open", Mock(side_effect=PermissionError("sensitive/path")))
    result = release_info.read_local_release(tmp_path).as_dict()
    assert result["error_code"] == "local_release_invalid"
    assert "sensitive" not in json.dumps(result)


@pytest.mark.parametrize("value,expected", [
    ("SOURCE", "SOURCE"), ("DOCKER", "DOCKER"), ("source", "SOURCE"),
    (" docker ", "DOCKER"), ("UNKNOWN", "UNKNOWN"), ("", "UNKNOWN"),
    (None, "UNKNOWN"), ("PIP", "UNKNOWN"), ("APPIMAGE", "UNKNOWN"),
    ("WINDOWS_INSTALLER", "UNKNOWN"), ("anything", "UNKNOWN"),
])
def test_install_declaration(value, expected):
    assert release_info.identify_install_method({"REMEMBER_ME_INSTALL_METHOD": value}) == expected


def test_undeclared_does_not_guess_docker_or_host(monkeypatch):
    monkeypatch.setenv("REMEMBER_ME_INSTALL_METHOD", "DOCKER")
    assert release_info.identify_install_method() == "DOCKER"
    assert release_info.identify_install_method({"MEMORY_DEPLOYMENT_MODE": "docker"}) == "UNKNOWN"


def test_default_path_is_project_root_not_cwd_data_or_environment(tmp_path, monkeypatch):
    source = tmp_path / "archive-without-git"
    source.mkdir()
    write_release(source)
    data = tmp_path / "data"
    data.mkdir()
    write_release(data, content("9.0.0"))
    monkeypatch.setattr(release_info, "PROJECT_ROOT", source)
    monkeypatch.chdir(data)
    result = release_info.read_local_release(environment={
        "MEMORY_DATA_DIR": str(data), "REMEMBER_ME_VERSION": "8.0.0",
        "GITHUB_REF_NAME": "v7.0.0"})
    assert result.version == "1.2.3"
    assert not (source / ".git").exists()


@pytest.mark.parametrize("body", [None, '{"version":', '{}', '{"version":"invalid"}'])
def test_unknown_runtime_still_starts_and_serves_memories(tmp_path, monkeypatch, body):
    from app.config.settings import Settings
    from app.runtime import Runtime
    from tests.conftest import FakeProvider
    monkeypatch.setattr(release_info, "PROJECT_ROOT", tmp_path)
    if body is not None:
        (tmp_path / "release.jsonc").write_text(body)
    settings = Settings.from_env({"MEMORY_DATA_DIR": str(tmp_path / "data"),
                                  "MEMORY_STORAGE_PATH": ":memory:", "EMBED_PRELOAD": "false"})
    runtime = Runtime(settings, provider_factory=lambda **_: FakeProvider(), configure_logging=False)
    assert runtime.release_info is None
    try:
        runtime.start()
        assert runtime.state == "running" and runtime.release_info.display_version == "Unknown"
        saved = runtime.service.memory_add("Unknown does not block memory writes")
        assert any(row["id"] == saved["id"] for row in runtime.service.memory_search("memory", k=5))
    finally:
        runtime.close()


def test_runtime_snapshot_until_restart_and_persistence(tmp_path, monkeypatch):
    from app.config.settings import Settings
    from app.runtime import Runtime
    from tests.conftest import FakeProvider
    monkeypatch.setattr(release_info, "PROJECT_ROOT", tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    config = data / "config.jsonc"
    config.write_text('// preserve\n{"debug":true,"language":"en","auto_download_model":false}')
    settings = Settings.from_config(data, environment={
        "MEMORY_STORAGE": "dual", "EMBED_PRELOAD": "false",
        "REMEMBER_ME_INSTALL_METHOD": "DOCKER"})
    first = Runtime(settings, provider_factory=lambda **_: FakeProvider(), configure_logging=False)
    # Construction does not even read the not-yet-existing release file.
    assert first.release_info is None
    path = write_release(tmp_path)
    try:
        first.start()
        saved = first.service.memory_add("Persist across metadata changes")
        path.write_text("bad release metadata")
        first.start()
        assert first.release_info.version == "1.2.3"
        assert first.release_info.install_method == "DOCKER"
    finally:
        first.close()
    second = Runtime(settings, provider_factory=lambda **_: FakeProvider(), configure_logging=False)
    try:
        second.start()
        assert second.release_info.display_version == "Unknown"
        assert any(row["id"] == saved["id"] for row in second.service.memory_search("Persist", k=5))
        assert second.settings.UI_DEBUG and second.language_effective == "en"
        assert config.read_text().startswith('// preserve\n')
        assert not (data / "release.jsonc").exists()
    finally:
        second.close()


@pytest.mark.parametrize("declared,dotenv,expected", [
    (None, None, "SOURCE"), ("", None, "SOURCE"), ("DOCKER", None, "DOCKER"),
    (None, "DOCKER", "DOCKER"), ("SOURCE", "DOCKER", "DOCKER"),
    (None, "future", "future"),
])
def test_start_launcher_sets_default_after_dotenv(tmp_path, declared, dotenv, expected):
    (tmp_path / "bin").mkdir()
    shutil.copy2(ROOT / "bin/start.sh", tmp_path / "bin/start.sh")
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/activate").write_text("# isolated activation\n")
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    python = fake_bin / "python"
    python.write_text('#!/bin/sh\nprintf "%s\\n" "$REMEMBER_ME_INSTALL_METHOD" "$MEMORY_STORAGE" "$*"\n')
    python.chmod(0o755)
    if dotenv is not None:
        (tmp_path / ".env").write_text(f"REMEMBER_ME_INSTALL_METHOD={dotenv}\n")
    env = {key: value for key, value in os.environ.items()
           if key not in ("REMEMBER_ME_INSTALL_METHOD", "MEMORY_STORAGE")}
    env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
    if declared is not None:
        env["REMEMBER_ME_INSTALL_METHOD"] = declared
    result = subprocess.run(["bash", str(tmp_path / "bin/start.sh"), "--stdio"],
                            cwd="/tmp", env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [expected, "dual", "-m app.server --stdio"]


def test_shipped_file_is_explicit_placeholder_or_valid_formal_metadata():
    value = jsonc.loads((ROOT / "release.jsonc").read_text())
    assert set(value) == {"version", *CONTENT_FIELDS}
    snapshot = release_info.read_local_release()
    if value["version"] is None:  # Current development placeholder, not a formal release.
        assert snapshot.display_version == "Unknown"
        assert snapshot.error_code == "local_release_invalid"
    else:
        assert snapshot.release == validate_release(value)


def test_module_import_and_runtime_construction_do_not_read_release_or_network(tmp_path):
    script = '''
import pathlib, sys, threading
def forbidden(*args, **kwargs):
    raise AssertionError("No file/network/process work for metadata import or construction")
def guard(event, args):
    if event in ("socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
        forbidden()
    if event == "open" and str(args[0]).endswith("release.jsonc"):
        forbidden()
sys.addaudithook(guard)
from app.config.settings import Settings
from app.runtime import Runtime
from app import release_info
pathlib.Path.open = forbidden
before = tuple(threading.enumerate())
import importlib
importlib.reload(release_info)
rt = Runtime(Settings.from_env({"MEMORY_DATA_DIR": sys.argv[1]}), configure_logging=False)
assert rt.release_info is None and tuple(threading.enumerate()) == before
'''
    result = subprocess.run([sys.executable, "-B", "-c", script, str(tmp_path / "data")],
                            cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
