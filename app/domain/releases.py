"""Pure validation and immutable structured release information."""
from dataclasses import dataclass

from app.domain.versioning import parse_version


MAX_RELEASE_BYTES = 256 * 1024
MAX_RELEASE_ITEMS = 100
MAX_RELEASE_ITEM_BYTES = 4 * 1024
CONTENT_FIELDS = ("new", "improvements", "fixes", "upgrade", "breaking")


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    new: tuple[str, ...]
    improvements: tuple[str, ...]
    fixes: tuple[str, ...]
    upgrade: tuple[str, ...]
    breaking: tuple[str, ...]

    def as_dict(self):
        """Return only the six public fields, with fresh JSON-compatible arrays."""
        return {"version": self.version,
                **{field: list(getattr(self, field)) for field in CONTENT_FIELDS}}


def validate_release(value) -> ReleaseInfo:
    """Validate all required fields without defaulting or truncating content."""
    if not isinstance(value, dict):
        raise ValueError("release must be an object")
    parse_version(value.get("version"))
    content = {}
    for field in CONTENT_FIELDS:
        items = value.get(field)
        if not isinstance(items, list) or len(items) > MAX_RELEASE_ITEMS:
            raise ValueError(f"{field} must be an array of at most {MAX_RELEASE_ITEMS} items")
        for item in items:
            if (not isinstance(item, str) or not item.strip()
                    or len(item.encode("utf-8")) > MAX_RELEASE_ITEM_BYTES):
                raise ValueError(f"{field} items must be nonblank text within the byte limit")
        content[field] = tuple(items)
    return ReleaseInfo(version=value["version"], **content)
