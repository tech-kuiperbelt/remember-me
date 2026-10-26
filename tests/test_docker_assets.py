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


def test_packaged_ref_is_exact_sha_without_a_line_ending(tmp_path, monkeypatch):
    script = load_script("package_model")
    source = tmp_path / "source"
    source.mkdir()
    content = b'{"module":"fixture"}'
    (source / "modules.json").write_bytes(content)
    sha = hashlib.sha256(content).hexdigest()
    manifest = {"revision": "a" * 40, "files": [{"path": "modules.json", "size": len(content),
                "sha256": sha, "source_kind": "lfs_sha256", "source_oid": sha}]}
    monkeypatch.setattr(script, "MANIFEST", manifest)
    monkeypatch.setattr(script, "HERE", tmp_path)
    (tmp_path / "licenses").mkdir()
    (tmp_path / "bge-m3-manifest.json").write_text("{}")
    output = tmp_path / "model"
    monkeypatch.setattr("sys.argv", ["package_model.py", "--source", str(source), "--output", str(output)])
    script.main()
    hub = output / "sentence-transformers/models--BAAI--bge-m3"
    assert (hub / "refs/main").read_bytes() == b"a" * 40
    # Mirror the offline Hub's verbatim ref lookup; stripping would hide the bug.
    assert (hub / "snapshots" / (hub / "refs/main").read_text() / "modules.json").is_file()


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


@pytest.mark.parametrize("target", ["runtime-local", "runtime-download"])
def test_runtime_includes_same_release_file_and_install_declaration(target):
    dockerfile = (ROOT / "Dockerfile").read_text()
    stage = dockerfile.split(f" AS {target}\n", 1)[1].split("\nFROM ", 1)[0]
    assert "ENV REMEMBER_ME_INSTALL_METHOD=DOCKER" in stage
    assert "COPY release.jsonc ./release.jsonc" in stage
    assert stage.index("COPY release.jsonc") > stage.index("COPY app/")
    assert "!release.jsonc" in (ROOT / ".dockerignore").read_text().splitlines()
    assert (ROOT / "release.jsonc").is_file()


@pytest.mark.parametrize("target", ["runtime-base", "runtime-download"])
def test_runtime_defaults_to_root_and_keeps_explicit_nonroot_account(target):
    dockerfile = (ROOT / "Dockerfile").read_text()
    stage = dockerfile.split(f" AS {target}\n", 1)[1].split("\nFROM ", 1)[0]
    users = [line.split(maxsplit=1)[1] for line in stage.splitlines() if line.startswith("USER ")]
    assert users[-1] == "0:0"
    assert "groupadd --gid 10001 remember-me" in dockerfile
    assert "useradd --uid 10001 --gid 10001" in dockerfile
