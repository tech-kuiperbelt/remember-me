"""Embedding provider resolution: explicit > database profile > new default."""
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from app.embedding.embed import EmbeddingSpec
from app.embedding.provider_resolution import (
    EmbeddingResolutionError,
    probe_embedding_profile,
    resolve_embedding_environment,
)
from app.storage.sqlite import SQLiteStorage


def _spec(provider, model, dimension, *, query_prefix="", document_prefix="",
          normalized=False):
    return EmbeddingSpec(provider, model, dimension, normalized=normalized,
                         query_prefix=query_prefix,
                         document_prefix=document_prefix).as_dict()


def _profile_database(path, spec):
    """Create a file-backed database with ``spec`` as its active profile."""
    storage = SQLiteStorage(str(path), embedding_spec=spec)
    storage.close()
    return path


def _data_without_profile(path):
    storage = SQLiteStorage(str(path))
    storage.add("a memory", [0.1, 0.2, 0.3, 0.4])
    storage.close()
    return path


def test_missing_database_uses_sentence_transformers_default(tmp_path):
    env = resolve_embedding_environment({}, db_path=str(tmp_path / "missing.db"))
    assert env["EMBED_PROVIDER"] == "sentence_transformers"
    assert env["SENTENCE_TRANSFORMERS_MODEL"] == "BAAI/bge-m3"
    assert env["SENTENCE_TRANSFORMERS_DEVICE"] == "cpu"


def test_memory_path_uses_default():
    env = resolve_embedding_environment({}, db_path=":memory:")
    assert env["EMBED_PROVIDER"] == "sentence_transformers"
    assert env["SENTENCE_TRANSFORMERS_DEVICE"] == "cpu"


def test_explicit_provider_and_parameters_are_unchanged(tmp_path):
    original = {"EMBED_PROVIDER": "ollama", "EMBED_MODEL": "custom", "EMBED_DIM": "768"}
    resolved = resolve_embedding_environment(original, db_path=str(tmp_path / "missing.db"))
    assert resolved == original
    assert original == {"EMBED_PROVIDER": "ollama", "EMBED_MODEL": "custom", "EMBED_DIM": "768"}


def test_blank_provider_is_not_treated_as_explicit(tmp_path):
    env = resolve_embedding_environment({"EMBED_PROVIDER": "  "}, db_path=str(tmp_path / "missing.db"))
    assert env["EMBED_PROVIDER"] == "sentence_transformers"


def test_adopts_active_ollama_profile(tmp_path):
    path = _profile_database(tmp_path / "mem.db", _spec("ollama", "bge-m3", 1024))
    env = resolve_embedding_environment({}, db_path=str(path))
    assert env["EMBED_PROVIDER"] == "ollama"
    assert env["EMBED_MODEL"] == "bge-m3"
    assert env["EMBED_DIM"] == "1024"


def test_adopts_sentence_transformers_profile_with_revision_and_prefixes(tmp_path):
    path = _profile_database(
        tmp_path / "mem.db",
        _spec("sentence_transformers", "BAAI/bge-m3@abc123", 1024, normalized=True,
              query_prefix="query: ", document_prefix="passage: "),
    )
    env = resolve_embedding_environment({}, db_path=str(path))
    assert env["EMBED_PROVIDER"] == "sentence_transformers"
    assert env["SENTENCE_TRANSFORMERS_MODEL"] == "BAAI/bge-m3"
    assert env["SENTENCE_TRANSFORMERS_REVISION"] == "abc123"
    assert env["SENTENCE_TRANSFORMERS_QUERY_PREFIX"] == "query: "
    assert env["SENTENCE_TRANSFORMERS_DOCUMENT_PREFIX"] == "passage: "
    assert env["SENTENCE_TRANSFORMERS_DEVICE"] == "cpu"


