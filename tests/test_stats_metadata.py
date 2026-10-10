"""Stats metadata API: single-snapshot storage aggregation and the Admin route.

Covers the frozen contract of ``GET /api/stats/metadata`` / ``memory_metadata_stats``
/ ``get_metadata_stats``: scoped totals, namespace options and distribution, the
three metadata dimensions (distinct / covered / coverage / Top 100), Anchor case
semantics, historical orphan values, snapshot consistency and parameter errors.
"""

import asyncio
import json
import sqlite3

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from app.service import MemoryService
from app.config.settings import Settings
from app.storage import create_storage
from app.storage.sqlite import SQLiteStorage
from app.embedding.embed import EmbeddingSpec

EMB = [0.1, 0.2, 0.3, 0.4]
SPEC = EmbeddingSpec("test", "tiny", 4, normalized=True).as_dict()
_TS = "2026-01-01T00:00:00+00:00"


def _add(store, text, namespace="default", metadata=None):
    return store.add(text, EMB, metadata=metadata or {}, namespace=namespace)["id"]


# ---------------------------------------------------------------------------
# Storage-level aggregation (SQLite + Dual consistency)
# ---------------------------------------------------------------------------

def _make(backend, tmp_path):
    if backend == "sqlite":
        return create_storage("sqlite", path=":memory:", embedding_spec=SPEC)
    return create_storage(
        "dual", primary_path=str(tmp_path / "primary.db"),
        secondary_path=str(tmp_path / "replica.jsonl"),
        embedding_spec=SPEC, start_background=False,
    )


@pytest.mark.parametrize("backend", ["sqlite", "dual"])
def test_aggregate_totals_distinct_coverage_and_top(backend, tmp_path):
    store = _make(backend, tmp_path)
    try:
        _add(store, "m1", namespace="alpha",
             metadata={"keywords": ["cache", "design"], "categories": ["design"],
                       "anchors": ["MemoryService"]})
        _add(store, "m2", namespace="alpha",
             metadata={"keywords": ["cache"], "categories": ["bugfix"]})
        _add(store, "m3", namespace="beta", metadata={"keywords": ["network"]})

        all_stats = store.get_metadata_stats()
        assert all_stats["total_memories"] == 3
        assert all_stats["namespace_live"] == {"alpha": 2, "beta": 1}
        kw = all_stats["dimensions"]["keywords"]
        assert kw["unique_count"] == 3
        assert kw["covered_memories"] == 3
        assert kw["top"] == [{"value": "cache", "count": 2},
                             {"value": "design", "count": 1},
                             {"value": "network", "count": 1}]
        cat = all_stats["dimensions"]["categories"]
        assert cat["unique_count"] == 2 and cat["covered_memories"] == 2
        assert all_stats["dimensions"]["anchors"]["unique_count"] == 1

        scoped = store.get_metadata_stats("alpha")
        assert scoped["total_memories"] == 2
        assert scoped["namespace_live"] == {"alpha": 2, "beta": 1}  # always global
        assert scoped["dimensions"]["keywords"]["unique_count"] == 2
        assert scoped["dimensions"]["keywords"]["covered_memories"] == 2
        assert scoped["dimensions"]["anchors"]["covered_memories"] == 1
    finally:
        store.stop()


def test_empty_database_and_default_namespace(tmp_path):
    store = SQLiteStorage(":memory:")
    try:
        stats = store.get_metadata_stats()
        assert stats["total_memories"] == 0
        assert stats["namespace_live"] == {}
        for dimension in ("categories", "keywords", "anchors"):
            assert stats["dimensions"][dimension] == {
                "unique_count": 0, "covered_memories": 0, "top": []}
        # A valid but empty non-default namespace is a zero scope, not an error.
        assert store.get_metadata_stats("default")["total_memories"] == 0
    finally:
        store.stop()


def test_category_seed_and_missing_metadata_excluded(tmp_path):
    store = SQLiteStorage(":memory:")
    try:
        _add(store, "plain", metadata={})           # no metadata at all
        _add(store, "seeded", metadata={"categories": ["design"]})
        stats = store.get_metadata_stats()
        # Seeds are a Service-level concept; storage only reports real usage.
        assert stats["dimensions"]["categories"]["unique_count"] == 1
        assert stats["dimensions"]["categories"]["top"] == [{"value": "design", "count": 1}]
        assert stats["dimensions"]["keywords"]["covered_memories"] == 0
        assert stats["dimensions"]["anchors"]["covered_memories"] == 0
    finally:
        store.stop()


