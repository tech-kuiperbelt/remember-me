"""Real filtered pagination, snapshot consistency and HTTP compatibility."""
import sqlite3

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from app.storage.sqlite import SQLiteStorage
from app.service import InvalidSearchFilter
from app.domain.namespace_policy import NamespaceNotFound


def add(store, text, namespace="default", metadata=None):
    return store.add(text, [1., 0.], namespace=namespace, metadata=metadata)["id"]


def test_global_sort_filter_total_and_boundaries(service):
    store = service.storage
    ids = [add(store, f"memory {i}", "a", {"keywords": ["cache", "a,b"],
           "categories": ["design"]}) for i in range(41)]
    add(store, "excluded", "b", {"keywords": ["cache"], "categories": ["bugfix"]})
    store.conn.execute("UPDATE memories SET updated_at = '2026-01-01T00:00:00Z'")
    store.conn.execute("UPDATE memories SET updated_at = '2026-02-01T00:00:00Z' WHERE id=?", (ids[0],))
    store.conn.commit()
    query = dict(namespace="a", categories=["other", "design"], keywords=["absent", "a,b"])
    first = service.memory_list_page(**query)
    assert first["total"] == 41 and len(first["items"]) == 20
    assert first["items"][0]["id"] == ids[0]
    expected = [ids[0], *sorted(ids[1:])]
    pages = [service.memory_list_page(offset=offset, **query) for offset in (0, 20, 40)]
    assert [m["id"] for page in pages for m in page["items"]] == expected
    assert all(page["total"] == 41 for page in pages)
    assert service.memory_list_page(offset=100, **query) == {"items": [], "total": 41}
    assert service.memory_list_page(namespace="a", keywords=["missing"])["total"] == 0
    assert not {"embedding", "version", "synced", "deleted"} & first["items"][0].keys()
    store.delete(ids[0])
    assert service.memory_list_page(**query)["total"] == 40


def test_timezone_order_invalid_date_fallback_and_strict_range(service):
    store = service.storage
    a, b, c = [add(store, text) for text in ("early", "later", "invalid")]
    for mid, updated, created in [
        (a, "2026-01-01T08:00:00+08:00", "2025-01-01T00:00:00Z"),
        (b, "2026-01-01T01:00:00Z", "2025-01-01T00:00:00Z"),
        (c, "bad", "2026-01-01T02:00:00Z"),
    ]:
        store.conn.execute("UPDATE memories SET updated_at=?,created_at=? WHERE id=?", (updated, created, mid))
    store.conn.commit()
    assert [m["id"] for m in service.memory_list_page()["items"]] == [c, b, a]
    result = service.memory_list_page(updated_after="2026-01-01T00:00:00Z",
                                      updated_before="2026-01-01T02:00:00Z")
    assert result["total"] == 1 and result["items"][0]["id"] == b


@pytest.mark.parametrize("sort_by", ["created_at", "updated_at"])
def test_microsecond_order_strict_boundaries_and_timezone_equivalence(service, sort_by):
    store = service.storage
    ids = sorted(add(store, f"microsecond {i}") for i in range(4))
    timestamps = ["2026-01-01T00:00:00.000100+00:00",
                  "2026-01-01T00:00:00.000200+00:00",
                  "2026-01-01T00:00:00.000300+00:00",
                  "2026-01-01T08:00:00.000300+08:00"]
    for mid, stamp in zip(ids, timestamps):
        store.conn.execute("UPDATE memories SET created_at=?, updated_at=? WHERE id=?", (stamp, stamp, mid))
    store.conn.commit()
    page = service.memory_list_page(sort_by=sort_by)
    assert [m["id"] for m in page["items"]] == [ids[2], ids[3], ids[1], ids[0]]
    after = service.memory_list_page(updated_after=timestamps[0])
    assert after["total"] == 3
    before = service.memory_list_page(updated_before=timestamps[1])
    assert before["total"] == 1 and before["items"][0]["id"] == ids[0]
    between = service.memory_list_page(updated_after=timestamps[0], updated_before=timestamps[3])
    assert between["total"] == 1 and between["items"][0]["id"] == ids[1]


@pytest.mark.parametrize("boundary", [
    {"updated_after": "2026-01-01T00:00:00.000100Z"},
    {"updated_before": "2026-01-01T00:00:00.000300Z"},
])
def test_submillisecond_record_is_not_lost_at_strict_boundary(service, boundary):
    mid = add(service.storage, "submillisecond boundary")
    service.storage.conn.execute("UPDATE memories SET updated_at=? WHERE id=?",
                                 ("2026-01-01T00:00:00.000200+00:00", mid))
    service.storage.conn.commit()
    page = service.memory_list_page(**boundary)
    assert page["total"] == 1 and page["items"][0]["id"] == mid


