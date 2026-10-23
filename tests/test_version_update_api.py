"""The approved REST snapshot contract, maintenance admission and safe audit."""
import asyncio
import json

import httpx
import pytest
from starlette.testclient import TestClient

from app import audit
from app.config.settings import Settings
from app.embedding.embed import ModelCacheMissing
from app.runtime import Runtime
from app.server import create_http_app
from tests.conftest import FakeProvider
from tests.version_update_helpers import Github
from app.operations.version_update import API_URL


def make_app(tmp_path, gh, *, missing_model=False):
    def provider(**kwargs):
        if missing_model:
            raise ModelCacheMissing('mock cache absent')
        return FakeProvider()
    runtime = Runtime(Settings.from_env({"MEMORY_DATA_DIR": str(tmp_path),
        "MEMORY_STORAGE_PATH": ":memory:", "EMBED_PRELOAD": "false",
        "MEMORY_AUTO_DOWNLOAD_MODEL": "false"}),
        provider_factory=provider, configure_logging=False)
    factory = lambda current, settings, **kw: gh.manager(current, settings=settings, **kw)
    return runtime, create_http_app(runtime, version_update_factory=factory)


def settle(client, runtime):
    async def done():
        await runtime.version_updates._task
    client.portal.call(done)


@pytest.mark.parametrize('missing_model', [False, True])
def test_get_read_only_contract_post_real_refresh_in_maintenance(tmp_path, missing_model):
    gh = Github()
    runtime, app = make_app(tmp_path, gh, missing_model=missing_model)
    with TestClient(app) as client:
        settle(client, runtime)
        for _ in range(3):
            response = client.get('/api/version-update')
            assert response.status_code == 200
            assert response.headers['cache-control'] == 'no-store'
            data = response.json()
            assert set(data) == {'current','check','latest','notes_scope','notes_complete',
                                 'releases','release_errors','notes_error_code'}
            assert set(data['current']) == {'version','install_method','release','error_code'}
            assert data['current']['version'] is data['current']['release'] is None
            assert data['check']['status'] == 'UPDATE_RECOMMENDED'
            assert data['notes_scope'] == 'latest_only' and data['notes_complete'] is None
            assert data['release_errors'] == data['releases'] == []
            assert 'commit' not in data['current'] and 'body' not in data['latest']
        assert len(gh.seen) == 2
        if not missing_model:
            assert client.post('/api/maintenance/enter').status_code == 200
        response = client.post('/api/version-update/check', json={})
        assert response.status_code == 202 and response.headers['cache-control'] == 'no-store'
        settle(client, runtime)
        assert len(gh.seen) == 4
        assert client.get('/api/config').status_code == 200
        assert client.get('/api/model').status_code == 200


@pytest.mark.parametrize('body', [b'bad', b'[]', b'null', b'{"url":"https://evil.invalid/"}', b' '*1025])
def test_invalid_check_body_never_triggers_requests(tmp_path, body):
    gh = Github()
    runtime, app = make_app(tmp_path, gh)
    with TestClient(app) as client:
        settle(client, runtime)
        response = client.post('/api/version-update/check', content=body)
        assert response.status_code == 400 and response.json()['response_code'] == 'invalid_request'
        assert response.headers['cache-control'] == 'no-store'
        assert client.post('/api/version-update/check?url=anything').status_code == 400
        assert len(gh.seen) == 2


def test_cooldown_response_and_no_get_side_effects(tmp_path):
    gh = Github()
    gh.overrides[API_URL + '/latest'] = httpx.Response(403, headers={'Retry-After': '120'})
    runtime, app = make_app(tmp_path, gh)
    with TestClient(app) as client:
        settle(client, runtime)
        before = len(gh.seen)
        response = client.post('/api/version-update/check')
        assert response.status_code == 429 and int(response.headers['retry-after']) > 0
        assert response.json()['check']['next_allowed_at']
        assert response.json()['check']['status'] == 'UNAVAILABLE'
        assert client.get('/api/version-update').status_code == 200
        assert len(gh.seen) == before


def test_audit_whitelist_and_no_proxy_or_notes_leak(tmp_path):
    gh = Github()
    runtime, app = make_app(tmp_path, gh)
    with TestClient(app) as client:
        settle(client, runtime)
        rows = [json.loads(line) for line in (tmp_path/'audit.jsonl').read_text().splitlines()]
        row = next(row for row in rows if row['tool'] == 'version_check')
        assert 'new capability' not in json.dumps(row)
        assert 'Back up before' not in json.dumps(row)
        assert row['params'] == {'trigger':'startup'}
    assert 'version_check' in audit.AUDIT_EVENTS
    assert audit._trim_params('version_check', {'trigger':'manual','proxy':'secret'}) == {'trigger':'manual'}
    assert audit._trim_result('version_check', {'status':'UP_TO_DATE','release':'private','proxy':'secret'}) == {
        'status':'UP_TO_DATE','error_code':None,'notes_complete':None}


def test_api_without_http_runtime_is_503(admin):
    from starlette.applications import Starlette
    from app.transport.admin import create_admin_routes
    with TestClient(Starlette(routes=create_admin_routes(admin.service))) as client:
        response = client.get('/api/version-update')
        assert response.status_code == 503
        assert response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('failure',['missing','parse','version_missing','version_invalid'])
@pytest.mark.parametrize('remote,status',[('release','UPDATE_RECOMMENDED'),('failure','UNAVAILABLE'),('empty','NO_RELEASE')])
def test_four_unknown_paths_continue_remote_checks(tmp_path,monkeypatch,failure,remote,status):
    import app.release_info as release_info
    monkeypatch.setattr(release_info,'PROJECT_ROOT',tmp_path)
    if failure != 'missing':
        body={'parse':'{broken', 'version_missing':'{}', 'version_invalid':'{"version":"invalid"}'}[failure]
        (tmp_path/'release.jsonc').write_text(body)
    gh=Github()
    if remote=='failure':
        gh.overrides[API_URL+'/latest']=httpx.ConnectError('DNS failure')
    elif remote=='empty':
        gh.overrides[API_URL+'/latest']=httpx.Response(404)
        gh.pages[1]=[]
    runtime,app=make_app(tmp_path,gh)
    with TestClient(app) as client:
        settle(client,runtime)
        result=client.get('/api/version-update').json()
        assert result['current']['version'] is result['current']['release'] is None
        assert result['check']['status']==status
        assert result['releases']==result['release_errors']==[]
        assert result['notes_complete'] is None
        assert gh.seen[0]==API_URL+'/latest'
        if remote=='release':
            assert len(gh.seen)==2 and result['notes_scope']=='latest_only'