def test_adopts_fastembed_profile(tmp_path):
    path = _profile_database(
        tmp_path / "mem.db",
        _spec("fastembed", "intfloat/multilingual-e5-large", 1024, normalized=True,
              query_prefix="query: ", document_prefix="passage: "),
    )
    env = resolve_embedding_environment({}, db_path=str(path))
    assert env["EMBED_PROVIDER"] == "fastembed"
    assert env["FASTEMBED_MODEL"] == "intfloat/multilingual-e5-large"
    assert env["FASTEMBED_QUERY_PREFIX"] == "query: "
    assert env["FASTEMBED_DOCUMENT_PREFIX"] == "passage: "


def test_explicit_conflicting_model_is_rejected(tmp_path):
    path = _profile_database(tmp_path / "mem.db", _spec("sentence_transformers", "BAAI/bge-m3", 1024))
    with pytest.raises(EmbeddingResolutionError, match="conflicts"):
        resolve_embedding_environment({"SENTENCE_TRANSFORMERS_MODEL": "other/model"},
                                      db_path=str(path))


def test_explicit_matching_provider_adopts_profile_identity(tmp_path):
    path = _profile_database(
        tmp_path / "mem.db",
        _spec("sentence_transformers", "BAAI/bge-m3@abc123", 1024, normalized=True,
              query_prefix="query: "),
    )
    env = resolve_embedding_environment({"EMBED_PROVIDER": "sentence_transformers"},
                                        db_path=str(path))
    assert env["SENTENCE_TRANSFORMERS_MODEL"] == "BAAI/bge-m3"
    assert env["SENTENCE_TRANSFORMERS_REVISION"] == "abc123"
    assert env["SENTENCE_TRANSFORMERS_QUERY_PREFIX"] == "query: "


def test_explicit_different_provider_is_left_unchanged(tmp_path):
    path = _profile_database(tmp_path / "mem.db", _spec("ollama", "bge-m3", 1024))
    resolved = resolve_embedding_environment({"EMBED_PROVIDER": "sentence_transformers"},
                                             db_path=str(path))
    # The Ollama profile identity is not adopted (the storage mismatch check owns
    # that), but the ST device default still applies.
    assert resolved["EMBED_PROVIDER"] == "sentence_transformers"
    assert "EMBED_MODEL" not in resolved and "EMBED_DIM" not in resolved
    assert resolved["SENTENCE_TRANSFORMERS_DEVICE"] == "cpu"


def test_explicit_target_can_change_model_within_same_provider(tmp_path):
    path = _profile_database(tmp_path / "mem.db", _spec("sentence_transformers", "BAAI/bge-m3", 1024))
    resolved = resolve_embedding_environment(
        {"EMBED_PROVIDER": "sentence_transformers", "SENTENCE_TRANSFORMERS_MODEL": "BAAI/bge-large"},
        db_path=str(path), explicit_is_target=True)
    assert resolved["SENTENCE_TRANSFORMERS_MODEL"] == "BAAI/bge-large"


def test_startup_still_rejects_same_provider_model_change(tmp_path):
    path = _profile_database(tmp_path / "mem.db", _spec("sentence_transformers", "BAAI/bge-m3", 1024))
    with pytest.raises(EmbeddingResolutionError, match="conflicts"):
        resolve_embedding_environment(
            {"EMBED_PROVIDER": "sentence_transformers", "SENTENCE_TRANSFORMERS_MODEL": "BAAI/bge-large"},
            db_path=str(path))


def test_explicit_sentence_transformers_new_database_defaults_to_cpu(tmp_path):
    resolved = resolve_embedding_environment(
        {"EMBED_PROVIDER": "sentence_transformers"}, db_path=str(tmp_path / "missing.db"))
    assert resolved["SENTENCE_TRANSFORMERS_DEVICE"] == "cpu"


def test_explicit_sentence_transformers_keeps_device_override(tmp_path):
    resolved = resolve_embedding_environment(
        {"EMBED_PROVIDER": "sentence_transformers", "SENTENCE_TRANSFORMERS_DEVICE": "cuda"},
        db_path=str(tmp_path / "missing.db"))
    assert resolved["SENTENCE_TRANSFORMERS_DEVICE"] == "cuda"


