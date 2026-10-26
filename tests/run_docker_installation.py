"""Opt-in Phase 3.3 acceptance of the installation-guide commands against a LOCAL image.

Never pulls/pushes images or touches production data. Uses fresh /tmp directories
and alternate loopback ports. Keeps evidence and stops/removes its own containers.
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PROFILE = 'ep_6134c0b15d2bb471'


def installation_blocks(path):
    blocks = re.findall(r'```bash\n(.*?)\n```', path.read_text(), flags=re.S)
    lines = [line for block in blocks for line in block.replace('\\\n', ' ').splitlines()]
    runs = [line for line in lines if line.startswith('docker run ')]
    return {
        'minimal': next(line for line in runs if ' -d ' not in line),
        'run': next(line for line in runs if ' -d ' in line and '--user' not in line),
        'secure': next(line for line in runs if '--user' in line),
        'compose': next(line for line in lines
                        if line.startswith('docker compose -f compose.yaml up -d --wait')),
    }


def docker_run_argv(block, *, image, name, data, port=None, extra=()):
    """Adapt the documented command to an isolated LOCAL fixture."""
    argv = shlex.split(block.replace('\\\n', ' '))
    assert argv[:2] == ['docker', 'run']
    assert argv[-1] == 'ghcr.io/tech-kuiperbelt/remember-me:latest'
    argv[-1] = image
    argv[argv.index('--name') + 1] = name
    mount = argv.index('-v') + 1
    assert argv[mount] == '$PWD:/data'
    argv[mount] = str(data) + ':/data'
    if '-p' in argv:
        assert port is not None and 1024 <= port <= 65535 and port not in (1314, 5263)
        published = argv.index('-p') + 1
        assert argv[published] == '127.0.0.1:1314:1314'
        argv[published] = f'127.0.0.1:{port}:1314'
    else:
        assert port is None
    if '--user' in argv:
        user = argv.index('--user') + 1
        assert argv[user] == '$(id -u):$(id -g)'
        argv[user] = f'{os.getuid()}:{os.getgid()}'
    # Even an absent fixture must never cause an implicit public pull.
    argv[-1:-1] = ['--pull', 'never', *extra]
    return argv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, help='Already-built local image; never pulled')
    parser.add_argument('--port', type=int, default=19143, help='First isolated port; never 1314 or 5263')
    parser.add_argument('--methods', default='minimal,run,compose,secure')
    args = parser.parse_args()
    methods = args.methods.split(',')
    assert methods and len(set(methods)) == len(methods)
    assert set(methods) <= {'minimal', 'run', 'compose', 'secure'}
    assert 1024 <= args.port and args.port + len(methods) - 1 <= 65535
    assert not {1314, 5263} & set(range(args.port, args.port + len(methods)))
    if 'secure' in methods:
        assert os.getuid() != 0, 'Run secure-mode acceptance from a non-root host account'
    work = Path(tempfile.mkdtemp(prefix='rm-phase33-'))
    guide = ROOT / 'docs/installation/docker.md'
    commands = installation_blocks(guide)
    assert commands == installation_blocks(ROOT / 'docs/installation/docker.zh-CN.md')
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

    for index, method in enumerate(methods):
        directory = work / method
        directory.mkdir()
        data = directory / 'data'
        data.mkdir()
        original_directory = data.stat()
        port = args.port + index
        name = f'{work.name}-{method}'
        env = dict(os.environ, REMEMBER_ME_IMAGE=args.image, REMEMBER_ME_DATA_DIR=str(data),
                   REMEMBER_ME_PORT=str(port), COMPOSE_PROJECT_NAME=name)
        report = {'port': port, 'checks': []}
        evidence['paths'][method] = report
        (directory / 'compose.yaml').write_bytes((ROOT / 'compose.yaml').read_bytes())
        container = None
        foreground = None

        def passed(check, detail=True):
            report['checks'].append({'check': check, 'detail': detail})
            save()
            print(f'PASS [{method}]: {check}', flush=True)

        def compose(*argv, **kwargs):
            if argv and argv[0] == 'up':
                argv = (*argv, '--pull', 'never')
            return command(['docker', 'compose', '-f', 'compose.yaml', *argv], env=env, cwd=directory, **kwargs)

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
            nonlocal container, foreground
            if method != 'compose':
                argv = docker_run_argv(commands[method], image=args.image, name=name,
                                       data=data, port=port)
                container = name
                if method == 'minimal':
                    with (directory / 'foreground.log').open('a') as output:
                        foreground = subprocess.Popen(argv, cwd=data, env=env,
                            stdout=output, stderr=subprocess.STDOUT)
                else:
                    command(argv, cwd=data, env=env)
            else:
                compose('up', '-d', '--wait', '--wait-timeout', '300')
                container = compose('ps', '-q', 'remember-me').stdout.strip()
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                try:
                    if request('/api/model').get('ready') and inspect()['State']['Health']['Status'] == 'healthy':
                        return
                except (OSError, AssertionError, RuntimeError):
                    pass
                time.sleep(1)
            raise TimeoutError(docker('logs', '--tail', '70', container, check=False).stdout)

        def stop():
            began = time.monotonic()
            if method != 'compose':
                docker('stop', '--time', '120', container, timeout=150)
            else:
                compose('stop', timeout=150)
            if foreground is not None:
                assert foreground.wait(timeout=10) == 0
            state = inspect()['State']
            assert state['ExitCode'] == 0 and not state['OOMKilled'], state
            return round(time.monotonic() - began, 3)

        def remove():
            nonlocal container
            logs = docker('logs', '--tail', '150', container, check=False)
            with (directory / 'container.log').open('a') as output:
                output.write(logs.stdout + logs.stderr)
            if method != 'compose':
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
            expected_uid = os.getuid() if method == 'secure' else 0
            assert execute('import os; print(os.getuid())') == str(expected_uid)
            assert info['HostConfig']['ReadonlyRootfs'] is (method == 'secure')
            if method == 'secure':
                assert info['HostConfig']['CapDrop'] == ['ALL']
                assert 'no-new-privileges:true' in info['HostConfig']['SecurityOpt']
            if method == 'minimal':
                assert info['HostConfig']['RestartPolicy']['Name'] == 'no'
                assert foreground is not None and foreground.poll() is None
            else:
                assert info['Config']['StopTimeout'] == 120
                assert info['HostConfig']['RestartPolicy']['Name'] == 'unless-stopped'
            assert info['HostConfig']['PortBindings']['1314/tcp'] == [
                {'HostIp': '127.0.0.1', 'HostPort': str(port)}]
            mounts = {mount['Destination']: mount for mount in info['Mounts']}
            assert mounts['/data']['Type'] == 'bind' and mounts['/data']['RW']
            assert mounts['/data']['Source'] == str(data)
            current = data.stat()
            assert (current.st_uid, current.st_gid, current.st_mode) == (
                original_directory.st_uid, original_directory.st_gid, original_directory.st_mode)
            if method != 'minimal':
                assert mounts['/etc/localtime']['RW'] is False
                assert execute("import time; print(time.strftime('%Z %z'))") == time.strftime('%Z %z')
                before = time.time()
                line = execute("""
