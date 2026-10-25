"""Opt-in real Docker acceptance, isolated from production data and port 1314.

Run after building both local images:
  python tests/run_docker_deployment.py --port 18142
Keeps evidence and stopped test containers on failure. Never uses ./data.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=18142)
    parser.add_argument('--image', default='remember-me:phase2.2-amd64')
    parser.add_argument('--old-image', default='remember-me:phase2.1-amd64')
    parser.add_argument('--work', type=Path, help='reuse only a directory created by this validator')
    args = parser.parse_args()
    work = args.work or Path(tempfile.mkdtemp(prefix='rm-phase22-'))
    assert work.name.startswith('rm-phase22-') and work.parent == Path('/tmp'), work
    env = dict(os.environ, REMEMBER_ME_IMAGE=args.image,
               REMEMBER_ME_DATA_DIR=str(work / 'data'), REMEMBER_ME_PORT=str(args.port))
    project = 'rm-phase22-' + work.name.rsplit('-', 1)[-1]
    base = ['docker', 'compose', '-f', str(ROOT / 'compose.yaml'), '-p', project]
    evidence = {'work': str(work), 'project': project, 'port': args.port, 'checks': []}

    def command(argv, *, environment=None, check=True):
        result = subprocess.run(argv, env=environment or env, text=True, capture_output=True)
        if check and result.returncode:
            raise RuntimeError(f'{argv}: {result.stderr}\n{result.stdout}')
        return result

    def compose(*argv, **kwargs):
        return command(base + list(argv), **kwargs)

    def passed(name, detail=True):
        evidence['checks'].append({'name': name, 'result': detail})
        (work / 'results.json').write_text(json.dumps(evidence, indent=2))
        print('PASS:', name, flush=True)

    def request(path, body=None, method=None, headers=None, expected=200):
        headers = {'Content-Type': 'application/json', **(headers or {})}
        req = urllib.request.Request('http://127.0.0.1:' + str(args.port) + path,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers=headers, method=method)
        try:
            response = urllib.request.urlopen(req, timeout=120)
        except urllib.error.HTTPError as exc:
            response = exc
        raw = response.read()
        assert response.status == expected, (path, response.status, raw)
        return json.loads(raw) if response.headers.get_content_type() == 'application/json' else raw.decode()

    def ready():
        deadline = time.monotonic() + 210
        while time.monotonic() < deadline:
            try:
                if request('/api/model')['ready']:
                    return
            except (OSError, AssertionError):
                pass
            time.sleep(1)
        raise TimeoutError(compose('logs', '--tail', '60').stdout)

    def execute(code):
        return compose('exec', '-T', 'remember-me', 'python', '-c', code).stdout.strip()

    def snapshot():
        return json.loads(execute("""
import sqlite3, json, hashlib
c=sqlite3.connect('/data/memories.db')
assert c.execute('pragma integrity_check').fetchone()[0]=='ok'
assert not c.execute('pragma foreign_key_check').fetchall()
rows=c.execute('select memory_id,profile_id,embedding from memory_embeddings order by memory_id').fetchall()
print(json.dumps({'vectors':[(m,p,hashlib.sha256(b).hexdigest()) for m,p,b in rows],
 'memories':c.execute('select id,text,metadata from memories order by id').fetchall()}))
