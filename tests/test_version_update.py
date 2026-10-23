"""Release targets, structured files, bounded history and safe failure states."""
import asyncio
import json
import socket
import ssl

import httpx
import pytest

from app.operations import version_update as module
from app.operations.version_update import API_URL, RAW_URL, CheckRejected
from tests.version_update_helpers import Github, Clock, check, local, meta, release


@pytest.mark.parametrize("current,status,scope", [
    (None, "UPDATE_RECOMMENDED", "latest_only"),
    ("1.2.0", "UP_TO_DATE", "latest_only"),
    ("2.0.0", "UP_TO_DATE", "latest_only"),
    ("1.0.0", "UPDATE_AVAILABLE", "upgrade_range"),
    ("1.10.0", "UP_TO_DATE", "latest_only"),
])
def test_compare_and_unknown(current, status, scope):
    async def run():
        gh = Github()
        manager = gh.manager(local(current))
        try:
            result = await check(manager)
            assert result["check"]["status"] == status
            assert result["notes_scope"] == scope
            assert result["latest"]["release"] == release()
            assert result["current"]["version"] == current
            if current is None:
                assert result["notes_complete"] is None and result["releases"] == []
                assert len(gh.seen) == 2  # No null comparison or invented range/list.
            result["latest"]["release"]["new"].append("mutated response")
            assert manager.snapshot()["latest"]["release"]["new"] == ["new capability"]
        finally:
            await manager.aclose()
    asyncio.run(run())


def test_no_release_needs_successful_list_and_ignores_prerelease():
    async def run():
        gh = Github()
        gh.overrides[API_URL + "/latest"] = httpx.Response(404)
        gh.pages[1] = [meta("2.0.0-rc.1", prerelease=True), meta(draft=True)]
        manager = gh.manager()
        try:
            result = await check(manager)
            assert result["check"]["status"] == "NO_RELEASE"
            assert result["latest"] is None and result["check"]["last_success_at"]
            assert not any(url.startswith(RAW_URL) for url in gh.seen)
            gh.pages[1] = [meta("9.0.0")]
            result = await check(manager)
            assert result["check"]["status"] == "UNAVAILABLE"
            assert result["latest"] is None  # List maximum cannot replace Latest.
            gh.overrides[API_URL + "?per_page=100&page=1"] = httpx.Response(500)
            assert (await check(manager))["check"]["status"] == "UNAVAILABLE"
        finally:
            await manager.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("response,code", [
    (httpx.Response(404), "latest_release_missing"),
    (httpx.Response(200, content="not json"), "latest_release_invalid"),
    (httpx.Response(200, json=release("1.0.0")), "latest_release_version_mismatch"),
    (httpx.Response(200, json={"version": "1.2.0"}), "latest_release_invalid"),
    (httpx.Response(200, content=b'\xff'), "latest_release_invalid"),
    (httpx.Response(200, content='{"version":"1.2.0","version":"1.2.0"}'), "latest_release_invalid"),
    (httpx.Response(200, content=b"x" * (256 * 1024 + 1)), "release_file_too_large"),
    (httpx.Response(302, headers={"location": "https://evil.invalid/"}), "release_file_invalid_response"),
])
def test_latest_file_failure_clears_previous_success(response, code):
    async def run():
        gh = Github()
        manager = gh.manager()
        try:
            success = await check(manager)
            gh.overrides[RAW_URL + "/v1.2.0/release.jsonc"] = response
            result = await check(manager)
            assert result["check"]["status"] == "UNAVAILABLE"
            assert result["check"]["error_code"] == code
            assert result["latest"] is None and result["releases"] == []
            assert result["notes_scope"] == "none" and result["notes_complete"] is None
            assert result["check"]["last_success_at"] == success["check"]["last_success_at"]
            assert manager._last_success["latest"] is not None
            assert len(gh.seen) == 4  # Both rounds really request Latest and raw.
        finally:
            await manager.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("change", [
    {"draft": True}, {"prerelease": True}, {"tag_name": "v01.2.0"},
    {"tag_name": "v1.2.0/../../evil"}, {"tag_name": "1.2.0"},
    {"tag_name": "v1.2.0+build"}, {"published_at": "not a date"},
])
def test_invalid_latest_metadata(change):
    async def run():
        gh = Github()
        gh.overrides[API_URL + "/latest"] = httpx.Response(200, json=meta(**change))
        manager = gh.manager()
        try:
            result = await check(manager)
            assert result["check"]["status"] == "UNAVAILABLE"
            assert result["check"]["error_code"] == "latest_release_invalid"
            assert len(gh.seen) == 1
        finally:
            await manager.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("host", ["api", "raw"])
@pytest.mark.parametrize("kind,code", [("dns", "unreachable"), ("timeout", "timeout"),
                                      ("tls", "tls_error"), ("server", "service_error"),
                                      ("forbidden", "forbidden")])
