"""Retrieval anchors: normalization, index isolation, write semantics and search."""
import asyncio
import json
import sqlite3

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from app.anchors import (MAX_ANCHORS, MAX_ANCHOR_CHARS, InvalidAnchors,
                         clean_anchors, normalize_anchors)
from app.lexical import LEXICAL_INDEX_VERSION, LexicalQueryError
from app.namespace_policy import NamespaceNotFound
from app.storage_sqlite import SQLiteStorage

EMB = [0.1, 0.2, 0.3]


# --------------------------------------------------------------------------
# Normalization / validation
# --------------------------------------------------------------------------

def test_normalize_preserves_case_and_order_and_dedupes_case_insensitively():
    assert normalize_anchors(["  RedisStore ", "redisstore", "operation-overlord"]) == [
        "RedisStore", "operation-overlord"]


def test_normalize_applies_nfkc_and_collapses_whitespace():
    # FULLWIDTH LATIN CAPITAL LETTER A normalizes to ASCII 'A' under NFKC.
    assert normalize_anchors(["\uff21  b\t c"]) == ["A b c"]


def test_normalize_dedupes_non_ascii_exactly():
    assert normalize_anchors(["数据", "数据"]) == ["数据"]
    assert normalize_anchors(["Data", "data"]) == ["Data"]


def test_normalize_rejects_invalid_input():
    for bad in (None, "x", 5, {"a": 1}):
        with pytest.raises(InvalidAnchors):
            normalize_anchors(bad)
    with pytest.raises(InvalidAnchors):
        normalize_anchors([""])
    with pytest.raises(InvalidAnchors):
        normalize_anchors(["   "])
    with pytest.raises(InvalidAnchors):
        normalize_anchors([1, 2])
    with pytest.raises(InvalidAnchors):
        normalize_anchors(["x" * (MAX_ANCHOR_CHARS + 1)])


def test_normalize_checks_raw_count_before_dedup():
    # Six items are rejected even though dedup would leave five.
    with pytest.raises(InvalidAnchors):
        normalize_anchors(["a", "b", "c", "d", "e", "a"])
    assert len(normalize_anchors(["a", "b", "c", "d", "e"])) == MAX_ANCHORS


def test_normalize_accepts_explicit_empty():
    assert normalize_anchors([]) == []


def test_clean_anchors_is_lenient_and_capped():
    assert clean_anchors(None) == []
    assert clean_anchors([1, "ok", "", "x" * 200, "ok"]) == ["ok"]
    assert clean_anchors([str(i) for i in range(10)]) == ["0", "1", "2", "3", "4"]


# --------------------------------------------------------------------------
# Storage: relations + embedding-lifecycle isolation
# --------------------------------------------------------------------------

def test_add_indexes_anchors_without_vectors(storage):
    storage.add("body", EMB, metadata={"anchors": ["RedisStore", "operation-overlord"]},
                namespace="default")
    values = [r["value"] for r in storage.conn.execute(
        "SELECT value FROM index_values WHERE dimension='anchors' ORDER BY value")]
    assert values == ["RedisStore", "operation-overlord"]
    assert storage.conn.execute("SELECT count(*) FROM index_value_embeddings").fetchone()[0] == 0
    assert storage.missing_index_value_embeddings({"anchors": ["NewAnchor"]}) == []
    assert storage.missing_queryable_index_value_embeddings() == []


def test_anchors_excluded_from_queryable_index_values(storage):
    storage.add("body", EMB, metadata={"anchors": ["RedisStore"], "keywords": ["pool"]},
                namespace="default")
    dims = {row["dimension"] for row in storage._queryable_index_values()}
    assert dims == {"keywords"}


def test_semantic_resolution_rejects_anchors(storage):
    storage.add("body", EMB, metadata={"anchors": ["RedisStore"]}, namespace="default")
    with pytest.raises(RuntimeError):
        storage.semantic_index_values("anchors", EMB)


def test_index_value_embedding_writer_rejects_anchors(storage, monkeypatch):
    monkeypatch.setattr(storage, "_active_profile_id", "ep_x")
    with pytest.raises(RuntimeError):
        storage._store_index_value_embeddings({("anchors", "RedisStore"): EMB})


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------

def test_search_anchor_contract_and_namespace(storage):
    storage.add("first body", EMB, metadata={"anchors": ["RedisStore"]}, namespace="ns-a")
    storage.add("second body", EMB, metadata={"anchors": ["RedisStore"]}, namespace="ns-b")
    storage.add("third body", EMB, metadata={"anchors": ["OtherThing"]}, namespace="ns-a")

    hits = storage.search_anchor("RedisStore")
    assert {h["id"] for h in hits}  # both namespaces
    assert all(h["match_info"]["type"] == "anchor" for h in hits)
    assert all(h["score"] == -h["match_info"]["raw_bm25"] for h in hits)

    scoped = storage.search_anchor("RedisStore", namespace="ns-a")
    assert len(scoped) == 1
    assert storage.search_anchor("RedisStore", namespace="missing") == []
    # content search must not see anchors
    assert storage.search_lexical("RedisStore") == []


