"""Only local, deterministic GitHub/raw fixtures; never contact real GitHub."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx

from app.domain.releases import validate_release
from app.release_info import LocalRelease
from app.operations.version_update import API_URL, RAW_URL, VersionUpdateManager


def release(version="1.2.0", **changes):
    return {"version": version, "new": ["new capability"], "improvements": [],
            "fixes": [], "upgrade": ["Back up before upgrading"], "breaking": [], **changes}


def meta(version="1.2.0", **changes):
    return {"tag_name": "v" + version, "draft": False, "prerelease": False,
            "published_at": "2026-10-01T00:00:00Z", **changes}


def local(version=None, method="SOURCE"):
    return LocalRelease(method, release=validate_release(release(version)) if version else None,
                        error_code=None if version else "local_release_invalid")


class Github:
    def __init__(self, version="1.2.0"):
        self.version = version
        self.seen = []
        self.overrides = {}
        self.pages = {1: [meta(version)]}
        self.raw = {}
        self.clients = []
        self.proxies = []

    async def handle(self, request):
        url = str(request.url)
        self.seen.append(url)
        if url in self.overrides:
            reply = self.overrides[url]
            if callable(reply):
                return await reply(request)
            if isinstance(reply, Exception):
                raise reply
            return reply
        if url == API_URL + "/latest":
            return httpx.Response(200, json=meta(self.version))
        if request.url.host == "api.github.com":
            page = int(request.url.params.get("page", 1))
            return httpx.Response(200, json=self.pages.get(page, []))
        assert request.url.host == "raw.githubusercontent.com", url
        version = request.url.path.split("/")[-2][1:]
        return httpx.Response(200, json=self.raw.get(version, release(version)))

    def client(self, proxy):
        self.proxies.append(proxy)
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handle), trust_env=False)
        self.clients.append(client)
        return client

    def manager(self, current=None, **kwargs):
        settings = kwargs.pop("settings", lambda: SimpleNamespace(PROXY_ENABLED=False, PROXY_URL=""))
        return VersionUpdateManager(current or local(), settings, client_factory=self.client, **kwargs)


async def check(manager):
    manager.request_check()
    await manager._task
    return manager.snapshot()


class Clock:
    def __init__(self):
        self.value = 0.0
        self.waiters = []

    def monotonic(self):
        return self.value

    def now(self):
        return datetime(2026, 10, 23, tzinfo=timezone.utc) + timedelta(seconds=self.value)

    async def sleep(self, delay):
        future = asyncio.get_running_loop().create_future()
        self.waiters.append((self.value + delay, future))
        await future

    async def advance(self, seconds):
        self.value += seconds
        for deadline, future in self.waiters:
            if deadline <= self.value and not future.done():
                future.set_result(None)
        for _ in range(20):
            await asyncio.sleep(0)

    def options(self):
        return dict(monotonic=self.monotonic, now=self.now, sleep=self.sleep)
