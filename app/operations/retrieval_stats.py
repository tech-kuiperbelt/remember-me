"""Read-only retrieval statistics aggregated from retained Audit JSONL files.

This module powers ``GET /api/stats/retrieval`` (plan 53.3). It is deliberately
independent of the MCP transport, the SQLite primary and the capped
``/api/audit`` reader: it scans the retained audit files itself so it can see
the whole retained history, uses capability-based eligibility instead of a
fixed schema date, and never writes to the journal or acquires its lock.

Guarantees:

- Never holds the audit writer lock and makes no snapshot promise; it detects
  concurrent append/rotation with a bounded file snapshot and retries once.
- Reads each file up to the length observed when it was opened, so append
  activity cannot be counted twice within one response.
- Streaming only: no query text, summary or full event is retained.
"""

import json
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

SUPPORTED_DAYS = frozenset({7, 30, 90})

# MCP audit tool name -> the public mode label used by the response.
SEARCH_MODES = {
    "search_semantic": "semantic",
    "search_lexical": "lexical",
    "search_anchor": "anchor",
    "search_meta": "meta",
}
_MODE_ORDER = ("semantic", "lexical", "anchor", "meta")
_MCP_SOURCE = "mcp"
_RATING_MIN = -10
_RATING_MAX = 10


class UnknownTimezone(ValueError):
    """The requested IANA timezone name is not recognized."""


class TimezoneDatabaseUnavailable(Exception):
    """The server has no usable timezone database (decision: 503, not UTC)."""


class RetrievalStatsReadError(Exception):
    """An existing audit file could not be read; never reported as empty."""


def journal_paths(audit):
    """Resolve ``(path, max_files)`` from a runtime journal or the legacy module."""
    path = getattr(audit, "path", None)
    if path is not None:
        return str(path), max(1, int(getattr(audit, "max_files", 10)))
    # The bare-service test wiring exposes the ``app.audit`` module, not a journal.
    return str(getattr(audit, "AUDIT_PATH", "audit.jsonl")), max(
        1, int(getattr(audit, "AUDIT_MAX_FILES", 10)))


def resolve_timezone(name):
    """Return a ``ZoneInfo`` for ``name`` or raise a typed error.

    Distinguishes an unknown name (400) from a missing timezone database (503);
    the caller must never silently fall back to UTC.
    """
    if not isinstance(name, str) or not name.strip():
        raise UnknownTimezone("timezone is required")
    candidate = name.strip()
    try:
        return ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError):
        pass
    try:
        known = available_timezones()
    except Exception:  # pragma: no cover - defensive; tzdb loading failure
        known = set()
    if not known:
        raise TimezoneDatabaseUnavailable(candidate)
    raise UnknownTimezone(candidate)


def discover_files(path, max_files):
    """Return existing retained files, newest first (``audit.jsonl``, ``.1`` …)."""
    base = Path(path)
    names = [base] + [Path(f"{base}.{index}") for index in range(1, max(1, max_files))]
    return [candidate for candidate in names if candidate.exists()]


def collect_retrieval_stats(path, max_files, *, days, tz, namespace, now=None):
    """Aggregate the retained Audit files into the frozen retrieval response.

    ``namespace`` must already be normalized/validated (``None`` means All);
    ``tz`` is a resolved ``tzinfo``. ``now`` is the single evaluation instant
    (defaults to the current UTC time) and is reused across the optional retry.
    """
    if days not in SUPPORTED_DAYS:
        raise ValueError("days must be one of 7, 30, 90")
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)

    window = _resolve_window(now, tz, days)
    files = discover_files(path, max_files)
    if not files:
        return _empty_response(window, tz, namespace, days,
                               warnings=["no_audit_history"])

    result = None
    for attempt in range(2):
        before = _snapshot(files)
        diag = _new_diag()
        result = _scan(files, window, tz, namespace, now, diag)
        files_after = discover_files(path, max_files)
        if files_after == files and before == _snapshot(files_after):
            return result
        files = files_after
        if attempt == 1:
            diag["warnings"].append("audit_changed_during_scan")
            return result
    return result


# ---------------------------------------------------------------------------
# Window and time helpers
# ---------------------------------------------------------------------------

