"""Category dimension for memories — a small, controlled taxonomy.

``categories`` is a first-class metadata dimension parallel to ``keywords``:
- keywords   = content concepts (unbounded, e.g. entities/topics)
- categories = memory nature/classification (small seeded taxonomy, may grow)

Agreed design rules:
- Seed vocabulary of ~20 canonical values; the LLM is asked to prefer those but
  may introduce new ones (same soft-constraint model as keywords).
- Each memory carries up to MAX_CATEGORIES (3) values; multiple values allowed.
- Values live in ``metadata["categories"]`` (JSON — automatically backed up in
  the NDJSON replica); the SQLite category tables are a rebuildable query index.
"""

CATEGORY_VOCABULARY: tuple[str, ...] = (
    "requirement", "business-rule", "design", "data-model", "api",
    "pattern", "convention", "bugfix", "performance", "refactor",
    "migration", "verification", "research", "investigation", "incident",
    "config", "tooling", "ops", "glossary", "other",
)

MAX_CATEGORIES = 3


def _clean_categories(value) -> list[str]:
    """Filter/trim/lowercase/deduplicate a categories value (no cap)."""
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        item = item.strip().lower()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def normalize_categories(value) -> list[str]:
    """Normalize a memory's categories value, capped at MAX_CATEGORIES.

    Order is preserved; entries beyond the cap are dropped (callers that need
    to report truncation should compare the raw length against the result).
    """
    return _clean_categories(value)[:MAX_CATEGORIES]


def normalize_categories_filter(value) -> list[str]:
    """Normalize a categories *filter* value (search_meta) — uncapped.

    Filters may reasonably list more categories than a single memory can carry;
    no truncation is applied, only cleaning/dedup.
    """
    return _clean_categories(value)


def categories_dropped(metadata) -> int:
    """Number of categories dropped by the cap for a metadata dict."""
    raw = (metadata or {}).get("categories")
    if not isinstance(raw, list):
        return 0
    return max(0, len(raw) - len(_clean_categories(raw)[:MAX_CATEGORIES]))


def category_counts_with_seeds(observed: dict | None) -> dict[str, int]:
    """Merge observed category counts with the seed vocabulary.

    Seeds always appear (count 0 when unused) so the LLM can reuse them;
    observed extras are appended ordered by count descending, then name.
    """
    counts: dict[str, int] = {seed: 0 for seed in CATEGORY_VOCABULARY}
    for name, cnt in (observed or {}).items():
        counts[name] = counts.get(name, 0) + cnt
    seeds = {name: counts.pop(name) for name in CATEGORY_VOCABULARY}
    extras = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {**seeds, **dict(extras)}