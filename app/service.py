import os
import logging
from datetime import datetime, timezone

from .embed import embed as embed_fn
from .summarize import summarize as summarize_fn
from .storage import create_storage
from . import summary_worker

logger = logging.getLogger("service")


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

STORAGE_BACKEND = os.environ.get("MEMORY_STORAGE", "sqlite")
STORAGE_PATH = os.environ.get("MEMORY_STORAGE_PATH")
STORAGE_PATH_DUAL = os.environ.get("MEMORY_STORAGE_PATH_DUAL")
SYNC_INTERVAL = int(os.environ.get("MEMORY_SYNC_INTERVAL", "300"))

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3")
SUMMARIZE_MODEL = os.environ.get("SUMMARIZE_MODEL", "qwen3:latest")
DEFAULT_NAMESPACE = os.environ.get("MEMORY_DEFAULT_NAMESPACE", "default")

if STORAGE_BACKEND == "dual":
    primary_path = STORAGE_PATH or "data/memories.db"
    secondary_path = STORAGE_PATH_DUAL or primary_path.replace(".db", ".jsonl")
    if secondary_path == primary_path:
        secondary_path = "data/memories.jsonl"
    logger.info("Dual storage: primary=%s secondary=%s sync_interval=%ds",
                primary_path, secondary_path, SYNC_INTERVAL)
    storage = create_storage("dual", primary_path=primary_path,
                             secondary_path=secondary_path,
                             sync_interval=SYNC_INTERVAL)
elif STORAGE_BACKEND == "json":
    path = STORAGE_PATH or "data/memories.json"
    logger.info("JSON storage: %s", path)
    storage = create_storage("json", path=path)
elif STORAGE_BACKEND in ("ndjson", "jsonl"):
    raise RuntimeError(
        f"MEMORY_STORAGE={STORAGE_BACKEND} is no longer allowed as a standalone backend. "
        "NDJSON/JSONL serves only as the disaster-recovery replica of the SQLite primary. "
        "Use 'sqlite' (single) or 'dual' (sqlite primary + ndjson replica)."
    )
else:
    path = STORAGE_PATH or "data/memories.db"
    logger.info("SQLite storage: %s", path)
    storage = create_storage("sqlite", path=path)

summary_worker.set_storage(storage)
summary_worker.start()


def embed(text: str) -> list[float]:
    try:
        return embed_fn(text, base_url=OLLAMA_BASE_URL, model=EMBED_MODEL)
    except Exception as e:
        logger.error("embed failed: %s", e, exc_info=True)
        raise


def summarize(text: str) -> str:
    try:
        return summarize_fn(text, base_url=OLLAMA_BASE_URL, model=SUMMARIZE_MODEL)
    except Exception as e:
        logger.error("summarize failed: %s", e, exc_info=True)
        raise


def _strip_internal_fields(d: dict) -> dict:
    """Remove storage-internal fields from a record before exposing it.

    ``embedding`` is a large vector with no LLM value; ``deleted``/``synced``
    are sync-protocol bookkeeping fields. All are dropped from every
    LLM-facing API (search/list/get/get_details).
    """
    d.pop("embedding", None)
    d.pop("deleted", None)
    d.pop("synced", None)
    return d


def _prepare_for_llm(results: list[dict]) -> list[dict]:
    """Shape search/list results for LLM consumption.

    - Internal fields (embedding/deleted/synced) are dropped by the callers.
    - Prefer ``summary``; fall back to full ``text`` when summary is empty.
    - Never expose the full ``text`` when a summary exists.
    """
    for r in results:
        if not r.get("summary"):
            r["summary"] = r.get("text", "")
        r.pop("text", None)
    return results


def memory_add(text: str, metadata: dict | None = None, namespace: str | None = None) -> dict:
    ns = namespace or DEFAULT_NAMESPACE
    logger.info("add: namespace=%s text_len=%d", ns, len(text))
    try:
        embedding = embed(text)
        metadata = _apply_keywords_to_metadata(metadata)
        result = storage.add(text, embedding, metadata=metadata, namespace=ns, summary=None)
        eid = result["id"]
        if result.get("created"):
            # New record: summary is generated in the background — do not block the caller.
            summary_worker.enqueue(eid, text)
            logger.info("add: ok id=%s (summary async)", eid)
        else:
            # Duplicate (namespace + text): existing record returned untouched.
            logger.info("add: duplicate skipped id=%s", eid)
        return {"id": eid, "created": result.get("created", True)}
    except Exception as e:
        logger.error("add failed: %s", e, exc_info=True)
        raise


