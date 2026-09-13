# remember-me — Memory MCP Server

## Entry point & execution

```bash
cd /path/to/remember-me
source .venv/bin/activate
python -m app.server              # stdio mode (default)
python -m app.server --port 1314  # SSE mode (HTTP service)
```

`bin/start.sh` is the recommended launcher: it sets `MEMORY_STORAGE=dual` and
sources the gitignored, host-specific `.env` (embedding provider and model
cache) if present. The configured provider must match the active embedding
profile in `data/memories.db`, otherwise startup fails and requires an offline
`app.embedding_cli reindex`/`activate`.

Two transport modes, distinguished by the `--port` flag:

| Mode | Command | Description |
|------|---------|-------------|
| **stdio** | `python -m app.server` | Subprocess stdin/stdout (default, backward compatible) |
| **SSE** | `python -m app.server --port 1314` | HTTP service via Starlette + Uvicorn, listening on `0.0.0.0:<port>` |

SSE endpoint: `GET /sse`, client POSTs to `/messages/?session_id=...`.

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `MEMORY_STORAGE` | `sqlite` | `sqlite` or `json` |
| `MEMORY_STORAGE_PATH` | `memories.db` / `memories.json` | Path for storage file |
| `EMBED_PROVIDER` | `ollama` | `ollama`, optional local `fastembed`, or `sentence_transformers` |
| `EMBED_MODEL` | `bge-m3` | Ollama embedding model |
| `EMBED_DIM` | `1024` | Expected Ollama vector dimension; mismatch fails explicitly |
| `EMBED_DTYPE` | `float16` | SQLite vector codec dtype: `float16` or `float32` |
| `EMBED_PRELOAD` | `true` | Warm the embedding provider before serving requests |
| `EMBED_MAX_INPUT_CHARS` | `8000` | Conservative input guard; oversized text is rejected, never truncated silently |
| `EMBED_TIMEOUT` | `120` | Ollama request timeout in seconds |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server used in `ollama` mode |
| `FASTEMBED_MODEL` | `intfloat/multilingual-e5-large` | FastEmbed model; must occur in the installed runtime's supported list |
| `FASTEMBED_CACHE_DIR` | FastEmbed default | Model cache or copied offline model cache |
| `FASTEMBED_OFFLINE` | `false` | Disable model downloads and require a populated cache |
| `FASTEMBED_MAX_INPUT_TOKENS` | `512` | Token guard for the default FastEmbed model |
| `SENTENCE_TRANSFORMERS_MODEL` | `BAAI/bge-m3` | Python/PyTorch in-process model |
| `SENTENCE_TRANSFORMERS_CACHE_DIR` | library default | Model cache directory |
| `SENTENCE_TRANSFORMERS_OFFLINE` | `false` | Require the model to exist in the local cache |
| `SENTENCE_TRANSFORMERS_DEVICE` | auto | Optional `cpu`, `cuda`, or other PyTorch device |
| `SENTENCE_TRANSFORMERS_MAX_INPUT_TOKENS` | `8192` | Explicit token guard; inputs are never silently truncated |
| `SENTENCE_TRANSFORMERS_BATCH_SIZE` | `8` | Reindex batch inference size |
| `MEMORY_DEFAULT_NAMESPACE` | `default` | Fallback namespace when `add` tool is called without `namespace`. Set to the project name so all LLM-written memories are scoped per project. |
| `MEMORY_AUDIT_PATH` | `data/audit.jsonl` | Active tool-call/background-task audit journal |
| `MEMORY_AUDIT_MAX_BYTES` | `10485760` | Max bytes per audit file before rotation (10MB) |
| `MEMORY_AUDIT_MAX_FILES` | `10` | Total audit files retained, including the active file |
| `MEMORY_AUDIT_MAX_EVENTS` | `0` | Optional per-file event limit; `0` disables count-based rotation |
| `MEMORY_COMPACT_INTERVAL` | `86400` | Minimum seconds between automatic NDJSON compact checks in dual mode |
| `MEMORY_COMPACT_MIN_BYTES` | `1048576` | Compact when at least this many NDJSON bytes are reclaimable |
| `MEMORY_COMPACT_MIN_RATIO` | `0.25` | Compact when reclaimable NDJSON bytes reach this share of the file |

Ollama **must be running locally** in `EMBED_PROVIDER=ollama` mode. FastEmbed and Sentence Transformers are optional dependencies installed with `requirements-fastembed.txt` and `requirements-sentence-transformers.txt`; production must be reindexed before switching providers.

## Key architecture facts

