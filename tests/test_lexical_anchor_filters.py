"""Lexical/Anchor shared candidate filters, min_score floor and Top-K (plan 50.5.8).

Covers storage, service, Admin HTTP and MCP so the same retrieval behaves
consistently across entry points. Scores are raw ``-bm25`` and are never
compared with cosine scores.
"""
import asyncio
import json

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from app.domain.lexical import LexicalQueryError, build_match
from app.domain.namespace_policy import NamespaceNotFound
from app.service import InvalidSearchFilter
from app.transport.admin import create_admin_routes

EMB = [0.1, 0.2, 0.3]


def _add(storage, text, namespace="default", metadata=None):
    return storage.add(text, EMB, metadata=metadata or {}, namespace=namespace)["id"]


def _set_updated(storage, memory_id, stamp):
    storage.conn.execute("UPDATE memories SET updated_at = ? WHERE id = ?", (stamp, memory_id))
    storage.conn.commit()


@pytest.fixture
def filtered(storage):
    """Three records sharing the token ``filter`` with disjoint metadata."""
    ids = {
        "design_alpha": _add(storage, "filter design alpha body",
                             metadata={"categories": ["design"], "keywords": ["alpha"]}),
        "bugfix_beta": _add(storage, "filter bugfix beta body",
                            metadata={"categories": ["bugfix"], "keywords": ["beta"]}),
        "design_beta": _add(storage, "filter design beta body",
                            metadata={"categories": ["design"], "keywords": ["beta"]}),
    }
    return storage, ids


# ---------------------------------------------------------------------------
# Storage: exact filters, dimension OR / cross-dimension AND
# ---------------------------------------------------------------------------

def test_lexical_exact_filters_or_within_and_across_dimensions(filtered):
    storage, ids = filtered
    assert {h["id"] for h in storage.search_lexical("filter")} == set(ids.values())
    assert {h["id"] for h in storage.search_lexical("filter", categories=["design"])} == \
        {ids["design_alpha"], ids["design_beta"]}
    assert {h["id"] for h in storage.search_lexical("filter", keywords=["beta"])} == \
        {ids["bugfix_beta"], ids["design_beta"]}
    assert {h["id"] for h in storage.search_lexical("filter", keywords=["alpha", "beta"])} == \
        set(ids.values())
    assert {h["id"] for h in storage.search_lexical(
        "filter", categories=["design"], keywords=["beta"])} == {ids["design_beta"]}
    # Unknown exact values never resolve semantically; they just match nothing.
    assert storage.search_lexical("filter", categories=["nope"]) == []
    assert storage.search_lexical("filter", keywords=["nope"]) == []


def test_anchor_filters_and_content_isolation(storage):
    content_only = _add(storage, "AnchorIsoToken in the body",
                        metadata={"categories": ["design"]})
    with_anchor = _add(storage, "unrelated body",
                       metadata={"anchors": ["AnchorIsoToken"], "categories": ["bugfix"]})
    # Content search matches body text only; anchor search matches anchors only.
    assert {h["id"] for h in storage.search_lexical("AnchorIsoToken")} == {content_only}
    assert {h["id"] for h in storage.search_anchor("AnchorIsoToken")} == {with_anchor}
    assert storage.search_anchor("AnchorIsoToken", categories=["design"]) == []
    assert {h["id"] for h in storage.search_anchor("AnchorIsoToken", categories=["bugfix"])} == \
        {with_anchor}
    hits = storage.search_anchor("AnchorIsoToken")
    assert storage.search_anchor("AnchorIsoToken", min_score=hits[0]["score"])[0]["id"] == with_anchor
    assert storage.search_anchor("AnchorIsoToken", min_score=hits[0]["score"] + 1e-9) == []


def test_filters_apply_before_top_k(storage):
    """A filtered-out high scorer must not consume the only result slot."""
    rare = _add(storage, "UltraRareFilterToken shared", metadata={"categories": ["bugfix"]})
    common = _add(storage, "filter shared body", metadata={"categories": ["design"]})
    unfiltered = storage.search_lexical("UltraRareFilterToken filter shared", k=1)
    assert unfiltered and unfiltered[0]["id"] == rare  # rare term dominates BM25
    filtered = storage.search_lexical(
        "UltraRareFilterToken filter shared", k=1, categories=["design"])
    assert [h["id"] for h in filtered] == [common]


# ---------------------------------------------------------------------------
# Storage: min_score and ordering
# ---------------------------------------------------------------------------

def test_min_score_includes_equal_and_excludes_just_above(storage):
    _add(storage, "minScoreNeedle body")
    hits = storage.search_lexical("minScoreNeedle")
    assert len(hits) == 1
    score = hits[0]["score"]
    assert storage.search_lexical("minScoreNeedle", min_score=score)[0]["id"] == hits[0]["id"]
    assert storage.search_lexical("minScoreNeedle", min_score=score + 1e-9) == []
    assert storage.search_lexical("minScoreNeedle", min_score=0) == hits