def test_microsecond_fallback_and_pre_epoch_time(service):
    store = service.storage
    early, fallback = sorted(add(store, f"pre-epoch {i}") for i in range(2))
    store.conn.execute("UPDATE memories SET updated_at=?,created_at=? WHERE id=?",
                       ("1969-12-31T23:59:59.999998Z", "1969-12-31T23:59:59.999998Z", early))
    store.conn.execute("UPDATE memories SET updated_at='invalid',created_at=? WHERE id=?",
                       ("1969-12-31T23:59:59.999999Z", fallback))
    store.conn.commit()
    assert [m["id"] for m in service.memory_list_page()["items"]] == [fallback, early]
    # Date fallback is for ordering only; range filtering requires a valid update.
    assert service.memory_list_page(updated_before="1969-12-31T23:59:59.999999Z")["total"] == 1


@pytest.mark.parametrize("read_only", [False, True])
def test_precision_survives_connection_open_and_reopen(tmp_path, read_only):
    path = str(tmp_path / "precision.db")
    store = SQLiteStorage(path)
    ids = sorted(add(store, f"large-year {i}") for i in range(2))
    for mid, microseconds in zip(ids, ("000100", "000200")):
        stamp = f"9999-01-01T00:00:00.{microseconds}+00:00"
        store.conn.execute("UPDATE memories SET created_at=?,updated_at=? WHERE id=?", (stamp, stamp, mid))
    store.conn.commit()
    store.close()
    store = SQLiteStorage(path, read_only=read_only)
    try:
        assert [m["id"] for m in store.list_memories_page()["items"]] == ids[::-1]
        store.close_connection()
        store.reopen_connection()
        assert [m["id"] for m in store.list_memories_page()["items"]] == ids[::-1]
    finally:
        store.close()


def test_page_read_snapshot_includes_no_concurrent_commit(tmp_path):
    path = str(tmp_path / "snapshot.db")
    store = SQLiteStorage(path)
    writer = sqlite3.connect(path)
    try:
        store.conn.execute("PRAGMA journal_mode=WAL")
        add(store, "original")
        fired = False

        def during_page(sql):
            nonlocal fired
            if sql.startswith("SELECT * FROM memories WHERE") and not fired:
                fired = True
                writer.execute("UPDATE memories SET deleted=1")
                writer.commit()

        store.conn.set_trace_callback(during_page)
        result = store.list_memories_page()
        assert fired and result["total"] == len(result["items"]) == 1
        store.conn.set_trace_callback(None)
        assert store.list_memories_page()["total"] == 0
    finally:
        writer.close()
        store.close()


def test_page_does_not_commit_callers_transaction(storage):
    mid = add(storage, "original")
    storage.conn.execute("UPDATE memories SET text='uncommitted' WHERE id=?", (mid,))
    assert storage.list_memories_page()["items"][0]["text"] == "uncommitted"
    storage.conn.rollback()
    assert storage.get(mid)["text"] == "original"


@pytest.mark.parametrize("changes", [
    {"limit": 1}, {"limit": True}, {"offset": -1}, {"offset": 2**63},
    {"sort_by": "text"}, {"updated_after": "2026-01-01"},
    {"updated_after": "invalid"}, {"updated_after": "2026-01-01T00:00:00Z",
                                  "updated_before": "2026-01-01T00:00:00Z"},
    {"keywords": ["word" + str(i) for i in range(21)]}, {"categories": "design"},
])
def test_service_rejects_invalid_page_query(service, changes):
    with pytest.raises(InvalidSearchFilter):
        service.memory_list_page(**changes)


def test_unknown_namespace_is_not_an_empty_page(service):
    with pytest.raises(NamespaceNotFound):
        service.memory_list_page(namespace="absent")


def test_http_page_route_defaults_comma_value_and_legacy_response(admin, service):
    mid = add(service.storage, "comma tag", metadata={"keywords": ["a,b"], "categories": ["design"]})
    with TestClient(Starlette(routes=admin.routes)) as client:
        response = client.get("/api/memories/page", params=[("keywords", "a,b"), ("categories", "design")])
        assert response.status_code == 200
        assert response.json()["total"] == 1 and response.json()["items"][0]["id"] == mid
        assert isinstance(client.get("/api/memories").json(), list)
        assert client.get("/api/memories/" + mid).json()["text"] == "comma tag"
        invalid = client.get("/api/memories/page?namespace=all")
        assert invalid.status_code == 400 and invalid.json()["response_code"] == "invalid_namespace"
        unknown = client.get("/api/memories/page?namespace=absent")
        assert unknown.status_code == 400 and unknown.json()["response_code"] == "namespace_not_found"


@pytest.mark.parametrize("query", ["limit=5", "limit=20&limit=50", "offset=-1", "offset=1.5",
    "offset=9223372036854775808", "sort_by=text", "unknown=x", "updated_after=not-a-date"])
def test_http_page_invalid_query_is_400(admin, query):
    with TestClient(Starlette(routes=admin.routes)) as client:
        response = client.get("/api/memories/page?" + query)
        assert response.status_code == 400
        assert response.json()["response_code"] == "invalid_request"
