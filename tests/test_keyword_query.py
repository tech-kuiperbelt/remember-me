"""Independent keyword discovery: complete vocabulary, scope and API contracts."""
import asyncio
import json
import sys

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from app.embedding.embed import EmbeddingSpec
from app.storage.sqlite import SQLiteStorage


@pytest.fixture(params=['sqlite', 'dual'])
def service(request, monkeypatch, tmp_path, service_factory):
    svc = service_factory()
    db = SQLiteStorage(':memory:', embedding_spec=EmbeddingSpec('test', 'keywords', 2).as_dict())
    vectors = {'cache': [1., 0.], 'cache clear': [0.99, 0.1],
               'transaction': [0., 1.], 'rare_id': [0., 1.], '中文': [0., 1.],
               '100%': [0., 1.]}
    def add(text, namespace, words):
        return db.add(text, [1., 0.], metadata={'keywords': words}, namespace=namespace,
                      index_embeddings={('keywords', w): vectors[w] for w in words})
    add('one', 'a', ['cache', 'cache clear', 'rare_id', '中文', '100%'])
    add('two', 'a', ['cache'])
    add('other', 'b', ['cache clear', 'transaction'])
    removed = add('deleted', 'a', ['transaction'])
    db.delete(removed['id'])
    store = db
    if request.param == 'dual':
        from app.storage.dual import DualStorage
        from app.storage.ndjson import NDJSONReplica
        store = DualStorage(db, NDJSONReplica(str(tmp_path / 'replica.jsonl')), start_background=False)
    monkeypatch.setattr(svc, 'storage', store)
    monkeypatch.setattr(svc, 'embed_query', lambda q: [0., 1.] if q == 'transaction' else [1., 0.])
    yield svc
    if request.param == 'dual':
        store.stop()
    db.close()


def test_browse_scope_pagination_and_no_embedding(service, monkeypatch):
    monkeypatch.setattr(service, 'embed_query', lambda _: pytest.fail('no inference for browse/literal'))
    result = service.memory_list_keywords(namespace='a', limit=2)
    assert result['results'][0]['total'] == 5
    assert result['results'][0]['items'][0] == {'keyword': 'cache', 'count': 2}
    pages = [service.memory_list_keywords(namespace='a', sort_by='keyword', limit=2, offset=o)
             ['results'][0]['items'] for o in (0, 2, 4)]
    assert [r['keyword'] for page in pages for r in page] == ['100%', 'cache', 'cache clear', 'rare_id', '中文']
    assert service.memory_list_keywords()['results'][0]['total'] == 6
    assert service.memory_list_keywords(mode='literal', queries=[' CACHE '])['results'][0]['items'] == [
        {'keyword': 'cache', 'count': 2}, {'keyword': 'cache clear', 'count': 2}]
    for query, word in [('%', '100%'), ('_', 'rare_id'), ('中文', '中文')]:
        assert service.memory_list_keywords(mode='literal', queries=[query])['results'][0]['items'][0]['keyword'] == word
    assert service.memory_list_keywords(mode='literal', queries=['absent'])['results'][0]['total'] == 0


def test_semantic_batch_exact_expansion_threshold_and_scope(service):
    result = service.memory_list_keywords(namespace='a', mode='semantic', queries=['cache', 'transaction'])
    assert 'search_id' not in result
    assert result['min_score'] == .65
    assert [r['keyword'] for r in result['results'][0]['items']] == ['cache', 'cache clear']
    assert 'transaction' not in [r['keyword'] for r in result['results'][1]['items']]
    top = service.memory_list_keywords(mode='semantic', queries=['cache'], limit=1)['results'][0]
    assert top['total'] == 2 and len(top['items']) == 1
    assert service.memory_list_keywords(mode='semantic', queries=['cache'], min_score=1)['results'][0]['total'] == 1


@pytest.mark.parametrize('args', [
    {'mode':'bad'}, {'mode':'browse', 'queries':[]}, {'mode':'literal', 'queries':[' ']},
    {'mode':'literal', 'queries':['a','b']}, {'mode':'semantic', 'queries':['a'] * 21},
    {'limit':True}, {'limit':101}, {'offset':-1}, {'sort_by':'score'}, {'min_score':.5},
    {'mode':'semantic','queries':['a'],'limit':21},
    {'mode':'semantic','queries':['a'],'offset':1},
    {'mode':'semantic','queries':['a'],'sort_by':'count'},
    {'mode':'semantic','queries':['a'],'min_score':float('nan')},
])
def test_validation(service, args):
    with pytest.raises(ValueError):
        service.memory_list_keywords(**args)


def test_semantic_failure_not_hidden(service, monkeypatch):
    def fail(_):
        raise RuntimeError('embedding unavailable')
    monkeypatch.setattr(service, 'embed_query', fail)
    with pytest.raises(RuntimeError, match='unavailable'):
        service.memory_list_keywords(mode='semantic', queries=['cache'])
    assert service.memory_list_keywords()['results']


def test_public_interfaces_and_audit(service, monkeypatch, admin):
    from app import audit
    events = []
    monkeypatch.setattr(audit, 'record', lambda *a, **kw: events.append((a, kw)))
    from tests.conftest import server_namespace
    server = server_namespace(service)
    tools = asyncio.run(server['handle_list_tools']()).tools
    assert 'list_keywords' in [t.name for t in tools]
    assert all('keyword_counts' not in t.description for t in tools)
    def call(name, args):
        return json.loads(asyncio.run(server['handle_call_tool'](name, args)).content[0].text)
    assert call('list_keywords', {'namespace':'a'})['results'][0]['total'] == 5
    unknown = call('list_keywords', {'namespace':'missing'})
    assert unknown['response_code'] == 'namespace_not_found' and 'search_id' not in unknown
    stats = call('stats', {})
    assert 'keyword_counts' not in stats and stats['keyword_total'] == 6
    resource = asyncio.run(server['handle_read_resource']('memories://stats'))
    assert 'keyword_counts' not in json.loads(resource.contents[0].text)
    with TestClient(Starlette(routes=admin.routes)) as client:
        response = client.post('/api/keywords/query', json={'namespace':'a'})
        assert response.status_code == 200 and response.json()['results'][0]['total'] == 5
        response = client.post('/api/keywords/query', json={'namespace':'missing'})
        assert response.status_code == 400 and response.json()['response_code'] == 'namespace_not_found'
        assert client.post('/api/keywords/query', json={'unexpected':True}).status_code == 400
        assert 'keyword_counts' not in client.get('/api/stats').json()
    assert any(e[0][0] == 'list_keywords' for e in events)
    result = service.memory_list_keywords(mode='semantic', queries=['cache'] * 20, min_score=0, limit=20)
    trimmed = audit._trim_result('list_keywords', result)
    assert len(trimmed['results']) == 20
    assert len(trimmed['results'][0]['items']) == 5
    assert trimmed['results'][0]['items_truncated']
    params = audit._trim_params('list_keywords', {'queries':['a' * 2000] * 25})
    assert params['queries_truncated'] and len(params['queries']) == 20