def test_keyword_multi_value_counts_coverage_once(tmp_path):
    store = SQLiteStorage(":memory:")
    try:
        _add(store, "multi", metadata={"keywords": ["a", "b", "c"]})
        kw = store.get_metadata_stats()["dimensions"]["keywords"]
        assert kw["unique_count"] == 3
        assert kw["covered_memories"] == 1  # not the sum of per-value counts
    finally:
        store.stop()


def test_anchor_case_semantics_single_memory_vs_cross_memory(tmp_path):
    store = SQLiteStorage(":memory:")
    try:
        _add(store, "one", metadata={"anchors": ["MemoryService", "memoryservice"]})
        anchors = store.get_metadata_stats()["dimensions"]["anchors"]
        assert anchors["unique_count"] == 1  # single-array ASCII dedupe
        assert anchors["top"] == [{"value": "MemoryService", "count": 1}]

        _add(store, "two", metadata={"anchors": ["memoryservice"]})
        anchors = store.get_metadata_stats()["dimensions"]["anchors"]
        assert anchors["unique_count"] == 2  # cross-memory values keep case
        by_value = {item["value"]: item["count"] for item in anchors["top"]}
        assert by_value == {"MemoryService": 1, "memoryservice": 1}
    finally:
        store.stop()


def test_deleted_and_orphan_values_are_invisible(tmp_path):
    store = SQLiteStorage(":memory:")
    try:
        memory_id = _add(store, "gone", metadata={"keywords": ["obsolete"]})
        assert store.get_metadata_stats()["dimensions"]["keywords"]["unique_count"] == 1
        assert store.delete(memory_id) is True
        stats = store.get_metadata_stats()
        assert stats["total_memories"] == 0
        # index_values keeps the historical row, but with no live association it
        # must not count toward distinct / coverage / Top.
        assert stats["dimensions"]["keywords"]["unique_count"] == 0
        assert stats["dimensions"]["keywords"]["top"] == []
    finally:
        store.stop()


def test_top_is_capped_but_distinct_and_coverage_are_not(tmp_path):
    store = SQLiteStorage(":memory:")
    try:
        # 120 distinct keywords, each on its own memory: Top truncates at 100.
        for index in range(120):
            _add(store, f"m{index}", metadata={"keywords": [f"kw{index:03d}"]})
        kw = store.get_metadata_stats()["dimensions"]["keywords"]
        assert kw["unique_count"] == 120
        assert kw["covered_memories"] == 120
        assert len(kw["top"]) == 100
        assert [item["value"] for item in kw["top"]] == sorted(
            (item["value"] for item in kw["top"]))
    finally:
        store.stop()


def test_top_ties_sort_by_value_ascending(tmp_path):
    store = SQLiteStorage(":memory:")
    try:
        for value in ("zeta", "alpha", "mu"):
            _add(store, f"m-{value}", metadata={"keywords": [value]})
        top = store.get_metadata_stats()["dimensions"]["keywords"]["top"]
        assert [item["value"] for item in top] == ["alpha", "mu", "zeta"]
    finally:
        store.stop()


def test_dual_metadata_stats_never_scans_replica_or_embeddings(tmp_path, monkeypatch):
    dual = _make("dual", tmp_path)
    try:
        _add(dual, "m", namespace="alpha", metadata={"keywords": ["x"]})
        monkeypatch.setattr(dual, "_inspect_replica_locked",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("replica scan")))
        monkeypatch.setattr(dual.secondary, "get_stats",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("replica get_stats")))
        stats = dual.get_metadata_stats("alpha")
        assert stats["total_memories"] == 1
        assert stats["dimensions"]["keywords"]["unique_count"] == 1
    finally:
        dual.stop()


# ---------------------------------------------------------------------------
# Snapshot consistency
# ---------------------------------------------------------------------------

