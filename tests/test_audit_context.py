"""Explicit audit links remain unambiguous across calls and journal instances."""
import asyncio
import json

from app.audit import AuditJournal
from app.embedding.embed import EmbeddingError
from app.server import create_mcp_server
from app.transport.admin import AdminAPI
from starlette.applications import Starlette
from starlette.testclient import TestClient


def call(api, name, arguments):
    result = asyncio.run(api.handle_call_tool(name, arguments))
    return json.loads(result.content[0].text)


def events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_explicit_keyword_search_detail_and_update_links(tmp_path, service_factory):
    journal = AuditJournal({"MEMORY_AUDIT_PATH": str(tmp_path / "calls.jsonl")})
    service = service_factory()
    service.audit = journal
    api = create_mcp_server(service)
    word_query = call(api, "list_keywords", {"mode": "browse"})
    query_id = word_query["keyword_query_id"]
    added = call(api, "add", {"text": "first memory", "keyword_query_id": query_id})
    found = call(api, "search_meta", {"keyword_query_id": query_id})
    search_id = found["search_id"]
    detail = call(api, "get_details", {"memory_ids": [added["id"]],
                                       "source_search_id": search_id})
    assert detail["found"][0]["id"] == added["id"]
    call(api, "update", {"memory_id": added["id"], "summary": "revised",
                         "source_search_id": search_id, "keyword_query_id": query_id})
    call(api, "report", {"search_id": search_id,
                         "ratings": [{"m_id": added["id"], "rating": 1}]})
    history = events(tmp_path / "calls.jsonl")
    assert [entry["tool"] for entry in history] == [
        "list_keywords", "add", "search_meta", "get_details", "update", "report"]
    assert history[0]["keyword_query_id"] == query_id
    assert history[2]["keyword_query_id"] == query_id
    assert history[3]["source_search_id"] == search_id
    assert history[3]["memory_refs"] == [{"id": added["id"], "namespace": "default"}]
    assert history[4]["memory_refs"][0]["namespace_before"] == "default"
    assert history[5]["search_id"] == search_id
    assert all(entry["context"]["source"] == "mcp" for entry in history)
    assert len({entry["context"]["request_id"] for entry in history}) == len(history)
    journal.close()


def test_journals_are_independent_and_keep_cropping(tmp_path):
    first = AuditJournal({"MEMORY_AUDIT_PATH": str(tmp_path / "first.jsonl"),
                          "MEMORY_AUDIT_MAX_EVENTS": "1"})
    second = AuditJournal({"MEMORY_AUDIT_PATH": str(tmp_path / "second.jsonl")})
    first.record("add", {"text": "private text", "namespace": "one"}, {"id": "a"}, 1)
    second.record("stats", {}, {"total": 2}, 1)
    first.record("stats", {}, {"total": 1}, 1)
    first.close()
    second.record("stats", {}, {"total": 3}, 1)
    assert len(first.read_events()) == 2
    assert [entry["result"]["total"] for entry in second.read_events()] == [3, 2]
    assert "text" not in first.read_events()[-1]["params"]
    second.close()


def test_embedding_failure_has_code_and_recovery_hint(tmp_path, service_factory, monkeypatch):
    journal = AuditJournal({"MEMORY_AUDIT_PATH": str(tmp_path / "calls.jsonl")})
    service = service_factory()
    service.audit = journal
    monkeypatch.setattr(service, "memory_search", lambda *a, **kw: (_ for _ in ()).throw(
        EmbeddingError("provider unavailable")))
    result = call(create_mcp_server(service), "search_semantic", {"query": "q"})
    assert result["response_code"] == "embedding_unavailable"
    assert "provider" in result["hint"]
    logged = events(tmp_path / "calls.jsonl")[0]
    assert logged["response_code"] == "embedding_unavailable"
    assert logged["status"] == "error"
    journal.close()


def test_admin_keyword_query_id_matches_audit(tmp_path, service_factory):
    journal = AuditJournal({"MEMORY_AUDIT_PATH": str(tmp_path / "calls.jsonl")})
    service = service_factory()
    service.audit = journal
    with TestClient(Starlette(routes=AdminAPI(service).routes)) as client:
        response = client.post("/api/keywords/query", json={"mode": "browse"})
    assert response.status_code == 200
    query_id = response.json()["keyword_query_id"]
    logged = events(tmp_path / "calls.jsonl")[0]
    assert logged["keyword_query_id"] == query_id
    assert logged["context"]["source"] == "admin_http"
    journal.close()