def test_search_anchor_query_validation(storage):
    storage.add("body", EMB, metadata={"anchors": ["RedisStore"]}, namespace="default")
    assert storage.search_anchor("!!!") == []
    for bad in ("", "   "):
        with pytest.raises(LexicalQueryError):
            storage.search_anchor(bad)
    with pytest.raises(LexicalQueryError):
        storage.search_anchor("x", k=0)


# --------------------------------------------------------------------------
# Write-path semantics for anchors
# --------------------------------------------------------------------------

def test_update_anchor_semantics(storage):
    mid = storage.add("original body", EMB, metadata={"anchors": ["Keep"]},
                      namespace="default")["id"]

    # whitespace-equivalent body change preserves anchors
    storage.update(mid, text="original body  ")
    assert storage.get(mid)["metadata"].get("anchors") == ["Keep"]
    assert len(storage.search_anchor("Keep")) == 1

    # explicit anchors replace (and []) clears
    storage.update(mid, metadata={"anchors": ["Replaced"]})
    assert storage.get(mid)["metadata"]["anchors"] == ["Replaced"]
    storage.update(mid, metadata={"anchors": []})
    assert storage.get(mid)["metadata"]["anchors"] == []
    assert storage.search_anchor("Replaced") == []

    # explicit anchors win even alongside a body change
    storage.update(mid, text="new body", metadata={"anchors": ["Explicit"]})
    assert storage.get(mid)["metadata"]["anchors"] == ["Explicit"]

    # a materially changed body without anchors clears them
    storage.update(mid, text="totally different body")
    assert storage.get(mid)["metadata"].get("anchors") is None
    assert storage.search_anchor("Explicit") == []


def test_update_metadata_preserves_or_changes_anchors(storage):
    mid = storage.add("body", EMB, metadata={"anchors": ["Keep"]}, namespace="default")["id"]
    storage.update_metadata(mid, {"note": "x"})
    assert storage.get(mid)["metadata"]["anchors"] == ["Keep"]
    storage.update_metadata(mid, {"anchors": ["Changed"]})
    assert storage.get(mid)["metadata"]["anchors"] == ["Changed"]
    assert len(storage.search_anchor("Changed")) == 1


def test_delete_and_clear_remove_anchor_fts(storage):
    a = storage.add("a", EMB, metadata={"anchors": ["Alpha"]}, namespace="ns-a")["id"]
    b = storage.add("b", EMB, metadata={"anchors": ["Beta"]}, namespace="ns-b")["id"]
    storage.delete(a)
    assert storage.search_anchor("Alpha") == []
    assert len(storage.search_anchor("Beta")) == 1
    storage.clear(namespace="ns-b")
    assert storage.search_anchor("Beta") == []
    storage.add("c", EMB, metadata={"anchors": ["Gamma"]}, namespace="ns-a")
    storage.clear()
    assert storage.search_anchor("Gamma") == []


