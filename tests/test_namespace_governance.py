"""Namespace governance: dynamic known-namespace set, unknown-namespace
rejection, confirmation codes and the unified response envelope.

Covers the service layer (single source of truth), the MCP tool boundary
(structured ``response_code`` instead of an MCP error) and the admin HTTP
boundary (400 + machine-readable code).
"""

import asyncio
import json
import sys
from unittest.mock import MagicMock

import pytest

from app.domain.namespace_policy import (
    NamespaceNotFound,
    NAMESPACE_NOT_FOUND,
    normalize_namespace,
    validate_namespace_name,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def service(monkeypatch, service_factory):
    """app.service backed by a real in-memory SQLite store, no provider calls."""
    service = service_factory()
    from app.storage.sqlite import SQLiteStorage

    storage = SQLiteStorage(":memory:")
    monkeypatch.setattr(service, "storage", storage)
    monkeypatch.setattr(service, "embed", lambda text: [0.1, 0.2, 0.3])
    monkeypatch.setattr(service, "embed_query", lambda text: [0.1, 0.2, 0.3])
    monkeypatch.setattr(service, "embed_documents",
                        lambda texts: [[0.1, 0.2, 0.3] for _ in texts])
    yield service
    storage.close()


def _total(service) -> int:
    return service.storage.get_stats()["total"]


def _load_server(service):
    from tests.conftest import server_namespace
    return server_namespace(service)


def _mcp(ns, tool, arguments):
    result = asyncio.run(ns["handle_call_tool"](tool, arguments))
    return json.loads(result.content[0].text)


class _Request:
    def __init__(self, body, memory_id=None):
        self._body = body
        self.path_params = {"memory_id": memory_id} if memory_id else {}

    async def json(self):
        return self._body


# ---------------------------------------------------------------------------
# Name normalization / validation (pure)
# ---------------------------------------------------------------------------

def test_normalize_namespace_trims_and_treats_blank_as_none():
    assert normalize_namespace("  atos-next  ") == "atos-next"
    assert normalize_namespace("") is None
    assert normalize_namespace("   ") is None
    assert normalize_namespace(None) is None
    assert normalize_namespace(123) is None


def test_normalize_namespace_keeps_case_and_unicode():
    assert normalize_namespace(" ATOS-Next ") == "ATOS-Next"
    assert normalize_namespace(" 待报销 ") == "待报销"


def test_validate_namespace_name_rejects_length_reserved_and_control():
    with pytest.raises(ValueError):
        validate_namespace_name("x" * 65)
    for reserved in ("all", "*", "none", "null"):
        with pytest.raises(ValueError):
            validate_namespace_name(reserved)
    with pytest.raises(ValueError):
        validate_namespace_name("bad\nname")


def test_validate_namespace_name_accepts_unicode_and_default():
    assert validate_namespace_name("待报销") == "待报销"
    assert validate_namespace_name("default") == "default"


# ---------------------------------------------------------------------------
# Known namespace set
# ---------------------------------------------------------------------------

def test_known_namespaces_includes_default_on_empty_store(service):
    assert service.known_namespaces() == [service.settings.DEFAULT_NAMESPACE]


def test_first_default_add_does_not_require_confirmation(service):
    result = service.memory_add("first")
    assert service.storage.get(result["id"])["namespace"] == service.settings.DEFAULT_NAMESPACE


def test_misconfigured_default_namespace_is_validated(service, monkeypatch):
    """A reserved/oversized default must fail loudly, never be written silently."""
    monkeypatch.setattr(service, "settings", __import__("dataclasses").replace(service.settings, DEFAULT_NAMESPACE="all"))
    with pytest.raises(ValueError, match="reserved"):
        service.memory_add("body")
    assert _total(service) == 0


def test_first_confirmed_write_appears_in_list(service):
    service.memory_add("first", namespace="ghost",
                       confirm_codes=[NAMESPACE_NOT_FOUND])
    assert "ghost" in service.storage.list_namespaces()
    assert "ghost" in service.known_namespaces()


def test_namespace_leaves_list_after_last_live_memory_deleted(service):
    result = service.memory_add("only one", namespace="ghost",
                                confirm_codes=[NAMESPACE_NOT_FOUND])
    assert "ghost" in service.storage.list_namespaces()

    service.memory_delete(result["id"])
    assert "ghost" not in service.storage.list_namespaces()
    assert "ghost" not in service.known_namespaces()


# ---------------------------------------------------------------------------
# Queries: known / unknown / omitted
# ---------------------------------------------------------------------------

def test_search_unknown_namespace_raises(service):
    with pytest.raises(NamespaceNotFound) as exc:
        service.memory_search("anything", namespace="ghost")
    assert exc.value.requested == "ghost"
    assert service.settings.DEFAULT_NAMESPACE in exc.value.known


def test_non_string_namespace_is_rejected_instead_of_using_default(service):
    with pytest.raises(ValueError, match="namespace must be a string"):
        service.memory_add("body", namespace=123)
    with pytest.raises(ValueError, match="namespace must be a string"):
        service.memory_search("body", namespace=123)
    with pytest.raises(ValueError, match="namespace must be a string"):
        service.memory_list(namespace=123)


def test_search_known_namespace_without_hits_is_empty_success(service, monkeypatch):
    service.memory_add("alpha", namespace="known",
                       confirm_codes=[NAMESPACE_NOT_FOUND])
    # Orthogonal query vector + high threshold → known namespace, zero hits.
    monkeypatch.setattr(service, "embed_query", lambda text: [1.0, 0.0, 0.0])
    assert service.memory_search("unrelated", namespace="known", threshold=0.9) == []


def test_search_without_namespace_spans_all(service):
    service.memory_add("alpha", namespace="known",
                       confirm_codes=[NAMESPACE_NOT_FOUND])
    results = service.memory_search("alpha", k=10)
    assert len(results) == 1


def test_list_unknown_namespace_raises(service):
    with pytest.raises(NamespaceNotFound):
        service.memory_list(namespace="ghost")


# ---------------------------------------------------------------------------
# Writes: confirmation semantics
# ---------------------------------------------------------------------------

def test_add_unknown_namespace_rejected(service):
    with pytest.raises(NamespaceNotFound):
        service.memory_add("body", namespace="ghost")
    assert _total(service) == 0


def test_add_irrelevant_confirm_code_still_rejected(service):
    with pytest.raises(NamespaceNotFound):
        service.memory_add("body", namespace="ghost",
                           confirm_codes=["some_other_rule"])
    assert _total(service) == 0


def test_add_blank_namespace_uses_default(service):
    result = service.memory_add("body", namespace="   ",
                                confirm_codes=[NAMESPACE_NOT_FOUND])
    assert service.storage.get(result["id"])["namespace"] == service.settings.DEFAULT_NAMESPACE


def test_confirm_code_cannot_bypass_name_validation(service):
    with pytest.raises(ValueError):
        service.memory_add("body", namespace="x" * 65,
                           confirm_codes=[NAMESPACE_NOT_FOUND])
    with pytest.raises(ValueError):
        service.memory_add("body", namespace="all",
                           confirm_codes=[NAMESPACE_NOT_FOUND])
    assert _total(service) == 0


def test_update_move_to_unknown_namespace_rejected_then_confirmed(service):
    result = service.memory_add("body", namespace="known",
                                confirm_codes=[NAMESPACE_NOT_FOUND])
    mid = result["id"]

    with pytest.raises(NamespaceNotFound):
        service.memory_update(mid, namespace="ghost")
    assert service.storage.get(mid)["namespace"] == "known"

    assert service.memory_update(
        mid, namespace="ghost", confirm_codes=[NAMESPACE_NOT_FOUND]) == {"success": True}
    assert service.storage.get(mid)["namespace"] == "ghost"


def test_update_blank_namespace_leaves_it_unchanged(service):
    result = service.memory_add("body", namespace="known",
                                confirm_codes=[NAMESPACE_NOT_FOUND])
    service.memory_update(result["id"], namespace="   ")
    assert service.storage.get(result["id"])["namespace"] == "known"


def test_migrate_namespace_auto_confirms_new_target(service):
    ids = [service.memory_add(f"body {i}", namespace="known",
                              confirm_codes=[NAMESPACE_NOT_FOUND])["id"]
           for i in range(3)]

    result = service.memory_migrate_namespace("known", "brand-new")

    assert result["migrated"] == 3
    assert service.storage.get(ids[0])["namespace"] == "brand-new"
    assert "brand-new" in service.storage.list_namespaces()


def test_migrate_namespace_unknown_source_reports_no_memories(service):
    result = service.memory_migrate_namespace("ghost", "target")
    assert result["migrated"] == 0
    assert "no memories" in result["error"]


def test_rename_namespace_moves_every_live_record_in_one_call(service):
    """[55] The move is a single storage transaction, not per-record updates."""
    ids = [service.memory_add(f"body {i}", namespace="src",
                              confirm_codes=[NAMESPACE_NOT_FOUND])["id"]
           for i in range(4)]
    moved = service.storage.rename_namespace("src", "dst")
    assert moved == 4
    assert all(service.storage.get(mid)["namespace"] == "dst" for mid in ids)
    assert "src" not in service.storage.list_namespaces()


# ---------------------------------------------------------------------------
# MCP boundary: unified envelope + audit
# ---------------------------------------------------------------------------

def test_mcp_search_unknown_namespace_envelope(service, monkeypatch):
    import app.audit as audit
    recorder = MagicMock()
    monkeypatch.setattr(audit, "record", recorder)
    ns = _load_server(service)

    data = _mcp(ns, "search_semantic", {"query": "x", "namespace": "ghost"})

    assert data["response_code"] == NAMESPACE_NOT_FOUND
    assert data["items"] == [] and data["total"] == 0
    assert data["response_meta"]["requested_namespace"] == "ghost"
    assert service.settings.DEFAULT_NAMESPACE in data["response_meta"]["known_namespaces"]
    assert "ghost" in data["hint"]

    # audit event carries the original namespace and the response code
    recorded = recorder.call_args[0][2]
    assert recorded["response_code"] == NAMESPACE_NOT_FOUND


def test_mcp_search_meta_unknown_namespace_envelope(service, monkeypatch):
    import app.audit as audit
    monkeypatch.setattr(audit, "record", MagicMock())
    ns = _load_server(service)

    data = _mcp(ns, "search_meta", {"namespace": "ghost"})

    assert data["response_code"] == NAMESPACE_NOT_FOUND
    assert data["items"] == []
    assert service.settings.DEFAULT_NAMESPACE in data["response_meta"]["known_namespaces"]


def test_mcp_known_namespace_without_hits_has_no_response_code(service, monkeypatch):
    import app.audit as audit
    monkeypatch.setattr(audit, "record", MagicMock())
    ns = _load_server(service)

    data = _mcp(ns, "search_meta", {"namespace": service.settings.DEFAULT_NAMESPACE,
                                     "updated_after": "2099-01-01T00:00:00+00:00"})

    assert "response_code" not in data
    assert data["items"] == []


def test_mcp_add_unknown_namespace_rejected_without_writing(service, monkeypatch):
    import app.audit as audit
    recorder = MagicMock()
    monkeypatch.setattr(audit, "record", recorder)
    ns = _load_server(service)

    data = _mcp(ns, "add", {"text": "body", "namespace": "ghost"})

    assert data["response_code"] == NAMESPACE_NOT_FOUND
    assert "id" not in data
    assert _total(service) == 0
    # original namespace is retained in the audit params
    assert recorder.call_args[0][1]["namespace"] == "ghost"


def test_mcp_add_confirmed_creates_namespace(service, monkeypatch):
    import app.audit as audit
    monkeypatch.setattr(audit, "record", MagicMock())
    ns = _load_server(service)

    data = _mcp(ns, "add", {"text": "body", "namespace": "ghost",
                            "confirm_codes": [NAMESPACE_NOT_FOUND]})

    assert data["created"] is True
    assert "ghost" in service.storage.list_namespaces()


# ---------------------------------------------------------------------------
# Admin HTTP boundary
# ---------------------------------------------------------------------------

def test_http_add_unknown_namespace_returns_400(service, admin):

    response = asyncio.run(admin.handle_add_memory(
        _Request({"text": "body", "namespace": "ghost"})))

    assert response.status_code == 400
    body = json.loads(response.body)
    assert body["response_code"] == NAMESPACE_NOT_FOUND
    assert body["response_meta"]["requested_namespace"] == "ghost"
    assert service.settings.DEFAULT_NAMESPACE in body["response_meta"]["known_namespaces"]
    assert _total(service) == 0


def test_http_add_confirmed_creates_namespace(service, admin):

    response = asyncio.run(admin.handle_add_memory(_Request({
        "text": "body", "namespace": "ghost",
        "confirm_codes": [NAMESPACE_NOT_FOUND],
    })))

    assert response.status_code == 201
    assert "ghost" in service.storage.list_namespaces()


def test_http_update_unknown_namespace_returns_400(service, admin):

    response = asyncio.run(admin.handle_update_memory(_Request(
        {"namespace": "ghost"}, memory_id="abc")))

    assert response.status_code == 400
    assert json.loads(response.body)["response_code"] == NAMESPACE_NOT_FOUND


def test_http_search_unknown_namespace_returns_400(service, admin):

    response = asyncio.run(admin.handle_search_memories(
        _Request({"query": "x", "namespace": "ghost"})))

    assert response.status_code == 400
    body = json.loads(response.body)
    assert body["response_code"] == NAMESPACE_NOT_FOUND
    assert body["response_meta"]["requested_namespace"] == "ghost"
    assert "ghost" in body["detail"]


def test_http_list_unknown_namespace_returns_400(service, admin):
    from starlette.datastructures import QueryParams

    class _ListRequest:
        query_params = QueryParams({"namespace": "ghost"})

    response = asyncio.run(admin.handle_list_memories(_ListRequest()))
    assert response.status_code == 400
    body = json.loads(response.body)
    assert body["response_code"] == NAMESPACE_NOT_FOUND
    assert body["response_meta"]["requested_namespace"] == "ghost"


def test_http_namespace_type_errors_return_400_not_500(service, admin):

    add = asyncio.run(admin.handle_add_memory(
        _Request({"text": "body", "namespace": 123})))
    assert add.status_code == 400
    assert json.loads(add.body)["response_code"] == "invalid_namespace"

    search = asyncio.run(admin.handle_search_memories(
        _Request({"query": "body", "namespace": 123})))
    assert search.status_code == 400
    assert json.loads(search.body)["response_code"] == "invalid_namespace"
