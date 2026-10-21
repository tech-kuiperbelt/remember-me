"""Actual image startup/Retry with outbound socket auditing, no network access."""
import json
from pathlib import Path
import subprocess
import tempfile
import time

IMAGE = 'remember-me:phase2.2-amd64'
REVISION = '5617a9f61b028005a4858fdac845db406aefb181'


def docker(*args, check=True):
    result = subprocess.run(['docker', *args], text=True, capture_output=True)
    if check and result.returncode:
        raise RuntimeError(result.stderr + result.stdout)
    return result


def main():
    work = Path(tempfile.mkdtemp(prefix='rm-offline-'))
    (work/'sitecustomize.py').write_text('''
import sys
def audit(event,args):
    if event == 'socket.connect':
        address=args[1]
        if isinstance(address,tuple) and address[0] not in ('127.0.0.1','::1','localhost'):
            with open('/data/outbound-attempts.log','a') as log:log.write(repr(address)+'\\n')
            raise RuntimeError('outbound connection forbidden by acceptance probe')
sys.addaudithook(audit)
''')
    missing = work/'missing'
    missing.mkdir()
    corrupt = work/'corrupt'/'sentence-transformers'/'models--BAAI--bge-m3'
    snapshot = corrupt/'snapshots'/REVISION
    snapshot.mkdir(parents=True)
    (corrupt/'refs').mkdir()
    (corrupt/'refs'/'main').write_text(REVISION)
    manifest = json.loads((Path(__file__).resolve().parents[1]/'docker/bge-m3-manifest.json').read_text())
    # Small runtime metadata copied out of the image; weights deliberately corrupt.
    helper = docker('create', IMAGE).stdout.strip()
    try:
        for record in manifest['files']:
            name = record['path']
            target = snapshot/name
            target.parent.mkdir(parents=True, exist_ok=True)
            if name == 'pytorch_model.bin':
                target.write_bytes(b'corrupt model for acceptance')
            else:
                source = '/opt/remember-me/models/sentence-transformers/models--BAAI--bge-m3/snapshots/'+REVISION+'/'+name
                docker('cp', '-L', helper+':'+source, str(target))
    finally:
        docker('rm', helper)
    checks = []
    for case in ['normal','missing','corrupt']:
        name = 'rm-offline-' + work.name.rsplit('-',1)[-1] + '-' + case
        command = ['run','-d','--name',name,'--network','none','--read-only',
                   '--tmpfs','/data:uid=10001,gid=10001,mode=0700',
                   '-e','PYTHONPATH=/probe:/opt/remember-me','-v',str(work)+':/probe:ro']
        if case != 'normal':
            model = missing if case == 'missing' else work/'corrupt'
            command += ['-v',str(model)+':/opt/remember-me/models:ro']
        docker(*command, IMAGE)

        def execute(code):
            return docker('exec',name,'python','-c',code).stdout.strip()

        def api(path, post=False):
            return json.loads(execute("import urllib.request; print(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:1314"+path+"',method='"+('POST' if post else 'GET')+"')).read().decode())"))
        try:
            deadline = time.monotonic()+180
            while True:
                try:
                    model = api('/api/model')
                    break
                except RuntimeError:
                    if time.monotonic()>deadline:
                        raise
                    time.sleep(1)
            if case == 'normal':
                assert model['ready'], model
                result = execute("import urllib.request,json; r=urllib.request.Request('http://127.0.0.1:1314/api/memories',data=json.dumps({'text':'Offline Chinese 中文 inference acceptance'}).encode(),headers={'Content-Type':'application/json'}); print(urllib.request.urlopen(r).read().decode())")
                assert json.loads(result)['created']
            else:
                assert not model['ready'], model
                api('/api/model/retry', True)
                deadline = time.monotonic()+30
                while api('/api/model')['status'] not in ['failed','waiting']:
                    assert time.monotonic()<deadline
                    time.sleep(.2)
                assert not api('/api/model')['ready']
                assert api('/api/maintenance')['required_reason']=='model_not_ready'
            execute("from pathlib import Path; assert not Path('/data/outbound-attempts.log').exists()")
            started = time.monotonic()
            docker('stop','--time','30',name)
            info = json.loads(docker('inspect',name).stdout)[0]
            assert info['State']['ExitCode']==0, info['State']
            checks.append({'case':case,'outbound_attempts':0,'exit_code':0,'stop_seconds':time.monotonic()-started})
            print('PASS:', case, checks[-1], flush=True)
        finally:
            (work/(case+'.log')).write_text(docker('logs',name,check=False).stdout + docker('logs',name,check=False).stderr)
            docker('rm','-f',name)
    (work/'results.json').write_text(json.dumps({'checks':checks},indent=2))
    print('Evidence:',work)


if __name__ == '__main__':
    main()
