"""Opt-in Phase 3.3 acceptance of the README commands against a LOCAL image.

Never pulls/pushes images or touches production data. Uses fresh /tmp directories
and alternate loopback ports. Keeps evidence and stops/removes its own containers.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PROFILE = 'ep_6134c0b15d2bb471'


def installation_blocks(path):
    blocks = re.findall(r'```bash\n(.*?)\n```', path.read_text(), flags=re.S)
    return {
        'initialize': next(b for b in blocks if '--entrypoint python' in b and 'init_data.py' in b),
        'run': next(b for b in blocks if 'docker run -d --name remember-me' in b),
        'compose': next(line for b in blocks for line in b.splitlines()
                        if line.startswith('docker compose -f compose.yaml up -d --wait')),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='remember-me:phase3.2-ready-fixed')
    parser.add_argument('--port', type=int, default=19143, help='Uses this port and port+1; never 1314')
    args = parser.parse_args()
    assert 1024 <= args.port <= 65534 and 1314 not in (args.port, args.port + 1)
    work = Path(tempfile.mkdtemp(prefix='rm-phase33-'))
    commands = installation_blocks(ROOT / 'README.md')
    assert commands == installation_blocks(ROOT / 'README.zh-CN.md')
    evidence = {'status': 'running', 'work': str(work), 'image': args.image, 'paths': {},
                'formal_registry_actions': False, 'public_download_verified': False}
    print('Evidence:', work, flush=True)

    def save():
        (work / 'results.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')

    def command(argv, *, env=None, cwd=None, check=True, timeout=360):
        result = subprocess.run(argv, env=env, cwd=cwd, text=True, capture_output=True, timeout=timeout)
        if check and result.returncode:
            raise RuntimeError(f'{argv}: {result.stdout}\n{result.stderr}')
        return result

    def docker(*argv, **kwargs):
        return command(['docker', *argv], **kwargs)

    image_id = json.loads(docker('image', 'inspect', args.image).stdout)[0]['Id']
    evidence['image_id'] = image_id
    evidence['docker_version'] = docker('version', '--format', '{{.Server.Version}}').stdout.strip()
    evidence['compose_version'] = docker('compose', 'version', '--short').stdout.strip()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    for index, method in enumerate(('run', 'compose')):
        directory = work / method
        directory.mkdir()
        data = directory / 'data'
        port = args.port + index
        name = f'{work.name}-{method}'
        env = dict(os.environ, REMEMBER_ME_IMAGE=args.image, REMEMBER_ME_DATA_DIR=str(data),
                   REMEMBER_ME_PORT=str(port), COMPOSE_PROJECT_NAME=name)
        report = {'port': port, 'checks': []}
        evidence['paths'][method] = report
        (directory / 'compose.yaml').write_bytes((ROOT / 'compose.yaml').read_bytes())
        container = None

        def passed(check, detail=True):
            report['checks'].append({'check': check, 'detail': detail})
            save()
            print(f'PASS [{method}]: {check}', flush=True)

        def compose(*argv, **kwargs):
            return command(['docker', 'compose', '-f', 'compose.yaml', *argv], env=env, cwd=directory, **kwargs)

        def shell(block):
            return command(['bash', '-euo', 'pipefail', '-c', block], env=env, cwd=directory)

        def request(path, payload=None, *, headers=None, expected=200):
            req = urllib.request.Request(f'http://127.0.0.1:{port}{path}',
                data=None if payload is None else json.dumps(payload).encode(),
                headers={'Content-Type': 'application/json', **(headers or {})})
            try:
                response = opener.open(req, timeout=60)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                body = response.read()
                assert response.status == expected, (path, response.status, body)
                return json.loads(body) if response.headers.get_content_type() == 'application/json' else body.decode()

        def mcp(tool, arguments):
            result = request('/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                'params': {'name': tool, 'arguments': arguments}},
                headers={'Accept': 'application/json, text/event-stream'})
            assert 'error' not in result, result
            result = result['result']
            assert not result.get('isError'), result
            return json.loads(result['content'][0]['text'])

        def inspect():
            return json.loads(docker('inspect', container).stdout)[0]

        def execute(code):
            return docker('exec', container, 'python', '-c', code).stdout.strip()

        def start():
            nonlocal container
            if method == 'run':
                shell(commands['initialize'])
                shell(commands['run'].replace('--name remember-me', '--name ' + name))
                container = name
            else:
                shell(commands['compose'])
                container = compose('ps', '-q', 'remember-me').stdout.strip()
                init_id = compose('ps', '-a', '-q', 'data-init').stdout.strip()
                state = json.loads(docker('inspect', init_id).stdout)[0]['State']
                assert state['ExitCode'] == 0, state
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                try:
                    if request('/api/model').get('ready') and inspect()['State']['Health']['Status'] == 'healthy':
                        return
                except (OSError, AssertionError):
                    pass
                time.sleep(1)
            raise TimeoutError(docker('logs', '--tail', '70', container, check=False).stdout)

        def stop():
            began = time.monotonic()
            if method == 'run':
                docker('stop', '--time', '120', container, timeout=150)
            else:
                compose('stop', timeout=150)
            state = inspect()['State']
            assert state['ExitCode'] == 0 and not state['OOMKilled'], state
            return round(time.monotonic() - began, 3)

        def remove():
            nonlocal container
            logs = docker('logs', '--tail', '150', container, check=False)
            with (directory / 'container.log').open('a') as output:
                output.write(logs.stdout + logs.stderr)
            if method == 'run':
                docker('rm', container)
            else:
                compose('down')
            container = None

        def snapshot():
            return json.loads(execute("""
