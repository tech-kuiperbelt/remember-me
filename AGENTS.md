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
| `MEMORY_AUDIT_PATH` | `data/audit.jsonl` | Append-only LLM tool-call audit journal |
| `MEMORY_AUDIT_MAX_EVENTS` | `10000` | Max events before the audit journal stops recording |

Ollama **must be running locally** for embeddings to work. If missing, add/search/update will fail.

## Key architecture facts

- **`app/server.py`** — MCP tool/resource registration. 7 tools: `add`, `search_semantic`, `search_meta`, `get_details`, `update`, `delete`, `stats`. 2 resources: `memories://stats`, `memories://list`. All tool calls flow through `handle_call_tool` (if/elif dispatch) which records each invocation to the audit journal.
- **`app/audit.py`** — LLM tool-call audit journal: append-only JSONL (`data/audit.jsonl`), thread-safe, rotation at `MEMORY_AUDIT_MAX_EVENTS`, never raises, never blocks tool calls. Per-tool params/result trimming lives here (`_trim_params` / `_trim_result`).
- **`app/service.py`** — Business layer: `memory_add/search/list/update/delete/stats/get_details`, storage bootstrap, summary worker wiring.
- **`storage.py`** — Abstract `MemoryStorage` with `SQLiteStorage` (default) and `JSONStorage`. Embeddings are pickled into SQLite BLOBs.
- **`embed.py`** — Single function: calls Ollama `/api/embeddings` with `{"model": …, "prompt": …}`.
- Embeddings use **cosine similarity** (via numpy) for search scoring.
- **`app/admin.py`** — Web UI (admin console) routes under `/admin/console` and `/api/*` (incl. `/api/audit`). Frontend is a single static file `web/index.html`.

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
- **Whenever adding or modifying an MCP tool, audit must be considered.** Checklist: ① register in `handle_list_tools`; ② add the dispatch branch in `handle_call_tool`; ③ add a `_TOOL_HINTS` entry; ④ wire audit (it flows automatically through the shared `audit.record` in `handle_call_tool`, but per-tool `params`/`result` trimming must be added in `app/audit.py` `_trim_params`/`_trim_result`); ⑤ keep tool description and hint consistent. Do not add a tool that is invisible to the audit journal.

## Memory usage protocol

Follow the global memory protocol rules in `~/.config/opencode/AGENTS.md`.

This project's namespace is `remember-me`. Set `MEMORY_DEFAULT_NAMESPACE=remember-me` when launching the MCP server so all agent-written memories default to this namespace.
