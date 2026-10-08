"""Main-case tests for DualStorage write delegation.

DualStorage delegates writes to primary (SQLite) and syncs to secondary via
a background outbox thread. We use a long sync interval so the background
thread stays idle during tests, and inject mock primaries/secondaries to
verify delegation without touching real files.
"""

from unittest.mock import MagicMock

import pytest

from app.storage.dual import DualStorage


@pytest.fixture
def mock_primary():
    return MagicMock()


@pytest.fixture
def mock_secondary():
    return MagicMock()


@pytest.fixture
def dual(mock_primary, mock_secondary):
    """A delegation-only DualStorage with no background maintenance thread."""
    d = DualStorage(primary=mock_primary, secondary=mock_secondary,
                    sync_interval=3600, start_background=False)
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


def test_dual_import_records_delegates_to_primary(dual, mock_primary):
    """Prepared JSONL imports are committed only by the SQLite primary."""
    expected = {"imported": 1, "skipped": 0, "renamed": 0,
                "imported_ids": ["x"]}
    mock_primary.import_records.return_value = expected
    records = [{"id": "x"}]
    memory_vectors = {"x": [0.1]}
    index_vectors = {("keywords", "k"): [0.1]}

    result = dual.import_records(records, memory_vectors, index_vectors, "profile")

    assert result == expected
    mock_primary.import_records.assert_called_once_with(
        records, memory_vectors, index_vectors, "profile"
    )


def test_dual_update_delete_clear_delegate(dual, mock_primary):
    """update/delete/clear all delegate to primary."""
    dual.update("id1", text="new", clear_summary=True)
    dual.delete("id2")
    dual.clear(namespace="ns1")
    mock_primary.update.assert_called_once_with(
        "id1", "new", None, None, None, None,
        index_embeddings=None, clear_summary=True)
    assert mock_primary.delete.call_count == 1
    assert mock_primary.clear.call_count == 1


def test_dual_list_namespaces_delegates_to_primary(dual, mock_primary):
    """The namespace existence list always comes from the SQLite primary."""
    mock_primary.list_namespaces.return_value = ["a", "b"]

    assert dual.list_namespaces() == ["a", "b"]
    mock_primary.list_namespaces.assert_called_once_with()


def test_dual_search_passes_time_scope_to_primary(dual, mock_primary):
    mock_primary.search.return_value = [{"id": "within-window"}]
    bounds = {"updated_after": "2026-01-01T00:00:00Z", "updated_before": "2027-01-01T00:00:00Z"}
    result = dual.search([1., 0.], k=1, namespace="default", **bounds)
    assert result == [{"id": "within-window"}]
    mock_primary.search.assert_called_once_with([1., 0.], k=1, namespace="default", **bounds)


@pytest.mark.parametrize("method", ["search_lexical", "search_anchor"])
def test_dual_term_search_forwards_filters(dual, mock_primary, method):
    """Lexical/anchor filters, time and min_score reach the SQLite primary."""
    getattr(mock_primary, method).return_value = [{"id": "hit"}]
    kwargs = {"k": 5, "namespace": "ns", "categories": ["design"], "keywords": ["alpha"],
              "updated_after": "2026-01-01T00:00:00Z", "updated_before": None,
              "min_score": 0.5}
    result = getattr(dual, method)("query", **kwargs)
    assert result == [{"id": "hit"}]
    getattr(mock_primary, method).assert_called_once_with("query", **kwargs)

