import os
import logging
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone

from .embed import create_embedder
from .index_value_resolver import IndexValueResolver, MAX_FILTER_VALUES
from .storage import create_storage, resolve_replica_path
from . import audit
from .summary_policy import derive_summary, normalize_summary
from .jsonl_transfer import load_jsonl, write_clean_jsonl, sha256_file, unlink_quietly
from .categories import (
    normalize_categories,
    normalize_categories_filter,
    categories_dropped,
    category_counts_with_seeds,
)
from .namespace_policy import (
    NamespaceNotFound,
    NAMESPACE_NOT_FOUND,
    normalize_namespace,
    validate_namespace_name,
    is_confirmed,
)

logger = logging.getLogger("service")


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

STORAGE_BACKEND = os.environ.get("MEMORY_STORAGE", "sqlite")
STORAGE_PATH = os.environ.get("MEMORY_STORAGE_PATH")
REPLICA_PATH = os.environ.get("MEMORY_REPLICA_PATH")
LEGACY_REPLICA_PATH = os.environ.get("MEMORY_STORAGE_PATH_DUAL")
SYNC_INTERVAL = int(os.environ.get("MEMORY_SYNC_INTERVAL", "300"))
COMPACT_INTERVAL = int(os.environ.get("MEMORY_COMPACT_INTERVAL", str(24 * 60 * 60)))
COMPACT_MIN_BYTES = int(os.environ.get("MEMORY_COMPACT_MIN_BYTES", str(1024 * 1024)))
COMPACT_MIN_RATIO = float(os.environ.get("MEMORY_COMPACT_MIN_RATIO", "0.25"))
HEALTH_CHECK_INTERVAL = int(os.environ.get(
    "MEMORY_HEALTH_CHECK_INTERVAL", str(24 * 60 * 60)
))

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_DTYPE = os.environ.get("EMBED_DTYPE", "float16")
EMBED_PRELOAD = os.environ.get("EMBED_PRELOAD", "true").lower() in ("1", "true", "yes")
DEFAULT_NAMESPACE = os.environ.get("MEMORY_DEFAULT_NAMESPACE", "default")
INDEX_SEMANTIC_TOP_K = int(os.environ.get("MEMORY_INDEX_SEMANTIC_TOP_K", "3"))
INDEX_SEMANTIC_MIN_SCORE = float(os.environ.get("MEMORY_INDEX_SEMANTIC_MIN_SCORE", "0.65"))
INDEX_SEMANTIC_MAX_GAP = float(os.environ.get("MEMORY_INDEX_SEMANTIC_MAX_GAP", "0.05"))
IMPORT_MAX_BYTES = int(os.environ.get("MEMORY_IMPORT_MAX_BYTES", str(64 * 1024 * 1024)))
IMPORT_MAX_RECORDS = int(os.environ.get("MEMORY_IMPORT_MAX_RECORDS", "10000"))

embedding_provider = create_embedder()
EMBED_PROVIDER = embedding_provider.spec.provider
EMBED_MODEL = embedding_provider.spec.model


if STORAGE_BACKEND == "dual":
    primary_path = STORAGE_PATH or "data/memories.db"
    secondary_path = resolve_replica_path(
        primary_path, REPLICA_PATH, LEGACY_REPLICA_PATH
    )
    logger.info("Dual storage: primary=%s secondary=%s sync_interval=%ds compact_interval=%ds",
                primary_path, secondary_path, SYNC_INTERVAL, COMPACT_INTERVAL)
    storage = create_storage("dual", primary_path=primary_path,
                             secondary_path=secondary_path,
                             embedding_dtype=EMBED_DTYPE,
                             embedding_spec=embedding_provider.spec.as_dict(),
                             sync_interval=SYNC_INTERVAL,
                             compact_interval=COMPACT_INTERVAL,
                             compact_min_bytes=COMPACT_MIN_BYTES,
                             compact_min_ratio=COMPACT_MIN_RATIO,
                             health_check_interval=HEALTH_CHECK_INTERVAL)
elif STORAGE_BACKEND in ("json", "ndjson", "jsonl"):
    raise RuntimeError(
        f"MEMORY_STORAGE={STORAGE_BACKEND} is no longer allowed as a standalone backend. "
        "JSON storage was removed; NDJSON serves only as the disaster-recovery replica. "
        "Use 'sqlite' (single) or 'dual' (sqlite primary + ndjson replica)."
    )
