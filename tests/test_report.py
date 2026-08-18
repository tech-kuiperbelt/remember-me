"""Tests for the memory_report service function (search-result feedback).

The report tool itself is pure record-keeping: service.memory_report validates
and normalizes ratings; the audit journal records the event at the MCP
boundary (covered by tests/test_audit.py). No storage/DB is involved here.
"""

import pytest

from app.service import memory_report, _normalize_rating


# -- rating normalization --

@pytest.mark.parametrize("raw,expected", [
    (5, 5),          # in range
    (0, 0),          # neutral
    (-10, -10),      # lower bound
    (10, 10),        # upper bound
    (15, 10),        # clamp high
    (-20, -10),      # clamp low
    (3.5, 4),        # round half up
    (-2.5, -2),      # round half away from zero
    (7.2, 7),        # round down
])
def test_normalize_rating_valid(raw, expected):
    assert _normalize_rating(raw) == expected


@pytest.mark.parametrize("raw", ["abc", None, True, False, [1, 2], {"a": 1}])
def test_normalize_rating_skips_non_numeric(raw):
    assert _normalize_rating(raw) is None


# -- memory_report --

def test_report_requires_search_id():
    with pytest.raises(ValueError):
        memory_report(None, [])
    with pytest.raises(ValueError):
        memory_report("", [])
    with pytest.raises(ValueError):
        memory_report("se_x", "not-a-list")


def test_report_normalizes_and_counts():
    result = memory_report("se_abcd", [
        {"m_id": "a", "rating": 8},       # kept
        {"m_id": "b", "rating": 99},      # clamped to 10
        {"m_id": "c", "rating": -99},     # clamped to -10
        {"m_id": "d", "rating": 3.6},     # rounded to 4
        {"m_id": "e", "rating": "bad"},   # skipped
        {"m_id": "", "rating": 5},        # skipped (empty m_id)
        {"not_a_dict": True},             # skipped
        {"m_id": "f", "rating": 0},       # kept (neutral)
    ])
    assert result["recorded"] is True
    assert result["count"] == 5           # a, b, c, d, f


def test_report_returns_count_only():
    result = memory_report("se_abcd", [{"m_id": "a", "rating": 1}])
    assert set(result.keys()) == {"recorded", "count"}


def test_report_empty_ratings_ok():
    assert memory_report("se_abcd", []) == {"recorded": True, "count": 0}
    assert memory_report("se_abcd", None) == {"recorded": True, "count": 0}
