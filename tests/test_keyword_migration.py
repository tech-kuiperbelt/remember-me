"""Keyword metadata persistence and offline migration boundaries."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.storage_sqlite import SQLiteStorage
from app.keyword_migrate_deepseek import (
    extract_keywords_deepseek,
    format_candidates_block,
)


FAKE_EMB = [0.1, 0.2, 0.3, 0.4]


def test_update_metadata_merges_keywords_without_changing_updated_at():
    db = SQLiteStorage(":memory:")
    result = db.add("text", FAKE_EMB, metadata={"a": 1}, namespace="ns1")
    before = db.get(result["id"])["updated_at"]

    assert db.update_metadata(result["id"], {"keywords": ["project"]}) is True
    rec = db.get(result["id"])
    assert rec["metadata"] == {"a": 1, "keywords": ["project"]}
    assert rec["updated_at"] == before
    assert db.get_stats()["keyword_counts"] == {"project": 1}


def test_update_metadata_missing_memory_returns_false():
    assert SQLiteStorage(":memory:").update_metadata("nope", {"keywords": ["x"]}) is False


def test_deepseek_cli_remains_explicit_offline_keyword_backfill(monkeypatch):
    from app import keyword_migrate_deepseek as migration
    run = MagicMock(return_value=SimpleNamespace(returncode=0, stdout='[" RTG ", "装船", "RTG"]', stderr=""))
    monkeypatch.setattr(migration.subprocess, "run", run)

    assert extract_keywords_deepseek("body", candidates=["rtg"]) == ["rtg", "装船"]
    assert run.call_args.args[0][:3] == ["opencode", "run", "--model"]
    assert "candidate keywords" in format_candidates_block(["rtg"])


def test_local_keyword_extractor_api_and_ui_are_removed(service, admin):
    from app import service as service_policy
    admin_routes = admin.routes

    assert not hasattr(service, "memory_extract_keywords")
    assert all(route.path != "/api/extract-keywords" for route in admin_routes)
    assert not Path("app/keyword_extract.py").exists()
    ui = Path("web/index.html").read_text(encoding="utf-8")
    assert "doExtractKeywords" not in ui
    assert "btn-kw" not in ui
