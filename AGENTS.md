# remember-me — Memory MCP Server

## Entry point & execution

```bash
cd /path/to/remember-me
source .venv/bin/activate
python server.py              # stdio mode (default)
python server.py --port 1314  # SSE mode (HTTP service)
```

Two transport modes, distinguished by the `--port` flag:

| Mode | Command | Description |
|------|---------|-------------|
| **stdio** | `python server.py` | Subprocess stdin/stdout (default, backward compatible) |
| **SSE** | `python server.py --port 1314` | HTTP service via Starlette + Uvicorn, listening on `0.0.0.0:<port>` |

SSE endpoint: `GET /sse`, client POSTs to `/messages/?session_id=...`.

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `MEMORY_STORAGE` | `sqlite` | `sqlite` or `json` |
| `MEMORY_STORAGE_PATH` | `memories.db` / `memories.json` | Path for storage file |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server for embeddings |
| `EMBED_MODEL` | `bge-m3` | Model name passed to Ollama `/api/embeddings` |
| `MEMORY_DEFAULT_NAMESPACE` | `default` | Fallback namespace when `add` tool is called without `namespace`. Set to the project name so all LLM-written memories are scoped per project. |

Ollama **must be running locally** for embeddings to work. If missing, add/search/update will fail.

## Key architecture facts

- **`server.py`** — MCP tool/resource registration. 9 tools: `add`, `search`, `list`, `get`, `update`, `delete`, `clear`, `stats`, `import`. 2 resources: `memories://stats`, `memories://list`.
- **`storage.py`** — Abstract `MemoryStorage` with `SQLiteStorage` (default) and `JSONStorage`. Embeddings are pickled into SQLite BLOBs.
- **`embed.py`** — Single function: calls Ollama `/api/embeddings` with `{"model": …, "prompt": …}`.
- Embeddings use **cosine similarity** (via numpy) for search scoring.

## Running & verification

```bash
source .venv/bin/activate
python -c "
import asyncio, sys
sys.path.insert(0, '.')
from server import server, storage
from embed import embed as embed_fn
e = embed_fn('hello')
eid = storage.add('hello', e)
r = storage.get(eid)
print('OK, memory id:', r['id'])
"
```

## Conventions

- No tests, no linter/formatter/typecheck config. Python stdlib style.
- Pickle is used for embedding serialization in SQLite (not JSON-safe).
- Update merges metadata (shallow merge, new keys overwrite old).
- `clear` without namespace deletes everything. `clear` with namespace deletes only that namespace.
- The docker-compose.yml uses external images (`mem0/openmemory-mcp`, `mem0/openmemory-ui`) — unrelated to this local code.

## Memory usage protocol

Follow the global memory protocol rules in `~/.config/opencode/AGENTS.md`.

This project's namespace is `remember-me`. Set `MEMORY_DEFAULT_NAMESPACE=remember-me` when launching the MCP server so all agent-written memories default to this namespace.