def _resolve_window(now, tz, days):
    local_now = now.astimezone(tz)
    end_date = local_now.date()
    start_date = end_date - timedelta(days=days - 1)
    start_local = datetime.combine(start_date, time.min, tzinfo=tz)
    return {
        "days": days,
        "tz": tz,
        "local_start_date": start_date,
        "local_end_date": end_date,
        "window_start_utc": start_local.astimezone(timezone.utc),
        "window_end_utc": now,
    }


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_ts(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _new_diag():
    return {
        "skipped_malformed_lines": 0,
        "skipped_unrecognized_source": 0,
        "excluded_test_reports": 0,
        "unmatched_reports": 0,
        "conflicting_search_ids": 0,
        "warnings": [],
    }


def _scope(window, tz, namespace, days):
    return {
        "namespace": namespace,
        "days": days,
        "timezone": str(tz),
        "local_start_date": window["local_start_date"].isoformat(),
        "local_end_date": window["local_end_date"].isoformat(),
        "window_start_utc": _iso(window["window_start_utc"]),
        "window_end_utc": _iso(window["window_end_utc"]),
        "available_from": None,
        "coverage_complete": None,
    }


def _empty_response(window, tz, namespace, days, warnings):
    return {
        "scope": _scope(window, tz, namespace, days),
        "search": {
            "total_searches": 0,
            "successful_searches": 0,
            "failed_searches": 0,
            "non_empty_searches": 0,
            "modes": [{"mode": mode, "count": 0} for mode in _MODE_ORDER],
            "trend": _bucket_counts(window, []),
        },
        "feedback": {
            "eligible_searches": 0,
            "rated_searches": 0,
            "coverage_percent": None,
            "rating_count": 0,
            "average_rating": None,
            "distribution": {"positive": 0, "neutral": 0, "negative": 0},
        },
        "data_quality": {**_new_diag(), "warnings": list(warnings)},
    }


def _bucket_counts(window, search_dates):
    """Deterministic trend buckets; ``search_dates`` are local dates already in window."""
    counts = {}
    for date in search_dates:
        counts[date] = counts.get(date, 0) + 1
    days = window["days"]
    start_date = window["local_start_date"]
    end_date = window["local_end_date"]
    if days in (7, 30):
        buckets = []
        cursor = start_date
        while cursor <= end_date:
            buckets.append({"date": cursor.isoformat(), "count": counts.get(cursor, 0)})
            cursor += timedelta(days=1)
        return buckets
    # 90 days: natural ISO weeks (Monday-based), edge weeks clipped to the range.
    tz = window["tz"]
    buckets = []
    monday = start_date - timedelta(days=start_date.weekday())
    while monday <= end_date:
        seg_start = max(monday, start_date)
        seg_end = min(monday + timedelta(days=6), end_date)
        count = 0
        cursor = seg_start
        while cursor <= seg_end:
            count += counts.get(cursor, 0)
            cursor += timedelta(days=1)
        buckets.append({
            "start_date": seg_start.isoformat(),
            "end_date": seg_end.isoformat(),
            "start_utc": _iso(datetime.combine(seg_start, time.min, tzinfo=tz)),
            "end_utc": _iso(datetime.combine(seg_end + timedelta(days=1), time.min, tzinfo=tz)),
            "count": count,
        })
        monday += timedelta(days=7)
    return buckets


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def _snapshot(files):
    snap = []
    for candidate in files:
        try:
            stat = candidate.stat()
        except OSError:
            snap.append((str(candidate), None))
            continue
        snap.append((str(candidate), (stat.st_ino, stat.st_dev, stat.st_size)))
    return snap


def _iter_events(candidate, diag, count_diag, needle=None):
    """Yield parsed events up to the size observed at open.

    A line that crosses the observed length (or a trailing partial line) is the
    concurrently-appended tail and is skipped. ``count_diag`` is False on the
    second pass so malformed/incomplete diagnostics are not double-counted.
    ``needle`` is a cheap pre-parse guard: a line without that substring cannot
    be a target event, so it is skipped before ``json.loads``.
    """
    try:
        limit = candidate.stat().st_size
        handle = candidate.open("rb")
    except FileNotFoundError:
        return  # rotated away between discovery and open
    except OSError as exc:
        raise RetrievalStatsReadError(str(exc)) from exc
    consumed = 0
    incomplete = False
    with handle:
        while True:
            try:
                raw = handle.readline()
            except OSError as exc:
                raise RetrievalStatsReadError(str(exc)) from exc
            if not raw:
                break
            if consumed + len(raw) > limit:
                incomplete = True
                break
            consumed += len(raw)
            try:
                text = raw.decode("utf-8").strip()
            except UnicodeDecodeError:
                if count_diag:
                    diag["skipped_malformed_lines"] += 1
                continue
            if not text:
                continue
            if needle is not None and needle not in text:
                continue
            try:
                event = json.loads(text)
            except (TypeError, ValueError):
                if count_diag:
                    diag["skipped_malformed_lines"] += 1
                continue
            if isinstance(event, dict):
                yield event
    if incomplete and count_diag:
        diag["warnings"].append("incomplete_tail")


def _scan(files, window, tz, namespace, now, diag):
    order = list(reversed(files))  # oldest rotated file first, newest last
    search_ids = set()
    signatures = {}
    conflicting = set()
    scoped_ids = {}
    mode_counts = {mode: 0 for mode in _MODE_ORDER}
    successful = failed = non_empty = 0
    search_dates = []
    available_from = None

    for candidate in order:
        # Pass 1 parses every line so malformed lines are counted regardless of
        # tool; the cheap needle guard is only safe where diagnostics are off.
        for event in _iter_events(candidate, diag, True):
            tool = event.get("tool")
            if tool not in SEARCH_MODES:
                continue
            ts = _parse_ts(event.get("ts"))
            status = event.get("status")
            if ts is None or status not in ("ok", "error"):
                continue
            if (event.get("context") or {}).get("source") != _MCP_SOURCE:
                diag["skipped_unrecognized_source"] += 1
                continue
            # Eligible search event (any window) for correlation and availability.
            if available_from is None or ts < available_from:
                available_from = ts
            search_id = event.get("search_id")
            result = event.get("result")
            items = result.get("items") if isinstance(result, dict) else None
            ids = None
            if isinstance(items, list):
                ids = frozenset(item.get("id") for item in items
                                if isinstance(item, dict) and isinstance(item.get("id"), str))
            if isinstance(search_id, str) and search_id:
                search_ids.add(search_id)
                signature = (event.get("namespace"), event.get("ts"), ids)
                previous = signatures.get(search_id)
                if previous is None:
                    signatures[search_id] = signature
                elif previous != signature:
                    conflicting.add(search_id)
            if ts < window["window_start_utc"] or ts > window["window_end_utc"]:
                continue
            if namespace is not None and event.get("namespace") != namespace:
                continue
            # Namespace and time scope the search metrics themselves (design §4).
            mode_counts[SEARCH_MODES[tool]] += 1
            if status == "ok":
                successful += 1
            else:
                failed += 1
            search_dates.append(ts.astimezone(tz).date())
            is_nonempty = status == "ok" and bool(ids)
            if is_nonempty:
                non_empty += 1
                if isinstance(search_id, str) and search_id:
                    scoped_ids[search_id] = ids

    diag["conflicting_search_ids"] = len(conflicting)
    for search_id in conflicting:
        scoped_ids.pop(search_id, None)

    aggregated = _aggregate_reports(order, scoped_ids, search_ids, now, diag)
    coverage = None
    if non_empty:
        coverage = round(100 * aggregated["rated_searches"] / non_empty, 2)
    average = None
    if aggregated["rating_count"]:
        average = round(aggregated["rating_sum"] / aggregated["rating_count"], 2)

    scope = _scope(window, tz, namespace, window["days"])
    scope["available_from"] = _iso(available_from) if available_from else None
    return {
        "scope": scope,
        "search": {
            "total_searches": successful + failed,
            "successful_searches": successful,
            "failed_searches": failed,
            "non_empty_searches": non_empty,
            "modes": [{"mode": mode, "count": mode_counts[mode]} for mode in _MODE_ORDER],
            "trend": _bucket_counts(window, search_dates),
        },
        "feedback": {
            "eligible_searches": non_empty,
            "rated_searches": aggregated["rated_searches"],
            "coverage_percent": coverage,
            "rating_count": aggregated["rating_count"],
            "average_rating": average,
            "distribution": aggregated["distribution"],
        },
        "data_quality": dict(diag),
    }


def _aggregate_reports(order, scoped_ids, search_ids, now, diag):
    ratings = {}
    excluded_test = 0
    unmatched = 0
    for candidate in order:
        for event in _iter_events(candidate, diag, False, needle='"report"'):
            if event.get("tool") != "report" or event.get("status") != "ok":
                continue
            if (event.get("context") or {}).get("source") != _MCP_SOURCE:
                continue
            ts = _parse_ts(event.get("ts"))
            if ts is None or ts > now:
                continue
            context = (event.get("result") or {}).get("feedback_context") or {}
            if context.get("analysis_group") == "declared_test":
                excluded_test += 1
                continue
            params = event.get("params") or {}
            search_id = params.get("search_id")
            if not isinstance(search_id, str) or not search_id:
                continue
            if search_id not in search_ids:
                unmatched += 1
                continue
            ids = scoped_ids.get(search_id)
            if ids is None:
                continue
            for item in params.get("ratings") or []:
                if not isinstance(item, dict):
                    continue
                memory_id = item.get("m_id")
                rating = item.get("rating")
                if not isinstance(memory_id, str) or not memory_id:
                    continue
                if isinstance(rating, bool) or not isinstance(rating, int):
                    continue
                if not _RATING_MIN <= rating <= _RATING_MAX:
                    continue
                if memory_id not in ids:
                    continue
                ratings[(search_id, memory_id)] = rating

    values = list(ratings.values())
    diag["excluded_test_reports"] += excluded_test
    diag["unmatched_reports"] += unmatched
    distribution = {"positive": 0, "neutral": 0, "negative": 0}
    for rating in values:
        if rating > 0:
            distribution["positive"] += 1
        elif rating < 0:
            distribution["negative"] += 1
        else:
            distribution["neutral"] += 1
    return {
        "rated_searches": len({key[0] for key in ratings}),
        "rating_count": len(values),
        "rating_sum": sum(values),
        "distribution": distribution,
    }