class _InterceptingConnection:
    """Forward to a real sqlite3 connection, firing a hook after key statements."""

    def __init__(self, real, trigger, hook):
        self._real = real
        self._trigger = trigger
        self._hook = hook
        self._fired = False

    def execute(self, sql, *args, **kwargs):
        cursor = self._real.execute(sql, *args, **kwargs)
        if not self._fired and self._trigger in sql:
            self._fired = True
            self._hook()
        return cursor

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_metadata_stats_reads_one_snapshot_then_sees_next_write(tmp_path):
    path = str(tmp_path / "snap.db")
    store = SQLiteStorage(path)
    writer = sqlite3.connect(path)
    real = store.conn
    try:
        _add(store, "base", metadata={"keywords": ["alpha"]})

        def interleave():
            writer.execute(
                "INSERT INTO memories (id, text, embedding, metadata, namespace, "
                "created_at, updated_at, synced, version, summary) "
                "VALUES ('late', 'late', NULL, '{}', 'default', ?, ?, 0, 0, 'late')",
                (_TS, _TS))
            link = writer.execute(
                "INSERT INTO index_values (dimension, value) VALUES ('keywords', 'late')")
            writer.execute(
                "INSERT INTO memory_index_values (memory_id, index_value_id) VALUES ('late', ?)",
                (link.lastrowid,))
            writer.commit()

        store.conn = _InterceptingConnection(real, "GROUP BY namespace", interleave)
        first = store.get_metadata_stats()
        assert first["total_memories"] == 1
        assert first["dimensions"]["keywords"]["unique_count"] == 1
        assert first["dimensions"]["keywords"]["covered_memories"] == 1

        store.conn = real
        second = store.get_metadata_stats()
        assert second["total_memories"] == 2
        assert second["dimensions"]["keywords"]["unique_count"] == 2
    finally:
        store.conn = real
        writer.close()
        store.close()


def test_metadata_stats_does_not_commit_outer_transaction(tmp_path):
    path = str(tmp_path / "outer.db")
    store = SQLiteStorage(path)
    other = sqlite3.connect(path)
    try:
        _add(store, "base", metadata={"keywords": ["alpha"]})
        store.conn.execute("BEGIN")
        store.conn.execute(
            "INSERT INTO memories (id, text, embedding, metadata, namespace, "
            "created_at, updated_at, synced, version, summary) "
            "VALUES ('uncommitted', 'u', NULL, '{}', 'default', ?, ?, 0, 0, 'u')",
            (_TS, _TS))
        assert store.conn.in_transaction

        result = store.get_metadata_stats()
        assert result["total_memories"] == 2  # uncommitted row is visible here
        assert store.conn.in_transaction is True  # RELEASE did not commit the caller

        store.conn.rollback()
        assert other.execute(
            "SELECT COUNT(*) FROM memories WHERE id = 'uncommitted'").fetchone()[0] == 0
    finally:
        other.close()
        store.close()


# ---------------------------------------------------------------------------
# Service semantics and public response assembly
# ---------------------------------------------------------------------------

def test_service_assembles_public_response(service):
    default_ns = service.settings.DEFAULT_NAMESPACE
    service.memory_add("a", namespace="alpha", confirm_codes=["namespace_not_found"],
                       metadata={"keywords": ["cache", "design"],
                                 "categories": ["design"], "anchors": ["MemoryService"]})
    service.memory_add("b", namespace="alpha", metadata={"keywords": ["cache"]})
    service.memory_add("c", namespace="beta", confirm_codes=["namespace_not_found"],
                       metadata={"keywords": ["network"]})

    result = service.memory_metadata_stats()
    assert result["scope"] == {"namespace": None}
    assert result["total_memories"] == 3
    assert result["default_namespace"] == default_ns
    assert set(result["namespace_options"]) == {"alpha", "beta", default_ns}
    assert result["namespace_distribution"] == [
        {"name": "alpha", "count": 2}, {"name": "beta", "count": 1}]
    kw = result["dimensions"]["keywords"]
    assert kw["unique_count"] == 3 and kw["covered_memories"] == 3
    assert kw["coverage_ratio"] == 1.0
    assert kw["top"][0] == {"value": "cache", "count": 2}
    assert "namespace_live" not in result and "scope" in result

    scoped = service.memory_metadata_stats("alpha")
    assert scoped["scope"] == {"namespace": "alpha"}
    assert scoped["total_memories"] == 2
    assert scoped["namespace_distribution"] == [{"name": "alpha", "count": 2}]
    assert scoped["dimensions"]["anchors"]["coverage_ratio"] == 0.5

    # A valid but empty default namespace yields zeros, not an error.
    empty = service.memory_metadata_stats(default_ns)
    assert empty["total_memories"] == 0
    assert empty["namespace_distribution"] == []
    assert empty["dimensions"]["keywords"]["coverage_ratio"] == 0


