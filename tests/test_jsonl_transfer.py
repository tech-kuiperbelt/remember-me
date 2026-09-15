"""Strict JSONL import/export contract tests."""

import json

import pytest

from app.jsonl_transfer import inspect_jsonl, load_jsonl, write_clean_jsonl


def _record(memory_id="one", text="body", updated="2026-01-02T00:00:00+00:00"):
    return {
        "id": memory_id, "text": text, "metadata": {}, "namespace": "ns",
        "created_at": "2026-01-01T00:00:00+00:00", "updated_at": updated,
        "summary": None,
    }


def _write_events(path, events):
    path.write_text("".join(json.dumps(event) + "\n" for event in events),
                    encoding="utf-8")


def test_history_uses_newest_timestamp_and_file_order_for_ties(tmp_path):
    path = tmp_path / "history.jsonl"
    _write_events(path, [
        _record(text="new", updated="2026-01-03T00:00:00+00:00"),
        {"id": "one", "deleted": True,
         "updated_at": "2026-01-02T00:00:00+00:00"},
        _record(text="tie-last", updated="2026-01-03T00:00:00+00:00"),
    ])

    assert load_jsonl(path)["records"][0]["text"] == "tie-last"


def test_newest_tombstone_removes_record(tmp_path):
    path = tmp_path / "deleted.jsonl"
    _write_events(path, [
        _record(),
        {"id": "one", "deleted": True,
         "updated_at": "2026-01-04T00:00:00+00:00"},
    ])
    assert load_jsonl(path)["records"] == []


@pytest.mark.parametrize("content, message", [
    ("{bad json}\n", "invalid JSON"),
    (json.dumps({"id": "one", "text": "body"}) + "\n", "missing namespace"),
    (json.dumps({**_record(), "metadata": []}) + "\n", "metadata must be an object"),
    (json.dumps({**_record(), "updated_at": "yesterday"}) + "\n", "invalid updated_at"),
])
def test_strict_reader_rejects_malformed_input(tmp_path, content, message):
    path = tmp_path / "bad.jsonl"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_jsonl(path)


def test_record_limit_applies_to_final_live_snapshot(tmp_path):
    path = tmp_path / "events.jsonl"
    _write_events(path, [
        _record("one"), _record("two"),
        {"id": "two", "deleted": True,
         "updated_at": "2026-01-04T00:00:00+00:00"},
    ])
    assert len(load_jsonl(path, max_records=1)["records"]) == 1


def test_clean_writer_omits_internal_fields_and_round_trips(tmp_path):
    path = tmp_path / "clean.jsonl"
    record = {**_record(), "embedding": [1.0], "synced": 1, "deleted": False}
    write_clean_jsonl([record], path)

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "embedding" not in raw
    assert "synced" not in raw
    assert "deleted" not in raw
    assert load_jsonl(path)["records"] == [_record()]


def test_tolerant_inspection_counts_every_corrupt_line(tmp_path):
    path = tmp_path / "mixed.jsonl"
    path.write_text(json.dumps(_record()) + "\n{bad}\n[]\n", encoding="utf-8")

    result = inspect_jsonl(path)

    assert len(result["records"]) == 1
    assert result["corrupt_lines"] == 2


def test_strict_reader_rejects_invalid_utf8(tmp_path):
    path = tmp_path / "bad-utf8.jsonl"
    path.write_bytes(b"\xff\xfe\n")
    with pytest.raises(UnicodeDecodeError):
        load_jsonl(path)
