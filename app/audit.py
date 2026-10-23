"""Tool-call and maintenance-work audit — append-only event journal.

Records MCP tool invocations and selected maintenance outcomes (time, tool,
status, duration, namespace, params summary, result summary) to a JSONL file
for human review in the admin UI. The audit stream is deliberately *not*
exposed to the LLM: it lives in the platform layer.

Guarantees:

- Never raises: audit failures must not affect the tool call path.
- Thread-safe: appends are protected by a lock.
- Bounded: files rotate by size (or an optional per-file event count), and
  the oldest backup is discarded after the configured file-count limit.
- Independent of the memory stores: the audit journal is separate from the
  SQLite primary / JSONL replica used for memories.
"""

import os
import json
import logging
import threading
import contextvars
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("audit")
request_audit_context = contextvars.ContextVar("request_audit_context", default=None)
# Per-request context for the Streamable HTTP transport. The /mcp ASGI adapter
# sets a fresh dict for each request (never a shared, mutated one) and resets it
# in `finally`, so tool-call events can carry a stable HTTP correlation id.
# MCPApplication._audit_context() merges this explicitly; it is not merged into
# `request_audit_context`, which the admin/SSE admission middleware owns.
http_transport_context = contextvars.ContextVar("http_transport_context", default=None)

AUDIT_PATH = "audit.jsonl"
AUDIT_MAX_BYTES = 10 * 1024 * 1024
AUDIT_MAX_FILES = 10
AUDIT_MAX_EVENTS = 0


# User-content keys stripped from the recorded params: the journal must not
# duplicate memory bodies (they already live in the primary store).
_TEXT_KEYS = ("text", "summary")
_MAX_FILTER_VALUES = 20
_MAX_FILTER_MATCHES = 3
_MAX_CONFIRM_CODES = 10
_MAX_AUDIT_RATINGS = 1000

_DEFAULT_POLICY = "default"

# Every event name the journal can receive: MCP tools plus background/admin
# events that have no MCP tool. Each name is explicitly bound to a params and a
# result policy below, so a new tool cannot silently inherit default trimming.
# Tests assert the MCP tool set is a subset of this set.
AUDIT_EVENTS = frozenset({
    "add", "update", "delete", "stats", "report",
    "search_semantic", "search_lexical", "search_anchor", "search_meta",
    "get_details", "list_keywords",
    "memory_import", "memory_export", "maintenance_enter", "maintenance_exit",
    "model_config", "model_prepare", "model_retry", "online_recover",
    "online_recover_start", "ndjson_health_check", "ndjson_compact",
    "embedding_reindex", "index_value_reindex",
    "version_check",
})

_default_lock = threading.RLock()
_default_journal = None


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iter_lines_reversed(path, chunk_size: int = 65536):
    """Yield a file's raw lines newest-first without reading it all at once."""
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        buffer = b""
        while position > 0:
            read = min(chunk_size, position)
            position -= read
            handle.seek(position)
            buffer = handle.read(read) + buffer
            lines = buffer.split(b"\n")
            buffer = lines.pop(0)
            for line in reversed(lines):
                yield line
        if buffer:
            yield buffer


def _params_default(args, result):
    """No tool-specific params handling; the common text/id rules still apply."""
    return args


def _params_list_keywords(args, result):
    args = {k: v for k, v in args.items() if k in
            ("namespace", "mode", "queries", "limit", "offset", "sort_by", "min_score",
             "categories")}
    queries = args.get("queries")
    if isinstance(queries, list):
        args["queries"] = [str(q)[:1000] for q in queries[:20]]
        args["queries_truncated"] = len(queries) > 20 or any(len(str(q)) > 1000 for q in queries[:20])
    categories = args.get("categories")
    if isinstance(categories, list):
        args["categories"] = [str(c)[:200] for c in categories[:20]]
        args["categories_truncated"] = len(categories) > 20
    return args


def _params_add(args, result):
    summary = args.get("summary")
    if isinstance(result, dict) and result.get("created") is False:
        args["summary_mode"] = "duplicate"
    elif isinstance(summary, str) and summary.strip():
        args["summary_mode"] = "input"
    else:
        args["summary_mode"] = "text_extract"
    return args


def _params_update(args, result):
    summary = args.get("summary")
    if isinstance(summary, str) and summary.strip():
        args["summary_mode"] = "input"
    elif args.get("text") is not None:
        args["summary_mode"] = "text_extract"
    else:
        args["summary_mode"] = "preserved"
    return args


