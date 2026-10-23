"""HTTP-owned, in-memory version checks. No Git, model work or database writes."""
import asyncio
import copy
import json
import math
import re
import ssl
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlsplit

import httpx

from app.config import jsonc
from app.domain.releases import MAX_RELEASE_BYTES, validate_release
from app.domain.versioning import compare_versions, parse_version

API_URL = "https://api.github.com/repos/tech-kuiperbelt/remember-me/releases"
RAW_URL = "https://raw.githubusercontent.com/tech-kuiperbelt/remember-me"
RELEASES_URL = "https://github.com/tech-kuiperbelt/remember-me/releases"
CHECK_INTERVAL = 24 * 60 * 60
CHECK_TIMEOUT = 30
MAX_PAGES = 5
MAX_RELEASES = 20
MAX_RAW_BYTES = 2 * 1024 * 1024
MAX_API_BYTES = 1024 * 1024


def utc_now():
    return datetime.now(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def create_http_client(proxy):
    """Both fixed HTTPS destinations use the same saved Settings proxy."""
    return httpx.AsyncClient(proxy=proxy, trust_env=False, follow_redirects=False,
                             timeout=httpx.Timeout(5),
                             limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
                             headers={"Accept": "application/vnd.github+json",
                                      "User-Agent": "Remember-Me-Version-Check"})


class CheckError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)  # Never propagate raw network/config exceptions.


