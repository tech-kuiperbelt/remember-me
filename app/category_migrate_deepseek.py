"""Batch category backfill for existing memories using deepseek-v4-flash.

Mirrors app/keyword_migrate_deepseek.py (same CLI shape, serial processing,
per-record resilience) but for the categories dimension. Deliberately no local
Ollama/qwen path: the qwen3 route proved slow and unstable on pure CPU.

Key differences from the keyword migration:
- Categories are a CONTROLLED taxonomy: a fixed 20-seed vocabulary
  (categories.CATEGORY_VOCABULARY) plus a small set of observed/invented
  extras. Per-memory output is capped at 3 (categories.normalize_categories).
- The candidate list is therefore always small — no sliding-window cap needed;
  the seeds are always sent, extras (observed + invented during this run) are
  appended so later records converge toward them.
- The model is instructed to PICK 1-3 categories describing the memory's
  nature/classification (not content keywords) and only invent a new category
  when none of the candidates fits.

Design (shared with the keyword migration):
- STRICTLY SERIAL: one record at a time; the category set grows monotonically.
- Writes via storage.update_metadata() so updated_at is NOT perturbed and the
  SQLite category index is rebuilt per record.
- Runs as a standalone CLI (``python -m app.category_migrate_deepseek``).
- Resilient per record: failures counted, record stays eligible for retry.
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
from .categories import CATEGORY_VOCABULARY, normalize_categories

logger = logging.getLogger("category_migrate_deepseek")

# Default model id as configured in ~/.config/opencode/opencode.jsonc
DEFAULT_MODEL = "opencode-go/deepseek-v4-flash"
DEFAULT_CLI = "opencode"

_MAX_CATEGORIES = 3  # matches categories.MAX_CATEGORIES (kept local for prompts)

CATEGORY_PROMPT_TEMPLATE = (
    "You are a precise memory categorizer for Chinese/English technical text.\n"
    "Rules:\n"
    "- Assign 1-%d categories that best describe the NATURE/CLASSIFICATION of "
    "the memory (e.g. requirement, bugfix, design, migration) — not its content "
    "keywords, entities, or ticket numbers.\n"
    "- Prefer the candidate categories below; pick the best 1-%d in priority "
    "order. Do NOT pad the output — fewer is fine.\n"
    "- Only introduce a brand-new category when none of the candidates fits and "
    "the memory's nature is genuinely different from all of them.\n"
    "- Use lowercase; use the exact candidate form when you reuse one.\n"
    "- Respond ONLY with a JSON array of strings, e.g. "
    "[\"bugfix\", \"performance\"]. No preamble, no quotes around the array.\n\n"
    "Candidate categories (prefer these exact forms):\n"
    "{candidates}\n\n"
    "Text:\n{text}"
) % (_MAX_CATEGORIES, _MAX_CATEGORIES)

_CANDIDATES_BLOCK_HEADER = "---- candidate categories ----"
_CANDIDATES_BLOCK_FOOTER = "---- end candidates ----"

# opencode prints a small banner to stderr and the answer to stdout; some
# output may carry ANSI codes.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_candidates_block(categories: list[str] | None) -> str:
    """Render the candidate category list as a delimited block.

    The delimiters make it unambiguous where the candidate list ends and the
    real text begins.
    """
    if not categories:
        return "(empty candidate list)"
    body = "\n".join(f"- {c}" for c in categories)
    return f"{_CANDIDATES_BLOCK_HEADER}\n{body}\n{_CANDIDATES_BLOCK_FOOTER}"


def _extract_json_array(output: str) -> list[str] | None:
    """Parse the first JSON array found in ``output``.

    Tolerates extra text / ANSI codes / markdown fences. Returns None when
    nothing parseable appears.
    """
    text = _ANSI_RE.sub("", output)
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


def extract_categories_deepseek(text: str, candidates: list[str] | None = None,
                                model: str = DEFAULT_MODEL,
                                cli: str = DEFAULT_CLI,
                                timeout: float = 120.0) -> list[str]:
    """Extract categories via `opencode run` (deepseek-v4-flash).

    ``candidates`` is the seed vocabulary + observed/invented extras; the model
    is asked to reuse those exact forms first. Returns a normalized category
    list capped at 3. Raises on CLI failure / timeout / unparseable output —
    callers decide how to count the failure.
    """
    prompt = CATEGORY_PROMPT_TEMPLATE.format(
        candidates=format_candidates_block(candidates or []),
        text=text,
    )
    proc = subprocess.run(
        [cli, "run", "--model", model, "Categorize the memory. Output ONLY the JSON array."],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"opencode exited {proc.returncode}: "
                           f"{proc.stderr.strip()[:200]}")
    parsed = _extract_json_array(proc.stdout)
    if not parsed:
        raise ValueError(f"no categories in output: {proc.stdout.strip()[:200]}")
    return normalize_categories(parsed)


class CategoryCandidates:
    """Seed vocabulary + observed/invented extras, in stable insertion order.

    The 20 canonical seeds always come first; extras observed in the store and
    categories invented during this run are appended so later records reuse the
    same forms. Small by nature — no sliding-window cap, unlike keywords.
    """

    def __init__(self, observed: list[str] | None = None):
        self._order: list[str] = []
        self._seen: set[str] = set()
        for cat in CATEGORY_VOCABULARY:
            self._add(cat)
        for cat in normalize_categories(observed or []):
            self._add(cat)

    def _add(self, cat: str):
        if cat in self._seen:
            return
        self._seen.add(cat)
        self._order.append(cat)

    def add_many(self, categories: list[str]):
        for cat in normalize_categories(categories):
            self._add(cat)

    def as_candidates(self) -> list[str]:
        return list(self._order)

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
        raise RuntimeError("category migration supports only MEMORY_STORAGE=sqlite or dual")
    path = db_path or os.environ.get("MEMORY_STORAGE_PATH") or "data/memories.db"
    return create_storage("sqlite", path=path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill metadata.categories for existing memories using deepseek-v4-flash")
    parser.add_argument("--namespace", default=None, help="Only this namespace")
    parser.add_argument("--mode", choices=("missing", "all"), default="missing",
                        help="missing: only records without categories (default); all: overwrite")
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
    from .configuration import output_path
    if args.db:
        args.db = output_path(args.db)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    storage = build_storage(args.db, read_only=args.dry_run)

    def close_storage():
        stop = getattr(storage, "stop", None)
        if callable(stop):
            stop()
    logger.info("category migration (deepseek, serial): namespace=%s mode=%s limit=%s model=%s db=%s",
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
        if args.mode == "missing" and m.get("metadata", {}).get("categories"):
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

    # Seed the candidates with the canonical vocabulary plus any categories
    # already present in the store so previously migrated records keep their
    # forms and new records converge toward them.
    observed = list(storage.get_stats().get("category_counts", {}).keys())
    candidates = CategoryCandidates(observed)
    logger.info("category candidates seeded with %d entries (%d seeds)",
                len(candidates), len(CATEGORY_VOCABULARY))

    counters = {"processed": 0, "failed": 0}
    failures = []
    results = []

    def work(m):
        if not m.get("text"):
            counters["failed"] += 1
            return
        try:
            categories = extract_categories_deepseek(
                m["text"], candidates=candidates.as_candidates(),
                model=args.model, cli=args.cli, timeout=args.timeout)
        except Exception as e:
            counters["failed"] += 1
            failures.append({"id": m["id"], "error": str(e)[:200]})
            logger.warning("failed id=%s: %s", m["id"], e)
            return
        if not categories:
            counters["failed"] += 1
            failures.append({"id": m["id"], "error": "empty categories"})
            return
        try:
            metadata_update = {"categories": categories}
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
        # Extras grow as genuinely new categories are introduced.
        candidates.add_many(categories)
        counters["processed"] += 1
        results.append({"id": m["id"], "categories": categories})
        if counters["processed"] % 10 == 0:
            logger.info("progress: processed=%d failed=%d candidates=%d elapsed=%.0fs",
                        counters["processed"], counters["failed"],
                        len(candidates), time.time() - t0)

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
        "category_count": len(candidates),
        "elapsed_seconds": round(elapsed, 1),
    }
    if args.memory_id:
        report["memory_id"] = args.memory_id
    if failures:
        report["failures"] = failures[:20]
    if results:
        report["sample"] = results[:5]
    logger.info("category migration done: %s", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    close_storage()
    return 0 if counters["failed"] == 0 else 2


if __name__ == "__main__":
    from .configuration import cli_environment
    if not any(arg in ("--help", "-h") for arg in sys.argv[1:]):
        cli_environment()
    sys.exit(main())
