"""Time scopes agree across meta, browsing and semantic candidates before Top-K."""
import asyncio
import json

import pytest
from starlette.requests import Request

from app.server import create_mcp_server
from app.service import InvalidSearchFilter
from app.transport.admin import AdminAPI


@pytest.fixture
def timed(service, monkeypatch):
    monkeypatch.setattr(service, 'embed_query', lambda _: [1., 0., 0.])
    records = {}
    for name, stamp, vector, namespace in [
        ('outside', '2025-01-01T00:00:00Z', [1., 0., 0.], 'default'),
        ('lower', '2026-01-01T00:00:00.000100Z', [1., 0., 0.], 'default'),
        ('inside', '2026-01-01T08:00:00.000200+08:00', [.8, .6, 0.], 'default'),
        ('upper', '2026-01-01T00:00:00.000300Z', [1., 0., 0.], 'default'),
        ('other', '2026-01-01T00:00:00.000200Z', [1., 0., 0.], 'other'),
        ('invalid', 'bad', [1., 0., 0.], 'default'),
        ('missing', '', [1., 0., 0.], 'default'),
        ('naive', '2026-01-01T00:00:00.000250', [.6, .8, 0.], 'default'),
    ]:
        record = service.storage.add(name, vector, namespace=namespace,
            metadata={'keywords': ['cache'], 'categories': ['design']},
            index_embeddings={('keywords', 'cache'): [1., 0., 0.], ('categories', 'design'): [1., 0., 0.]})
        service.storage.conn.execute('UPDATE memories SET updated_at=? WHERE id=?', (stamp, record['id']))
        records[name] = record['id']
    service.storage.conn.commit()
    return service, records


BOUNDS = {'updated_after': '2026-01-01T00:00:00.000100Z',
          'updated_before': '2026-01-01T00:00:00.000300Z'}


def test_time_range_filters_before_top_k_with_microseconds_and_offsets(timed):
    service, ids = timed
    assert service.memory_search('query', k=1, namespace='default')[0]['id'] != ids['inside']
    result = service.memory_search('query', k=1, namespace='default', keywords=['cache'], categories=['design'], **BOUNDS)
    assert [r['id'] for r in result] == [ids['inside']]
    all_items = service.memory_search('query', k=10, namespace='default', **BOUNDS)
    assert [r['id'] for r in all_items] == [ids['inside'], ids['naive']]
    page = service.memory_list_page(namespace='default', **BOUNDS)
    assert {r['id'] for r in page['items']} == {ids['inside'], ids['naive']}
    assert service.memory_search('query', namespace='default', threshold=.9, **BOUNDS) == []
    assert service.memory_search('query', updated_after='2027-01-01T00:00:00Z') == []


@pytest.mark.parametrize('bound', [
    {'updated_after': BOUNDS['updated_after']}, {'updated_before': BOUNDS['updated_before']},
])
def test_one_sided_range_matches_browse_candidates(timed, bound):
    service, _ = timed
    search = service.memory_search('query', k=100, namespace='default', **bound)
    page = service.memory_list_page(namespace='default', limit=50, **bound)
    assert {r['id'] for r in search} == {r['id'] for r in page['items']}


@pytest.mark.parametrize('bounds', [
    {'updated_after': '2026-01-01'}, {'updated_before': 'bad'}, {'updated_after': 123},
    {'updated_after': []}, {'updated_before': True},
    {'updated_after': BOUNDS['updated_before'], 'updated_before': BOUNDS['updated_after']},
    {'updated_after': BOUNDS['updated_after'], 'updated_before': BOUNDS['updated_after']},
])
def test_invalid_range_rejected_before_any_embedding(service, monkeypatch, bounds):
    def forbidden(*args, **kwargs):
        pytest.fail('invalid range reached embedding or filter resolution')
    monkeypatch.setattr(service, 'embed_query', forbidden)
    monkeypatch.setattr(service, '_index_resolver', forbidden)
    with pytest.raises(InvalidSearchFilter):
        service.memory_search('query', keywords=['new'], **bounds)


async def admin_call(service, body):
    async def receive():
        return {'type': 'http.request', 'body': json.dumps(body).encode(), 'more_body': False}
    request = Request({'type': 'http', 'method': 'POST', 'path': '/api/memories/search', 'headers': []}, receive)
    return await AdminAPI(service).handle_search_memories(request)


@pytest.mark.parametrize('resolution', [False, True])
def test_admin_range_and_legacy_response_shape(timed, resolution):
    service, ids = timed
    response = asyncio.run(admin_call(service, {'query': 'query', 'namespace': 'default', 'k': 1,
                                               'include_resolution': resolution, **BOUNDS}))
    assert response.status_code == 200
    result = json.loads(response.body)
    items = result['items'] if resolution else result
    assert [item['id'] for item in items] == [ids['inside']]
    response = asyncio.run(admin_call(service, {'query': 'query', 'updated_after': 'bad'}))
    assert response.status_code == 400
    assert json.loads(response.body)['response_code'] == 'invalid_request'


