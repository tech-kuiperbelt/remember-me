"""Shared strict updated-time bounds for memory retrieval."""
from datetime import datetime, timezone


def normalize_updated_range(updated_after=None, updated_before=None):
    bounds = []
    for name, raw in (("updated_after", updated_after), ("updated_before", updated_before)):
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            bounds.append(None)
            continue
        try:
            stamp = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                raise ValueError("timezone required")
            bounds.append(stamp.astimezone(timezone.utc))
        except (ValueError, TypeError, AttributeError, OverflowError) as exc:
            raise ValueError(f"{name} must be an ISO 8601 timestamp with timezone") from exc
    after, before = bounds
    if after and before and after >= before:
        raise ValueError("updated_after must be before updated_before")
    return tuple(stamp.isoformat() if stamp else None for stamp in bounds)
