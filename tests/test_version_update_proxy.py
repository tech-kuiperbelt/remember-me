"""The existing saved proxy reaches both destinations, without changing models."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from starlette.testclient import TestClient

from app.config.configuration import Configuration
from app.config.settings import Settings
from app.runtime import Runtime
from app.server import create_http_app
from app.operations.version_update import API_URL, RAW_URL, create_http_client as production_client
from tests.conftest import FakeProvider
from tests.version_update_helpers import Github, check, meta


@pytest.mark.parametrize('docker',[False,True])
def test_saved_configuration_projects_proxy_even_when_docker_environment_filters_it(tmp_path,docker):
    proxy='http://test-user:test-pass@127.0.0.1:12345'
    (tmp_path/'config.jsonc').write_text(json.dumps({'proxy':{'enabled':True,'url':proxy}}))
    env={'MEMORY_DEPLOYMENT_MODE':'docker'} if docker else {}
    env.update(HTTP_PROXY='http://inherited.invalid:1',HTTPS_PROXY='http://inherited.invalid:2')
    settings=Settings.from_config(tmp_path,environment=env)
    assert settings.PROXY_ENABLED and settings.PROXY_URL==proxy
    if docker:
        assert 'HTTP_PROXY' not in settings.environment
        assert settings.environment['HF_HUB_OFFLINE']=='1'
        assert settings.environment['MEMORY_AUTO_DOWNLOAD_MODEL']=='false'
    else:
        assert settings.environment['HTTP_PROXY']==proxy
    rebuilt=Settings.from_configuration(Configuration.load(tmp_path),environment=settings.environment)
    assert rebuilt.PROXY_ENABLED and rebuilt.PROXY_URL==proxy
    assert not Settings.from_env(env).PROXY_ENABLED
    assert Settings.from_env(env).PROXY_URL==''


@pytest.mark.parametrize('docker',[False,True])
def test_save_applies_next_check_and_closes_old_client(tmp_path,docker):
    async def run():
        gh=Github()
        env={'MEMORY_STORAGE_PATH':':memory:','EMBED_PRELOAD':'false'}
        if docker:
            env['MEMORY_DEPLOYMENT_MODE']='docker'
        runtime=Runtime(Settings.from_config(tmp_path,environment=env),
                        provider_factory=lambda **_:FakeProvider(),configure_logging=False)
        runtime.start()
        manager=gh.manager(runtime.release_info,settings=lambda:runtime.settings)
        try:
            await check(manager)
            assert gh.proxies==[None]
            proxy='http://test-user:test-pass@127.0.0.1:12345'
            runtime.save_model_config({'proxy':{'enabled':True,'url':proxy}})
            result=await check(manager)
            assert gh.proxies==[None,proxy] and gh.clients[0].is_closed
            assert result['check']['status'].startswith('UPDATE_')
            assert 'test-pass' not in json.dumps(result)
            assert '***@' in runtime.model_config()['proxy']['url']
            runtime.save_model_config({'proxy':{'enabled':False}})
            await check(manager)
            assert gh.proxies==[None,proxy,None]
            assert gh.clients[1].is_closed
        finally:
            await manager.aclose()
            runtime.close()
    asyncio.run(run())


def test_inflight_round_uses_one_config_and_failed_save_does_not_publish(tmp_path,monkeypatch):
    async def run():
        gh=Github()
        entered,release=asyncio.Event(),asyncio.Event()
        async def waiting(request):
            entered.set()
            await release.wait()
            return httpx.Response(200,json=meta())
        gh.overrides[API_URL+'/latest']=waiting
        runtime=Runtime(Settings.from_config(tmp_path,environment={
            'MEMORY_STORAGE_PATH':':memory:','EMBED_PRELOAD':'false'}),
            provider_factory=lambda **_:FakeProvider(),configure_logging=False).start()
        manager=gh.manager(runtime.release_info,settings=lambda:runtime.settings)
        try:
            manager.request_check()
            await entered.wait()
            runtime.save_model_config({'proxy':{'enabled':True,'url':'http://127.0.0.1:23456'}})
            assert not gh.clients[0].is_closed
            release.set()
            await manager._task
            assert gh.proxies==[None] and len(gh.clients)==1
            gh.overrides.clear()
            await check(manager)
            assert gh.proxies==[None,'http://127.0.0.1:23456']
            monkeypatch.setattr(Configuration,'save',Mock(side_effect=OSError('disk failed')))
            with pytest.raises(OSError):
                runtime.save_model_config({'proxy':{'enabled':False}})
            assert runtime.settings.PROXY_ENABLED
        finally:
            await manager.aclose()
            runtime.close()
    asyncio.run(run())


def test_client_adapter_explicit_proxy_no_environment_or_redirects(monkeypatch):
    captured=[]
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:captured.append(kw))
    for proxy in (None,'http://127.0.0.1:12345'):
        production_client(proxy)
        kw=captured[-1]
        assert kw['proxy']==proxy and kw['trust_env'] is False
        assert kw['follow_redirects'] is False and kw.get('verify',True) is True
        assert kw['timeout'].connect==kw['timeout'].read==5


def test_real_client_uses_local_proxy_for_both_fixed_https_hosts(tmp_path,monkeypatch):
    connections=[]
    class Proxy(BaseHTTPRequestHandler):
        def do_CONNECT(self):
            connections.append((self.path,self.headers.get('Proxy-Authorization')))
            self.send_response(502)
            self.end_headers()
        def log_message(self,*args):
            pass
    proxy=ThreadingHTTPServer(('127.0.0.1',0),Proxy)
    worker=threading.Thread(target=proxy.serve_forever,daemon=True)
    worker.start()
    monkeypatch.setenv('HTTPS_PROXY','http://inherited.invalid:1')
    monkeypatch.setenv('NO_PROXY','*')
    url=f'http://test-user:test-pass@127.0.0.1:{proxy.server_port}'
    (tmp_path/'config.jsonc').write_text(json.dumps({'proxy':{'enabled':True,'url':url}}))
    settings=Settings.from_config(tmp_path,environment={'MEMORY_DEPLOYMENT_MODE':'docker'})
    async def run():
        async with production_client(settings.PROXY_URL if settings.PROXY_ENABLED else None) as client:
            for destination in (API_URL+'/latest',RAW_URL+'/v1.2.0/release.jsonc'):
                with pytest.raises(httpx.ProxyError):
                    await client.get(destination)
    try:
        asyncio.run(run())
    finally:
        proxy.shutdown()
        proxy.server_close()
        worker.join(2)
    assert [row[0] for row in connections]==['api.github.com:443','raw.githubusercontent.com:443']
    assert all(row[1].startswith('Basic ') for row in connections)


def test_model_retry_rebuild_keeps_proxy_and_docker_offline(tmp_path):
    from app.embedding.embed import ModelCacheMissing
    path=tmp_path/'config.jsonc'
    path.write_text(json.dumps({'proxy':{'enabled':True,'url':'http://127.0.0.1:23456'}}))
    restored=False
    def provider(**kwargs):
        assert kwargs.get('cache_only') is True
        if not restored:
            raise ModelCacheMissing('fixture missing')
        return FakeProvider()
    runtime=Runtime(Settings.from_config(tmp_path,environment={
        'MEMORY_DEPLOYMENT_MODE':'docker','MEMORY_STORAGE_PATH':':memory:',
        'EMBED_PRELOAD':'false','EMBED_PROVIDER':'sentence_transformers'}),
        provider_factory=provider,configure_logging=False)
    with TestClient(create_http_app(runtime)) as client:
        restored=True
        assert client.post('/api/model/retry').status_code==202
        worker=runtime._model_worker
        if worker:
            worker.join(3)
            assert not worker.is_alive()
        assert runtime.settings.PROXY_ENABLED
        assert runtime.settings.PROXY_URL=='http://127.0.0.1:23456'
        assert 'HTTP_PROXY' not in runtime.settings.environment
        assert runtime.settings.environment['HF_HUB_OFFLINE']=='1'
