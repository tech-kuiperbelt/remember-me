from typing import Optional
import requests

OLLAMA_BASE_URL = "http://localhost:11434"
EMBED_MODEL = "bge-m3"


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


def embed(text: str, base_url: Optional[str] = None, model: Optional[str] = None) -> list[float]:
    url = f"{(base_url or OLLAMA_BASE_URL).rstrip('/')}/api/embeddings"
    r = requests.post(url, json={"model": model or EMBED_MODEL, "prompt": text},
                      proxies=_bypass_proxy(url))
    r.raise_for_status()
    return r.json()["embedding"]
