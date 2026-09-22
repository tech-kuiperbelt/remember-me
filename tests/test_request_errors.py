"""HTTP req errors must not masquerade as namespace or backend failures."""
import asyncio
import json

import pytest
from starlette.datastructures import QueryParams
from app import admin
from app.namespace_policy import InvalidNamespace, validate_namespace_name


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


@pytest.mark.parametrize('field', ['limit', 'offset'])
def test_list_bad_numbers(field):
    status, data = invoke(admin.handle_list_memories, Request(params={field:'abc'}))
    assert status == 400 and data['response_code'] == 'invalid_request'
    assert field in data['detail']


@pytest.mark.parametrize('field', ['k', 'threshold'])
def test_search_bad_numbers(field):
    status, data = invoke(admin.handle_search_memories, Request({'query':'hello',field:'abc'}))
    assert status == 400 and data['response_code'] == 'invalid_request'


@pytest.mark.parametrize('handler', [admin.handle_add_memory, admin.handle_update_memory, admin.handle_search_memories])
@pytest.mark.parametrize('req', [Request(malformed=True), Request([]), Request('hello')])
def test_bad_json_objects(handler, req):
    status, data = invoke(handler, req)
    assert status == 400 and data['response_code'] == 'invalid_request'
    assert data['response_meta']['reason'] == data['detail']


@pytest.mark.parametrize('handler,body', [
    (admin.handle_add_memory, {}), (admin.handle_search_memories, {}),
    (admin.handle_add_memory, {'text':None}), (admin.handle_search_memories, {'query':42}),
    (admin.handle_update_memory, {'metadata':[]}), (admin.handle_update_memory, {'text':42}),
])
def test_required_fields_and_types(handler, body):
    assert invoke(handler, Request(body))[1]['response_code'] == 'invalid_request'


@pytest.mark.parametrize('handler,business,req', [
    (admin.handle_add_memory, 'memory_add', Request({'text':'hello'})),
    (admin.handle_update_memory, 'memory_update', Request({})),
    (admin.handle_search_memories, 'memory_search', Request({'query':'hello'})),
    (admin.handle_list_memories, 'memory_list', Request()),
])
def test_internal_valueerror_is_500(monkeypatch, handler, business, req):
    def fail(*args, **kwargs):
        raise ValueError('internal vector failure')
    monkeypatch.setattr(admin, business, fail)
    status, data = invoke(handler, req)
    assert status == 500 and data == {'detail':'Internal error'}


@pytest.mark.parametrize('name', ['all', '*', 'x'*65, 'bad\nname', 123])
def test_namespace_exception_compatible(name):
    with pytest.raises(InvalidNamespace) as result:
        validate_namespace_name(name)
    assert isinstance(result.value, ValueError)


@pytest.mark.parametrize('handler,business,body', [
    (admin.handle_add_memory, 'memory_add', {'text':'hello'}),
    (admin.handle_update_memory, 'memory_update', {'text':'hello'}),
    (admin.handle_search_memories, 'memory_search', {'query':'hello'}),
])
def test_embedding_length_response_unchanged(monkeypatch, handler, business, body):
    from app.embed import EmbeddingInputTooLong
    def fail(*args, **kwargs):
        raise EmbeddingInputTooLong(8001, 8000)
    monkeypatch.setattr(admin, business, fail)
    status, data = invoke(handler, Request(body))
    assert status == 413 and data['code'] == 'embedding_input_too_long'


def test_normal_crud_with_fake_embeddings(monkeypatch):
    from app import service
    from app.storage_sqlite import SQLiteStorage
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


@pytest.mark.parametrize('dimension', ['keywords', 'categories'])
def test_search_filter_limit_is_request_error(dimension):
    status, data = invoke(admin.handle_search_memories,
                          Request({'query':'hello', dimension:[str(i) for i in range(21)]}))
    assert status == 400 and data['response_code'] == 'invalid_request'
