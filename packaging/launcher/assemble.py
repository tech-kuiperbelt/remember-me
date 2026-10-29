"""Validate explicit build sources and add the Linux Launcher to a new output."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import importlib.metadata as metadata
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess


INPUTS = (
    'packaging/standalone/build.sh', 'packaging/standalone/start.sh',
    'packaging/standalone/runtime.env', 'packaging/standalone/verify_runtime.py',
    'bin/verify_cpu_runtime.py', 'docker/pip-bootstrap.lock',
    'requirements/docker-cpu-py312-linux-amd64.lock', 'docker/package_model.py',
    'docker/bge-m3-manifest.json',
)
GUI_PACKAGES = {'pygobject': '3.48.2', 'pycairo': '1.26.1', 'pystray': '0.19.5',
                'pillow': '12.0.0', 'six': '1.17.0', 'python-xlib': '0.33'}


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def digest(path, algorithm='sha256'):
    result = hashlib.new(algorithm)
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            result.update(chunk)
    return result.digest()


def sha(path):
    return digest(path).hex()


def canonical(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def safe_tree(directory):
    for path in directory.rglob('*'):
        require(not path.is_symlink() or (path.exists() and path.resolve().is_relative_to(directory)),
                f'Broken or external source link: {path}')


def recorded_packages(directory, boundary):
    packages = {}
    checked = 0
    for distribution in metadata.distributions(path=[str(directory)]):
        packages[canonical(distribution.metadata['Name'])] = distribution.version
        require(distribution.files is not None, f'Package lacks RECORD: {distribution.metadata["Name"]}')
        for entry in distribution.files:
            path = Path(distribution.locate_file(entry))
            require(path.resolve().is_relative_to(boundary) and path.is_file(), f'Invalid RECORD path: {path}')
            if entry.hash:
                actual = base64.urlsafe_b64encode(digest(path, entry.hash.mode)).rstrip(b'=').decode()
                require(actual == entry.hash.value, f'Package RECORD hash mismatch: {path}')
                checked += 1
    return packages, checked


def model_source(root, runner):
    models = runner / 'models'
    manifest = json.loads((root / 'docker/bge-m3-manifest.json').read_text())
    require(sha(models / 'bge-m3-manifest.json') == sha(root / 'docker/bge-m3-manifest.json'),
            'Runner model manifest differs from pinned input')
    hub = models / 'sentence-transformers/models--BAAI--bge-m3'
    snapshot = hub / 'snapshots' / manifest['revision']
    require((hub / 'refs/main').read_text() == manifest['revision'], 'Runner model ref differs')
    allowed = {'bge-m3-manifest.json', str((hub / 'refs/main').relative_to(models))}
    for record in manifest['files']:
        allowed.add(str((snapshot / record['path']).relative_to(models)))
        allowed.add(str((hub / 'blobs' / record['source_oid']).relative_to(models)))
    for source in (root / 'docker/licenses').rglob('*'):
        if source.is_file():
            name = 'licenses/' + str(source.relative_to(root / 'docker/licenses'))
            allowed.add(name)
            require(sha(models / name) == sha(source), f'Model license mismatch: {name}')
    actual = {str(p.relative_to(models)) for p in models.rglob('*') if not p.is_dir()}
    require(actual == allowed, 'Model tree contains missing or undeclared files; not copied')
    safe_tree(models)
    spec = importlib.util.spec_from_file_location('model_packager', root / 'docker/package_model.py')
    packager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packager)
    for record in manifest['files']:
        packager.verify(snapshot / record['path'], record)
    return len(manifest['files'])


def copy_tree(source, target, *, source_code=False):
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc') if source_code else None
    shutil.copytree(source, target, symlinks=True, ignore=ignore)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'runner', 'gui', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    root, runner, gui, output = (getattr(args, n).resolve() for n in ('root', 'runner', 'gui', 'output'))
    fresh_runner = output == runner
    require(fresh_runner or not output.exists(), 'Refusing existing output')
    for source in (runner, gui):
        require(not (source / '.build-incomplete').exists(), f'Incomplete source: {source}')
        require(not (source / '.launcher-build-incomplete').exists(), f'Incomplete source: {source}')
    runner_bytes = (runner / 'build-manifest.json').read_bytes()
    runner_manifest = json.loads(runner_bytes)
    require(runner_manifest['python_version'] == '3.12.15', 'Runner Python differs from pinned 3.12.15')
    for name in INPUTS:
        require(runner_manifest['input_sha256'].get(name) == sha(root / name), f'Runner input changed: {name}')
    for name, expected in runner_manifest['resource_sha256'].items():
        path = runner / name
        require(path.resolve().is_relative_to(runner) and sha(path) == expected, f'Runner resource mismatch: {name}')
    for directory in ('runtime', 'licenses'):
        safe_tree(runner / directory)
    production, production_records = recorded_packages(
        runner / 'runtime/python/lib/python3.12/site-packages', runner / 'runtime')
    require(production == {canonical(k): v for k, v in runner_manifest['packages'].items()},
            'Runner installed packages differ from its manifest')
    model_count = model_source(root, runner)
    gui_bytes = (gui / 'build-manifest.json').read_bytes()
    gui_manifest = json.loads(gui_bytes)
    require(gui_manifest['python'].split()[0] == '3.12.15', 'GUI Python differs from Runner')
    for name in ('inputs.json', 'requirements-runtime.lock', 'requirements-build.lock'):
        require(sha(gui / name) == sha(root / 'packaging/tray-prototype' / name), f'GUI fixed input mismatch: {name}')
    safe_tree(gui / 'vendor')
    gui_packages, gui_records = recorded_packages(gui / 'vendor', gui / 'vendor')
    require(gui_packages == GUI_PACKAGES, 'GUI package versions differ from validated S2-A inputs')
    require(gui_packages == {canonical(k): v for k, v in gui_manifest['packages'].items()}, 'GUI manifest mismatch')
    for path in (gui / 'vendor/gi').glob('*.so'):
        dynamic = subprocess.check_output(['readelf', '-d', str(path)], text=True)
        require('RPATH' not in dynamic and 'RUNPATH' not in dynamic, f'GI extension contains runtime search path: {path}')
    if not fresh_runner:
        output.mkdir(parents=True)
    (output / '.launcher-build-incomplete').touch()
    if not fresh_runner:
        for directory in ('runtime', 'models', 'licenses'):
            copy_tree(runner / directory, output / directory)
        for directory in ('app', 'web'):
            copy_tree(root / directory, output / directory, source_code=True)
        for name in ('release.jsonc', 'LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md'):
            shutil.copy2(root / name, output / name)
        shutil.copy2(root / 'packaging/standalone/start.sh', output / 'start.sh')
        (output / 'start.sh').chmod(0o755)
    copy_tree(root / 'launcher', output / 'launcher', source_code=True)
    copy_tree(gui / 'vendor', output / 'launcher/vendor')
    shutil.copy2(root / 'packaging/launcher/launch.sh', output / 'launch.sh')
    (output / 'launch.sh').chmod(0o755)
    shutil.copy2(root / 'packaging/launcher/README.md', output / 'LAUNCHER-README.md')
    (output / 'launcher/runner-build-manifest.json').write_bytes(runner_bytes)
    (output / 'launcher/gui-build-manifest.json').write_bytes(gui_bytes)
    for name in ('inputs.json', 'requirements-runtime.lock', 'requirements-build.lock'):
        shutil.copy2(root / 'packaging/tray-prototype' / name, output / 'launcher' / name)
    def git(*arguments):
        return subprocess.check_output(['git', '-C', str(root), *arguments], text=True).rstrip('\n')
    inputs = list(INPUTS)
    inputs += [str(p.relative_to(root)) for folder in ('packaging/launcher', 'launcher')
               for p in sorted((root / folder).rglob('*')) if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc']
    inputs += ['packaging/tray-prototype/' + n for n in ('build.sh', 'inputs.json', 'requirements-runtime.lock', 'requirements-build.lock')]
    resources = [p for folder in ('app', 'web', 'licenses', 'launcher') for p in (output / folder).rglob('*') if p.is_file()]
    resources += [output / n for n in ('start.sh', 'launch.sh', 'LAUNCHER-README.md', 'release.jsonc', 'LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md')]
    manifest = dict(runner_manifest)
    manifest.update(built_at=datetime.now(timezone.utc).isoformat(), source_commit=git('rev-parse', 'HEAD'),
                    source_status=git('status', '--porcelain', '--untracked-files=normal').splitlines(),
                    source_diff_sha256=hashlib.sha256(subprocess.check_output(['git', '-C', str(root), 'diff', 'HEAD', '--binary'])).hexdigest(),
                    input_sha256={name: sha(root / name) for name in inputs},
                    resource_sha256={str(p.relative_to(output)): sha(p) for p in sorted(resources)})
    manifest['launcher'] = {
        'backend': 'pystray.appindicator', 'private_packages': gui_manifest['packages'],
        'native_wheels': gui_manifest['native_wheels'], 'system_gui_libraries': True,
        'runner_source_manifest_sha256': hashlib.sha256(runner_bytes).hexdigest(),
        'gui_source_manifest_sha256': hashlib.sha256(gui_bytes).hexdigest(),
        'verified_record_files': {'production': production_records, 'gui': gui_records},
        'verified_model_files': model_count,
        'gui_desktop_validation': 'Separate runtime acceptance; importing GUI is not visibility evidence',
    }
    (output / 'build-manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'assembled': str(output), 'production_packages': len(production),
                      'gui_packages': gui_packages, 'verified_model_files': model_count}, ensure_ascii=False))


if __name__ == '__main__':
    main()
