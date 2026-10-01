"""Offline anchor backfill for existing memories using deepseek-v4-flash.

Two explicit stages, per docs/project-management/plans/41-historical-anchor-backfill.md:

- ``draft`` is READ-ONLY. It reads the SQLite primary, asks the model for 0-5
  retrieval anchors per eligible record (live; ``metadata.anchors``
  missing/empty), and writes a review/restart manifest (JSONL). It never writes
  the database, takes no writer lock, and is safe to run while the service is up.
- ``apply`` is the only writing stage. It consumes a *complete* manifest and
  writes every generated row in ONE SQLite transaction, preserving
  ``updated_at``. It acquires the primary writer lock and therefore requires the
  service to be stopped.

Design:
- Automation, not approval: the manifest is an audit/restart artifact. There is
  no per-record human approval step.
- Anchors are retrieval cues, not necessarily literal quotes: the model may
  return grounded aliases, abbreviations, or likely query wording, and must
  attach one note (rationale + plausible query) per anchor. There is no literal
  substring gate.
- STRICTLY SERIAL, but batched: one ``opencode run`` call handles up to
  ``--batch-size`` (default 10) memories and returns one JSON object per memory.
  The CLI pays a large fixed startup cost per call, so batching is ~8x faster;
  memories are delimited in the prompt and results are matched back by id.
- A manifest is one atomic batch. Draft must complete every selected record
  (``generated`` or ``skip``) and append a trailer before apply accepts it; any
  ``error`` row or an interrupted run leaves it incomplete and inapplicable.
- Apply is all-or-nothing: any stale/conflicting record aborts the whole batch
  before a single write, and the batch commits once.
"""

import argparse
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timezone

from app.cli import deepseek_cli
from app.domain.anchors import MAX_ANCHORS, clean_anchors, normalize_anchors
from app.storage import create_storage, resolve_replica_path

logger = logging.getLogger("anchor_migrate_deepseek")

# Default model id as configured in ~/.config/opencode/opencode.jsonc
DEFAULT_MODEL = deepseek_cli.DEFAULT_MODEL
DEFAULT_CLI = deepseek_cli.DEFAULT_CLI
SCHEMA_VERSION = "anchor-manifest/2"
CONTENT_HASH_ALGO = "sha256/text-utf8"
PREVIEW_CHARS = 500

MANIFEST_TYPES = ("header", "row", "trailer")
ROW_STATUSES = ("generated", "error", "skip")
NOTE_FIELDS = ("anchor", "rationale", "query")
ROW_FIELDS = (
    "type", "schema_version", "memory_id", "namespace", "content_hash",
    "content_hash_algo", "text_preview", "proposed_anchors", "anchor_notes",
    "status", "error", "model", "cli", "drafted_at",
)
HEADER_FIELDS = (
    "type", "schema_version", "namespace", "selected", "ids_digest",
    "model", "cli", "created_at",
)
TRAILER_FIELDS = (
    "type", "schema_version", "selected", "ids_digest", "generated", "skip",
    "completed_at",
)

DEFAULT_BATCH_SIZE = 10
MEMORY_TEXT_CAP = 8000

BATCH_ANCHOR_PROMPT_TEMPLATE = (
    "You propose retrieval anchors for the memories below.\n"
    "Return ONLY a JSON array with exactly ONE object per memory, in the same "
    "order, shaped like "
    "{\"id\": \"<the given id>\", \"anchors\": [\"...\"], \"anchor_notes\": "
    "[{\"anchor\": \"...\", \"rationale\": \"...\", \"query\": \"...\"}]}.\n"
    "Rules:\n"
    "- Propose 0-%d anchors per memory.\n"
    "- An anchor is a distinctive retrieval cue grounded in that memory's own "
    "text: a literal identifier (name, class, API, error code, config key, "
    "version, file path, ticket id), or a grounded alias, abbreviation, or "
    "likely query wording.\n"
    "- Every anchor must be supported by that memory's meaning. Do NOT invent "
    "facts, do NOT return generic topic labels, and do NOT mix content across "
    "memories.\n"
    "- Preserve the canonical spelling of proper identifiers.\n"
    "- Return EXACTLY one note per anchor, with the same anchor string, a short "
    "rationale tied to that memory, and one plausible future query.\n"
    "- Copy each memory's id exactly as given.\n\n"
    "Memories:\n<<<MEMORIES>>>"
) % MAX_ANCHORS

