"""Lexical (FTS5/BM25) retrieval: query builder, write-path sync and startup rebuild."""
import asyncio
import json

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from app.lexical import (LEXICAL_INDEX_VERSION, MAX_QUERY_CHARS, MAX_QUERY_TOKENS,
                         LexicalQueryError, build_match, normalize_k)
from app.namespace_policy import NamespaceNotFound
from app.storage_sqlite import SQLiteStorage

EMB = [0.1, 0.2, 0.3]


def _add(store, text, namespace="default", metadata=None):
    return store.add(text, EMB, metadata=metadata or {}, namespace=namespace)["id"]


# --------------------------------------------------------------------------
# Query builder
# --------------------------------------------------------------------------

def test_build_match_quotes_terms_and_combines_with_or():
    assert build_match("atas-v11 mass") == '"atas" OR "v11" OR "mass"'


def test_build_match_zero_tokens_returns_none():
    assert build_match("!!! ???") is None


def test_build_match_rejects_blank_and_oversize():
    for bad in ("", "   "):
        with pytest.raises(LexicalQueryError):
            build_match(bad)
    with pytest.raises(LexicalQueryError):
        build_match("x" * (MAX_QUERY_CHARS + 1))
    with pytest.raises(LexicalQueryError):
        build_match(" ".join(f"t{i}" for i in range(MAX_QUERY_TOKENS + 1)))


def test_build_match_rejects_non_string():
    with pytest.raises(LexicalQueryError):
        build_match(None)


def test_normalize_k_range():
    assert normalize_k(10) == 10
    for bad in (0, 101, -1, "x", 1.5, True):
        with pytest.raises(LexicalQueryError):
            normalize_k(bad)


# --------------------------------------------------------------------------
# Storage search
# --------------------------------------------------------------------------

def test_search_lexical_identifier_and_score_contract(storage):
    _add(storage, "PoolAcquirePendingLimitException raised by the pool")
    _add(storage, "unrelated memory about weather")
    hits = storage.search_lexical("PoolAcquirePendingLimitException")
    assert len(hits) == 1
    assert hits[0]["match_info"]["type"] == "lexical"
    assert hits[0]["match_info"]["score_type"] == "fts5_bm25"
    assert hits[0]["score"] == -hits[0]["match_info"]["raw_bm25"]


def test_search_lexical_punctuation_only_is_empty(storage):
    _add(storage, "anything at all")
    assert storage.search_lexical("!!!") == []


def test_search_lexical_namespace_filter(storage):
    _add(storage, "shared alpha token", namespace="ns-a")
    _add(storage, "shared beta token", namespace="ns-b")
    assert len(storage.search_lexical("alpha")) == 1
    assert len(storage.search_lexical("shared", namespace="ns-a")) == 1
    assert storage.search_lexical("shared", namespace="missing") == []


def test_search_lexical_rejects_over_limit(storage):
    _add(storage, "whatever")
    with pytest.raises(LexicalQueryError):
        storage.search_lexical("x" * (MAX_QUERY_CHARS + 1))
    with pytest.raises(LexicalQueryError):
        storage.search_lexical("token", k=0)


# --------------------------------------------------------------------------
# Write-path synchronization
# --------------------------------------------------------------------------

def test_write_paths_keep_fts_in_sync(storage):
    mid = _add(storage, "FirstUniqueToken body")
    assert len(storage.search_lexical("FirstUniqueToken")) == 1

    storage.update(mid, text="SecondUniqueToken body")
    assert storage.search_lexical("FirstUniqueToken") == []
    assert len(storage.search_lexical("SecondUniqueToken")) == 1

    # Metadata-only update must leave the content index untouched.
    storage.update_metadata(mid, {"note": "x"})
    assert len(storage.search_lexical("SecondUniqueToken")) == 1

    storage.delete(mid)
    assert storage.search_lexical("SecondUniqueToken") == []


def test_clear_removes_fts_rows(storage):
    _add(storage, "ClearMeToken", namespace="ns-a")
    _add(storage, "OtherToken", namespace="ns-b")
    storage.clear(namespace="ns-b")
    assert len(storage.search_lexical("ClearMeToken")) == 1
    assert storage.search_lexical("OtherToken") == []
    storage.clear()
    assert storage.search_lexical("ClearMeToken") == []


