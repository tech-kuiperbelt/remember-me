"""Model-root decoupling: resolution, containment, idempotency and defaults."""
from pathlib import Path

import pytest

from app.config.configuration import (
    MODEL_DIR_ENV, Configuration, contained_path, resolve_model_dir,
)
from app.config.settings import Settings


def _data_root(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    return root


def _config(data):
    return Configuration(data / "config.jsonc", data, 1314, "zh-CN")


def test_resolve_model_dir_defaults_below_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.configuration.PROJECT_ROOT", tmp_path)
    data = _data_root(tmp_path)
    resolved = resolve_model_dir(None, data_dir=data, environment={})
    assert resolved == (data / "models").resolve()
    assert not (data / "models").exists()  # resolution has no side effects


def test_resolve_model_dir_priority_and_relative(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.configuration.PROJECT_ROOT", tmp_path)
    data = _data_root(tmp_path)
    assert resolve_model_dir("rel/models", data_dir=data, environment={}) == \
        (tmp_path / "rel/models").resolve()
    env = {MODEL_DIR_ENV: "env/models"}
    assert resolve_model_dir(None, data_dir=data, environment=env) == \
        (tmp_path / "env/models").resolve()
    # A CLI value wins over the environment.
    assert resolve_model_dir("cli/models", data_dir=data, environment=env) == \
        (tmp_path / "cli/models").resolve()


def test_blank_env_is_unset_but_blank_cli_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.configuration.PROJECT_ROOT", tmp_path)
    data = _data_root(tmp_path)
    assert resolve_model_dir(None, data_dir=data, environment={MODEL_DIR_ENV: "  "}) == \
        (data / "models").resolve()
    with pytest.raises(ValueError, match="requires a path"):
        resolve_model_dir("  ", data_dir=data, environment={})


def test_contained_path_rejects_symlink_escape(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "link").symlink_to(outside)
    with pytest.raises(ValueError, match="outside model dir"):
        contained_path(root, "link", label="cache")


def test_runtime_environment_default_keeps_historical_layout(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.configuration.PROJECT_ROOT", tmp_path)
    data = _data_root(tmp_path)
    env = _config(data).runtime_environment({})
    assert env["MEMORY_MODEL_DIR"] == str((data / "models").resolve())
    assert env["SENTENCE_TRANSFORMERS_CACHE_DIR"] == str((data / "models/sentence-transformers").resolve())
    assert env["FASTEMBED_CACHE_DIR"] == str((data / "models/fastembed").resolve())
    assert env["HF_HOME"] == str((data / "models/huggingface").resolve())
    assert env["HF_HUB_CACHE"] == str((data / "models/huggingface/hub").resolve())
    assert env["TORCH_HOME"] == str((data / "models/torch").resolve())


def test_runtime_environment_external_model_root(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.configuration.PROJECT_ROOT", tmp_path)
    data = _data_root(tmp_path)
    models = tmp_path / "models-ro"
    env = _config(data).runtime_environment({}, model_dir=str(models))
    assert env["MEMORY_MODEL_DIR"] == str(models.resolve())
    assert env["SENTENCE_TRANSFORMERS_CACHE_DIR"] == str((models / "sentence-transformers").resolve())
    assert env["FASTEMBED_CACHE_DIR"] == str((models / "fastembed").resolve())
    assert env["HF_HUB_CACHE"] == str((models / "huggingface/hub").resolve())
    # Writable metadata caches stay in the data directory.
    assert env["HF_HOME"] == str((data / "cache/huggingface").resolve())
    assert env["TORCH_HOME"] == str((data / "cache/torch").resolve())


def test_runtime_environment_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.configuration.PROJECT_ROOT", tmp_path)
    data = _data_root(tmp_path)
    config = _config(data)
    keys = ("MEMORY_MODEL_DIR", "HF_HOME", "HF_HUB_CACHE", "TORCH_HOME",
            "SENTENCE_TRANSFORMERS_CACHE_DIR", "FASTEMBED_CACHE_DIR")
    first = config.runtime_environment({})
    second = config.runtime_environment(first)
    assert {key: second[key] for key in keys} == {key: first[key] for key in keys}
    external = config.runtime_environment({}, model_dir=str(tmp_path / "ro"))
    again = config.runtime_environment(external)
    assert {key: again[key] for key in keys} == {key: external[key] for key in keys}


def test_external_root_rejects_provider_cache_outside(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.configuration.PROJECT_ROOT", tmp_path)
    data = _data_root(tmp_path)
    with pytest.raises(ValueError, match="outside model dir"):
        _config(data).runtime_environment(
            {"SENTENCE_TRANSFORMERS_CACHE_DIR": str(tmp_path / "elsewhere")},
            model_dir=str(tmp_path / "ro"))


def test_default_mode_keeps_provider_cache_relative_to_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.configuration.PROJECT_ROOT", tmp_path)
    data = _data_root(tmp_path)
    env = _config(data).runtime_environment({"SENTENCE_TRANSFORMERS_CACHE_DIR": "models/custom-st"})
    assert env["SENTENCE_TRANSFORMERS_CACHE_DIR"] == str((data / "models/custom-st").resolve())


def test_settings_from_config_passes_model_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.configuration.PROJECT_ROOT", tmp_path)
    data = _data_root(tmp_path)
    models = tmp_path / "ro-models"
    settings = Settings.from_config(data, model_dir=str(models), environment={})
    assert settings.environment["MEMORY_MODEL_DIR"] == str(models.resolve())


def test_migrate_preserves_external_model_root(tmp_path):
    import sqlite3
    from app.cli.config_migrate import migrate

    project = tmp_path / "project"
    source = project / "data"
    source.mkdir(parents=True)
    connection = sqlite3.connect(source / "memories.db")
    connection.execute("create table t(v text)")
    connection.commit()
    connection.close()
    (source / "memories.jsonl").write_text("{}\n")
    (source / "models" / "sentence-transformers").mkdir(parents=True)

    models_ro = tmp_path / "models-ro"
    models_ro.mkdir()
    env = {MODEL_DIR_ENV: str(models_ro)}
    target = tmp_path / "new-data"

    preview = migrate(target, source, environment=env, project_root=project, dry_run=True)
    assert preview["model_dir_preserved"] == str(models_ro)
    assert preview["path_settings"][MODEL_DIR_ENV] == str(models_ro)
    assert "SENTENCE_TRANSFORMERS_CACHE_DIR" not in preview["path_settings"]
    assert not any(Path(copy["source"]).name == "models" for copy in preview["copies"])

    migrate(target, source, environment=env, project_root=project)
    env_text = (project / ".env").read_text()
    assert f"{MODEL_DIR_ENV}={models_ro}" in env_text
    assert "SENTENCE_TRANSFORMERS_CACHE_DIR" not in env_text


def test_default_mode_custom_cache_symlinks_scoped_to_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.configuration.PROJECT_ROOT", tmp_path)
    data = _data_root(tmp_path)
    cache = data / "cache" / "st"
    cache.mkdir(parents=True)
    (cache / "weights.bin").write_bytes(b"w")
    (cache / "alias.bin").symlink_to(cache / "weights.bin")
    env = _config(data).runtime_environment({"SENTENCE_TRANSFORMERS_CACHE_DIR": "cache/st"})
    assert env["SENTENCE_TRANSFORMERS_CACHE_DIR"] == str(cache.resolve())

    outside = tmp_path / "outside"
    outside.mkdir()
    (cache / "escape.bin").symlink_to(outside / "missing")
    with pytest.raises(ValueError, match="outside"):
        _config(data).runtime_environment({"SENTENCE_TRANSFORMERS_CACHE_DIR": "cache/st"})


def test_whitespace_env_is_default_but_whitespace_cli_errors(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.configuration.PROJECT_ROOT", tmp_path)
    data = _data_root(tmp_path)
    config = _config(data)
    env = config.runtime_environment({MODEL_DIR_ENV: "  "})
    assert env["MEMORY_MODEL_DIR"] == str((data / "models").resolve())
    assert env["HF_HOME"] == str((data / "models/huggingface").resolve())
    with pytest.raises(ValueError, match="requires a path"):
        config.runtime_environment({}, model_dir="  ")


def test_cli_model_dir_wins_over_normalized_default_marker(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.configuration.PROJECT_ROOT", tmp_path)
    data = _data_root(tmp_path)
    config = _config(data)
    normalized = config.runtime_environment({})
    external = tmp_path / "ro"
    env = config.runtime_environment(normalized, model_dir=str(external))
    assert env["MEMORY_MODEL_DIR"] == str(external.resolve())
    assert env["HF_HOME"] == str((data / "cache/huggingface").resolve())
    assert env["HF_HUB_CACHE"] == str((external / "huggingface/hub").resolve())
    # A derived cache path rebases to the new root's default, not a nested models/.
    assert env["SENTENCE_TRANSFORMERS_CACHE_DIR"] == str((external / "sentence-transformers").resolve())


def test_default_mode_cache_symlink_inside_data_dir_is_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.configuration.PROJECT_ROOT", tmp_path)
    data = _data_root(tmp_path)
    target = data / "cache" / "st"
    target.mkdir(parents=True)
    (data / "models").mkdir()
    (data / "models" / "sentence-transformers").symlink_to(target)
    env = _config(data).runtime_environment({})
    assert Path(env["SENTENCE_TRANSFORMERS_CACHE_DIR"]).resolve() == target.resolve()


def test_default_mode_cache_symlink_outside_data_dir_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.configuration.PROJECT_ROOT", tmp_path)
    data = _data_root(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (data / "models").mkdir()
    (data / "models" / "sentence-transformers").symlink_to(outside)
    with pytest.raises(ValueError, match="outside data_dir"):
        _config(data).runtime_environment({})


def test_explicit_cache_override_equal_to_old_default_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.configuration.PROJECT_ROOT", tmp_path)
    data = _data_root(tmp_path)
    requested = data / "models" / "sentence-transformers"
    env = _config(data).runtime_environment(
        {"SENTENCE_TRANSFORMERS_CACHE_DIR": str(requested)}, model_dir=str(data))
    # A user-supplied value inside the explicit root must not be rebased.
    assert env["SENTENCE_TRANSFORMERS_CACHE_DIR"] == str(requested.resolve())


def _migration_layout(tmp_path):
    import sqlite3
    project = tmp_path / "project"
    source = project / "data"
    source.mkdir(parents=True)
    connection = sqlite3.connect(source / "memories.db")
    connection.execute("create table t(v text)")
    connection.commit()
    connection.close()
    (source / "memories.jsonl").write_text("{}\n")
    (source / "models" / "sentence-transformers").mkdir(parents=True)
    models_ro = tmp_path / "models-ro"
    models_ro.mkdir()
    return project, source, models_ro


def test_migrate_external_root_drops_derived_cache_override(tmp_path):
    from app.cli.config_migrate import migrate
    project, source, models_ro = _migration_layout(tmp_path)
    # Default-mode source (no MEMORY_MODEL_DIR); --model-dir selects a new root.
    env = {"SENTENCE_TRANSFORMERS_CACHE_DIR": "models/sentence-transformers"}
    target = tmp_path / "new-data"
    preview = migrate(target, source, model_dir=str(models_ro), environment=env,
                      project_root=project, dry_run=True)
    assert "SENTENCE_TRANSFORMERS_CACHE_DIR" not in preview["path_settings"]
    migrate(target, source, model_dir=str(models_ro), environment=env, project_root=project)
    assert "SENTENCE_TRANSFORMERS_CACHE_DIR" not in (project / ".env").read_text()


def test_migrate_preserves_model_relative_override_for_existing_root(tmp_path):
    from app.cli.config_migrate import migrate
    project, source, models_ro = _migration_layout(tmp_path)
    (models_ro / "models" / "sentence-transformers").mkdir(parents=True)
    (models_ro / "models" / "fastembed").mkdir(parents=True)
    env = {MODEL_DIR_ENV: str(models_ro),
           "SENTENCE_TRANSFORMERS_CACHE_DIR": "models/sentence-transformers",
           "FASTEMBED_CACHE_DIR": "models/fastembed"}
    target = tmp_path / "new-data"
    preview = migrate(target, source, environment=env, project_root=project, dry_run=True)
    assert preview["path_settings"]["SENTENCE_TRANSFORMERS_CACHE_DIR"] == "models/sentence-transformers"
    assert preview["path_settings"]["FASTEMBED_CACHE_DIR"] == "models/fastembed"
    migrate(target, source, environment=env, project_root=project)
    text = (project / ".env").read_text()
    assert "SENTENCE_TRANSFORMERS_CACHE_DIR=models/sentence-transformers" in text
    assert "FASTEMBED_CACHE_DIR=models/fastembed" in text


def test_migrate_external_root_rewrites_compatible_cache_override(tmp_path):
    from app.cli.config_migrate import migrate
    project, source, models_ro = _migration_layout(tmp_path)
    custom = models_ro / "st-cache"
    custom.mkdir()
    env = {MODEL_DIR_ENV: str(models_ro), "SENTENCE_TRANSFORMERS_CACHE_DIR": str(custom)}
    migrate(tmp_path / "new-data", source, environment=env, project_root=project)
    assert "SENTENCE_TRANSFORMERS_CACHE_DIR=st-cache" in (project / ".env").read_text()


def test_migrate_rejects_incompatible_cache_override(tmp_path):
    from app.cli.config_migrate import migrate
    project, source, models_ro = _migration_layout(tmp_path)
    env = {MODEL_DIR_ENV: str(models_ro),
           "SENTENCE_TRANSFORMERS_CACHE_DIR": str(tmp_path / "old-cache")}
    with pytest.raises(ValueError, match="outside the explicit model dir"):
        migrate(tmp_path / "new-data", source, environment=env, project_root=project, dry_run=True)


def test_migrate_rejects_incompatible_local_model_path(tmp_path):
    from app.cli.config_migrate import migrate
    project, source, models_ro = _migration_layout(tmp_path)
    env = {MODEL_DIR_ENV: str(models_ro),
           "SENTENCE_TRANSFORMERS_MODEL": str(tmp_path / "old-model")}
    with pytest.raises(ValueError, match="outside the explicit model dir"):
        migrate(tmp_path / "new-data", source, environment=env, project_root=project, dry_run=True)


def test_migrate_blank_model_dir_is_rejected(tmp_path):
    from app.cli.config_migrate import migrate
    project, source, _ = _migration_layout(tmp_path)
    with pytest.raises(ValueError, match="requires a path"):
        migrate(tmp_path / "new-data", source, model_dir="  ",
                environment={}, project_root=project, dry_run=True)


def test_migrate_accepts_model_relative_cache_override(tmp_path):
    from app.cli.config_migrate import migrate
    project, source, models_ro = _migration_layout(tmp_path)
    (models_ro / "sentence-transformers").mkdir()
    (models_ro / "custom-st").mkdir()
    target = tmp_path / "new-data"

    # Relative to the model root, exactly as runtime resolves it.
    env = {MODEL_DIR_ENV: str(models_ro),
           "SENTENCE_TRANSFORMERS_CACHE_DIR": "sentence-transformers"}
    preview = migrate(target, source, environment=env, project_root=project, dry_run=True)
    assert preview["path_settings"]["SENTENCE_TRANSFORMERS_CACHE_DIR"] == "sentence-transformers"

    # Absolute inside the model root and an override previously emitted by migration.
    for value in (str(models_ro / "custom-st"), "custom-st"):
        env = {MODEL_DIR_ENV: str(models_ro), "SENTENCE_TRANSFORMERS_CACHE_DIR": value}
        preview = migrate(target, source, environment=env, project_root=project, dry_run=True)
        assert preview["path_settings"]["SENTENCE_TRANSFORMERS_CACHE_DIR"] == "custom-st"


def test_migrate_preserves_local_model_path_syntax(tmp_path, monkeypatch):
    from app.cli.config_migrate import migrate
    from app.config.configuration import Configuration

    monkeypatch.setattr("app.config.configuration.PROJECT_ROOT", tmp_path)
    project, source, models_ro = _migration_layout(tmp_path)
    local = models_ro / "local" / "bge"
    local.mkdir(parents=True)
    env = {MODEL_DIR_ENV: str(models_ro), "SENTENCE_TRANSFORMERS_MODEL": str(local)}
    target = tmp_path / "new-data"
    migrate(target, source, environment=env, project_root=project)

    text = (project / ".env").read_text()
    assert "SENTENCE_TRANSFORMERS_MODEL=./local/bge" in text
    # runtime resolves the emitted form back to the original local directory
    config = Configuration(target / "config.jsonc", target, 1314, "zh-CN")
    resolved = config.runtime_environment({
        MODEL_DIR_ENV: str(models_ro), "SENTENCE_TRANSFORMERS_MODEL": "./local/bge"})
    assert resolved["SENTENCE_TRANSFORMERS_MODEL"] == str(local.resolve())


def test_migrate_excludes_model_root_by_resolved_path(tmp_path):
    from app.cli.config_migrate import migrate
    project, source, _ = _migration_layout(tmp_path)
    shared = source / "shared-models"
    (shared / "snapshots").mkdir(parents=True)
    (shared / "snapshots" / "weights.bin").write_bytes(b"w" * 1024)
    preview = migrate(tmp_path / "new-data", source, dry_run=True, project_root=project,
                      environment={MODEL_DIR_ENV: str(shared)})
    paths = [str(copy["source"]) for copy in preview["copies"]]
    assert not any("shared-models" in path for path in paths)


def test_migrate_excludes_model_root_symlink_alias(tmp_path):
    from app.cli.config_migrate import migrate
    project, source, _ = _migration_layout(tmp_path)
    external = tmp_path / "external-models"
    (external / "snapshots").mkdir(parents=True)
    (external / "snapshots" / "weights.bin").write_bytes(b"w" * 1024)
    (source / "external-link").symlink_to(external)
    preview = migrate(tmp_path / "new-data", source, dry_run=True, project_root=project,
                      environment={MODEL_DIR_ENV: str(external)})
    paths = [str(copy["source"]) for copy in preview["copies"]]
    assert not any("external-link" in path or "external-models" in path for path in paths)


def test_migrate_selectively_copies_nested_model_root(tmp_path):
    from app.cli.config_migrate import migrate
    project, source, _ = _migration_layout(tmp_path)
    storage = source / "storage"
    (storage / "models").mkdir(parents=True)
    (storage / "models" / "weights.bin").write_bytes(b"w" * 1024)
    (storage / "other.txt").write_text("keep me")
    preview = migrate(tmp_path / "new-data", source, dry_run=True, project_root=project,
                      environment={MODEL_DIR_ENV: str(storage / "models")})
    targets = [str(copy["target"]) for copy in preview["copies"]]
    sources = [str(copy["source"]) for copy in preview["copies"]]
    assert any(target.endswith("storage/other.txt") for target in targets)
    assert not any("storage/models" in source for source in sources)


def test_migrate_model_root_equal_to_data_root_copies_data(tmp_path):
    from app.cli.config_migrate import migrate
    project, source, _ = _migration_layout(tmp_path)
    preview = migrate(tmp_path / "new-data", source, dry_run=True, project_root=project,
                      environment={MODEL_DIR_ENV: str(source)})
    sources = [str(copy["source"]) for copy in preview["copies"]]
    assert any(path.endswith("memories.db") for path in sources)
    assert any(path.endswith("memories.jsonl") for path in sources)
