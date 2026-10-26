"""Opt-in real Runtime/browser + controlled, verified HTTPS GitHub integration.

SOURCE uses the real file loader with an isolated root and a fake embedding provider.
DOCKER uses a local, already-built image and its actual BGE provider/entrypoint.
The Docker TLS fixture adds read-only CA and per-case release metadata mounts;
ordinary deployment acceptance is performed separately by run_docker_installation.
No GitHub traffic is forwarded, and no release/tag/registry action is performed.
"""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tests.chrome_viewport import run_viewport
from tests.version_tls_fixture import Fixture, certificates, start_tls_proxy, start_control_proxy


def command(argv, **kwargs):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=360, **kwargs)
    if result.returncode:
        raise RuntimeError(str(argv[:3])+': '+result.stdout+result.stderr)
    return result.stdout.strip()


def api(url, data=None):
    request = urllib.request.Request(url,data=None if data is None else json.dumps(data).encode(),
                                    headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(request, timeout=60)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        body = response.read()
        assert response.status in (200,202), (url,response.status,body)
        return json.loads(body)


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def wait_ready(upstream):
    deadline = time.monotonic()+180
    while time.monotonic()<deadline:
        try:
            if api(upstream+'/api/model')['ready']:
                return
        except (OSError,AssertionError,KeyError):
            pass
        time.sleep(.2)
    raise TimeoutError('model readiness '+upstream)


@contextmanager
def host_runtime(data, local_root, bundle, proxy):
    import certifi
    import uvicorn
    from app.config.settings import Settings
    from app.runtime import Runtime
    from app.release_info import read_local_release
    from app.server import create_http_app
    from app.operations.version_update import VersionUpdateManager, create_http_client
    from tests.conftest import FakeProvider
    from tests.version_update_helpers import Clock
    data.mkdir()
    (data/'config.jsonc').write_text(json.dumps({'proxy':{'enabled':True,'url':proxy},'auto_download_model':False}))
    settings=Settings.from_config(data,environment={'REMEMBER_ME_INSTALL_METHOD':'SOURCE','MEMORY_STORAGE':'dual',
        'EMBED_PRELOAD':'false','MEMORY_DEFAULT_NAMESPACE':'phase-e-test'})
    runtime=Runtime(settings,provider_factory=lambda **_:FakeProvider(),configure_logging=False)
    clock=Clock(); clients=[]; owned={}
    clock.now=lambda:datetime.now(timezone.utc)+timedelta(seconds=clock.value)
    def factory(current, settings, **kwargs):
        owned['loop']=asyncio.get_running_loop()
        def client(proxy):
            result=create_http_client(proxy);clients.append(result);return result
        return VersionUpdateManager(current,settings,client_factory=client,**clock.options(),**kwargs)
    listener=socket.socket();listener.bind(('127.0.0.1',0));listen_port=listener.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(create_http_app(runtime,port=listen_port,version_update_factory=factory),log_level='error'))
    with patch('certifi.where',return_value=str(bundle)), patch('app.runtime.read_local_release',side_effect=lambda **kw:read_local_release(local_root,**kw)):
        thread=threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True);thread.start()
        try:
            upstream=f'http://127.0.0.1:{listen_port}';wait_ready(upstream)
            yield upstream,lambda seconds:asyncio.run_coroutine_threadsafe(clock.advance(seconds),owned['loop']).result(10)
        finally:
            server.should_exit=True;thread.join(15);listener.close()
            assert not thread.is_alive(),'HTTP Runtime did not close'
            assert runtime.state=='closed',runtime.state
            assert clients and all(client.is_closed for client in clients),'HTTP clients leaked'