_WHITESPACE = re.compile(r"\s+")


class ManifestError(ValueError):
    """The manifest is missing, malformed, tampered with, or incomplete."""


class _Manifest:
    def __init__(self, header, rows, trailer, first_type, last_type):
        self.header = header
        self.rows = rows
        self.trailer = trailer
        self.first_type = first_type
        self.last_type = last_type


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_for_match(text: str) -> str:
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", text or ""))


def _content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _text_preview(text: str) -> str:
    return _normalize_for_match(text)[:PREVIEW_CHARS]


def _ids_digest(memory_ids) -> str:
    body = "\n".join(sorted(memory_ids))
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _extract_json_array(output: str):
    """Parse the first JSON array found in ``output``; None when none parses."""
    return deepseek_cli.extract_json_array(output)


def extract_anchors_batch_deepseek(memories, model: str = DEFAULT_MODEL,
                                   cli: str = DEFAULT_CLI,
                                   timeout: float = 120.0) -> dict:
    """Ask the model for anchors for a batch of memories in ONE call.

    ``memories`` is a list of ``(memory_id, text)``. Returns a dict mapping
    ``memory_id -> {"anchors": [...], "anchor_notes": [...]}``. Raises on CLI
    failure, timeout, or unparseable output; ids absent from the response are
    omitted (the caller records them as ``error`` and retries on resume).
    """
    blocks = []
    for memory_id, text in memories:
        blocks.append(f"--- id={memory_id} ---")
        blocks.append((text or "")[:MEMORY_TEXT_CAP])
    prompt = BATCH_ANCHOR_PROMPT_TEMPLATE.replace("<<<MEMORIES>>>", "\n".join(blocks))
    stdout = deepseek_cli.run_opencode(
        prompt, instruction="Extract anchors for every memory. Output ONLY the JSON array.",
        model=model, cli=cli, timeout=timeout)
    parsed = _extract_json_array(stdout)
    if parsed is None:
        raise ValueError(f"no anchor array in output: {stdout.strip()[:200]}")
    result: dict[str, dict] = {}
    for item in parsed:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            result[item["id"]] = item
    return result


def select_anchors(raw_anchors, raw_notes) -> list[tuple[str, dict]]:
    """Pair model anchors with their notes, dropping unsupported candidates.

    Invalid anchors are discarded (``clean_anchors``). An anchor is kept only
    when it has a matching note with a non-empty rationale and query, because
    the note is the grounding evidence for a non-literal cue. Returns ``[]``
    when nothing survives — the caller records the row as ``skip``.
    """
    anchors = clean_anchors(raw_anchors)
    if not anchors:
        return []
    notes: dict[str, dict] = {}
    for note in raw_notes or []:
        if not isinstance(note, dict):
            continue
        raw = note.get("anchor")
        if not isinstance(raw, str):
            continue
        try:
            normalized = normalize_anchors([raw])
        except Exception:
            continue
        if not normalized:
            continue
        rationale = note.get("rationale")
        query = note.get("query")
        if not isinstance(rationale, str) or not rationale.strip():
            continue
        if not isinstance(query, str) or not query.strip():
            continue
        notes.setdefault(normalized[0], {
            "anchor": normalized[0],
            "rationale": rationale.strip(),
            "query": query.strip(),
        })
    paired = [(a, notes[a]) for a in anchors if a in notes]
    return paired


def _has_anchors(metadata) -> bool:
    return bool(clean_anchors((metadata or {}).get("anchors")))


def build_storage(db_path: str | None = None, read_only: bool = False):
    """Resolve the offline backend for the anchor backfill."""
    return deepseek_cli.build_cli_storage(
        db_path, read_only, role="anchor",
        create_storage=create_storage, resolve_replica_path=resolve_replica_path)


def _default_manifest_path() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    from app.config.configuration import output_path
    return output_path(os.path.join("anchor-backfill", f"draft-{stamp}.jsonl"))


