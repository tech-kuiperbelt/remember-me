"""Configuration bootstrapping, path containment and offline migration."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from app.configuration import Configuration, rooted_path
from app.config_migrate import migrate
from app.database_lock import DatabaseWriterLock
from app.settings import Settings


def config(directory, **values):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "config.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    return path


def test_data_directory_selects_config_and_defaults_without_cwd(tmp_path, monkeypatch):
    monkeypatch.setattr("app.configuration.PROJECT_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path.parent)
    result = Configuration.load("custom", environment={})
    assert result.path == tmp_path / "custom/config.json"
    assert result.data_dir == tmp_path / "custom"
    assert (result.port, result.language) == (1314, "zh-CN")
    assert not result.path.exists()
    Configuration.load("custom", create=True, environment={})
    assert json.loads(result.path.read_text()) == {"port": 1314, "language": "zh-CN"}


@pytest.mark.parametrize("values", [
    {"port": 0}, {"port": 65536}, {"port": True}, {"port": "1314"},
    {"language": "fr"}, {"data_dir": "."}, [],
])
def test_invalid_configuration_fails_before_runtime(tmp_path, values):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(values))
    with pytest.raises(ValueError):
        Configuration.load(tmp_path, environment={})


def test_malformed_configuration_is_not_overwritten(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"port":')
    with pytest.raises(ValueError, match="Cannot read config"):
        Configuration.load(tmp_path, create=True, environment={})
    assert path.read_text() == '{"port":'


def test_all_runtime_paths_resolve_under_selected_root(tmp_path):
    config(tmp_path, port=2345, language="en")
    settings = Settings.from_config(tmp_path, environment={
        "MEMORY_STORAGE_PATH": "db/main.db", "MEMORY_AUDIT_PATH": "audit/calls.jsonl",
        "FASTEMBED_CACHE_DIR": "cache/fastembed",
    })
    assert settings.PORT == 2345 and settings.LANGUAGE == "en"
    assert settings.STORAGE_PATH == str(tmp_path / "db/main.db")
    assert settings.REPLICA_PATH == str(tmp_path / "db/main.jsonl")
    assert settings.AUDIT_PATH == str(tmp_path / "audit/calls.jsonl")
    assert settings.LOG_DIR == str(tmp_path / "logs")
    assert settings.TEMP_DIR == str(tmp_path / "tmp")
    for key in ("FASTEMBED_CACHE_DIR", "SENTENCE_TRANSFORMERS_CACHE_DIR", "HF_HOME",
                "HF_HUB_CACHE", "TORCH_HOME", "XDG_CACHE_HOME", "TMPDIR"):
        assert Path(settings.environment[key]).is_relative_to(tmp_path)


@pytest.mark.parametrize("key", ["MEMORY_STORAGE_PATH", "MEMORY_AUDIT_PATH",
                                 "MEMORY_REPLICA_PATH", "FASTEMBED_CACHE_DIR",
                                 "SENTENCE_TRANSFORMERS_CACHE_DIR", "SENTENCE_TRANSFORMERS_MODEL"])
def test_external_runtime_paths_are_rejected(tmp_path, key):
    with pytest.raises(ValueError, match="outside data_dir"):
        Settings.from_config(tmp_path, environment={key: str(tmp_path.parent / "outside")})


def test_path_containment_checks_symlinks(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    (root / "escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="outside data_dir"):
        rooted_path(root, "escape/memories.db")
    with pytest.raises(ValueError, match="outside data_dir"):
        rooted_path(root, "../other.db")
    assert rooted_path(root, root / "db/main.db") == root / "db/main.db"


def legacy_layout(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "data"
    source.mkdir()
    connection = sqlite3.connect(source / "memories.db")
    connection.execute("create table sample(value text)")
    connection.execute("insert into sample values ('unchanged')")
    connection.commit()
    connection.close()
    (source / "memories.jsonl").write_text('{}\n')
    (source / "audit.jsonl").write_text('{}\n')
    (project / "logs").mkdir()
    (project / "logs/server.log").write_text("old logs")
    cache = tmp_path / "external-cache"
    cache.mkdir()
    (cache / "weights.bin").write_bytes(b"model weights")
    (cache / "alias.bin").symlink_to(cache / "weights.bin")
    (project / ".env").write_text("EMBED_PROVIDER=sentence_transformers\nSENTENCE_TRANSFORMERS_CACHE_DIR=/old\n")
    return project, source, {"SENTENCE_TRANSFORMERS_CACHE_DIR": str(cache)}


def test_migration_preview_is_read_only_then_copies_and_switches(tmp_path):
    project, source, env = legacy_layout(tmp_path)
    target = tmp_path / "new-data"
    preview = migrate(target, environment=env, project_root=project, dry_run=True)
    assert not target.exists() and not (source / "config.json").exists()
    assert not preview["conflicts"]
    result = migrate(target, environment=env, project_root=project)
    assert result["migrated"] and result["source_files_retained"]
    assert (target / "memories.db").read_bytes() == (source / "memories.db").read_bytes()
    assert (target / "logs/server.log").read_text() == "old logs"
    assert (target / "models/sentence-transformers/weights.bin").read_bytes() == b"model weights"
    assert not (target / "models/sentence-transformers/alias.bin").is_symlink()
    assert json.loads((target / "config.json").read_text()) == {"port": 1314, "language": "zh-CN"}
    assert "EMBED_PROVIDER=sentence_transformers" in (project / ".env").read_text()
    assert "SENTENCE_TRANSFORMERS_CACHE_DIR=models/sentence-transformers" in (project / ".env").read_text()
    settings = Settings.from_config(target, environment=result["path_settings"])
    assert Path(settings.STORAGE_PATH) == target / "memories.db"


def test_in_place_migration_does_not_copy_existing_database(tmp_path):
    project, source, env = legacy_layout(tmp_path)
    result = migrate(environment=env, project_root=project)
    assert result["migrated"]
    assert (source / "logs/server.log").exists()
    assert (source / "config.json").exists()


def test_migration_rejects_conflicts_and_live_writer(tmp_path):
    project, source, env = legacy_layout(tmp_path)
    target = tmp_path / "new-data"
    with DatabaseWriterLock(str(source / "memories.db")):
        with pytest.raises(RuntimeError, match="active remember-me writer"):
            migrate(target, environment=env, project_root=project)
    target.mkdir(exist_ok=True)
    (target / "audit.jsonl").write_text("keep")
    with pytest.raises(ValueError, match="already exist"):
        migrate(target, environment=env, project_root=project)
    assert (target / "audit.jsonl").read_text() == "keep"


def test_failed_migration_keeps_original_configuration_and_files(tmp_path, monkeypatch):
    project, source, env = legacy_layout(tmp_path)
    original_env = (project / ".env").read_text()
    target = tmp_path / "new-data"
    from app.config_migrate import atomic_json
    def failed_publish(path, *args, **kwargs):
        if Path(path).name == "config.json":
            raise OSError("simulated publish failure")
        return atomic_json(path, *args, **kwargs)
    monkeypatch.setattr("app.config_migrate.atomic_json", failed_publish)
    with pytest.raises(OSError, match="simulated"):
        migrate(target, environment=env, project_root=project)
    assert (project / ".env").read_text() == original_env
    assert not (target / "memories.db").exists()
    assert not (target / "config.json").exists()
    assert (source / "memories.db").exists()


def test_migration_preserves_language_and_port_for_new_data_root(tmp_path):
    project, source, env = legacy_layout(tmp_path)
    config(source, port=2314, language="en")
    result = migrate(tmp_path / "new-data", source, environment=env, project_root=project)
    settings = Configuration.load(result["target_data_dir"], environment={})
    assert (settings.port, settings.language) == (2314, "en")


def test_second_migration_uses_active_logs_instead_of_retained_legacy_logs(tmp_path):
    project, source, env = legacy_layout(tmp_path)
    first = migrate(environment=env, project_root=project)
    (source / "logs/server.log").write_text("new active logs")
    second = migrate(tmp_path / "new-data", source,
                     environment={**first["path_settings"], "MEMORY_DATA_DIR": str(source)},
                     project_root=project)
    assert second["migrated"]
    assert (tmp_path / "new-data/logs/server.log").read_text() == "new active logs"
    assert (project / "logs/server.log").read_text() == "old logs"


def test_migration_crash_is_detected_and_can_be_rolled_back(tmp_path):
    project, source, env = legacy_layout(tmp_path)
    original_env = (project / ".env").read_text()
    target = tmp_path / "new-data"
    script = '''import json, os, sys
import app.config_migrate as migration
original = migration._atomic_text
def crash(path, text):
    original(path, text)
    os._exit(19)
migration._atomic_text = crash
migration.migrate(sys.argv[1], environment=json.loads(sys.argv[3]), project_root=sys.argv[2])
'''
    result = subprocess.run([sys.executable, "-c", script, str(target), str(project), json.dumps(env)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 19, result.stderr
    with pytest.raises(ValueError, match="Interrupted data migration"):
        Configuration.load(target, environment={})
    preview = migrate(target, project_root=project, environment={}, dry_run=True)
    assert preview["interrupted_migration"]
    recovered = migrate(target, project_root=project, environment={})
    assert recovered["recovered"] and not recovered["committed"]
    assert (project / ".env").read_text() == original_env
    assert not (target / "memories.db").exists()
    assert not (target / ".config-migration.json").exists()
    assert migrate(target, project_root=project, environment=env)["migrated"]
