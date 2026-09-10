"""Tool-call and background-work audit — append-only event journal.

Records every MCP tool invocation plus summary fallback outcomes (time, tool,
status, duration, namespace, params summary, result summary) to a JSONL file
for human review in the admin UI. The audit stream is deliberately *not*
exposed to the LLM: it lives in the platform layer.

Guarantees:

- Never raises: audit failures must not affect the tool call path.
- Thread-safe: appends are protected by a lock (summary worker is a thread).
- Bounded: files rotate by size (or an optional per-file event count), and
  the oldest backup is discarded after the configured file-count limit.
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
AUDIT_MAX_BYTES = int(os.environ.get("MEMORY_AUDIT_MAX_BYTES", str(10 * 1024 * 1024)))
AUDIT_MAX_FILES = max(1, int(os.environ.get("MEMORY_AUDIT_MAX_FILES", "10")))
# Backward-compatible optional per-file limit. Zero disables count rotation.
AUDIT_MAX_EVENTS = max(0, int(os.environ.get("MEMORY_AUDIT_MAX_EVENTS", "0")))

# User-content keys stripped from the recorded params: the journal must not
# duplicate memory bodies (they already live in the primary store).
_TEXT_KEYS = ("text", "summary")

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
                          "keywords": (it.get("metadata") or {}).get("keywords"),
                          "categories": (it.get("metadata") or {}).get("categories")})
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
