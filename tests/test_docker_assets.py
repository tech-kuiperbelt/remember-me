"""Build-only asset integrity and first-start config preservation checks."""
import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "docker" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("source_kind", ["git_blob", "lfs_sha256"])
def test_model_verification_rejects_modified_bytes_of_same_length(tmp_path, source_kind):
    script = load_script("package_model")
    body = b"approved model bytes"
    path = tmp_path / "model.bin"
    path.write_bytes(body)
    sha = hashlib.sha256(body).hexdigest()
    git = hashlib.sha1(f"blob {len(body)}\0".encode() + body).hexdigest()
    record = dict(path="model.bin", size=len(body), sha256=sha,
                  source_kind=source_kind, source_oid=git if source_kind == "git_blob" else sha)
    script.verify(path, record)
    path.write_bytes(b"x" * len(body))
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        script.verify(path, record)


def test_model_verification_checks_official_git_identity(tmp_path):
    script = load_script("package_model")
    path = tmp_path / "config.json"
    path.write_bytes(b"{}")
    record = dict(path="config.json", size=2, sha256=hashlib.sha256(b"{}").hexdigest(),
                  source_kind="git_blob", source_oid="0" * 40)
    with pytest.raises(ValueError, match="Official source digest mismatch"):
        script.verify(path, record)


@pytest.mark.parametrize("filename,body", [("config.jsonc", "invalid existing jsonc"),
                                          ("config.json", '{"auto_download_model":true}')])
def test_entrypoint_preserves_existing_configuration(tmp_path, monkeypatch, filename, body):
    script = load_script("entrypoint")
    path = tmp_path / filename
    path.write_text(body)
    monkeypatch.setenv("MEMORY_DATA_DIR", str(tmp_path))
    calls = []
    monkeypatch.setattr(script.os, "execv", lambda *args: calls.append(args))
    script.main()
    assert path.read_text() == body
    if filename == "config.json":
        assert not (tmp_path / "config.jsonc").exists()
    assert calls[0][1][1:3] == ["-m", "app.server"]