def test_min_score_is_not_rounded(storage):
    """The raw -bm25 value is preserved, not rounded to six decimals."""
    _add(storage, "NoRoundingNeedle body")
    hits = storage.search_lexical("NoRoundingNeedle")
    raw = hits[0]["match_info"]["raw_bm25"]
    with storage._lock:
        computed = storage.conn.execute(
            "SELECT bm25(memory_content_fts) FROM memory_content_fts "
            "WHERE memory_content_fts MATCH ?",
            (build_match("NoRoundingNeedle"),),
        ).fetchone()[0]
    assert raw == float(computed)
    assert hits[0]["score"] == -raw


def test_equal_scores_tie_break_by_id_ascending(storage):
    a = _add(storage, "tieshared alpha")
    b = _add(storage, "tieshared beta")
    hits = storage.search_lexical("tieshared")
    assert {h["id"] for h in hits} == {a, b}
    assert [h["id"] for h in hits] == sorted([a, b])


# ---------------------------------------------------------------------------
# Storage: time bounds
# ---------------------------------------------------------------------------

def test_lexical_time_bounds_microseconds_and_timezone(storage):
    lower = _add(storage, "time shared lower")
    inside = _add(storage, "time shared inside")
    upper = _add(storage, "time shared upper")
    beyond = _add(storage, "time shared beyond")
    _set_updated(storage, lower, "2026-01-01T00:00:00.000100Z")
    _set_updated(storage, inside, "2026-01-01T08:00:00.000200+08:00")
    _set_updated(storage, upper, "2026-01-01T00:00:00.000300Z")
    _set_updated(storage, beyond, "2026-01-01T00:00:00.000400Z")

    strict = storage.search_lexical(
        "time", updated_after="2026-01-01T00:00:00.000100Z",
        updated_before="2026-01-01T00:00:00.000300Z")
    assert [h["id"] for h in strict] == [inside]
    # Single bound is strict: the boundary record is excluded.
    after = storage.search_lexical("time", updated_after="2026-01-01T00:00:00.000200Z")
    assert {h["id"] for h in after} == {upper, beyond}


def test_time_bound_excludes_missing_and_invalid_updated_at(storage):
    good = _add(storage, "history shared good")
    missing = _add(storage, "history shared missing")
    invalid = _add(storage, "history shared invalid")
    _set_updated(storage, good, "2026-05-01T00:00:00Z")
    _set_updated(storage, missing, "")
    _set_updated(storage, invalid, "not-a-timestamp")
    hits = storage.search_lexical("history", updated_after="2026-01-01T00:00:00Z")
    assert [h["id"] for h in hits] == [good]
    hits = storage.search_lexical("history", updated_before="2027-01-01T00:00:00Z")
    assert [h["id"] for h in hits] == [good]


# ---------------------------------------------------------------------------
# Service: validation, no embedding, no semantic resolution
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["1", True, -1, float("nan"), float("inf"), float("-inf")])
def test_service_rejects_invalid_min_score(service, bad):
    service.memory_add("ValidateNeedle body")
    with pytest.raises(InvalidSearchFilter):
        service.memory_search_lexical("ValidateNeedle", min_score=bad)
    with pytest.raises(InvalidSearchFilter):
        service.memory_search_anchor("ValidateNeedle", min_score=bad)


@pytest.mark.parametrize("bad", [True, 1.5, 0, 101, "x"])
def test_strict_top_k_is_rejected(service, bad):
    service.memory_add("KNeedle body")
    with pytest.raises(LexicalQueryError):
        service.memory_search_lexical("KNeedle", k=bad)
    with pytest.raises(LexicalQueryError):
        service.memory_search_anchor("KNeedle", k=bad)


def test_none_k_and_score_default(service):
    service.memory_add("DefaultNeedle body")
    assert len(service.memory_search_lexical("DefaultNeedle", k=None, min_score=None)) == 1


@pytest.mark.parametrize("bad_time", ["bad", 123, "2026-01-01", True])
def test_service_rejects_invalid_time_before_storage(service, monkeypatch, bad_time):
    monkeypatch.setattr(service.storage, "search_lexical",
                        lambda *a, **k: pytest.fail("invalid filter reached storage"))
    with pytest.raises(InvalidSearchFilter):
        service.memory_search_lexical("q", updated_after=bad_time)
    with pytest.raises(InvalidSearchFilter):
        service.memory_search_anchor("q", updated_before=bad_time)


@pytest.mark.parametrize("kwargs", [
    {"keywords": [f"k{i}" for i in range(21)]},
    {"categories": [f"c{i}" for i in range(21)]},
])
def test_service_caps_filter_values(service, kwargs):
    with pytest.raises(InvalidSearchFilter):
        service.memory_search_lexical("q", **kwargs)


def test_service_rejects_reversed_or_malformed_range(service):
    with pytest.raises(InvalidSearchFilter):
        service.memory_search_lexical(
            "q", updated_after="2027-01-01T00:00:00Z", updated_before="2026-01-01T00:00:00Z")
    with pytest.raises(InvalidSearchFilter):
        service.memory_search_anchor("q", updated_after=[]) 