"""))

    try:
        print('Evidence:', work, flush=True)
        compose('up', '-d', '--wait', '--wait-timeout', '240')
        ready()
        cid = compose('ps', '-q', 'remember-me').stdout.strip()
        info = json.loads(command(['docker', 'inspect', cid]).stdout)[0]
        assert info['Config']['User'] == '10001:10001'
        assert info['HostConfig']['ReadonlyRootfs']
        assert info['HostConfig']['PortBindings']['1314/tcp'] == [{'HostIp': '127.0.0.1', 'HostPort': str(args.port)}]
        assert execute('import os; print(os.getuid(), os.getgid())') == '10001 10001'
        assert request('/api/config')['auto_download_model'] is False
        assert 'data-deployment-mode="docker"' in request('/admin/console')
        passed('empty bind directory, non-root, read-only root, loopback publication and HTTP health')

        for path in ['/mcp', '/sse', '/messages/?session_id=unknown']:
            method = 'GET' if path == '/sse' else 'POST'
            request(path, method=method, headers={'Host': 'attacker.invalid'}, expected=421)
            request(path, method=method, headers={'Origin': 'https://attacker.invalid'}, expected=403)
        passed('Host 421 and Origin 403 on all three MCP routes (HTTP probes)')

        created = request('/api/memories', {'text': 'Phase22 WAL durable memory 持久化测试',
            'namespace': 'docker-phase22', 'confirm_codes': ['namespace_not_found'],
            'metadata': {'keywords': ['persistence'], 'categories': ['testing'], 'anchors': ['Phase22Anchor']}}, expected=201)
        mid = created['id']
        request('/api/memories/' + mid, {'text': 'Phase22 WAL durable memory UPDATED 更新完成',
            'metadata': {'keywords': ['persistence'], 'categories': ['testing'], 'anchors': ['Phase22Anchor']}}, method='PUT')

        def verify_memory():
            assert 'UPDATED' in request('/api/memories/' + mid)['text']
            for suffix, query in [('', '持久化 durable memory'), ('/lexical', 'UPDATED'), ('/anchor', 'Phase22Anchor')]:
                result = request('/api/memories/search' + suffix, {'query': query, 'namespace': 'docker-phase22'})
                assert mid in json.dumps(result), result

        verify_memory()
        original = snapshot()
        assert {v[1] for v in original['vectors']} == {'ep_6134c0b15d2bb471'}
        passed('real BGE-M3 CRUD, semantic, FTS and Anchor search; SQLite integrity/profile', original)
        request('/api/maintenance/enter', {}, method='POST')
        request('/api/sync', {}, method='POST')
        replica = execute("from pathlib import Path; print(Path('/data/memories.jsonl').read_text())")
        assert mid in replica and 'UPDATED' in replica
        execute("from pathlib import Path; assert not Path('/data/models').exists(); assert Path('/data/logs').is_dir(); assert Path('/data/tmp').is_dir()")
        passed('Dual JSONL contains updated record; runtime files in /data, no host model copy')
        request('/api/maintenance/exit', {}, method='POST')

        request('/api/config', {'debug': True, 'language': 'en'}, method='POST')
        persisted = request('/api/config')
        compose('stop')
        stopped = json.loads(command(['docker', 'inspect', cid]).stdout)[0]
        assert stopped['State']['ExitCode'] == 0
        compose('down')
        compose('up', '-d', '--wait', '--wait-timeout', '240')
        ready()
        verify_memory()
        assert snapshot() == original
        assert request('/api/config') == persisted
        passed('graceful stop exit 0, down/up retains data, vectors, profile and configuration')

        # An old compatible image has no init helper: reuse the already prepared data.
        compose('stop')
        old_env = dict(env, REMEMBER_ME_IMAGE=args.old_image)
        compose('up', '-d', '--no-deps', '--force-recreate', 'remember-me', environment=old_env)
        ready()
        verify_memory()
        assert snapshot() == original
        compose('stop')
        compose('up', '-d', '--force-recreate', '--wait', '--wait-timeout', '240')
        ready()
        assert snapshot() == original
        passed(f'compatible {args.image} → {args.old_image} → {args.image} reuse, vector bytes unchanged')

        compose('stop')
        command(['docker', 'run', '--rm', '--network', 'none', '--user', '0:0', '--entrypoint', 'tar',
                 '-v', str(work / 'data') + ':/source:ro', '-v', str(work) + ':/backup', args.image,
                 '-C', '/source', '-cpf', '/backup/instance.tar', '.'])
        restored = work / ('restored-' + str(time.time_ns()))
        restored.mkdir()
        command(['docker', 'run', '--rm', '--network', 'none', '--user', '0:0', '--entrypoint', 'tar',
                 '-v', str(restored) + ':/target', '-v', str(work) + ':/backup:ro', args.image,
                 '-C', '/target', '--same-owner', '-xpf', '/backup/instance.tar'])
        compose('down')
        env['REMEMBER_ME_DATA_DIR'] = str(restored)
        evidence['restored_data'] = str(restored)
        compose('up', '-d', '--wait', '--wait-timeout', '240')
        ready()
        verify_memory()
        assert snapshot() == original
        assert request('/api/config') == persisted
        passed('stopped full-directory backup and restore into a new directory')
        evidence['image'] = json.loads(command(['docker', 'image', 'inspect', args.image]).stdout)[0]['Id']
        evidence['old_image'] = json.loads(command(['docker', 'image', 'inspect', args.old_image]).stdout)[0]['Id']
        evidence['backup_sha256'] = hashlib.sha256((work / 'instance.tar').read_bytes()).hexdigest()
        evidence['status'] = 'passed'
        (work / 'results.json').write_text(json.dumps(evidence, indent=2))
        print('Acceptance complete. Container remains available for real browser/client tests.', flush=True)
    except BaseException:
        (work / 'compose.log').write_text(compose('logs', '--no-color', check=False).stdout)
        raise


if __name__ == '__main__':
    main()
