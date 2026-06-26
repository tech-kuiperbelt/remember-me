import os
import logging

from .embed import embed as embed_fn
from .storage import create_storage

logger = logging.getLogger("service")

STORAGE_BACKEND = os.environ.get("MEMORY_STORAGE", "sqlite")
STORAGE_PATH = os.environ.get("MEMORY_STORAGE_PATH")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3")
DEFAULT_NAMESPACE = os.environ.get("MEMORY_DEFAULT_NAMESPACE", "default")

if STORAGE_BACKEND == "json":
    path = STORAGE_PATH or "data/memories.json"
    storage = create_storage("json", path=path)
else:
    path = STORAGE_PATH or "data/memories.db"
    storage = create_storage("sqlite", path=path)


def embed(text: str) -> list[float]:
    try:
        return embed_fn(text, base_url=OLLAMA_BASE_URL, model=EMBED_MODEL)
    except Exception as e:
        logger.error("embed failed: %s", e, exc_info=True)
        raise


def strip_embedding(d: dict) -> dict:
    d.pop("embedding", None)
    return d


def memory_add(text: str, metadata: dict | None = None, namespace: str | None = None) -> dict:
    ns = namespace or DEFAULT_NAMESPACE
    logger.info("add: namespace=%s text_len=%d", ns, len(text))
    try:
        eid = storage.add(text, embed(text), metadata=metadata, namespace=ns)
        logger.info("add: ok id=%s", eid)
        return {"id": eid}
    except Exception as e:
        logger.error("add failed: %s", e, exc_info=True)
        raise


def memory_search(query: str, k: int = 5, threshold: float = 0.0,
                  namespace: str | None = None) -> list[dict]:
    logger.info("search: query_len=%d k=%d threshold=%.3f namespace=%s", len(query), k, threshold, namespace)
    try:
        query_emb = embed(query)
        results = storage.search(query_emb, k=k, threshold=threshold, namespace=namespace)
        logger.info("search: %d results", len(results))
        return [strip_embedding(r) for r in results]
    except Exception as e:
        logger.error("search failed: %s", e, exc_info=True)
        raise


def memory_list(namespace: str | None = None, limit: int = 100,
                offset: int = 0) -> list[dict]:
    logger.info("list: namespace=%s limit=%d offset=%d", namespace, limit, offset)
    try:
        results = storage.list_memories(namespace=namespace, limit=limit, offset=offset)
        logger.info("list: %d results", len(results))
        return [strip_embedding(r) for r in results]
    except Exception as e:
        logger.error("list failed: %s", e, exc_info=True)
        raise


def memory_get(memory_id: str) -> dict | None:
    logger.info("get: id=%s", memory_id)
    try:
        result = storage.get(memory_id)
        logger.info("get: %s", "found" if result else "not found")
        return strip_embedding(result) if result else None
    except Exception as e:
        logger.error("get failed: %s", e, exc_info=True)
        raise


def memory_update(memory_id: str, text: str | None = None,
                  metadata: dict | None = None,
                  namespace: str | None = None) -> dict:
    logger.info("update: id=%s has_text=%s has_metadata=%s has_ns=%s", memory_id, text is not None, metadata is not None, namespace is not None)
    try:
        embedding = embed(text) if text else None
        ok = storage.update(memory_id, text=text, embedding=embedding, metadata=metadata, namespace=namespace)
        logger.info("update: %s", "ok" if ok else "not found")
        return {"success": ok}
    except Exception as e:
        logger.error("update failed: %s", e, exc_info=True)
        raise


def memory_delete(memory_id: str) -> dict:
    logger.info("delete: id=%s", memory_id)
    try:
        ok = storage.delete(memory_id)
        logger.info("delete: %s", "ok" if ok else "not found")
        return {"success": ok}
    except Exception as e:
        logger.error("delete failed: %s", e, exc_info=True)
        raise


def memory_clear(namespace: str | None = None) -> dict:
    logger.info("clear: namespace=%s", namespace)
    try:
        n = storage.clear(namespace=namespace)
        logger.info("clear: deleted=%d", n)
        return {"deleted": n}
    except Exception as e:
        logger.error("clear failed: %s", e, exc_info=True)
        raise


def memory_stats() -> dict:
    try:
        stats = storage.get_stats()
        stats["embed_model"] = EMBED_MODEL
        stats["ollama_base_url"] = OLLAMA_BASE_URL
        return stats
    except Exception as e:
        logger.error("stats failed: %s", e, exc_info=True)
        raise


def memory_import(file_path: str, namespace_prefix: str = "") -> dict:
    logger.info("import: file=%s prefix=%s", file_path, namespace_prefix)
    try:
        backend = "json" if file_path.endswith(".json") else "sqlite"
        src = create_storage(backend, path=file_path)
        result = storage.merge(src, namespace_prefix=namespace_prefix)
        logger.info("import: %s", result)
        return result
    except Exception as e:
        logger.error("import failed: %s", e, exc_info=True)
        raise
