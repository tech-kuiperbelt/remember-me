"""Build a complete relocatable ARM64 app and DMG on macOS 15+.

Native dependency resolution is recorded as a candidate lock, installed with
hashes from an offline wheelhouse. This does not publish or change release.jsonc.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import sys
from urllib.parse import urlparse
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
PBS = 'cpython-3.12.15+20261020-aarch64-apple-darwin-install_only.tar.gz'
PBS_URL = 'https://github.com/astral-sh/python-build-standalone/releases/download/20261020/' + PBS
PBS_SHA = '921fc822416bbad146b026d2051dbfe08338ce4144ced64c56afd899a4351752'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def fetch(url, path, sha):
    with urlopen(url, timeout=120) as source, path.open('wb') as output:
        shutil.copyfileobj(source, output, 8 * 1024 * 1024)
    if digest(path) != sha:
        raise ValueError(f'SHA256 mismatch: {path}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', required=True, type=Path)
    args = parser.parse_args()
    if sys.platform != 'darwin' or platform.machine() != 'arm64' or int(platform.mac_ver()[0].split('.')[0]) < 15:
        raise RuntimeError('Build requires native macOS 15+ ARM64')
    work = args.work.resolve()
    work.mkdir()  # Never overwrite a previous build.
    evidence = work / 'evidence'
    evidence.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(
        ('PYTHON', 'PIP_', 'MEMORY_', 'EMBED_', 'HF_', 'HUGGINGFACE_', 'TRANSFORMERS_',
         'SENTENCE_TRANSFORMERS_', 'FASTEMBED_', 'TORCH_', 'DYLD_'))}
    env.update(PIP_CONFIG_FILE='/dev/null', PIP_DISABLE_PIP_VERSION_CHECK='1',
               PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
    def run(*command, **kwargs):
        print('Running:', *map(str, command), flush=True)
        return subprocess.run(list(map(str, command)), env=env, cwd=work, check=True, **kwargs)
    stage = work / 'stage'
    resources = stage / 'Remember Me.app/Contents/Resources'
    resources.mkdir(parents=True)
    runtime = resources / 'runtime'
    runtime.mkdir()
    archive = work / PBS
    fetch(PBS_URL, archive, PBS_SHA)
    run('/usr/bin/tar', '-xzf', archive, '-C', runtime)
    py = runtime / 'python/bin/python3.12'
    run(py, '-I', '-B', '-c', "import platform,sys;assert sys.version_info[:3]==(3,12,15);assert platform.machine()=='arm64'")
    run(py, '-I', '-m', 'pip', 'install', '--no-compile', '--no-cache-dir', '--require-hashes',
        '--only-binary=:all:', '-r', ROOT / 'docker/pip-bootstrap.lock')
    baseline = (ROOT / 'requirements/docker-cpu-py312-linux-amd64.lock').read_text()
    pins = dict(re.findall(r'^([\w-]+)==([^\s\\]+)', baseline, re.M))
    # macOS PyPI wheels have CPU/MPS support; launch always explicitly selects CPU.
    pins['torch'] = '2.14.1'
    constraints = evidence / 'production-pins.txt'
    constraints.write_text(''.join(f'{k}=={v}\n' for k, v in sorted(pins.items())))
    report = evidence / 'resolve.json'
    run(py, '-I', '-m', 'pip', 'install', '--dry-run', '--ignore-installed', '--no-cache-dir',
        '--only-binary=:all:', '--index-url', 'https://pypi.org/simple', '--report', report, '-r', constraints)
    entries = json.loads(report.read_text())['install']
    lock = evidence / 'macos-arm64-candidate.lock'
    offline = evidence / 'macos-arm64-offline.lock'
    direct, local = [], []
    for entry in entries:
        url = entry['download_info']['url']
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname != 'files.pythonhosted.org' or not parsed.path.endswith('.whl'):
            raise ValueError(f'Unexpected wheel source: {url}')
        sha = entry['download_info']['archive_info']['hashes']['sha256']
        if not re.fullmatch('[a-f0-9]{64}', sha):
            raise ValueError('Invalid wheel digest')
        name, version = entry['metadata']['name'], entry['metadata']['version']
        direct.append(f'{name} @ {url} --hash=sha256:{sha}\n')
        local.append(f'{name}=={version} --hash=sha256:{sha}\n')
    lock.write_text('# Native candidate; not an approved production lock.\n' + ''.join(direct))
    offline.write_text(''.join(local))
    wheels = work / 'wheels'
    run(py, '-I', '-m', 'pip', 'download', '--no-cache-dir', '--require-hashes', '--only-binary=:all:',
        '--dest', wheels, '-r', lock)
    run(py, '-I', '-m', 'pip', 'install', '--no-cache-dir', '--no-compile', '--no-index',
        '--find-links', wheels, '--require-hashes', '--only-binary=:all:', '-r', offline)
    with (evidence / 'pip-check.txt').open('w') as output:
        run(py, '-I', '-m', 'pip', 'check', stdout=output, stderr=subprocess.STDOUT)
    for name in ('app', 'web'):
        shutil.copytree(ROOT / name, resources / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('release.jsonc', 'LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT / name, resources / name)
    shutil.copy2(ROOT / 'packaging/macos/bootstrap.py', resources / 'bootstrap.py')
    licenses = resources / 'licenses/runtime'
    licenses.mkdir(parents=True)
    baseline_env = dict(re.findall(r'^(\w+)=(.*)$', (ROOT / 'packaging/standalone/runtime.env').read_text(), re.M))
    for key in ('PYTHON_LICENSE', 'OPENSSL_LICENSE'):
        fetch(baseline_env[key + '_URL'], licenses / baseline_env[key + '_FILE'], baseline_env[key + '_SHA256'])
    run(py, '-I', '-B', ROOT / 'docker/package_model.py', '--download-cache', work / 'model-cache',
        '--output', resources / 'models')
    contents = resources.parent
    macos = contents / 'MacOS'
    macos.mkdir()
    run('/usr/bin/xcrun', 'swiftc', '-O', '-target', 'arm64-apple-macos15.0',
        ROOT / 'packaging/macos/Launcher.swift', '-o', macos / 'remember-me')
    # Explicit deployment target; no independent release version is entered here.
    sys.path.insert(0, str(ROOT))
    from app.release_info import read_local_release
    release = read_local_release(ROOT)
    if release.error_code:
        raise ValueError(release.error_code)
    version = release.version
    info = {'CFBundleExecutable': 'remember-me', 'CFBundleIdentifier': 'org.remember-me.offline-beta',
            'CFBundleName': 'Remember Me', 'CFBundleDisplayName': 'Remember Me',
            'CFBundlePackageType': 'APPL', 'LSMinimumSystemVersion': '15.0',
            'NSHighResolutionCapable': True}
    if version:
        info.update(CFBundleShortVersionString=version, CFBundleVersion=version)
    (contents / 'Info.plist').write_bytes(plistlib.dumps(info))
    # Audit Mach-O install names. No Homebrew, runner or build-tree libraries may ship.
    with (evidence / 'macho-libraries.txt').open('w') as output:
        for path in sorted(contents.rglob('*')):
            if path.is_symlink() or not path.is_file():
                continue
            with path.open('rb') as source:
                magic = source.read(4)
            if magic not in (b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'):
                continue
            headers = subprocess.check_output(['/usr/bin/lipo', '-archs', str(path)], text=True)
            if 'arm64' not in headers.split():
                raise ValueError(f'Non-ARM64 Mach-O: {path}')
            listing = subprocess.check_output(['/usr/bin/otool', '-L', str(path)], text=True)
            output.write(listing + '\n')
            for line in listing.splitlines()[1:]:
                reference = line.strip().split(' (')[0]
                if reference.startswith('/') and not reference.startswith(('/usr/lib/', '/System/Library/')):
                    raise ValueError(f'External dylib dependency: {path}: {reference}')
    manifest = {'source_commit': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
                'platform': 'macos-arm64', 'minimum_macos': '15.0', 'python_url': PBS_URL, 'python_sha256': PBS_SHA,
                'release_version': version, 'dependency_lock': 'native candidate; requires review before promotion',
                'wheel_sha256': {p.name: digest(p) for p in sorted(wheels.iterdir())},
                'model': json.loads((ROOT / 'docker/bge-m3-manifest.json').read_text())['revision']}
    (evidence / 'build.json').write_text(json.dumps(manifest, indent=2) + '\n')
    shutil.copy2(evidence / 'build.json', resources / 'build.json')
    # ARM64 requires ad-hoc Mach-O identity; this is neither Developer ID signing nor notarization.
    run('/usr/bin/codesign', '--force', '--deep', '--sign', '-', stage / 'Remember Me.app')
    run('/usr/bin/codesign', '--verify', '--deep', '--strict', stage / 'Remember Me.app')
    (stage / 'Applications').symlink_to('/Applications')
    (stage / 'READ ME.txt').write_text('Remember Me — macOS ARM64 offline Beta\n'
        'Copy Remember Me.app to Applications, eject this disk, then open the app.\n'
        'Unsigned Beta: Developer ID and Apple notarization are not included.\n'
        'Quit Remember Me (Cmd-Q) or use Configuration / Maintenance / Shut Down Server.\n'
        'Data: ~/Library/Application Support/remember-me/ (keep this directory on upgrades).\n')
    output = work / 'output'
    output.mkdir()
    dmg = output / 'remember-me-macos-arm64-beta.dmg'
    run('/usr/bin/hdiutil', 'create', '-volname', 'Remember Me Beta', '-srcfolder', stage,
        '-format', 'UDZO', '-ov', dmg)
    (output / (dmg.name + '.sha256')).write_text(f'{digest(dmg)}  {dmg.name}\n')
    (evidence / 'dmg.json').write_text(json.dumps({'name': dmg.name, 'bytes': dmg.stat().st_size,
                                                 'sha256': digest(dmg)}, indent=2) + '\n')


if __name__ == '__main__':
    main()
