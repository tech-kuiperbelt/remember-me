"""Formal version syntax and numeric ordering, independent of installation."""
import pytest

from app.domain.versioning import compare_versions, parse_version


@pytest.mark.parametrize("version,parts", [
    ("0.0.0", (0, 0, 0)), ("1.2.3", (1, 2, 3)),
    ("10.200.3000", (10, 200, 3000)),
])
def test_formal_version(version, parts):
    assert parse_version(version) == parts


@pytest.mark.parametrize("version", [
    None, True, 123, [], {}, "", "Unknown", "dev", "v1.0.0", "1.0", "1.0.0.0",
    "01.0.0", "1.00.0", "1.0.00", "-1.0.0", "+1.0.0", "1.0.0-rc.1",
    "1.0.0+build", " 1.0.0", "1.0.0 ", "1.0.0\n", "１.0.0", "1.٠.0",
])
def test_invalid_version(version):
    with pytest.raises(ValueError):
        parse_version(version)


@pytest.mark.parametrize("left,right,result", [
    ("1.9.0", "1.10.0", -1), ("2.0.0", "1.99.99", 1),
    ("1.0.10", "1.0.9", 1), ("1.0.0", "1.0.0", 0),
])
def test_numeric_comparison(left, right, result):
    assert compare_versions(left, right) == result
    assert compare_versions(right, left) == -result


def test_unknown_is_not_zero_or_comparable():
    with pytest.raises(ValueError):
        compare_versions(None, "1.0.0")
