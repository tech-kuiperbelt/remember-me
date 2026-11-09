#!/usr/bin/env python3
"""Accept the final DMG on macOS ARM64; source Python only orchestrates checks.

Every product import/inference and service runs with the DMG's Python under an
OS sandbox denying non-loopback outbound connections. GUI checks remain manual.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import traceback
from urllib.request import Request, ProxyHandler, build_opener


TEXT = 'MacOSOfflineSmoke remembers a bilingual memory. 请记住这段中文离线记忆。'
UPDATED = TEXT + ' Updated before restart.'
NAMESPACE = 'macos-smoke'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def tree_digest(root):
    """Hash contents, symlink targets and modes to detect writes in the app."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(str(path.lstat().st_mode).encode())
        if path.is_symlink():
            digest.update(os.readlink(path).encode())
        elif path.is_file():
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
    return digest.hexdigest()


def group_members(pgid):
    output = subprocess.check_output(['/bin/ps', '-axo', 'pid=,ppid=,pgid=,stat=,command='], text=True)
    return [line.strip() for line in output.splitlines() if int(line.split()[2]) == pgid]


class Client:
    def __init__(self, port):
        self.base = f'http://127.0.0.1:{port}'
        self.opener = build_opener(ProxyHandler({}))
        self.sequence = 0

    def request(self, path, payload=None, method=None):
        headers = {'Accept': 'application/json, text/event-stream',
                   'MCP-Protocol-Version': '2025-03-26'}
        if payload is not None:
            headers['Content-Type'] = 'application/json'
        request = Request(self.base + path, headers=headers, method=method,
                          data=None if payload is None else json.dumps(payload).encode())
        with self.opener.open(request, timeout=120) as response:
            body = response.read()
            return json.loads(body) if 'json' in response.headers.get('Content-Type', '') else body

    def rpc(self, method, params):
        self.sequence += 1
        response = self.request('/mcp', {'jsonrpc': '2.0', 'id': self.sequence,
                                       'method': method, 'params': params})
        require(response.get('id') == self.sequence and 'error' not in response,
                f'MCP protocol failure: {response}')
        return response['result']

    def tool(self, name, arguments):
        response = self.rpc('tools/call', {'name': name, 'arguments': arguments})
        require(not response.get('isError'), f'MCP {name} failed: {response}')
        return json.loads(response['content'][0]['text'])


INFERENCE = r'''
import errno, json, os, platform, site, socket, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
runtime = root / 'runtime/python'
assert sys.platform == 'darwin' and platform.machine() == 'arm64'
assert sys.version_info[:2] == (3, 12)
assert Path(sys.executable).resolve().is_relative_to(runtime)
assert Path(sys.prefix).resolve() == runtime.resolve() and sys.prefix == sys.base_prefix
assert not site.ENABLE_USER_SITE
# Failure must be sandbox denial, not an unavailable network or timeout.
with socket.socket() as probe:
    probe.settimeout(3)
    try:
        probe.connect(('1.1.1.1', 443))
    except OSError as exc:
        assert exc.errno in (errno.EPERM, errno.EACCES), repr(exc)
    else:
        raise AssertionError('OS sandbox allowed external networking')
sys.path.insert(0, str(root))
from bootstrap import configure
data = configure(root)
assert data == Path.home() / 'Library/Application Support/remember-me'
assert not json.loads((data / 'config.jsonc').read_text())['auto_download_model']
import torch
import sentence_transformers
from sentence_transformers import SentenceTransformer
from sentence_transformers.models import Pooling, Normalize
torch.set_num_threads(2)
manifest = json.loads((root / 'models/bge-m3-manifest.json').read_text())
assert manifest['revision'] == '5617a9f61b028005a4858fdac845db406aefb181'
snapshot = root / 'models/sentence-transformers/models--BAAI--bge-m3/snapshots' / manifest['revision']
model = SentenceTransformer(str(snapshot), device='cpu', local_files_only=True)
assert str(model.device) == 'cpu' and torch.version.cuda is None
pooling = [module for module in model if isinstance(module, Pooling)]
assert len(pooling) == 1
pool = pooling[0].get_config_dict()
assert pool.get('pooling_mode') == 'cls' or pool.get('pooling_mode_cls_token') is True
assert isinstance(model[-1], Normalize)
vectors = model.encode(['Remember the offline English memory.', '请记住这段中文离线记忆。'], convert_to_tensor=True)
assert vectors.device.type == 'cpu' and tuple(vectors.shape) == (2, 1024)
assert torch.isfinite(vectors).all()
assert torch.allclose(vectors.norm(dim=1), torch.ones(2), atol=1e-5)
print(json.dumps({'python': sys.version, 'executable': sys.executable, 'prefix': sys.prefix,
 'torch': torch.__version__, 'sentence_transformers': sentence_transformers.__version__,
 'device': str(model.device), 'shape': list(vectors.shape), 'model_revision': manifest['revision'],
 'default_data_directory': str(data), 'outbound_network': 'OS sandbox denied (EPERM/EACCES)'}))
'''