def test_profile_id_resolves_a_retained_profile(tmp_path):
    from app.embedding.provider_resolution import read_embedding_profile

    path = tmp_path / "mem.db"
    _profile_database(path, _spec("sentence_transformers", "BAAI/bge-m3", 1024))
    ollama = _spec("ollama", "bge-m3", 1024)
    connection = sqlite3.connect(str(path))
    connection.execute(
        "INSERT INTO embedding_profiles (profile_id, provider, model, dimension, "
        "normalized, query_prefix, document_prefix, status, created_at, updated_at) "
        "VALUES (?, 'ollama', 'bge-m3', 1024, 0, '', '', 'retired', '2026-01-01', '2026-01-01')",
        (ollama["profile_id"],),
    )
    connection.commit()
    connection.close()

    record = read_embedding_profile(str(path), ollama["profile_id"])
    assert record["provider"] == "ollama"
    resolved = resolve_embedding_environment({}, db_path=str(path),
                                             profile_id=ollama["profile_id"])
    assert resolved["EMBED_PROVIDER"] == "ollama"
    assert resolved["EMBED_MODEL"] == "bge-m3"
    assert resolved["EMBED_DIM"] == "1024"


def test_unknown_profile_id_is_rejected(tmp_path):
    path = _profile_database(tmp_path / "mem.db", _spec("ollama", "bge-m3", 1024))
    with pytest.raises(EmbeddingResolutionError, match="unknown embedding profile"):
        resolve_embedding_environment({}, db_path=str(path), profile_id="ep_missing")


def test_corrupt_database_without_explicit_target_is_rejected(tmp_path):
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"not a database")
    # Recovery must not silently rebuild an unidentifiable target with the default.
    with pytest.raises(EmbeddingResolutionError):
        resolve_embedding_environment({}, db_path=str(path), explicit_is_target=True)


def test_missing_target_without_explicit_uses_default(tmp_path):
    resolved = resolve_embedding_environment(
        {}, db_path=str(tmp_path / "missing.db"), explicit_is_target=True)
    assert resolved["EMBED_PROVIDER"] == "sentence_transformers"


def test_corrupt_database_with_explicit_target_bypasses_probe(tmp_path):
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"not a database")
    resolved = resolve_embedding_environment(
        {"EMBED_PROVIDER": "ollama", "EMBED_MODEL": "bge-m3", "EMBED_DIM": "1024"},
        db_path=str(path), explicit_is_target=True)
    assert resolved["EMBED_PROVIDER"] == "ollama"
    assert resolved["EMBED_MODEL"] == "bge-m3"


def test_data_without_active_profile_is_rejected(tmp_path):
    path = _data_without_profile(tmp_path / "mem.db")
    with pytest.raises(EmbeddingResolutionError, match="no active embedding profile"):
        resolve_embedding_environment({}, db_path=str(path))


def test_empty_database_uses_default(tmp_path):
    path = tmp_path / "empty.db"
    SQLiteStorage(str(path)).close()
    env = resolve_embedding_environment({}, db_path=str(path))
    assert env["EMBED_PROVIDER"] == "sentence_transformers"


def test_legacy_database_without_profile_table_is_rejected(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(str(path))
    connection.execute(
        "CREATE TABLE memories (id TEXT PRIMARY KEY, text TEXT, deleted INTEGER DEFAULT 0)"
    )
    connection.execute("INSERT INTO memories (id, text, deleted) VALUES ('one', 'text', 0)")
    connection.commit()
    connection.close()
    with pytest.raises(EmbeddingResolutionError, match="no active embedding profile"):
        resolve_embedding_environment({}, db_path=str(path))


def test_corrupt_database_is_rejected(tmp_path):
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"this is not a sqlite database")
    with pytest.raises(EmbeddingResolutionError):
        resolve_embedding_environment({}, db_path=str(path))


