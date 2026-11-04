"""Validate an already-smoked complete runtime; never migrate or acquire user data."""
import hashlib
import json
from pathlib import Path
import re
import sys


def validate(root):
    root = Path(root).resolve()
    sys.path.insert(0, str(root))
    from app.config.jsonc import loads

    for name in ('runtime/python/python.exe', 'runtime/python/pythonw.exe', 'beta_start.py',
                 'tray_launcher.py', 'windows_tray.py', 'beta-policy.json',
                 'models/manifest.json', 'web/index.html'):
        if not (root / name).is_file():
            raise ValueError('Missing Setup input: ' + name)
    smoke = json.loads((root / 'build-evidence/business-smoke.json').read_text(encoding='utf-8'))
    if smoke.get('status') != 'passed':
        raise ValueError('Native business smoke must pass before Setup creation')
    version = loads((root / 'release.jsonc').read_text(encoding='utf-8')).get('version')
    if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('release.jsonc requires an approved numeric version')
    trusted_model = json.loads((Path(__file__).resolve().parents[2] / 'docker/bge-m3-manifest.json').read_text(encoding='utf-8'))
    snapshot = root / 'models/sentence-transformers/models--BAAI--bge-m3/snapshots' / trusted_model['revision']
    model_configs = {snapshot / entry['path']: entry for entry in trusted_model['files']
                     if Path(entry['path']).name == 'config.json'}
    for path in root.rglob('*'):
        if path.is_symlink():
            raise ValueError('Bundle must contain real files, not links: ' + str(path))
        if path.is_file() and path in model_configs:
            expected = model_configs[path]
            if path.stat().st_size != expected['size'] or hashlib.sha256(path.read_bytes()).hexdigest() != expected['sha256']:
                raise ValueError('Pinned model configuration mismatch: ' + str(path))
            continue
        if path.is_file() and (path.name in {'memories.db', 'config.jsonc', 'config.json'} or
                               path.name.endswith(('.writer.lock', '.db-wal', '.db-shm'))):
            raise ValueError('Unexpected user/runtime state in Bundle: ' + str(path))
    return version


if __name__ == '__main__':
    print(validate(sys.argv[1]))
