"""Validate an already-smoked complete runtime; never migrate or acquire user data."""
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
    for path in root.rglob('*'):
        if path.is_symlink():
            raise ValueError('Bundle must contain real files, not links: ' + str(path))
        if path.is_file() and (path.name in {'memories.db', 'config.jsonc', 'config.json'} or
                               path.name.endswith(('.writer.lock', '.db-wal', '.db-shm'))):
            raise ValueError('Unexpected user/runtime state in Bundle: ' + str(path))
    return version


if __name__ == '__main__':
    print(validate(sys.argv[1]))