def test_fts_failure_rolls_back_memory_write(storage, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("fts down")

    monkeypatch.setattr(storage, "_sync_content_fts", boom)
    with pytest.raises(RuntimeError, match="fts down"):
        storage.add("RollbackToken body", EMB, namespace="default")
    assert storage.search_lexical("RollbackToken") == []
    assert storage.conn.execute(
        "SELECT count(*) FROM memories WHERE deleted = 0").fetchone()[0] == 0


def test_import_records_syncs_fts(storage):
    memory_id = "a" * 16
    records = [{"id": memory_id, "text": "ImportedUniqueToken", "metadata": {},
                "namespace": "default", "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00", "summary": None}]
    storage.import_records(records, {memory_id: EMB}, {}, storage._active_profile_id)
    assert len(storage.search_lexical("ImportedUniqueToken")) == 1


# --------------------------------------------------------------------------
# Startup: version check and deterministic rebuild
# --------------------------------------------------------------------------

def test_startup_rebuilds_when_table_missing_but_version_kept(tmp_path):
    """Dropping the FTS table alone must not leave existing records unfindable."""
    import sqlite3
    db = str(tmp_path / "m.db")
    store = SQLiteStorage(db)
    _add(store, "TableGoneToken body")
    assert len(store.search_lexical("TableGoneToken")) == 1
    store.close()
    # Drop only the FTS table; the version marker stays valid.
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE memory_content_fts")
    conn.commit()
    assert conn.execute(
        "SELECT value FROM storage_state WHERE key = 'lexical_index_version'").fetchone()
    conn.close()

    reopened = SQLiteStorage(db)
    try:
        assert len(reopened.search_lexical("TableGoneToken")) == 1
        assert reopened._get_storage_state("lexical_index_version") == LEXICAL_INDEX_VERSION
    finally:
        reopened.close()


def test_startup_rebuilds_when_version_missing(tmp_path):
    db = str(tmp_path / "m.db")
    store = SQLiteStorage(db)
    _add(store, "RebuildToken body")
    store.conn.execute("DELETE FROM memory_content_fts")
    store.conn.execute("DELETE FROM storage_state WHERE key = 'lexical_index_version'")
    store.conn.commit()
    store.close()

    reopened = SQLiteStorage(db)
    try:
        assert len(reopened.search_lexical("RebuildToken")) == 1
        assert reopened._get_storage_state("lexical_index_version") == LEXICAL_INDEX_VERSION
    finally:
        reopened.close()


def test_startup_rebuilds_on_version_mismatch(tmp_path):
    db = str(tmp_path / "m.db")
    store = SQLiteStorage(db)
    _add(store, "MismatchToken body")
    store.conn.execute("UPDATE storage_state SET value = ? WHERE key = 'lexical_index_version'",
                       (json.dumps(999),))
    store.conn.commit()
    store.close()

    reopened = SQLiteStorage(db)
    try:
        assert len(reopened.search_lexical("MismatchToken")) == 1
        assert reopened._get_storage_state("lexical_index_version") == LEXICAL_INDEX_VERSION
    finally:
        reopened.close()


def test_startup_rebuild_failure_propagates(tmp_path, monkeypatch):
    import sqlite3
    db = str(tmp_path / "m.db")
    store = SQLiteStorage(db)
    _add(store, "FailToken body")
    store.close()
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE memory_content_fts")
    conn.execute("DELETE FROM storage_state WHERE key = 'lexical_index_version'")
    conn.commit()
    conn.close()

    def boom(self):
        raise RuntimeError("rebuild failed")

    monkeypatch.setattr(SQLiteStorage, "_rebuild_lexical_index", boom)
    with pytest.raises(RuntimeError, match="rebuild failed"):
        SQLiteStorage(db)


# --------------------------------------------------------------------------
# Service / MCP / Admin surface
# --------------------------------------------------------------------------

def test_service_lexical_search_and_unknown_namespace(service):
    service.memory_add("ServiceLexicalToken body")
    hits = service.memory_search_lexical("ServiceLexicalToken")
    assert len(hits) == 1
    assert "embedding" not in hits[0]
    with pytest.raises(NamespaceNotFound):
        service.memory_search_lexical("ServiceLexicalToken", namespace="nope")


def test_mcp_tool_registration_dispatch_and_envelopes(service):
    from app.server import create_mcp_server
    api = create_mcp_server(service=service)
    names = {tool.name for tool in asyncio.run(api.handle_list_tools()).tools}
    assert "search_lexical" in names and len(names) == 11

    service.memory_add("McpLexicalToken body")
    payload = json.loads(asyncio.run(
        api.handle_call_tool("search_lexical", {"query": "McpLexicalToken"})).content[0].text)
    assert payload["total"] == 1
    assert payload["search_id"].startswith("se_")
    assert payload["items"][0]["match_info"]["type"] == "lexical"

    envelope = json.loads(asyncio.run(api.handle_call_tool(
        "search_lexical", {"query": "McpLexicalToken", "namespace": "missing"})).content[0].text)
    assert envelope["response_code"] == "namespace_not_found"
    # A search tool must return the empty search envelope, not the write-rejection
    # payload (which would omit search_id and tell the caller to use confirm_codes).
    assert envelope["search_id"].startswith("se_")
    assert envelope["total"] == 0 and envelope["items"] == []
    assert "confirm_codes" not in envelope["hint"]

    invalid = json.loads(asyncio.run(api.handle_call_tool(
        "search_lexical", {"query": "   "})).content[0].text)
    assert invalid["response_code"] == "invalid_query"


def test_admin_lexical_endpoint(service):
    from app.admin import create_admin_routes
    service.memory_add("AdminLexicalToken body")
    app = Starlette(routes=create_admin_routes(service=service))
    with TestClient(app) as client:
        ok = client.post("/api/memories/search/lexical", json={"query": "AdminLexicalToken"})
        assert ok.status_code == 200
        assert len(ok.json()) == 1
        invalid = client.post("/api/memories/search/lexical", json={"query": "   "})
        assert invalid.status_code == 400
        assert invalid.json()["response_code"] == "invalid_query"
        missing = client.post("/api/memories/search/lexical",
                              json={"query": "AdminLexicalToken", "namespace": "missing"})
        assert missing.status_code == 400
        assert missing.json()["response_code"] == "namespace_not_found"
