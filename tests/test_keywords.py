"""Main-case tests for keywords in the generic SQLite metadata index.

JSON/NDJSON replicas keep keywords inside metadata but build no query index,
so these tests exercise SQLiteStorage directly plus service normalization.
"""

from unittest.mock import MagicMock
import asyncio
import runpy
import sys

import pytest

from app.storage_sqlite import SQLiteStorage
import app.service as service


FAKE_EMB = [0.1, 0.2, 0.3, 0.4]


@pytest.fixture
def db():
    """Fresh in-memory SQLite storage."""
    return SQLiteStorage(":memory:")


def add_memory(db, text, keywords=None, namespace="ns1", **extra_meta):
    metadata = dict(extra_meta)
    if keywords is not None:
        metadata["keywords"] = keywords
    return db.add(text, FAKE_EMB, metadata=metadata, namespace=namespace)


# -- normalization helpers (service layer) --

def test_normalize_keywords_valid():
    assert service._normalize_keywords(["  Project ", "project", "Testing"]) == ["project", "testing"]


def test_normalize_keywords_filters_invalid():
    assert service._normalize_keywords(["ok", 123, None, "", "  ", "OK"]) == ["ok"]


def test_normalize_keywords_non_list_returns_none():
    assert service._normalize_keywords("project") is None
    assert service._normalize_keywords(None) is None
    assert service._normalize_keywords([]) is None


# -- add: index sync --

def test_add_with_keywords_syncs_index(db):
    r = add_memory(db, "text a", keywords=["Project", "project", "TESTING"])
    assert r["created"] is True
    stats = db.get_stats()
    assert stats["keyword_counts"] == {"project": 1, "testing": 1}


def test_add_without_keywords_no_index(db):
    add_memory(db, "text b", keywords=None)
    assert db.get_stats()["keyword_counts"] == {}


def test_add_ignores_invalid_keywords(db):
    add_memory(db, "text c", keywords=["ok", 123, None, ""])
    assert db.get_stats()["keyword_counts"] == {"ok": 1}


# -- list: keyword filtering --

def test_list_keywords_or_match(db):
    add_memory(db, "alpha", keywords=["project"])
    add_memory(db, "beta", keywords=["testing"])
    add_memory(db, "gamma", keywords=None)

    results = db.list_memories(keywords=["project", "testing"])
    texts = {r["text"] for r in results}
    assert texts == {"alpha", "beta"}


def test_list_keywords_empty_disables_filter(db):
    add_memory(db, "alpha", keywords=["project"])
    add_memory(db, "beta", keywords=None)
    results = db.list_memories(keywords=[])
    assert len(results) == 2


def test_list_keywords_with_namespace_and(db):
    add_memory(db, "alpha", keywords=["project"], namespace="ns1")
    add_memory(db, "beta", keywords=["project"], namespace="ns2")

    results = db.list_memories(namespace="ns1", keywords=["project"])
    assert [r["text"] for r in results] == ["alpha"]


def test_list_keywords_nonexistent_returns_empty(db):
    add_memory(db, "alpha", keywords=["project"])
    assert db.list_memories(keywords=["ghost"]) == []


# -- semantic search: keyword pre-filtering --

def test_search_keywords_or_match(db):
    add_memory(db, "alpha", keywords=["project"])
    add_memory(db, "beta", keywords=["testing"])
    add_memory(db, "gamma", keywords=None)

    results = db.search(FAKE_EMB, k=10, keywords=["project", "testing"])
    assert {r["text"] for r in results} == {"alpha", "beta"}


def test_search_keywords_filtered_before_k_cutoff(db):
    add_memory(db, "alpha", keywords=["project"])
    add_memory(db, "beta", keywords=["project"])
    add_memory(db, "gamma", keywords=["other"])

    results = db.search(FAKE_EMB, k=1, keywords=["project"])
    assert len(results) == 1
    assert results[0]["text"] in {"alpha", "beta"}


def test_search_combines_keywords_and_categories(db):
    db.add("alpha", FAKE_EMB,
           metadata={"keywords": ["project"], "categories": ["bugfix"]},
           namespace="ns1")
    db.add("beta", FAKE_EMB,
           metadata={"keywords": ["project"], "categories": ["design"]},
           namespace="ns1")
    db.add("gamma", FAKE_EMB,
           metadata={"keywords": ["other"], "categories": ["bugfix"]},
           namespace="ns1")

    results = db.search(FAKE_EMB, k=10, keywords=["project"], categories=["bugfix"])
    assert [r["text"] for r in results] == ["alpha"]


# -- update: replace semantics --

def test_update_replaces_keywords(db):
    r = add_memory(db, "text", keywords=["project"])
    assert db.get_stats()["keyword_counts"] == {"project": 1}

    db.update(r["id"], metadata={"keywords": ["testing", "deployment"]})
    assert db.get_stats()["keyword_counts"] == {"testing": 1, "deployment": 1}
    assert {x["text"] for x in db.list_memories(keywords=["project"])} == set()


def test_update_preserves_keywords_when_absent(db):
    r = add_memory(db, "text", keywords=["project"])
    db.update(r["id"], metadata={"category": "ops"})  # no keywords key
    assert db.get_stats()["keyword_counts"] == {"project": 1}


def test_update_empty_keywords_clears(db):
    r = add_memory(db, "text", keywords=["project"])
    db.update(r["id"], metadata={"keywords": []})
    assert db.get_stats()["keyword_counts"] == {}
    assert db.list_memories(keywords=["project"]) == []


# -- delete / clear: association cleanup --