- **`app/server.py`** — MCP tool/resource registration. 8 tools: `add`, `search_semantic`, `search_meta`, `get_details`, `update`, `delete`, `stats`, `report`. 2 resources: `memories://stats`, `memories://list`. All tool calls flow through `handle_call_tool` (if/elif dispatch) which records each invocation to the audit journal. `search_semantic`/`search_meta` responses carry a per-call `search_id` that the `report` tool uses to attach feedback on search result helpfulness. `search_semantic` accepts both `keywords` and `categories` as exact candidate pre-filters before cosine scoring: OR within each dimension, AND across dimensions/namespace.
- **`app/audit.py`** — Tool-call/background-task audit journal: rolling JSONL (`data/audit.jsonl` plus `.1`…`.9` by default), thread-safe, 10MB per file / 10 files by default, never raises, never blocks tool calls. Per-tool params/result trimming lives here (`_trim_params` / `_trim_result`).
- In dual mode, the NDJSON replica receives append-only incremental writes. A background compact check runs at most once per 24 hours (or on the next startup after an overdue interval), atomically removes obsolete lines/tombstones only when the configured reclaimable-space threshold is met, and reports its last result in admin stats/audit. Full Sync always produces a clean snapshot immediately.
- **`app/service.py`** — Business layer: `memory_add/search/list/update/delete/stats/get_details`, storage bootstrap, summary worker wiring.
- **`app/categories.py`** — Category dimension (`metadata.categories`, max 3 per memory): 20-seed canonical vocabulary (`CATEGORY_VOCABULARY`), normalization/cap helpers, and `category_counts_with_seeds` (stats always lists the seed vocabulary, count 0 when unused). Categories are a separate dimension from keywords — keywords = content concepts, categories = memory nature/classification. SQLite stores both dimensions in generic `index_values(dimension, value)` / `memory_index_values(memory_id, index_value_id)` tables and manages them through shared sync/rebuild/count/filter paths; startup migrates and drops the legacy per-dimension tables by rebuilding from authoritative `memories.metadata`. NDJSON carries metadata for recovery.
- **`app/category_migrate_deepseek.py`** — Offline backfill CLI for `metadata.categories` on existing memories (deepseek-v4-flash via `opencode run`, strictly serial, per-record resilience, `--dry-run` supported). No local-Ollama/qwen path by design.
- **`app/storage_sqlite.py`** — SQLite primary plus versioned embedding profiles. Dense vectors live in `memory_embeddings(memory_id, profile_id, embedding)`; the first startup migrates legacy inline pickle vectors into the active profile. New BLOBs use the portable `RMEV` codec and float16 by default. Reindex builds a shadow profile and switches it atomically; retained profiles support rollback. The NDJSON disaster-recovery replica deliberately stores no embeddings.
- **`app/embedding_codec.py`** — Versioned float16/float32 dense-vector BLOB codec with read compatibility for trusted legacy pickle data.
- **`app/embed.py`** — Provider abstraction with distinct document/query paths. Ollama remains the default; FastEmbed 0.8 is optional and performs local ONNX inference, cache/offline validation, batching, normalization, dimension checks and input-length checks.
- **`app/embedding_cli.py`** — Offline embedding administration: `status`, `migrate-codec`, `download-model`, `reindex`, and `activate`. Stop the service before mutating the production database. Reindex is intentionally not an MCP tool.
- Embeddings use **cosine similarity** (via numpy) for search scoring.
- **`app/admin.py`** — Web UI (admin console) routes under `/admin/console` and `/api/*` (incl. `/api/audit`). Frontend is a single static file `web/index.html`. JSONL→SQLite recovery is intentionally offline-only: stop the service, then run `python -m app.recover_cli data/memories.jsonl data/memories.db`; it regenerates embeddings using the currently configured model and atomically replaces the target DB only on success.

## Running & verification

```bash
source .venv/bin/activate
python -c "
import asyncio, sys
sys.path.insert(0, '.')
from app.service import storage
from app.embed import embed as embed_fn
e = embed_fn('hello')
result = storage.add('hello', e)
r = storage.get(result['id'])
print('OK, memory id:', r['id'])
"
```

## Conventions

- No tests, no linter/formatter/typecheck config. Python stdlib style.
- New SQLite embeddings use the versioned RMEV float16 codec. Legacy pickle BLOBs remain read-compatible and are migrated transactionally into the first active profile.
- Update merges metadata (shallow merge, new keys overwrite old).
- `clear` without namespace deletes everything. `clear` with namespace deletes only that namespace.
- The docker-compose.yml uses external images (`mem0/openmemory-mcp`, `mem0/openmemory-ui`) — unrelated to this local code.
- **Whenever adding or modifying an MCP tool, audit must be considered.** Checklist: ① register in `handle_list_tools`; ② add the dispatch branch in `handle_call_tool`; ③ add a `_TOOL_HINTS` entry; ④ wire audit (it flows automatically through the shared `audit.record` in `handle_call_tool`, but per-tool `params`/`result` trimming must be added in `app/audit.py` `_trim_params`/`_trim_result`); ⑤ keep tool description and hint consistent. Do not add a tool that is invisible to the audit journal.

## Memory usage protocol

Follow the global memory protocol rules in `~/.config/opencode/AGENTS.md`.

This project's namespace is `remember-me`. Set `MEMORY_DEFAULT_NAMESPACE=remember-me` when launching the MCP server so all agent-written memories default to this namespace.

## Project management documents

The authoritative project backlog is kept in the repository, not in a long memory record:

- `docs/project-management/backlog-todo.md` — open and ignored work.
- `docs/project-management/backlog-done.md` — completed work archive.

Memory records should contain only a short pointer to these files and a compact status, so updates do not exceed the embedding model context window.
