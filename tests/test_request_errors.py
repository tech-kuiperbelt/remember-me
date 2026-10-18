"""HTTP req errors must not masquerade as namespace or backend failures."""
import asyncio
import json

import pytest
from starlette.datastructures import QueryParams
from app.domain.namespace_policy import InvalidNamespace, validate_namespace_name


class Request:
    def __init__(self, body=None, params=None, malformed=False):
        self.body = body
        self.query_params = QueryParams(params or {})
        self.path_params = {'memory_id': 'test'}
        self.malformed = malformed

    async def json(self):
        if self.malformed:
            raise json.JSONDecodeError('invalid', '{', 1)
        return self.body


def invoke(handler, req):
    response = asyncio.run(handler(req))
    return response.status_code, json.loads(response.body)


def test_admin_stats_exposes_configured_default_only_in_admin(admin, monkeypatch):
    from dataclasses import replace
    monkeypatch.setattr(admin.service, 'settings', replace(admin.service.settings,
                        DEFAULT_NAMESPACE='project-not-default'))
    status, data = invoke(admin.handle_stats, Request())
    assert status == 200
    assert data['default_namespace'] == 'project-not-default'
    assert 'default_namespace' not in admin.service.memory_stats()


@pytest.mark.parametrize('field', ['limit', 'offset'])
def test_list_bad_numbers(field, admin):
    status, data = invoke(admin.handle_list_memories, Request(params={field:'abc'}))
    assert status == 400 and data['response_code'] == 'invalid_request'
    assert field in data['detail']


@pytest.mark.parametrize('field', ['k', 'threshold'])
def test_search_bad_numbers(field, admin):
    status, data = invoke(admin.handle_search_memories, Request({'query':'hello',field:'abc'}))
    assert status == 400 and data['response_code'] == 'invalid_request'


@pytest.mark.parametrize('handler', ['handle_add_memory', 'handle_update_memory', 'handle_search_memories'])
@pytest.mark.parametrize('req', [Request(malformed=True), Request([]), Request('hello')])
def test_bad_json_objects(handler, req, admin):
    status, data = invoke(getattr(admin, handler), req)
    assert status == 400 and data['response_code'] == 'invalid_request'
    assert data['response_meta']['reason'] == data['detail']


@pytest.mark.parametrize('handler,body', [
    ('handle_add_memory', {}), ('handle_search_memories', {}),
    ('handle_add_memory', {'text':None}), ('handle_search_memories', {'query':42}),
    ('handle_update_memory', {'metadata':[]}), ('handle_update_memory', {'text':42}),
    ('handle_update_memory', {'summary':42}),
])
def test_required_fields_and_types(handler, body, admin):
    assert invoke(getattr(admin, handler), Request(body))[1]['response_code'] == 'invalid_request'


@pytest.mark.parametrize('handler,business,req', [
    ('handle_add_memory', 'memory_add', Request({'text':'hello'})),
    ('handle_update_memory', 'memory_update', Request({})),
    ('handle_search_memories', 'memory_search', Request({'query':'hello'})),
    ('handle_list_memories', 'memory_list', Request()),
])
def test_internal_valueerror_is_500(monkeypatch, handler, business, req, admin):
    def fail(*args, **kwargs):
        raise ValueError('internal vector failure')
    monkeypatch.setattr(admin.service, business, fail)
    status, data = invoke(getattr(admin, handler), req)
    assert status == 500 and data == {'detail':'Internal error'}


@pytest.mark.parametrize('name', ['all', '*', 'x'*65, 'bad\nname', 123])
def test_namespace_exception_compatible(name):
    with pytest.raises(InvalidNamespace) as result:
        validate_namespace_name(name)
    assert isinstance(result.value, ValueError)


@pytest.mark.parametrize('handler,business,body', [
    ('handle_add_memory', 'memory_add', {'text':'hello'}),
    ('handle_update_memory', 'memory_update', {'text':'hello'}),
    ('handle_search_memories', 'memory_search', {'query':'hello'}),
])
def test_embedding_length_response_unchanged(monkeypatch, handler, business, body, admin):
    from app.embedding.embed import EmbeddingInputTooLong
    def fail(*args, **kwargs):
        raise EmbeddingInputTooLong(8001, 8000)
    monkeypatch.setattr(admin.service, business, fail)
    status, data = invoke(getattr(admin, handler), Request(body))
    assert status == 413 and data['code'] == 'embedding_input_too_long'