import sqlite3,json,hashlib
from contextlib import closing
with closing(sqlite3.connect('file:/data/memories.db?mode=ro',uri=True)) as c:
 assert c.execute('pragma integrity_check').fetchone()[0]=='ok'
 assert not c.execute('pragma foreign_key_check').fetchall()
 rows=c.execute('select memory_id,profile_id,embedding from memory_embeddings order by memory_id').fetchall()
 print(json.dumps({'vectors':[(m,p,hashlib.sha256(b).hexdigest()) for m,p,b in rows],
  'memories':c.execute('select id,text,metadata from memories order by id').fetchall()}))
"""))

        try:
            start()
            info = inspect()
            assert info['Image'] == image_id
            assert info['Config']['User'] == '10001:10001'
            assert info['Config']['StopTimeout'] == 120
            assert info['HostConfig']['ReadonlyRootfs']
            assert info['HostConfig']['RestartPolicy']['Name'] == 'unless-stopped'
            assert info['HostConfig']['CapDrop'] == ['ALL']
            assert 'no-new-privileges:true' in info['HostConfig']['SecurityOpt']
            assert info['HostConfig']['PortBindings']['1314/tcp'] == [{'HostIp': '127.0.0.1', 'HostPort': str(port)}]
            mounts = info['Mounts']
            assert len(mounts) == 1 and mounts[0]['Type'] == 'bind' and mounts[0]['Destination'] == '/data'
            assert mounts[0]['Source'] == str(data) and mounts[0]['RW']
            assert execute("import os; s=os.stat('/data'); print(os.getuid(),os.getgid(),s.st_uid,s.st_gid,oct(s.st_mode & 0o777))") == '10001 10001 10001 10001 0o750'
            config = request('/api/config')
            assert config['auto_download_model'] is False and config['local_only'] is True
            assert 'data-deployment-mode="docker"' in request('/admin/console')
            passed('fresh empty directory, permissions, identical security/persistence defaults, built-in model, healthy')

            for path in ('/mcp', '/sse'):
                body = {} if path == '/mcp' else None
                request(path, body, headers={'Host': 'attacker.invalid'}, expected=421)
                request(path, body, headers={'Origin': 'https://attacker.invalid'}, expected=403)
            with opener.open(f'http://127.0.0.1:{port}/sse', timeout=10) as response:
                assert response.status == 200 and response.headers.get_content_type() == 'text/event-stream'
                first_event = response.readline().decode().strip()
                assert first_event == 'event: endpoint', first_event
            mcp('stats', {})
            text = f'Phase33 {method} persistent memory 中文安装验收'
            created = mcp('add', {'text': text, 'summary': 'Isolated installation acceptance',
                'namespace': 'docker-phase33', 'confirm_codes': ['namespace_not_found'],
                'metadata': {'keywords': ['persistence'], 'categories': ['verification'], 'anchors': ['Phase33Install']}})
            mid = created['id']

            def verify():
                assert mid in json.dumps(mcp('search_semantic', {'query': text, 'namespace': 'docker-phase33'}))
                assert text == request('/api/memories/' + mid)['text']

            verify()
            request('/api/config', {'language': 'en', 'debug': False})
            config = request('/api/config')
            original = snapshot()
            assert {v[1] for v in original['vectors']} == {PROFILE}
            request('/api/maintenance/enter', {})
            request('/api/sync', {})
            execute(f"from pathlib import Path; assert {mid!r} in Path('/data/memories.jsonl').read_text(); assert not Path('/data/models').exists()")
            request('/api/maintenance/exit', {})
            passed('real MCP add/search over /mcp, SSE handshake, Host/Origin, Dual JSONL, SQLite integrity and Profile')

            seconds = stop()
            remove()
            start()
            verify()
            assert snapshot() == original and request('/api/config') == config
            passed('graceful stop and container recreation retain memory, vector bytes, Profile and configuration', {'stop_seconds': seconds})
            stop()

            # Same stopped full-directory archive as README sudo tar, using the
            # already installed image's root helper instead of host sudo.
            backup = directory / 'instance.tar'
            docker('run', '--rm', '--network', 'none', '--read-only', '--user', '0:0', '--entrypoint', 'tar',
                '--mount', f'type=bind,src={data},dst=/source,readonly',
                '--mount', f'type=bind,src={directory},dst=/backup', args.image,
                '--numeric-owner', '-C', '/source', '-cpf', '/backup/instance.tar', '.')
            restored = directory / 'restored-data'
            restored.mkdir()
            docker('run', '--rm', '--network', 'none', '--read-only', '--user', '0:0', '--entrypoint', 'tar',
                '--mount', f'type=bind,src={restored},dst=/target',
                '--mount', f'type=bind,src={directory},dst=/backup,readonly', args.image,
                '--numeric-owner', '--same-owner', '-C', '/target', '-xpf', '/backup/instance.tar')
            remove()
            env['REMEMBER_ME_DATA_DIR'] = str(restored)
            start()
            verify()
            assert snapshot() == original and request('/api/config') == config
            passed('full stopped backup restores ownership, configuration, vectors and retrieval to new directory',
                   {'archive_sha256': hashlib.sha256(backup.read_bytes()).hexdigest()})
            stop()
            remove()
            report['status'] = 'passed'
        except BaseException as exc:
            report['status'] = 'failed'
            report['error'] = str(exc)
            raise
        finally:
            if container:
                logs = docker('logs', '--tail', '150', container, check=False)
                (directory / 'container.log').write_text(logs.stdout + logs.stderr)
                if method == 'run':
                    docker('stop', '--time', '120', container, check=False, timeout=150)
                    docker('rm', container, check=False)
                else:
                    compose('down', check=False, timeout=150)
            save()
    evidence['status'] = 'passed'
    save()
    print('PASS: both README installation paths; no registry writes or production data access.', flush=True)


if __name__ == '__main__':
    main()