def test_delete_cleans_keyword_associations(db):
    r = add_memory(db, "text", keywords=["project"])
    assert db.get_stats()["keyword_counts"] == {"project": 1}
    db.delete(r["id"])
    assert db.get_stats()["keyword_counts"] == {}
    assert db.list_memories(keywords=["project"]) == []


def test_clear_namespace_cleans_associations(db):
    add_memory(db, "a", keywords=["project"], namespace="ns1")
    add_memory(db, "b", keywords=["project"], namespace="ns2")
    assert db.get_stats()["keyword_counts"] == {"project": 2}

    db.clear(namespace="ns1")
    assert db.get_stats()["keyword_counts"] == {"project": 1}


# -- rebuild --

def test_rebuild_keyword_index(db):
    r1 = add_memory(db, "a", keywords=["project"])
    add_memory(db, "b", keywords=["testing"])
    # Corrupt the index deliberately.
    db.conn.execute(
        "DELETE FROM memory_index_values WHERE index_value_id IN "
        "(SELECT id FROM index_values WHERE dimension = 'keywords')"
    )
    db.conn.commit()
    assert db.get_stats()["keyword_counts"] == {}

    result = db.rebuild_keyword_index()
    assert result["keyword_index_rebuilt"] == 2
    assert db.get_stats()["keyword_counts"] == {"project": 1, "testing": 1}
    assert {x["text"] for x in db.list_memories(keywords=["project"])} == {"a"}


# -- merge imports keywords --

def test_merge_syncs_keywords(db):
    from app.storage_json import JSONStorage
    src = JSONStorage("/tmp/keywords_merge_src.json")
    src.add("imported", FAKE_EMB, metadata={"keywords": ["arch"]}, namespace="ns1")

    result = db.merge(src)
    assert result["imported"] == 1
    assert db.get_stats()["keyword_counts"] == {"arch": 1}


# -- service layer integration (mock storage) --

def test_memory_add_normalizes_keywords(monkeypatch):
    storage = MagicMock()
    storage.add.return_value = {"id": "x", "created": True}
    monkeypatch.setattr(service, "storage", storage)
    monkeypatch.setattr(service, "embed", lambda t: FAKE_EMB)

    service.memory_add("text", metadata={"keywords": ["  Proj ", "proj"]})
    _, kwargs = storage.add.call_args
    assert kwargs["metadata"]["keywords"] == ["proj"]


def test_memory_list_normalizes_keywords(monkeypatch):
    storage = MagicMock()
    storage.list_memories.return_value = []
    monkeypatch.setattr(service, "storage", storage)

    service.memory_list(keywords=["  PROJ ", "proj"])
    _, kwargs = storage.list_memories.call_args
    assert kwargs["keywords"] == ["proj"]


def test_memory_search_normalizes_keywords(monkeypatch):
    storage = MagicMock()
    storage.search.return_value = []
    monkeypatch.setattr(service, "storage", storage)
    monkeypatch.setattr(service, "embed", lambda t: FAKE_EMB)

    service.memory_search("q", keywords=["  PROJ ", "proj", "Testing"])
    _, kwargs = storage.search.call_args
    assert kwargs["keywords"] == ["proj", "testing"]


def test_search_semantic_schema_exposes_both_index_filters():
    real_argv = sys.argv
    try:
        sys.argv = ["server"]
        namespace = runpy.run_module("app.server", run_name="not_main")
    finally:
        sys.argv = real_argv
    result = asyncio.run(namespace["handle_list_tools"]())
    tool = next(t for t in result.tools if t.name == "search_semantic")
    properties = tool.inputSchema["properties"]
    assert "keywords" in properties
    assert "categories" in properties


# ---------------------------------------------------------------------------
# MCP-boundary trimming of keyword_counts (app.keyword_trim)
# ---------------------------------------------------------------------------

def test_trim_keeps_high_citation_and_cjk():
    from app.keyword_trim import trim_keyword_counts

    kc = {
        "wi": 51,                       # high citation -> kept
        "五月花计划": 39,                 # CJK, high citation -> kept
        "堆场规划": 1,                   # CJK, cited once -> kept (protection)
        "cntrwi": 2,                    # non-CJK, cited 2 <= min 2 -> trimmed
        "cntrwi_state": 3,              # non-CJK, cited 3 > min 2 -> kept
        "0fa7ba33b4099ae0": 1,          # non-CJK, cited once -> trimmed
        "thvt-5220": 2,                 # non-CJK, cited 2 <= min 2 -> trimmed
    }
    out = trim_keyword_counts(kc)
    assert out == {"wi": 51, "五月花计划": 39, "堆场规划": 1, "cntrwi_state": 3}


def test_trim_min_citations_env():
    import os
    from app.keyword_trim import trim_keyword_counts

    kc = {"a": 1, "b": 2, "c": 3, "中文词": 1}
    os.environ["MEMORY_KEYWORDS_MIN_CITATIONS"] = "2"
    try:
        out = trim_keyword_counts(kc)
        assert out == {"c": 3, "中文词": 1}  # c>2 kept; b=2 trimmed; CJK always kept
    finally:
        os.environ.pop("MEMORY_KEYWORDS_MIN_CITATIONS", None)


def test_trim_disabled_with_zero():
    import os
    from app.keyword_trim import trim_keyword_counts

    kc = {"a": 1, "b": 2}
    os.environ["MEMORY_KEYWORDS_MIN_CITATIONS"] = "0"
    try:
        assert trim_keyword_counts(kc) == kc  # 0 disables trimming
    finally:
        os.environ.pop("MEMORY_KEYWORDS_MIN_CITATIONS", None)
