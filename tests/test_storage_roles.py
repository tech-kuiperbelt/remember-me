"""Business-store and disaster-replica role boundaries."""

import pytest

from app.storage import MemoryStorage, ReplicaStorage, create_storage
from app.storage.dual import DualStorage
from app.storage.ndjson import NDJSONReplica
from app.storage.sqlite import SQLiteStorage


def test_factory_exposes_only_sqlite_and_dual(tmp_path):
    sqlite = create_storage("sqlite", path=":memory:")
    dual = create_storage(
        "dual", primary_path=str(tmp_path / "primary.db"),
        secondary_path=str(tmp_path / "replica.jsonl"), start_background=False,
    )
    try:
        assert isinstance(sqlite, MemoryStorage)
        assert isinstance(dual, DualStorage)
        assert isinstance(dual.secondary, NDJSONReplica)
        assert isinstance(dual.secondary, ReplicaStorage)
        assert not isinstance(dual.secondary, MemoryStorage)
    finally:
        dual.stop()

    for removed in ("json", "ndjson", "jsonl"):
        with pytest.raises(ValueError, match="Unknown backend"):
            create_storage(removed)


def test_dual_rejects_non_ndjson_replica_path(tmp_path):
    with pytest.raises(ValueError, match="must end with"):
        create_storage(
            "dual", primary_path=str(tmp_path / "primary.db"),
            secondary_path=str(tmp_path / "replica.json"),
        )


def test_dual_derives_replica_path_from_primary(tmp_path):
    primary = tmp_path / "primary.sqlite"
    dual = create_storage(
        "dual", primary_path=str(primary), start_background=False
    )
    try:
        assert dual.secondary.path == str(tmp_path / "primary.jsonl")
    finally:
        dual.stop()


def test_dual_rejects_same_primary_and_replica_path(tmp_path):
    same = str(tmp_path / "same.ndjson")
    with pytest.raises(ValueError, match="must be different"):
        create_storage("dual", primary_path=same, secondary_path=same)


def test_replica_has_no_business_storage_methods(tmp_path):
    replica = NDJSONReplica(str(tmp_path / "replica.jsonl"))
    for method in ("add", "search", "merge", "update_summary", "get_pending_summaries"):
        assert not hasattr(replica, method)