def _sanitize_k(k) -> int:
    """Coerce k to a positive int.

    Non-positive k (e.g. k=-1) would slice scored[:-1] and return nearly
    everything in Python; non-int k breaks the sort/slice entirely.
    """
    try:
        k = int(k)
    except (TypeError, ValueError):
        return 100
    return k if k > 0 else 100


def _normalize_keywords(keywords) -> list[str] | None:
    """Filter, trim, lowercase and deduplicate a keywords value.

    - Non-list values → None (no keywords).
    - Non-string items are dropped; only non-empty trimmed strings remain.
    - Returns None when the result is empty (equivalent to no keywords).
    """
    if not isinstance(keywords, list):
        return None
    result = []
    seen = set()
    for kw in keywords:
        if not isinstance(kw, str):
            continue
        kw = kw.strip().lower()
        if kw and kw not in seen:
            seen.add(kw)
            result.append(kw)
    return result or None


def _apply_keywords_to_metadata(metadata: dict | None) -> dict | None:
    """Normalize metadata.keywords in place; returns the metadata dict.

    When metadata is None or has no keywords key, it is returned unchanged.
    When the normalized result is empty, keywords is set to [] so storage
    clears the previous keyword associations (update replace semantics).
    """
    if metadata is None or "keywords" not in metadata:
        return metadata
    normalized = _normalize_keywords(metadata.get("keywords"))
    metadata["keywords"] = normalized or []
    return metadata


def memory_search(query: str, k: int = 5, threshold: float = 0.0,
                  namespace: str | None = None) -> list[dict]:
    k = _sanitize_k(k)
    logger.info("search: query_len=%d k=%d threshold=%.3f namespace=%s", len(query), k, threshold, namespace)
    try:
        query_emb = embed(query)
        results = storage.search(query_emb, k=k, threshold=threshold, namespace=namespace)
        logger.info("search: %d results", len(results))
        return [_strip_internal_fields(r) for r in results]
    except Exception as e:
        logger.error("search failed: %s", e, exc_info=True)
        raise


def memory_list(namespace: str | None = None, limit: int = 100,
                offset: int = 0, sort_by: str = "created_at",
                updated_after: str | None = None,
                updated_before: str | None = None,
                keywords: list | None = None) -> list[dict]:
    if sort_by not in ("created_at", "updated_at"):
        sort_by = "created_at"
    kw = _normalize_keywords(keywords)
    logger.info("list: namespace=%s limit=%d offset=%d sort_by=%s updated_after=%s updated_before=%s keywords=%s",
                namespace, limit, offset, sort_by, updated_after, updated_before, kw)
    try:
        results = storage.list_memories(namespace=namespace, limit=limit, offset=offset,
                                        sort_by=sort_by, updated_after=updated_after,
                                        updated_before=updated_before, keywords=kw)
        logger.info("list: %d results", len(results))
        return [_strip_internal_fields(r) for r in results]
    except Exception as e:
        logger.error("list failed: %s", e, exc_info=True)
        raise


def memory_get(memory_id: str) -> dict | None:
    logger.info("get: id=%s", memory_id)
    try:
        result = storage.get(memory_id)
        logger.info("get: %s", "found" if result else "not found")
        return _strip_internal_fields(result) if result else None
    except Exception as e:
        logger.error("get failed: %s", e, exc_info=True)
        raise


