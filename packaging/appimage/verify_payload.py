"""Verify the existing Launcher assembly without installing dependencies."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
payload = Path(sys.argv[2]).resolve()
manifest = json.loads((payload / 'build-manifest.json').read_text())
if (payload / '.launcher-build-incomplete').exists():
    raise SystemExit('Launcher payload is incomplete')
for name in ('.env', 'data', 'config.jsonc', 'memories.db', 'memories.jsonl', 'audit.jsonl'):
    if (payload / name).exists() or (payload / name).is_symlink():
        raise SystemExit(f'Unexpected runtime data in Launcher payload: {name}')

def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for part in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(part)
    return digest.hexdigest()

for name, digest in manifest['input_sha256'].items():
    if sha(root / name) != digest:
        raise SystemExit(f'Launcher build input changed: {name}')
for name, digest in manifest['resource_sha256'].items():
    path = payload / name
    if not path.resolve().is_relative_to(payload) or sha(path) != digest:
        raise SystemExit(f'Launcher resource mismatch: {name}')

spec = importlib.util.spec_from_file_location('launcher_assembly', root / 'packaging/launcher/assemble.py')
assembly = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assembly)
production, production_records = assembly.recorded_packages(
    payload / 'runtime/python/lib/python3.12/site-packages', payload / 'runtime')
gui, gui_records = assembly.recorded_packages(payload / 'launcher/vendor', payload / 'launcher/vendor')
if production != {assembly.canonical(k): v for k, v in manifest['packages'].items()}:
    raise SystemExit('Production packages differ from manifest')
if gui != assembly.GUI_PACKAGES:
    raise SystemExit('GUI packages differ from locked versions')
models = assembly.model_source(root, payload)
for directory in ('runtime', 'licenses', 'launcher'):
    assembly.safe_tree(payload / directory)
print(json.dumps({'payload_manifest_sha256': sha(payload / 'build-manifest.json'),
                  'input_hashes': len(manifest['input_sha256']),
                  'resource_hashes': len(manifest['resource_sha256']),
                  'production_record_hashes': production_records,
                  'gui_record_hashes': gui_records, 'model_files': models}, indent=2))
