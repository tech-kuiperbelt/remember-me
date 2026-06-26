from typing import Optional
import requests

OLLAMA_BASE_URL = "http://localhost:11434"
EMBED_MODEL = "bge-m3"

def embed(text: str, base_url: Optional[str] = None, model: Optional[str] = None) -> list[float]:
    url = f"{(base_url or OLLAMA_BASE_URL).rstrip('/')}/api/embeddings"
    r = requests.post(url, json={"model": model or EMBED_MODEL, "prompt": text})
    r.raise_for_status()
    return r.json()["embedding"]
