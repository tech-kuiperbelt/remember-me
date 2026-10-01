import os
import logging
import time
from pathlib import Path
from datetime import datetime, timezone

from app.embedding.index_value_resolver import IndexValueResolver, MAX_FILTER_VALUES
from app.operations.import_coordinator import ImportCoordinator
from app.embedding.provider_adapter import wrap_provider
from app.storage import StorageCapabilityError
from app.domain.lexical import LexicalQueryError
from app.domain.anchors import normalize_anchors, InvalidAnchors
from app import audit
from app.domain.summary_policy import derive_summary, normalize_summary
from app.storage.jsonl_transfer import load_jsonl, write_clean_jsonl, sha256_file
from app.domain.categories import (
    normalize_categories,
    normalize_categories_filter,
    categories_dropped,
    category_counts_with_seeds,
)
from app.domain.namespace_policy import (
    NamespaceNotFound,
    InvalidNamespace,
    NAMESPACE_NOT_FOUND,
    normalize_namespace,
    validate_namespace_name,
    is_confirmed,
)

logger = logging.getLogger("service")


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _apply_anchors_to_metadata(metadata: dict | None) -> dict | None:
    """Strictly validate and normalize metadata.anchors in place.

    Caller input is strict (design §7): any invalid item rejects the whole write
    rather than being silently dropped or coerced to []. An explicit ``[]`` is
    valid and means "no anchors" (on update: clear). The server never generates
    anchors. Raises :class:`InvalidAnchors` before embedding or storage work.
    """
    if metadata is None or "anchors" not in metadata:
        return metadata
    metadata["anchors"] = normalize_anchors(metadata.get("anchors"))
    return metadata


class InvalidSearchFilter(ValueError):
    """Caller supplied more filter values than the supported search limit."""


def _deduplicate_source(records):
    chosen = {}
    for position, record in enumerate(records):
        key = (record["namespace"], record["text"])
        marker = (datetime.fromisoformat(record["updated_at"].replace("Z", "+00:00")), position)
        if key not in chosen or marker >= chosen[key][0]:
            chosen[key] = (marker, record)
    return [item[1] for item in chosen.values()]


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


