"""Offline anchor backfill v2: candidate pairing, batch manifest, single-transaction apply."""
import argparse
import json
from pathlib import Path

import pytest

from app import anchor_migrate_deepseek as am
from app.storage_sqlite import SQLiteStorage

EMB = [0.1, 0.2, 0.3]


# --------------------------------------------------------------------------
# Candidate pairing
# --------------------------------------------------------------------------

def _note(anchor, rationale="reason", query="a query"):
    return {"anchor": anchor, "rationale": rationale, "query": query}


def test_select_anchors_pairs_notes_and_drops_unnoted():
    paired = am.select_anchors(["Foo", "Bar"], [_note("Foo")])
    assert [a for a, _ in paired] == ["Foo"]
    assert paired[0][1]["rationale"] == "reason"


def test_select_anchors_requires_non_blank_note_fields():
    assert am.select_anchors(["Foo"], [_note("Foo", rationale=" ")]) == []
    assert am.select_anchors(["Foo"], [_note("Foo", query="")]) == []


def test_select_anchors_allows_grounded_aliases_absent_from_text():
    # No literal-substring gate: an alias with a grounding note is kept.
    paired = am.select_anchors(["cache layer"], [_note("cache layer")])
    assert [a for a, _ in paired] == ["cache layer"]


def test_select_anchors_empty_inputs():
    assert am.select_anchors([], []) == []
    assert am.select_anchors(None, None) == []


def test_ids_digest_is_order_independent():
    assert am._ids_digest(["b", "a"]) == am._ids_digest(["a", "b"])
    assert am._ids_digest(["a"]).startswith("sha256:")


def test_extract_json_array_tolerates_fences_and_prose():
    out = "noise ```json\n[{\"id\": \"X\", \"anchors\": []}]\n``` tail"
    assert am._extract_json_array(out)[0]["id"] == "X"
    assert am._extract_json_array("no array") is None


def test_extract_json_array_keeps_multi_object_array_in_prose():
    out = 'noise [{"id": "m1", "anchors": ["a"]}, {"id": "m2", "anchors": ["b"]}] tail'
    assert [item["id"] for item in am._extract_json_array(out)] == ["m1", "m2"]


