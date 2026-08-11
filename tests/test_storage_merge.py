"""Main-case tests for storage.merge() dedup behavior (content + namespace).

Parameterized over sqlite / json / ndjson via the ``storage`` fixture.
The source storage is always an independent instance (distinct file / :memory:
connection), so cross-contamination between source and destination is
impossible by construction.
"""


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
