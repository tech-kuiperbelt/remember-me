"""Summary generation — independent module, mirrors app/embed.py.

Future implementations (FastEmbed, local LLM, remote API) can add
alternative providers behind this single function interface.
"""

from typing import Optional
import requests

OLLAMA_BASE_URL = "http://localhost:11434"
SUMMARIZE_MODEL = "qwen3:latest"

# Prompt instructs the model to keep key identifiers and business times
# while staying concise. Short texts may produce equally short summaries.
_SUMMARY_PROMPT = (
    "You are a precise text summarizer. Summarize the following text concisely.\n"
    "Rules:\n"
    "- Keep all technical terms, ticket numbers, and business event times.\n"
    "- Typically 30-50 Chinese characters; complex content may reach 80-100.\n"
    "- If the text is short, produce an equally short summary without padding.\n"
    "- Respond ONLY with the summary text, no preamble or quotes.\n\n"
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


def summarize(text: str, base_url: Optional[str] = None,
              model: Optional[str] = None) -> str:
    """Generate a concise summary of ``text`` via Ollama.

    Raises on HTTP/parsing errors — callers decide how to handle failure.
    """
    url = f"{(base_url or OLLAMA_BASE_URL).rstrip('/')}/api/generate"
    r = requests.post(url, json={
        "model": model or SUMMARIZE_MODEL,
        "prompt": _SUMMARY_PROMPT.format(text=text),
        "stream": False,
        # qwen3 defaults to thinking mode which wastes tokens/time on
        # reasoning; summary generation doesn't need it (~2.4x faster).
        "think": False,
    }, proxies=_bypass_proxy(url))
    r.raise_for_status()
    return r.json().get("response", "").strip()