def test_write_outcome_stays_under_data_root(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("MEMORY_DATA_DIR", str(root))
    result = am._write_outcome("outcome.jsonl", {"stage": "apply"})
    assert Path(result).parent == root / "anchor-backfill"
    assert Path(result).exists()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _draft_args(**kw):
    base = dict(stage="draft", namespace="default", limit=None, manifest=None,
                model="m", cli="c", timeout=1.0, dry_run=False, db=None,
                exclude_manifest=None, batch_size=10)
    base.update(kw)
    return argparse.Namespace(**base)


def _apply_args(manifest, **kw):
    base = dict(stage="apply", manifest=str(manifest), namespace=None,
                dry_run=False, db=None)
    base.update(kw)
    return argparse.Namespace(**base)


def _seed(path, records):
    st = SQLiteStorage(str(path))
    ids = []
    for rec in records:
        r = st.add(rec["text"], EMB, metadata=rec.get("metadata"),
                   namespace=rec.get("namespace", "default"))
        ids.append(r["id"])
    st.close()
    return ids


def _load(path, mid):
    st = SQLiteStorage(str(path))
    try:
        return st.get(mid)
    finally:
        st.close()


def _row(mid, ns="default", status="generated", anchors=None, text="",
         content_hash=None, with_notes=True):
    anchors = anchors or []
    notes = [_note(a) for a in anchors] if with_notes else []
    return {
        "type": "row", "schema_version": am.SCHEMA_VERSION, "memory_id": mid,
        "namespace": ns,
        "content_hash": content_hash if content_hash is not None else am._content_hash(text),
        "content_hash_algo": am.CONTENT_HASH_ALGO,
        "text_preview": (text or "")[:am.PREVIEW_CHARS],
        "proposed_anchors": anchors, "anchor_notes": notes,
        "status": status, "error": None, "model": "m", "cli": "c",
        "drafted_at": "2026-09-28T00:00:00+00:00",
    }


def _header(ids, ns="default"):
    return {"type": "header", "schema_version": am.SCHEMA_VERSION, "namespace": ns,
            "selected": len(ids), "ids_digest": am._ids_digest(ids),
            "model": "m", "cli": "c", "created_at": "2026-09-28T00:00:00+00:00"}


def _trailer(rows):
    ids = [r["memory_id"] for r in rows]
    return {"type": "trailer", "schema_version": am.SCHEMA_VERSION, "selected": len(ids),
            "ids_digest": am._ids_digest(ids),
            "generated": sum(1 for r in rows if r["status"] == "generated"),
            "skip": sum(1 for r in rows if r["status"] == "skip"),
            "completed_at": "2026-09-28T00:00:00+00:00"}


def _write_manifest(path, rows, trailer=True):
    entries = [_header([r["memory_id"] for r in rows])] + rows
    if trailer:
        entries.append(_trailer(rows))
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------
# Draft stage
# --------------------------------------------------------------------------

def test_draft_writes_header_rows_trailer_and_leaves_db_untouched(tmp_path, monkeypatch, capsys):
    db = tmp_path / "mem.db"
    ids = _seed(db, [{"text": "RedisStore powers cache"},
                     {"text": "plain prose"}])
    before = {mid: _load(db, mid)["updated_at"] for mid in ids}

    def fake(memories, model=None, cli=None, timeout=None):
        out = {}
        for mid, text in memories:
            if "RedisStore" in text:
                out[mid] = {"anchors": ["RedisStore"], "anchor_notes": [_note("RedisStore")]}
            else:
                out[mid] = {"anchors": [], "anchor_notes": []}
        return out

    monkeypatch.setattr(am, "extract_anchors_batch_deepseek", fake)
    manifest = tmp_path / "draft.jsonl"
    assert am.run_draft(_draft_args(db=str(db), manifest=str(manifest))) == 0

    lines = [json.loads(l) for l in manifest.read_text().splitlines()]
    assert lines[0]["type"] == "header" and lines[0]["selected"] == 2
    assert lines[-1]["type"] == "trailer" and lines[-1]["generated"] == 1 and lines[-1]["skip"] == 1
    rows = {l["memory_id"]: l for l in lines if l["type"] == "row"}
    assert rows[ids[0]]["status"] == "generated"
    assert rows[ids[0]]["anchor_notes"][0]["anchor"] == "RedisStore"
    assert rows[ids[1]]["status"] == "skip"

    for mid in ids:
        assert "anchors" not in _load(db, mid)["metadata"]
        assert _load(db, mid)["updated_at"] == before[mid]
    assert json.loads(capsys.readouterr().out)["complete"] is True


def test_draft_error_row_leaves_manifest_incomplete(tmp_path, monkeypatch):
    db = tmp_path / "mem.db"
    _seed(db, [{"text": "one"}])

    def boom(memories, model=None, cli=None, timeout=None):
        raise RuntimeError("model down")

    monkeypatch.setattr(am, "extract_anchors_batch_deepseek", boom)
    manifest = tmp_path / "draft.jsonl"
    assert am.run_draft(_draft_args(db=str(db), manifest=str(manifest))) == 2
    kinds = [json.loads(l)["type"] for l in manifest.read_text().splitlines()]
    assert "trailer" not in kinds
    # Incomplete manifests must be rejected by apply.
    assert am.run_apply(_apply_args(manifest, db=str(db))) == 2


def test_draft_resume_compacts_and_reads_trailer(tmp_path, monkeypatch):
    db = tmp_path / "mem.db"
    ids = _seed(db, [{"text": "RedisStore one"}, {"text": "RedisStore two"}])
    manifest = tmp_path / "draft.jsonl"
    # Incomplete manifest: header declares both ids, but only the first row exists.
    with open(manifest, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(_header(ids), ensure_ascii=False) + "\n")
        fh.write(json.dumps(_row(ids[0], anchors=["RedisStore"], text="RedisStore one"),
                            ensure_ascii=False) + "\n")

    def fake(memories, model=None, cli=None, timeout=None):
        return {mid: {"anchors": ["RedisStore"], "anchor_notes": [_note("RedisStore")]}
                for mid, _text in memories}

    monkeypatch.setattr(am, "extract_anchors_batch_deepseek", fake)
    assert am.run_draft(_draft_args(db=str(db), manifest=str(manifest))) == 0
    lines = [json.loads(l) for l in manifest.read_text().splitlines()]
    assert lines[0]["type"] == "header"
    assert lines[-1]["type"] == "trailer"
    row_ids = [l["memory_id"] for l in lines if l["type"] == "row"]
    assert sorted(row_ids) == sorted(ids)
    assert len(row_ids) == len(set(row_ids))
    # Resuming a completed manifest is a no-op.
    assert am.run_draft(_draft_args(db=str(db), manifest=str(manifest))) == 0


def test_draft_dry_run_writes_no_manifest(tmp_path):
    db = tmp_path / "mem.db"
    _seed(db, [{"text": "hello"}])
    manifest = tmp_path / "draft.jsonl"
    assert am.run_draft(_draft_args(db=str(db), manifest=str(manifest), dry_run=True)) == 0
    assert not manifest.exists()


def test_draft_exclude_manifest_advances_to_next_batch(tmp_path, monkeypatch):
    db = tmp_path / "mem.db"
    ids = _seed(db, [{"text": "RedisStore one"}, {"text": "RedisStore two"},
                     {"text": "RedisStore three"}])

    def fake(memories, model=None, cli=None, timeout=None):
        return {mid: {"anchors": ["RedisStore"], "anchor_notes": [_note("RedisStore")]}
                for mid, _text in memories}

    monkeypatch.setattr(am, "extract_anchors_batch_deepseek", fake)

    def row_ids(path):
        return {json.loads(l)["memory_id"]
                for l in path.read_text().splitlines()
                if json.loads(l).get("type") == "row"}

    first = tmp_path / "b1.jsonl"
    assert am.run_draft(_draft_args(db=str(db), manifest=str(first), limit=2)) == 0
    assert len(row_ids(first)) == 2

    # Next batch excludes ids already recorded in the first manifest, so it
    # advances without applying anything.
    second = tmp_path / "b2.jsonl"
    assert am.run_draft(_draft_args(db=str(db), manifest=str(second), limit=2,
                                    exclude_manifest=[str(first)])) == 0
    assert len(row_ids(second)) == 1
    assert row_ids(second).isdisjoint(row_ids(first))
    assert row_ids(first) | row_ids(second) == set(ids)


def test_draft_missing_exclude_manifest_fails(tmp_path):
    db = tmp_path / "mem.db"
    _seed(db, [{"text": "RedisStore one"}])
    manifest = tmp_path / "draft.jsonl"
    rc = am.run_draft(_draft_args(db=str(db), manifest=str(manifest),
                                  exclude_manifest=[str(tmp_path / "nope.jsonl")]))
    assert rc == 2


def test_draft_batch_size_groups_model_calls(tmp_path, monkeypatch, capsys):
    db = tmp_path / "mem.db"
    _seed(db, [{"text": "RedisStore 1"}, {"text": "RedisStore 2"}, {"text": "RedisStore 3"}])
    calls = []

    def fake(memories, model=None, cli=None, timeout=None):
        calls.append(len(memories))
        return {mid: {"anchors": ["RedisStore"], "anchor_notes": [_note("RedisStore")]}
                for mid, _text in memories}

    monkeypatch.setattr(am, "extract_anchors_batch_deepseek", fake)
    manifest = tmp_path / "m.jsonl"
    assert am.run_draft(_draft_args(db=str(db), manifest=str(manifest), batch_size=2)) == 0
    assert calls == [2, 1]
    report = json.loads(capsys.readouterr().out)
    assert report["model_calls"] == 2 and report["generated"] == 3


def test_draft_missing_id_in_model_output_is_error(tmp_path, monkeypatch):
    db = tmp_path / "mem.db"
    _seed(db, [{"text": "RedisStore 1"}, {"text": "RedisStore 2"}])

    monkeypatch.setattr(am, "extract_anchors_batch_deepseek",
                        lambda memories, **k: {})
    manifest = tmp_path / "m.jsonl"
    assert am.run_draft(_draft_args(db=str(db), manifest=str(manifest))) == 2
    rows = [json.loads(l) for l in manifest.read_text().splitlines() if json.loads(l)["type"] == "row"]
    assert all(r["status"] == "error" and "missing from model output" in r["error"] for r in rows)


# --------------------------------------------------------------------------
# Storage: single-transaction batch
# --------------------------------------------------------------------------

def test_update_anchors_batch_commits_and_preserves_updated_at(tmp_path):
    db = tmp_path / "mem.db"
    ids = _seed(db, [{"text": "a"}, {"text": "b"}])
    before = {mid: _load(db, mid)["updated_at"] for mid in ids}
    st = SQLiteStorage(str(db))
    try:
        assert st.update_anchors_batch([(ids[0], ["Alpha"]), (ids[1], ["Beta"])]) == 2
    finally:
        st.close()
    for mid in ids:
        rec = _load(db, mid)
        assert rec["updated_at"] == before[mid]
    assert _load(db, ids[0])["metadata"]["anchors"] == ["Alpha"]
    assert _load(db, ids[1])["metadata"]["anchors"] == ["Beta"]


def test_update_anchors_batch_rolls_back_entirely_on_failure(tmp_path):
    db = tmp_path / "mem.db"
    ids = _seed(db, [{"text": "a"}, {"text": "b"}])
    st = SQLiteStorage(str(db))
    try:
        with pytest.raises(RuntimeError):
            st.update_anchors_batch([(ids[0], ["Alpha"]), ("missing-id", ["Beta"])])
    finally:
        st.close()
    assert "anchors" not in _load(db, ids[0])["metadata"]
    assert "anchors" not in _load(db, ids[1])["metadata"]


# --------------------------------------------------------------------------
# Apply stage
# --------------------------------------------------------------------------

def test_apply_commits_batch_and_preserves_updated_at(tmp_path, capsys):
    db = tmp_path / "mem.db"
    ids = _seed(db, [{"text": "RedisStore alpha"}, {"text": "RedisStore beta"}])
    before = {mid: _load(db, mid)["updated_at"] for mid in ids}
    manifest = tmp_path / "m.jsonl"
    _write_manifest(manifest, [
        _row(ids[0], anchors=["RedisStore"], text="RedisStore alpha"),
        _row(ids[1], anchors=["RedisStore stores"], text="RedisStore beta"),
    ])
    assert am.run_apply(_apply_args(manifest, db=str(db))) == 0
    for mid in ids:
        assert _load(db, mid)["updated_at"] == before[mid]
    assert _load(db, ids[0])["metadata"]["anchors"] == ["RedisStore"]

    report = json.loads(capsys.readouterr().out)
    assert report["transaction"] == "committed" and report["applied"] == 2
    assert report["coverage_delta"]["memories_with_anchors"] == 2
    assert report["updated_at_mismatch"] == 0


def test_apply_rejects_incomplete_or_tampered_manifest(tmp_path):
    db = tmp_path / "mem.db"
    ids = _seed(db, [{"text": "RedisStore alpha"}])
    no_trailer = tmp_path / "no-trailer.jsonl"
    _write_manifest(no_trailer, [_row(ids[0], anchors=["RedisStore"], text="RedisStore alpha")],
                    trailer=False)
    assert am.run_apply(_apply_args(no_trailer, db=str(db))) == 2

    bad_digest = tmp_path / "bad-digest.jsonl"
    _write_manifest(bad_digest, [_row(ids[0], anchors=["RedisStore"], text="RedisStore alpha")])
    lines = bad_digest.read_text().splitlines()
    header = json.loads(lines[0]); header["ids_digest"] = "sha256:deadbeef"
    lines[0] = json.dumps(header, ensure_ascii=False)
    bad_digest.write_text("\n".join(lines) + "\n")
    assert am.run_apply(_apply_args(bad_digest, db=str(db))) == 2
    assert "anchors" not in _load(db, ids[0])["metadata"]


def test_apply_rejects_error_rows(tmp_path):
    db = tmp_path / "mem.db"
    ids = _seed(db, [{"text": "RedisStore alpha"}])
    manifest = tmp_path / "m.jsonl"
    _write_manifest(manifest, [_row(ids[0], status="error", text="RedisStore alpha")])
    assert am.run_apply(_apply_args(manifest, db=str(db))) == 2


def test_apply_skips_only_manifest_is_noop(tmp_path, capsys):
    db = tmp_path / "mem.db"
    ids = _seed(db, [{"text": "plain prose"}])
    manifest = tmp_path / "m.jsonl"
    _write_manifest(manifest, [_row(ids[0], status="skip", text="plain prose")])
    assert am.run_apply(_apply_args(manifest, db=str(db))) == 0
    assert json.loads(capsys.readouterr().out)["transaction"] == "noop"


def test_apply_aborts_whole_batch_on_stale_record(tmp_path):
    db = tmp_path / "mem.db"
    ids = _seed(db, [{"text": "RedisStore alpha"}, {"text": "RedisStore beta"}])
    manifest = tmp_path / "m.jsonl"
    _write_manifest(manifest, [
        _row(ids[0], anchors=["RedisStore"], text="RedisStore alpha"),
        _row(ids[1], anchors=["RedisStore"], text="RedisStore beta", content_hash="deadbeef"),
    ])
    assert am.run_apply(_apply_args(manifest, db=str(db))) == 2
    assert "anchors" not in _load(db, ids[0])["metadata"]
    assert "anchors" not in _load(db, ids[1])["metadata"]


def test_apply_aborts_on_already_anchored_conflict(tmp_path):
    db = tmp_path / "mem.db"
    ids = _seed(db, [{"text": "RedisStore alpha", "metadata": {"anchors": ["Other"]}}])
    manifest = tmp_path / "m.jsonl"
    _write_manifest(manifest, [_row(ids[0], anchors=["RedisStore"], text="RedisStore alpha")])
    assert am.run_apply(_apply_args(manifest, db=str(db))) == 2
    assert _load(db, ids[0])["metadata"]["anchors"] == ["Other"]


def test_apply_all_already_applied_is_noop(tmp_path, capsys):
    db = tmp_path / "mem.db"
    ids = _seed(db, [{"text": "RedisStore alpha", "metadata": {"anchors": ["RedisStore"]}}])
    manifest = tmp_path / "m.jsonl"
    _write_manifest(manifest, [_row(ids[0], anchors=["RedisStore"], text="RedisStore alpha")])
    assert am.run_apply(_apply_args(manifest, db=str(db))) == 0
    assert json.loads(capsys.readouterr().out)["transaction"] == "noop"


def test_apply_mixed_already_applied_and_new_aborts(tmp_path):
    db = tmp_path / "mem.db"
    ids = _seed(db, [
        {"text": "RedisStore alpha", "metadata": {"anchors": ["RedisStore"]}},
        {"text": "RedisStore beta"},
    ])
    manifest = tmp_path / "m.jsonl"
    _write_manifest(manifest, [
        _row(ids[0], anchors=["RedisStore"], text="RedisStore alpha"),
        _row(ids[1], anchors=["RedisStore"], text="RedisStore beta"),
    ])
    assert am.run_apply(_apply_args(manifest, db=str(db))) == 2
    assert "anchors" not in _load(db, ids[1])["metadata"]


def test_apply_dry_run_writes_nothing(tmp_path):
    db = tmp_path / "mem.db"
    ids = _seed(db, [{"text": "RedisStore alpha"}])
    manifest = tmp_path / "m.jsonl"
    _write_manifest(manifest, [_row(ids[0], anchors=["RedisStore"], text="RedisStore alpha")])
    assert am.run_apply(_apply_args(manifest, db=str(db), dry_run=True)) == 0
    assert "anchors" not in _load(db, ids[0])["metadata"]
    assert not list(tmp_path.glob("apply-*.jsonl"))
