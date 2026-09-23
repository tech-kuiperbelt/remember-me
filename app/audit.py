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

AUDIT_PATH = "data/audit.jsonl"
AUDIT_MAX_BYTES = 10 * 1024 * 1024
AUDIT_MAX_FILES = 10
AUDIT_MAX_EVENTS = 0


def configure(environment=None):
    """Configure explicitly at an entrypoint; never read environment at import."""
    global AUDIT_PATH, AUDIT_MAX_BYTES, AUDIT_MAX_FILES, AUDIT_MAX_EVENTS
    env = os.environ if environment is None else environment
    path = env.get("MEMORY_AUDIT_PATH", "data/audit.jsonl")
    max_bytes = int(env.get("MEMORY_AUDIT_MAX_BYTES", "10485760"))
    max_files = max(1, int(env.get("MEMORY_AUDIT_MAX_FILES", "10")))
    max_events = int(env.get("MEMORY_AUDIT_MAX_EVENTS", "0"))
    close()
    with _lock:
        AUDIT_PATH, AUDIT_MAX_BYTES = path, max_bytes
        AUDIT_MAX_FILES, AUDIT_MAX_EVENTS = max_files, max_events


def close():
    global _file, _count
    with _lock:
        if _file is not None:
            _file.close()
            _file = None
        _count = 0


# User-content keys stripped from the recorded params: the journal must not
# duplicate memory bodies (they already live in the primary store).
_TEXT_KEYS = ("text", "summary")
_MAX_FILTER_VALUES = 20
_MAX_FILTER_MATCHES = 3
_MAX_CONFIRM_CODES = 10