class MemoryService:
    """Business operations over explicitly supplied resources; owns an import coordinator."""

    def __init__(self, settings, storage, embedding_provider, audit_journal=None):
        self.settings = settings
        self.storage = storage
        # One shared provider adapter: serializes calls and translates errors,
        # no matter whether the runtime or a test constructed the service.
        self.embedding_provider = wrap_provider(embedding_provider)
        self.audit = audit_journal or audit
        # Resolve the job method at call time so tests can patch the seam.
        self._imports = ImportCoordinator(lambda *args: self._run_import_job(*args))

    def close(self):
        """Reject new imports and drain every worker before the runtime closes storage."""
        self._imports.close()

    def memory_import_start(self, file_path, original_name, namespace_prefix=""):
        return self._start_import(file_path, original_name, namespace_prefix)

    def embed(self, text: str) -> list[float]:
        return self.embedding_provider.embed_document(text)

    def embed_query(self, text: str) -> list[float]:
        return self.embedding_provider.embed_query(text)

    def embed_documents(self, texts) -> list[list[float]]:
        return self.embedding_provider.embed_documents(texts)

    def _index_resolver(self) -> IndexValueResolver:
        return IndexValueResolver(
            self.storage, self.embed_query, self.embed_documents,
            top_k=self.settings.INDEX_SEMANTIC_TOP_K,
            min_score=self.settings.INDEX_SEMANTIC_MIN_SCORE,
            max_gap=self.settings.INDEX_SEMANTIC_MAX_GAP,
        )

    def warmup_embedding_provider(self) -> None:
        if not self.settings.EMBED_PRELOAD:
            return
        logger.info("warming embedding provider=%s model=%s", self.embedding_provider.spec.provider, self.embedding_provider.spec.model)
        self.embedding_provider.warmup()
        logger.info("embedding provider ready dimension=%d profile=%s",
                    self.embedding_provider.spec.dimension, self.embedding_provider.spec.profile_id)

    def known_namespaces(self) -> list[str]:
        """Namespaces currently in use, plus the configured default namespace."""
        known = list(self.storage.list_namespaces())
        if self.settings.DEFAULT_NAMESPACE not in known:
            known.append(self.settings.DEFAULT_NAMESPACE)
        return sorted(known)

    def check_namespace(self, namespace, confirm_codes=None) -> str:
        """Normalize/validate a namespace and enforce that it exists.

        Returns the trimmed namespace. ``None``/blank resolves to the default
        namespace. Raises ``NamespaceNotFound`` for an unknown namespace unless
        the caller explicitly confirms the matching validation rule; name format
        (length/reserved characters) is never bypassable by confirmation codes.
        """
        if namespace is not None and not isinstance(namespace, str):
            raise InvalidNamespace("namespace must be a string")
        ns = normalize_namespace(namespace) or self.settings.DEFAULT_NAMESPACE
        validate_namespace_name(ns)
        if ns == self.settings.DEFAULT_NAMESPACE:
            return ns
        if ns in self.storage.list_namespaces():
            return ns
        if is_confirmed(confirm_codes, NAMESPACE_NOT_FOUND):
            return ns
        raise NamespaceNotFound(ns, self.known_namespaces())

    def memory_add(self, text: str, metadata: dict | None = None,
                   namespace: str | None = None, summary: str | None = None,
                   confirm_codes: list | None = None) -> dict:
        ns = self.check_namespace(namespace, confirm_codes)
        summary = _normalize_summary(summary)
        # Validate caller anchors before embedding or any storage work.
        metadata = _apply_anchors_to_metadata(metadata)
        logger.info("add: namespace=%s text_len=%d has_summary=%s",
                    ns, len(text), summary is not None)
        try:
            embedding = self.embed(text)
            dropped = categories_dropped(metadata)
            metadata = _apply_keywords_to_metadata(metadata)
            metadata = _apply_categories_to_metadata(metadata)
            index_embeddings = self._index_resolver().prepare_embeddings(metadata)
            write_kwargs = {"index_embeddings": index_embeddings} if index_embeddings else {}
            result = self.storage.add(text, embedding, metadata=metadata, namespace=ns,
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

    def memory_search(self, query: str, k: int = 5, threshold: float = 0.0,
                      namespace: str | None = None,
                      categories: list | None = None,
                      keywords: list | None = None,
                      return_resolution: bool = False):
        k = _sanitize_k(k)
        # Reject an unknown namespace before embedding: an empty result must not
        # be confused with "namespace does not exist".
        if namespace is not None and not isinstance(namespace, str):
            raise InvalidNamespace("namespace must be a string")
        namespace = normalize_namespace(namespace)
        if namespace is not None:
            namespace = self.check_namespace(namespace)
        kw = _normalize_keywords(keywords)
        cats = normalize_categories_filter(categories)
        for dimension, values in (("keywords", kw), ("categories", cats)):
            if values and len(values) > MAX_FILTER_VALUES:
                raise InvalidSearchFilter(
                    f"{dimension} accepts at most {MAX_FILTER_VALUES} filter values"
                )
        logger.info("search: query_len=%d k=%d threshold=%.3f namespace=%s keywords=%s categories=%s",
                    len(query), k, threshold, namespace, kw, cats)
        try:
            filters = {"keywords": kw, "categories": cats}
            supplied_filters = any(filters.values())
            resolved, resolution = self._index_resolver().resolve(filters, namespace)
            if supplied_filters and any(
                    filters[dimension] and not resolved.get(dimension)
                    for dimension in filters):
                results = []
            else:
                query_emb = self.embed_query(query)
                results = self.storage.search(
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

    def memory_search_lexical(self, query: str, k: int = 10,
                              namespace: str | None = None) -> list[dict]:
        """Content-only lexical (FTS5/BM25) search; scores are not cosine-comparable."""
        if namespace is not None and not isinstance(namespace, str):
            raise InvalidNamespace("namespace must be a string")
        namespace = normalize_namespace(namespace)
        if namespace is not None:
            namespace = self.check_namespace(namespace)
        logger.info("lexical search: query_len=%d k=%s namespace=%s",
                    len(query) if isinstance(query, str) else -1, k, namespace)
        try:
            results = self.storage.search_lexical(query, k=k, namespace=namespace)
        except LexicalQueryError:
            # Expected input validation; transports map it to a structured 4xx.
            raise
        except Exception as e:
            logger.error("lexical search failed: %s", e, exc_info=True)
            raise
        logger.info("lexical search: %d results", len(results))
        return [_strip_internal_fields(r) for r in results]

    def memory_search_anchor(self, query: str, k: int = 10,
                             namespace: str | None = None) -> list[dict]:
        """Anchor-only lexical (FTS5/BM25) search; scores are not cosine-comparable."""
        if namespace is not None and not isinstance(namespace, str):
            raise InvalidNamespace("namespace must be a string")
        namespace = normalize_namespace(namespace)
        if namespace is not None:
            namespace = self.check_namespace(namespace)
        logger.info("anchor search: query_len=%d k=%s namespace=%s",
                    len(query) if isinstance(query, str) else -1, k, namespace)
        try:
            results = self.storage.search_anchor(query, k=k, namespace=namespace)
        except LexicalQueryError:
            # Expected input validation; transports map it to a structured 4xx.
            raise
        except Exception as e:
            logger.error("anchor search failed: %s", e, exc_info=True)
            raise
        logger.info("anchor search: %d results", len(results))
        return [_strip_internal_fields(r) for r in results]

    def memory_list(self, namespace: str | None = None, limit: int = 100,
                    offset: int = 0, sort_by: str = "created_at",
                    updated_after: str | None = None,
                    updated_before: str | None = None,
                    keywords: list | None = None,
                    categories: list | None = None) -> list[dict]:
        if sort_by not in ("created_at", "updated_at"):
            sort_by = "created_at"
        if namespace is not None and not isinstance(namespace, str):
            raise InvalidNamespace("namespace must be a string")
        namespace = normalize_namespace(namespace)
        if namespace is not None:
            namespace = self.check_namespace(namespace)
        kw = _normalize_keywords(keywords)
        cats = normalize_categories_filter(categories)
        logger.info("list: namespace=%s limit=%d offset=%d sort_by=%s updated_after=%s updated_before=%s keywords=%s categories=%s",
                    namespace, limit, offset, sort_by, updated_after, updated_before, kw, cats)
        try:
            results = self.storage.list_memories(namespace=namespace, limit=limit, offset=offset,
                                            sort_by=sort_by, updated_after=updated_after,
                                            updated_before=updated_before, keywords=kw,
                                            categories=cats)
            logger.info("list: %d results", len(results))
            return [_strip_internal_fields(r) for r in results]
        except Exception as e:
            logger.error("list failed: %s", e, exc_info=True)
            raise

    def memory_list_page(self, namespace=None, limit=20, offset=0,
                         sort_by="updated_at", updated_after=None,
                         updated_before=None, keywords=None, categories=None):
        if type(limit) is not int or limit not in (10, 20, 50):
            raise InvalidSearchFilter("limit must be 10, 20 or 50")
        if type(offset) is not int or not 0 <= offset <= 2**63 - 1:
            raise InvalidSearchFilter("offset must be a non-negative SQLite integer")
        if sort_by not in ("created_at", "updated_at"):
            raise InvalidSearchFilter("sort_by must be created_at or updated_at")
        if namespace is not None and not isinstance(namespace, str):
            raise InvalidNamespace("namespace must be a string")
        namespace = normalize_namespace(namespace)
        if namespace is not None:
            namespace = self.check_namespace(namespace)
        filters = {}
        for name, raw in (("keywords", keywords), ("categories", categories)):
            if raw is not None and (not isinstance(raw, list)
                                    or any(not isinstance(v, str) for v in raw)):
                raise InvalidSearchFilter(f"{name} must be a list of strings")
            values = _normalize_keywords(raw) or []
            if len(values) > MAX_FILTER_VALUES:
                raise InvalidSearchFilter(f"{name} accepts at most {MAX_FILTER_VALUES} values")
            filters[name] = values
        bounds = []
        for name, raw in (("updated_after", updated_after), ("updated_before", updated_before)):
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                bounds.append(None)
                continue
            try:
                stamp = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
                if stamp.tzinfo is None or stamp.utcoffset() is None:
                    raise ValueError("timezone required")
                bounds.append(stamp.astimezone(timezone.utc))
            except (ValueError, TypeError, AttributeError, OverflowError) as exc:
                raise InvalidSearchFilter(f"{name} must be an ISO 8601 timestamp with timezone") from exc
        after, before = bounds
        if after and before and after >= before:
            raise InvalidSearchFilter("updated_after must be before updated_before")
        result = self.storage.list_memories_page(
            namespace=namespace, limit=limit, offset=offset, sort_by=sort_by,
            updated_after=after.isoformat() if after else None,
            updated_before=before.isoformat() if before else None, **filters)
        return {"items": [_strip_internal_fields(row) for row in result["items"]],
                "total": result["total"]}

    def memory_get(self, memory_id: str) -> dict | None:
        logger.info("get: id=%s", memory_id)
        try:
            result = self.storage.get(memory_id)
            logger.info("get: %s", "found" if result else "not found")
            return _strip_internal_fields(result) if result else None
        except Exception as e:
            logger.error("get failed: %s", e, exc_info=True)
            raise

    def memory_update(self, memory_id: str, text: str | None = None,
                      metadata: dict | None = None,
                      namespace: str | None = None,
                      summary: str | None = None,
                      confirm_codes: list | None = None) -> dict:
        # Blank/omitted namespace means "leave unchanged"; an explicit namespace
        # must be an existing one unless the caller confirms its creation.
        if namespace is not None and not isinstance(namespace, str):
            raise InvalidNamespace("namespace must be a string")
        target_ns = normalize_namespace(namespace)
        if target_ns is not None:
            target_ns = self.check_namespace(target_ns, confirm_codes)
        summary = _normalize_summary(summary)
        # Validate caller anchors before embedding or any storage work.
        metadata = _apply_anchors_to_metadata(metadata)
        logger.info("update: id=%s has_text=%s has_metadata=%s has_ns=%s has_summary=%s",
                    memory_id, text is not None, metadata is not None,
                    target_ns is not None, summary is not None)
        try:
            if text is not None:
                embedding = self.embed(text)
            else:
                embedding = None
            dropped = categories_dropped(metadata)
            metadata = _apply_keywords_to_metadata(metadata)
            metadata = _apply_categories_to_metadata(metadata)
            index_embeddings = self._index_resolver().prepare_embeddings(metadata)
            write_kwargs = {"index_embeddings": index_embeddings} if index_embeddings else {}
            ok = self.storage.update(memory_id, text=text, embedding=embedding,
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

    def memory_delete(self, memory_id: str) -> dict:
        logger.info("delete: id=%s", memory_id)
        try:
            ok = self.storage.delete(memory_id)
            logger.info("delete: %s", "ok" if ok else "not found")
            return {"success": ok}
        except Exception as e:
            logger.error("delete failed: %s", e, exc_info=True)
            raise

    def memory_clear(self, namespace: str | None = None) -> dict:
        logger.info("clear: namespace=%s", namespace)
        try:
            n = self.storage.clear(namespace=namespace)
            logger.info("clear: deleted=%d", n)
            return {"deleted": n}
        except Exception as e:
            logger.error("clear failed: %s", e, exc_info=True)
            raise

    def memory_list_keywords(self, namespace=None, mode="browse", queries=None, limit=None,
                             offset=0, sort_by=None, min_score=None, categories=None):
        """Read the complete live vocabulary, optionally by name or meaning.

        ``categories`` (optional) restricts results to keywords used by live
        memories carrying any of the listed categories (exact match, OR); it
        composes with ``namespace`` using AND. Omitted/empty means no filter.
        """
        import math
        if namespace is not None:
            namespace = self.check_namespace(namespace)
        if categories is not None and not isinstance(categories, list):
            raise ValueError("categories must be a list of strings")
        cats = normalize_categories_filter(categories)
        if len(cats) > MAX_FILTER_VALUES:
            raise ValueError(f"categories accepts at most {MAX_FILTER_VALUES} filter values")
        if mode not in ("browse", "literal", "semantic"):
            raise ValueError("mode must be browse, literal or semantic")
        semantic = mode == "semantic"
        if mode == "browse":
            if queries is not None:
                raise ValueError("browse does not accept queries")
            queries = [None]
        elif (not isinstance(queries, list) or not 1 <= len(queries) <= (20 if semantic else 1)
              or any(not isinstance(q, str) or not q.strip() for q in queries)):
            raise ValueError("queries must contain 1–20 nonblank strings (exactly one for literal)")
        limit = (5 if semantic else 50) if limit is None else limit
        if type(limit) is not int or not 1 <= limit <= (20 if semantic else 100):
            raise ValueError("invalid limit")
        if type(offset) is not int or offset < 0 or (semantic and offset != 0):
            raise ValueError("invalid offset")
        sort_by = ("score" if semantic else "count") if sort_by is None else sort_by
        if sort_by not in (("score",) if semantic else ("count", "keyword")):
            raise ValueError("invalid sort_by for mode")
        if not semantic and min_score is not None:
            raise ValueError("min_score is only valid for semantic mode")
        if semantic:
            min_score = 0.65 if min_score is None else min_score
            if (type(min_score) not in (int, float) or not math.isfinite(min_score)
                    or not 0 <= min_score <= 1):
                raise ValueError("min_score must be between 0 and 1")
        counts = self.storage.keyword_counts(namespace, cats or None)
        groups = []
        # Duplicate concepts share inference but retain their original response positions.
        scored_queries = {}
        for query in queries:
            normalized = query.strip().lower() if query is not None else None
            if semantic:
                if normalized not in scored_queries:
                    scored_queries[normalized] = self.storage.semantic_index_values(
                        "keywords", self.embed_query(normalized), namespace)
                items = [{"keyword": r["value"], "count": counts[r["value"]], "score": r["score"]}
                         for r in scored_queries[normalized]
                         if r["value"] in counts and r["score"] >= min_score]
                items.sort(key=lambda r: (-r["score"], -r["count"], r["keyword"]))
            else:
                items = [{"keyword": k, "count": n} for k, n in counts.items()
                         if normalized is None or normalized in k]
                items.sort(key=(lambda r: r["keyword"]) if sort_by == "keyword"
                           else (lambda r: (-r["count"], r["keyword"])))
            groups.append({"query": query, "total": len(items), "items": items[offset:offset + limit]})
        result = {"namespace": namespace, "mode": mode, "categories": cats or None,
                  "results": groups, "limit": limit, "offset": offset, "sort_by": sort_by}
        if semantic:
            result["min_score"] = min_score
        return result

    def memory_stats(self) -> dict:
        try:
            stats = dict(self.storage.get_stats())
            stats["keyword_total"] = len(stats.pop("keyword_counts", {}))
            # Seeds always present (count 0 when unused) so the LLM can reuse them.
            stats["category_counts"] = category_counts_with_seeds(
                stats.get("category_counts") or {})
            stats["embed_model"] = self.embedding_provider.spec.model
            stats["embed_provider"] = self.embedding_provider.spec.provider
            stats["embed_dimension"] = self.embedding_provider.spec.dimension
            stats["embed_profile"] = self.embedding_provider.spec.profile_id
            stats["embed_dtype"] = self.settings.EMBED_DTYPE
            stats["ollama_base_url"] = self.settings.OLLAMA_BASE_URL
            stats["server_time"] = _now_utc_iso()
            if self.settings.environment.get("MEMORY_PATHS_ACTIVE") == "1":
                stats = self._relative_paths(stats)
            return stats
        except Exception as e:
            logger.error("stats failed: %s", e, exc_info=True)
            raise

    def memory_export_full(self, destination: str) -> dict:
        try:
            return self.storage.export_full_snapshot(destination)
        except StorageCapabilityError as exc:
            raise RuntimeError(str(exc)) from exc

    def memory_export_namespace(self, namespace: str, destination: str) -> dict:
        namespace = (namespace or "").strip()
        if not namespace:
            raise ValueError("namespace is required")
        records = self.storage.export_records(namespace)
        if not records:
            raise LookupError(f"namespace not found: {namespace}")
        write_clean_jsonl(records, destination)
        return {"records": len(records), "bytes": os.path.getsize(destination),
                "sha256": sha256_file(destination), "path": destination,
                "namespace": namespace}

    def _set_import_job(self, import_id, **changes):
        self._imports.update(import_id, **changes)

    def memory_import_status(self, import_id: str) -> dict | None:
        return self._imports.status(import_id)

    def wait_import(self, import_id: str, timeout: float | None = None) -> dict | None:
        """Wait for final cleanup and snapshot; raise TimeoutError if not done."""
        return self._imports.wait(import_id, timeout)

    def _run_import_job(self, import_id, file_path, original_name, namespace_prefix):
        started = time.perf_counter()
        audit_args = {"import_id": import_id, "filename": original_name,
                      "namespace_prefix": namespace_prefix}
        timings = {}
        phase_started = time.perf_counter()
        try:
            audit_args.update({"bytes": os.path.getsize(file_path),
                               "sha256": sha256_file(file_path)})
            self._set_import_job(import_id, status="validating", phase="validating")
            loaded = load_jsonl(file_path, max_records=self.settings.IMPORT_MAX_RECORDS)
            source_total = len(loaded["records"])
            records = []
            for item in loaded["records"]:
                copied = dict(item)
                copied["namespace"] = namespace_prefix + copied["namespace"]
                records.append(copied)
            records = _deduplicate_source(records)
            effective_total = len(records)
            target_pairs = {(item["namespace"], item["text"])
                            for item in self.storage.export_records()}
            pre_skipped = source_total - effective_total
            planned = []
            for item in records:
                if (item["namespace"], item["text"]) in target_pairs:
                    pre_skipped += 1
                else:
                    planned.append(item)
            timings["validating"] = round((time.perf_counter() - phase_started) * 1000, 3)
            profile = self.storage.active_embedding_profile()
            if not profile:
                raise RuntimeError("target database has no active embedding profile")

            self._set_import_job(import_id, status="embedding_memories", phase="embedding_memories",
                            source_total=source_total, effective_total=effective_total,
                            progress={"completed": 0, "total": len(planned)},
                            target_profile=profile, timings_ms=dict(timings))
            phase_started = time.perf_counter()
            vectors = self.embed_documents([item["text"] for item in planned]) if planned else []
            if len(vectors) != len(planned):
                raise RuntimeError("embedding provider returned the wrong number of memory vectors")
            memory_vectors = {item["id"]: vector for item, vector in zip(planned, vectors)}
            timings["embedding_memories"] = round(
                (time.perf_counter() - phase_started) * 1000, 3
            )
            self._set_import_job(import_id, progress={"completed": len(planned), "total": len(planned)},
                            status="embedding_index_values", phase="embedding_index_values",
                            timings_ms=dict(timings))

            phase_started = time.perf_counter()
            missing = set()
            for item in planned:
                missing.update(self.storage.missing_index_value_embeddings(item.get("metadata") or {}))
            missing = sorted(missing)
            index_vectors_list = (
                self.embed_documents([value for _dimension, value in missing]) if missing else []
            )
            if len(index_vectors_list) != len(missing):
                raise RuntimeError("embedding provider returned the wrong number of index vectors")
            index_vectors = dict(zip(missing, index_vectors_list))
            timings["embedding_index_values"] = round(
                (time.perf_counter() - phase_started) * 1000, 3
            )

            self._set_import_job(import_id, status="committing", phase="committing",
                            timings_ms=dict(timings))
            phase_started = time.perf_counter()
            result = self.storage.import_records(planned, memory_vectors, index_vectors, profile)
            timings["committing"] = round((time.perf_counter() - phase_started) * 1000, 3)
            result.update({"source_total": source_total, "effective_total": effective_total,
                           "skipped": result["skipped"] + pre_skipped,
                           "target_profile": profile, "timings_ms": timings})
            completed_at = _now_utc_iso()
            self._set_import_job(import_id, status="completed", phase="completed", result=result,
                            completed_at=completed_at)
            self.audit.record("memory_import", audit_args, result,
                         (time.perf_counter() - started) * 1000)
        except Exception as exc:
            logger.error("JSONL import failed id=%s", import_id, exc_info=True)
            error = {"type": type(exc).__name__, "message": str(exc)}
            self._set_import_job(import_id, status="failed", phase="failed", error=error,
                            timings_ms=timings,
                            completed_at=_now_utc_iso())
            self.audit.record("memory_import", audit_args, None,
                         (time.perf_counter() - started) * 1000, error=error)

    def _start_import(self, file_path: str, original_name: str,
                            namespace_prefix: str = "") -> dict:
        if not original_name.lower().endswith((".jsonl", ".ndjson")):
            raise ValueError("import file must end with .jsonl or .ndjson")
        file_bytes = os.path.getsize(file_path)
        if file_bytes > self.settings.IMPORT_MAX_BYTES:
            raise ValueError(f"import file exceeds {self.settings.IMPORT_MAX_BYTES} bytes")
        file_sha256 = sha256_file(file_path)
        return self._imports.start(
            file_path, original_name, namespace_prefix,
            job_fields={"bytes": file_bytes, "sha256": file_sha256},
        )

    def memory_get_details(self, memory_ids: list[str]) -> dict:
        """Fetch full content for a list of memory IDs (partial success).

        Returns {"found": [...], "missing": [...]}. Embeddings are stripped.
        """
        logger.info("get_details: ids=%s", memory_ids)
        try:
            result = self.storage.get_details(memory_ids or [])
            result["found"] = [_strip_internal_fields(m) for m in result["found"]]
            logger.info("get_details: found=%d missing=%d", len(result["found"]), len(result["missing"]))
            return result
        except Exception as e:
            logger.error("get_details failed: %s", e, exc_info=True)
            raise

    def memory_sync(self) -> dict:
        """Manually trigger full sync of secondary from primary (dual mode only)."""
        logger.info("sync")
        try:
            return self.storage.force_sync()
        except StorageCapabilityError:
            return {"error": "storage backend does not support sync"}
        except Exception as e:
            logger.error("sync failed: %s", e, exc_info=True)
            raise

    def memory_namespaces(self) -> dict:
        """Return all namespace names and their record counts."""
        stats = self.storage.get_stats()
        return {
            "namespaces": stats.get("namespaces", []),
            "namespace_counts": stats.get("namespace_counts", {}),
        }

    def memory_storage_paths(self) -> dict:
        """Expose the configured storage paths for the UI.

        In dual mode: primary_path is the SQLite primary, secondary_path is the
        NDJSON disaster-recovery replica. In single-backend mode only the
        backend type is reported (no replica paths).
        """
        result = self.storage.storage_paths()
        return self._relative_paths(result) if self.settings.environment.get("MEMORY_PATHS_ACTIVE") == "1" else result

    def _relative_paths(self, value):
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if key in ("path", "primary_path", "secondary_path") and isinstance(item, str) and item not in (":memory:", "?"):
                    result[key] = str(Path(item).resolve().relative_to(Path(self.settings.DATA_DIR).resolve()))
                else:
                    result[key] = self._relative_paths(item)
            return result
        if isinstance(value, list):
            return [self._relative_paths(item) for item in value]
        return value

    def memory_migrate_namespace(self, source: str, target: str) -> dict:
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
            memories = self.memory_list(namespace=source_ns, limit=10 ** 9)
        except NamespaceNotFound:
            return {"error": f"no memories found in namespace '{source_ns}'", "migrated": 0}
        if not memories:
            return {"error": f"no memories found in namespace '{source_ns}'", "migrated": 0}
        migrated = 0
        for m in memories:
            self.memory_update(m["id"], namespace=target_ns,
                          confirm_codes=[NAMESPACE_NOT_FOUND])
            migrated += 1
        return {"migrated": migrated, "source": source_ns, "target": target_ns}

    def memory_report(self, search_id, ratings) -> dict:
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
