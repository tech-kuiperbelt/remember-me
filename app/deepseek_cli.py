"""Shared helpers for the offline DeepSeek backfill CLIs.

The keyword, category and anchor backfills are separate entry points but share
the same storage assembly, ``opencode run`` invocation, ANSI/JSON output
handling and resource cleanup. Those live here so a new dimension only needs a
prompt and its candidate logic. Per-CLI function facades (``build_storage``,
``format_candidates_block``, ``extract_*``) stay in place and delegate here.

Path resolution reuses the offline entry points' precedence: an explicit
``--db``/``MEMORY_STORAGE_PATH`` wins, and the last-resort default is the
per-user data directory computed purely (no directory or config file is
created).
"""

from __future__ import annotations

import json
import os
import re
import subprocess

from .configuration import PATH_DEFAULTS, resolve_data_dir, rooted_path
from .storage import create_storage, resolve_replica_path

# Default model id as configured in ~/.config/opencode/opencode.jsonc
DEFAULT_MODEL = "opencode-go/deepseek-v4-flash"
DEFAULT_CLI = "opencode"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


def strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", (text or "").strip())


def run_opencode(prompt: str, *, instruction: str, model: str = DEFAULT_MODEL,
                 cli: str = DEFAULT_CLI, timeout: float = 120.0) -> str:
    """Run one ``opencode run`` and return stdout, raising on CLI failure."""
    proc = subprocess.run(
        [cli, "run", "--model", model, instruction],
        input=prompt, capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"opencode exited {proc.returncode}: "
                           f"{proc.stderr.strip()[:200]}")
    return proc.stdout


def extract_json_array(output: str, *, strings_only: bool = False):
    """Parse the first JSON array found in ``output``.

    Tolerates ANSI codes, markdown fences and surrounding prose. With
    ``strings_only`` the result keeps scalars only (keyword/category); otherwise
    raw items (including objects) are returned (anchor).
    """
    text = strip_fences(strip_ansi(output))
    parsed = None
    try:
        candidate = json.loads(text)
        if isinstance(candidate, list):
            parsed = candidate
    except json.JSONDecodeError:
        pass
    if parsed is None:
        # Preserve each baseline parser's pattern: keyword/category used the
        # non-greedy slice; anchor used the greedy one, which is required for a
        # multi-object array wrapped in prose (the non-greedy slice would match
        # an inner array instead).
        patterns = (r"\[.*?\]",) if strings_only else (r"\[.*\]",)
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.DOTALL):
                try:
                    candidate = json.loads(match.group(0))
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, list):
                    parsed = candidate
                    break
            if parsed is not None:
                break
    if parsed is None:
        return None
    if strings_only:
        return [str(x) for x in parsed if not isinstance(x, (dict, list))]
    return parsed


def format_candidates_block(values: list[str] | None, *, label: str) -> str:
    """Render a delimited candidate list; delimiters keep the text unambiguous."""
    if not values:
        return "(empty candidate list)"
    body = "\n".join(f"- {value}" for value in values)
    return f"---- candidate {label} ----\n{body}\n---- end candidates ----"


def stop_storage(storage) -> None:
    """Release storage resources if the object owns a ``stop`` method."""
    stop = getattr(storage, "stop", None)
    if callable(stop):
        stop()


def default_primary_path() -> str:
    """Last-resort primary path under the resolved data root (pure computation).

    Honours an explicit ``MEMORY_STORAGE_PATH`` first; otherwise it roots the
    default primary under ``MEMORY_DATA_DIR`` (relative values resolve against
    PROJECT_ROOT) or the per-user data directory, without creating anything.
    """
    configured = os.environ.get("MEMORY_STORAGE_PATH")
    if configured:
        return configured
    return str(rooted_path(resolve_data_dir(), PATH_DEFAULTS["MEMORY_STORAGE_PATH"]))


def build_cli_storage(db_path=None, read_only: bool = False, *, role: str,
                      create_storage=create_storage,
                      resolve_replica_path=resolve_replica_path):
    """Resolve the offline backend exactly like the service bootstrap.

    ``db_path`` (CLI ``--db``) overrides the primary path; the backend comes from
    ``MEMORY_STORAGE``. A read-only/dry run deliberately bypasses dual mode, so
    it claims no writer lock and starts no replica worker.
    """
    if read_only:
        return create_storage("sqlite", path=db_path or default_primary_path(),
                              read_only=True)

    backend = os.environ.get("MEMORY_STORAGE", "sqlite")
    if backend == "dual":
        primary = db_path or default_primary_path()
        secondary = resolve_replica_path(
            primary, os.environ.get("MEMORY_REPLICA_PATH"),
            os.environ.get("MEMORY_STORAGE_PATH_DUAL"),
        )
        return create_storage("dual", primary_path=primary, secondary_path=secondary,
                              sync_interval=int(os.environ.get("MEMORY_SYNC_INTERVAL", "300")))
    if backend != "sqlite":
        raise RuntimeError(f"{role} migration supports only MEMORY_STORAGE=sqlite or dual")
    return create_storage("sqlite", path=db_path or default_primary_path())
