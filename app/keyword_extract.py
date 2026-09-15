"""Keyword extraction using the configured local Ollama endpoint.

Uses the local Ollama LLM (qwen family) to extract content keywords from a
memory. Returns a normalized list of lowercase strings. The model output is
forced to JSON via Ollama's ``format`` option so parsing is stable.

This module is used by the migration entry point (service.memory_extract_keywords)
that backfills metadata.keywords for existing records.
"""

from typing import Optional
import json
import requests

OLLAMA_BASE_URL = "http://localhost:11434"
KEYWORD_MODEL = "qwen3:latest"

# Prompt instructs the model to return ONLY a JSON array of strings.
# Keywords are content concepts — not category, type, or classification.
_KEYWORD_PROMPT = (
    "You are a precise keyword extractor for Chinese/English technical text.\n"
    "Rules:\n"
    "- Extract 3-8 keywords that capture the concrete concepts, entities, and "
    "topics in the text.\n"
    "- Use lowercase; prefer stable canonical forms (e.g. 'rtg' not 'RTG').\n"
    "- Do NOT use category-like or type-like labels (e.g. 'incident', "
    "'recap', 'bug' alone, 'task').\n"
    "- Keep technical terms, ticket numbers, and business entities.\n"
    "- Respond ONLY with a JSON array of strings, e.g. [\"keyword1\", "
    "\"keyword2\"]. No preamble, no quotes around the array.\n\n"
    "Text:\n{text}"
)


def _bypass_proxy(url: str) -> dict | None:
    """Return proxies dict disabling the env proxy for local Ollama hosts.

    Shell environments often export http_proxy/https_proxy; the requests
    library applies them to every request, including localhost. Ollama is a
    local service, so never route it through a proxy. Remote base URLs still
    honor the environment proxy.
    """
    host = url.split("://", 1)[-1].split("/", 1)[0]
    if host in ("localhost", "127.0.0.1", "::1", "[::1]"):
        return {"http": None, "https": None}
    return None


def extract_keywords(text: str, base_url: Optional[str] = None,
                     model: Optional[str] = None) -> list[str]:
    """Extract keywords from ``text`` via Ollama.

    Returns a list of normalized (strip/lower) keyword strings.
    Raises on HTTP/parsing errors — callers decide how to handle failure.
    """
    url = f"{(base_url or OLLAMA_BASE_URL).rstrip('/')}/api/generate"
    r = requests.post(url, json={
        "model": model or KEYWORD_MODEL,
        "prompt": _KEYWORD_PROMPT.format(text=text),
        "stream": False,
        "format": "json",
        # qwen3 defaults to thinking mode; keyword extraction doesn't need it.
        "think": False,
    }, proxies=_bypass_proxy(url))
    r.raise_for_status()
    raw = r.json().get("response", "").strip()
    parsed = json.loads(raw)
    # qwen3 sometimes emits a JSON object (e.g. {"rtg incident":"inc-2026"})
    # despite format="json"; treat keys and values as candidate keywords so
    # the extraction degrades gracefully instead of failing outright.
    if isinstance(parsed, dict):
        candidates = []
        for k, v in parsed.items():
            candidates.append(str(k))
            if isinstance(v, (str, int, float)):
                candidates.append(str(v))
    elif isinstance(parsed, list):
        candidates = [str(item) for item in parsed if not isinstance(item, (dict, list))]
    else:
        raise ValueError(f"keyword extractor returned non-array: {raw[:200]}")
    result = []
    seen = set()
    for kw in candidates:
        kw = kw.strip().lower()
        if kw and kw not in seen:
            seen.add(kw)
            result.append(kw)
    return result