def test_mcp_range_receipt_and_audit(timed, tmp_path):
    from app.audit import AuditJournal
    service, ids = timed
    journal = AuditJournal({'MEMORY_AUDIT_PATH': str(tmp_path / 'calls.jsonl')})
    service.audit = journal
    api = create_mcp_server(service=service)
    try:
        result = asyncio.run(api.handle_call_tool('search_semantic', {'query': 'query', 'namespace': 'default', 'k': 1, **BOUNDS}))
        payload = json.loads(result.content[0].text)
        assert [item['id'] for item in payload['items']] == [ids['inside']]
        assert payload['search_id']
        feedback = asyncio.run(api.handle_call_tool('report', {'search_id': payload['search_id'],
            'ratings': [{'m_id': ids['inside'], 'rating': 1}]}))
        assert json.loads(feedback.content[0].text)['recorded'] is True
        with pytest.raises(InvalidSearchFilter):
            asyncio.run(api.handle_call_tool('search_semantic', {'query': 'query', 'updated_after': 'bad'}))
    finally:
        journal.close()
    events = [json.loads(line) for line in (tmp_path / 'calls.jsonl').read_text().splitlines()]
    assert events[0]['params']['updated_after'] == BOUNDS['updated_after']
    assert events[0]['params']['updated_before'] == BOUNDS['updated_before']


def test_empty_bounds_preserve_legacy_results(timed):
    service, _ = timed
    assert service.memory_search('query', updated_after=' ', updated_before=None) == service.memory_search('query')


@pytest.mark.parametrize('bounds', [
    BOUNDS,
    {'updated_after': BOUNDS['updated_after']},
    {'updated_before': BOUNDS['updated_before']},
    {'updated_after': '2026-01-01T08:00:00.000100+08:00',
     'updated_before': '2026-01-01T08:00:00.000300+08:00'},
])
def test_meta_time_candidates_match_browse_and_semantic(timed, bounds):
    service, _ = timed
    filters = {'namespace': 'default', 'keywords': ['cache'], 'categories': ['design'], **bounds}
    meta = service.memory_list(**filters)
    page = service.memory_list_page(**filters)
    semantic = service.memory_search('query', k=100, **filters)
    assert {r['id'] for r in meta} == {r['id'] for r in page['items']} == {r['id'] for r in semantic}


@pytest.mark.parametrize('bounds', [
    {'updated_after': '2026-01-01'}, {'updated_before': 'bad'}, {'updated_after': 123},
    {'updated_after': []}, {'updated_before': True},
    {'updated_after': BOUNDS['updated_before'], 'updated_before': BOUNDS['updated_after']},
    {'updated_after': BOUNDS['updated_after'], 'updated_before': BOUNDS['updated_after']},
])
def test_meta_invalid_range_rejected_before_storage(service, monkeypatch, bounds):
    def forbidden(*args, **kwargs):
        pytest.fail('invalid range reached storage')
    monkeypatch.setattr(service.storage, 'list_memories', forbidden)
    with pytest.raises(InvalidSearchFilter):
        service.memory_list(**bounds)


def test_meta_blank_bounds_are_unfiltered(timed):
    service, ids = timed
    records = service.memory_list(updated_after=' ', updated_before='')
    assert {r['id'] for r in records} == set(ids.values())


def test_meta_mcp_time_scope_receipt_and_audit(timed, tmp_path):
    from app.audit import AuditJournal
    service, ids = timed
    journal = AuditJournal({'MEMORY_AUDIT_PATH': str(tmp_path / 'meta.jsonl')})
    service.audit = journal
    api = create_mcp_server(service)
    try:
        arguments = {'namespace': 'default', **BOUNDS}
        response = asyncio.run(api.handle_call_tool('search_meta', arguments))
        payload = json.loads(response.content[0].text)
        assert {r['id'] for r in payload['items']} == {ids['inside'], ids['naive']}
        feedback = asyncio.run(api.handle_call_tool('report', {'search_id': payload['search_id'],
            'ratings': [{'m_id': ids['inside'], 'rating': 1}]}))
        assert json.loads(feedback.content[0].text)['recorded'] is True
        with pytest.raises(InvalidSearchFilter):
            asyncio.run(api.handle_call_tool('search_meta', {'updated_after': '2026-01-01'}))
    finally:
        journal.close()
    events = [json.loads(line) for line in (tmp_path / 'meta.jsonl').read_text().splitlines()]
    assert events[0]['params']['updated_after'] == BOUNDS['updated_after']
    assert events[0]['params']['updated_before'] == BOUNDS['updated_before']


@pytest.mark.parametrize('invalid', [False, True])
def test_legacy_admin_list_time_validation(timed, invalid):
    from urllib.parse import urlencode
    service, ids = timed
    bounds = {'updated_after': '2026-01-01'} if invalid else BOUNDS
    request = Request({'type': 'http', 'method': 'GET', 'path': '/api/memories',
        'query_string': urlencode({'namespace': 'default', **bounds}).encode(), 'headers': []})
    response = asyncio.run(AdminAPI(service).handle_list_memories(request))
    payload = json.loads(response.body)
    if invalid:
        assert response.status_code == 400
        assert payload['response_code'] == 'invalid_request'
    else:
        assert response.status_code == 200
        assert {r['id'] for r in payload} == {ids['inside'], ids['naive']}
