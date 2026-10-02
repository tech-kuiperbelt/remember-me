"""Shared validation for retrieval numeric filter bounds.

Kept dependency-free alongside :mod:`app.domain.time_filters`. The BM25 term
searches accept an optional minimum relevance floor; validation is strict and
never coerces, so transports stay consistent with the service contract.
"""

import math


def normalize_min_score(value):
    """Normalize a ``min_score`` bound.

    - ``None`` (omitted or explicit null) means no bound.
    - Accepts a finite, non-negative JSON number (int or float, including
      scientific notation); there is no upper bound.
    - Rejects booleans, strings, negatives, NaN and Infinity.

    Raises :class:`ValueError`; callers map it to their transport error shape.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("min_score must be a finite non-negative number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("min_score must be a finite non-negative number")
    return number