elif STORAGE_BACKEND == "sqlite":
    path = STORAGE_PATH or "data/memories.db"
    logger.info("SQLite storage: %s", path)
    storage = create_storage("sqlite", path=path, embedding_dtype=EMBED_DTYPE,
                             embedding_spec=embedding_provider.spec.as_dict())
else:
    raise RuntimeError(
        f"unknown MEMORY_STORAGE={STORAGE_BACKEND}; use 'sqlite' or 'dual'"
    )

if hasattr(storage, "index_embedding_completeness"):
    _index_completeness = storage.index_embedding_completeness()
    if not _index_completeness["complete"]:
        raise RuntimeError(
            "active embedding profile is missing keyword/category vectors: "
            f"expected={_index_completeness['expected']}, "
            f"stored={_index_completeness['stored']}. Stop the service and run "
            "'python -m app.embedding_cli reindex-index-values data/memories.db'."
        )

def embed(text: str) -> list[float]:
    try:
        return embedding_provider.embed_document(text)
    except Exception as e:
        logger.error("embed failed: %s", e, exc_info=True)
        raise


def embed_query(text: str) -> list[float]:
    try:
        # Ollama's current bge-m3 path has identical query/document handling.
        # Calling the public wrapper preserves compatibility with tests and
        # deployments that patch ``service.embed``. Prefix-aware providers use
        # their dedicated query method.
        if EMBED_PROVIDER == "ollama":
            return embed(text)
        return embedding_provider.embed_query(text)
    except Exception as e:
        logger.error("query embed failed: %s", e, exc_info=True)
        raise


def embed_documents(texts) -> list[list[float]]:
    # Preserve the patchable legacy embed() seam used by Ollama deployments
    # and tests; native batch providers use their efficient batch path.
    if EMBED_PROVIDER == "ollama":
        return [embed(text) for text in texts]
    return embedding_provider.embed_documents(texts)


def _index_resolver() -> IndexValueResolver:
    return IndexValueResolver(
        storage, embed_query, embed_documents,
        top_k=INDEX_SEMANTIC_TOP_K,
        min_score=INDEX_SEMANTIC_MIN_SCORE,
        max_gap=INDEX_SEMANTIC_MAX_GAP,
    )


def warmup_embedding_provider() -> None:
    if not EMBED_PRELOAD:
        return
    logger.info("warming embedding provider=%s model=%s", EMBED_PROVIDER, EMBED_MODEL)
    embedding_provider.warmup()
    logger.info("embedding provider ready dimension=%d profile=%s",
                embedding_provider.spec.dimension, embedding_provider.spec.profile_id)


def _strip_internal_fields(d: dict) -> dict:
    """Remove storage-internal fields from a record before exposing it.

    ``embedding`` is a large vector with no LLM value; ``deleted``/``synced``/``version``
    are sync-protocol bookkeeping fields. All are dropped from every
    LLM-facing API (search/list/get/get_details).
    """
    d.pop("embedding", None)
    d.pop("deleted", None)
    d.pop("synced", None)
    d.pop("version", None)
    return d


def _prepare_for_llm(results: list[dict]) -> list[dict]:
    """Shape search/list results for LLM consumption.

    - Internal fields (embedding/deleted/synced) are dropped by the callers.
    - Prefer ``summary``; derive a deterministic lead when summary is empty.
    - Never expose the full ``text`` when a summary exists.
    """
    for r in results:
        if not r.get("summary"):
            r["summary"] = derive_summary(r.get("text", ""))
        r.pop("text", None)
    return results


def _normalize_summary(summary) -> str | None:
    """Return a caller summary stripped, or None when it is unusable."""
    return normalize_summary(summary)


def known_namespaces() -> list[str]:
    """Namespaces currently in use, plus the configured default namespace."""
    known = list(storage.list_namespaces())
    if DEFAULT_NAMESPACE not in known:
        known.append(DEFAULT_NAMESPACE)
    return sorted(known)


def check_namespace(namespace, confirm_codes=None) -> str:
    """Normalize/validate a namespace and enforce that it exists.

    Returns the trimmed namespace. ``None``/blank resolves to the default
    namespace. Raises ``NamespaceNotFound`` for an unknown namespace unless
    the caller explicitly confirms the matching validation rule; name format
    (length/reserved characters) is never bypassable by confirmation codes.
    """
    if namespace is not None and not isinstance(namespace, str):
        raise ValueError("namespace must be a string")
    ns = normalize_namespace(namespace) or DEFAULT_NAMESPACE
    validate_namespace_name(ns)
    if ns == DEFAULT_NAMESPACE:
        return ns
    if ns in storage.list_namespaces():
        return ns
    if is_confirmed(confirm_codes, NAMESPACE_NOT_FOUND):
        return ns
    raise NamespaceNotFound(ns, known_namespaces())


