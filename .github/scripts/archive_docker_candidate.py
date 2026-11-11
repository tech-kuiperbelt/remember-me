#!/usr/bin/env python3
"""Retain a tested Docker candidate; optionally stage it in the private cache package.

The CI export option retains the exact tested runtime image. Only after all CI
checks succeed is docker-save streamed through deterministic gzip into
1 GiB files suitable for long-term Release attachment storage.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.config import jsonc
from app.domain.releases import validate_release


def command(args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def source_identity():
    commit = command(['git', 'rev-parse', 'HEAD'])
    if os.environ.get('GITHUB_SHA', commit) != commit:
        raise ValueError('Workflow SHA does not match the build checkout')
    subprocess.run(['git', 'diff', '--exit-code', 'HEAD'], cwd=ROOT, check=True)
    release_bytes = (ROOT / 'release.jsonc').read_bytes()
    committed = subprocess.check_output(['git', 'show', commit + ':release.jsonc'], cwd=ROOT)
    if release_bytes != committed:
        raise ValueError('release.jsonc differs from the committed file')
    release = validate_release(jsonc.loads(release_bytes.decode()))
    if not re.fullmatch(r'\d+\.\d+\.\d+', release.version or ''):
        raise ValueError('A numeric release version is required')
    return {'source_commit': commit, 'version': release.version,
            'release_sha256': hashlib.sha256(release_bytes).hexdigest()}



def staging_info(registry, repository, ref, *, missing=False):
    record = registry.manifest(repository, ref, missing=missing)
    if record is None:
        return None
    config_digest = record['manifest']['config']['digest']
    _, body = registry.resource(repository, 'blobs/' + config_digest)
    if 'sha256:' + hashlib.sha256(body).hexdigest() != config_digest:
        raise ValueError('Staging config bytes do not match their digest')
    config = json.loads(body)
    if config.get('os') != 'linux' or config.get('architecture') != 'amd64':
        raise ValueError('Staging image is not Linux AMD64')
    return {**record, 'image_id': config_digest,
            'labels': config.get('config', {}).get('Labels', {})}


def stage_candidate(image, image_id, identity, ref, *, registry=None, run=command):
    from docker_release import CACHE, Registry, identity as verify_identity
    expected_ref = f'ghcr.io/{CACHE}:candidate-{identity["source_commit"]}'
    if ref != expected_ref:
        raise ValueError('Staging is restricted to a commit-specific private-cache tag')
    if registry is None:
        token = os.environ.get('GITHUB_TOKEN', '')
        if not token:
            raise ValueError('GITHUB_TOKEN is required for private staging')
        registry = Registry(token, os.environ.get('GITHUB_ACTOR', ''))
    def private_gate():
        package = registry.package(CACHE)
        if package is None or package.get('visibility') != 'private':
            raise ValueError('Staging package must already exist and remain private')
    private_gate()
    tag = ref.rsplit(':', 1)[1]
    existing = staging_info(registry, CACHE, tag, missing=True)
    if existing is None:
        private_gate()
        # Recheck immediately before push. An unexpected existing identity is
        # always an error; this command never replaces a conflicting manifest.
        existing = staging_info(registry, CACHE, tag, missing=True)
        if existing is None:
            run(['docker', 'tag', image, ref])
            run(['docker', 'push', ref])
    observed = staging_info(registry, CACHE, tag)
    verify_identity(observed, identity['version'], identity['source_commit'], image_id=image_id)
    private_gate()
    immutable = staging_info(registry, CACHE, observed['digest'])
    verify_identity(immutable, identity['version'], identity['source_commit'],
                    expected_digest=observed['digest'], image_id=image_id)
    return {'staging_ref': ref, 'staging_digest': observed['digest'],
            'staging_image_id': immutable['image_id'], 'staging_visibility': 'private'}


class SplitWriter:
    def __init__(self, output, part_bytes):
        self.output = output
        self.part_bytes = part_bytes
        self.parts = []
        self.current = None
        self.part_hash = None
        self.part_size = 0
        self.total = 0
        self.digest = hashlib.sha256()

    def write(self, data):
        original = len(data)
        self.digest.update(data)
        self.total += original
        while data:
            if self.current is None:
                self.path = self.output / f'docker-amd64.tar.gz.part{len(self.parts):03d}'
                self.current = self.path.open('xb')
                self.part_hash = hashlib.sha256()
                self.part_size = 0
            amount = min(len(data), self.part_bytes - self.part_size)
            block, data = data[:amount], data[amount:]
            self.current.write(block)
            self.part_hash.update(block)
            self.part_size += amount
            if self.part_size == self.part_bytes:
                self.close_part()
        return original

    def close_part(self):
        if self.current is not None:
            self.current.close()
            self.parts.append({'name': self.path.name, 'bytes': self.part_size,
                               'sha256': self.part_hash.hexdigest()})
            self.current = None

    def flush(self):
        if self.current is not None:
            self.current.flush()


def archive(image, output, identity, part_bytes=1024**3, staging_ref=None):
    info = json.loads(command(['docker', 'image', 'inspect', image]))[0]
    labels = info['Config'].get('Labels') or {}
    expected = {'org.opencontainers.image.revision': identity['source_commit'],
                'org.opencontainers.image.version': identity['version']}
    if any(labels.get(k) != v for k, v in expected.items()):
        raise ValueError('Candidate labels do not match the frozen source')
    if info['Os'] != 'linux' or info['Architecture'] != 'amd64':
        raise ValueError('Candidate is not Linux AMD64')
    shipped = command(['docker', 'run', '--rm', '--network', 'none', '--read-only',
                       '--entrypoint', '/opt/venv/bin/python', image, '-c',
                       'import hashlib;print(hashlib.sha256(open("/opt/remember-me/release.jsonc","rb").read()).hexdigest())'])
    if shipped != identity['release_sha256']:
        raise ValueError('Shipped release.jsonc hash differs from frozen source')
    output.mkdir(parents=True, exist_ok=False)
    split = SplitWriter(output, part_bytes)
    with (output / 'docker-save.log').open('wb') as error:
        process = subprocess.Popen(['docker', 'save', image], stdout=subprocess.PIPE, stderr=error)
        try:
            with gzip.GzipFile(filename='', mode='wb', fileobj=split, mtime=0, compresslevel=1) as compressed:
                shutil.copyfileobj(process.stdout, compressed, 8 * 1024 * 1024)
            split.close_part()
            if process.wait() != 0:
                raise RuntimeError('docker save failed; archive is incomplete')
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()
            split.close_part()
    manifest = {**identity, 'image': image, 'image_id': info['Id'],
                'image_bytes': info['Size'], 'platform': 'linux/amd64',
                'ci_run': f'https://github.com/{os.environ.get("GITHUB_REPOSITORY", "tech-kuiperbelt/remember-me")}/actions/runs/{os.environ.get("GITHUB_RUN_ID", "local")}',
                'archive': {'format': 'docker-save+gzip', 'bytes': split.total,
                            'sha256': split.digest.hexdigest(), 'parts': split.parts}}
    metadata = ROOT / 'artifacts/cold-metadata.json'
    if metadata.is_file():
        manifest['buildx'] = json.loads(metadata.read_text())
    if staging_ref:
        manifest.update(stage_candidate(image, info['Id'], identity, staging_ref))
    (output / 'frozen-image.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (output / 'SHA256SUMS').write_text(''.join(f'{p["sha256"]}  {p["name"]}\n' for p in split.parts))
    print(json.dumps(manifest, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default=os.environ.get('IMAGE', ''))
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/frozen-image')
    parser.add_argument('--staging-ref', help='Commit-specific tag in the existing private buildcache package only')
    args = parser.parse_args()
    identity = source_identity()
    image = args.image
    if not image:
        parser.error('--image is required')
    archive(image, args.output, identity, staging_ref=args.staging_ref)


if __name__ == '__main__':
    main()
