"""Tests for the LLM tool-call audit journal (app/audit.py).

The audit journal is a standalone append-only JSONL file, independent of the
memory stores. Tests point ``app.audit.AUDIT_PATH`` at tmp_path and reset the
module-level writer state between tests so nothing touches real ``data/``.
"""

import json
from pathlib import Path

import pytest

import app.audit as audit


@pytest.fixture
def journal(tmp_path, monkeypatch):
    """Point the audit journal at a temp file and reset its writer state."""
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit, "AUDIT_PATH", str(path))
    monkeypatch.setattr(audit, "AUDIT_MAX_BYTES", 10 * 1024 * 1024)
    monkeypatch.setattr(audit, "AUDIT_MAX_FILES", 10)
    monkeypatch.setattr(audit, "AUDIT_MAX_EVENTS", 0)
    # Reset module-level writer state so each test starts clean.
    monkeypatch.setattr(audit, "_file", None)
    monkeypatch.setattr(audit, "_count", 0)
    return path


def _read_lines(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# -- basic recording --

def test_record_writes_one_event(journal):
    audit.record("add", {"text": "hello", "namespace": "ns1", "metadata": {"k": "v"}},
                 {"id": "abc", "created": True}, 12.345)
    events = _read_lines(journal)
    assert len(events) == 1
    ev = events[0]
    assert ev["tool"] == "add"
    assert ev["status"] == "ok"
    assert ev["duration_ms"] == 12.3          # rounded to 1 decimal
    assert ev["namespace"] == "ns1"
    assert ev["error"] is None
    assert "ts" in ev


def test_record_never_raises_on_bad_args(journal, monkeypatch):
    """Even a pathological event must not raise (audit is best-effort)."""

    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(audit, "_open_file", boom)
    # record() itself swallows everything
    audit.record("add", None, None, 1.0)
    assert not journal.exists() or journal.read_text(encoding="utf-8") == ""


# -- per-tool param/result trimming --

def test_add_strips_text_from_params(journal):
    audit.record("add", {"text": "the full memory body...", "namespace": "ns", "metadata": {"a": 1}},
                 {"id": "abc", "created": True}, 1.0)
    ev = _read_lines(journal)[0]
    assert "text" not in ev["params"]
    assert ev["params"] == {"namespace": "ns", "metadata": {"a": 1},
                            "summary_mode": "fallback"}
    assert ev["result"] == {"id": "abc", "created": True}


def test_write_strips_summary_and_records_presence(journal):
    audit.record("update", {"memory_id": "abc", "text": "body",
                            "summary": "  concise summary  "},
                 {"success": True}, 1.0)
    params = _read_lines(journal)[0]["params"]
    assert "text" not in params
    assert "summary" not in params
    assert params == {"memory_id": "abc", "summary_mode": "input"}


@pytest.mark.parametrize("arguments,result,expected", [
    ({"text": "body"}, {"id": "a", "created": True}, "fallback"),
    ({"text": "body", "summary": "given"}, {"id": "a", "created": True}, "input"),
    ({"text": "body", "summary": "given"}, {"id": "a", "created": False}, "duplicate"),
    ({"memory_id": "a", "text": "new"}, {"success": True}, "fallback"),
    ({"memory_id": "a", "summary": "fixed"}, {"success": True}, "input"),
    ({"memory_id": "a", "metadata": {"x": 1}}, {"success": True}, "preserved"),
])
def test_write_summary_mode(journal, arguments, result, expected):
    tool = "add" if "memory_id" not in arguments else "update"
    audit.record(tool, arguments, result, 1.0)
    assert _read_lines(journal)[0]["params"]["summary_mode"] == expected


def test_search_semantic_keeps_score(journal):
    resolution = {"config": {"top_k": 3, "min_score": 0.65, "max_gap": 0.05},
                  "keywords": [{"input": "project", "match_type": "exact",
                                "matches": [{"value": "project", "score": None,
                                             "accepted": True}]}]}
    result = {"note": "...", "total": 2, "filter_resolution": resolution,
              "items": [{"id": "a", "summary": "s1", "score": 0.9},
                        {"id": "b", "summary": "s2", "score": 0.7}]}
    arguments = {"query": "what is x", "k": 5, "threshold": 0.0, "namespace": "ns",
                 "keywords": ["project"], "categories": ["design"]}
    audit.record("search_semantic", arguments,
                 result, 3.0)
    ev = _read_lines(journal)[0]
    assert ev["params"]["query"] == "what is x"
    assert ev["params"]["keywords"] == ["project"]
    assert ev["params"]["categories"] == ["design"]
    assert ev["result"]["items"][0] == {"id": "a", "summary": "s1", "score": 0.9}
    assert ev["result"]["filter_resolution"] == resolution


def test_search_semantic_caps_filter_values_in_params_and_resolution(journal):
    values = [f"keyword-{i}" for i in range(25)]
    records = [
        {"input": value, "match_type": "semantic",
         "matches": [{"value": f"match-{j}", "score": 0.9 - j / 100,
                       "accepted": True} for j in range(5)]}
        for value in values
    ]
    audit.record(
        "search_semantic",
        {"query": "q", "keywords": values},
        {"items": [], "filter_resolution": {"config": {}, "keywords": records}},
        1.0,
    )

    event = _read_lines(journal)[0]
    assert len(event["params"]["keywords"]) == 20
    assert event["params"]["keywords_total"] == 25
    assert event["params"]["keywords_truncated"] is True
    resolution = event["result"]["filter_resolution"]
    assert len(resolution["keywords"]) == 20
    assert resolution["keywords_total"] == 25
    assert resolution["keywords_truncated"] is True
    assert len(resolution["keywords"][0]["matches"]) == 3
    assert resolution["keywords"][0]["matches_total"] == 5


def test_search_meta_has_no_score(journal):
    result = {"note": "...", "total": 1,
              "items": [{"id": "a", "summary": "s1"}]}
    audit.record("search_meta", {"namespace": "ns", "limit": 100, "offset": 0, "sort_by": "created_at"},
                 result, 2.0)
    ev = _read_lines(journal)[0]
    item = ev["result"]["items"][0]
    assert item == {"id": "a", "summary": "s1"}
    assert "score" not in item


def test_search_result_keeps_search_id(journal):
    result = {"search_id": "se_abcd1234", "note": "...", "total": 1,
              "items": [{"id": "a", "summary": "s1", "score": 0.9}]}
    audit.record("search_semantic", {"query": "q", "k": 5, "namespace": "ns"}, result, 3.0)
    ev = _read_lines(journal)[0]
    assert ev["result"]["search_id"] == "se_abcd1234"


def test_report_event_records_search_id_and_ratings(journal):
    arguments = {"search_id": "se_abcd1234", "ratings": [{"m_id": "a", "rating": 8},
                                                         {"m_id": "b", "rating": -3}]}
    audit.record("report", arguments, {"recorded": True, "count": 2}, 1.0)
    ev = _read_lines(journal)[0]
    assert ev["tool"] == "report"
    assert ev["status"] == "ok"
    assert ev["params"] == arguments          # no content fields to trim
    assert ev["result"] == {"recorded": True, "count": 2}


def test_get_details_summary_keywords(journal):
    result = {"found": [{"id": "a", "summary": "s1",
                         "metadata": {"keywords": ["kw1"], "categories": ["bugfix"]},
                         "text": "BODY"}],
              "missing": ["zzz"]}
    audit.record("get_details", {"memory_ids": ["a", "zzz"]}, result, 1.0)
    ev = _read_lines(journal)[0]
    assert ev["result"]["found"] == [{"id": "a", "summary": "s1",
                                      "keywords": ["kw1"], "categories": ["bugfix"]}]
    assert "text" not in ev["result"]["found"][0]
    assert ev["result"]["missing"] == ["zzz"]


def test_stats_drops_keyword_counts_and_base_url(journal):
    result = {"total": 10, "namespace_counts": {"default": 10},
              "keyword_counts": {"kw": 3}, "embed_model": "bge-m3",
              "summarize_model": "qwen3:latest", "ollama_base_url": "http://localhost:11434",
              "server_time": "2026-08-18T00:00:00+00:00"}
    audit.record("stats", {}, result, 5.0)
    ev = _read_lines(journal)[0]
    assert "keyword_counts" not in ev["result"]
    assert "ollama_base_url" not in ev["result"]
    assert ev["result"]["total"] == 10


def test_transfer_audit_drops_paths_and_imported_ids(journal):
    audit.record(
        "memory_import",
        {"filename": "backup.jsonl", "path": "/secret/upload", "sha256": "abc"},
        {"imported": 1, "imported_ids": ["one"], "path": "/secret/result",
         "source_path": "/secret/replica"},
        5.0,
    )
    event = _read_lines(journal)[0]
    assert event["params"] == {"filename": "backup.jsonl", "sha256": "abc"}
    assert event["result"] == {"imported": 1}


# -- error path --

def test_record_error_event(journal):
    audit.record("delete", {"memory_id": "abc"}, None, 1.0,
                 error={"type": "ValueError", "message": "boom"})
    ev = _read_lines(journal)[0]
    assert ev["status"] == "error"
    assert ev["result"] is None
    assert ev["error"] == {"type": "ValueError", "message": "boom"}


# -- rotation --

def test_count_rotation_continues_after_max(journal, monkeypatch):
    monkeypatch.setattr(audit, "AUDIT_MAX_EVENTS", 3)
    for i in range(5):
        audit.record("stats", {}, {"total": i}, 1.0)
    assert len(_read_lines(journal)) == 2
    assert len(_read_lines(Path(f"{journal}.1"))) == 3
    assert [event["result"]["total"] for event in audit.read_events()] == [4, 3, 2, 1, 0]


def test_size_rotation_uses_utf8_bytes_and_keeps_large_event(journal, monkeypatch):
    monkeypatch.setattr(audit, "AUDIT_MAX_BYTES", 1)
    audit.record("stats", {}, {"value": "中文"}, 1.0)
    audit.record("stats", {}, {"value": "next"}, 1.0)

    assert len(_read_lines(journal)) == 1
    assert _read_lines(journal)[0]["result"]["value"] == "next"
    assert _read_lines(Path(f"{journal}.1"))[0]["result"]["value"] == "中文"


def test_rotation_discards_oldest_file(journal, monkeypatch):
    monkeypatch.setattr(audit, "AUDIT_MAX_EVENTS", 1)
    monkeypatch.setattr(audit, "AUDIT_MAX_FILES", 3)
    for i in range(5):
        audit.record("stats", {}, {"total": i}, 1.0)

    assert [event["result"]["total"] for event in audit.read_events()] == [4, 3, 2]
    assert Path(f"{journal}.2").exists()
    assert not Path(f"{journal}.3").exists()


def test_rotation_failure_does_not_stop_future_writes(journal, monkeypatch):
    monkeypatch.setattr(audit, "AUDIT_MAX_EVENTS", 1)
    audit.record("stats", {}, {"total": 1}, 1.0)

    def fail_replace(*args):
        raise OSError("rename failed")

    monkeypatch.setattr(audit.os, "replace", fail_replace)
    audit.record("stats", {}, {"total": 2}, 1.0)
    audit.record("stats", {}, {"total": 3}, 1.0)

    assert [event["result"]["total"] for event in audit.read_events()] == [3, 2, 1]


# -- read_events --

def test_read_events_newest_first_with_filters(journal):
    audit.record("add", {"namespace": "ns1", "text": "x"}, {"id": "1", "created": True}, 1.0)
    audit.record("delete", {"memory_id": "1"}, {"success": True}, 1.0)
    audit.record("add", {"namespace": "ns2", "text": "y"}, {"id": "2", "created": True}, 1.0)

    all_events = audit.read_events(limit=100)
    assert [e["tool"] for e in all_events] == ["add", "delete", "add"]  # newest first

    adds = audit.read_events(limit=100, tool="add")
    assert len(adds) == 2
    assert all(e["tool"] == "add" for e in adds)

    ns2 = audit.read_events(limit=100, namespace="ns2")
    assert len(ns2) == 1
    assert ns2[0]["result"]["id"] == "2"

    limited = audit.read_events(limit=2)
    assert len(limited) == 2


def test_read_events_filters_across_rotated_files(journal, monkeypatch):
    monkeypatch.setattr(audit, "AUDIT_MAX_EVENTS", 1)
    audit.record("add", {"namespace": "old", "text": "x"},
                 {"id": "1", "created": True}, 1.0)
    audit.record("delete", {"namespace": "new", "memory_id": "1"},
                 {"success": True}, 1.0)

    assert [event["tool"] for event in audit.read_events()] == ["delete", "add"]
    assert [event["tool"] for event in audit.read_events(tool="add")] == ["add"]
    assert [event["namespace"] for event in audit.read_events(namespace="old")] == ["old"]


def test_read_events_skips_corrupt_lines(journal):
    journal.write_text('{"tool": "add", "status": "ok", "result": {}}\nNOT_JSON\n{"tool": "stats"}\n',
                       encoding="utf-8")
    events = audit.read_events(limit=100)
    assert len(events) == 2  # corrupt line skipped


def test_read_events_missing_file(journal):
    assert audit.read_events(limit=10) == []
