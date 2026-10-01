"""Strict JSONL transfer helpers shared by import, export, and recovery."""

import hashlib
import json
from datetime import datetime
from pathlib import Path


RECORD_FIELDS = ("id", "text", "metadata", "namespace", "created_at", "updated_at", "summary")


def _timestamp(value, field, line_no):
    if not isinstance(value, str):
        raise ValueError(f"line {line_no}: {field} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"line {line_no}: invalid {field}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"line {line_no}: {field} must include a timezone")
    return parsed


def _parse_item(item, line_no):
    if not isinstance(item, dict):
        raise ValueError(f"line {line_no}: record must be an object")
    memory_id = item.get("id")
    if not isinstance(memory_id, str) or not memory_id.strip():
        raise ValueError(f"line {line_no}: id must be a non-empty string")
    memory_id = memory_id.strip()
    if item.get("deleted") is True:
        updated = _timestamp(item.get("updated_at"), "updated_at", line_no)
        return memory_id, updated, None
    for field in ("text", "namespace", "created_at", "updated_at"):
        if field not in item:
            raise ValueError(f"line {line_no}: missing {field}")
    if not isinstance(item["text"], str) or not item["text"].strip():
        raise ValueError(f"line {line_no}: text must be a non-empty string")
    if not isinstance(item["namespace"], str) or not item["namespace"].strip():
        raise ValueError(f"line {line_no}: namespace must be a non-empty string")
    _timestamp(item["created_at"], "created_at", line_no)
    updated = _timestamp(item["updated_at"], "updated_at", line_no)
    metadata = item.get("metadata")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError(f"line {line_no}: metadata must be an object")
    summary = item.get("summary")
    if summary is not None and not isinstance(summary, str):
        raise ValueError(f"line {line_no}: summary must be a string or null")
    clean = {
        "id": memory_id, "text": item["text"], "metadata": metadata,
        "namespace": item["namespace"].strip(),
        "created_at": item["created_at"], "updated_at": item["updated_at"],
        "summary": summary,
    }
    return memory_id, updated, clean


def _read_jsonl(path, strict):
    """Parse events, optionally collecting malformed-line diagnostics."""
    states = {}
    lines = 0
    corrupt_lines = 0
    errors = []
    with open(path, "r", encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, 1):
            if not raw.strip():
                continue
            lines += 1
            try:
                item = json.loads(raw)
            except json.JSONDecodeError as exc:
                error = ValueError(f"line {line_no}: invalid JSON")
                if strict:
                    raise error from exc
                corrupt_lines += 1
                errors.append(str(error))
                continue
            try:
                memory_id, updated, clean = _parse_item(item, line_no)
            except ValueError as exc:
                if strict:
                    raise
                corrupt_lines += 1
                errors.append(str(exc))
                continue
            marker = (updated, line_no)
            if memory_id not in states or marker >= states[memory_id][0]:
                states[memory_id] = (marker, clean)
    records = [state[1] for state in states.values() if state[1] is not None]
    return {"records": records, "lines": lines, "corrupt_lines": corrupt_lines,
            "errors": errors[:10]}


def load_jsonl(path, max_records=None):
    """Strictly parse an append-only memory JSONL file into its live snapshot."""
    result = _read_jsonl(path, strict=True)
    if max_records is not None and len(result["records"]) > max_records:
        raise ValueError(f"JSONL contains more than {max_records} live records")
    return {"records": result["records"], "lines": result["lines"]}


def inspect_jsonl(path):
    """Return a tolerant on-disk integrity view for health reporting."""
    return _read_jsonl(path, strict=False)


def write_clean_jsonl(records, path):
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            clean = {field: record.get(field) for field in RECORD_FIELDS}
            clean["metadata"] = clean.get("metadata") or {}
            handle.write(json.dumps(clean, ensure_ascii=False, separators=(",", ":")) + "\n")
    verified = load_jsonl(path)
    expected_ids = [record.get("id") for record in records]
    actual_ids = [record["id"] for record in verified["records"]]
    if len(actual_ids) != len(expected_ids) or set(actual_ids) != set(expected_ids):
        raise RuntimeError("clean JSONL verification failed: record IDs are not unique")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_filename_part(value):
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in value.strip())
    return cleaned.strip("-.")[:80] or "namespace"


def unlink_quietly(path):
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass
