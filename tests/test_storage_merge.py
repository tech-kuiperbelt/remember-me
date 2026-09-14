"""Same-profile, atomic SQLite import behavior."""

import pytest

from app.embed import EmbeddingSpec
from app.index_value_resolver import IndexValueResolver
from app.storage_sqlite import SQLiteStorage


SPEC = EmbeddingSpec("test", "merge", 4, normalized=True).as_dict()


@pytest.fixture
def make_storage():
    def create(_tag):
        return SQLiteStorage(":memory:", embedding_spec=SPEC)
    return create


@pytest.fixture
def storage(make_storage):
    return make_storage("dst")


def test_merge_skips_duplicate_content(storage, make_storage, fake_emb):
    """Source with 1 duplicate + 1 new → imported=1, skipped=1."""
    storage.add("hello world", fake_emb, namespace="ns1")  # exists in dst

    src = make_storage("src1")
    src.add("hello world", fake_emb, namespace="ns1")      # dup
    src.add("brand new content", fake_emb, namespace="ns1")  # new

    result = storage.merge(src, namespace_prefix="")
    assert result["imported"] == 1
    assert result["skipped"] == 1
    assert result["total"] == 2
    assert len(result["imported_ids"]) == 1
    # Duplicate was not re-inserted.
    assert storage.get_stats()["total"] == 2  # hello world + brand new


def test_merge_all_duplicates_imports_zero(storage, make_storage, fake_emb):
    """Source entirely overlapping → imported=0, skipped=N."""
    storage.add("a", fake_emb, namespace="ns1")
    storage.add("b", fake_emb, namespace="ns1")

    src = make_storage("src2")
    src.add("a", fake_emb, namespace="ns1")
    src.add("b", fake_emb, namespace="ns1")

    result = storage.merge(src)
    assert result["imported"] == 0
    assert result["skipped"] == 2
    assert result["total"] == 2
    assert result["imported_ids"] == []
    assert storage.get_stats()["total"] == 2


def test_merge_namespace_prefix_distinguishes(storage, make_storage, fake_emb):
    """Prefixed namespaces must not collide with unprefixed ones."""
    storage.add("hello world", fake_emb, namespace="ns1")  # dst has ns1

    src = make_storage("src3")
    src.add("hello world", fake_emb, namespace="other")  # -> x_other, NOT a dup

    result = storage.merge(src, namespace_prefix="x_")
    assert result["imported"] == 1
    assert result["skipped"] == 0


def test_merge_dup_detection_uses_prefixed_namespace(storage, make_storage, fake_emb):
    """Duplicate detection uses the final (prefixed) namespace."""
    storage.add("hello world", fake_emb, namespace="x_ns1")  # dst has x_ns1

    src = make_storage("src4")
    src.add("hello world", fake_emb, namespace="ns1")  # -> x_ns1, IS a dup

    result = storage.merge(src, namespace_prefix="x_")
    assert result["imported"] == 0
    assert result["skipped"] == 1


def test_merge_id_collision_different_content_renames(storage, make_storage, fake_emb):
    """Same id but different content → id regenerated (renamed), not skipped."""
    existing = storage.add("hello world", fake_emb, namespace="ns1")

    src = make_storage("src5")
    # Craft a source record whose id collides with dst but content differs.
    src._add_with_id(
        existing["id"], "different content", fake_emb, {}, "nsZ",
        "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
    )

    result = storage.merge(src)
    assert result["renamed"] == 1
    assert result["imported"] == 1
    assert result["skipped"] == 0


def test_merge_rejects_different_active_profile(storage, fake_emb):
    other_spec = EmbeddingSpec("test", "other", 4, normalized=True).as_dict()
    source = SQLiteStorage(":memory:", embedding_spec=other_spec)
    source.add("source", fake_emb)

    with pytest.raises(RuntimeError, match="profiles must match"):
        storage.merge(source)
    assert storage.get_stats()["total"] == 0


def test_merge_rejects_incomplete_source_memory_vectors(storage, make_storage, fake_emb):
    source = make_storage("source")
    memory_id = source.add("source", fake_emb)["id"]
    source.conn.execute("DELETE FROM memory_embeddings WHERE memory_id=?", (memory_id,))
    source.conn.commit()

    with pytest.raises(RuntimeError, match="memory embeddings are incomplete"):
        storage.merge(source)
    assert storage.get_stats()["total"] == 0


def test_merge_rejects_incomplete_source_index_vectors(storage, make_storage, fake_emb):
    source = make_storage("source")
    source.add("source", fake_emb, metadata={"keywords": ["project"]})

    with pytest.raises(RuntimeError, match="index-value embeddings are incomplete"):
        storage.merge(source)
    assert storage.get_stats()["total"] == 0


def test_merge_copies_index_vectors_in_same_transaction(storage, make_storage, fake_emb):
    source = make_storage("source")
    resolver = IndexValueResolver(
        source, lambda _value: fake_emb,
        lambda values: [fake_emb for _value in values],
    )
    metadata = {"keywords": ["project"], "categories": ["design"]}
    source.add("source", fake_emb, metadata=metadata,
               index_embeddings=resolver.prepare_embeddings(metadata))

    result = storage.merge(source)

    assert result["imported"] == 1
    assert storage.index_embedding_completeness()["complete"] is True
    assert storage.get_stats()["keyword_counts"] == {"project": 1}
    assert storage.get_stats()["category_counts"] == {"design": 1}


def test_merge_rolls_back_everything_on_index_vector_write_failure(
        storage, make_storage, fake_emb, monkeypatch):
    source = make_storage("source")
    resolver = IndexValueResolver(
        source, lambda _value: fake_emb,
        lambda values: [fake_emb for _value in values],
    )
    metadata = {"keywords": ["project"]}
    source.add("source", fake_emb, metadata=metadata,
               index_embeddings=resolver.prepare_embeddings(metadata))

    monkeypatch.setattr(
        storage, "_store_index_value_embeddings",
        lambda _vectors: (_ for _ in ()).throw(RuntimeError("write failed")),
    )
    with pytest.raises(RuntimeError, match="write failed"):
        storage.merge(source)

    assert storage.get_stats()["total"] == 0
    assert storage.get_stats()["keyword_counts"] == {}


def test_read_only_import_source_does_not_create_missing_database(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError, match="does not exist"):
        SQLiteStorage(str(missing), read_only=True)
    assert not missing.exists()


def test_read_only_import_source_does_not_mutate_schema(tmp_path, fake_emb):
    path = tmp_path / "source.db"
    writable = SQLiteStorage(str(path), embedding_spec=SPEC)
    writable.add("source", fake_emb)
    writable.conn.close()
    before = path.stat().st_mtime_ns

    source = SQLiteStorage(str(path), read_only=True)
    assert source.list_memories()[0]["text"] == "source"
    source.conn.close()
    assert path.stat().st_mtime_ns == before