def test_network_errors_safe(host, kind, code):
    async def run():
        gh = Github()
        if kind == "dns":
            error = httpx.ConnectError("private proxy user:password")
            error.__cause__ = socket.gaierror("DNS failed")
        elif kind == "timeout":
            error = httpx.ReadTimeout("secret timeout path")
        elif kind == "tls":
            error = httpx.ConnectError("sensitive TLS details")
            error.__cause__ = ssl.SSLCertVerificationError("certificate failure")
        else:
            error = httpx.Response(503 if kind == "server" else 403)
        gh.overrides[API_URL + "/latest" if host == "api" else RAW_URL + "/v1.2.0/release.jsonc"] = error
        manager = gh.manager()
        try:
            result = await check(manager)
            assert result["check"]["status"] == "UNAVAILABLE"
            prefix = "github" if host == "api" else "release_file"
            assert result["check"]["error_code"] == prefix + "_" + code
            assert "private" not in json.dumps(result) and "sensitive" not in json.dumps(result)
        finally:
            await manager.aclose()
    asyncio.run(run())


def test_history_sorted_deduplicated_latest_only_target():
    async def run():
        gh = Github("1.3.0")
        gh.pages[1] = [meta("1.0.0"), meta("1.2.0"), meta("9.0.0"), meta("1.1.0"),
                       meta("1.2.0"), meta("1.3.0"), meta("1.4.0-rc.1", prerelease=True)]
        gh.raw["1.1.0"] = release("1.1.0", breaking=["Do not omit this intermediate change"])
        manager = gh.manager(local("1.0.0"))
        try:
            result = await check(manager)
            assert result["latest"]["version"] == "1.3.0"
            assert [row["version"] for row in result["releases"]] == ["1.3.0", "1.2.0", "1.1.0"]
            assert result["notes_complete"] is True
            assert result["releases"][-1]["release"]["breaking"]
            assert len([url for url in gh.seen if url.endswith("/v1.3.0/release.jsonc")]) == 1
            assert not any("v9.0.0/release.jsonc" in url for url in gh.seen)
        finally:
            await manager.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("response,code", [
    (httpx.Response(404), "release_file_not_found"),
    (httpx.Response(200, json=release("1.0.0")), "release_version_mismatch"),
    (httpx.Response(200, json={}), "release_file_invalid"),
    (httpx.ReadTimeout("secret"), "release_file_timeout"),
])
def test_history_failure_retains_latest_and_links(response, code):
    async def run():
        gh = Github()
        gh.pages[1] = [meta("1.1.0"), meta()]
        gh.overrides[RAW_URL + "/v1.1.0/release.jsonc"] = response
        manager = gh.manager(local("1.0.0"))
        try:
            result = await check(manager)
            assert result["check"]["status"] == "UPDATE_AVAILABLE"
            assert result["check"]["error_code"] is None
            assert result["latest"]["release"] == release()
            assert result["notes_complete"] is False
            assert result["release_errors"] == [{"tag": "v1.1.0", "code": code,
                "url": "https://github.com/tech-kuiperbelt/remember-me/releases/tag/v1.1.0"}]
        finally:
            await manager.aclose()
    asyncio.run(run())


def test_history_pagination_does_not_stop_on_old_version():
    async def run():
        gh = Github()
        first = API_URL + "?per_page=100&page=1"
        gh.overrides[first] = httpx.Response(200, json=[meta("0.1.0")], headers={
            "Link": '<' + API_URL + '?per_page=100&page=2>; rel="next"'})
        gh.pages[2] = [meta("1.1.0")]
        manager = gh.manager(local("1.0.0"))
        try:
            result = await check(manager)
            assert result["notes_complete"]
            assert len(result["releases"]) == 2
            gh.overrides[API_URL + "?per_page=100&page=2"] = httpx.Response(500)
            result = await check(manager)
            assert result["latest"] and result["notes_complete"] is False
            assert result["notes_error_code"] == "release_list_failed"
            gh.overrides[first] = httpx.Response(200, json=[], headers={
                "Link": '<https://evil.invalid/?page=2>; rel="next"'})
            result = await check(manager)
            assert result["notes_complete"] is False
            assert all("evil.invalid" not in url for url in gh.seen)
        finally:
            await manager.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("limit", ["pages", "files", "bytes"])