def test_unknown_profile_provider_is_rejected(tmp_path):
    path = _profile_database(tmp_path / "mem.db", _spec("ollama", "bge-m3", 1024))
    connection = sqlite3.connect(str(path))
    connection.execute("UPDATE embedding_profiles SET provider = 'mystery'")
    connection.commit()
    connection.close()
    with pytest.raises(EmbeddingResolutionError, match="unknown provider"):
        resolve_embedding_environment({}, db_path=str(path))


def test_resolver_for_configured_profile_resolves_from_database(tmp_path, monkeypatch):
    from app.embedding import index_value_resolver
    from tests.conftest import FakeProvider

    path = _profile_database(tmp_path / "mem.db", _spec("ollama", "bge-m3", 1024))
    storage = SQLiteStorage(str(path))
    monkeypatch.delenv("EMBED_PROVIDER", raising=False)
    captured = {}
    provider = FakeProvider()
    provider.spec = EmbeddingSpec("ollama", "bge-m3", 1024)

    def fake_create_embedder(**kwargs):
        captured.update(kwargs)
        return provider

    monkeypatch.setattr("app.embedding.embed.create_embedder", fake_create_embedder)
    try:
        resolver = index_value_resolver.resolver_for_configured_profile(storage)
        assert resolver is not None
        assert captured["environment"]["EMBED_PROVIDER"] == "ollama"
    finally:
        storage.close()


def test_probe_does_not_lock_or_modify_database(tmp_path):
    path = _profile_database(tmp_path / "mem.db", _spec("ollama", "bge-m3", 1024))
    before = path.read_bytes()
    probe = probe_embedding_profile(str(path))
    assert probe.exists and probe.profile is not None
    resolve_embedding_environment({}, db_path=str(path))
    assert path.read_bytes() == before
    assert not (tmp_path / "mem.db.writer.lock").exists()


def test_runtime_adopts_database_profile_with_real_provider_factory(tmp_path, monkeypatch):
    from app import runtime as runtime_module
    from app.config.settings import Settings
    from tests.conftest import FakeProvider

    database = tmp_path / "mem.db"
    _profile_database(database, _spec("ollama", "bge-m3", 1024))

    provider = FakeProvider()
    provider.spec = EmbeddingSpec("ollama", "bge-m3", 1024)
    captured = {}

    def fake_factory(**kwargs):
        captured.update(kwargs)
        return provider

    # The runtime only resolves for its own real factory; aliasing the module
    # global makes the identity gate see this fake as the real one. Process
    # environment activation is stubbed so the test does not leak os.environ.
    monkeypatch.setattr(runtime_module, "create_embedder", fake_factory)
    monkeypatch.setattr(runtime_module, "activate_environment", lambda env: None)
    settings = Settings.from_env({
        "MEMORY_STORAGE": "sqlite", "MEMORY_DATA_DIR": str(tmp_path),
        "MEMORY_STORAGE_PATH": str(database),
        "MEMORY_AUDIT_PATH": str(tmp_path / "audit.jsonl"), "EMBED_PRELOAD": "false",
    })
    runtime = runtime_module.Runtime(settings, provider_factory=fake_factory,
                                     configure_logging=False)
    try:
        runtime.start()
        environment = captured["environment"]
        assert environment["EMBED_PROVIDER"] == "ollama"
        assert environment["EMBED_MODEL"] == "bge-m3"
        assert environment["EMBED_DIM"] == "1024"
    finally:
        runtime.close()



class _Journal:
    def record(self, *args, **kwargs):
        pass