def test_service_rejects_blank_and_unknown_namespaces(service):
    from app.domain.namespace_policy import InvalidNamespace, NamespaceNotFound

    for blank in ("", "   "):
        with pytest.raises(InvalidNamespace):
            service.memory_metadata_stats(blank)
    with pytest.raises(InvalidNamespace):
        service.memory_metadata_stats("all")
    with pytest.raises(NamespaceNotFound):
        service.memory_metadata_stats("does-not-exist")
    with pytest.raises(InvalidNamespace):
        service.memory_metadata_stats(5)


def test_service_metadata_stats_does_not_call_legacy_stats(service, monkeypatch):
    service.memory_add("a", namespace="alpha", confirm_codes=["namespace_not_found"],
                       metadata={"keywords": ["cache"]})
    monkeypatch.setattr(service.storage, "get_stats",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("legacy stats")))
    result = service.memory_metadata_stats("alpha")
    assert result["total_memories"] == 1


# ---------------------------------------------------------------------------
# Admin HTTP route
# ---------------------------------------------------------------------------

@pytest.fixture
def agent(service):
    from app.transport.admin import AdminAPI
    with TestClient(Starlette(routes=AdminAPI(service).routes)) as client:
        yield client


def _seed(service):
    service.memory_add("a", namespace="alpha", confirm_codes=["namespace_not_found"],
                       metadata={"keywords": ["cache", "design"], "categories": ["design"]})
    service.memory_add("b", namespace="beta", confirm_codes=["namespace_not_found"],
                       metadata={"keywords": ["network"]})
    service.memory_add("c", namespace="待报销", confirm_codes=["namespace_not_found"],
                       metadata={"categories": ["other"]})


def test_route_omitted_and_scoped_namespace(agent, service):
    _seed(service)
    response = agent.get("/api/stats/metadata")
    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == {"namespace": None}
    assert body["total_memories"] == 3
    assert set(body["dimensions"]) == {"categories", "keywords", "anchors"}

    response = agent.get("/api/stats/metadata", params={"namespace": "alpha"})
    assert response.status_code == 200
    assert response.json()["scope"] == {"namespace": "alpha"}


def test_route_url_encoded_unicode_namespace(agent, service):
    _seed(service)
    response = agent.get("/api/stats/metadata?namespace=%E5%BE%85%E6%8A%A5%E9%94%80")
    assert response.status_code == 200
    assert response.json()["scope"] == {"namespace": "待报销"}
    assert response.json()["total_memories"] == 1


@pytest.mark.parametrize("query", [
    "namespace=",
    "namespace=%20%20",
    "namespace=all",
    "namespace=" + "x" * 65,
])
def test_route_invalid_namespace_is_400(agent, query):
    response = agent.get(f"/api/stats/metadata?{query}")
    assert response.status_code == 400
    assert response.json()["response_code"] == "invalid_namespace"


def test_route_unknown_namespace_is_400(agent):
    response = agent.get("/api/stats/metadata", params={"namespace": "missing"})
    assert response.status_code == 400
    body = response.json()
    assert body["response_code"] == "namespace_not_found"
    assert body["response_meta"]["requested_namespace"] == "missing"


@pytest.mark.parametrize("query", [
    "namespace=a&namespace=b",
    "namespace=a&limit=5",
    "foo=1",
])
def test_route_invalid_request_parameters_are_400(agent, query):
    response = agent.get(f"/api/stats/metadata?{query}")
    assert response.status_code == 400
    assert response.json()["response_code"] == "invalid_request"


def test_legacy_stats_contract_is_unchanged(agent, service):
    _seed(service)
    legacy = agent.get("/api/stats")
    assert legacy.status_code == 200
    old = legacy.json()
    assert old["total"] == 3
    assert "namespace_counts" in old and "default_namespace" in old
    # The new endpoint must not leak internal aggregation fields to clients.
    new = agent.get("/api/stats/metadata").json()
    assert "namespace_live" not in new


def test_service_and_http_response_match(service, agent):
    _seed(service)
    direct = service.memory_metadata_stats("alpha")
    via_http = agent.get("/api/stats/metadata", params={"namespace": "alpha"}).json()
    assert direct == via_http
