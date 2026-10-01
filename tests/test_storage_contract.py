"""Phase 3: SQLite/dual share one MemoryStorage contract, capabilities are
explicit, and the provider boundary is uniform."""

from unittest.mock import Mock

import pytest

from app.embedding.embed import EmbeddingError, EmbeddingSpec
from app.embedding.index_value_resolver import IndexValueResolver
from app.embedding.provider_adapter import wrap_provider
from app.service import MemoryService
from app.config.settings import Settings
from app.storage import (
    MemoryStorage,
    ReplicaStorage,
    SQLitePrimaryStorage,
    StorageCapabilityError,
    create_storage,
)
from app.storage.ndjson import NDJSONReplica
from app.storage.sqlite import SQLiteStorage
from tests.conftest import FakeProvider


SPEC = EmbeddingSpec("test", "tiny", 2, normalized=True).as_dict()


def _make(backend, tmp_path):
    if backend == "sqlite":
        return create_storage("sqlite", path=":memory:", embedding_spec=SPEC)
    return create_storage(
        "dual", primary_path=str(tmp_path / "primary.db"),
        secondary_path=str(tmp_path / "replica.jsonl"),
        embedding_spec=SPEC, start_background=False,
    )


def _resolver(store):
    return IndexValueResolver(
        store, embed_query=lambda _value: [1.0, 0.0],
        embed_documents=lambda values: [[1.0, 0.0] for _value in values],
    )


@pytest.mark.parametrize("backend", ["sqlite", "dual"])
def test_business_contract_is_shared_by_sqlite_and_dual(backend, tmp_path):
    store = _make(backend, tmp_path)
    try:
        assert isinstance(store, MemoryStorage)
        assert store.active_embedding_profile() == SPEC["profile_id"]

        metadata = {"keywords": ["cache refresh"], "categories": ["bugfix"]}
        created = store.add("hello", [1.0, 0.0], metadata=metadata, namespace="ns",
                            index_embeddings=_resolver(store).prepare_embeddings(metadata))
        assert created["created"] is True
        memory_id = created["id"]

        assert store.keyword_counts("ns") == {"cache refresh": 1}
        assert store.keyword_counts("ns", categories=["bugfix"]) == {"cache refresh": 1}
        assert store.keyword_counts("ns", categories=["design"]) == {}
        assert store.exact_index_values("keywords", ["cache refresh"], "ns") == {"cache refresh"}
        candidates = store.semantic_index_values("keywords", [1.0, 0.0], "ns")
        assert candidates and candidates[0]["value"] == "cache refresh"
        assert store.index_embedding_completeness()["complete"] is True

        assert store.search([1.0, 0.0], k=5, namespace="ns")[0]["text"] == "hello"
        assert [row["text"] for row in store.list_memories(namespace="ns")] == ["hello"]
        assert store.get(memory_id)["text"] == "hello"
        assert set(store.live_memory_ids()) == {memory_id}
        assert store.get_stats()["total"] == 1

        assert store.update(memory_id, text="changed", embedding=[0.0, 1.0]) is True
        assert store.get(memory_id)["text"] == "changed"

        merged = {"keywords": ["cache merge"]}
        assert store.update_metadata(
            memory_id, merged,
            index_embeddings=_resolver(store).prepare_embeddings(merged)) is True
        assert store.keyword_counts("ns") == {"cache merge": 1}
        assert store.keyword_counts("ns", categories=["bugfix"]) == {"cache merge": 1}

        assert store.delete(memory_id) is True
        assert store.get(memory_id) is None
        assert memory_id not in set(store.live_memory_ids())

        paths = store.storage_paths()
        assert paths["backend"] == ("sqlite" if backend == "sqlite" else "dual")
    finally:
        store.stop()


def test_full_export_and_sync_are_explicit_capabilities(tmp_path):
    sqlite = create_storage("sqlite", path=":memory:")
    try:
        with pytest.raises(StorageCapabilityError):
            sqlite.force_sync()
        with pytest.raises(StorageCapabilityError):
            sqlite.export_full_snapshot(str(tmp_path / "out.jsonl"))
    finally:
        sqlite.stop()

    dual = create_storage(
        "dual", primary_path=str(tmp_path / "primary.db"),
        secondary_path=str(tmp_path / "replica.jsonl"), start_background=False,
    )
    try:
        assert dual.force_sync()["synced"] == 0
    finally:
        dual.stop()


def test_incomplete_storage_doubles_fail_at_construction():
    class IncompleteMemory(MemoryStorage):
        pass

    class IncompleteReplica(ReplicaStorage):
        pass

    with pytest.raises(TypeError):
        IncompleteMemory()
    with pytest.raises(TypeError):
        IncompleteReplica()


def test_sqlite_storage_exposes_the_primary_capability_surface(tmp_path):
    store = SQLiteStorage(str(tmp_path / "p.db"))
    try:
        assert isinstance(store, SQLitePrimaryStorage)
        # Outbox and connection-swap methods are declared, not probed at runtime.
        assert store.count_unsynced() == 0
        assert store.get_unsynced_records(limit=10) == []
        store.close_connection()
        store.reopen_connection()
    finally:
        store.close()


def test_ndjson_replica_implements_the_lock_lifecycle(tmp_path):
    replica = NDJSONReplica(str(tmp_path / "replica.jsonl"))
    assert isinstance(replica, ReplicaStorage)
    replica.acquire_writer_lock()
    replica.release_writer_lock()


def test_service_translates_provider_failures_to_embedding_error():
    class Boom:
        spec = EmbeddingSpec("test", "tiny", 2)

        def embed_document(self, text):
            raise RuntimeError("boom")

        def embed_query(self, text):
            raise RuntimeError("boom")

        def embed_documents(self, texts):
            raise RuntimeError("boom")

        def warmup(self):
            pass

    storage = SQLiteStorage(":memory:")
    service = MemoryService(Settings.from_env(), storage, Boom())
    try:
        for call in (lambda: service.embed("x"),
                     lambda: service.embed_query("x"),
                     lambda: service.embed_documents(["x"])):
            with pytest.raises(EmbeddingError, match="boom"):
                call()
    finally:
        service.close()
        storage.stop()


def test_service_delegates_query_and_batch_paths_once(service_factory):
    provider = FakeProvider()
    provider.embed_query = Mock(return_value=[1.0, 0.0, 0.0])
    provider.embed_documents = Mock(return_value=[[0.0, 1.0, 0.0]])
    service = service_factory(provider=provider)

    assert service.embed_query("query") == [1.0, 0.0, 0.0]
    assert service.embed_documents(["document"]) == [[0.0, 1.0, 0.0]]
    provider.embed_query.assert_called_once_with("query")
    provider.embed_documents.assert_called_once_with(["document"])


def test_memory_service_wraps_injected_provider_for_serialization():
    from app.embedding.provider_adapter import SerializedProvider

    storage = SQLiteStorage(":memory:")
    provider = FakeProvider()
    service = MemoryService(Settings.from_env(), storage, provider)
    try:
        assert isinstance(service.embedding_provider, SerializedProvider)
    finally:
        service.close()
        storage.stop()


def test_runtime_provider_is_wrapped_once(tmp_path):
    from tests.test_runtime import runtime

    rt = runtime(tmp_path)
    try:
        rt.start()
        assert wrap_provider(rt.provider) is rt.provider
        assert rt.service.embedding_provider is rt.provider
    finally:
        rt.close()