def memory_add(text: str, metadata: dict | None = None,
               namespace: str | None = None, summary: str | None = None,
               confirm_codes: list | None = None) -> dict:
    ns = check_namespace(namespace, confirm_codes)
    summary = _normalize_summary(summary)
    logger.info("add: namespace=%s text_len=%d has_summary=%s",
                ns, len(text), summary is not None)
    try:
        embedding = embed(text)
        dropped = categories_dropped(metadata)
        metadata = _apply_keywords_to_metadata(metadata)
        metadata = _apply_categories_to_metadata(metadata)
        index_embeddings = _index_resolver().prepare_embeddings(metadata)
        write_kwargs = {"index_embeddings": index_embeddings} if index_embeddings else {}
        result = storage.add(text, embedding, metadata=metadata, namespace=ns,
                             summary=summary, **write_kwargs)
        eid = result["id"]
        if result.get("created"):
            logger.info("add: ok id=%s (summary %s)", eid,
                        "supplied" if summary is not None else "text extract")
        else:
            # Duplicate (namespace + text): existing record returned untouched.
            logger.info("add: duplicate skipped id=%s", eid)
        out = {"id": eid, "created": result.get("created", True)}
        if result.get("created") and dropped:
            # Surface the cap so the MCP hint can steer the LLM for next time.
            out["categories_dropped"] = dropped
            out["categories"] = normalize_categories((metadata or {}).get("categories"))
        return out
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


def _apply_categories_to_metadata(metadata: dict | None) -> dict | None:
    """Normalize metadata.categories in place (trim/lower/dedupe, max 3).

    Mirrors ``_apply_keywords_to_metadata``: when the normalized result is
    empty, categories is set to [] so storage clears previous associations.
    """
    if metadata is None or "categories" not in metadata:
        return metadata
    metadata["categories"] = normalize_categories(metadata.get("categories"))
    return metadata


def memory_search(query: str, k: int = 5, threshold: float = 0.0,
                  namespace: str | None = None,
                  categories: list | None = None,
                  keywords: list | None = None,
                  return_resolution: bool = False):
    k = _sanitize_k(k)
    # Reject an unknown namespace before embedding: an empty result must not
    # be confused with "namespace does not exist".
    if namespace is not None and not isinstance(namespace, str):
        raise ValueError("namespace must be a string")
    namespace = normalize_namespace(namespace)
    if namespace is not None:
        namespace = check_namespace(namespace)
    kw = _normalize_keywords(keywords)
    cats = normalize_categories_filter(categories)
    for dimension, values in (("keywords", kw), ("categories", cats)):
        if values and len(values) > MAX_FILTER_VALUES:
            raise ValueError(
                f"{dimension} accepts at most {MAX_FILTER_VALUES} filter values"
            )
    logger.info("search: query_len=%d k=%d threshold=%.3f namespace=%s keywords=%s categories=%s",
                len(query), k, threshold, namespace, kw, cats)
    try:
        filters = {"keywords": kw, "categories": cats}
        supplied_filters = any(filters.values())
        resolved, resolution = _index_resolver().resolve(filters, namespace)
        if supplied_filters and any(
                filters[dimension] and not resolved.get(dimension)
                for dimension in filters):
            results = []
        else:
            query_emb = embed_query(query)
            results = storage.search(
                query_emb, k=k, threshold=threshold, namespace=namespace,
                keywords=resolved.get("keywords"), categories=resolved.get("categories"),
            )
        logger.info("search: %d results", len(results))
        items = [_strip_internal_fields(r) for r in results]
        if return_resolution:
            return {"items": items,
                    "filter_resolution": resolution if supplied_filters else None}
        return items
    except Exception as e:
        logger.error("search failed: %s", e, exc_info=True)
        raise


