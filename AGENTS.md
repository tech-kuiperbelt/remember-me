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
| `MEMORY_AUDIT_PATH` | `data/audit.jsonl` | Active tool-call/background-task audit journal |
| `MEMORY_AUDIT_MAX_BYTES` | `10485760` | Max bytes per audit file before rotation (10MB) |
| `MEMORY_AUDIT_MAX_FILES` | `10` | Total audit files retained, including the active file |
| `MEMORY_AUDIT_MAX_EVENTS` | `0` | Optional per-file event limit; `0` disables count-based rotation |
| `MEMORY_COMPACT_INTERVAL` | `86400` | Minimum seconds between automatic NDJSON compact checks in dual mode |
| `MEMORY_COMPACT_MIN_BYTES` | `1048576` | Compact when at least this many NDJSON bytes are reclaimable |
| `MEMORY_COMPACT_MIN_RATIO` | `0.25` | Compact when reclaimable NDJSON bytes reach this share of the file |

Ollama **must be running locally** for embeddings to work. If missing, add/search/update will fail.

## Key architecture facts

- **`app/server.py`** — MCP tool/resource registration. 8 tools: `add`, `search_semantic`, `search_meta`, `get_details`, `update`, `delete`, `stats`, `report`. 2 resources: `memories://stats`, `memories://list`. All tool calls flow through `handle_call_tool` (if/elif dispatch) which records each invocation to the audit journal. `search_semantic`/`search_meta` responses carry a per-call `search_id` that the `report` tool uses to attach feedback on search result helpfulness.
- **`app/audit.py`** — Tool-call/background-task audit journal: rolling JSONL (`data/audit.jsonl` plus `.1`…`.9` by default), thread-safe, 10MB per file / 10 files by default, never raises, never blocks tool calls. Per-tool params/result trimming lives here (`_trim_params` / `_trim_result`).
- In dual mode, the NDJSON replica receives append-only incremental writes. A background compact check runs at most once per 24 hours (or on the next startup after an overdue interval), atomically removes obsolete lines/tombstones only when the configured reclaimable-space threshold is met, and reports its last result in admin stats/audit. Full Sync always produces a clean snapshot immediately.
- **`app/service.py`** — Business layer: `memory_add/search/list/update/delete/stats/get_details`, storage bootstrap, summary worker wiring.
- **`storage.py`** — Abstract `MemoryStorage` with `SQLiteStorage` (default) and `JSONStorage`. Embeddings are pickled into SQLite BLOBs; the NDJSON disaster-recovery replica deliberately stores no embeddings.
- **`embed.py`** — Single function: calls Ollama `/api/embeddings` with `{"model": …, "prompt": …}`.
- Embeddings use **cosine similarity** (via numpy) for search scoring.
- **`app/admin.py`** — Web UI (admin console) routes under `/admin/console` and `/api/*` (incl. `/api/audit`). Frontend is a single static file `web/index.html`. JSONL→SQLite recovery is intentionally offline-only: stop the service, then run `python -m app.recover_cli data/memories.jsonl data/memories.db`; it regenerates embeddings using the currently configured model and atomically replaces the target DB only on success.

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
