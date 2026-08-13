"""Keyword vocabulary trimming for the MCP stats boundary.

The full keyword_counts dict is ~26KB (~8k-13k tokens) — heavy on every
stats call. Long-tail keywords (cited once or twice) are mostly code fragments,
hashes/IDs with little retrieval value. We drop keywords cited <=
MIN_CITATIONS unless they contain CJK (Chinese business terms are
low-frequency but high value, e.g. 堆场规划 / 二次确认).

Measured on the real corpus (2026-08-13): trimming cited<=2 non-CJK saves
~61% bytes (26.2KB -> 10.3KB) while retaining ~79% of total citations and
all 47 low-frequency Chinese keywords. Marginal cost-benefit (per 1% coverage
lost, volume saved) peaks at 3.76 for mc=0->1 and 3.08 for mc=1->2, then
drops; mc=2 is the sweet spot before diminishing returns.

Tune with env: MEMORY_KEYWORDS_MIN_CITATIONS (default 2).
"""

import os
import re

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def trim_keyword_counts(keyword_counts: dict) -> dict:
    min_citations = int(os.getenv("MEMORY_KEYWORDS_MIN_CITATIONS", "2"))
    if min_citations <= 0:
        return keyword_counts
    return {
        w: c for w, c in keyword_counts.items()
        if c > min_citations or _CJK_RE.search(w)
    }
