"""Retrieval statistics API: audit aggregation and the Admin route (plan 53.3).

Covers capability-based search counting, calendar-day/weekly trends, feedback
correlation and dedup, data-quality diagnostics, timezone validation, bounded
reads, and the Admin HTTP contract. Aggregation tests use synthetic JSONL and
an injected evaluation instant; HTTP tests use the shared ``service`` fixture.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from app.operations import retrieval_stats as rs
from app.transport.admin import AdminAPI

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
UTC = ZoneInfo("UTC")
SEARCH = ("search_semantic", "search_lexical", "search_anchor", "search_meta")


def _iso(dt):
    return dt.isoformat()


def search_event(ts, tool="search_semantic", *, status="ok", namespace=None,
                 search_id="se_1", items=("m1",), source="mcp"):
    event = {"ts": _iso(ts), "tool": tool, "status": status, "duration_ms": 1.0,
             "namespace": namespace, "search_id": search_id}
    if source is not None:
        event["context"] = {"source": source}
    result = {"search_id": search_id, "total": len(items or [])}
    if items is not None:
        result["items"] = [{"id": memory_id} for memory_id in items]
    event["result"] = result
    event["params"] = {"namespace": namespace}
    return event


def report_event(ts, search_id="se_1", ratings=(), *, status="ok",
                 analysis_group=None, namespace=None):
    event = {"ts": _iso(ts), "tool": "report", "status": status, "duration_ms": 0.0,
             "namespace": namespace,
             "params": {"search_id": search_id,
                        "ratings": [{"m_id": memory_id, "rating": rating}
                                    for memory_id, rating in ratings]}}
    result = {"recorded": True, "count": len(ratings)}
    if analysis_group is not None:
        result["feedback_context"] = {"analysis_group": analysis_group}
    event["result"] = result
    event["context"] = {"source": "mcp"}
    return event


def write_log(path, events):
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


def collect(tmp_path, events, *, days=7, namespace=None, tz=UTC, name="audit.jsonl",
            max_files=10, now=NOW):
    path = tmp_path / name
    if events is not None:
        write_log(path, events)
    return rs.collect_retrieval_stats(path, max_files, days=days, tz=tz,
                                      namespace=namespace, now=now)


# ---------------------------------------------------------------------------
# Timezone resolution and journal paths
# ---------------------------------------------------------------------------

def test_resolve_timezone_valid_and_unknown():
    assert rs.resolve_timezone("Asia/Shanghai") is not None
    for bad in ("Not/AZone", "", None, 5):
        with pytest.raises(rs.UnknownTimezone):
            rs.resolve_timezone(bad)


def test_resolve_timezone_missing_database(monkeypatch):
    def _no_zone(*args, **kwargs):
        raise ZoneInfoNotFoundError("no tzdata")

    monkeypatch.setattr(rs, "ZoneInfo", _no_zone)
    monkeypatch.setattr(rs, "available_timezones", lambda: set())
    with pytest.raises(rs.TimezoneDatabaseUnavailable):
        rs.resolve_timezone("Asia/Shanghai")


def test_journal_paths_supports_journal_and_legacy_module():
    class Journal:
        path = "/tmp/j.jsonl"
        max_files = 4
    assert rs.journal_paths(Journal()) == ("/tmp/j.jsonl", 4)

    class Legacy:
        AUDIT_PATH = "/tmp/legacy.jsonl"
        AUDIT_MAX_FILES = 3
    assert rs.journal_paths(Legacy()) == ("/tmp/legacy.jsonl", 3)


# ---------------------------------------------------------------------------
# Search counting
# ---------------------------------------------------------------------------

def test_search_counts_modes_trend_and_available(tmp_path):
    events = [
        search_event(NOW, "search_semantic", search_id="se_a", items=("m1",)),
        search_event(NOW, "search_lexical", status="error", search_id="se_b", items=None),
        search_event(NOW - timedelta(days=6),
                     "search_meta", namespace="alpha", search_id="se_c", items=()),
        search_event(datetime(2026, 9, 1, tzinfo=timezone.utc),
                     "search_semantic", search_id="se_old", items=("m2",)),  # out of window
    ]
    result = collect(tmp_path, events)
    search = result["search"]
    assert search["total_searches"] == 3
    assert search["successful_searches"] == 2
    assert search["failed_searches"] == 1
    assert search["non_empty_searches"] == 1  # se_c is empty, se_b failed
    assert [m["mode"] for m in search["modes"]] == ["semantic", "lexical", "anchor", "meta"]
    counts = {m["mode"]: m["count"] for m in search["modes"]}
    assert counts == {"semantic": 1, "lexical": 1, "anchor": 0, "meta": 1}
    assert len(search["trend"]) == 7
    assert sum(b["count"] for b in search["trend"]) == 3
    # available_from ignores the request window but requires capability eligibility.
    assert result["scope"]["available_from"] == "2026-09-01T00:00:00Z"


def test_unrecognized_source_is_skipped_and_counted(tmp_path):
    events = [
        search_event(NOW, source="mcp"),
        search_event(NOW, source="admin_http", search_id="se_admin"),
        search_event(NOW, source=None, search_id="se_none"),
    ]
    result = collect(tmp_path, events)
    assert result["search"]["total_searches"] == 1
    assert result["data_quality"]["skipped_unrecognized_source"] == 2


def test_missing_result_field_is_not_inferred_empty(tmp_path):
    event = search_event(NOW, search_id="se_x", items=("m1",))
    event["result"] = {"search_id": "se_x", "total": 1}  # items omitted
    result = collect(tmp_path, [event])
    assert result["search"]["successful_searches"] == 1
    assert result["search"]["non_empty_searches"] == 0


def test_malformed_lines_counted_once(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text("not json{\n\n" + json.dumps(search_event(NOW)) + "\n", encoding="utf-8")
    result = rs.collect_retrieval_stats(path, 10, days=7, tz=UTC, namespace=None, now=NOW)
    assert result["data_quality"]["skipped_malformed_lines"] == 1
    assert result["search"]["total_searches"] == 1


def test_bounded_read_marks_incomplete_tail(tmp_path):
    path = tmp_path / "audit.jsonl"
    line = json.dumps(search_event(NOW)) + "\n"
    path.write_text(line + line, encoding="utf-8")

    class Capped:
        def __init__(self, real, limit):
            self.real = real
            self.limit = limit

        def stat(self):
            return os.stat_result((0, 0, 0, 0, 0, 0, self.limit, 0, 0, 0))

        def open(self, mode):
            return Path(self.real).open(mode)

    diag = rs._new_diag()
    events = list(rs._iter_events(Capped(path, len(line)), diag, True))
    assert len(events) == 1
    assert "incomplete_tail" in diag["warnings"]


def test_no_audit_history(tmp_path):
    result = rs.collect_retrieval_stats(tmp_path / "missing.jsonl", 10,
                                        days=7, tz=UTC, namespace=None, now=NOW)
    assert result["search"]["total_searches"] == 0
    assert result["feedback"]["coverage_percent"] is None
    assert result["feedback"]["average_rating"] is None
    assert result["data_quality"]["warnings"] == ["no_audit_history"]


# ---------------------------------------------------------------------------
# Feedback correlation
# ---------------------------------------------------------------------------

def test_feedback_coverage_dedup_and_distribution(tmp_path):
    events = [
        search_event(NOW, search_id="se_a", items=("m1", "m2")),
        search_event(NOW, search_id="se_b", namespace="alpha", items=("m3",)),
        report_event(NOW, "se_a", [("m1", 8), ("m2", 0)]),
        report_event(NOW, "se_a", [("m1", 2)]),          # same pair: last wins
        report_event(NOW, "se_b", [("m3", -3)]),
    ]
    result = collect(tmp_path, events)
    feedback = result["feedback"]
    assert result["search"]["non_empty_searches"] == 2
    assert feedback["eligible_searches"] == 2
    assert feedback["rated_searches"] == 2
    assert feedback["coverage_percent"] == 100.0
    assert feedback["rating_count"] == 3
    assert feedback["distribution"] == {"positive": 1, "neutral": 1, "negative": 1}
    assert feedback["average_rating"] == round((2 + 0 - 3) / 3, 2)


def test_feedback_test_excluded_before_dedup_and_unmatched(tmp_path):
    events = [
        search_event(NOW, search_id="se_a", items=("m1",)),
        report_event(NOW, "se_a", [("m1", 10)], analysis_group="declared_test"),
        report_event(NOW, "se_a", [("m1", 4)]),
        report_event(NOW, "se_missing", [("m9", 5)]),   # search absent from history
    ]
    result = collect(tmp_path, events)
    assert result["feedback"]["rating_count"] == 1
    assert result["feedback"]["average_rating"] == 4.0
    assert result["data_quality"]["excluded_test_reports"] == 1
    assert result["data_quality"]["unmatched_reports"] == 1


def test_feedback_out_of_scope_membership_and_late(tmp_path):
    events = [
        search_event(NOW, namespace="alpha", search_id="se_a", items=("m1",)),
        # member not returned by that search -> ignored
        report_event(NOW, "se_a", [("m_other", 7)]),
        # search outside the selected namespace -> not attributed
        search_event(NOW, namespace="beta", search_id="se_b", items=("m2",)),
        report_event(NOW, "se_b", [("m2", 7)]),
        # report after the evaluation instant -> excluded
        report_event(NOW.replace(hour=13), "se_a", [("m1", 5)]),
    ]
    result = collect(tmp_path, events, namespace="alpha")
    assert result["search"]["non_empty_searches"] == 1
    assert result["feedback"]["rating_count"] == 0
    assert result["feedback"]["coverage_percent"] == 0.0


def test_namespace_scopes_all_search_metrics(tmp_path):
    events = [
        search_event(NOW, namespace="alpha", search_id="se_a", items=("m1",)),
        search_event(NOW, namespace="beta", search_id="se_b", items=("m2",)),
        search_event(NOW, namespace="beta", status="error", search_id="se_c", items=None),
    ]
    everything = collect(tmp_path, events)
    assert everything["search"]["total_searches"] == 3

    scoped = collect(tmp_path, events, namespace="alpha")
    assert scoped["search"]["total_searches"] == 1
    assert scoped["search"]["successful_searches"] == 1
    assert scoped["search"]["failed_searches"] == 0
    assert scoped["search"]["non_empty_searches"] == 1
    assert sum(m["count"] for m in scoped["search"]["modes"]) == 1
    assert sum(b["count"] for b in scoped["search"]["trend"]) == 1


def test_conflicting_search_id_excluded_from_feedback(tmp_path):
    events = [
        search_event(NOW, search_id="se_dup", items=("m1",)),
        search_event(NOW, search_id="se_dup", items=("m2",)),
        report_event(NOW, "se_dup", [("m1", 9)]),
    ]
    result = collect(tmp_path, events)
    assert result["data_quality"]["conflicting_search_ids"] == 1
    assert result["feedback"]["rating_count"] == 0


def test_neutral_zero_is_valid_but_missing_rating_is_not(tmp_path):
    events = [
        search_event(NOW, search_id="se_a", items=("m1", "m2")),
        report_event(NOW, "se_a", [("m1", 0), ("m2", "bad")]),
    ]
    result = collect(tmp_path, events)
    assert result["feedback"]["rating_count"] == 1
    assert result["feedback"]["distribution"]["neutral"] == 1
    assert result["feedback"]["average_rating"] == 0.0


# ---------------------------------------------------------------------------
# Windows and buckets
# ---------------------------------------------------------------------------

def test_90_day_weekly_buckets_sum_and_shape(tmp_path):
    events = [
        search_event(datetime(2026, 10, 10, 1, tzinfo=timezone.utc), search_id="se_1"),
        search_event(datetime(2026, 10, 5, 1, tzinfo=timezone.utc), search_id="se_2"),
    ]
    result = collect(tmp_path, events, days=90)
    trend = result["search"]["trend"]
    assert all(set(b) == {"start_date", "end_date", "start_utc", "end_utc", "count"}
               for b in trend)
    assert sum(b["count"] for b in trend) == 2
    assert trend[0]["start_date"] == result["scope"]["local_start_date"]
    assert trend[-1]["end_date"] == result["scope"]["local_end_date"]


def test_zero_window_shape(tmp_path):
    result = collect(tmp_path, [], days=30)
    assert len(result["search"]["trend"]) == 30
    assert all(b["count"] == 0 for b in result["search"]["trend"])
    assert result["feedback"]["coverage_percent"] is None


# ---------------------------------------------------------------------------
# Admin HTTP contract
# ---------------------------------------------------------------------------

@pytest.fixture
def agent(service):
    with TestClient(Starlette(routes=AdminAPI(service).routes)) as client:
        yield client


@pytest.fixture
def audit_log(tmp_path, monkeypatch):
    path = tmp_path / "http-audit.jsonl"
    monkeypatch.setattr("app.audit.AUDIT_PATH", str(path))
    return path


def test_route_ok_and_shape(agent, audit_log):
    write_log(audit_log, [search_event(datetime.now(timezone.utc), search_id="se_http",
                                       items=("m1",))])
    response = agent.get("/api/stats/retrieval", params={"timezone": "UTC"})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"scope", "search", "feedback", "data_quality"}
    assert body["scope"]["days"] == 7
    assert body["scope"]["timezone"] == "UTC"
    assert body["search"]["total_searches"] == 1
    assert len(body["search"]["trend"]) == 7


@pytest.mark.parametrize("params", [
    {},                                            # timezone required
    {"timezone": "UTC", "days": "5"},              # unsupported days
    {"timezone": "UTC", "days": "abc"},            # non-numeric days
    {"timezone": "UTC", "extra": "1"},             # unknown parameter
])
def test_route_parameter_errors(agent, audit_log, params):
    response = agent.get("/api/stats/retrieval", params=params)
    assert response.status_code == 400
    assert response.json()["response_code"] == "invalid_request"


def test_route_duplicate_parameter(agent, audit_log):
    response = agent.get("/api/stats/retrieval?timezone=UTC&timezone=UTC")
    assert response.status_code == 400
    assert response.json()["response_code"] == "invalid_request"


def test_route_unknown_timezone(agent, audit_log):
    response = agent.get("/api/stats/retrieval", params={"timezone": "Not/AZone"})
    assert response.status_code == 400
    assert response.json()["response_code"] == "invalid_timezone"


def test_route_namespace_errors(agent, service, audit_log):
    blank = agent.get("/api/stats/retrieval", params={"timezone": "UTC", "namespace": "  "})
    assert blank.status_code == 400
    assert blank.json()["response_code"] == "invalid_namespace"

    service.memory_add("x", namespace="alpha", confirm_codes=["namespace_not_found"], metadata={})
    ok = agent.get("/api/stats/retrieval", params={"timezone": "UTC", "namespace": "alpha"})
    assert ok.status_code == 200
    assert ok.json()["scope"]["namespace"] == "alpha"

    missing = agent.get("/api/stats/retrieval", params={"timezone": "UTC", "namespace": "nope"})
    assert missing.status_code == 400
    assert missing.json()["response_code"] == "namespace_not_found"


def test_route_timezone_database_unavailable(agent, audit_log, monkeypatch):
    def _raise(_name):
        raise rs.TimezoneDatabaseUnavailable(_name)

    monkeypatch.setattr("app.transport.admin.resolve_timezone", _raise)
    response = agent.get("/api/stats/retrieval", params={"timezone": "UTC"})
    assert response.status_code == 503
    assert response.json()["response_code"] == "timezone_database_unavailable"
