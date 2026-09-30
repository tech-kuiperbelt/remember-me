"""Tests for categories in the generic SQLite metadata index.

Active-value reference:
- `categories` = metadata dimension for memory nature/classification
- keywords    = metadata dimension for content concepts

JSON/NDJSON replicas keep categories inside metadata but build no query index.
These tests exercise SQLiteStorage directly plus normalization/cap helpers.
"""

from unittest.mock import MagicMock

import pytest

from app.storage_sqlite import SQLiteStorage
from app.categories import (
    CATEGORY_VOCABULARY,
    MAX_CATEGORIES,
    normalize_categories,
    normalize_categories_filter,
    categories_dropped,
    category_counts_with_seeds,
)
from app import service as service_policy


FAKE_EMB = [0.1, 0.2, 0.3, 0.4]


@pytest.fixture
def db():
    """Fresh in-memory SQLite storage."""
    return SQLiteStorage(":memory:")


def add_memory(db, text, categories=None, namespace="ns1", **extra_meta):
    metadata = dict(extra_meta)
    if categories is not None:
        metadata["categories"] = categories
    return db.add(text, FAKE_EMB, metadata=metadata, namespace=namespace)


# -- categories module helpers --

def test_vocabulary_size_and_cap():
    assert len(CATEGORY_VOCABULARY) == 20
    assert MAX_CATEGORIES == 3


def test_normalize_categories_caps_and_cleans():
    raw = [" Bugfix ", "design", "design", 123, "", "config", "ops"]
    assert normalize_categories(raw) == ["bugfix", "design", "config"]


def test_normalize_categories_non_list():
    assert normalize_categories("design") == []
    assert normalize_categories(None) == []
    assert normalize_categories([]) == []


def test_normalize_categories_filter_uncapped():
    assert normalize_categories_filter(["a", "A", "b", "c", "d", "a"]) == ["a", "b", "c", "d"]


def test_categories_dropped_count():
    assert categories_dropped({"categories": ["x", "y", "z", "w"]}) == 1
    assert categories_dropped({"categories": ["x", "y"]}) == 0
    assert categories_dropped({"categories": "design"}) == 0
    assert categories_dropped(None) == 0


def test_category_counts_with_seeds():
    out = category_counts_with_seeds({"config": 5, "gossip": 2})
    # every seed present (count 0 when unused)
    assert all(out.get(seed, -1) == 0 for seed in CATEGORY_VOCABULARY if seed != "config")
    assert out["config"] == 5
    # extras appended after seeds, ordered by count desc
    keys = list(out)
    assert keys[: len(CATEGORY_VOCABULARY)] == list(CATEGORY_VOCABULARY)
    assert keys[-1] == "gossip"
    assert out["gossip"] == 2


# -- add: index sync --

def test_add_with_categories_syncs_index(db):
    r = add_memory(db, "text a", categories=["Bugfix", "bugfix", "TESTING"])
    assert r["created"] is True
    stats = db.get_stats()
    assert stats["category_counts"] == {"bugfix": 1, "testing": 1}


def test_add_without_categories_no_index(db):
    add_memory(db, "text b", categories=None)
    assert db.get_stats()["category_counts"] == {}


def test_add_index_caps_at_three(db):
    r = add_memory(db, "text c", categories=["a", "b", "c", "d"])
    assert db.get_stats()["category_counts"] == {"a": 1, "b": 1, "c": 1}


# -- list: category filtering --

def test_list_categories_or_match(db):
    add_memory(db, "alpha", categories=["bugfix"])
    add_memory(db, "beta", categories=["design"])
    add_memory(db, "gamma", categories=None)

    results = db.list_memories(categories=["bugfix", "design"])
    assert {r["text"] for r in results} == {"alpha", "beta"}


def test_list_categories_empty_disables_filter(db):
    add_memory(db, "alpha", categories=["bugfix"])
    add_memory(db, "beta", categories=None)
    assert len(db.list_memories(categories=[])) == 2


def test_list_categories_with_namespace_and(db):
    add_memory(db, "alpha", categories=["bugfix"], namespace="ns1")
    add_memory(db, "beta", categories=["bugfix"], namespace="ns2")

    results = db.list_memories(namespace="ns1", categories=["bugfix"])
    assert [r["text"] for r in results] == ["alpha"]


