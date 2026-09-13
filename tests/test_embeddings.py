"""Embedding codec, provider guard, profile migration and reindex tests."""

import pickle
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from app.embed import (
    EmbeddingInputTooLong,
    EmbeddingSpec,
    FastEmbedEmbedder,
    OllamaEmbedder,
    SentenceTransformersEmbedder,
)
from app.embedding_codec import (
    EmbeddingCodecError,
    decode_embedding,
    encode_embedding,
    inspect_embedding_blob,
)
from app.embedding_cli import _database_maintenance_lock
from app.storage_sqlite import SQLiteStorage
from app.storage_dual import DualStorage
from app.storage_ndjson import NDJSONStorage


def _spec(provider="ollama", model="bge-test", dimension=4):
    return EmbeddingSpec(provider, model, dimension).as_dict()


def test_codec_float16_is_versioned_compact_and_accurate():
    vector = [0.1, -0.2, 3.5, 0.0]
    blob = encode_embedding(vector, dtype="float16")
    info = inspect_embedding_blob(blob)

    assert info.format == "rmev"
    assert info.version == 1
    assert info.dtype == "float16"
    assert info.dimension == 4
    assert len(blob) == 12 + 4 * 2
    assert decode_embedding(blob) == pytest.approx(vector, abs=0.001)


def test_codec_reads_legacy_pickle_and_rejects_corruption():
    legacy = pickle.dumps([0.1, 0.2, 0.3])
    assert decode_embedding(legacy) == [0.1, 0.2, 0.3]
    assert inspect_embedding_blob(legacy).format == "pickle"
    with pytest.raises(EmbeddingCodecError):
        decode_embedding(b"not-an-embedding")
    with pytest.raises(EmbeddingCodecError):
        inspect_embedding_blob(encode_embedding([1.0])[:-1])


def test_provider_rejects_long_input_before_http_call(monkeypatch):
    provider = OllamaEmbedder(model="x", dimension=4, max_input_chars=5)
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("app.embed.requests.post", fail_if_called)
    with pytest.raises(EmbeddingInputTooLong, match="project document"):
        provider.embed_document("123456")
    assert called is False


def test_fastembed_provider_batches_normalizes_and_checks_tokens(monkeypatch):
    class FakeTextEmbedding:
        seen = None

        @classmethod
        def list_supported_models(cls):
            return [{"model": "fake-multilingual", "dim": 3}]

        def __init__(self, model_name, **kwargs):
            self.model_name = model_name
            self.kwargs = kwargs
            self.seen = []
            FakeTextEmbedding.instance = self

        def token_count(self, text):
            return len(text.split())

        def embed(self, texts):
            self.seen.extend(texts)
            return [np.array([3.0, 4.0, 0.0], dtype=np.float32) for _ in texts]

    monkeypatch.setitem(sys.modules, "fastembed", SimpleNamespace(TextEmbedding=FakeTextEmbedding))
    provider = FastEmbedEmbedder(
        "fake-multilingual", offline=True, query_prefix="query: ",
        document_prefix="passage: ", max_input_tokens=4,
    )

    query = provider.embed_query("hello world")
    documents = provider.embed_documents(["one", "two"])

    assert query == pytest.approx([0.6, 0.8, 0.0])
    assert len(documents) == 2
    assert FakeTextEmbedding.instance.seen == [
        "query: hello world", "passage: one", "passage: two",
    ]
    assert FakeTextEmbedding.instance.kwargs["local_files_only"] is True
    with pytest.raises(EmbeddingInputTooLong, match="tokens"):
        provider.embed_query("one two three four")


