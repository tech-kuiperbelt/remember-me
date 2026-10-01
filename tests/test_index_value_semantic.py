"""Semantic keyword/category resolution and index-value vector storage."""

import pytest

from app.embedding.embed import EmbeddingSpec
from app.embedding.index_value_resolver import IndexValueResolver
from app.storage.sqlite import SQLiteStorage


SPEC = EmbeddingSpec("test", "tiny", 2, normalized=True).as_dict()


def _vector(value):
    vectors = {
        "cache refresh": [1.0, 0.0],
        "cache clear": [0.999, 0.04],
        "transaction": [0.0, 1.0],
        "bugfix": [0.8, 0.6],
    }
    return vectors[value]


def _resolver(storage, query=None):
    return IndexValueResolver(
        storage,
        embed_query=query or (lambda _value: [1.0, 0.0]),
        embed_documents=lambda values: [_vector(value) for value in values],
        top_k=3, min_score=0.65, max_gap=0.05,
    )


def _add(storage, resolver, text, metadata, namespace="ns"):
    prepared = resolver.prepare_embeddings(metadata)
    return storage.add(text, [1.0, 0.0], metadata=metadata, namespace=namespace,
                       index_embeddings=prepared)


def test_schema_and_write_store_index_value_vectors():
    storage = SQLiteStorage(":memory:", embedding_spec=SPEC)
    resolver = _resolver(storage)

    _add(storage, resolver, "one", {
        "keywords": ["cache refresh"], "categories": ["bugfix"],
    })

    tables = {row[0] for row in storage.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "index_value_embeddings" in tables
    assert storage.index_embedding_completeness() == {
        "profile_id": SPEC["profile_id"], "expected": 2, "stored": 2, "complete": True,
    }


def test_exact_match_does_not_embed_query():
    storage = SQLiteStorage(":memory:", embedding_spec=SPEC)
    resolver = _resolver(storage)
    _add(storage, resolver, "one", {"keywords": ["cache refresh"]})
    resolver.embed_query = lambda _value: pytest.fail("exact match must not embed")

    applied, detail = resolver.resolve({"keywords": ["cache refresh"]}, "ns")

    assert applied == {"keywords": ["cache refresh"]}
    assert detail["keywords"][0]["match_type"] == "exact"


def test_adaptive_top_three_keeps_close_synonyms_only():
    storage = SQLiteStorage(":memory:", embedding_spec=SPEC)
    resolver = _resolver(storage)
    _add(storage, resolver, "refresh", {"keywords": ["cache refresh"]})
    _add(storage, resolver, "clear", {"keywords": ["cache clear"]})
    _add(storage, resolver, "tx", {"keywords": ["transaction"]})

    applied, detail = resolver.resolve({"keywords": ["缓存重置"]}, "ns")

    assert applied == {"keywords": ["cache refresh", "cache clear"]}
    assert detail["keywords"][0]["match_type"] == "semantic"
    assert all(match["score"] >= 0.65 for match in detail["keywords"][0]["matches"])


def test_resolution_is_dimension_and_namespace_scoped():
    storage = SQLiteStorage(":memory:", embedding_spec=SPEC)
    resolver = _resolver(storage)
    _add(storage, resolver, "n1", {"keywords": ["cache refresh"]}, "ns1")
    _add(storage, resolver, "n2", {"keywords": ["cache clear"]}, "ns2")
    _add(storage, resolver, "category", {"categories": ["bugfix"]}, "ns1")

    applied, _detail = resolver.resolve({"keywords": ["缓存"]}, "ns1")

    assert applied == {"keywords": ["cache refresh"]}


def test_below_threshold_produces_no_applied_values():
    storage = SQLiteStorage(":memory:", embedding_spec=SPEC)
    resolver = _resolver(storage, query=lambda _value: [-1.0, 0.0])
    _add(storage, resolver, "one", {"keywords": ["cache refresh"]})

    applied, detail = resolver.resolve({"keywords": ["unrelated"]}, "ns")

    assert applied == {"keywords": []}
    assert detail["keywords"][0] == {
        "input": "unrelated", "match_type": "none", "matches": [],
    }


def test_reindex_and_codec_cover_both_vector_tables():
    storage = SQLiteStorage(":memory:", embedding_spec=SPEC)
    resolver = _resolver(storage)
    _add(storage, resolver, "one", {"keywords": ["cache refresh"]})

    result = storage.reindex_index_values(
        SPEC["profile_id"], lambda values: [_vector(value) for value in values]
    )
    recoded = storage.reencode_active_embeddings("float32")

    assert result["reindexed"] == 1
    assert recoded["reencoded"] == 1
    assert recoded["index_values_reencoded"] == 1


def test_metadata_index_rebuild_preserves_value_ids_and_vectors():
    storage = SQLiteStorage(":memory:", embedding_spec=SPEC)
    resolver = _resolver(storage)
    _add(storage, resolver, "one", {"keywords": ["cache refresh"]})
    before = storage.conn.execute(
        "SELECT id FROM index_values WHERE dimension='keywords' AND value='cache refresh'"
    ).fetchone()[0]

    storage.rebuild_keyword_index()

    after = storage.conn.execute(
        "SELECT id FROM index_values WHERE dimension='keywords' AND value='cache refresh'"
    ).fetchone()[0]
    assert after == before
    assert storage.index_embedding_completeness()["complete"] is True


def test_index_embedding_failure_happens_before_memory_write():
    storage = SQLiteStorage(":memory:", embedding_spec=SPEC)
    resolver = IndexValueResolver(
        storage, embed_query=lambda _value: [1.0, 0.0],
        embed_documents=lambda _values: (_ for _ in ()).throw(RuntimeError("provider failed")),
    )

    with pytest.raises(RuntimeError, match="provider failed"):
        prepared = resolver.prepare_embeddings({"keywords": ["new value"]})
        storage.add("must not persist", [1.0, 0.0],
                    metadata={"keywords": ["new value"]}, index_embeddings=prepared)

    assert storage.get_stats()["total"] == 0


def test_update_metadata_cannot_leave_a_new_index_value_without_embedding():
    storage = SQLiteStorage(":memory:", embedding_spec=SPEC)
    memory_id = storage.add("one", [1.0, 0.0], namespace="ns")["id"]

    with pytest.raises(RuntimeError, match="without embeddings"):
        storage.update_metadata(memory_id, {"keywords": ["cache refresh"]})

    assert storage.get(memory_id)["metadata"] == {}
    assert storage.index_embedding_completeness()["complete"] is True

    metadata = {"keywords": ["cache refresh"]}
    prepared = _resolver(storage).prepare_embeddings(metadata)
    assert storage.update_metadata(
        memory_id, metadata, index_embeddings=prepared
    ) is True
    assert storage.index_embedding_completeness() == {
        "profile_id": SPEC["profile_id"], "expected": 1, "stored": 1, "complete": True,
    }


def test_service_semantic_search_returns_resolution_and_filters(monkeypatch, service):
    from app import service as service_policy

    storage = SQLiteStorage(":memory:", embedding_spec=SPEC)
    resolver = _resolver(storage)
    _add(storage, resolver, "refresh", {"keywords": ["cache refresh"]})
    monkeypatch.setattr(service, "storage", storage)
    monkeypatch.setattr(service, "embed_query", lambda _value: [1.0, 0.0])
    monkeypatch.setattr(service, "embed_documents", lambda values: [_vector(v) for v in values])

    result = service.memory_search(
        "query", keywords=["缓存重置"], namespace="ns", return_resolution=True
    )

    assert [item["text"] for item in result["items"]] == ["refresh"]
    assert result["filter_resolution"]["keywords"][0]["match_type"] == "semantic"


def test_service_returns_empty_without_embedding_main_query_when_dimension_unresolved(monkeypatch, service):
    from app import service as service_policy

    storage = SQLiteStorage(":memory:", embedding_spec=SPEC)
    resolver = _resolver(storage, query=lambda _value: [-1.0, 0.0])
    _add(storage, resolver, "refresh", {"keywords": ["cache refresh"]})
    monkeypatch.setattr(service, "storage", storage)
    calls = []

    def query(value):
        calls.append(value)
        return [-1.0, 0.0]

    monkeypatch.setattr(service, "embed_query", query)
    monkeypatch.setattr(service, "embed_documents", lambda values: [_vector(v) for v in values])

    result = service.memory_search(
        "main query", keywords=["unrelated"], namespace="ns", return_resolution=True
    )

    assert result["items"] == []
    assert calls == ["unrelated"]


def test_service_rejects_too_many_semantic_filter_values(service):
    from app import service as service_policy

    with pytest.raises(ValueError, match="at most 20"):
        service.memory_search("query", keywords=[f"keyword-{i}" for i in range(21)])