def memory_update(memory_id: str, text: str | None = None,
                  metadata: dict | None = None,
                  namespace: str | None = None) -> dict:
    logger.info("update: id=%s has_text=%s has_metadata=%s has_ns=%s", memory_id, text is not None, metadata is not None, namespace is not None)
    try:
        if text is not None:
            embedding = embed(text)
            # Text changed: clear the stale summary now ("" clears it in storage),
            # regenerate in the background worker.
            summary = ""
        else:
            embedding = None
            summary = None
        metadata = _apply_keywords_to_metadata(metadata)
        ok = storage.update(memory_id, text=text, embedding=embedding,
                            metadata=metadata, namespace=namespace, summary=summary)
        if ok and text is not None:
            # Text changed — stale summary must be cleared and regenerated async.
            summary_worker.enqueue(memory_id, text)
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
        stats["summarize_model"] = SUMMARIZE_MODEL
        stats["ollama_base_url"] = OLLAMA_BASE_URL
        stats["server_time"] = _now_utc_iso()
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
        # Imported records carry metadata.keywords; make sure the SQLite
        # keyword index is rebuilt so list(keywords=...) stays consistent.
        if hasattr(storage, "rebuild_keyword_index"):
            storage.rebuild_keyword_index()
        # Summaries for imported records without one are generated in the
        # background worker (like add/update) — never block the request.
        enqueued = 0
        for mid in result.get("imported_ids", []):
            mem = storage.get(mid)
            if mem is None or mem.get("summary"):
                continue
            summary_worker.enqueue(mid, mem["text"])
            enqueued += 1
        result["summarize_queued"] = enqueued
        logger.info("import: %s", result)
        return result
    except Exception as e:
        logger.error("import failed: %s", e, exc_info=True)
        raise


def memory_get_details(memory_ids: list[str]) -> dict:
    """Fetch full content for a list of memory IDs (partial success).

    Returns {"found": [...], "missing": [...]}. Embeddings are stripped.
    """
    logger.info("get_details: ids=%s", memory_ids)
    try:
        result = storage.get_details(memory_ids or [])
        result["found"] = [_strip_internal_fields(m) for m in result["found"]]
        logger.info("get_details: found=%d missing=%d", len(result["found"]), len(result["missing"]))
        return result
    except Exception as e:
        logger.error("get_details failed: %s", e, exc_info=True)
        raise


def memory_summarize(namespace: str | None = None, mode: str = "missing",
                     memory_id: str | None = None) -> dict:
    """Generate summaries for existing memories (migration entry point).

    mode="missing" (default): only records with an empty summary.
    mode="all": regenerate every summary (overwrite).

    When ``memory_id`` is given, only that memory is processed (mode still applies:
    "missing" skips records that already have a summary).

    Summary-only updates never change updated_at (via storage.update_summary).
    """
    mode = mode if mode in ("missing", "all") else "missing"
    logger.info("summarize migration: namespace=%s mode=%s memory_id=%s", namespace, mode, memory_id)
    try:
        if memory_id:
            m = storage.get(memory_id)
            if m is None:
                result = {"processed": 0, "failed": 0, "mode": mode, "total": 0,
                          "error": f"memory not found: {memory_id}", "memory_id": memory_id}
                logger.info("summarize migration done: %s", result)
                return result
            memories = [m]
        else:
            memories = memory_list(namespace=namespace, limit=10 ** 9)
        processed = 0
        failed = 0
        for m in memories:
            if mode == "missing" and m.get("summary"):
                continue
            try:
                summary = summarize(m["text"])
            except Exception:
                # keep summary empty so the record remains eligible for retry
                failed += 1
                continue
            storage.update_summary(m["id"], summary)
            processed += 1
        result = {"processed": processed, "failed": failed, "mode": mode, "total": len(memories)}
        if memory_id:
            result["memory_id"] = memory_id
        logger.info("summarize migration done: %s", result)
        return result
    except Exception as e:
        logger.error("summarize migration failed: %s", e, exc_info=True)
        raise