def test_limits_report_incomplete_without_losing_target(monkeypatch, limit):
    async def run():
        gh = Github()
        gh.pages[1] = [meta("1.1.0"), meta("1.0.1")]
        if limit == "pages":
            monkeypatch.setattr(module, "MAX_PAGES", 1)
            gh.overrides[API_URL + "?per_page=100&page=1"] = httpx.Response(200,
                json=gh.pages[1], headers={"Link": '<' + API_URL + '?per_page=100&page=2>; rel="next"'})
        elif limit == "files":
            monkeypatch.setattr(module, "MAX_RELEASES", 1)
        else:
            monkeypatch.setattr(module, "MAX_RAW_BYTES", len(json.dumps(release()).encode()) + 15)
        manager = gh.manager(local("1.0.0"))
        try:
            result = await check(manager)
            assert result["check"]["status"] == "UPDATE_AVAILABLE"
            assert result["latest"] and result["notes_complete"] is False
            assert result["notes_error_code"] == "release_limit_exceeded"
        finally:
            await manager.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("headers", [
    {"Retry-After": "120"},
    {"Retry-After": "172800000"},  # Do not silently shorten a long server wait.
    {"Retry-After": "Fri, 23 Oct 2026 00:02:00 GMT"},
    {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1792713720"},
    {"Retry-After": "invalid"},
])
def test_rate_limit_wait_and_manual_recovery(headers):
    async def run():
        gh, clock = Github(), Clock()
        gh.overrides[API_URL + "/latest"] = httpx.Response(429, headers=headers)
        manager = gh.manager(**clock.options())
        try:
            result = await check(manager)
            assert result["check"]["status"] == "UNAVAILABLE"
            assert result["check"]["error_code"] == "github_rate_limited"
            assert result["check"]["next_allowed_at"]
            before = len(gh.seen)
            with pytest.raises(CheckRejected) as exc:
                manager.request_check()
            assert exc.value.status == 429 and len(gh.seen) == before
            await clock.advance(manager.retry_after() + 1)
            gh.overrides.clear()
            assert (await check(manager))["check"]["status"] == "UPDATE_RECOMMENDED"
        finally:
            await manager.aclose()
    asyncio.run(run())


def test_history_rate_limit_keeps_target_and_stops_new_requests():
    async def run():
        gh, clock = Github(), Clock()
        gh.pages[1] = [meta("1.1.0"), meta("1.0.1")]
        gh.overrides[RAW_URL + '/v1.1.0/release.jsonc'] = httpx.Response(429, headers={'Retry-After':'120'})
        manager = gh.manager(local('1.0.0'), **clock.options())
        try:
            result = await check(manager)
            assert result['check']['status'] == 'UPDATE_AVAILABLE'
            assert result['check']['error_code'] is None and result['latest']
            assert not result['notes_complete'] and result['check']['next_allowed_at']
            assert not any('/v1.0.1/' in url for url in gh.seen)
            with pytest.raises(CheckRejected) as exc:
                manager.request_check()
            assert exc.value.status == 429
        finally:
            await manager.aclose()
    asyncio.run(run())


def test_api_markdown_is_ignored_and_structured_text_is_not_executed():
    async def run():
        gh = Github()
        gh.overrides[API_URL+'/latest'] = httpx.Response(200, json=meta(
            body='this legacy body must not reach clients', html_url='https://evil.invalid/'))
        gh.raw['1.2.0'] = release(new=['<script>unsafe()</script>', '[bad](javascript:unsafe)'])
        manager = gh.manager()
        try:
            result = await check(manager)
            assert 'legacy body' not in json.dumps(result) and 'evil.invalid' not in json.dumps(result)
            assert result['latest']['release']['new'][0] == '<script>unsafe()</script>'
        finally:
            await manager.aclose()
    asyncio.run(run())


def test_canonical_pagination_hint_never_changes_repository():
    async def run():
        gh = Github()
        gh.overrides[API_URL+'?per_page=100&page=1'] = httpx.Response(200, json=[meta('1.1.0')],
            headers={'Link':'<https://api.github.com/repositories/123/releases?page=2&per_page=100>; rel="next"'})
        manager = gh.manager(local('1.0.0'))
        try:
            result = await check(manager)
            assert result['notes_complete']
            assert any('page=2' in url for url in gh.seen)
            assert not any('/repositories/' in url for url in gh.seen)
        finally:
            await manager.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('failure',['link','date','decode','unexpected'])
def test_malformed_history_never_overwrites_valid_target(monkeypatch,failure):
    async def run():
        gh=Github()
        gh.pages[1]=[meta('1.1.0')]
        if failure=='link':
            gh.overrides[API_URL+'?per_page=100&page=1']=httpx.Response(200,json=gh.pages[1],
                headers={'Link':'<https://[broken/releases?per_page=100&page=2>; rel="next"'})
        elif failure=='date':
            gh.pages[1]=[meta('1.1.0',published_at='0001-01-01T00:00:00+14:00')]
        elif failure=='decode':
            gh.overrides[RAW_URL+'/v1.1.0/release.jsonc']=httpx.DecodingError('private compression error')
        else:
            async def broken(*args):
                raise OSError('private adapter failure')
            monkeypatch.setattr(module.GitHubReleases,'history',broken)
        manager=gh.manager(local('1.0.0'))
        try:
            result=await check(manager)
            assert result['check']['status']=='UPDATE_AVAILABLE'
            assert result['check']['error_code'] is None
            assert result['latest']['release']==release() and result['notes_complete'] is False
            assert 'private' not in json.dumps(result)
        finally:
            await manager.aclose()
    asyncio.run(run())