def test_list_categories_nonexistent_returns_empty(db):
    add_memory(db, "alpha", categories=["bugfix"])
    assert db.list_memories(categories=["ghost"]) == []


def test_list_combines_keywords_and_categories(db):
    add_memory(db, "alpha", keywords=["proj"], categories=["bugfix"])
    add_memory(db, "beta", keywords=["proj"], categories=None)
    add_memory(db, "gamma", keywords=None, categories=["bugfix"])

    results = db.list_memories(keywords=["proj"], categories=["bugfix"])
    assert [r["text"] for r in results] == ["alpha"]


# -- search: category filtering --

def test_search_categories_or_match(db):
    add_memory(db, "alpha", categories=["bugfix"])
    add_memory(db, "beta", categories=["design"])
    add_memory(db, "gamma", categories=None)

    results = db.search(FAKE_EMB, k=10, categories=["bugfix", "design"])
    assert {r["text"] for r in results} == {"alpha", "beta"}


def test_search_categories_empty_disables_filter(db):
    add_memory(db, "alpha", categories=["bugfix"])
    add_memory(db, "beta", categories=None)
    assert len(db.search(FAKE_EMB, k=10, categories=[])) == 2


def test_search_categories_filtered_before_k_cutoff(db):
    add_memory(db, "alpha", categories=["bugfix"])
    add_memory(db, "beta", categories=["bugfix"])
    add_memory(db, "delta", categories=["design"])

    # Filter applies BEFORE the k cutoff: only bugfix memories are candidates,
    # so with k=1 only one of the two bugfix records is returned.
    results = db.search(FAKE_EMB, k=1, categories=["bugfix"])
    assert len(results) == 1
    assert {r["text"] for r in results}.issubset({"alpha", "beta"})


def test_search_categories_unlimited_count(db):
    # The filter is NOT capped at 3 (only what a single memory carries is).
    add_memory(db, "alpha", categories=["a", "b"])
    add_memory(db, "beta", categories=["b", "c"])
    add_memory(db, "gamma", categories=["d"])

    results = db.search(FAKE_EMB, k=10, categories=["a", "b", "c", "d", "e", "f"])
    assert {r["text"] for r in results} == {"alpha", "beta", "gamma"}


def test_search_categories_nonexistent_returns_empty(db):
    add_memory(db, "alpha", categories=["bugfix"])
    assert db.search(FAKE_EMB, k=10, categories=["ghost"]) == []


def test_search_combines_namespace_and_categories(db):
    add_memory(db, "alpha", categories=["bugfix"], namespace="ns1")
    add_memory(db, "beta", categories=["bugfix"], namespace="ns2")
    results = db.search(FAKE_EMB, k=10, namespace="ns1", categories=["bugfix"])
    assert [r["text"] for r in results] == ["alpha"]


def test_memory_search_normalizes_categories(monkeypatch, service):
    storage = MagicMock()
    storage.search.return_value = []
    storage.exact_index_values.return_value = {"bug", "design"}
    monkeypatch.setattr(service, "storage", storage)
    monkeypatch.setattr(service, "embed", lambda t: FAKE_EMB)

    service.memory_search("q", categories=["  BUG ", "bug", "design"])
    _, kwargs = storage.search.call_args
    assert kwargs["categories"] == ["bug", "design"]


# -- update: replace semantics --

def test_update_replaces_categories(db):
    r = add_memory(db, "text", categories=["bugfix"])
    assert db.get_stats()["category_counts"] == {"bugfix": 1}

    db.update(r["id"], metadata={"categories": ["design", "ops"]})
    assert db.get_stats()["category_counts"] == {"design": 1, "ops": 1}
    assert db.list_memories(categories=["bugfix"]) == []


def test_update_preserves_categories_when_absent(db):
    r = add_memory(db, "text", categories=["bugfix"])
    # Singular "category" is an unrelated metadata key — must not touch the
    # plural "categories" index (naming guard against field confusion).
    db.update(r["id"], metadata={"category": "ops"})
    assert db.get_stats()["category_counts"] == {"bugfix": 1}


def test_update_empty_categories_clears(db):
    r = add_memory(db, "text", categories=["bugfix"])
    db.update(r["id"], metadata={"categories": []})
    assert db.get_stats()["category_counts"] == {}
    assert db.list_memories(categories=["bugfix"]) == []


