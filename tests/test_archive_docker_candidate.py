"""Freeze controls must reject identity changes and preserve complete image bytes."""
import gzip
import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('candidate_archive', ROOT / '.github/scripts/archive_docker_candidate.py')
ARCHIVE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ARCHIVE)


def test_split_gzip_roundtrip_and_independent_part_hashes(tmp_path):
    original = bytes(range(256)) * 300
    writer = ARCHIVE.SplitWriter(tmp_path, 97)
    with gzip.GzipFile(filename='', mode='wb', fileobj=writer, mtime=0) as out:
        out.write(original)
    writer.close_part()
    parts = [(tmp_path / p['name']).read_bytes() for p in writer.parts]
    assert len(parts) > 1
    assert gzip.decompress(b''.join(parts)) == original
    assert all(p['sha256'] == hashlib.sha256(data).hexdigest()
               and p['bytes'] == len(data) for p, data in zip(writer.parts, parts))
    assert all(len(data) == 97 for data in parts[:-1])
    assert writer.digest.hexdigest() == hashlib.sha256(b''.join(parts)).hexdigest()
    assert writer.total == sum(map(len, parts))


def test_archive_rejects_changed_source_labels_before_export(tmp_path, monkeypatch):
    monkeypatch.setattr(ARCHIVE, 'command', lambda *a: '[{"Config":{"Labels":{}},"Os":"linux","Architecture":"amd64"}]')
    with pytest.raises(ValueError, match='labels'):
        ARCHIVE.archive('candidate', tmp_path / 'out',
                        {'source_commit': 'a' * 40, 'version': '1.1.3', 'release_sha256': 'b' * 64})
    assert not (tmp_path / 'out').exists()


def test_archive_rejects_wrong_shipped_release_before_export(tmp_path, monkeypatch):
    import json
    identity = {'source_commit': 'a' * 40, 'version': '1.1.3', 'release_sha256': 'b' * 64}
    labels = {'org.opencontainers.image.revision': identity['source_commit'],
              'org.opencontainers.image.version': identity['version']}
    def run(args):
        if args[1:3] == ['image', 'inspect']:
            return json.dumps([{'Config': {'Labels': labels}, 'Os': 'linux', 'Architecture': 'amd64'}])
        return 'c' * 64
    monkeypatch.setattr(ARCHIVE, 'command', run)
    with pytest.raises(ValueError, match='Shipped release'):
        ARCHIVE.archive('candidate', tmp_path / 'out', identity)
    assert not (tmp_path / 'out').exists()


class StagingRegistry:
    def __init__(self, *, visibility='private', exists=True, revision='a' * 40):
        import json
        self.visibility = visibility
        self.exists = exists
        self.body = json.dumps({'os': 'linux', 'architecture': 'amd64', 'config': {'Labels': {
            'org.opencontainers.image.source': 'https://github.com/tech-kuiperbelt/remember-me',
            'org.opencontainers.image.version': '1.1.3',
            'org.opencontainers.image.revision': revision}}}).encode()
        self.image_id = 'sha256:' + hashlib.sha256(self.body).hexdigest()
        self.digest = 'sha256:' + 'e' * 64

    def package(self, repository):
        assert repository == 'tech-kuiperbelt/remember-me-buildcache'
        return {'visibility': self.visibility}

    def manifest(self, repository, ref, *, missing=False):
        assert repository == 'tech-kuiperbelt/remember-me-buildcache'
        assert ref in ('candidate-' + 'a' * 40, self.digest)
        if not self.exists:
            assert missing
            return None
        return {'digest': self.digest, 'manifest': {'config': {'digest': self.image_id}}}

    def resource(self, repository, path):
        assert path == 'blobs/' + self.image_id
        return {}, self.body


def staging_identity():
    return {'source_commit': 'a' * 40, 'version': '1.1.3', 'release_sha256': 'b' * 64}


@pytest.mark.parametrize('ref', ['ghcr.io/tech-kuiperbelt/remember-me:1.1.3',
                               'ghcr.io/tech-kuiperbelt/remember-me-buildcache:amd64',
                               'ghcr.io/tech-kuiperbelt/remember-me-buildcache:latest'])
def test_staging_ref_cannot_select_public_release_latest_or_cache_tag(ref):
    with pytest.raises(ValueError, match='restricted'):
        ARCHIVE.stage_candidate('image', 'sha256:' + 'c' * 64, staging_identity(), ref)


def test_staging_ref_requires_existing_private_package():
    registry = StagingRegistry(visibility='public')
    with pytest.raises(ValueError, match='private'):
        ARCHIVE.stage_candidate('image', registry.image_id, staging_identity(),
            'ghcr.io/tech-kuiperbelt/remember-me-buildcache:candidate-' + 'a' * 40,
            registry=registry, run=lambda args: pytest.fail('must not run Docker'))


def test_staging_reuses_same_immutable_identity_without_push():
    registry = StagingRegistry()
    result = ARCHIVE.stage_candidate('image', registry.image_id, staging_identity(),
        'ghcr.io/tech-kuiperbelt/remember-me-buildcache:candidate-' + 'a' * 40,
        registry=registry, run=lambda args: pytest.fail('must not push an existing ref'))
    assert result['staging_digest'] == registry.digest
    assert result['staging_image_id'] == registry.image_id
    assert result['staging_visibility'] == 'private'


def test_staging_conflicting_identity_is_never_overwritten():
    registry = StagingRegistry(revision='c' * 40)
    with pytest.raises(RuntimeError, match='commit'):
        ARCHIVE.stage_candidate('image', registry.image_id, staging_identity(),
            'ghcr.io/tech-kuiperbelt/remember-me-buildcache:candidate-' + 'a' * 40,
            registry=registry, run=lambda args: pytest.fail('must not overwrite'))


def test_staging_pushes_only_absent_commit_ref_then_verifies_digest():
    registry = StagingRegistry(exists=False)
    commands = []
    def run(args):
        commands.append(args)
        if args[1] == 'push':
            registry.exists = True
    ref = 'ghcr.io/tech-kuiperbelt/remember-me-buildcache:candidate-' + 'a' * 40
    result = ARCHIVE.stage_candidate('image', registry.image_id, staging_identity(), ref,
                                     registry=registry, run=run)
    assert commands == [['docker', 'tag', 'image', ref], ['docker', 'push', ref]]
    assert result['staging_digest'] == registry.digest