def _params_transfer(args, result):
    args.pop("path", None)
    return args


def _params_version_check(args, result):
    return {"trigger": args.get("trigger") if args.get("trigger") in
            ("startup", "automatic", "manual") else "unknown"}


def _params_search_filters(args, result):
    for dimension in ("keywords", "categories"):
        values = args.get(dimension)
        if isinstance(values, list) and len(values) > _MAX_FILTER_VALUES:
            args[dimension] = values[:_MAX_FILTER_VALUES]
            args[f"{dimension}_total"] = len(values)
            args[f"{dimension}_truncated"] = True
    return args


def _params_report(args, result):
    for field in ("search_id", "feedback_source", "feedback_purpose"):
        if field in args:
            args[field] = str(args[field])[:64]
    ratings = args.get("ratings")
    if isinstance(ratings, list):
        args["ratings"] = [
            {"m_id": str(item.get("m_id"))[:64],
             "rating": item.get("rating") if type(item.get("rating")) is int
             else str(item.get("rating"))[:32]}
            if isinstance(item, dict) else {"invalid_type": type(item).__name__}
            for item in ratings[:_MAX_AUDIT_RATINGS]
        ]
        if len(ratings) > _MAX_AUDIT_RATINGS:
            args["ratings_total"] = len(ratings)
            args["ratings_truncated"] = True
    elif "ratings" in args:
        args["ratings"] = {"invalid_type": type(ratings).__name__}
    return args


# Every AUDIT_EVENTS name is bound to a policy here (default unless overridden),
# so adding a tool forces an explicit trimming decision.
PARAM_POLICIES: dict[str, object] = {
    **{name: _params_default for name in AUDIT_EVENTS},
    _DEFAULT_POLICY: _params_default,
    "list_keywords": _params_list_keywords,
    "add": _params_add,
    "update": _params_update,
    "memory_import": _params_transfer,
    "memory_export": _params_transfer,
    "search_semantic": _params_search_filters,
    "search_lexical": _params_search_filters,
    "search_anchor": _params_search_filters,
    "report": _params_report,
    "version_check": _params_version_check,
}


def _trim_params(tool: str, arguments: dict | None, result=None) -> dict:
    """Per-tool params record — drop user-content fields, keep metadata."""
    args = dict(arguments or {})
    for key in ("keyword_query_id", "source_search_id"):
        if key in args:
            args[key] = str(args[key])[:64]
    policy = PARAM_POLICIES.get(tool, PARAM_POLICIES[_DEFAULT_POLICY])
    args = policy(args, result)
    for key in _TEXT_KEYS:
        args.pop(key, None)
    return args


def _confirm_codes(arguments) -> list | None:
    """Record confirmation codes as-is, capped and type-filtered."""
    codes = (arguments or {}).get("confirm_codes")
    if not isinstance(codes, list) or not codes:
        return None
    kept = [c for c in codes if isinstance(c, str)][:_MAX_CONFIRM_CODES]
    return kept or None


def _trim_filter_resolution(resolution):
    if not isinstance(resolution, dict):
        return resolution
    trimmed = {"config": resolution.get("config")}
    for dimension in ("keywords", "categories"):
        records = resolution.get(dimension)
        if not isinstance(records, list):
            continue
        kept = []
        for record in records[:_MAX_FILTER_VALUES]:
            if not isinstance(record, dict):
                continue
            item = dict(record)
            matches = item.get("matches")
            if isinstance(matches, list):
                item["matches"] = matches[:_MAX_FILTER_MATCHES]
                if len(matches) > _MAX_FILTER_MATCHES:
                    item["matches_total"] = len(matches)
                    item["matches_truncated"] = True
            kept.append(item)
        trimmed[dimension] = kept
        if len(records) > _MAX_FILTER_VALUES:
            trimmed[f"{dimension}_total"] = len(records)
            trimmed[f"{dimension}_truncated"] = True
    return trimmed


def _result_default(tool, result):
    return dict(result)


def _result_search(tool, result):
    items = result.get("items") or []
    keep_score = tool in ("search_semantic", "search_lexical", "search_anchor")
    trimmed = {
        "search_id": result.get("search_id"),
        "total": result.get("total", len(items)),
        **({"filter_resolution": _trim_filter_resolution(result.get("filter_resolution"))}
           if tool == "search_semantic" and result.get("filter_resolution") is not None
           else {}),
        "items": [
            {"id": it.get("id"),
             "summary": it.get("summary"),
             **({"score": it.get("score")} if keep_score else {})}
            for it in items
        ],
    }
    if result.get("response_code") is not None:
        trimmed["response_code"] = result["response_code"]
    if result.get("response_meta") is not None:
        trimmed["response_meta"] = result["response_meta"]
    return trimmed