def memory_list(namespace: str | None = None, limit: int = 100,
                offset: int = 0, sort_by: str = "created_at",
                updated_after: str | None = None,
                updated_before: str | None = None,
                keywords: list | None = None,
                categories: list | None = None) -> list[dict]:
    if sort_by not in ("created_at", "updated_at"):
        sort_by = "created_at"
    if namespace is not None and not isinstance(namespace, str):
        raise ValueError("namespace must be a string")
    namespace = normalize_namespace(namespace)
    if namespace is not None:
        namespace = check_namespace(namespace)
    kw = _normalize_keywords(keywords)
    cats = normalize_categories_filter(categories)
    logger.info("list: namespace=%s limit=%d offset=%d sort_by=%s updated_after=%s updated_before=%s keywords=%s categories=%s",
                namespace, limit, offset, sort_by, updated_after, updated_before, kw, cats)
    try:
        results = storage.list_memories(namespace=namespace, limit=limit, offset=offset,
                                        sort_by=sort_by, updated_after=updated_after,
                                        updated_before=updated_before, keywords=kw,
                                        categories=cats)
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
                  namespace: str | None = None,
                  summary: str | None = None,
                  confirm_codes: list | None = None) -> dict:
    # Blank/omitted namespace means "leave unchanged"; an explicit namespace
    # must be an existing one unless the caller confirms its creation.
    if namespace is not None and not isinstance(namespace, str):
        raise ValueError("namespace must be a string")
    target_ns = normalize_namespace(namespace)
    if target_ns is not None:
        target_ns = check_namespace(target_ns, confirm_codes)
    summary = _normalize_summary(summary)
    logger.info("update: id=%s has_text=%s has_metadata=%s has_ns=%s has_summary=%s",
                memory_id, text is not None, metadata is not None,
                target_ns is not None, summary is not None)
    try:
        if text is not None:
            embedding = embed(text)
        else:
            embedding = None
        dropped = categories_dropped(metadata)
        metadata = _apply_keywords_to_metadata(metadata)
        metadata = _apply_categories_to_metadata(metadata)
        index_embeddings = _index_resolver().prepare_embeddings(metadata)
        write_kwargs = {"index_embeddings": index_embeddings} if index_embeddings else {}
        ok = storage.update(memory_id, text=text, embedding=embedding,
                            metadata=metadata, namespace=target_ns,
                            summary=summary,
                            **write_kwargs)
        logger.info("update: %s", "ok" if ok else "not found")
        out = {"success": ok}
        if ok and dropped:
            out["categories_dropped"] = dropped
            out["categories"] = normalize_categories((metadata or {}).get("categories"))
        return out
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
        # Seeds always present (count 0 when unused) so the LLM can reuse them.
        stats["category_counts"] = category_counts_with_seeds(
            stats.get("category_counts") or {})
        stats["embed_model"] = EMBED_MODEL
        stats["embed_provider"] = EMBED_PROVIDER
        stats["embed_dimension"] = embedding_provider.spec.dimension
        stats["embed_profile"] = embedding_provider.spec.profile_id
        stats["embed_dtype"] = EMBED_DTYPE
        stats["ollama_base_url"] = OLLAMA_BASE_URL
        stats["server_time"] = _now_utc_iso()
        return stats
    except Exception as e:
        logger.error("stats failed: %s", e, exc_info=True)
        raise


def _primary_storage():
    # Avoid duck-typing here: mocks and proxy objects commonly synthesize a
    # ``primary`` attribute, which would route imports to the wrong object.
    from .storage_dual import DualStorage
    return storage.primary if isinstance(storage, DualStorage) else storage


def memory_export_full(destination: str) -> dict:
    from .storage_dual import DualStorage
    if not isinstance(storage, DualStorage):
        raise RuntimeError("Full Export requires MEMORY_STORAGE=dual")
    return storage.export_full_snapshot(destination)


def memory_export_namespace(namespace: str, destination: str) -> dict:
    namespace = (namespace or "").strip()
    if not namespace:
        raise ValueError("namespace is required")
    records = _primary_storage().export_records(namespace)
    if not records:
        raise LookupError(f"namespace not found: {namespace}")
    write_clean_jsonl(records, destination)
    return {"records": len(records), "bytes": os.path.getsize(destination),
            "sha256": sha256_file(destination), "path": destination,
            "namespace": namespace}


_import_jobs_lock = threading.Lock()
_import_jobs = OrderedDict()
_active_import_id = None


def _set_import_job(import_id, **changes):
    with _import_jobs_lock:
        _import_jobs[import_id].update(changes)


def memory_import_status(import_id: str) -> dict | None:
    with _import_jobs_lock:
        job = _import_jobs.get(import_id)
        return dict(job) if job else None


