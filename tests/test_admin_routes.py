"""Admin routes declare their access and error policy explicitly."""

import json

import pytest
from starlette.requests import Request

from app.transport.admin import AdminAPI, domain_error_response
from app.domain.anchors import InvalidAnchors
from app.embedding.embed import EmbeddingInputTooLong
from app.domain.lexical import LexicalQueryError
from app.domain.namespace_policy import InvalidNamespace, NamespaceNotFound
from app.service import InvalidSearchFilter

ALLOWED_POLICIES = {"static", "asset", "read", "write", "action", "control", "exempt"}
EXPECTED_ORDER = [
    ("/admin/console", ("GET",)),
    ("/admin/console/static/{asset:path}", ("GET",)),
    ("/api/memories", ("GET",)),
    ("/api/memories", ("POST",)),
    ("/api/memories", ("DELETE",)),
    ("/api/memories/page", ("GET",)),
    ("/api/memories/search", ("POST",)),
    ("/api/memories/search/lexical", ("POST",)),
    ("/api/memories/search/anchor", ("POST",)),
    ("/api/memories/details", ("POST",)),
    ("/api/memories/{memory_id}", ("GET",)),
    ("/api/memories/{memory_id}", ("PUT",)),
    ("/api/memories/{memory_id}", ("DELETE",)),
    ("/api/keywords/query", ("POST",)),
    ("/api/stats", ("GET",)),
    ("/api/storage-paths", ("GET",)),
    ("/api/import", ("POST",)),
    ("/api/import/{import_id}", ("GET",)),
    ("/api/export/full", ("POST",)),
    ("/api/export/namespace", ("POST",)),
    ("/api/sync", ("POST",)),
    ("/api/shutdown", ("POST",)),
    ("/api/restart", ("POST",)),
    ("/api/namespaces/migrate", ("POST",)),
    ("/api/audit", ("GET",)),
    ("/api/maintenance", ("GET",)),
    ("/api/model", ("GET",)),
    ("/api/model/retry", ("POST",)),
    ("/api/config/model", ("GET", "POST")),
    ("/api/config", ("GET", "POST")),
    ("/api/maintenance/enter", ("POST",)),
    ("/api/maintenance/exit", ("POST",)),
    ("/api/recover", ("POST",)),
    ("/api/recover/{recover_id}", ("GET",)),
]


def test_route_table_is_ordered_unique_and_complete():
    table = AdminAPI.ROUTE_TABLE
    assert [(r.path, r.methods) for r in table] == EXPECTED_ORDER
    assert len({(r.path, r.methods) for r in table}) == len(table)
    for route in table:
        assert route.policy in ALLOWED_POLICIES
        assert route.errors in {"boundary", "none"}
        assert hasattr(AdminAPI, route.handler)


def test_action_write_and_read_sets():
    table = AdminAPI.ROUTE_TABLE
    actions = {(r.path, r.methods, r.action) for r in table if r.policy == "action"}
    assert actions == {
        ("/api/memories", ("DELETE",), "clear"),
        ("/api/export/full", ("POST",), "export_full"),
        ("/api/export/namespace", ("POST",), "export_namespace"),
        ("/api/sync", ("POST",), "sync"),
        ("/api/shutdown", ("POST",), "shutdown"),
        ("/api/restart", ("POST",), "restart"),
        ("/api/namespaces/migrate", ("POST",), "migrate_namespace"),
    }
    writes = {(r.path, r.methods) for r in table if r.policy == "write"}
    assert writes == {
        ("/api/memories", ("POST",)),
        ("/api/memories/{memory_id}", ("PUT",)),
        ("/api/memories/{memory_id}", ("DELETE",)),
    }
    # Every route has exactly one declared policy; control/exempt are unguarded.
    assert {r.policy for r in table} <= ALLOWED_POLICIES


def test_boundary_routes_use_the_shared_mapper():
    for route in AdminAPI.ROUTE_TABLE:
        handler = getattr(AdminAPI, route.handler)
        if route.errors == "boundary":
            assert hasattr(handler, "__wrapped__"), route.handler
        else:
            assert not hasattr(handler, "__wrapped__"), route.handler