def test_lexical_never_embeds_or_resolves(service, monkeypatch):
    service.memory_add("NoResolveNeedle body", metadata={"keywords": ["alpha"]})
    monkeypatch.setattr(service, "embed_query", lambda *a, **k: pytest.fail("embedding used"))
    monkeypatch.setattr(service, "_index_resolver",
                        lambda: pytest.fail("semantic resolver used"))
    hits = service.memory_search_lexical("NoResolveNeedle", keywords=["alpha"])
    assert len(hits) == 1


def test_unknown_namespace_rejected_before_storage(service):
    service.memory_add("KnownNs body")
    with pytest.raises(NamespaceNotFound):
        service.memory_search_lexical("KnownNs", namespace="missing")
    with pytest.raises(NamespaceNotFound):
        service.memory_search_anchor("KnownNs", namespace="missing")


# ---------------------------------------------------------------------------
# Admin HTTP
# ---------------------------------------------------------------------------

def test_admin_lexical_filters_and_error_codes(service):
    service.memory_add("AdminFilterToken body",
                       metadata={"categories": ["design"], "keywords": ["alpha"]})
    service.memory_add("AdminFilterToken other", metadata={"categories": ["bugfix"]})
    app = Starlette(routes=create_admin_routes(service=service))
    with TestClient(app) as client:
        ok = client.post("/api/memories/search/lexical",
                         json={"query": "AdminFilterToken", "categories": ["design"]})
        assert ok.status_code == 200 and len(ok.json()) == 1

        anchor = client.post("/api/memories/search/anchor",
                             json={"query": "AdminFilterToken", "min_score": 0})
        assert anchor.status_code == 200

        bad_score = client.post("/api/memories/search/lexical",
                                json={"query": "AdminFilterToken", "min_score": "x"})
        assert bad_score.status_code == 400
        assert bad_score.json()["response_code"] == "invalid_request"

        bad_time = client.post("/api/memories/search/anchor",
                               json={"query": "AdminFilterToken", "updated_after": "bad"})
        assert bad_time.status_code == 400
        assert bad_time.json()["response_code"] == "invalid_request"

        bad_k = client.post("/api/memories/search/lexical",
                            json={"query": "AdminFilterToken", "k": 1.5})
        assert bad_k.status_code == 400
        assert bad_k.json()["response_code"] == "invalid_query"

        bad_query = client.post("/api/memories/search/lexical", json={"query": "   "})
        assert bad_query.status_code == 400
        assert bad_query.json()["response_code"] == "invalid_query"

        missing = client.post("/api/memories/search/anchor",
                              json={"query": "AdminFilterToken", "namespace": "missing"})
        assert missing.status_code == 400
        assert missing.json()["response_code"] == "namespace_not_found"


# ---------------------------------------------------------------------------
# MCP: schema, envelope, receipt/audit and error shape
# ---------------------------------------------------------------------------

def _call(api, name, arguments):
    result = asyncio.run(api.handle_call_tool(name, arguments))
    return json.loads(result.content[0].text)


def test_mcp_schema_exposes_filters(service):
    from app.server import create_mcp_server
    api = create_mcp_server(service=service)
    tools = {t.name: t for t in asyncio.run(api.handle_list_tools()).tools}
    for name in ("search_lexical", "search_anchor"):
        props = set(tools[name].inputSchema["properties"])
        assert {"min_score", "categories", "keywords", "updated_after", "updated_before"} <= props


def test_mcp_filters_receipt_and_audit(service, tmp_path):
    from app.audit import AuditJournal
    from app.server import create_mcp_server
    journal = AuditJournal({"MEMORY_AUDIT_PATH": str(tmp_path / "calls.jsonl")})
    service.audit = journal
    service.memory_add("McpFilterToken body", metadata={"categories": ["design"],
                                                        "keywords": ["alpha"]})
    api = create_mcp_server(service=service)
    try:
        payload = _call(api, "search_lexical",
                        {"query": "McpFilterToken", "categories": ["design"],
                         "keywords": ["alpha"], "min_score": 0,
                         "updated_after": "2000-01-01T00:00:00Z"})
        assert payload["total"] == 1 and payload["search_id"].startswith("se_")
        feedback = _call(api, "report", {"search_id": payload["search_id"],
                                         "ratings": [{"m_id": payload["items"][0]["id"],
                                                      "rating": 1}]})
        assert feedback["recorded"] is True

        # Invalid k keeps the structured invalid_query envelope.
        bad_k = _call(api, "search_lexical", {"query": "McpFilterToken", "k": 1.5})
        assert bad_k["response_code"] == "invalid_query"
        # Invalid min_score propagates InvalidSearchFilter (filter channel).
        with pytest.raises(InvalidSearchFilter):
            _call(api, "search_anchor", {"query": "McpFilterToken", "min_score": -1})
    finally:
        journal.close()
    events = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    params = events[0]["params"]
    assert params["min_score"] == 0
    assert params["categories"] == ["design"]
    assert params["keywords"] == ["alpha"]
