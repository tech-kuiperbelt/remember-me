"""Main-case tests for storage.add() dedup behavior (content + namespace).

Parameterized over sqlite / json / ndjson backends via the ``storage``
fixture. Every case runs against a fresh, isolated instance.
"""


def test_add_new_returns_created_true(storage, fake_emb):
    """A brand-new record is inserted with created=True and a 16-char id."""
    result = storage.add("hello world", fake_emb, namespace="ns1")
    assert result["created"] is True
    assert isinstance(result["id"], str) and len(result["id"]) == 16


def test_add_duplicate_same_namespace_returns_existing(storage, fake_emb):
    """Same text + same namespace → no insert, created=False, same id."""
    first = storage.add("hello world", fake_emb, namespace="ns1")
    second = storage.add("hello world", fake_emb, namespace="ns1")
    assert second["created"] is False
    assert second["id"] == first["id"]
    # Only one record exists.
    assert storage.get_stats()["total"] == 1


def test_add_same_text_different_namespace_creates(storage, fake_emb):
    """Same text in a different namespace is NOT a duplicate."""
    first = storage.add("hello world", fake_emb, namespace="ns1")
    second = storage.add("hello world", fake_emb, namespace="ns2")
    assert second["created"] is True
    assert second["id"] != first["id"]
    assert storage.get_stats()["total"] == 2


def test_add_different_text_same_namespace_creates(storage, fake_emb):
    """Different text in the same namespace is NOT a duplicate."""
    first = storage.add("hello world", fake_emb, namespace="ns1")
    second = storage.add("another memory", fake_emb, namespace="ns1")
    assert second["created"] is True
    assert second["id"] != first["id"]


def test_add_after_delete_creates_new(storage, fake_emb):
    """A deleted record does not block re-adding the same content."""
    first = storage.add("hello world", fake_emb, namespace="ns1")
    storage.delete(first["id"])
    second = storage.add("hello world", fake_emb, namespace="ns1")
    assert second["created"] is True
    assert second["id"] != first["id"]


def test_add_dedup_ignores_metadata(storage, fake_emb):
    """Dedup is on (namespace, text) only — metadata differences do not matter."""
    first = storage.add("hello world", fake_emb, namespace="ns1", metadata={"a": 1})
    second = storage.add("hello world", fake_emb, namespace="ns1", metadata={"b": 2})
    assert second["created"] is False
    assert second["id"] == first["id"]