_lock = threading.Lock()
_file = None
_count = 0


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _open_file() -> None:
    """Open the journal in append mode, creating parent dirs as needed."""
    global _file, _count
    if _file is not None:
        return
    path = Path(AUDIT_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    _file = open(path, "a", encoding="utf-8")
    if path.exists() and path.stat().st_size > 0:
        with path.open("r", encoding="utf-8") as fh:
            _count = sum(1 for _ in fh)


def _rotate() -> bool:
    """Rotate the current journal and reopen it. Never raises.

    ``AUDIT_MAX_FILES`` includes the active file, so a value of 10 keeps the
    active journal plus ``.1`` through ``.9``. A failed rename may leave a
    partial backup shift, but the active path is always reopened so auditing
    can continue instead of becoming permanently disabled.
    """
    global _file, _count
    path = Path(AUDIT_PATH)
    try:
        if _file is not None:
            _file.close()
            _file = None
        if AUDIT_MAX_FILES == 1:
            if path.exists():
                path.unlink()
        else:
            for suffix in range(AUDIT_MAX_FILES - 1, 0, -1):
                source = path if suffix == 1 else Path(f"{path}.{suffix - 1}")
                target = Path(f"{path}.{suffix}")
                if source.exists():
                    os.replace(source, target)
        _count = 0
        return True
    except Exception as exc:
        logger.error("audit rotation failed: %s", exc, exc_info=True)
        return False
    finally:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _file = open(path, "a", encoding="utf-8")
            if path.exists() and path.stat().st_size > 0:
                with path.open("r", encoding="utf-8") as fh:
                    _count = sum(1 for _ in fh)
        except Exception as exc:
            _file = None
            logger.error("audit journal reopen failed: %s", exc, exc_info=True)


def _should_rotate(next_size: int) -> bool:
    """Return whether the next encoded event should start a new file."""
    path = Path(AUDIT_PATH)
    current_size = path.stat().st_size if path.exists() else 0
    by_size = (AUDIT_MAX_BYTES > 0 and current_size > 0 and
               current_size + next_size > AUDIT_MAX_BYTES)
    by_count = AUDIT_MAX_EVENTS > 0 and _count >= AUDIT_MAX_EVENTS
    return by_size or by_count


def _trim_params(tool: str, arguments: dict | None, result=None) -> dict:
    """Per-tool params record — drop user-content fields, keep metadata."""
    args = dict(arguments or {})
    for key in ("keyword_query_id", "source_search_id"):
        if key in args:
            args[key] = str(args[key])[:64]
    if tool == "list_keywords":
        args = {k: v for k, v in args.items() if k in
                ("namespace", "mode", "queries", "limit", "offset", "sort_by", "min_score")}
        queries = args.get("queries")
        if isinstance(queries, list):
            args["queries"] = [str(q)[:1000] for q in queries[:20]]
            args["queries_truncated"] = len(queries) > 20 or any(len(str(q)) > 1000 for q in queries[:20])
    if tool == "add":
        summary = args.get("summary")
        if isinstance(result, dict) and result.get("created") is False:
            args["summary_mode"] = "duplicate"
        elif isinstance(summary, str) and summary.strip():
            args["summary_mode"] = "input"
        else:
            args["summary_mode"] = "text_extract"
    elif tool == "update":
        summary = args.get("summary")
        if isinstance(summary, str) and summary.strip():
            args["summary_mode"] = "input"
        elif args.get("text") is not None:
            args["summary_mode"] = "text_extract"
        else:
            args["summary_mode"] = "preserved"
    elif tool in ("memory_import", "memory_export"):
        args.pop("path", None)
    for key in _TEXT_KEYS:
        args.pop(key, None)
    if tool == "search_semantic":
        for dimension in ("keywords", "categories"):
            values = args.get(dimension)
            if isinstance(values, list) and len(values) > _MAX_FILTER_VALUES:
                args[dimension] = values[:_MAX_FILTER_VALUES]
                args[f"{dimension}_total"] = len(values)
                args[f"{dimension}_truncated"] = True
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


def _trim_result(tool: str, result) -> dict | None:
    """Per-tool result record — keep light summaries only.

    - search_semantic: ``[{id, summary, score}]``
    - search_meta:     ``[{id, summary}]`` (no score — list is exact, not cosine)
    - get_details:     ``{found: [{id, summary, keywords}], missing: [...]}``
    - stats:           everything except the keyword vocabulary and model URLs
    - others:          pass through as-is (already small).
    """
    if result is None:
        return None
    if not isinstance(result, dict):
        return {"value": str(result)}

    if tool == "search_semantic" or tool == "search_meta":
        items = result.get("items") or []
        keep_score = tool == "search_semantic"
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

    if tool == "list_keywords":
        trimmed = {k: v for k, v in result.items() if k != "results"}
        groups = result.get("results", [])
        trimmed["results"] = [
            {"query": str(g["query"])[:1000] if g.get("query") is not None else None,
             "total": g.get("total"), "items": g.get("items", [])[:5],
             "items_truncated": len(g.get("items", [])) > 5}
            for g in groups[:20]]
        trimmed["results_truncated"] = len(groups) > 20
        return trimmed

    if tool == "get_details":
        found = []
        for it in result.get("found") or []:
            found.append({"id": it.get("id"), "summary": it.get("summary"),
                          "keywords": (it.get("metadata") or {}).get("keywords"),
                          "categories": (it.get("metadata") or {}).get("categories")})
        return {"found": found, "missing": result.get("missing", [])}

    if tool == "stats":
        # Drop the keyword vocabulary (hundreds of entries would bloat the
        # journal on every stats call) and provider URLs (config, not stats).
        return {k: v for k, v in result.items()
                if k not in ("keyword_counts", "ollama_base_url")}

    if tool in ("memory_import", "memory_export"):
        return {key: value for key, value in result.items()
                if key not in ("path", "source_path", "imported_ids")}

    return dict(result)


def record(tool: str, arguments: dict | None, result, duration_ms: float,
           error: dict | None = None) -> None:
    """Append one audit event. Never raises.

    ``error`` is ``{"type": ..., "message": ...}`` for failed calls, else None.
    """
    global _count
    try:
        with _lock:
            _open_file()
            event = {
                "ts": _now_utc_iso(),
                "tool": tool,
                "status": "error" if error else "ok",
                "duration_ms": round(duration_ms, 1),
                "namespace": (arguments or {}).get("namespace"),
                "confirm_codes": _confirm_codes(arguments),
                "response_code": (result.get("response_code")
                                  if isinstance(result, dict) else None),
                "error": error,
                "params": _trim_params(tool, arguments, result),
                "result": _trim_result(tool, result),
            }
            line = json.dumps(event, ensure_ascii=False, default=str) + "\n"
            if _should_rotate(len(line.encode("utf-8"))):
                _rotate()
            if _file is None:
                _open_file()
            _file.write(line)
            _file.flush()
            _count += 1
    except Exception as e:
        # Audit is best-effort — never let it break a tool call.
        logger.error("audit record failed: %s", e, exc_info=True)


def read_events(limit: int = 500, tool: str | None = None,
                namespace: str | None = None) -> list[dict]:
    """Return recent events, newest first, with optional filters.

    Lines that fail to parse are skipped so one corrupt line never breaks
    the admin UI.
    """
    events: list[dict] = []
    path = Path(AUDIT_PATH)
    paths = [path] + [Path(f"{path}.{suffix}")
                      for suffix in range(1, AUDIT_MAX_FILES)]
    for candidate in paths:
        if not candidate.exists():
            continue
        try:
            with candidate.open("r", encoding="utf-8") as fh:
                lines = fh.readlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if tool and ev.get("tool") != tool:
                    continue
                if namespace and ev.get("namespace") != namespace:
                    continue
                events.append(ev)
                if len(events) >= limit:
                    return events
        except (OSError, UnicodeError) as exc:
            logger.error("audit read failed for %s: %s", candidate, exc)
    return events


class AuditJournal:
    """One runtime's audit file and rotation state, independent of other runtimes."""

    def __init__(self, environment=None):
        env = os.environ if environment is None else environment
        self.path = Path(env.get("MEMORY_AUDIT_PATH", "data/audit.jsonl"))
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
        if self.path.stat().st_size:
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
                event = {
                    "ts": _now_utc_iso(), "tool": tool,
                    "status": "error" if error else "ok",
                    "duration_ms": round(duration_ms, 1),
                    "namespace": (arguments or {}).get("namespace"),
                    "search_id": ((result or {}).get("search_id") if isinstance(result, dict)
                                  else None) or (arguments or {}).get("search_id"),
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
                with candidate.open("r", encoding="utf-8") as handle:
                    lines = handle.readlines()
                for line in reversed(lines):
                    try:
                        event = json.loads(line)
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