import logging
from pathlib import Path
from app.log_config import configure, close
handlers = configure('/data/logs')
try:
 logging.getLogger('installation.acceptance').info('host timezone probe')
finally:
 close(handlers)
print(Path('/data/logs/memory-server.log').read_text().splitlines()[-1])
""")
                logged = datetime.strptime(line.split(' [INFO]', 1)[0], '%Y-%m-%d %H:%M:%S,%f').timestamp()
                assert before - 1 <= logged <= time.time() + 1, line
                passed('host timezone matches container offset and actual application log timestamp', line)
            config = request('/api/config')
            assert config['auto_download_model'] is False and config['local_only'] is True
            assert 'data-deployment-mode="docker"' in request('/admin/console')
            passed('fresh ordinary directory, expected user/options, unchanged directory ownership, built-in model, healthy')

            for path in ('/mcp', '/sse'):
                body = {} if path == '/mcp' else None
                request(path, body, headers={'Host': 'attacker.invalid'}, expected=421)
                request(path, body, headers={'Origin': 'https://attacker.invalid'}, expected=403)
            with opener.open(f'http://127.0.0.1:{port}/sse', timeout=10) as response:
                assert response.status == 200 and response.headers.get_content_type() == 'text/event-stream'
                assert response.readline().decode().strip() == 'event: endpoint'
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
            passed('real MCP add/search and HTTP get, SSE handshake, Host/Origin, Dual JSONL, SQLite integrity and Profile')

            seconds = stop()
            remove()
            start()
            verify()
            assert snapshot() == original and request('/api/config') == config
            passed('graceful stop and container recreation retain memory, vector bytes, Profile and configuration', {'stop_seconds': seconds})
            stop()

            # Same stopped full-directory archive as the installation guide, using the
            # already installed image's root helper instead of host sudo.
            backup = directory / 'instance.tar'
            docker('run', '--rm', '--pull', 'never', '--network', 'none', '--read-only', '--user', '0:0', '--entrypoint', 'tar',
                '--mount', f'type=bind,src={data},dst=/source,readonly',
                '--mount', f'type=bind,src={directory},dst=/backup', args.image,
                '--numeric-owner', '-C', '/source', '-cpf', '/backup/instance.tar', '.')
            restored = directory / 'restored-data'
            restored.mkdir()
            docker('run', '--rm', '--pull', 'never', '--network', 'none', '--read-only', '--user', '0:0', '--entrypoint', 'tar',
                '--mount', f'type=bind,src={restored},dst=/target',
                '--mount', f'type=bind,src={directory},dst=/backup,readonly', args.image,
                '--numeric-owner', '--same-owner', '-C', '/target', '-xpf', '/backup/instance.tar')
            remove()
            data = restored
            original_directory = data.stat()
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
                if method != 'compose':
                    docker('stop', '--time', '120', container, check=False, timeout=150)
                    docker('rm', container, check=False)
                else:
                    compose('down', check=False, timeout=150)
                if foreground is not None:
                    foreground.wait(timeout=10)
            save()
    evidence['status'] = 'passed'
    save()
    print('PASS: selected Docker installation modes; no registry writes or production data access.', flush=True)


if __name__ == '__main__':
    main()