def _write_manifest(path: str, entries: list[dict]) -> None:
    """Atomically replace a manifest with ``entries`` (temp file + os.replace)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _parse_manifest(path: str) -> _Manifest:
    """Classify manifest lines by ``type`` without semantic validation."""
    header = trailer = None
    rows = []
    first_type = last_type = None
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ManifestError(f"line {lineno}: invalid JSON: {e}")
            if not isinstance(obj, dict):
                raise ManifestError(f"line {lineno}: line must be an object")
            kind = obj.get("type")
            if kind not in MANIFEST_TYPES:
                raise ManifestError(f"line {lineno}: unknown type {kind!r}")
            if first_type is None:
                first_type = kind
            last_type = kind
            if kind == "header":
                if header is not None:
                    raise ManifestError("manifest has multiple headers")
                header = obj
            elif kind == "trailer":
                if trailer is not None:
                    raise ManifestError("manifest has multiple trailers")
                trailer = obj
            else:
                rows.append(obj)
    return _Manifest(header, rows, trailer, first_type, last_type)


def _ids_from_manifests(paths) -> set[str]:
    """Collect memory_ids already recorded in prior manifests (the ledger).

    Batching uses the manifests themselves as the record of what was already
    drafted: the next batch is ``eligible DB ids - already drafted ids``.
    """
    ids: set[str] = set()
    for path in paths or []:
        if not os.path.isfile(path):
            raise ManifestError(f"exclude manifest not found: {path}")
        for row in _parse_manifest(path).rows:
            mid = row.get("memory_id")
            if isinstance(mid, str) and mid:
                ids.add(mid)
    return ids


def _base_row(memory: dict, args) -> dict:
    text = memory.get("text") or ""
    return {
        "type": "row",
        "schema_version": SCHEMA_VERSION,
        "memory_id": memory["id"],
        "namespace": memory.get("namespace"),
        "content_hash": _content_hash(text),
        "content_hash_algo": CONTENT_HASH_ALGO,
        "text_preview": _text_preview(text),
        "proposed_anchors": [],
        "anchor_notes": [],
        "status": "error",
        "error": None,
        "model": args.model,
        "cli": args.cli,
        "drafted_at": _now(),
    }


def run_draft(args) -> int:
    storage = build_storage(args.db, read_only=True)
    try:
        logger.info("anchor draft (strictly serial): namespace=%s limit=%s model=%s db=%s",
                    args.namespace, args.limit, args.model, args.db)
        memories = storage.list_memories(namespace=args.namespace, limit=10 ** 9,
                                         sort_by="updated_at")
        eligible = [m for m in memories if not _has_anchors(m.get("metadata"))]
        try:
            excluded = _ids_from_manifests(getattr(args, "exclude_manifest", None))
        except ManifestError as e:
            print(json.dumps({"stage": "draft", "error": str(e)}, ensure_ascii=False))
            return 2
        remaining = [m for m in eligible if m["id"] not in excluded]
        targets = remaining[: args.limit] if args.limit else remaining
        target_ids = [m["id"] for m in targets]
        digest = _ids_digest(target_ids)

        if args.dry_run:
            print(json.dumps({"stage": "draft", "namespace": args.namespace,
                              "eligible": len(eligible), "excluded": len(excluded),
                              "remaining": len(remaining), "selected": len(targets),
                              "dry_run": True}, ensure_ascii=False))
            return 0

        from app.config.configuration import output_path
        manifest_path = output_path(args.manifest) if args.manifest else _default_manifest_path()
        os.makedirs(os.path.dirname(manifest_path) or ".", exist_ok=True)

        header = None
        done_ids: set[str] = set()
        mode = "created"
        if os.path.exists(manifest_path) and os.path.getsize(manifest_path) > 0:
            parsed = _parse_manifest(manifest_path)
            if parsed.header is None:
                print(json.dumps({"stage": "draft", "manifest": manifest_path,
                                  "error": "existing manifest has no header",
                                  "draftable": 0}, ensure_ascii=False))
                return 2
            header = parsed.header
            if (header.get("ids_digest") != digest
                    or header.get("selected") != len(target_ids)):
                print(json.dumps({"stage": "draft", "manifest": manifest_path,
                                  "error": "existing manifest selection differs "
                                           "from the current batch",
                                  "draftable": 0}, ensure_ascii=False))
                return 2
            if parsed.trailer is not None:
                print(json.dumps({"stage": "draft", "manifest": manifest_path,
                                  "status": "already_complete",
                                  "selected": len(target_ids)}, ensure_ascii=False))
                return 0
            keep = [r for r in parsed.rows if r.get("status") in ("generated", "skip")]
            done_ids = {r["memory_id"] for r in keep}
            _write_manifest(manifest_path, [header] + keep)
            mode = "resumed"
        else:
            header = {
                "type": "header",
                "schema_version": SCHEMA_VERSION,
                "namespace": args.namespace,
                "selected": len(target_ids),
                "ids_digest": digest,
                "model": args.model,
                "cli": args.cli,
                "created_at": _now(),
            }
            _write_manifest(manifest_path, [header])

        by_id = {m["id"]: m for m in targets}
        pending = [mid for mid in target_ids if mid not in done_ids]
        batch_size = max(1, int(getattr(args, "batch_size", DEFAULT_BATCH_SIZE)
                               or DEFAULT_BATCH_SIZE))
        counters = {"generated": 0, "skip": 0, "error": 0, "resumed": len(done_ids),
                    "model_calls": 0}
        t0 = time.time()
        with open(manifest_path, "a", encoding="utf-8") as fh:
            for start in range(0, len(pending), batch_size):
                chunk = pending[start:start + batch_size]
                try:
                    batch = extract_anchors_batch_deepseek(
                        [(mid, by_id[mid].get("text") or "") for mid in chunk],
                        model=args.model, cli=args.cli, timeout=args.timeout)
                    counters["model_calls"] += 1
                    batch_error = None
                except Exception as e:
                    batch = {}
                    batch_error = str(e)[:200]
                    counters["model_calls"] += 1
                    logger.warning("draft batch failed (%d ids): %s", len(chunk), e)
                for mid in chunk:
                    row = _base_row(by_id[mid], args)
                    obj = batch.get(mid)
                    if batch_error is not None:
                        row["status"] = "error"
                        row["error"] = batch_error
                        counters["error"] += 1
                    elif not isinstance(obj, dict):
                        row["status"] = "error"
                        row["error"] = "id missing from model output"
                        counters["error"] += 1
                    else:
                        paired = select_anchors(obj.get("anchors"), obj.get("anchor_notes"))
                        if paired:
                            row["proposed_anchors"] = [a for a, _ in paired]
                            row["anchor_notes"] = [n for _, n in paired]
                            row["status"] = "generated"
                            counters["generated"] += 1
                        else:
                            row["status"] = "skip"
                            counters["skip"] += 1
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    fh.flush()

        parsed = _parse_manifest(manifest_path)
        accounted = {r["memory_id"] for r in parsed.rows}
        has_error = any(r.get("status") == "error" for r in parsed.rows)
        complete = accounted == set(target_ids) and not has_error
        if complete:
            generated = sum(1 for r in parsed.rows if r.get("status") == "generated")
            skipped = sum(1 for r in parsed.rows if r.get("status") == "skip")
            trailer = {
                "type": "trailer",
                "schema_version": SCHEMA_VERSION,
                "selected": len(target_ids),
                "ids_digest": digest,
                "generated": generated,
                "skip": skipped,
                "completed_at": _now(),
            }
            with open(manifest_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(trailer, ensure_ascii=False) + "\n")
                fh.flush()

        report = {
            "stage": "draft",
            "manifest": manifest_path,
            "mode": mode,
            "namespace": args.namespace,
            "eligible": len(eligible),
            "excluded": len(excluded),
            "remaining": len(remaining),
            "selected": len(target_ids),
            **counters,
            "complete": complete,
            "elapsed_seconds": round(time.time() - t0, 1),
        }
        logger.info("anchor draft done: %s", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if complete else 2
    finally:
        deepseek_cli.stop_storage(storage)


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def _validate_note(note) -> dict:
    if not isinstance(note, dict):
        raise ManifestError("anchor_notes entries must be objects")
    missing = [f for f in NOTE_FIELDS if not isinstance(note.get(f), str) or not note.get(f).strip()]
    if missing:
        raise ManifestError(f"anchor_notes entry missing/blank fields: {', '.join(missing)}")
    return note


def _validate_manifest(path: str) -> _Manifest:
    """Semantically validate a whole manifest; any problem rejects the run."""
    if not path:
        raise ManifestError("--manifest is required")
    if not os.path.isfile(path):
        raise ManifestError(f"manifest not found: {path}")
    mf = _parse_manifest(path)
    if mf.header is None or mf.trailer is None:
        raise ManifestError("manifest is incomplete: header and trailer are both required")
    if mf.first_type != "header" or mf.last_type != "trailer":
        raise ManifestError("manifest must start with a header and end with a trailer")
    for row in [mf.header, *mf.rows, mf.trailer]:
        if row.get("schema_version") != SCHEMA_VERSION:
            raise ManifestError(f"unsupported schema_version {row.get('schema_version')!r}")

    header_missing = [f for f in HEADER_FIELDS if f not in mf.header]
    if header_missing:
        raise ManifestError(f"header missing fields: {', '.join(header_missing)}")
    if not isinstance(mf.header["selected"], int) or mf.header["selected"] < 0:
        raise ManifestError("header.selected must be a non-negative integer")

    seen: set[str] = set()
    for row in mf.rows:
        missing = [f for f in ROW_FIELDS if f not in row]
        if missing:
            raise ManifestError(f"row missing fields: {', '.join(missing)}")
        if not isinstance(row["memory_id"], str) or not row["memory_id"]:
            raise ManifestError("row memory_id must be a non-empty string")
        if row["memory_id"] in seen:
            raise ManifestError(f"duplicate memory_id {row['memory_id']}")
        seen.add(row["memory_id"])
        if not isinstance(row["namespace"], str):
            raise ManifestError(f"row {row['memory_id']}: namespace must be a string")
        if row["status"] not in ROW_STATUSES:
            raise ManifestError(f"row {row['memory_id']}: invalid status {row['status']!r}")
        if row["status"] == "error":
            raise ManifestError(
                f"manifest is incomplete: row {row['memory_id']} has status error")
        if not isinstance(row["proposed_anchors"], list):
            raise ManifestError(f"row {row['memory_id']}: proposed_anchors must be a list")
        if not isinstance(row["anchor_notes"], list):
            raise ManifestError(f"row {row['memory_id']}: anchor_notes must be a list")
        if row["status"] == "generated":
            if not row["proposed_anchors"]:
                raise ManifestError(f"row {row['memory_id']}: generated row has no anchors")
            try:
                normalize_anchors(row["proposed_anchors"])
            except Exception as e:
                raise ManifestError(f"row {row['memory_id']}: invalid anchors: {e}")
            if len(row["anchor_notes"]) != len(row["proposed_anchors"]):
                raise ManifestError(
                    f"row {row['memory_id']}: anchor_notes must match anchors one-to-one")
            for note in row["anchor_notes"]:
                _validate_note(note)

    trailer_missing = [f for f in TRAILER_FIELDS if f not in mf.trailer]
    if trailer_missing:
        raise ManifestError(f"trailer missing fields: {', '.join(trailer_missing)}")

    ids = [r["memory_id"] for r in mf.rows]
    expected = _ids_digest(ids)
    if (mf.header["selected"] != len(ids)
            or mf.trailer.get("selected") != len(ids)
            or mf.header["ids_digest"] != expected
            or mf.trailer.get("ids_digest") != expected):
        raise ManifestError("manifest selected count/digest does not match its rows")
    generated = sum(1 for r in mf.rows if r["status"] == "generated")
    skipped = sum(1 for r in mf.rows if r["status"] == "skip")
    if mf.trailer.get("generated") != generated or mf.trailer.get("skip") != skipped:
        raise ManifestError("manifest trailer counts do not match its rows")
    return mf


def _write_outcome(manifest_path: str, outcome: dict) -> str:
    out_dir = os.path.dirname(manifest_path) or "anchor-backfill"
    root = os.environ.get("MEMORY_DATA_DIR")
    if root:
        from app.config.configuration import rooted_path
        try:
            out_dir = str(rooted_path(root, out_dir))
        except ValueError:
            out_dir = str(rooted_path(root, "anchor-backfill"))
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result_path = os.path.join(out_dir, f"apply-{stamp}.jsonl")
    with open(result_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(outcome, ensure_ascii=False) + "\n")
    return result_path


def run_apply(args) -> int:
    try:
        manifest = _validate_manifest(args.manifest)
    except ManifestError as e:
        logger.error("manifest rejected: %s", e)
        print(json.dumps({"stage": "apply", "error": str(e), "applied": 0},
                         ensure_ascii=False))
        return 2

    generated = [r for r in manifest.rows if r["status"] == "generated"]
    skipped = sum(1 for r in manifest.rows if r["status"] == "skip")
    if not generated:
        print(json.dumps({"stage": "apply", "manifest": args.manifest,
                          "generated": 0, "skip": skipped, "applied": 0,
                          "transaction": "noop",
                          "message": "no generated rows; nothing to do"},
                         ensure_ascii=False))
        return 0

    try:
        storage = build_storage(args.db, read_only=args.dry_run)
    except Exception as e:
        logger.error("cannot acquire storage/writer lock: %s", e)
        print(json.dumps({"stage": "apply", "manifest": args.manifest,
                          "error": f"storage/writer lock unavailable: {e}",
                          "applied": 0}, ensure_ascii=False))
        return 2

    def abort(reason: str, detail: str) -> int:
        logger.error("anchor apply aborted: %s (%s)", reason, detail)
        outcome = {
            "schema_version": SCHEMA_VERSION, "manifest": args.manifest,
            "namespace": args.namespace, "generated": len(generated),
            "skip": skipped, "applied": 0, "transaction": "aborted",
            "abort_reason": reason, "error": detail, "coverage_delta": None,
            "checked_at": _now(),
        }
        if not args.dry_run:
            outcome["result_manifest"] = _write_outcome(args.manifest, outcome)
        print(json.dumps({"stage": "apply", **outcome}, ensure_ascii=False, indent=2))
        return 2

    try:
        before_stats = None if args.dry_run else storage.get_stats()
        updated_before: dict[str, str | None] = {}
        already: list[tuple[dict, list[str]]] = []
        new: list[tuple[dict, list[str]]] = []
        for row in generated:
            mid = row["memory_id"]
            rec = storage.get(mid)
            if rec is None:
                return abort("not_live", f"record not found or deleted: {mid}")
            namespace = rec.get("namespace")
            if namespace != row["namespace"] or (args.namespace and namespace != args.namespace):
                return abort("namespace_mismatch", f"{mid}: {namespace!r}")
            if _content_hash(rec.get("text") or "") != row.get("content_hash"):
                return abort("content_changed", mid)
            try:
                anchors = normalize_anchors(row["proposed_anchors"])
            except Exception as e:
                return abort("invalid_anchors", f"{mid}: {e}")
            if not anchors:
                return abort("invalid_anchors", f"{mid}: empty")
            current = clean_anchors((rec.get("metadata") or {}).get("anchors"))
            if current:
                if current == anchors:
                    already.append((row, anchors))
                else:
                    return abort("already_anchored", f"{mid}: existing anchors differ")
            else:
                new.append((row, anchors))
                updated_before[mid] = rec.get("updated_at")

        if already and new:
            return abort("mixed_already_applied",
                         f"already_applied={len(already)} new={len(new)}")
        if already and not new:
            outcome = {
                "schema_version": SCHEMA_VERSION, "manifest": args.manifest,
                "namespace": args.namespace, "generated": len(generated),
                "skip": skipped, "applied": 0, "transaction": "noop",
                "abort_reason": None, "error": None,
                "coverage_delta": {"anchor_total": 0, "memories_with_anchors": 0},
                "checked_at": _now(),
            }
            if not args.dry_run:
                outcome["result_manifest"] = _write_outcome(args.manifest, outcome)
            print(json.dumps({"stage": "apply", **outcome,
                              "message": "entire batch already applied"},
                             ensure_ascii=False, indent=2))
            return 0

        if args.dry_run:
            print(json.dumps({"stage": "apply", "manifest": args.manifest,
                              "generated": len(generated), "skip": skipped,
                              "planned_applied": len(new), "transaction": "dry_run",
                              "dry_run": True}, ensure_ascii=False))
            return 0

        try:
            storage.update_anchors_batch([(row["memory_id"], anchors)
                                          for row, anchors in new])
        except Exception as e:
            return abort("storage_error", str(e)[:300])

        updated_at_mismatch = 0
        for row, _anchors in new:
            after = storage.get(row["memory_id"]) or {}
            if after.get("updated_at") != updated_before.get(row["memory_id"]):
                updated_at_mismatch += 1
        after_stats = storage.get_stats()
        delta = {
            "anchor_total": after_stats.get("anchor_total", 0) - before_stats.get("anchor_total", 0),
            "memories_with_anchors": (after_stats.get("memories_with_anchors", 0)
                                      - before_stats.get("memories_with_anchors", 0)),
        }
        outcome = {
            "schema_version": SCHEMA_VERSION, "manifest": args.manifest,
            "namespace": args.namespace, "generated": len(generated),
            "skip": skipped, "applied": len(new), "transaction": "committed",
            "abort_reason": None, "error": None,
            "updated_at_mismatch": updated_at_mismatch,
            "coverage_delta": delta, "checked_at": _now(),
        }
        outcome["result_manifest"] = _write_outcome(args.manifest, outcome)
        logger.info("anchor apply done: %s", outcome)
        print(json.dumps({"stage": "apply", **outcome}, ensure_ascii=False, indent=2))
        return 2 if updated_at_mismatch else 0
    finally:
        deepseek_cli.stop_storage(storage)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="app.anchor_migrate_deepseek",
        description="Backfill metadata.anchors for existing memories using deepseek-v4-flash")
    sub = parser.add_subparsers(dest="stage", required=True)

    p_draft = sub.add_parser("draft", help="Read-only: generate a batch manifest")
    p_draft.add_argument("--namespace", default="remember-me", help="Target namespace")
    p_draft.add_argument("--limit", type=int, default=None,
                         help="Max eligible records to draft (None = all)")
    p_draft.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                         help="Memories per opencode call (default 10). The CLI pays a "
                              "fixed ~30s startup per call, so batching amortizes it.")
    p_draft.add_argument("--manifest", default=None,
                         help="Manifest path (default anchor-backfill/draft-<UTC>.jsonl, "
                              "rooted under the data directory when configured)")
    p_draft.add_argument("--exclude-manifest", action="append", default=None, metavar="PATH",
                         help="Skip memory_ids already present in this manifest (repeatable). "
                              "Use prior manifests as the ledger to draft disjoint batches "
                              "without applying in between.")
    p_draft.add_argument("--model", default=DEFAULT_MODEL, help="opencode model id")
    p_draft.add_argument("--cli", default=DEFAULT_CLI, help="opencode CLI binary")
    p_draft.add_argument("--timeout", type=float, default=120.0,
                         help="Per-record CLI timeout in seconds")
    p_draft.add_argument("--dry-run", action="store_true",
                         help="Count eligible records only; no model calls, no manifest")
    p_draft.add_argument("--db", default=None,
                         help="SQLite path override (default: MEMORY_STORAGE_PATH or the configured data directory)")

    p_apply = sub.add_parser("apply", help="Write one complete batch (service must be stopped)")
    p_apply.add_argument("--manifest", required=True, help="Reviewed manifest path")
    p_apply.add_argument("--namespace", default=None,
                         help="Guard: only write rows for this namespace")
    p_apply.add_argument("--dry-run", action="store_true",
                         help="Validate and check staleness without writing (no writer lock)")
    p_apply.add_argument("--db", default=None,
                         help="SQLite path override (default: MEMORY_STORAGE_PATH or the configured data directory)")

    args = parser.parse_args(argv)
    from app.config.configuration import output_path
    if args.db:
        args.db = output_path(args.db)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.stage == "draft":
        return run_draft(args)
    return run_apply(args)


def entrypoint():
    """Run the command with its configuration and exit-status policy."""
    from app.config.configuration import cli_environment
    if not any(arg in ("--help", "-h") for arg in sys.argv[1:]):
        cli_environment()
    sys.exit(main())


if __name__ == "__main__":
    entrypoint()
