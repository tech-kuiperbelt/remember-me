"""Thread-safe serialization wrapper for embedding providers.

Online requests, background imports and online recovery may run on different
threads, but a provider's local model / HTTP session is not necessarily
reentrant. This adapter serializes every provider call behind one reentrant lock
while forwarding the public surface (``spec``, ``embed_*``, ``warmup``,
``close``). It is also the single place that translates provider failures into
``EmbeddingError``.

It never changes provider behavior: batching, query/document prefixes,
dimension and token validation stay inside the wrapped provider. The lock only
orders calls, and the service still completes inference before acquiring a
storage transaction lock.
"""

import logging
import threading

from .embed import EmbeddingError

logger = logging.getLogger("provider")


def _embedding_call(fn, *args):
    """Run one provider embedding call, translating failures to EmbeddingError."""
    try:
        return fn(*args)
    except EmbeddingError:
        raise
    except Exception as exc:
        logger.error("embedding provider call failed: %s", exc, exc_info=True)
        raise EmbeddingError(f"embedding provider failed: {exc}") from exc


class SerializedProvider:
    def __init__(self, provider):
        self._provider = provider
        self._lock = threading.RLock()

    @property
    def spec(self):
        return self._provider.spec

    def embed_document(self, text):
        with self._lock:
            return _embedding_call(self._provider.embed_document, text)

    def embed_query(self, text):
        with self._lock:
            return _embedding_call(self._provider.embed_query, text)

    def embed_documents(self, texts):
        with self._lock:
            return _embedding_call(self._provider.embed_documents, texts)

    def warmup(self):
        with self._lock:
            return self._provider.warmup()

    def close(self):
        with self._lock:
            close = getattr(self._provider, "close", None)
            if callable(close):
                return close()


def wrap_provider(provider):
    """Wrap a provider once; ``None`` and already-wrapped values pass through."""
    if provider is None or isinstance(provider, SerializedProvider):
        return provider
    return SerializedProvider(provider)
