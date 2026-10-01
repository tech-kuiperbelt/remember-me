"""Shared helpers for the offline DeepSeek backfill CLIs."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.cli import deepseek_cli


# -- output extraction (existing samples) --

@pytest.mark.parametrize("output,expected", [
    ('["a", "b"]', ["a", "b"]),
    ('```json\n["a"]\n```', ["a"]),
    ('\x1b[32m["a"]\x1b[0m', ["a"]),
    ('Here you go: ["a","b"] done', ["a", "b"]),
])
def test_extract_json_array_strings_only(output, expected):
    assert deepseek_cli.extract_json_array(output, strings_only=True) == expected


def test_extract_json_array_keeps_raw_items_for_anchors():
    out = "prose [ {\"id\": \"X\", \"anchors\": [\"a\"]} ] trailing"
    assert deepseek_cli.extract_json_array(out)[0]["id"] == "X"


def test_extract_json_array_raw_keeps_multi_object_array_in_prose():
    out = ('Here are the anchors:\n'
           '[{"id": "m1", "anchors": ["PoolAcquire"]}, '
           '{"id": "m2", "anchors": ["ORA-00060"]}]\n'
           'Hope that helps!')
    parsed = deepseek_cli.extract_json_array(out)
    assert [item["id"] for item in parsed] == ["m1", "m2"]


def test_extract_json_array_returns_none_without_array():
    assert deepseek_cli.extract_json_array("no array") is None


def test_extract_json_array_drops_non_scalars_when_strings_only():
    assert deepseek_cli.extract_json_array('[{"a": 1}, "b"]', strings_only=True) == ["b"]


# -- opencode invocation --

def test_run_opencode_returns_stdout(monkeypatch):
    run = MagicMock(return_value=SimpleNamespace(returncode=0, stdout="ok", stderr=""))
    monkeypatch.setattr(deepseek_cli.subprocess, "run", run)
    assert deepseek_cli.run_opencode("body", instruction="do it", model="m", cli="c") == "ok"
    assert run.call_args.args[0] == ["c", "run", "--model", "m", "do it"]
    assert run.call_args.kwargs["input"] == "body"


def test_run_opencode_raises_on_nonzero_exit(monkeypatch):
    run = MagicMock(return_value=SimpleNamespace(returncode=1, stdout="", stderr="boom"))
    monkeypatch.setattr(deepseek_cli.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="boom"):
        deepseek_cli.run_opencode("body", instruction="do it")


# -- candidates / cleanup --

def test_format_candidates_block_label_and_empty():
    block = deepseek_cli.format_candidates_block(["a", "b"], label="keywords")
    assert "---- candidate keywords ----" in block and "- a" in block
    assert "---- end candidates ----" in block
    assert deepseek_cli.format_candidates_block([], label="categories") == "(empty candidate list)"


def test_stop_storage_is_optional_and_called():
    storage = MagicMock()
    deepseek_cli.stop_storage(storage)
    storage.stop.assert_called_once()
    deepseek_cli.stop_storage(object())  # no stop method: no error


# -- storage assembly --

def test_build_cli_storage_db_override_wins(monkeypatch):
    monkeypatch.delenv("MEMORY_STORAGE", raising=False)
    monkeypatch.delenv("MEMORY_STORAGE_PATH", raising=False)
    captured = {}
    deepseek_cli.build_cli_storage(
        "/tmp/override.db", role="keyword",
        create_storage=lambda backend, **kw: captured.update(backend=backend, kw=kw))
    assert captured == {"backend": "sqlite", "kw": {"path": "/tmp/override.db"}}


def test_build_cli_storage_env_path_fallback(monkeypatch):
    monkeypatch.setenv("MEMORY_STORAGE_PATH", "/tmp/env.db")
    monkeypatch.delenv("MEMORY_STORAGE", raising=False)
    captured = {}
    deepseek_cli.build_cli_storage(
        None, role="category",
        create_storage=lambda backend, **kw: captured.update(backend=backend, kw=kw))
    assert captured["kw"]["path"] == "/tmp/env.db"


def test_default_primary_path_honours_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("MEMORY_STORAGE_PATH", raising=False)
    root = tmp_path / "phase5-root"
    monkeypatch.setenv("MEMORY_DATA_DIR", str(root))
    assert deepseek_cli.default_primary_path() == str(root / "memories.db")
    assert not root.exists()  # pure computation: no directory/config created


def test_default_primary_path_resolves_relative_data_dir(monkeypatch):
    from app.config.configuration import PROJECT_ROOT
    monkeypatch.delenv("MEMORY_STORAGE_PATH", raising=False)
    monkeypatch.setenv("MEMORY_DATA_DIR", "phase5-relative-root")
    expected = PROJECT_ROOT / "phase5-relative-root" / "memories.db"
    assert deepseek_cli.default_primary_path() == str(expected)
    assert not expected.parent.exists()


def test_build_cli_storage_uses_data_dir_root(monkeypatch, tmp_path):
    monkeypatch.delenv("MEMORY_STORAGE_PATH", raising=False)
    monkeypatch.delenv("MEMORY_STORAGE", raising=False)
    monkeypatch.setenv("MEMORY_DATA_DIR", str(tmp_path / "root"))
    captured = {}
    deepseek_cli.build_cli_storage(
        None, role="keyword",
        create_storage=lambda backend, **kw: captured.update(backend=backend, kw=kw))
    assert captured["kw"]["path"] == str(tmp_path / "root" / "memories.db")
    assert not (tmp_path / "root").exists()


def test_read_only_never_uses_dual(monkeypatch):
    monkeypatch.setenv("MEMORY_STORAGE", "dual")
    captured = {}
    deepseek_cli.build_cli_storage(
        "/tmp/x.db", True, role="keyword",
        create_storage=lambda backend, **kw: captured.update(backend=backend, kw=kw))
    assert captured["backend"] == "sqlite"
    assert captured["kw"] == {"path": "/tmp/x.db", "read_only": True}


def test_dual_backend_resolves_replica(monkeypatch):
    monkeypatch.setenv("MEMORY_STORAGE", "dual")
    monkeypatch.delenv("MEMORY_REPLICA_PATH", raising=False)
    monkeypatch.delenv("MEMORY_STORAGE_PATH_DUAL", raising=False)
    captured = {}
    deepseek_cli.build_cli_storage(
        "/tmp/p.db", role="anchor",
        create_storage=lambda backend, **kw: captured.update(backend=backend, kw=kw),
        resolve_replica_path=lambda primary, configured, legacy: "/tmp/p.jsonl")
    assert captured["backend"] == "dual"
    assert captured["kw"]["secondary_path"] == "/tmp/p.jsonl"


def test_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv("MEMORY_STORAGE", "json")
    with pytest.raises(RuntimeError, match="sqlite or dual"):
        deepseek_cli.build_cli_storage("/tmp/x.db", role="keyword",
                                       create_storage=lambda *a, **k: None)