def test_reindex_cli_can_change_model_within_same_provider(tmp_path, monkeypatch):
    from app.cli import embedding_cli

    database = tmp_path / "mem.db"
    _profile_database(database, _spec("sentence_transformers", "BAAI/bge-m3", 1024))
    target = _spec("sentence_transformers", "BAAI/bge-large", 1024)
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            spec=SimpleNamespace(as_dict=lambda: target, profile_id=target["profile_id"]),
            embed_documents=lambda texts: [],
        )

    monkeypatch.setattr(embedding_cli, "create_embedder", factory)
    monkeypatch.setenv("EMBED_PROVIDER", "sentence_transformers")
    monkeypatch.setenv("SENTENCE_TRANSFORMERS_MODEL", "BAAI/bge-large")
    monkeypatch.setattr(sys, "argv", ["embedding_cli", "reindex", str(database)])

    embedding_cli._main(_Journal())

    assert captured["environment"]["SENTENCE_TRANSFORMERS_MODEL"] == "BAAI/bge-large"
    storage = SQLiteStorage(str(database))
    try:
        assert storage.active_embedding_profile() == target["profile_id"]
    finally:
        storage.close()


def test_reindex_index_values_cli_resolves_target_profile(tmp_path, monkeypatch):
    from app.cli import embedding_cli

    database = tmp_path / "mem.db"
    _profile_database(database, _spec("sentence_transformers", "BAAI/bge-m3", 1024))
    ollama = _spec("ollama", "bge-m3", 1024)
    connection = sqlite3.connect(str(database))
    connection.execute(
        "INSERT INTO embedding_profiles (profile_id, provider, model, dimension, "
        "normalized, query_prefix, document_prefix, status, created_at, updated_at) "
        "VALUES (?, 'ollama', 'bge-m3', 1024, 0, '', '', 'retired', '2026-01-01', '2026-01-01')",
        (ollama["profile_id"],),
    )
    connection.commit()
    connection.close()

    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            spec=SimpleNamespace(as_dict=lambda: ollama, profile_id=ollama["profile_id"]),
            embed_documents=lambda texts: [],
        )

    monkeypatch.setattr(embedding_cli, "create_embedder", factory)
    monkeypatch.setattr(
        sys, "argv",
        ["embedding_cli", "reindex-index-values", str(database), "--profile-id", ollama["profile_id"]])

    embedding_cli._main(_Journal())

    assert captured["environment"]["EMBED_PROVIDER"] == "ollama"
    assert captured["environment"]["EMBED_MODEL"] == "bge-m3"


def test_recover_cli_rebuilds_corrupt_target_with_explicit_provider(tmp_path, monkeypatch):
    from app.cli import recover_cli

    source = tmp_path / "backup.jsonl"
    source.write_text("")
    target = tmp_path / "corrupt.db"
    target.write_bytes(b"not a database")

    captured = {}
    recover_calls = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            spec=SimpleNamespace(as_dict=lambda: {"profile_id": "ep_x"}, provider="ollama"),
            embed_document=lambda text: [0.0], embed_documents=lambda texts: [],
        )

    def recover(*args, **kwargs):
        recover_calls["called"] = True
        return {"recovered": 0, "errors": 0}

    monkeypatch.setattr(recover_cli, "create_embedder", factory)
    monkeypatch.setattr(recover_cli, "recover_ndjson_to_sqlite", recover)
    monkeypatch.setenv("EMBED_PROVIDER", "ollama")
    monkeypatch.setattr(sys, "argv", ["recover_cli", str(source), str(target)])

    recover_cli.main()

    assert captured["environment"]["EMBED_PROVIDER"] == "ollama"
    assert recover_calls["called"] is True


def test_recover_cli_rejects_corrupt_target_without_explicit_provider(tmp_path, monkeypatch):
    from app.cli import recover_cli

    source = tmp_path / "backup.jsonl"
    source.write_text("")
    target = tmp_path / "corrupt.db"
    target.write_bytes(b"not a database")

    monkeypatch.delenv("EMBED_PROVIDER", raising=False)
    monkeypatch.setattr(sys, "argv", ["recover_cli", str(source), str(target)])

    with pytest.raises(SystemExit) as excinfo:
        recover_cli.main()
    assert excinfo.value.code == 2
