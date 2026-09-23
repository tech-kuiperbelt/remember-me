"""Embedding providers used by the memory service.

Ollama remains the default. FastEmbed and Sentence Transformers are optional
and imported only when selected, so the base server keeps its small dependency
set.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
from dataclasses import dataclass
from typing import Iterable, Optional, Protocol

import numpy as np
import requests


OLLAMA_BASE_URL = "http://localhost:11434"
EMBED_MODEL = "bge-m3"
DEFAULT_MAX_INPUT_CHARS = 8_000


class EmbeddingError(RuntimeError):
    """Base error for embedding generation failures."""


class EmbeddingInputTooLong(EmbeddingError):
    """The memory/query exceeds the configured model input safety limit."""

    def __init__(self, length: int, limit: int, unit: str = "characters"):
        self.length = length
        self.limit = limit
        self.unit = unit
        super().__init__(
            f"embedding input is too long ({length} {unit}; limit {limit}). "
            "Store long content as a project document and save only its summary and path in memory."
        )


@dataclass(frozen=True)
class EmbeddingSpec:
    provider: str
    model: str
    dimension: int
    normalized: bool = False
    query_prefix: str = ""
    document_prefix: str = ""
    max_input_chars: int = DEFAULT_MAX_INPUT_CHARS
    max_input_tokens: int = 0

    @property
    def profile_id(self) -> str:
        """Stable identity for one comparable vector space."""
        payload = json.dumps({
            "provider": self.provider,
            "model": self.model,
            "dimension": self.dimension,
            "normalized": self.normalized,
            "query_prefix": self.query_prefix,
            "document_prefix": self.document_prefix,
        }, sort_keys=True, separators=(",", ":"))
        return "ep_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def as_dict(self) -> dict:
        return {
            "profile_id": self.profile_id,
            "provider": self.provider,
            "model": self.model,
            "dimension": self.dimension,
            "normalized": self.normalized,
            "query_prefix": self.query_prefix,
            "document_prefix": self.document_prefix,
            "max_input_chars": self.max_input_chars,
            "max_input_tokens": self.max_input_tokens,
        }


class Embedder(Protocol):
    spec: EmbeddingSpec

    def embed_document(self, text: str) -> list[float]: ...
    def embed_query(self, text: str) -> list[float]: ...
    def embed_documents(self, texts: Iterable[str]) -> list[list[float]]: ...
    def warmup(self) -> None: ...


def _bypass_proxy(url: str) -> dict | None:
    """Disable environment proxies for local Ollama endpoints."""
    host = url.split("://", 1)[-1].split("/", 1)[0]
    if host in ("localhost", "127.0.0.1", "::1", "[::1]"):
        return {"http": None, "https": None}
    return None


class _BaseEmbedder:
    spec: EmbeddingSpec

    def _prepare(self, text: str, prefix: str) -> str:
        if not isinstance(text, str) or not text.strip():
            raise EmbeddingError("embedding input must be a non-empty string")
        if self.spec.max_input_chars > 0 and len(text) > self.spec.max_input_chars:
            raise EmbeddingInputTooLong(len(text), self.spec.max_input_chars)
        return prefix + text

    @staticmethod
    def _validate_vector(vector, expected_dimension: int) -> list[float]:
        array = np.asarray(vector, dtype=np.float32)
        if array.ndim != 1 or array.size != expected_dimension:
            raise EmbeddingError(
                f"embedding dimension mismatch: expected {expected_dimension}, got "
                f"{array.size if array.ndim == 1 else 'non-vector'}"
            )
        if not np.isfinite(array).all():
            raise EmbeddingError("embedding contains non-finite values")
        return array.tolist()

    def embed_documents(self, texts: Iterable[str]) -> list[list[float]]:
        return [self.embed_document(text) for text in texts]

    def warmup(self) -> None:
        self.embed_query("warmup")


class OllamaEmbedder(_BaseEmbedder):
    def __init__(self, base_url: str = OLLAMA_BASE_URL, model: str = EMBED_MODEL,
                 dimension: int = 1024, max_input_chars: int = DEFAULT_MAX_INPUT_CHARS,
                 timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.spec = EmbeddingSpec(
            provider="ollama", model=model, dimension=dimension,
            normalized=False, max_input_chars=max_input_chars,
        )

    def _request(self, text: str) -> list[float]:
        url = f"{self.base_url}/api/embeddings"
        response = requests.post(
            url,
            json={"model": self.spec.model, "prompt": text},
            proxies=_bypass_proxy(url),
            timeout=self.timeout,
        )
        if not response.ok:
            try:
                message = str(response.json().get("error", ""))
            except (ValueError, AttributeError):
                message = response.text or ""
            lowered = message.lower()
            if "context length" in lowered or "input length" in lowered:
                raise EmbeddingInputTooLong(len(text), self.spec.max_input_chars)
            response.raise_for_status()
        try:
            vector = response.json()["embedding"]
        except (ValueError, KeyError, TypeError) as exc:
            raise EmbeddingError("Ollama returned no embedding vector") from exc
        return self._validate_vector(vector, self.spec.dimension)

    def embed_document(self, text: str) -> list[float]:
        return self._request(self._prepare(text, self.spec.document_prefix))

    def embed_query(self, text: str) -> list[float]:
        return self._request(self._prepare(text, self.spec.query_prefix))


class FastEmbedEmbedder(_BaseEmbedder):
    """Optional local ONNX provider backed by qdrant/fastembed."""

    def __init__(self, model: str, cache_dir: str | None = None,
                 offline: bool = False, max_input_chars: int = DEFAULT_MAX_INPUT_CHARS,
                 max_input_tokens: int = 512, query_prefix: str = "",
                 document_prefix: str = ""):
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise EmbeddingError(
                "FastEmbed provider requires the optional dependency: "
                "pip install -r requirements-fastembed.txt"
            ) from exc

        supported = {item["model"]: item for item in TextEmbedding.list_supported_models()}
        if model not in supported:
            raise EmbeddingError(
                f"FastEmbed model is not supported by this installation: {model}"
            )
        description = supported[model]
        dimension = int(description["dim"])
        kwargs = {"model_name": model}
        signature = inspect.signature(TextEmbedding)
        if cache_dir and "cache_dir" in signature.parameters:
            kwargs["cache_dir"] = cache_dir
        supports_extra_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        if "local_files_only" in signature.parameters or supports_extra_kwargs:
            kwargs["local_files_only"] = offline
        self._model = TextEmbedding(**kwargs)
        self.spec = EmbeddingSpec(
            provider="fastembed", model=model, dimension=dimension,
            normalized=True, query_prefix=query_prefix,
            document_prefix=document_prefix, max_input_chars=max_input_chars,
            max_input_tokens=max_input_tokens,
        )

    def _prepare_fastembed(self, text: str, prefix: str) -> str:
        prepared = self._prepare(text, prefix)
        if self.spec.max_input_tokens > 0 and hasattr(self._model, "token_count"):
            token_count = int(self._model.token_count(prepared))
            if token_count > self.spec.max_input_tokens:
                raise EmbeddingInputTooLong(
                    token_count, self.spec.max_input_tokens, unit="tokens"
                )
        return prepared

    def _embed(self, texts: list[str]) -> list[list[float]]:
        values = list(self._model.embed(texts))
        if len(values) != len(texts):
            raise EmbeddingError(
                f"FastEmbed returned {len(values)} vectors for {len(texts)} inputs"
            )
        result = []
        for value in values:
            vector = np.asarray(
                self._validate_vector(value, self.spec.dimension), dtype=np.float32
            )
            norm = float(np.linalg.norm(vector))
            if norm <= 0:
                raise EmbeddingError("FastEmbed returned a zero-length vector")
            result.append((vector / norm).tolist())
        return result

    def embed_document(self, text: str) -> list[float]:
        return self._embed([self._prepare_fastembed(text, self.spec.document_prefix)])[0]

    def embed_query(self, text: str) -> list[float]:
        return self._embed([self._prepare_fastembed(text, self.spec.query_prefix)])[0]

    def embed_documents(self, texts: Iterable[str]) -> list[list[float]]:
        prepared = [self._prepare_fastembed(text, self.spec.document_prefix) for text in texts]
        return self._embed(prepared) if prepared else []


class SentenceTransformersEmbedder(_BaseEmbedder):
    """Optional in-process PyTorch provider backed by sentence-transformers."""

    def __init__(self, model: str, cache_dir: str | None = None,
                 offline: bool = False, device: str | None = None,
                 revision: str | None = None,
                 max_input_chars: int = DEFAULT_MAX_INPUT_CHARS,
                 max_input_tokens: int = 8192, batch_size: int = 8,
                 query_prefix: str = "", document_prefix: str = ""):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingError(
                "Sentence Transformers provider requires the optional dependency: "
                "pip install -r requirements-sentence-transformers.txt"
            ) from exc

        kwargs = {}
        signature = inspect.signature(SentenceTransformer)
        supports_extra_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )

        def supported(name: str) -> bool:
            return name in signature.parameters or supports_extra_kwargs

        if cache_dir and supported("cache_folder"):
            kwargs["cache_folder"] = cache_dir
        if device and supported("device"):
            kwargs["device"] = device
        if revision and supported("revision"):
            kwargs["revision"] = revision
        if supported("local_files_only"):
            kwargs["local_files_only"] = offline

        self._model = SentenceTransformer(model, **kwargs)
        dimension_fn = getattr(self._model, "get_embedding_dimension", None)
        if dimension_fn is None:
            dimension_fn = self._model.get_sentence_embedding_dimension
        dimension = dimension_fn()
        if dimension is None:
            raise EmbeddingError(
                f"Sentence Transformers returned no embedding dimension for {model}"
            )
        self.batch_size = batch_size
        model_identity = f"{model}@{revision}" if revision else model
        self.spec = EmbeddingSpec(
            provider="sentence_transformers", model=model_identity,
            dimension=int(dimension), normalized=True,
            query_prefix=query_prefix, document_prefix=document_prefix,
            max_input_chars=max_input_chars, max_input_tokens=max_input_tokens,
        )

    def _prepare_sentence_transformers(self, text: str, prefix: str) -> str:
        prepared = self._prepare(text, prefix)
        tokenizer = getattr(self._model, "tokenizer", None)
        if self.spec.max_input_tokens > 0 and tokenizer is not None:
            encoded = tokenizer(
                prepared, add_special_tokens=True, truncation=False,
                return_attention_mask=False, return_token_type_ids=False,
            )
            input_ids = encoded.get("input_ids", [])
            token_count = len(input_ids)
            if token_count > self.spec.max_input_tokens:
                raise EmbeddingInputTooLong(
                    token_count, self.spec.max_input_tokens, unit="tokens"
                )
        return prepared

    def _embed(self, texts: list[str]) -> list[list[float]]:
        values = np.asarray(self._model.encode(
            texts,
            batch_size=self.batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        ), dtype=np.float32)
        if values.ndim == 1 and len(texts) == 1:
            values = values.reshape(1, -1)
        if values.ndim != 2 or values.shape[0] != len(texts):
            count = values.shape[0] if values.ndim > 0 else 0
            raise EmbeddingError(
                f"Sentence Transformers returned {count} vectors for {len(texts)} inputs"
            )
        return [self._validate_vector(value, self.spec.dimension) for value in values]

    def embed_document(self, text: str) -> list[float]:
        prepared = self._prepare_sentence_transformers(text, self.spec.document_prefix)
        return self._embed([prepared])[0]

    def embed_query(self, text: str) -> list[float]:
        prepared = self._prepare_sentence_transformers(text, self.spec.query_prefix)
        return self._embed([prepared])[0]

    def embed_documents(self, texts: Iterable[str]) -> list[list[float]]:
        prepared = [
            self._prepare_sentence_transformers(text, self.spec.document_prefix)
            for text in texts
        ]
        return self._embed(prepared) if prepared else []


def create_embedder(provider: str | None = None, *, environment=None) -> Embedder:
    env = os.environ if environment is None else environment
    provider = (provider or env.get("EMBED_PROVIDER", "ollama")).strip().lower()
    max_chars = int(env.get("EMBED_MAX_INPUT_CHARS", str(DEFAULT_MAX_INPUT_CHARS)))
    if provider == "ollama":
        return OllamaEmbedder(
            base_url=env.get("OLLAMA_BASE_URL", OLLAMA_BASE_URL),
            model=env.get("EMBED_MODEL", EMBED_MODEL),
            dimension=int(env.get("EMBED_DIM", "1024")),
            max_input_chars=max_chars,
            timeout=float(env.get("EMBED_TIMEOUT", "120")),
        )
    if provider == "fastembed":
        model = env.get("FASTEMBED_MODEL", "intfloat/multilingual-e5-large")
        default_query_prefix = "query: " if "e5" in model.lower() else ""
        default_document_prefix = "passage: " if "e5" in model.lower() else ""
        return FastEmbedEmbedder(
            model=model,
            cache_dir=env.get("FASTEMBED_CACHE_DIR") or None,
            offline=env.get("FASTEMBED_OFFLINE", "false").lower() in ("1", "true", "yes"),
            max_input_chars=max_chars,
            max_input_tokens=int(env.get("FASTEMBED_MAX_INPUT_TOKENS", "512")),
            query_prefix=env.get("FASTEMBED_QUERY_PREFIX", default_query_prefix),
            document_prefix=env.get("FASTEMBED_DOCUMENT_PREFIX", default_document_prefix),
        )
    if provider in ("sentence_transformers", "sentence-transformers"):
        return SentenceTransformersEmbedder(
            model=env.get("SENTENCE_TRANSFORMERS_MODEL", "BAAI/bge-m3"),
            cache_dir=env.get("SENTENCE_TRANSFORMERS_CACHE_DIR") or None,
            offline=env.get(
                "SENTENCE_TRANSFORMERS_OFFLINE", "false"
            ).lower() in ("1", "true", "yes"),
            device=env.get("SENTENCE_TRANSFORMERS_DEVICE") or None,
            revision=env.get("SENTENCE_TRANSFORMERS_REVISION") or None,
            max_input_chars=max_chars,
            max_input_tokens=int(env.get(
                "SENTENCE_TRANSFORMERS_MAX_INPUT_TOKENS", "8192"
            )),
            batch_size=int(env.get("SENTENCE_TRANSFORMERS_BATCH_SIZE", "8")),
            query_prefix=env.get("SENTENCE_TRANSFORMERS_QUERY_PREFIX", ""),
            document_prefix=env.get("SENTENCE_TRANSFORMERS_DOCUMENT_PREFIX", ""),
        )
    raise EmbeddingError(f"unknown EMBED_PROVIDER: {provider}")


def embed(text: str, base_url: Optional[str] = None,
          model: Optional[str] = None) -> list[float]:
    """Backward-compatible one-shot Ollama document embedding helper."""
    return OllamaEmbedder(
        base_url=base_url or OLLAMA_BASE_URL,
        model=model or EMBED_MODEL,
    ).embed_document(text)