LOCK = r'''
import sys
sys.path.insert(0, sys.argv[1])
from app.storage.database_lock import DatabaseWriterLock
from app.storage.ndjson import NDJSONReplica
lock = DatabaseWriterLock(sys.argv[2]) if sys.argv[3] == 'primary' else NDJSONReplica(sys.argv[2])
if sys.argv[3] == 'primary':
    lock.acquire()
    lock.release()
else:
    lock.acquire_writer_lock()
    lock.release_writer_lock()
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dmg', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True, help='New or empty evidence directory')
    args = parser.parse_args()
    dmg, work = args.dmg.resolve(), args.work.resolve()
    require(sys.platform == 'darwin' and platform.machine() == 'arm64', 'Requires native macOS ARM64')
    require(not work.exists() or not any(work.iterdir()), 'Acceptance work directory must be empty')
    require(not dmg.is_relative_to(work), 'DMG must be outside the acceptance directory')
    work.mkdir(parents=True, exist_ok=True)
    mount = work / 'mounted'
    mount.mkdir()
    result = {'status': 'failed', 'dmg': str(dmg), 'checks': [],
              'unverified': ['Finder drag-to-Applications installation', 'Dock/menu visual appearance',
                             'Finder/Dock icon visual rendering on a real Mac',
                             'Automatic browser opening', 'AppKit Quit from GUI',
                             'Gatekeeper/quarantine behavior on a separate Mac']}
    mounted = False
    process = None
    log = None
    app = work / 'Remember Me.app'
    profile = work / 'offline.sb'
    profile.write_text('(version 1)\n(allow default)\n(deny network-outbound)\n'
                       '(allow network-outbound (remote ip "localhost:*"))\n')
    environment = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': str(work / 'home'),
                   'TMPDIR': str(work / 'tmp'), 'LANG': 'en_US.UTF-8',
                   'OMP_NUM_THREADS': '2', 'OPENBLAS_NUM_THREADS': '2',
                   'TOKENIZERS_PARALLELISM': 'false'}
    Path(environment['HOME']).mkdir()
    Path(environment['TMPDIR']).mkdir()

    def sandbox(argv):
        return ['/usr/bin/sandbox-exec', '-f', str(profile), *map(str, argv)]

    def passed(name, detail=True):
        result['checks'].append({'check': name, 'detail': detail})
        print('PASS:', name, flush=True)

    def product_python(code, *arguments):
        return sandbox([app / 'Contents/Resources/runtime/python/bin/python3.12',
                        '-I', '-B', '-c', code, *arguments])

    try:
        subprocess.run(['/usr/bin/hdiutil', 'verify', str(dmg)], check=True)
        subprocess.run(['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse',
                        '-mountpoint', str(mount), str(dmg)], check=True)
        mounted = True
        source = mount / 'Remember Me.app'
        require(source.is_dir(), 'Final DMG has no Remember Me.app')
        subprocess.run(['/usr/bin/ditto', str(source), str(app)], check=True)
        passed('copied app from verified final read-only DMG')
        info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
        require(info.get('CFBundleIconFile') == 'RememberMe.icns',
                f'Bundle icon not declared: {info.get("CFBundleIconFile")!r}')
        icon = app / 'Contents/Resources/RememberMe.icns'
        require(icon.is_file(), 'Bundle icon file is missing')
        iconset = work / 'icon.iconset'
        subprocess.run(['/usr/bin/iconutil', '-c', 'iconset', str(icon), '-o', str(iconset)], check=True)
        icon_files = sorted(path.name for path in iconset.glob('*.png'))
        require(len(icon_files) >= 10, f'Incomplete iconset: {icon_files}')
        passed('bundle declares RememberMe.icns and iconutil expands a complete iconset', icon_files)
        before = tree_digest(app)
        resources = app / 'Contents/Resources'
        inference = subprocess.run(product_python(INFERENCE, resources), env=environment,
                                   cwd=work, capture_output=True, text=True, timeout=300)
        (work / 'inference.log').write_text(inference.stdout + inference.stderr)
        require(inference.returncode == 0, 'Bundled Python inference failed; see inference.log')
        passed('bundled Python/PyTorch/Sentence Transformers, OS-offline bilingual CPU inference',
               json.loads(inference.stdout.strip().splitlines()[-1]))
        data = work / 'data'
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        client = Client(port)
        memory_id = None
        for phase in ('initial', 'relocated'):
            if phase == 'relocated':
                relocated = work / 'moved path with spaces/Remember Me.app'
                relocated.parent.mkdir()
                app.rename(relocated)
                app = relocated
                resources = app / 'Contents/Resources'
                passed('moved complete app to a different path containing spaces', str(app))
            executable = app / 'Contents/MacOS/remember-me'
            argv = [executable, '--no-browser', '--data-dir', data, '--port', port]
            log = (work / f'{phase}-server.log').open('wb')
            process = subprocess.Popen(sandbox(argv), cwd=work, env=environment,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic() + 300
            model = None
            while time.monotonic() < deadline:
                require(process.poll() is None, f'Service exited {process.returncode}; see {phase}-server.log')
                try:
                    model = client.request('/api/model')
                    if model.get('ready'):
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(0.5)
            else:
                raise RuntimeError(f'Model readiness timed out: {model}')
            passed(f'{phase}: app executable starts HTTP service under OS offline sandbox', model)
            for path in ('/admin/console', '/admin/console/static/app.css',
                         '/admin/console/static/js/main.js', '/read-me'):
                require(bool(client.request(path)), f'Web resource unavailable: {path}')
            passed(f'{phase}: Web UI and static assets accessible')
            config = client.request('/api/config')
            require(config['local_only'] is True and config['auto_download_model'] is False,
                    f'Unexpected packaged configuration: {config}')
            client.rpc('initialize', {'protocolVersion': '2025-03-26', 'capabilities': {},
                                     'clientInfo': {'name': 'macos-dmg-acceptance', 'version': '1'}})
            names = {tool['name'] for tool in client.rpc('tools/list', {})['tools']}
            require({'add', 'search_semantic', 'get_details', 'update', 'delete'} <= names,
                    f'Core MCP tools absent: {names}')
            if phase == 'initial':
                # Full duplicate launch must reject promptly before loading a second model.
                duplicate = subprocess.run(sandbox(argv), env=environment, cwd=work,
                                           capture_output=True, timeout=30)
                (work / 'duplicate-launch.log').write_bytes(duplicate.stdout + duplicate.stderr)
                require(duplicate.returncode != 0 and b'already active' in duplicate.stderr,
                        'Duplicate app launch did not reject the active data directory')
                passed('duplicate launch rejects same data directory')
                for kind, name in (('primary', 'memories.db'), ('replica', 'memories.jsonl')):
                    contender = subprocess.run(product_python(LOCK, resources, data / name, kind),
                                               cwd=work, env=environment, capture_output=True, timeout=30)
                    require(contender.returncode != 0 and b'active' in contender.stderr,
                            f'{kind} lock did not reject an independent writer: {contender.stderr!r}')
                passed('primary and JSONL writer locks exclude independent processes')
                added = client.tool('add', {'text': TEXT, 'summary': 'macOS offline acceptance fixture.',
                                          'namespace': NAMESPACE, 'confirm_codes': ['namespace_not_found'],
                                          'metadata': {'categories': ['verification'], 'anchors': ['MacOSOfflineSmoke']}})
                require(added.get('created') and added.get('id'), f'Create failed: {added}')
                memory_id = added['id']
                updated = client.tool('update', {'memory_id': memory_id, 'text': UPDATED,
                                                'summary': 'Updated macOS offline acceptance fixture.',
                                                'metadata': {'categories': ['verification'], 'anchors': ['MacOSOfflineSmoke']}})
                require(updated.get('success'), f'Update failed: {updated}')
                passed('MCP creates and updates memory using packaged offline model', memory_id)
            details = client.tool('get_details', {'memory_ids': [memory_id]})
            require(not details['missing'] and details['found'][0]['text'] == UPDATED,
                    f'Memory content incorrect: {details}')
            found = client.tool('search_semantic', {'query': '中文离线记忆', 'namespace': NAMESPACE, 'k': 3})
            require(memory_id in {item['id'] for item in found['items']}, f'Search missed memory: {found}')
            passed(f'{phase}: MCP read and semantic retrieval preserve updated content')
            if phase == 'relocated':
                require(client.tool('delete', {'memory_id': memory_id}).get('success'), 'Delete failed')
                require(memory_id in client.tool('get_details', {'memory_ids': [memory_id]})['missing'],
                        'Deleted memory is still returned')
                passed('MCP deletion verified')
                process.send_signal(signal.SIGTERM)
            else:
                client.request('/api/maintenance/enter', {})
                client.request('/api/shutdown', {})
            pid = process.pid
            code = process.wait(timeout=120)
            require(code in (0, -signal.SIGTERM, 128 + signal.SIGTERM), f'Unexpected shutdown exit code: {code}')
            process = None
            log.close()
            log = None
            require('Application shutdown complete.' in (work / f'{phase}-server.log').read_text(),
                    f'{phase}: graceful shutdown completion missing')
            # CPython helpers can observe EOF and exit just after their parent.
            # Wait for the whole group; retain identities instead of hiding leaks.
            initial_members = group_members(pid)
            remaining = initial_members
            drain_deadline = time.monotonic() + 10
            while remaining and time.monotonic() < drain_deadline:
                time.sleep(0.1)
                remaining = group_members(pid)
            (work / f'{phase}-processes.log').write_text(
                f'Immediately after parent exit: {initial_members!r}\nAfter drain: {remaining!r}\n')
            require(not remaining, f'Service processes remain in process group {pid}: {remaining}')
            with socket.socket() as sock:
                require(sock.connect_ex(('127.0.0.1', port)) != 0, 'Service port remains open')
            for kind, name in (('primary', 'memories.db'), ('replica', 'memories.jsonl')):
                released = subprocess.run(product_python(LOCK, resources, data / name, kind),
                                          cwd=work, env=environment, capture_output=True, timeout=30)
                require(released.returncode == 0, f'{kind} lock not released: {released.stderr!r}')
            with sqlite3.connect((data / 'memories.db').as_uri() + '?mode=ro', uri=True) as db:
                require(db.execute('pragma integrity_check').fetchone()[0] == 'ok', 'SQLite integrity failure')
                require(not db.execute('pragma foreign_key_check').fetchall(), 'SQLite foreign key failure')
            require((data / 'memories.jsonl').is_file(), 'Dual JSONL replica missing')
            if phase == 'initial':
                require(memory_id in (data / 'memories.jsonl').read_text(), 'Persisted JSONL memory missing')
            passed(f'{phase}: graceful exit, no remaining processes/listener, both locks released, SQLite integrity')
        require(tree_digest(app) == before, 'Application resources changed during runtime')
        passed('app contents remain byte-for-byte unchanged across inference, CRUD and relocation')
        result['status'] = 'passed'
    except BaseException:
        result['error'] = traceback.format_exc()
        raise
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        if log is not None:
            log.close()
        if mounted:
            detached = subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount)], capture_output=True)
            if detached.returncode:
                result['detach_error'] = detached.stderr.decode(errors='replace')
        (work / 'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
