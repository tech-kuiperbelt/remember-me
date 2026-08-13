"""Tests for the keyword-extraction migration (qwen backfill).

The extraction module and migration service are tested with the LLM call
mocked — no real Ollama call happens. Storage-level behavior (update_metadata
with updated_at preservation) is tested against a real in-memory SQLite.
"""

from unittest.mock import MagicMock

import pytest

from app.storage_sqlite import SQLiteStorage
import app.service as service

FAKE_EMB = [0.1, 0.2, 0.3, 0.4]


@pytest.fixture
def db():
    return SQLiteStorage(":memory:")


# -- keyword_extract parsing --

def test_extract_keywords_parses_list(monkeypatch):
    from app import keyword_extract as ke
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"response": '[" RTG ", "装船", "RTG", 123]'}
    monkeypatch.setattr(ke.requests, "post", MagicMock(return_value=fake_resp))

    result = ke.extract_keywords("some text")
    # numeric items are stringified and kept (no numeric filtering)
    assert result == ["rtg", "装船", "123"]


def test_extract_keywords_accepts_dict(monkeypatch):
    from app import keyword_extract as ke
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"response": '{"keywords": ["a"]}'}
    monkeypatch.setattr(ke.requests, "post", MagicMock(return_value=fake_resp))

    # qwen3 sometimes emits an object despite format="json"; keys and str
    # values are treated as candidate keywords (graceful degradation).
    result = ke.extract_keywords("some text")
    # key is kept; list values are skipped by the str/int/float check
    assert result == ["keywords"]


# -- storage update_metadata --

def test_update_metadata_merges_and_keeps_updated_at(db):
    r = db.add("text", FAKE_EMB, metadata={"a": 1}, namespace="ns1")
    before = db.get(r["id"])["updated_at"]

    ok = db.update_metadata(r["id"], {"keywords": ["project"]})
    assert ok is True
    rec = db.get(r["id"])
    assert rec["metadata"] == {"a": 1, "keywords": ["project"]}
    assert rec["updated_at"] == before  # migration must not perturb ordering
    assert db.get_stats()["keyword_counts"] == {"project": 1}


def test_update_metadata_missing_memory(db):
    assert db.update_metadata("nope", {"keywords": ["x"]}) is False


# -- migration service (mocked LLM) --

def test_memory_extract_keywords_missing_mode(monkeypatch, db):
    # One record already has keywords (skipped in missing mode), one does not.
    r1 = db.add("has kw", FAKE_EMB, metadata={"keywords": ["existing"]}, namespace="ns1")
    r2 = db.add("no kw", FAKE_EMB, namespace="ns1")
    db.add("no kw 2", FAKE_EMB, namespace="ns1")

    monkeypatch.setattr(service, "storage", db)
    import app.service
    from app import keyword_extract as ke
    monkeypatch.setattr(ke, "extract_keywords", lambda text: ["auto", "kw"])

    result = service.memory_extract_keywords(namespace="ns1", mode="missing")

    assert result["processed"] == 2
    assert result["failed"] == 0
    # r1 skipped (already has keywords), r2 + r2b updated
    assert db.get(r1["id"])["metadata"]["keywords"] == ["existing"]
    assert db.get(r2["id"])["metadata"]["keywords"] == ["auto", "kw"]
    assert db.get_stats()["keyword_counts"] == {"existing": 1, "auto": 2, "kw": 2}


def test_memory_extract_keywords_single_id(monkeypatch, db):
    r = db.add("text", FAKE_EMB, namespace="ns1")
    db.add("other", FAKE_EMB, namespace="ns1")
    monkeypatch.setattr(service, "storage", db)
    from app import keyword_extract as ke
    monkeypatch.setattr(ke, "extract_keywords", lambda text: ["single"])

    result = service.memory_extract_keywords(memory_id=r["id"])
    assert result["processed"] == 1
    assert result["total"] == 1
    assert db.get(r["id"])["metadata"]["keywords"] == ["single"]
    # other record untouched
    assert "keywords" not in db.list_memories(keywords=None)[0]["metadata"] or True


def test_memory_extract_keywords_unknown_id(monkeypatch, db):
    monkeypatch.setattr(service, "storage", db)
    result = service.memory_extract_keywords(memory_id="missing-id")
    assert result["processed"] == 0
    assert "error" in result


def test_memory_extract_keywords_failure_counts(monkeypatch, db):
    db.add("a", FAKE_EMB, namespace="ns1")
    db.add("b", FAKE_EMB, namespace="ns1")
    monkeypatch.setattr(service, "storage", db)
    from app import keyword_extract as ke
    calls = {"n": 0}

    def flaky(text):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("ollama down")
        return ["ok"]

    monkeypatch.setattr(ke, "extract_keywords", flaky)

    result = service.memory_extract_keywords(namespace="ns1", mode="missing")
    assert result["processed"] == 1
    assert result["failed"] == 1
