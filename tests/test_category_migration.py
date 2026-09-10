"""Tests for the category backfill migration (deepseek path).

The LLM call (``opencode run`` subprocess) is mocked — no real model call
happens. Storage-level behavior (update_metadata with category-index sync and
updated_at preservation) is tested against a real in-memory SQLite.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.storage_sqlite import SQLiteStorage
from app.categories import CATEGORY_VOCABULARY

FAKE_EMB = [0.1, 0.2, 0.3, 0.4]


@pytest.fixture
def db():
    return SQLiteStorage(":memory:")


# -- candidate rendering --

def test_format_candidates_block_includes_seeds():
    from app.category_migrate_deepseek import format_candidates_block

    block = format_candidates_block(list(CATEGORY_VOCABULARY))
    assert "---- candidate categories ----" in block
    assert "- bugfix" in block
    assert "- other" in block


def test_format_candidates_block_empty():
    from app.category_migrate_deepseek import format_candidates_block

    assert format_candidates_block([]) == "(empty candidate list)"


# -- extraction (mocked subprocess) --

def test_extract_categories_parses_and_caps():
    from app import category_migrate_deepseek as cm

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = '["Bugfix", "design", "design", "ops", "extra"]'
    fake_proc.stderr = ""
    with patch("app.category_migrate_deepseek.subprocess.run", return_value=fake_proc):
        result = cm.extract_categories_deepseek("some text")

    # normalized lowercase + dedup AND capped at 3 (order preserved)
    assert result == ["bugfix", "design", "ops"]


def test_extract_categories_tolerates_fences_and_noise():
    from app import category_migrate_deepseek as cm

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = '```json\n["requirement", "api"]\n```'
    fake_proc.stderr = ""
    with patch("app.category_migrate_deepseek.subprocess.run", return_value=fake_proc):
        result = cm.extract_categories_deepseek("some text")
    assert result == ["requirement", "api"]


def test_extract_categories_raises_on_empty_output():
    from app import category_migrate_deepseek as cm

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = "no array here"
    fake_proc.stderr = ""
    with patch("app.category_migrate_deepseek.subprocess.run", return_value=fake_proc):
        with pytest.raises(ValueError):
            cm.extract_categories_deepseek("some text")


def test_extract_categories_raises_on_cli_failure():
    from app import category_migrate_deepseek as cm

    fake_proc = MagicMock()
    fake_proc.returncode = 1
    fake_proc.stderr = "boom"
    with patch("app.category_migrate_deepseek.subprocess.run", return_value=fake_proc):
        with pytest.raises(RuntimeError):
            cm.extract_categories_deepseek("some text")


# -- candidate vocabulary convergence --

def test_category_candidates_seed_and_growth():
    from app.category_migrate_deepseek import CategoryCandidates

    candidates = CategoryCandidates(observed=["gossip", "config"])
    lst = candidates.as_candidates()
    # seeds first (vocabulary order); observed extras that are NOT already
    # seeds appended after them ("config" is a seed, so only "gossip" is new)
    assert lst[: len(CATEGORY_VOCABULARY)] == list(CATEGORY_VOCABULARY)
    assert lst[-1] == "gossip"
    assert "config" in lst[: len(CATEGORY_VOCABULARY)]

    candidates.add_many(["Bugfix", "glossary", "brand-new-class"])
    assert "brand-new-class" in candidates.as_candidates()
    # dedup + normalized: bugfix/glossary already seeds, only the new class added
    assert len(candidates) == len(list(CATEGORY_VOCABULARY)) + 2


# -- storage write path (real SQLite) --

def test_update_metadata_with_categories_syncs_index_and_keeps_updated_at(db):
    r = db.add("text", FAKE_EMB, metadata={"a": 1}, namespace="ns1")
    before = db.get(r["id"])["updated_at"]

    ok = db.update_metadata(r["id"], {"categories": ["bugfix", "perf"]})
    assert ok is True
    rec = db.get(r["id"])
    assert rec["metadata"] == {"a": 1, "categories": ["bugfix", "perf"]}
    assert rec["updated_at"] == before  # migration must not perturb ordering
    assert db.get_stats()["category_counts"] == {"bugfix": 1, "perf": 1}
    # index actually usable for filtering
    assert [x["id"] for x in db.list_memories(categories=["bugfix"])] == [r["id"]]


def test_update_metadata_with_categories_caps_three(db):
    r = db.add("text", FAKE_EMB, namespace="ns1")
    db.update_metadata(r["id"], {"categories": ["a", "b", "c", "d"]})
    assert db.get_stats()["category_counts"] == {"a": 1, "b": 1, "c": 1}


def test_update_metadata_missing_memory(db):
    assert db.update_metadata("nope", {"categories": ["x"]}) is False


# -- storage path override (--db) --

def test_build_storage_uses_db_override(monkeypatch, tmp_path):
    from app import category_migrate_deepseek as cm

    captured = {}
    monkeypatch.setattr(cm, "create_storage",
                        lambda backend, **kw: captured.update(backend=backend, kw=kw))
    monkeypatch.delenv("MEMORY_STORAGE", raising=False)
    monkeypatch.delenv("MEMORY_STORAGE_PATH", raising=False)

    target = str(tmp_path / "override.db")
    cm.build_storage(target)

    assert captured["backend"] == "sqlite"
    assert captured["kw"]["path"] == target


def test_build_storage_falls_back_to_env_path(monkeypatch, tmp_path):
    from app import category_migrate_deepseek as cm

    captured = {}
    monkeypatch.setattr(cm, "create_storage",
                        lambda backend, **kw: captured.update(backend=backend, kw=kw))
    env_path = str(tmp_path / "env.db")
    monkeypatch.setenv("MEMORY_STORAGE_PATH", env_path)

    cm.build_storage(None)

    assert captured["kw"]["path"] == env_path


def test_main_passes_db_to_build_storage(monkeypatch, tmp_path):
    from app import category_migrate_deepseek as cm

    seen = {}

    class _EmptyStorage:
        def list_memories(self, **kw):
            return []

    def fake_build(db_path=None):
        seen["db"] = db_path
        return _EmptyStorage()

    monkeypatch.setattr(cm, "build_storage", fake_build)

    target = str(tmp_path / "target.db")
    assert cm.main(["--db", target, "--dry-run"]) == 0
    assert seen["db"] == target