def _result_list_keywords(tool, result):
    trimmed = {k: v for k, v in result.items() if k != "results"}
    groups = result.get("results", [])
    trimmed["results"] = [
        {"query": str(g["query"])[:1000] if g.get("query") is not None else None,
         "total": g.get("total"), "items": g.get("items", [])[:5],
         "items_truncated": len(g.get("items", [])) > 5}
        for g in groups[:20]]
    trimmed["results_truncated"] = len(groups) > 20
    return trimmed


def _result_get_details(tool, result):
    found = []
    for it in result.get("found") or []:
        found.append({"id": it.get("id"), "summary": it.get("summary"),
                      "keywords": (it.get("metadata") or {}).get("keywords"),
                      "categories": (it.get("metadata") or {}).get("categories")})
    return {"found": found, "missing": result.get("missing", [])}


def _result_stats(tool, result):
    # Drop the keyword vocabulary (hundreds of entries would bloat the journal
    # on every stats call) and provider URLs (config, not stats).
    return {k: v for k, v in result.items()
            if k not in ("keyword_counts", "ollama_base_url")}


def _result_transfer(tool, result):
    return {key: value for key, value in result.items()
            if key not in ("path", "source_path", "imported_ids")}


def _result_version_check(tool, result):
    return {key: result.get(key) for key in ("status", "error_code", "notes_complete")}


# Every AUDIT_EVENTS name is bound to a policy here (default unless overridden),
# so adding a tool forces an explicit trimming decision.
RESULT_POLICIES: dict[str, object] = {
    **{name: _result_default for name in AUDIT_EVENTS},
    _DEFAULT_POLICY: _result_default,
    "search_semantic": _result_search,
    "search_lexical": _result_search,
    "search_anchor": _result_search,
    "search_meta": _result_search,
    "list_keywords": _result_list_keywords,
    "get_details": _result_get_details,
    "stats": _result_stats,
    "memory_import": _result_transfer,
    "memory_export": _result_transfer,
    "version_check": _result_version_check,
}


def _trim_result(tool: str, result) -> dict | None:
    """Per-tool result record — keep light summaries only.

    - search_*: ``[{id, summary, score?}]`` (BM25 for lexical/anchor)
    - list_keywords / get_details / stats / transfer events: bounded subsets
    - others: pass through as-is (already small).
    """
    if result is None:
        return None
    if not isinstance(result, dict):
        return {"value": str(result)}
    policy = RESULT_POLICIES.get(tool, RESULT_POLICIES[_DEFAULT_POLICY])
    return policy(tool, result)