def memory_extract_keywords(namespace: str | None = None, mode: str = "missing",
                            memory_id: str | None = None) -> dict:
    """Backfill metadata.keywords for existing memories using the local LLM.

    mode="missing" (default): only records without metadata.keywords.
    mode="all": re-extract keywords for every record (overwrite).

    When ``memory_id`` is given, only that memory is processed.

    Keywords are written via storage.update_metadata() so updated_at is left
    unchanged (migration must not perturb time-based list ordering). The
    SQLite keyword index is rebuilt per record as part of the write.
    """
    from .keyword_extract import extract_keywords as _extract
    mode = mode if mode in ("missing", "all") else "missing"
    logger.info("keyword extraction migration: namespace=%s mode=%s memory_id=%s", namespace, mode, memory_id)
    try:
        if memory_id:
            m = storage.get(memory_id)
            if m is None:
                result = {"processed": 0, "failed": 0, "mode": mode, "total": 0,
                          "error": f"memory not found: {memory_id}", "memory_id": memory_id}
                logger.info("keyword extraction migration done: %s", result)
                return result
            memories = [m]
        else:
            memories = memory_list(namespace=namespace, limit=10 ** 9)
        processed = 0
        failed = 0
        for m in memories:
            if mode == "missing" and m.get("metadata", {}).get("keywords"):
                continue
            try:
                keywords = _extract(m["text"])
            except Exception:
                # keep keywords empty so the record remains eligible for retry
                failed += 1
                continue
            if not keywords:
                failed += 1
                continue
            storage.update_metadata(m["id"], {"keywords": keywords})
            processed += 1
        result = {"processed": processed, "failed": failed, "mode": mode, "total": len(memories)}
        if memory_id:
            result["memory_id"] = memory_id
        logger.info("keyword extraction migration done: %s", result)
        return result
    except Exception as e:
        logger.error("keyword extraction migration failed: %s", e, exc_info=True)
        raise


def memory_sync() -> dict:
    """Manually trigger full sync of secondary from primary (dual mode only)."""
    logger.info("sync")
    try:
        if hasattr(storage, "force_sync"):
            return storage.force_sync()
        return {"error": "storage backend does not support sync"}
    except Exception as e:
        logger.error("sync failed: %s", e, exc_info=True)
        raise


def memory_namespaces() -> dict:
    """Return all namespace names and their record counts."""
    stats = storage.get_stats()
    return {
        "namespaces": stats.get("namespaces", []),
        "namespace_counts": stats.get("namespace_counts", {}),
    }


def memory_storage_paths() -> dict:
    """Expose the configured storage paths for the UI.

    In dual mode: primary_path is the SQLite primary, secondary_path is the
    NDJSON disaster-recovery replica. In single-backend mode only the
    backend type is reported (no replica paths).
    """
    stats = storage.get_stats()
    result = {"backend": stats.get("backend", "?")}
    if hasattr(storage, "primary"):
        result["primary_path"] = getattr(storage.primary, "path", "?")
        result["secondary_path"] = getattr(storage.secondary, "path", "?")
    return result


def _detect_storage_backend(path: str) -> str:
    """Map a storage file path to its backend name."""
    if path.endswith(".jsonl") or path.endswith(".ndjson"):
        return "ndjson"
    if path.endswith(".json"):
        return "json"
    return "sqlite"


def memory_migrate_namespace(source: str, target: str) -> dict:
    """Move all memories from source namespace to target namespace."""
    if source == target:
        return {"error": "source and target are the same", "migrated": 0}
    memories = memory_list(namespace=source, limit=10 ** 9)
    if not memories:
        return {"error": f"no memories found in namespace '{source}'", "migrated": 0}
    migrated = 0
    for m in memories:
        memory_update(m["id"], namespace=target)
        migrated += 1
    return {"migrated": migrated, "source": source, "target": target}


def memory_recover(source_path: str, target_path: str) -> dict:
    """Recover/rebuild target storage file from source storage file.

    Usage example (CLI):
        python -m app.recover_cli data/memories.json data/memories.db
    """
    logger.info("recover: source=%s target=%s", source_path, target_path)
    from .recovery import recover as _recover
    try:
        src_backend = _detect_storage_backend(source_path)
        tgt_backend = _detect_storage_backend(target_path)
        src = create_storage(src_backend, path=source_path)
        tgt = create_storage(tgt_backend, path=target_path)
        result = _recover(tgt, src)
        # Recovered records carry metadata.keywords in the target DB;
        # rebuild the SQLite keyword index so list(keywords=...) works.
        if hasattr(tgt, "rebuild_keyword_index"):
            tgt.rebuild_keyword_index()
        logger.info("recover: %s", result)
        return result
    except Exception as e:
        logger.error("recover failed: %s", e, exc_info=True)
        raise