@contextmanager
def docker_runtime(image, work, local_file, bundle, proxy):
    from tests.run_docker_installation import installation_blocks, docker_run_argv
    data=work/'data';data.mkdir();name='rm-version-e-'+work.name;listen_port=port()
    env=dict(os.environ,REMEMBER_ME_IMAGE=image,REMEMBER_ME_DATA_DIR=str(data),REMEMBER_ME_PORT=str(listen_port))
    blocks=installation_blocks(ROOT/'docs/installation/docker.md')
    command(['docker','image','inspect',image])
    fixture_config=work/'config.jsonc';fixture_config.write_text(json.dumps({
        'proxy':{'enabled':True,'url':proxy},'auto_download_model':False,'local_only':True}))
    # Seed only the isolated root using the fixture image; never pull a public tag.
    command(['docker','run','--rm','--pull','never','--network','none','--read-only','--user','0:0','--entrypoint','python',
        '--mount',f'type=bind,src={data},dst=/data','--mount',f'type=bind,src={fixture_config},dst=/fixture.jsonc,readonly',image,
        '-c',"import shutil;shutil.copyfile('/fixture.jsonc','/data/config.jsonc')"])
    ca_path=command(['docker','run','--rm','--pull','never','--network','none','--read-only','--entrypoint','python',image,'-c','import certifi;print(certifi.where())'])
    additions=['--mount',f'type=bind,src={bundle},dst={ca_path},readonly','-e','MEMORY_DEFAULT_NAMESPACE=phase-e-test']
    if local_file:
        additions+=['--mount',f'type=bind,src={local_file},dst=/opt/remember-me/release.jsonc,readonly']
    argv=docker_run_argv(blocks['run'],image=image,name=name,data=data,port=listen_port,extra=additions)
    command(argv,env=env,cwd=data)
    try:
        upstream=f'http://127.0.0.1:{listen_port}';wait_ready(upstream)
        yield upstream,None
    finally:
        log=command(['docker','logs',name]);(work/'container.log').write_text(log)
        command(['docker','stop','--time','120',name])
        state=json.loads(command(['docker','inspect',name]))[0]['State']
        assert state['ExitCode']==0 and not state['OOMKilled'],state
        command(['docker','rm',name])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=['source','docker'],default='source')
    parser.add_argument('--cases',default='valid,null,missing,invalid,missing_version,bad_version')
    parser.add_argument('--languages',default='zh-CN,en')
    parser.add_argument('--image',default='remember-me:version-phase-e-cc7ca0a')
    args=parser.parse_args()
    root_hash=hashlib.sha256((ROOT/'release.jsonc').read_bytes()).hexdigest()
    work=Path(tempfile.mkdtemp(prefix='rm-version-e-'));print('Evidence:',work,flush=True)
    evidence={'mode':args.mode,'base_commit':'cc7ca0ae6d652d3bb6abd6e4968413a1bc85b355','cases':[],'work':str(work)}
    def save(): (work/'results.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
    fixture=Fixture();bundle=certificates(work)
    bind='127.0.0.1' if args.mode=='source' else command(['docker','network','inspect','bridge','--format','{{(index .IPAM.Config 0).Gateway}}'])
    good,good_url=start_tls_proxy(fixture,bind,work);bad,bad_url=start_tls_proxy(fixture,bind,work,bad=True)
    script=(ROOT/'tests/browser/version_update_integration.js').read_text()
    cases=args.cases.split(',')
    assert set(cases)<={'valid','null','missing','invalid','missing_version','bad_version'}
    try:
        for case in cases:
            scenario=work/case;scenario.mkdir();local_root=scenario/'release-root';local_root.mkdir()
            release=Fixture.release('1.0.0')
            if case=='null':release['version']=None
            if case=='missing_version':release.pop('version')
            if case=='bad_version':release['version']='invalid'
            if case!='missing':(local_root/'release.jsonc').write_text('{bad JSONC' if case=='invalid' else json.dumps(release))
            fixture.mode='ok'
            if args.mode=='source':ctx=host_runtime(scenario/'data',local_root,bundle,good_url)
            else:
                assert case in ('valid','null'),'Docker uses valid and null release fixtures'
                local_file=local_root/'release.jsonc'
                ctx=docker_runtime(args.image,scenario,local_file,bundle,good_url)
                evidence['image_id']=json.loads(command(['docker','image','inspect',args.image]))[0]['Id']
            with ctx as (upstream,advance):
                control,url=start_control_proxy(fixture,upstream,advance=advance)
                try:
                    for language in args.languages.split(','):
                        api(upstream+'/api/config',{'language':language})
                        config={'version':'1.0.0' if case=='valid' else None,'method':'SOURCE' if args.mode=='source' else 'DOCKER',
                                'good_proxy':good_url,'bad_proxy':bad_url,'clock':advance is not None}
                        seed='window.__phase_e='+json.dumps(config)+';'+script
                        with tempfile.TemporaryDirectory(prefix='rm-version-e-chrome-') as profile:
                            result=run_viewport(url+'/admin/console',profile,1440 if language=='zh-CN' else 390,1000,
                                scenario/(language+'.png'),scenario_script=seed,result_timeout=120)
                        evidence['cases'].append({'case':case,'language':language,**result})
                        save();print(result['text'],case,language,flush=True)
                        assert result['text'].startswith('PASS:'),result
                    if args.mode=='docker':
                        output=command([sys.executable,str(ROOT/'tests/run_docker_browser.py'),url+'/admin/console'])
                        evidence.setdefault('docker_ui_restart',[]).append({'case':case,'result':output})
                        print('PASS: real Docker UI save/restart/config persistence',case,flush=True)
                finally:
                    control.shutdown();control.server_close()
            save()
        evidence['result']='passed'
    except BaseException as exc:
        evidence.update(result='failed',error=str(exc));raise
    finally:
        good.shutdown();bad.shutdown();good.server_close();bad.server_close()
        evidence['wire']={'requests':fixture.seen,'connects':fixture.connects}
        evidence['root_release_unchanged']=root_hash==hashlib.sha256((ROOT/'release.jsonc').read_bytes()).hexdigest()
        save();assert evidence['root_release_unchanged']


if __name__=='__main__':main()
