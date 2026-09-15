"""Explicit offline keyword backfill for existing memories using deepseek-v4-flash.

The previous local-Ollama migration was removed from the service and admin UI.
This CLI is intentionally separate: an operator explicitly invokes it with
``opencode run`` when a historical backfill is desired. It does not create a
runtime MCP or HTTP dependency on a generative model.

Design:
- STRICTLY SERIAL: each record's extraction depends on the accumulated keyword
  vocabulary (candidates), so records are processed one at a time and the
  vocabulary grows monotonically. --workers is therefore removed; concurrency
  would corrupt the global vocabulary state.
- Vocabulary convergence: the current keyword set is sent to deepseek with every
  call and the model is instructed to REUSE existing word forms first and only
  invent new words when the text really has a new concept. The vocabulary starts
  from all keywords already present in the store (so previously migrated records
  keep their forms) and grows as new records are processed.
- Vocabulary cap: when the vocabulary exceeds MAX_CANDIDATES (default 500) the
  least-recently-added words are dropped from the prompt (sliding window). Old
  words stay in the store, they just stop being sent as candidates.
- Runs as a standalone CLI (``python -m app.keyword_migrate_deepseek``) so it can
  be launched in a terminal / background process without touching the MCP server.
- Uses storage.update_metadata() so updated_at is NOT perturbed and the SQLite
  keyword index is rebuilt per record.
- Resilient per record: CLI timeout, output extraction with several fallbacks,
  failure is counted and the record stays eligible for a later retry.
- Batch/limit options let you chew through the backlog in chunks.
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

from .storage import create_storage, resolve_replica_path
from .index_value_resolver import resolver_for_configured_profile

logger = logging.getLogger("keyword_migrate_deepseek")

# Default model id as configured in ~/.config/opencode/opencode.jsonc
DEFAULT_MODEL = "opencode-go/deepseek-v4-flash"
DEFAULT_CLI = "opencode"
KEYWORD_PROMPT_TEMPLATE = (
    "You are a precise keyword extractor for Chinese/English technical text.\n"
    "Rules:\n"
    "- Extract 3-8 keywords that capture the concrete concepts, entities, and "
    "topics in the text.\n"
    "- Use lowercase; prefer stable canonical forms (e.g. 'rtg' not 'RTG').\n"
    "- Do NOT use category-like or type-like labels (e.g. 'incident', "
    "'recap', 'bug' alone, 'task').\n"
    "- Keep technical terms, ticket numbers, and business entities.\n"
    "- When a concept in the text already exists in the candidate list below, "
    "REUSE the candidate's exact word form (do not paraphrase, split, or "
    "recombine it). This keeps the vocabulary stable across records.\n"
    "- Only add a brand-new keyword when the text contains a concept that has "
    "no matching candidate in the list.\n"
    "- Respond ONLY with a JSON array of strings, e.g. [\"keyword1\", "
    "\"keyword2\"]. No preamble, no quotes around the array.\n\n"
    "Candidate keywords (prefer these exact forms when applicable):\n"
    "{candidates}\n\n"
    "Text:\n{text}"
)

# Vocabulary sent to the model is capped to this many entries (sliding window
# over the most recently added words).
MAX_CANDIDATES = 500
_CANDIDATES_BLOCK_HEADER = "---- candidate keywords ----"
_CANDIDATES_BLOCK_FOOTER = "---- end candidates ----"


def format_candidates_block(keywords: list[str]) -> str:
    """Render the candidate vocabulary as a delimited block.

    The delimiters make it unambiguous for the model where the candidate list
    ends and the real text begins, and survive texts that contain JSON arrays.
    """
    if not keywords:
        return "(empty candidate list)"
    body = "\n".join(f"- {kw}" for kw in keywords)
    return f"{_CANDIDATES_BLOCK_HEADER}\n{body}\n{_CANDIDATES_BLOCK_FOOTER}"

# opencode prints a small banner ("> build · model") to stderr and the final
# answer to stdout; some output may carry ANSI codes.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_keywords(keywords) -> list[str]:
    """Trim/lowercase/dedup a parsed keyword list; drop non-strings and empties."""
    result = []
    seen = set()
    for kw in keywords or []:
        if not isinstance(kw, str):
            continue
        kw = kw.strip().lower()
        if kw and kw not in seen:
            seen.add(kw)
            result.append(kw)
    return result


def _extract_json_array(output: str) -> list[str] | None:
    """Parse the first JSON array found in ``output``.

    deepseek returns a bare JSON array on stdout, but tolerate extra text /
    ANSI codes / markdown fences. Returns None when nothing parseable appears.
    """
    text = _ANSI_RE.sub("", output)
    # Strip markdown code fences if the model wrapped the answer.
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    for match in re.finditer(r"\[.*?\]", text, re.DOTALL):
        candidate = match.group(0)
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            return [str(x) for x in parsed if not isinstance(x, (dict, list))]
    return None


def extract_keywords_deepseek(text: str, candidates: list[str] | None = None,
                              model: str = DEFAULT_MODEL,
                              cli: str = DEFAULT_CLI,
                              timeout: float = 120.0) -> list[str]:
    """Extract keywords via `opencode run` (deepseek-v4-flash).

    ``candidates`` is the current keyword vocabulary; the model is asked to
    reuse those exact word forms first. Raises on CLI failure / timeout /
    unparseable output — callers decide how to count the failure. Returns a
    normalized keyword list.
    """
    prompt = KEYWORD_PROMPT_TEMPLATE.format(
        candidates=format_candidates_block(candidates or []),
        text=text,
    )
    proc = subprocess.run(
        [cli, "run", "--model", model, "Extract keywords. Output ONLY the JSON array."],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"opencode exited {proc.returncode}: "
                           f"{proc.stderr.strip()[:200]}")
    keywords = _extract_json_array(proc.stdout)
    if not keywords:
        raise ValueError(f"no keywords in output: {proc.stdout.strip()[:200]}")
    return _normalize_keywords(keywords)


class KeywordVocabulary:
    """Monotonic keyword vocabulary with a sliding-window cap.

    Words are kept in insertion order; when the list exceeds ``max_size`` the
    oldest entries are trimmed from the prompt view (they stay in the store,
    just stop being sent as candidates).
    """

    def __init__(self, initial: list[str] | None = None, max_size: int = MAX_CANDIDATES):
        self._max_size = max_size
        self._order: list[str] = []
        self._seen: set[str] = set()
        for kw in _normalize_keywords(initial or []):
            self._add(kw)

    def _add(self, kw: str):
        if kw in self._seen:
            return
        self._seen.add(kw)
        self._order.append(kw)

    def add_many(self, keywords: list[str]):
        for kw in _normalize_keywords(keywords):
            self._add(kw)

    def as_candidates(self) -> list[str]:
        """Return up to max_size most recently added words (for the prompt)."""
        return self._order[-self._max_size:]

    def __len__(self) -> int:
        return len(self._order)


def build_storage(db_path: str | None = None, read_only: bool = False):
    """Reuse the same env-based backend resolution as service.py (dual default).

    ``db_path`` (CLI ``--db``) overrides the primary storage path; the backend
    itself still comes from ``MEMORY_STORAGE``.
    """
    # A dry run is a diagnostic read and must not claim either writer lock.
    # It also deliberately bypasses dual mode: no replica worker is needed to
    # inspect the primary database.
    if read_only:
        path = db_path or os.environ.get("MEMORY_STORAGE_PATH") or "data/memories.db"
        return create_storage("sqlite", path=path, read_only=True)

    backend = os.environ.get("MEMORY_STORAGE", "sqlite")
    if backend == "dual":
        primary = db_path or os.environ.get("MEMORY_STORAGE_PATH", "data/memories.db")
        secondary = resolve_replica_path(
            primary, os.environ.get("MEMORY_REPLICA_PATH"),
            os.environ.get("MEMORY_STORAGE_PATH_DUAL")
        )
        return create_storage("dual", primary_path=primary,
                              secondary_path=secondary,
                              sync_interval=int(os.environ.get("MEMORY_SYNC_INTERVAL", "300")))
    if backend != "sqlite":
        raise RuntimeError("keyword migration supports only MEMORY_STORAGE=sqlite or dual")
    path = db_path or os.environ.get("MEMORY_STORAGE_PATH") or "data/memories.db"
    return create_storage("sqlite", path=path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill metadata.keywords for existing memories using deepseek-v4-flash")
    parser.add_argument("--namespace", default=None, help="Only this namespace")
    parser.add_argument("--mode", choices=("missing", "all"), default="missing",
                        help="missing: only records without keywords (default); all: overwrite")
    parser.add_argument("--memory-id", default=None,
                        help="Process a single memory by id (mode still applies)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max records to process (None = all)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="opencode model id")
    parser.add_argument("--cli", default=DEFAULT_CLI, help="opencode CLI binary")
    parser.add_argument("--timeout", type=float, default=120.0,
                        help="Per-record CLI timeout in seconds")
    parser.add_argument("--dry-run", action="store_true",
                        help="Count what would be processed without calling the model")
    parser.add_argument("--db", default=None,
                        help="Storage file path override (default: from env or data/memories.db)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    storage = build_storage(args.db, read_only=args.dry_run)

    def close_storage():
        stop = getattr(storage, "stop", None)
        if callable(stop):
            stop()
    logger.info("keyword migration (deepseek, serial): namespace=%s mode=%s limit=%s model=%s db=%s",
                args.namespace, args.mode, args.limit, args.model, args.db)

    if args.memory_id:
        m = storage.get(args.memory_id)
        memories = [m] if m else []
        if not memories:
            print(json.dumps({"processed": 0, "failed": 0, "total": 0,
                              "error": f"memory not found: {args.memory_id}",
                              "memory_id": args.memory_id}))
            close_storage()
            return 1
    else:
        memories = storage.list_memories(namespace=args.namespace,
                                         limit=args.limit or 10 ** 9)
    if args.limit:
        memories = memories[: args.limit]

    # Filter by mode.
    to_process = []
    skipped = 0
    for m in memories:
        if args.mode == "missing" and m.get("metadata", {}).get("keywords"):
            skipped += 1
            continue
        to_process.append(m)

    if args.dry_run:
        print(json.dumps({"mode": args.mode, "candidates": len(to_process),
                          "skipped": skipped, "total": len(memories),
                          "dry_run": True}))
        close_storage()
        return 0

    index_resolver = resolver_for_configured_profile(storage)

    # Seed the vocabulary with all keywords already present in the store so
    # previously migrated records keep their word forms and new records
    # converge toward them.
    if hasattr(storage, "get_keyword_vocabulary"):
        existing_keywords = storage.get_keyword_vocabulary(namespace=args.namespace)
    else:
        existing_keywords = list(storage.get_stats().get("keyword_counts", {}).keys())
    vocabulary = KeywordVocabulary(existing_keywords)
    logger.info("vocabulary seeded with %d existing keywords", len(vocabulary))

    counters = {"processed": 0, "failed": 0}
    failures = []
    results = []

    def work(m):
        if not m.get("text"):
            counters["failed"] += 1
            return
        try:
            keywords = extract_keywords_deepseek(
                m["text"], candidates=vocabulary.as_candidates(),
                model=args.model, cli=args.cli, timeout=args.timeout)
        except Exception as e:
            counters["failed"] += 1
            failures.append({"id": m["id"], "error": str(e)[:200]})
            logger.warning("failed id=%s: %s", m["id"], e)
            return
        if not keywords:
            counters["failed"] += 1
            failures.append({"id": m["id"], "error": "empty keywords"})
            return
        try:
            metadata_update = {"keywords": keywords}
            index_embeddings = (index_resolver.prepare_embeddings(metadata_update)
                                if index_resolver else None)
            storage.update_metadata(
                m["id"], metadata_update, index_embeddings=index_embeddings
            )
        except Exception as e:
            counters["failed"] += 1
            failures.append({"id": m["id"], "error": str(e)[:200]})
            logger.warning("failed id=%s: %s", m["id"], e)
            return
        # Vocabulary grows as new concepts appear; later records reuse them.
        vocabulary.add_many(keywords)
        counters["processed"] += 1
        results.append({"id": m["id"], "keywords": keywords})
        if counters["processed"] % 10 == 0:
            logger.info("progress: processed=%d failed=%d vocab=%d elapsed=%.0fs",
                        counters["processed"], counters["failed"],
                        len(vocabulary), time.time() - t0)

    t0 = time.time()
    for m in to_process:
        work(m)

    elapsed = time.time() - t0
    report = {
        "mode": args.mode,
        "total": len(memories),
        "skipped": skipped,
        "processed": counters["processed"],
        "failed": counters["failed"],
        "vocabulary_size": len(vocabulary),
        "elapsed_seconds": round(elapsed, 1),
    }
    if args.memory_id:
        report["memory_id"] = args.memory_id
    if failures:
        report["failures"] = failures[:20]
    if results:
        report["sample"] = results[:5]
    logger.info("keyword migration done: %s", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    close_storage()
    return 0 if counters["failed"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