class CheckRejected(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__("version check unavailable")


def _rate_delay(headers, body, now):
    limited = (headers.get("x-ratelimit-remaining") == "0"
               or "retry-after" in headers or b"rate limit" in body.lower())
    delays = [60.0]
    if "retry-after" in headers:
        try:
            value = headers["retry-after"]
            try:
                delay = float(value)
            except ValueError:
                delay = (parsedate_to_datetime(value) - now).total_seconds()
            if math.isfinite(delay):
                delays = [max(0.0, delay)]
        except (ValueError, TypeError, OverflowError):
            pass
    if headers.get("x-ratelimit-remaining") == "0":
        try:
            delay = float(headers["x-ratelimit-reset"]) - now.timestamp()
            if math.isfinite(delay):
                delays.append(max(0.0, delay))
        except (KeyError, ValueError, OverflowError):
            pass
    # Preserve long waits, bounding only at the representable UTC timestamp.
    maximum = (datetime.max.replace(tzinfo=timezone.utc) - now).total_seconds() - 1
    return limited, min(max(delays), maximum)


def _transport_code(exc, prefix):
    if isinstance(exc, httpx.TimeoutException):
        return prefix + "_timeout"
    cause = exc
    for _ in range(8):
        if isinstance(cause, ssl.SSLError):
            return prefix + "_tls_error"
        cause = cause.__cause__ or cause.__context__
        if cause is None:
            break
    return prefix + "_unreachable"


def _metadata(item):
    if (not isinstance(item, dict) or type(item.get("draft")) is not bool
            or type(item.get("prerelease")) is not bool):
        raise CheckError("latest_release_invalid")
    if item["draft"] or item["prerelease"]:
        return None
    tag = item.get("tag_name")
    try:
        if not isinstance(tag, str) or not tag.startswith("v"):
            raise ValueError()
        parse_version(tag[1:])
        published = datetime.fromisoformat(item["published_at"].replace("Z", "+00:00"))
        if published.tzinfo is None:
            raise ValueError()
        published_at = iso(published)
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise CheckError("latest_release_invalid") from None
    return {"tag": tag, "version": tag[1:], "url": f"{RELEASES_URL}/tag/{tag}",
            "published_at": published_at}


class GitHubReleases:
    """One bounded round; raw file deduplication never crosses check rounds."""

    def __init__(self, client, *, now=utc_now):
        self.client, self.now = client, now
        self.raw_bytes = 0
        self.retry_delay = None

    async def _get(self, url, *, raw=False):
        prefix = "release_file" if raw else "github"
        limit = MAX_RELEASE_BYTES if raw else MAX_API_BYTES
        body = bytearray()
        try:
            async with self.client.stream("GET", url) as response:
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    if raw:
                        self.raw_bytes += len(chunk)
                        if self.raw_bytes > MAX_RAW_BYTES:
                            raise CheckError("release_limit_exceeded")
                    if len(body) + len(chunk) > limit:
                        raise CheckError(prefix + "_too_large")
                    body.extend(chunk)
                status, headers = response.status_code, response.headers
        except httpx.RequestError as exc:
            code = (prefix + "_invalid_response" if isinstance(exc, httpx.DecodingError)
                    else _transport_code(exc, prefix))
            raise CheckError(code) from None
        if status == 429 or status == 403:
            limited, delay = _rate_delay(headers, body, self.now())
            if status == 429 or limited:
                self.retry_delay = max(self.retry_delay or 0, delay)
                raise CheckError(prefix + "_rate_limited")
            raise CheckError(prefix + "_forbidden")
        if status >= 500:
            raise CheckError(prefix + "_service_error")
        if status not in (200, 404):
            raise CheckError(prefix + "_invalid_response")
        return status, bytes(body), headers

    @staticmethod
    def _json(body):
        try:
            return json.loads(body.decode("utf-8"))
        except (ValueError, RecursionError):
            raise CheckError("github_invalid_response") from None

    async def _pages(self):
        for page in range(1, MAX_PAGES + 1):
            status, body, headers = await self._get(f"{API_URL}?per_page=100&page={page}")
            values = self._json(body)
            if status != 200 or not isinstance(values, list) or len(values) > 100:
                raise CheckError("release_list_failed")
            # Never follow an arbitrary Link URL. Only advance our fixed URL.
            links = httpx.Response(200, headers=headers).links
            for link in links.values():
                try:
                    parts = urlsplit(link.get("url", ""))
                except ValueError:
                    raise CheckError("release_list_failed") from None
                query = parse_qs(parts.query)
                # GitHub may canonicalize Link paths to /repositories/<id>/releases.
                # These are pagination hints only: requests still use our fixed repo URL.
                fixed_path = parts.path == urlsplit(API_URL).path
                canonical_path = re.fullmatch(r"/repositories/[1-9][0-9]*/releases", parts.path)
                if (parts.scheme != "https" or parts.netloc != "api.github.com"
                        or not (fixed_path or canonical_path) or parts.fragment
                        or set(query) != {"page", "per_page"}
                        or query["per_page"] != ["100"]
                        or len(query["page"]) != 1 or not query["page"][0].isascii()
                        or not query["page"][0].isdigit() or len(query["page"][0]) > 9):
                    raise CheckError("release_list_failed")
            if "next" in links:
                if parse_qs(urlsplit(links["next"]["url"]).query)["page"] != [str(page + 1)]:
                    raise CheckError("release_list_failed")
            yield values
            if "next" not in links and len(values) < 100:
                return
        raise CheckError("release_limit_exceeded")

    async def _release(self, meta, *, latest=False):
        try:
            status, body, _ = await self._get(f"{RAW_URL}/{meta['tag']}/release.jsonc", raw=True)
            if status == 404:
                raise CheckError("release_file_not_found")
            try:
                release = validate_release(jsonc.loads(body.decode("utf-8")))
            except (ValueError, RecursionError):
                raise CheckError("release_file_invalid") from None
            if release.version != meta["version"]:
                raise CheckError("release_version_mismatch")
            return {**meta, "release": release.as_dict()}
        except CheckError as exc:
            if latest:
                mapped = {"release_file_not_found": "latest_release_missing",
                          "release_file_invalid": "latest_release_invalid",
                          "release_version_mismatch": "latest_release_version_mismatch"}
                raise CheckError(mapped.get(exc.code, exc.code)) from None
            raise

    async def latest(self):
        status, body, _ = await self._get(API_URL + "/latest")
        if status == 404:
            # A 404 is not proof. Successfully enumerate without selecting a target.
            async for values in self._pages():
                for item in values:
                    if _metadata(item) is not None:
                        raise CheckError("latest_release_invalid")
            return None
        meta = _metadata(self._json(body))
        if meta is None:
            raise CheckError("latest_release_invalid")
        return await self._release(meta, latest=True)

    async def history(self, current, latest, result):
        """Mutate this round's draft so a timeout retains already fetched notes."""
        candidates = {}
        try:
            async for values in self._pages():
                for item in values:
                    meta = _metadata(item)
                    if (meta is not None and compare_versions(current, meta["version"]) < 0
                            and compare_versions(meta["version"], latest["version"]) <= 0):
                        candidates[meta["tag"]] = meta
        except CheckError as exc:
            result.update(notes_complete=False, notes_error_code=(
                "release_limit_exceeded" if exc.code == "release_limit_exceeded"
                else "release_list_failed"))
        candidates.pop(latest["tag"], None)  # Latest raw is fetched once per round.
        ordered = sorted(candidates.values(), key=lambda row: parse_version(row["version"]), reverse=True)
        for index, meta in enumerate(ordered):
            if index >= MAX_RELEASES - 1:
                result.update(notes_complete=False, notes_error_code="release_limit_exceeded")
                break
            if self.retry_delay is not None:
                result.update(notes_complete=False, notes_error_code="release_rate_limited")
                break
            try:
                result["releases"].append(await self._release(meta))
            except CheckError as exc:
                result["notes_complete"] = False
                result["release_errors"].append({"tag": meta["tag"], "code": exc.code,
                                                  "url": meta["url"]})
                if self.retry_delay is not None or exc.code == "release_limit_exceeded":
                    result["notes_error_code"] = exc.code
                    break


class VersionUpdateManager:
    """All mutable check state is confined to the owning HTTP event loop."""

    def __init__(self, current, settings, *, audit=None, running=lambda: True,
                 client_factory=None, monotonic=time.monotonic, now=utc_now,
                 sleep=asyncio.sleep, interval=CHECK_INTERVAL, timeout=CHECK_TIMEOUT):
        self.current, self.settings, self.audit, self.running = current, settings, audit, running
        self.client_factory = client_factory or create_http_client
        self.monotonic, self.now, self.sleep = monotonic, now, sleep
        self.interval, self.timeout = interval, timeout
        self._client = self._proxy = None
        self._task = self._scheduler = None
        self._closed = False
        self._cooldown = 0.0
        self._last_success = None  # Diagnostic only, never substituted after failure.
        self._data = {"current": current.as_dict(),
                      "check": {"status": "CHECKING", "checking": False,
                                "last_attempt_at": None, "last_success_at": None,
                                "error_code": None, "next_allowed_at": None, "revision": 0},
                      **self._empty_notes()}

    @staticmethod
    def _empty_notes():
        return {"latest": None, "notes_scope": "none", "notes_complete": None,
                "releases": [], "release_errors": [], "notes_error_code": None}

    def snapshot(self):
        snapshot = copy.deepcopy(self._data)
        if self.monotonic() >= self._cooldown:
            snapshot["check"]["next_allowed_at"] = None
        return snapshot

    def retry_after(self):
        return max(0, math.ceil(self._cooldown - self.monotonic()))

    def request_check(self, trigger="manual"):
        if self._closed or not self.running():
            raise CheckRejected(503)
        if self._task is not None and not self._task.done():
            return self.snapshot()
        if self.monotonic() < self._cooldown:
            raise CheckRejected(429)
        self._data["check"].update(checking=True, last_attempt_at=iso(self.now()),
                                   next_allowed_at=None)
        self._data["check"]["revision"] += 1
        self._task = asyncio.create_task(self._run(trigger), name="version-check")
        return self.snapshot()

    def start(self):
        if self._scheduler is None and not self._closed:
            self.request_check("startup")
            deadline = self.monotonic() + self.interval
            self._scheduler = asyncio.create_task(self._automatic(deadline), name="version-check-schedule")

    async def _automatic(self, deadline):
        while not self._closed:
            await self.sleep(max(0, deadline - self.monotonic()))
            try:
                self.request_check("automatic")
            except CheckRejected:
                pass
            deadline += self.interval
            if deadline <= self.monotonic():
                deadline += (math.floor((self.monotonic() - deadline) / self.interval) + 1) * self.interval

    async def _get_client(self):
        settings = self.settings()
        proxy = settings.PROXY_URL if settings.PROXY_ENABLED else None
        if self._client is None or proxy != self._proxy:
            if self._client is not None:
                await self._client.aclose()
                self._client = None
            self._client = self.client_factory(proxy)
            self._proxy = proxy
        return self._client

    async def _run(self, trigger):
        began = self.monotonic()
        reader = None
        try:
            reader = GitHubReleases(await self._get_client(), now=self.now)
            remaining = max(0, self.timeout - (self.monotonic() - began))
            latest = await asyncio.wait_for(reader.latest(), timeout=remaining)
            result = self._empty_notes()
            if latest is None:
                status = "NO_RELEASE"
            else:
                result.update(latest=latest, notes_scope="latest_only")
                if self.current.version is None:
                    status = "UPDATE_RECOMMENDED"
                elif compare_versions(self.current.version, latest["version"]) >= 0:
                    status = "UP_TO_DATE"
                else:
                    status = "UPDATE_AVAILABLE"
                    result.update(notes_scope="upgrade_range", notes_complete=True, releases=[latest])
                    try:
                        remaining = max(0, self.timeout - (self.monotonic() - began))
                        await asyncio.wait_for(reader.history(self.current.version, latest, result),
                                               timeout=remaining)
                    except asyncio.TimeoutError:
                        result.update(notes_complete=False, notes_error_code="release_history_timeout")
                    except Exception:
                        # An already validated target remains valid even if an
                        # unexpected history adapter/parser failure occurs.
                        result.update(notes_complete=False, notes_error_code="release_history_failed")
            self._data.update(result)
            self._data["check"].update(status=status, error_code=None, last_success_at=iso(self.now()))
            self._last_success = copy.deepcopy(result)
        except (CheckError, asyncio.TimeoutError) as exc:
            self._data.update(self._empty_notes())
            self._data["check"].update(status="UNAVAILABLE", error_code=(
                exc.code if isinstance(exc, CheckError) else "github_timeout"))
        except asyncio.CancelledError:
            raise
        except Exception:
            # Do not log raw proxy credentials, URLs or exception text.
            self._data.update(self._empty_notes())
            self._data["check"].update(status="UNAVAILABLE", error_code="check_failed")
        finally:
            if reader is not None and reader.retry_delay is not None:
                self._cooldown = self.monotonic() + reader.retry_delay
                self._data["check"]["next_allowed_at"] = iso(self.now() + timedelta(seconds=reader.retry_delay))
            if not self._closed and self.audit is not None:
                row = {key: self._data["check"][key] for key in ("status", "error_code")}
                row["notes_complete"] = self._data["notes_complete"]
                work = asyncio.create_task(asyncio.to_thread(
                    self.audit.record, "version_check", {"trigger": trigger}, row,
                    (self.monotonic() - began) * 1000, context={"source": "version_check"}))
                try:
                    await asyncio.shield(work)
                except asyncio.CancelledError:
                    await work  # Drain the journal writer before Runtime.close.
                    raise
            self._data["check"]["checking"] = False
            self._data["check"]["revision"] += 1

    async def aclose(self):
        self._closed = True
        tasks = [task for task in (self._scheduler, self._task) if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._data["check"]["checking"] = False