class AuditJournal:
    """One runtime's audit file and rotation state, independent of other runtimes."""

    def __init__(self, environment=None):
        env = os.environ if environment is None else environment
        self.path = Path(env.get("MEMORY_AUDIT_PATH", "audit.jsonl"))
        self.max_bytes = int(env.get("MEMORY_AUDIT_MAX_BYTES", "10485760"))
        self.max_files = max(1, int(env.get("MEMORY_AUDIT_MAX_FILES", "10")))
        self.max_events = int(env.get("MEMORY_AUDIT_MAX_EVENTS", "0"))
        self._lock = threading.Lock()
        self._file = None
        self._count = 0

    def close(self):
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None
            self._count = 0

    def _open(self):
        if self._file is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a", encoding="utf-8")
        # Only count events when a count-based rotation is configured; otherwise
        # the whole-file read at open is pure startup cost.
        if self.max_events > 0 and self.path.stat().st_size:
            with self.path.open("r", encoding="utf-8") as handle:
                self._count = sum(1 for _ in handle)

    def _rotate(self):
        if self._file is not None:
            self._file.close()
            self._file = None
        try:
            if self.max_files == 1:
                if self.path.exists():
                    self.path.unlink()
            else:
                for suffix in range(self.max_files - 1, 0, -1):
                    source = self.path if suffix == 1 else Path(f"{self.path}.{suffix - 1}")
                    target = Path(f"{self.path}.{suffix}")
                    if source.exists():
                        os.replace(source, target)
            self._count = 0
        except OSError:
            logger.exception("audit rotation failed")
        finally:
            self._open()

    def record(self, tool, arguments, result, duration_ms, error=None, *, context=None,
               response_code=None, memory_refs=None):
        """Append one bounded event; failures never affect the caller."""
        try:
            with self._lock:
                self._open()
                search_id = ((result or {}).get("search_id") if isinstance(result, dict)
                             else None) or (arguments or {}).get("search_id")
                event = {
                    "ts": _now_utc_iso(), "tool": tool,
                    "status": "error" if error else "ok",
                    "duration_ms": round(duration_ms, 1),
                    "namespace": (arguments or {}).get("namespace"),
                    "search_id": str(search_id)[:64] if search_id else None,
                    "source_search_id": str((arguments or {}).get("source_search_id"))[:64]
                                        if (arguments or {}).get("source_search_id") else None,
                    "keyword_query_id": ((result or {}).get("keyword_query_id")
                                         if isinstance(result, dict) else None)
                                        or (str((arguments or {}).get("keyword_query_id"))[:64]
                                            if (arguments or {}).get("keyword_query_id") else None),
                    "confirm_codes": _confirm_codes(arguments),
                    "response_code": response_code or (result.get("response_code")
                                          if isinstance(result, dict) else None),
                    "error": error, "params": _trim_params(tool, arguments, result),
                    "result": _trim_result(tool, result),
                    "context": context or request_audit_context.get() or {"source": "background"},
                    "memory_refs": (memory_refs or [])[:100],
                }
                if not event["namespace"] and event["memory_refs"]:
                    namespaces = {ref.get("namespace") for ref in event["memory_refs"]}
                    if len(namespaces) == 1:
                        event["namespace"] = next(iter(namespaces))
                encoded = (json.dumps(event, ensure_ascii=False, default=str) + "\n").encode("utf-8")
                size = self.path.stat().st_size
                if ((self.max_bytes > 0 and size > 0 and size + len(encoded) > self.max_bytes)
                        or (self.max_events > 0 and self._count >= self.max_events)):
                    self._rotate()
                self._open()
                self._file.write(encoded.decode("utf-8"))
                self._file.flush()
                self._count += 1
        except Exception:
            logger.exception("audit record failed")

    def read_events(self, limit=500, tool=None, namespace=None):
        events = []
        paths = [self.path] + [Path(f"{self.path}.{suffix}")
                               for suffix in range(1, self.max_files)]
        for candidate in paths:
            try:
                if not candidate.exists():
                    continue
                # Read newest-first incrementally; stop as soon as the limit is
                # met instead of loading each whole file into memory.
                for raw in _iter_lines_reversed(candidate):
                    text = raw.decode("utf-8", "replace").strip()
                    if not text:
                        continue
                    try:
                        event = json.loads(text)
                    except (TypeError, ValueError):
                        continue
                    if tool and event.get("tool") != tool:
                        continue
                    if namespace and event.get("namespace") != namespace:
                        continue
                    events.append(event)
                    if len(events) >= limit:
                        return events
            except (OSError, UnicodeError):
                logger.exception("audit read failed for %s", candidate)
        return events


def configure(environment=None):
    """Point the legacy module API at one journal; runtime instances stay independent."""
    global _default_journal, AUDIT_PATH, AUDIT_MAX_BYTES, AUDIT_MAX_FILES, AUDIT_MAX_EVENTS
    journal = AuditJournal(environment)
    with _default_lock:
        if _default_journal is not None:
            _default_journal.close()
        _default_journal = journal
        AUDIT_PATH = str(journal.path)
        AUDIT_MAX_BYTES = journal.max_bytes
        AUDIT_MAX_FILES = journal.max_files
        AUDIT_MAX_EVENTS = journal.max_events


def _legacy_journal():
    if _default_journal is None:
        configure()
    return _default_journal


def close():
    with _default_lock:
        if _default_journal is not None:
            _default_journal.close()


def record(tool: str, arguments: dict | None, result, duration_ms: float,
           error: dict | None = None, *, context=None, response_code=None,
           memory_refs=None) -> None:
    """Compatibility entrypoint; all writing and rotation use AuditJournal."""
    try:
        with _default_lock:
            _legacy_journal().record(tool, arguments, result, duration_ms, error=error,
                                     context=context, response_code=response_code,
                                     memory_refs=memory_refs)
    except Exception:
        logger.exception("audit record failed")


def read_events(limit: int = 500, tool: str | None = None,
                namespace: str | None = None) -> list[dict]:
    with _default_lock:
        return _legacy_journal().read_events(limit=limit, tool=tool, namespace=namespace)