def _deduplicate_source(records):
    chosen = {}
    for position, record in enumerate(records):
        key = (record["namespace"], record["text"])
        marker = (datetime.fromisoformat(record["updated_at"].replace("Z", "+00:00")), position)
        if key not in chosen or marker >= chosen[key][0]:
            chosen[key] = (marker, record)
    return [item[1] for item in chosen.values()]


def _run_import_job(import_id, file_path, original_name, namespace_prefix):
    global _active_import_id
    started = time.perf_counter()
    audit_args = {"import_id": import_id, "filename": original_name,
                  "namespace_prefix": namespace_prefix}
    timings = {}
    phase_started = time.perf_counter()
    try:
        audit_args.update({"bytes": os.path.getsize(file_path),
                           "sha256": sha256_file(file_path)})
        _set_import_job(import_id, status="validating", phase="validating")
        loaded = load_jsonl(file_path, max_records=IMPORT_MAX_RECORDS)
        source_total = len(loaded["records"])
        records = []
        for item in loaded["records"]:
            copied = dict(item)
            copied["namespace"] = namespace_prefix + copied["namespace"]
            records.append(copied)
        records = _deduplicate_source(records)
        effective_total = len(records)
        primary = _primary_storage()
        target_pairs = {(item["namespace"], item["text"])
                        for item in primary.export_records()}
        pre_skipped = source_total - effective_total
        planned = []
        for item in records:
            if (item["namespace"], item["text"]) in target_pairs:
                pre_skipped += 1
            else:
                planned.append(item)
        timings["validating"] = round((time.perf_counter() - phase_started) * 1000, 3)
        profile = primary.get_stats().get("embedding_profile", {}).get("active")
        if not profile:
            raise RuntimeError("target database has no active embedding profile")

        _set_import_job(import_id, status="embedding_memories", phase="embedding_memories",
                        source_total=source_total, effective_total=effective_total,
                        progress={"completed": 0, "total": len(planned)},
                        target_profile=profile, timings_ms=dict(timings))
        phase_started = time.perf_counter()
        vectors = embed_documents([item["text"] for item in planned]) if planned else []
        if len(vectors) != len(planned):
            raise RuntimeError("embedding provider returned the wrong number of memory vectors")
        memory_vectors = {item["id"]: vector for item, vector in zip(planned, vectors)}
        timings["embedding_memories"] = round(
            (time.perf_counter() - phase_started) * 1000, 3
        )
        _set_import_job(import_id, progress={"completed": len(planned), "total": len(planned)},
                        status="embedding_index_values", phase="embedding_index_values",
                        timings_ms=dict(timings))

        phase_started = time.perf_counter()
        missing = set()
        for item in planned:
            missing.update(primary.missing_index_value_embeddings(item.get("metadata") or {}))
        missing = sorted(missing)
        index_vectors_list = (
            embed_documents([value for _dimension, value in missing]) if missing else []
        )
        if len(index_vectors_list) != len(missing):
            raise RuntimeError("embedding provider returned the wrong number of index vectors")
        index_vectors = dict(zip(missing, index_vectors_list))
        timings["embedding_index_values"] = round(
            (time.perf_counter() - phase_started) * 1000, 3
        )

        _set_import_job(import_id, status="committing", phase="committing",
                        timings_ms=dict(timings))
        phase_started = time.perf_counter()
        result = storage.import_records(planned, memory_vectors, index_vectors, profile)
        timings["committing"] = round((time.perf_counter() - phase_started) * 1000, 3)
        result.update({"source_total": source_total, "effective_total": effective_total,
                       "skipped": result["skipped"] + pre_skipped,
                       "target_profile": profile, "timings_ms": timings})
        completed_at = _now_utc_iso()
        _set_import_job(import_id, status="completed", phase="completed", result=result,
                        completed_at=completed_at)
        audit.record("memory_import", audit_args, result,
                     (time.perf_counter() - started) * 1000)
    except Exception as exc:
        logger.error("JSONL import failed id=%s", import_id, exc_info=True)
        error = {"type": type(exc).__name__, "message": str(exc)}
        _set_import_job(import_id, status="failed", phase="failed", error=error,
                        timings_ms=timings,
                        completed_at=_now_utc_iso())
        audit.record("memory_import", audit_args, None,
                     (time.perf_counter() - started) * 1000, error=error)
    finally:
        unlink_quietly(file_path)
        with _import_jobs_lock:
            if _active_import_id == import_id:
                _active_import_id = None