# -- delete / clear: association cleanup --

def test_delete_cleans_category_associations(db):
    r = add_memory(db, "text", categories=["bugfix"])
    assert db.get_stats()["category_counts"] == {"bugfix": 1}
    db.delete(r["id"])
    assert db.get_stats()["category_counts"] == {}
    assert db.list_memories(categories=["bugfix"]) == []


def test_clear_namespace_cleans_associations(db):
    add_memory(db, "a", categories=["bugfix"], namespace="ns1")
    add_memory(db, "b", categories=["bugfix"], namespace="ns2")
    assert db.get_stats()["category_counts"] == {"bugfix": 2}

    db.clear(namespace="ns1")
    assert db.get_stats()["category_counts"] == {"bugfix": 1}


def test_clear_all_cleans_associations(db):
    add_memory(db, "a", categories=["bugfix"])
    add_memory(db, "b", categories=["design"])
    db.clear()
    assert db.get_stats()["category_counts"] == {}


# -- rebuild --

def test_rebuild_category_index(db):
    add_memory(db, "a", categories=["bugfix"])
    add_memory(db, "b", categories=["design"])
    # Corrupt the index deliberately.
    db.conn.execute(
        "DELETE FROM memory_index_values WHERE index_value_id IN "
        "(SELECT id FROM index_values WHERE dimension = 'categories')"
    )
    db.conn.commit()
    assert db.get_stats()["category_counts"] == {}

    result = db.rebuild_category_index()
    assert result["category_index_rebuilt"] == 2
    assert db.get_stats()["category_counts"] == {"bugfix": 1, "design": 1}
    assert {x["text"] for x in db.list_memories(categories=["bugfix"])} == {"a"}


# -- service layer integration (mock storage) --

def test_memory_add_normalizes_and_reports_truncation(monkeypatch, service):
    storage = MagicMock()
    storage.add.return_value = {"id": "x", "created": True}
    monkeypatch.setattr(service, "storage", storage)
    monkeypatch.setattr(service, "embed", lambda t: FAKE_EMB)

    out = service.memory_add("text", metadata={"categories": ["  Bug ", "bug", "design", "ops"]},
                             confirm_codes=["namespace_not_found"])
    _, kwargs = storage.add.call_args
    assert kwargs["metadata"]["categories"] == ["bug", "design", "ops"]
    assert out["categories_dropped"] == 1
    assert out["categories"] == ["bug", "design", "ops"]


def test_memory_add_duplicate_reports_no_truncation(monkeypatch, service):
    storage = MagicMock()
    storage.add.return_value = {"id": "x", "created": False}
    monkeypatch.setattr(service, "storage", storage)
    monkeypatch.setattr(service, "embed", lambda t: FAKE_EMB)

    out = service.memory_add("text", metadata={"categories": ["a", "b", "c", "d"]},
                             confirm_codes=["namespace_not_found"])
    assert "categories_dropped" not in out
    assert out["created"] is False


def test_memory_update_normalizes_and_reports_truncation(monkeypatch, service):
    storage = MagicMock()
    storage.update.return_value = True
    monkeypatch.setattr(service, "storage", storage)

    out = service.memory_update("m1", metadata={"categories": ["a", "b", "c", "d"]})
    _, kwargs = storage.update.call_args
    assert kwargs["metadata"]["categories"] == ["a", "b", "c"]
    assert out["categories_dropped"] == 1


def test_memory_list_normalizes_categories(monkeypatch, service):
    storage = MagicMock()
    storage.list_memories.return_value = []
    monkeypatch.setattr(service, "storage", storage)

    service.memory_list(categories=["  BUG ", "bug", "design"])
    _, kwargs = storage.list_memories.call_args
    assert kwargs["categories"] == ["bug", "design"]


def test_memory_stats_merges_seed_vocabulary(monkeypatch, service):
    storage = MagicMock()
    storage.get_stats.return_value = {"total": 3, "keyword_counts": {}, "category_counts": {"config": 1}}
    monkeypatch.setattr(service, "storage", storage)

    stats = service.memory_stats()
    assert stats["category_counts"]["config"] == 1
    assert stats["category_counts"]["bugfix"] == 0
    assert set(stats["category_counts"]) >= set(CATEGORY_VOCABULARY)
    assert "summarize_model" not in stats