def test_normal_crud_with_fake_embeddings(monkeypatch, service, admin):
    from app.storage.sqlite import SQLiteStorage
    db = SQLiteStorage(':memory:')
    monkeypatch.setattr(service, 'storage', db)
    monkeypatch.setattr(service, 'embed', lambda _: [1., 0.])
    monkeypatch.setattr(service, 'embed_query', lambda _: [1., 0.])
    try:
        status, added = invoke(admin.handle_add_memory, Request({'text':'hello'}))
        assert status == 201
        req = Request({'text':'updated'})
        req.path_params['memory_id'] = added['id']
        assert invoke(admin.handle_update_memory, req)[0] == 200
        assert invoke(admin.handle_list_memories, Request())[1][0]['text'] == 'updated'
        assert invoke(admin.handle_search_memories, Request({'query':'updated'}))[0] == 200
        assert invoke(admin.handle_delete_memory, req)[0] == 200
        assert invoke(admin.handle_list_memories, Request())[1] == []
    finally:
        db.close()


def test_migrate_namespace_keeps_plain_error_envelope(admin):
    """Namespace migrate keeps its own error shape (detail only, no response_code)."""
    status, data = invoke(admin.handle_migrate_namespace,
                          Request({'source': 'known', 'target': 'all'}))
    assert status == 400
    assert 'response_code' not in data
    assert data['detail']


@pytest.mark.parametrize('dimension', ['keywords', 'categories'])
def test_search_filter_limit_is_request_error(dimension, admin):
    status, data = invoke(admin.handle_search_memories,
                          Request({'query':'hello', dimension:[str(i) for i in range(21)]}))
    assert status == 400 and data['response_code'] == 'invalid_request'


# -- [55] malformed/typed bodies must be request errors, not 500s -------------

@pytest.mark.parametrize('req', [Request(malformed=True), Request([]), Request('hello')])
def test_get_details_bad_json_objects(req, admin):
    status, data = invoke(admin.handle_get_details, req)
    assert status == 400 and data['response_code'] == 'invalid_request'


def test_get_details_rejects_non_string_ids(admin):
    status, data = invoke(admin.handle_get_details, Request({'memory_ids': [1, 2]}))
    assert status == 400 and data['response_code'] == 'invalid_request'
    assert 'memory_ids' in data['detail']


@pytest.mark.parametrize('req', [Request(malformed=True), Request([]), Request('hello'),
                                 Request({'source': 'known'}), Request({'target': 'known'})])
def test_migrate_namespace_bad_json_objects(req, admin):
    """Migrate keeps its plain envelope but must still reject malformed bodies as 400."""
    status, data = invoke(admin.handle_migrate_namespace, req)
    assert status == 400
    assert 'response_code' not in data
    assert data['detail']


def test_list_negative_limit_is_request_error(admin):
    """A negative limit must not silently mean 'no SQL limit'."""
    status, data = invoke(admin.handle_list_memories, Request(params={'limit': '-1'}))
    assert status == 400 and data['response_code'] == 'invalid_request'


def test_list_zero_and_oversized_limit_is_request_error(admin):
    for value in ('0', '1001'):
        status, data = invoke(admin.handle_list_memories, Request(params={'limit': value}))
        assert status == 400 and data['response_code'] == 'invalid_request', value


# -- [55] MCP numeric arguments are correctable request errors ----------------

def _mcp(service, tool, arguments):
    import asyncio, json as _json
    from tests.conftest import server_namespace
    ns = server_namespace(service)
    result = asyncio.run(ns['handle_call_tool'](tool, arguments))
    return _json.loads(result.content[0].text)


@pytest.mark.parametrize('tool,arguments', [
    ('search_semantic', {'query': 'hello', 'k': 'abc'}),
    ('search_semantic', {'query': 'hello', 'threshold': 'x'}),
    ('search_semantic', {'query': 'hello', 'k': -1}),
    ('search_meta', {'limit': 'abc'}),
    ('search_meta', {'limit': -1}),
    ('search_meta', {'offset': -1}),
])
def test_mcp_numeric_arguments_are_structured(tool, arguments, service):
    data = _mcp(service, tool, arguments)
    assert data['response_code'] == 'invalid_request'
