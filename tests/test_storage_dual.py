"""Main-case tests for DualStorage write delegation.

DualStorage delegates writes to primary (SQLite) and syncs to secondary via
a background outbox thread. We use a long sync interval so the background
thread stays idle during tests, and inject mock primaries/secondaries to
verify delegation without touching real files.
"""

from unittest.mock import MagicMock

import pytest

from app.storage_dual import DualStorage


@pytest.fixture
def mock_primary():
    return MagicMock()


@pytest.fixture
def mock_secondary():
    return MagicMock()


@pytest.fixture
def dual(mock_primary, mock_secondary):
    """A DualStorage whose background sync is effectively disabled."""
    d = DualStorage(primary=mock_primary, secondary=mock_secondary, sync_interval=3600)
    yield d
    d.stop()


def test_dual_add_delegates_to_primary(dual, mock_primary):
    """add() returns primary's {id, created} dict unchanged."""
    mock_primary.add.return_value = {"id": "abc123", "created": True}

    result = dual.add("text", [0.1, 0.2], namespace="ns1")

    assert result == {"id": "abc123", "created": True}
    mock_primary.add.assert_called_once()


def test_dual_add_duplicate_flag_passthrough(dual, mock_primary):
    """created=False from primary (duplicate) passes through unchanged."""
    mock_primary.add.return_value = {"id": "abc123", "created": False}

    result = dual.add("text", [0.1, 0.2], namespace="ns1")

    assert result == {"id": "abc123", "created": False}


def test_dual_merge_delegates_to_primary(dual, mock_primary):
    """merge() delegates and returns primary's result with skipped count."""
    mock_primary.merge.return_value = {
        "imported": 1, "skipped": 2, "renamed": 0, "total": 3, "imported_ids": ["x"],
    }

    result = dual.merge(MagicMock(), namespace_prefix="p_")

    assert result["skipped"] == 2
    assert result["imported"] == 1
    mock_primary.merge.assert_called_once()


def test_dual_update_delete_clear_delegate(dual, mock_primary):
    """update/delete/clear all delegate to primary."""
    dual.update("id1", text="new")
    dual.delete("id2")
    dual.clear(namespace="ns1")
    assert mock_primary.update.call_count == 1
    assert mock_primary.delete.call_count == 1
    assert mock_primary.clear.call_count == 1


def test_dual_guarded_summary_update_delegates(dual, mock_primary):
    mock_primary.update_summary_if_pending.return_value = True

    result = dual.update_summary_if_pending("id1", "text", "summary")

    assert result is True
    mock_primary.update_summary_if_pending.assert_called_once_with(
        "id1", "text", "summary"
    )