def test_sentence_transformers_provider_batches_and_checks_tokens(monkeypatch):
    class FakeTokenizer:
        def __call__(self, text, **_kwargs):
            return {"input_ids": text.split()}

    class FakeSentenceTransformer:
        instance = None

        def __init__(self, model_name, **kwargs):
            self.model_name = model_name
            self.kwargs = kwargs
            self.tokenizer = FakeTokenizer()
            self.seen = []
            FakeSentenceTransformer.instance = self

        def get_sentence_embedding_dimension(self):
            return 3

        def encode(self, texts, **kwargs):
            self.seen.extend(texts)
            self.encode_kwargs = kwargs
            return np.asarray([[0.6, 0.8, 0.0] for _ in texts], dtype=np.float32)

    monkeypatch.setitem(
        sys.modules, "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )
    provider = SentenceTransformersEmbedder(
        "BAAI/bge-m3", cache_dir="/tmp/models", offline=True, device="cpu",
        revision="abc123", max_input_tokens=4, batch_size=2,
    )

    query = provider.embed_query("hello world")
    documents = provider.embed_documents(["one", "two"])

    assert query == pytest.approx([0.6, 0.8, 0.0])
    assert len(documents) == 2
    assert provider.spec.provider == "sentence_transformers"
    assert provider.spec.model == "BAAI/bge-m3@abc123"
    assert provider.spec.dimension == 3
    assert FakeSentenceTransformer.instance.kwargs == {
        "cache_folder": "/tmp/models", "device": "cpu",
        "revision": "abc123", "local_files_only": True,
    }
    assert FakeSentenceTransformer.instance.encode_kwargs["normalize_embeddings"] is True
    with pytest.raises(EmbeddingInputTooLong, match="tokens"):
        provider.embed_query("one two three four five")


def test_legacy_inline_vectors_migrate_to_first_profile(tmp_path):
    path = tmp_path / "memory.db"
    legacy = SQLiteStorage(str(path))
    mid = legacy.add("text", [0.1, 0.2, 0.3, 0.4])["id"]
    legacy.conn.close()

    profiled = SQLiteStorage(str(path), embedding_spec=_spec())
    inline = profiled.conn.execute(
        "SELECT embedding FROM memories WHERE id=?", (mid,)
    ).fetchone()[0]
    stored = profiled.conn.execute(
        "SELECT embedding FROM memory_embeddings WHERE memory_id=?", (mid,)
    ).fetchone()[0]

    assert inline is None
    assert inspect_embedding_blob(stored).dtype == "float16"
    assert profiled.get(mid)["embedding"] == pytest.approx([0.1, 0.2, 0.3, 0.4], abs=0.001)
    assert profiled.get_stats()["embedding_profile"]["active"] == _spec()["profile_id"]


def test_profiled_storage_rejects_wrong_vector_dimension(tmp_path):
    storage = SQLiteStorage(str(tmp_path / "memory.db"), embedding_spec=_spec())

    with pytest.raises(ValueError, match="expected 4, got 2"):
        storage.add("text", [0.1, 0.2])


def test_dual_stats_exposes_primary_embedding_status(tmp_path):
    primary = SQLiteStorage(str(tmp_path / "memory.db"), embedding_spec=_spec())
    primary.add("text", [0.1, 0.2, 0.3, 0.4])
    dual = DualStorage(
        primary, NDJSONStorage(str(tmp_path / "memory.jsonl")),
        start_background=False,
    )

    stats = dual.get_stats()

    assert stats["embedding_storage"]["count"] == 1
    assert stats["embedding_profile"]["active"] == _spec()["profile_id"]


def test_configured_profile_mismatch_refuses_startup(tmp_path):
    path = tmp_path / "memory.db"
    first = SQLiteStorage(str(path), embedding_spec=_spec())
    first.conn.close()

    with pytest.raises(RuntimeError, match="offline reindex"):
        SQLiteStorage(str(path), embedding_spec=_spec(model="different"))


def test_startup_reconciles_inline_vectors_written_by_stale_process(tmp_path):
    path = tmp_path / "memory.db"
    storage = SQLiteStorage(str(path), embedding_spec=_spec())
    mid = storage.add("old", [1, 0, 0, 0])["id"]
    storage.conn.execute(
        "UPDATE memories SET embedding=? WHERE id=?",
        (encode_embedding([0, 1, 0, 0]), mid),
    )
    storage.conn.commit()
    storage.conn.close()

    reopened = SQLiteStorage(str(path), embedding_spec=_spec())

    assert reopened.conn.execute(
        "SELECT embedding FROM memories WHERE id=?", (mid,)
    ).fetchone()[0] is None
    assert reopened.get(mid)["embedding"] == pytest.approx([0, 1, 0, 0])


def test_reindex_builds_shadow_then_switches_and_can_roll_back(tmp_path):
    path = tmp_path / "memory.db"
    old_spec = _spec()
    storage = SQLiteStorage(str(path), embedding_spec=old_spec)
    first = storage.add(
        "first", [1, 0, 0, 0],
        metadata={"keywords": ["index"], "categories": ["design"]},
    )["id"]
    second = storage.add("second", [0, 1, 0, 0])["id"]
    new_spec = _spec(provider="fastembed", model="new", dimension=2)

    def embed_batch(texts):
        return [[1.0, 0.0] if text == "first" else [0.0, 1.0] for text in texts]

    progress = []
    result = storage.reindex_embeddings(
        new_spec, embed_batch, batch_size=1,
        progress_callback=lambda completed, total: progress.append((completed, total)),
    )

    assert result["reindexed"] == 2
    assert storage.get_stats()["embedding_profile"]["active"] == new_spec["profile_id"]
    assert storage.search([1.0, 0.0], k=1)[0]["id"] == first
    assert storage.search(
        [1.0, 0.0], k=1, keywords=["index"], categories=["design"]
    )[0]["id"] == first
    assert storage.get(second)["embedding"] == pytest.approx([0.0, 1.0])
    assert progress == [(1, 2), (2, 2)]

    rollback = storage.activate_embedding_profile(old_spec["profile_id"])
    assert rollback["previous_profile"] == new_spec["profile_id"]
    assert storage.search([0, 1, 0, 0], k=1)[0]["id"] == second


def test_failed_reindex_keeps_old_profile_active(tmp_path):
    storage = SQLiteStorage(str(tmp_path / "memory.db"), embedding_spec=_spec())
    storage.add("first", [1, 0, 0, 0])
    old_profile = storage.get_stats()["embedding_profile"]["active"]

    def fail(_texts):
        raise RuntimeError("provider failed")

    with pytest.raises(RuntimeError, match="provider failed"):
        storage.reindex_embeddings(_spec(model="new"), fail)

    stats = storage.get_stats()
    assert stats["embedding_profile"]["active"] == old_profile
    assert any(p["status"] == "failed" for p in stats["embedding_profile"]["profiles"])


def test_float16_preserves_sample_top_k_order():
    rng = np.random.default_rng(7)
    vectors = rng.normal(size=(64, 32)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    half = vectors.astype(np.float16).astype(np.float32)
    half /= np.linalg.norm(half, axis=1, keepdims=True)

    original_top = np.argsort(-(vectors[:8] @ vectors.T), axis=1)[:, :10]
    half_top = np.argsort(-(half[:8] @ half.T), axis=1)[:, :10]
    assert np.array_equal(original_top, half_top)


def test_embedding_maintenance_lock_rejects_parallel_command(tmp_path):
    database = str(tmp_path / "memory.db")
    with _database_maintenance_lock(database):
        with pytest.raises(RuntimeError, match="already running"):
            with _database_maintenance_lock(database):
                pass
