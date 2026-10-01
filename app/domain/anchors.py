"""Retrieval-anchor domain rules.

An anchor is a distinctive term or short phrase a future query may use to
identify a specific memory (class names, error codes, config keys, identifiers),
*not* a topic keyword. Anchors are always supplied by the caller in
``metadata.anchors``; the server never generates them.

Two entry points:

- :func:`normalize_anchors` is strict and is used for caller input (add/update).
  It rejects the whole submission when any item is invalid.
- :func:`clean_anchors` is lenient and is used when extracting anchors for the
  generic index and for rebuild/import, so historical or malformed metadata
  cannot make a rebuild fail.
"""

import re
import unicodedata

MAX_ANCHORS = 5
MAX_ANCHOR_CHARS = 128

_WHITESPACE = re.compile(r"\s+")


class InvalidAnchors(ValueError):
    """Caller-submitted metadata.anchors failed validation."""


def _normalize_one(value: str) -> str:
    """NFKC + trim + collapse internal whitespace. Case is preserved."""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", value)).strip()


def _dedupe_key(value: str) -> str:
    """Deduplicate case-insensitively for ASCII, exactly for non-ASCII strings."""
    return value.lower() if value.isascii() else value


def normalize_anchors(values) -> list[str]:
    """Strictly validate and normalize caller-provided anchors.

    Rules (design §7 / HLD §15): must be a list of strings; the *raw* list may
    not exceed :data:`MAX_ANCHORS` (checked before dedup); every item must be
    non-empty and at most :data:`MAX_ANCHOR_CHARS` after normalization. The first
    occurrence wins and order is preserved. Any invalid item rejects the whole
    submission — invalid items are never silently dropped.
    """
    if not isinstance(values, list):
        raise InvalidAnchors("anchors must be an array of strings")
    if len(values) > MAX_ANCHORS:
        raise InvalidAnchors(f"anchors accepts at most {MAX_ANCHORS} values")
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            raise InvalidAnchors("each anchor must be a string")
        normalized = _normalize_one(item)
        if not normalized:
            raise InvalidAnchors("anchors must not be empty")
        if len(normalized) > MAX_ANCHOR_CHARS:
            raise InvalidAnchors(
                f"each anchor must be at most {MAX_ANCHOR_CHARS} characters")
        key = _dedupe_key(normalized)
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def clean_anchors(values) -> list[str]:
    """Leniently extract anchors for indexing/rebuild/import.

    Invalid items are dropped (never raised) and the result is capped at
    :data:`MAX_ANCHORS`. Caller input never takes this path: it is validated by
    :func:`normalize_anchors` before it reaches storage.
    """
    if not isinstance(values, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        if len(result) >= MAX_ANCHORS:
            break
        if not isinstance(item, str):
            continue
        normalized = _normalize_one(item)
        if not normalized or len(normalized) > MAX_ANCHOR_CHARS:
            continue
        key = _dedupe_key(normalized)
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def anchor_fts_text(anchors) -> str:
    """Serialize anchors into the FTS field (newline separated, per design §16)."""
    return "\n".join(anchors)