def test_import_records_syncs_anchor_fts(storage):
    memory_id = "b" * 16
    records = [{"id": memory_id, "text": "imported body",
                "metadata": {"anchors": ["ImportedAnchor"]}, "namespace": "default",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00", "summary": None}]
    storage.import_records(records, {memory_id: EMB}, {}, storage._active_profile_id)
    assert len(storage.search_anchor("ImportedAnchor")) == 1


def test_upgrade_backfills_anchor_relations(tmp_path):
    """An upgrade must also populate the anchors relation index, not only the FTS."""
    db = str(tmp_path / "m.db")
    store = SQLiteStorage(db)
    store.add("body", EMB, metadata={"anchors": ["UpgradeAnchor"]}, namespace="default")
    # Simulate a database upgraded by an earlier version: metadata already had
    # anchors, but the anchors relation dimension was never populated.
    store.conn.execute(
        "DELETE FROM memory_index_values WHERE index_value_id IN "
        "(SELECT id FROM index_values WHERE dimension = 'anchors')")
    store.conn.execute("DELETE FROM index_values WHERE dimension = 'anchors'")
    store.conn.execute("UPDATE storage_state SET value = ? WHERE key = 'lexical_index_version'",
                       (json.dumps(2),))
    store.conn.commit()
    assert store._metadata_index_counts("anchors") == {}
    store.close()

    reopened = SQLiteStorage(db)
    try:
        assert reopened._metadata_index_counts("anchors") == {"UpgradeAnchor": 1}
        assert len(reopened.search_anchor("UpgradeAnchor")) == 1
        stats = reopened.get_stats()
        assert stats["anchor_total"] == 1 and stats["memories_with_anchors"] == 1
        assert reopened._get_storage_state("lexical_index_version") == LEXICAL_INDEX_VERSION
    finally:
        reopened.close()


def test_audit_trims_search_anchor_results():
    from app.audit import _trim_result
    result = {"search_id": "se_x", "note": "long guidance", "total": 1,
              "items": [{"id": "m1", "summary": "s", "score": 1.5,
                         "metadata": {"blob": "x" * 5000}}]}
    trimmed = _trim_result("search_anchor", result)
    assert set(trimmed) == {"search_id", "total", "items"}
    assert trimmed["items"] == [{"id": "m1", "summary": "s", "score": 1.5}]


def test_anchor_fts_rebuilt_on_startup(tmp_path):
    db = str(tmp_path / "m.db")
    store = SQLiteStorage(db)
    store.add("body", EMB, metadata={"anchors": ["RebuildAnchor"]}, namespace="default")
    assert store._get_storage_state("lexical_index_version") == LEXICAL_INDEX_VERSION
    store.close()

    # Drop only the anchor FTS table, keep the (current) version marker.
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE memory_anchor_fts")
    conn.commit()
    conn.close()

    reopened = SQLiteStorage(db)
    try:
        assert len(reopened.search_anchor("RebuildAnchor")) == 1
        assert reopened._get_storage_state("lexical_index_version") == LEXICAL_INDEX_VERSION
    finally:
        reopened.close()


# --------------------------------------------------------------------------
# Service / MCP / Admin surface
# --------------------------------------------------------------------------

def test_service_rejects_invalid_anchors_before_writing(service):
    with pytest.raises(InvalidAnchors):
        service.memory_add("body", metadata={"anchors": ["ok", "x" * 200]})
    assert service.memory_stats()["total"] == 0
    with pytest.raises(InvalidAnchors):
        service.memory_add("body", metadata={"anchors": [1]})


def test_service_search_anchor_and_stats(service):
    service.memory_add("body", metadata={"anchors": ["RedisStore"]})
    hits = service.memory_search_anchor("RedisStore")
    assert len(hits) == 1 and "embedding" not in hits[0]
    with pytest.raises(NamespaceNotFound):
        service.memory_search_anchor("RedisStore", namespace="nope")
    stats = service.memory_stats()
    assert stats["anchor_total"] == 1 and stats["memories_with_anchors"] == 1


def test_mcp_tools_and_anchor_envelopes(service):
    from app.server import create_mcp_server
    api = create_mcp_server(service=service)
    names = {tool.name for tool in asyncio.run(api.handle_list_tools()).tools}
    assert "search_anchor" in names and len(names) == 11

    service.memory_add("McpAnchorToken body", metadata={"anchors": ["McpAnchorToken"]})
    payload = json.loads(asyncio.run(
        api.handle_call_tool("search_anchor", {"query": "McpAnchorToken"})).content[0].text)
    assert payload["total"] == 1
    assert payload["search_id"].startswith("se_")
    assert payload["items"][0]["match_info"]["type"] == "anchor"

    envelope = json.loads(asyncio.run(api.handle_call_tool(
        "search_anchor", {"query": "McpAnchorToken", "namespace": "missing"})).content[0].text)
    assert envelope["response_code"] == "namespace_not_found"
    assert envelope["search_id"].startswith("se_") and envelope["items"] == []

    invalid = json.loads(asyncio.run(api.handle_call_tool(
        "search_anchor", {"query": "   "})).content[0].text)
    assert invalid["response_code"] == "invalid_query"

    bad_write = json.loads(asyncio.run(api.handle_call_tool(
        "add", {"text": "x", "metadata": {"anchors": ["a", "b", "c", "d", "e", "f"]}})).content[0].text)
    assert bad_write["response_code"] == "invalid_anchors"


def test_admin_anchor_endpoint_and_invalid_anchors(service):
    from app.admin import create_admin_routes
    service.memory_add("AdminAnchorToken body", metadata={"anchors": ["AdminAnchorToken"]}, namespace="default")
    app = Starlette(routes=create_admin_routes(service=service))
    with TestClient(app) as client:
        ok = client.post("/api/memories/search/anchor", json={"query": "AdminAnchorToken"})
        assert ok.status_code == 200 and len(ok.json()) == 1

        invalid = client.post("/api/memories/search/anchor", json={"query": "  "})
        assert invalid.status_code == 400
        assert invalid.json()["response_code"] == "invalid_query"

        missing = client.post("/api/memories/search/anchor",
                              json={"query": "AdminAnchorToken", "namespace": "missing"})
        assert missing.status_code == 400
        assert missing.json()["response_code"] == "namespace_not_found"

        bad_add = client.post("/api/memories", json={
            "text": "y", "metadata": {"anchors": ["a", "b", "c", "d", "e", "f"]}})
        assert bad_add.status_code == 400
        assert bad_add.json()["response_code"] == "invalid_anchors"
