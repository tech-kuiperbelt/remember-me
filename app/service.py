import os

from .embed import embed as embed_fn
from .storage import create_storage

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
    return embed_fn(text, base_url=OLLAMA_BASE_URL, model=EMBED_MODEL)


def strip_embedding(d: dict) -> dict:
    d.pop("embedding", None)
    return d


def memory_add(text: str, metadata: dict | None = None, namespace: str | None = None) -> dict:
    ns = namespace or DEFAULT_NAMESPACE
    eid = storage.add(text, embed(text), metadata=metadata, namespace=ns)
    return {"id": eid}


def memory_search(query: str, k: int = 5, threshold: float = 0.0,
                  namespace: str | None = None) -> list[dict]:
    query_emb = embed(query)
    results = storage.search(query_emb, k=k, threshold=threshold, namespace=namespace)
    return [strip_embedding(r) for r in results]


def memory_list(namespace: str | None = None, limit: int = 100,
                offset: int = 0) -> list[dict]:
    results = storage.list_memories(namespace=namespace, limit=limit, offset=offset)
    return [strip_embedding(r) for r in results]


def memory_get(memory_id: str) -> dict | None:
    result = storage.get(memory_id)
    return strip_embedding(result) if result else None


def memory_update(memory_id: str, text: str | None = None,
                  metadata: dict | None = None) -> dict:
    embedding = embed(text) if text else None
    ok = storage.update(memory_id, text=text, embedding=embedding, metadata=metadata)
    return {"success": ok}


def memory_delete(memory_id: str) -> dict:
    ok = storage.delete(memory_id)
    return {"success": ok}


def memory_clear(namespace: str | None = None) -> dict:
    n = storage.clear(namespace=namespace)
    return {"deleted": n}


def memory_stats() -> dict:
    stats = storage.get_stats()
    stats["embed_model"] = EMBED_MODEL
    stats["ollama_base_url"] = OLLAMA_BASE_URL
    return stats


def memory_import(file_path: str, namespace_prefix: str = "") -> dict:
    backend = "json" if file_path.endswith(".json") else "sqlite"
    src = create_storage(backend, path=file_path)
    return storage.merge(src, namespace_prefix=namespace_prefix)
