"""Strict formal X.Y.Z versions; no Git identity or prerelease inference."""
import re


_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", re.ASCII)


def parse_version(value: str) -> tuple[int, int, int]:
    """Return numeric components, rejecting prefixes, suffixes and leading zeros."""
    if not isinstance(value, str) or not (match := _VERSION.fullmatch(value)):
        raise ValueError("version must be a formal X.Y.Z string")
    return tuple(int(component) for component in match.groups())


def compare_versions(left: str, right: str) -> int:
    """Compare valid formal versions numerically; Unknown is never comparable."""
    a, b = parse_version(left), parse_version(right)
    return (a > b) - (a < b)
