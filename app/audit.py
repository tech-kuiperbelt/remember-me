"""Tool-call and background-work audit — append-only event journal.

Records every MCP tool invocation plus summary fallback outcomes (time, tool,
status, duration, namespace, params summary, result summary) to a JSONL file
for human review in the admin UI. The audit stream is deliberately *not*
exposed to the LLM: it lives in the platform layer.

Guarantees:

- Never raises: audit failures must not affect the tool call path.
- Thread-safe: appends are protected by a lock (summary worker is a thread).
- Bounded: after ``AUDIT_MAX_EVENTS`` records the journal stops writing and
  logs a single warning — it never truncates or deletes historical data.
- Independent of the memory stores: the audit journal is separate from the
  SQLite primary / JSONL replica used for memories.
"""

import os
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("audit")

AUDIT_PATH = os.environ.get("MEMORY_AUDIT_PATH", "data/audit.jsonl")
AUDIT_MAX_EVENTS = int(os.environ.get("MEMORY_AUDIT_MAX_EVENTS", "10000"))

# User-content keys stripped from the recorded params: the journal must not
# duplicate memory bodies (they already live in the primary store).
_TEXT_KEYS = ("text", "summary")

_lock = threading.Lock()
_file = None
_count = 0
_stopped = False
_warned = False


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


def _trim_params(tool: str, arguments: dict | None, result=None) -> dict:
    """Per-tool params record — drop user-content fields, keep metadata."""
    args = dict(arguments or {})
    if tool == "add":
        summary = args.get("summary")
        if isinstance(result, dict) and result.get("created") is False:
            args["summary_mode"] = "duplicate"
        elif isinstance(summary, str) and summary.strip():
            args["summary_mode"] = "input"
        else:
            args["summary_mode"] = "fallback"
    elif tool == "update":
        summary = args.get("summary")
        if isinstance(summary, str) and summary.strip():
            args["summary_mode"] = "input"
        elif args.get("text") is not None:
            args["summary_mode"] = "fallback"
        else:
            args["summary_mode"] = "preserved"
    for key in _TEXT_KEYS:
        args.pop(key, None)
    return args


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
        return {
            "search_id": result.get("search_id"),
            "total": result.get("total", len(items)),
            "items": [
                {"id": it.get("id"),
                 "summary": it.get("summary"),
                 **({"score": it.get("score")} if keep_score else {})}
                for it in items
            ],
        }

    if tool == "get_details":
        found = []
        for it in result.get("found") or []:
            found.append({"id": it.get("id"), "summary": it.get("summary"),
                          "keywords": (it.get("metadata") or {}).get("keywords")})
        return {"found": found, "missing": result.get("missing", [])}

    if tool == "stats":
        # Drop the keyword vocabulary (hundreds of entries would bloat the
        # journal on every stats call) and provider URLs (config, not stats).
        return {k: v for k, v in result.items()
                if k not in ("keyword_counts", "ollama_base_url")}

    return dict(result)


def record(tool: str, arguments: dict | None, result, duration_ms: float,
           error: dict | None = None) -> None:
    """Append one audit event. Never raises.

    ``error`` is ``{"type": ..., "message": ...}`` for failed calls, else None.
    """
    global _count, _stopped, _warned
    try:
        with _lock:
            if _stopped:
                return
            _open_file()
            if _count >= AUDIT_MAX_EVENTS:
                _stopped = True
                if not _warned:
                    _warned = True
                    logger.warning("audit journal stopped: %d events (max %d). "
                                   "Archive %s to resume recording.",
                                   _count, AUDIT_MAX_EVENTS, AUDIT_PATH)
                return
            event = {
                "ts": _now_utc_iso(),
                "tool": tool,
                "status": "error" if error else "ok",
                "duration_ms": round(duration_ms, 1),
                "namespace": (arguments or {}).get("namespace"),
                "error": error,
                "params": _trim_params(tool, arguments, result),
                "result": _trim_result(tool, result),
            }
            _file.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
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
    path = Path(AUDIT_PATH)
    if not path.exists():
        return []
    events: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
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
    except OSError as e:
        logger.error("audit read failed: %s", e)
        return []
    events.reverse()
    return events[:limit]