def memory_import_start(file_path: str, original_name: str,
                        namespace_prefix: str = "") -> dict:
    global _active_import_id
    if not original_name.lower().endswith((".jsonl", ".ndjson")):
        raise ValueError("import file must end with .jsonl or .ndjson")
    file_bytes = os.path.getsize(file_path)
    if file_bytes > IMPORT_MAX_BYTES:
        raise ValueError(f"import file exceeds {IMPORT_MAX_BYTES} bytes")
    file_sha256 = sha256_file(file_path)
    import_id = "im_" + os.urandom(8).hex()
    with _import_jobs_lock:
        if _active_import_id is not None:
            raise RuntimeError("another import is already running")
        _active_import_id = import_id
        _import_jobs[import_id] = {
            "import_id": import_id, "status": "queued", "phase": "queued",
            "filename": os.path.basename(original_name), "bytes": file_bytes,
            "sha256": file_sha256, "namespace_prefix": namespace_prefix,
            "created_at": _now_utc_iso(), "progress": {"completed": 0, "total": 0},
            "result": None, "error": None,
        }
        while len(_import_jobs) > 20:
            _import_jobs.popitem(last=False)
    worker = threading.Thread(
        target=_run_import_job,
        args=(import_id, file_path, os.path.basename(original_name), namespace_prefix),
        daemon=True, name=f"memory-import-{import_id}",
    )
    try:
        worker.start()
    except Exception:
        with _import_jobs_lock:
            _import_jobs.pop(import_id, None)
            if _active_import_id == import_id:
                _active_import_id = None
        raise
    return {"import_id": import_id, "status": "queued"}


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


def memory_migrate_namespace(source: str, target: str) -> dict:
    """Move all memories from source namespace to target namespace.

    This is an explicit admin operation, so the confirmation needed to create
    a brand-new target namespace is supplied internally and the operator sees
    no behavior change. An unknown *source* is reported as "no memories"
    rather than as a namespace error.
    """
    source_ns = normalize_namespace(source)
    target_ns = normalize_namespace(target)
    if source_ns is None or target_ns is None:
        return {"error": "source and target namespaces are required", "migrated": 0}
    validate_namespace_name(target_ns)
    if source_ns == target_ns:
        return {"error": "source and target are the same", "migrated": 0}
    try:
        memories = memory_list(namespace=source_ns, limit=10 ** 9)
    except NamespaceNotFound:
        return {"error": f"no memories found in namespace '{source_ns}'", "migrated": 0}
    if not memories:
        return {"error": f"no memories found in namespace '{source_ns}'", "migrated": 0}
    migrated = 0
    for m in memories:
        memory_update(m["id"], namespace=target_ns,
                      confirm_codes=[NAMESPACE_NOT_FOUND])
        migrated += 1
    return {"migrated": migrated, "source": source_ns, "target": target_ns}


def _normalize_rating(rating) -> int | None:
    """Coerce a rating to an int in [-10, 10].

    - Non-numeric values (str/None/bool/list/…) → None (skip this item).
    - Floats are rounded to the nearest int, then clamped to [-10, 10].
    """
    if isinstance(rating, bool) or not isinstance(rating, (int, float)):
        return None
    if not isinstance(rating, int):
        rating = round(rating)
    return max(-10, min(10, rating))


def memory_report(search_id, ratings) -> dict:
    """Record LLM feedback on search result helpfulness.

    Pure record-keeping: no storage writes here — the audit journal records
    the event at the MCP tool boundary (handle_call_tool → audit.record).
    Ratings are normalized leniently: out-of-range values are clamped to
    [-10, 10], floats are rounded, and unparseable items are skipped so a
    single bad entry never fails the whole call.

    ``search_id`` is required (correlates with the originating search).
    """
    if not isinstance(search_id, str) or not search_id:
        raise ValueError("search_id is required and must be a non-empty string")
    if ratings is None:
        ratings = []
    if not isinstance(ratings, list):
        raise ValueError("ratings must be a list of {m_id, rating} objects")

    normalized = []
    for item in ratings:
        if not isinstance(item, dict):
            continue
        m_id = item.get("m_id")
        if not isinstance(m_id, str) or not m_id:
            continue
        rating = _normalize_rating(item.get("rating"))
        if rating is None:
            continue
        normalized.append({"m_id": m_id, "rating": rating})

    return {"recorded": True, "count": len(normalized)}
