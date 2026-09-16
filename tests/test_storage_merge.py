"""Atomic prepared-record import behavior for JSONL transfer."""

import pytest

from app.embed import EmbeddingSpec
from app.storage_sqlite import SQLiteStorage


SPEC = EmbeddingSpec("test", "import", 4, normalized=True).as_dict()


@pytest.fixture
def storage():
    return SQLiteStorage(":memory:", embedding_spec=SPEC)


def _record(memory_id="source-id", text="source", namespace="ns", metadata=None,
            summary=None):
    return {
        "id": memory_id, "text": text, "metadata": metadata or {},
        "namespace": namespace, "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-02T00:00:00+00:00", "summary": summary,
    }


def _import(storage, records, fake_emb, index_vectors=None, profile=None):
    return storage.import_records(
        records,
        {record["id"]: fake_emb for record in records},
        index_vectors or {},
        profile or storage.get_stats()["embedding_profile"]["active"],
    )


def test_import_skips_existing_namespace_and_text(storage, fake_emb):
    storage.add("source", fake_emb, namespace="ns")

    result = _import(storage, [_record()], fake_emb)

    assert result["imported"] == 0
    assert result["skipped"] == 1
    assert storage.get_stats()["total"] == 1


def test_import_preserves_fields_and_marks_outbox_pending(storage, fake_emb):
    record = _record(metadata={"keywords": ["project"], "categories": ["design"]},
                     summary=None)
    index_vectors = {
        ("keywords", "project"): fake_emb,
        ("categories", "design"): fake_emb,
    }

    result = _import(storage, [record], fake_emb, index_vectors)
    imported = storage.get("source-id")

    assert result["imported"] == 1
    assert imported["created_at"] == record["created_at"]
    assert imported["updated_at"] == record["updated_at"]
    assert imported["summary"] is None
    assert imported["synced"] == 0
    assert imported["version"] == 0
    assert storage.index_embedding_completeness()["complete"] is True


def test_import_renames_live_id_collision(storage, fake_emb):
    existing = storage.add("existing", fake_emb, namespace="ns")
    record = _record(memory_id=existing["id"], text="different", namespace="other")

    result = _import(storage, [record], fake_emb)

    assert result["renamed"] == 1
    assert result["imported_ids"][0] != existing["id"]


def test_import_renames_soft_deleted_id_collision(storage, fake_emb):
    existing = storage.add("existing", fake_emb, namespace="ns")
    storage.delete(existing["id"])
    record = _record(memory_id=existing["id"], text="replacement")

    result = _import(storage, [record], fake_emb)

    assert result["renamed"] == 1
    assert result["imported_ids"][0] != existing["id"]


def test_import_rejects_profile_change_before_commit(storage, fake_emb):
    with pytest.raises(RuntimeError, match="profile changed"):
        _import(storage, [_record()], fake_emb, profile="stale-profile")
    assert storage.get_stats()["total"] == 0


def test_import_rolls_back_memory_when_index_vector_write_fails(
        storage, fake_emb, monkeypatch):
    record = _record(metadata={"keywords": ["project"]})
    monkeypatch.setattr(
        storage, "_store_index_value_embeddings",
        lambda _vectors: (_ for _ in ()).throw(RuntimeError("write failed")),
    )

    with pytest.raises(RuntimeError, match="write failed"):
        _import(storage, [record], fake_emb, {("keywords", "project"): fake_emb})

    assert storage.get_stats()["total"] == 0
    assert storage.get_stats()["keyword_counts"] == {}


def test_import_rolls_back_when_memory_vector_is_missing(storage, fake_emb):
    profile = storage.get_stats()["embedding_profile"]["active"]
    with pytest.raises(KeyError):
        storage.import_records([_record()], {}, {}, profile)
    assert storage.get_stats()["total"] == 0


def test_read_only_sqlite_open_remains_available_for_offline_tools(tmp_path, fake_emb):
    path = tmp_path / "source.db"
    writable = SQLiteStorage(str(path), embedding_spec=SPEC)
    writable.add("source", fake_emb)
    writable.conn.close()
    before = path.stat().st_mtime_ns

    source = SQLiteStorage(str(path), read_only=True)
    assert source.list_memories()[0]["text"] == "source"
    source.conn.close()
    assert path.stat().st_mtime_ns == before
