"""Deterministic summary handling with no generative-model dependency."""

import re


SUMMARY_MAX_CHARS = 80
_MIN_HEADING_CHARS = 10
_MARKUP_PREFIX = re.compile(r"^(?:#{1,6}\s+|[-*+]\s+|\d+[.)、]\s*)")
_SENTENCE_END = re.compile(r"[。！？!?；;]")


def normalize_whitespace(text: str) -> str:
    """Trim text and collapse all whitespace runs to one ASCII space."""
    return " ".join((text or "").split())


def normalize_summary(summary) -> str | None:
    """Return a usable caller summary, or ``None`` for blank/non-string input."""
    if not isinstance(summary, str):
        return None
    value = summary.strip()
    return value or None


def derive_summary(text: str, max_chars: int = SUMMARY_MAX_CHARS) -> str:
    """Extract a compact lead sentence/line from ``text`` deterministically."""
    lines = []
    for raw_line in (text or "").splitlines():
        line = normalize_whitespace(raw_line)
        if not line or line in ("```", "~~~"):
            continue
        line = _MARKUP_PREFIX.sub("", line).strip()
        if line:
            lines.append(line)

    if not lines:
        return ""

    candidate = lines[0]
    if len(candidate.rstrip("：:")) < _MIN_HEADING_CHARS and len(lines) > 1:
        candidate = f"{candidate} {lines[1]}"

    sentence_end = _SENTENCE_END.search(candidate)
    if sentence_end and sentence_end.end() <= max_chars:
        return candidate[:sentence_end.end()]
    if len(candidate) <= max_chars:
        return candidate
    return candidate[:max_chars].rstrip() + "…"


def summary_for_add(text: str, summary) -> str:
    """Prefer a caller summary; otherwise derive one from the new text."""
    return normalize_summary(summary) or derive_summary(text)


def summary_for_update(existing_text: str, existing_summary: str | None,
                       new_text: str | None, summary) -> str | None:
    """Resolve summary policy for an update performed under the storage lock."""
    supplied = normalize_summary(summary)
    if supplied is not None:
        return supplied
    if new_text is None:
        return existing_summary
    if normalize_whitespace(new_text) == normalize_whitespace(existing_text):
        return existing_summary
    return derive_summary(new_text)
