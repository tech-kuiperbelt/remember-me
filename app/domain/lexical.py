"""Minimal shared query builder for the lexical (FTS5/BM25) retrieval primitive.

This module is intentionally dependency-free and holds the *shared* pieces used by
both the content FTS index (Phase 1) and, later, the anchor FTS index (Phase 2):
the index version, the input limits and the escaping/MATCH construction.

Tokenization here only decides which substrings count as searchable tokens for
validation and limits; SQLite's ``unicode61`` tokenizer performs the actual
matching. We therefore never hand-split CamelCase or CJK, which would make the
query and index rules diverge.
"""

import re

# Bump when the FTS schema, tokenizer or index representation changes. A single
# version covers both the content and anchor FTS indexes, and the same rebuild
# also backfills the derived anchor relation index from metadata.
# v2 adds memory_anchor_fts; v3 rebuilds the anchors relation dimension so
# databases that already carried metadata.anchors get correct anchor stats.
LEXICAL_INDEX_VERSION = 3

# Conservative engineering limits (not SQLite hard limits); see the pre-coding
# validation record. Over-limit input is rejected, never truncated.
MAX_QUERY_CHARS = 4096
MAX_QUERY_TOKENS = 256

# Public contract for the lexical/anchor search primitives.
DEFAULT_K = 10
MIN_K = 1
MAX_K = 100

# Unicode letters/digits form tokens; punctuation, whitespace and underscore separate.
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


class LexicalQueryError(ValueError):
    """Invalid lexical query input (wrong type, blank, or over the limits)."""


def query_tokens(query: str) -> list[str]:
    """Return the candidate searchable tokens of ``query`` (no validation)."""
    if not isinstance(query, str):
        raise LexicalQueryError("query must be a string")
    return _TOKEN_RE.findall(query)


def build_match(query: str) -> str | None:
    """Build a safe FTS5 MATCH expression, or ``None`` when there is nothing to search.

    - Non-string or blank input is rejected.
    - Zero tokens (e.g. pure punctuation) returns ``None`` so callers answer with an
      empty result instead of handing FTS5 an invalid expression.
    - Input over :data:`MAX_QUERY_CHARS` / :data:`MAX_QUERY_TOKENS` is rejected.
    """
    if not isinstance(query, str):
        raise LexicalQueryError("query must be a string")
    if not query.strip():
        raise LexicalQueryError("query must not be empty")
    if len(query) > MAX_QUERY_CHARS:
        raise LexicalQueryError(f"query exceeds {MAX_QUERY_CHARS} characters")
    found = _TOKEN_RE.findall(query)
    if not found:
        return None
    if len(found) > MAX_QUERY_TOKENS:
        raise LexicalQueryError(f"query exceeds {MAX_QUERY_TOKENS} tokens")
    # Each token is quoted so user input can never become FTS5 syntax.
    return " OR ".join('"' + token.replace('"', '""') + '"' for token in found)


def normalize_k(k) -> int:
    """Validate and coerce ``k`` to the public 1–100 range."""
    if isinstance(k, bool) or not isinstance(k, int):
        raise LexicalQueryError("k must be an integer")
    if not (MIN_K <= k <= MAX_K):
        raise LexicalQueryError(f"k must be between {MIN_K} and {MAX_K}")
    return k