def test_generated_routes_keep_declared_order_and_methods():
    api = AdminAPI.__new__(AdminAPI)
    api._service = None
    api.runtime = None
    routes = AdminAPI.routes.fget(api)
    assert [(r.path, tuple(sorted(r.methods))) for r in routes] == [
        (path, tuple(sorted(methods + (("HEAD",) if "GET" in methods else ()))))
        for path, methods in EXPECTED_ORDER
    ]


def _response(exc):
    response = domain_error_response(exc)
    return response.status_code, json.loads(response.body)


def test_domain_error_mapping_preserves_codes():
    status, body = _response(NamespaceNotFound("missing", ["a"]))
    assert status == 400 and body["response_code"] == "namespace_not_found"
    assert body["response_meta"]["known_namespaces"] == ["a"]

    status, body = _response(InvalidNamespace("bad name"))
    assert status == 400 and body["response_code"] == "invalid_namespace"

    status, body = _response(InvalidAnchors("bad anchor"))
    assert status == 400 and body["response_code"] == "invalid_anchors"
    assert body["response_meta"] == {"field": "metadata.anchors"}

    status, body = _response(EmbeddingInputTooLong(9, 8))
    assert status == 413 and body["code"] == "embedding_input_too_long"
    assert "response_code" not in body

    status, body = _response(InvalidSearchFilter("too many"))
    assert status == 400 and body["response_code"] == "invalid_request"

    status, body = _response(LexicalQueryError("blank"))
    assert status == 400 and body["response_code"] == "invalid_query"

    assert domain_error_response(ValueError("plain")) is None


@pytest.mark.parametrize('include', ['omitted', False, True, None, 0, 1, 'true'])
def test_search_resolution_envelope_and_boolean_validation(include):
    import asyncio
    from types import SimpleNamespace

    seen = []
    def search(*args, **kwargs):
        seen.append(kwargs)
        return {'items': [], 'filter_resolution': None} if kwargs['return_resolution'] else []

    api = AdminAPI(SimpleNamespace(memory_search=search))
    body = {'query': 'test'}
    if include != 'omitted':
        body['include_resolution'] = include
    async def receive():
        return {'type': 'http.request', 'body': json.dumps(body).encode(), 'more_body': False}
    request = Request({'type': 'http', 'method': 'POST', 'path': '/api/memories/search', 'headers': []}, receive)
    response = asyncio.run(api.handle_search_memories(request))
    valid = include == 'omitted' or isinstance(include, bool)
    if valid:
        assert response.status_code == 200
        assert json.loads(response.body) == ({'items': [], 'filter_resolution': None} if include is True else [])
        assert seen[0]['return_resolution'] is (include is True)
    else:
        assert response.status_code == 400
        assert json.loads(response.body)['response_code'] == 'invalid_request'
        assert not seen


def test_search_resolution_real_service_and_error_mapping(admin, service, monkeypatch):
    import asyncio

    record = service.memory_add('cache design', namespace=service.settings.DEFAULT_NAMESPACE,
                                metadata={'keywords': ['cache'], 'categories': ['design']})
    async def call(body):
        async def receive():
            return {'type': 'http.request', 'body': json.dumps(body).encode(), 'more_body': False}
        req = Request({'type': 'http', 'method': 'POST', 'path': '/api/memories/search', 'headers': []}, receive)
        return await admin.handle_search_memories(req)

    body = {'query': 'cache', 'include_resolution': True, 'keywords': ['cache'], 'categories': ['design']}
    response = asyncio.run(call(body))
    assert response.status_code == 200
    envelope = json.loads(response.body)
    assert envelope['items'][0]['id'] == record['id']
    for dimension in ('keywords', 'categories'):
        assert envelope['filter_resolution'][dimension][0]['match_type'] == 'exact'
    assert 'embedding' not in envelope['items'][0]

    response = asyncio.run(call({**body, 'namespace': 'missing-search-scope'}))
    assert response.status_code == 400
    assert json.loads(response.body)['response_code'] == 'namespace_not_found'

    def fail(*args, **kwargs):
        raise RuntimeError('provider failure')
    monkeypatch.setattr(service, 'memory_search', fail)
    response = asyncio.run(call(body))
    assert response.status_code == 500
    assert json.loads(response.body)['detail'] == 'Internal error'
