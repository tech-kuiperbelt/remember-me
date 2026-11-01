"""Offline coverage for numeric beta publication and release selection."""
import asyncio
import subprocess
from types import SimpleNamespace

import pytest

from tests.test_docker_release import R, arguments, registry, workflow
from tests.version_update_helpers import Github, check, local, meta


@pytest.mark.parametrize('candidate', ['1.1.0', '1.3.2'])
def test_beta_never_writes_latest_even_after_verification(tmp_path, registry, workflow, monkeypatch, candidate):
    old = registry.add('1.0.1', '1.0.1', 'a' * 40)
    registry.refs[(R.REPOSITORY, 'latest')] = registry.refs[(R.REPOSITORY, '1.0.1')]
    info = registry.add(candidate, candidate, 'a' * 40)
    args = arguments(tmp_path, tag='v' + candidate, existing_digest=info['digest'],
                     verified_digest=info['digest'], image_id=info['image_id'])
    monkeypatch.setattr(R, 'resolve_source', lambda *a: {})
    monkeypatch.setattr(R, 'release_at_commit', lambda *a: (SimpleNamespace(version=candidate), args.release_sha256))
    monkeypatch.setattr(R, 'release_gate', lambda *a, **kw: None)
    calls = []
    monkeypatch.setattr(R, 'command', lambda *a, **kw: calls.append(a))
    for attempt in range(2):  # Same path used for publish and resume.
        journal = R.Journal(tmp_path / str(attempt), {})
        R.promote(args, registry.client, journal)
        assert journal.data['latest'] == 'unchanged (beta)'
        assert registry.client.inspect('latest')['digest'] == old['digest']
    assert not calls


@pytest.mark.parametrize('versions,current,expected,channel,status', [
    ([meta('1.0.1'), meta('1.1.0', prerelease=True)], '1.0.1', '1.1.0', 'beta', 'UPDATE_AVAILABLE'),
    ([meta('1.1.0', prerelease=True), meta('1.2.0')], '1.0.1', '1.2.0', 'stable', 'UPDATE_AVAILABLE'),
    ([meta('1.1.0')], '1.0.1', '1.1.0', 'beta', 'UPDATE_AVAILABLE'),
    ([meta('1.2.0', prerelease=True)], '1.0.1', '1.2.0', 'beta', 'UPDATE_AVAILABLE'),
    ([meta('1.2.0'), meta('9.0.0', draft=True)], '1.2.0', '1.2.0', 'stable', 'UP_TO_DATE'),
    ([meta('1.2.0')], '1.4.0', '1.2.0', 'stable', 'UP_TO_DATE'),
])
def test_version_selection(versions, current, expected, channel, status):
    async def run():
        gh = Github()
        gh.pages[1] = versions
        manager = gh.manager(local(current))
        try:
            result = await check(manager)
            assert result['check']['status'] == status
            assert result['latest']['version'] == expected
            assert result['latest']['channel'] == channel
            assert not any(url.endswith('/latest') for url in gh.seen)
        finally:
            await manager.aclose()
    asyncio.run(run())
