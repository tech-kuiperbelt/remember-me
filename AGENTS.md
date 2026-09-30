# remember-me — Memory MCP Server

## Entry point & execution

```bash
cd /path/to/remember-me
source .venv/bin/activate
python -m app.server              # HTTP mode (default, config port or 1314)
python -m app.server --stdio       # subprocess stdin/stdout
python -m app.server --data-dir /path/to/data  # config is <DIR>/config.json
```

`bin/start.sh` is the recommended launcher: it sets `MEMORY_STORAGE=dual` and
sources the gitignored, host-specific `.env` (embedding provider and model
cache) if present. The configured provider must match the active embedding
profile in `data/memories.db`, otherwise startup fails and requires an offline
`app.embedding_cli reindex`/`activate`.

Data directory selection: `--data-dir` > `MEMORY_DATA_DIR` > project `data`; relative
directories are resolved against the project root. `<data-dir>/config.json` contains
`{"port":1314,"language":"zh-CN"}` and is created only on normal startup. Language
is stored but not yet applied to translations. `--port` temporarily overrides the
configured port; `--stdio` explicitly selects subprocess transport. Runtime paths,
logs, temporary files and local model caches are contained in the selected root.
Path environment variables and CLI output paths are root-relative; external paths
are rejected. Use `bin/manage config migrate --dry-run` then stop the service and
run without `--dry-run` to copy and verify legacy paths, retaining sources. For a
new root: `bin/manage --data-dir NEW config migrate --source-data-dir OLD`. A pending
migration journal blocks startup; rerun migration to recover before proceeding.
See `docs/project-management/plans/43-config-and-data-root.md`.

Two transport modes:

| Mode | Command | Description |
|------|---------|-------------|
| **stdio** | `python -m app.server --stdio` | Subprocess stdin/stdout (explicit) |
| **HTTP** | `python -m app.server` | Starlette + Uvicorn on `0.0.0.0:<port>`; serves legacy SSE **and** Streamable HTTP from one instance |

HTTP endpoints (all share the same service/runtime/provider/storage):

| Endpoint | Transport | Notes |
|---|---|---|
| `GET /sse` + `POST /messages/?session_id=...` | legacy HTTP+SSE | retained for existing clients (e.g. opencode); Codex treats it as legacy |
| `POST /mcp` | MCP Streamable HTTP | `stateless=True` + `json_response=True`; no `Mcp-Session-Id`; `GET`/`HEAD`/`DELETE`/other methods return `405` with `Allow: POST` (security is checked first); Host/Origin restricted to loopback (`127.0.0.1`/`localhost`) — illegal Host `421`, illegal Origin `403` |
| `GET /read-me` | static page | LLM-facing config guide; self-references `http://127.0.0.1:<port>` (port follows `--port`); read-only, no auth; `503` while closing |

`/mcp` is the protocol endpoint; `/read-me` only documents how to connect. See `docs/project-management/plans/29-streamable-http-and-read-me.md`.

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `MEMORY_STORAGE` | `sqlite` | `sqlite` or `dual`; all other values fail startup |
| `MEMORY_DATA_DIR` | project `data` | Runtime data root; `--data-dir` takes precedence |
| `MEMORY_STORAGE_PATH` | `memories.db` | SQLite primary path relative to data root |
| `MEMORY_REPLICA_PATH` | derived from primary | NDJSON replica path in dual mode (`.jsonl` or `.ndjson`) |
| `MEMORY_STORAGE_PATH_DUAL` | unset | Deprecated compatibility alias for `MEMORY_REPLICA_PATH` |
| `EMBED_PROVIDER` | `ollama` | `ollama`, optional local `fastembed`, or `sentence_transformers` |
| `EMBED_MODEL` | `bge-m3` | Ollama embedding model |
| `EMBED_DIM` | `1024` | Expected Ollama vector dimension; mismatch fails explicitly |
| `EMBED_DTYPE` | `float16` | SQLite vector codec dtype: `float16` or `float32` |
| `EMBED_PRELOAD` | `true` | Warm the embedding provider before serving requests |
| `MEMORY_INDEX_SEMANTIC_TOP_K` | `3` | Max system index values retained per unmatched keyword/category filter value |
| `MEMORY_INDEX_SEMANTIC_MIN_SCORE` | `0.65` | Minimum cosine score for semantic index-value resolution |
| `MEMORY_INDEX_SEMANTIC_MAX_GAP` | `0.05` | Maximum score gap from the best index-value match |
| `EMBED_MAX_INPUT_CHARS` | `8000` | Conservative input guard; oversized text is rejected, never truncated silently |
| `EMBED_TIMEOUT` | `120` | Ollama request timeout in seconds |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server used in `ollama` mode |
| `FASTEMBED_MODEL` | `intfloat/multilingual-e5-large` | FastEmbed model; must occur in the installed runtime's supported list |
| `FASTEMBED_CACHE_DIR` | `models/fastembed` | Model cache or copied offline model cache |
| `FASTEMBED_OFFLINE` | `false` | Disable model downloads and require a populated cache |
| `FASTEMBED_MAX_INPUT_TOKENS` | `512` | Token guard for the default FastEmbed model |
| `SENTENCE_TRANSFORMERS_MODEL` | `BAAI/bge-m3` | Python/PyTorch in-process model |
| `SENTENCE_TRANSFORMERS_CACHE_DIR` | `models/sentence-transformers` | Model cache directory |
| `SENTENCE_TRANSFORMERS_OFFLINE` | `false` | Require the model to exist in the local cache |
| `SENTENCE_TRANSFORMERS_DEVICE` | auto | Optional `cpu`, `cuda`, or other PyTorch device |
| `SENTENCE_TRANSFORMERS_MAX_INPUT_TOKENS` | `8192` | Explicit token guard; inputs are never silently truncated |
| `SENTENCE_TRANSFORMERS_BATCH_SIZE` | `8` | Reindex batch inference size |
| `MEMORY_DEFAULT_NAMESPACE` | `default` | Fallback namespace when `add` tool is called without `namespace`. Set to the project name so all LLM-written memories are scoped per project. This configured namespace is always treated as known, so a fresh database can be bootstrapped. |
| `MEMORY_AUDIT_PATH` | `audit.jsonl` | Active tool-call/background-task audit journal |
| `MEMORY_AUDIT_MAX_BYTES` | `10485760` | Max bytes per audit file before rotation (10MB) |
| `MEMORY_AUDIT_MAX_FILES` | `10` | Total audit files retained, including the active file |
| `MEMORY_AUDIT_MAX_EVENTS` | `0` | Optional per-file event limit; `0` disables count-based rotation |
| `MEMORY_COMPACT_INTERVAL` | `86400` | Minimum seconds between automatic NDJSON compact checks in dual mode |
| `MEMORY_COMPACT_MIN_BYTES` | `1048576` | Compact when at least this many NDJSON bytes are reclaimable |
| `MEMORY_COMPACT_MIN_RATIO` | `0.25` | Compact when reclaimable NDJSON bytes reach this share of the file |
| `MEMORY_HEALTH_CHECK_INTERVAL` | `86400` | Seconds between automatic exact SQLite/JSONL health checks in dual mode; startup always checks, drift/error retries after 1 hour |
| `MEMORY_IMPORT_MAX_BYTES` | `67108864` | Maximum uploaded JSONL import size (64 MiB) |
| `MEMORY_IMPORT_MAX_RECORDS` | `10000` | Maximum live records accepted from one JSONL import |

Ollama **must be running locally** in `EMBED_PROVIDER=ollama` mode. FastEmbed and Sentence Transformers are optional dependencies installed with `requirements-fastembed.txt` and `requirements-sentence-transformers.txt`; production must be reindexed before switching providers.

## Key architecture facts

- **`app/settings.py` / `app/runtime.py`** — Configuration-backed immutable `Settings.from_config()` snapshot (`from_env` remains a side-effect-free low-level helper) and explicit runtime ownership. `create_runtime(settings)` acquires nothing; `start()` configures logging/audit, creates provider/storage, validates and warms up, then starts dual sync/health/background work. `close()` waits for imports and periodic sync, attempts final outbox sync, then releases stores/locks, provider, audit and owned logging handlers. Startup failure cleans up acquired resources. Imports and `python -m app.server --help` have no runtime side effects. One active runtime/audit configuration per process.
- **`bin/manage` / `README.md`** — Operator entry point for offline embedding/profile maintenance, historical backfills, and CLI JSONL recovery. Read-only status can run while the service is active; independent write CLIs require graceful shutdown. The Ops UI supports online maintenance and uploaded JSONL recovery inside the active runtime.
- **Shutdown/restart** — Ops offers Restart Server and Shut Down Server after entering maintenance mode. `/api/restart` and `/api/shutdown` respond before rejecting new work and asking Uvicorn to exit; active SSE sessions end, HTTP work drains, and imports finish without a timeout. Restart execs only after cleanup; shutdown requires a terminal start. stdio EOF/SIGTERM also clean up; piped stdin uses cancellable asynchronous reads. See `docs/project-management/plans/38-runtime-lifecycle.md`.

- **`app/server.py`** — MCP tool/resource registration. 11 tools: `add`, `search_semantic`, `search_lexical`, `search_anchor`, `search_meta`, `get_details`, `update`, `delete`, `stats`, `report`, `list_keywords`. 2 resources: `memories://stats`, `memories://list`. All tool calls flow through `handle_call_tool` (if/elif dispatch) which records each invocation to the audit journal. `search_semantic`/`search_meta` responses carry a per-call `search_id` that the `report` tool uses to attach feedback on search result helpfulness. `search_semantic` accepts `keywords` and `categories` candidate filters: exact values are preferred, unmatched values are semantically resolved within the same dimension/namespace, then the resolved values filter candidates before memory-text cosine scoring. Values use OR within each dimension and AND across dimensions/namespace; `filter_resolution` exposes and audits the mapping. `search_lexical` is a separate content-only BM25 primitive (literal identifiers, error codes, symbols) whose score is not comparable with cosine; `search_anchor` matches only the anchors declared in `metadata.anchors` (identity signals, not content). `search_semantic`/`search_lexical`/`search_anchor`/`search_meta` each carry a per-call `search_id` consumed by `report`. Strict anchor-validation failures return `response_code="invalid_anchors"`. `search_meta` remains exact-only. Unknown namespaces are rejected: `search_semantic`/`search_meta` return an empty-result envelope with `response_code="namespace_not_found"` and `response_meta.known_namespaces`, while `add`/`update` refuse to write unless the caller passes the matching `confirm_codes` entry (the admin HTTP API does the same and maps it to HTTP 400). The HTTP app also serves legacy SSE (`/sse` + `/messages/`) and stateless Streamable HTTP (`/mcp`), plus the static `/read-me` config page; all transports share one `mcp.server`, runtime, provider and storage.
- **Keyword discovery** — `list_keywords` and `POST /api/keywords/query` share browse/literal/semantic modes with optional namespace. Semantic mode accepts 1–20 concepts and reuses active keyword vectors, returns score/count candidates without exact-first suppression, and does not create a search_id. Public Stats returns `keyword_total`, never `keyword_counts`; complete vocabulary is available through the new query API without low-frequency trimming. The Keywords UI has scoped search, pagination, and links to exact memory filters. Before setting keywords, query semantic candidates in the target namespace and reuse only when meaning fits; new words remain allowed. See `docs/project-management/plans/36-keyword-discovery.md`.
- **`app/audit.py`** — Runtime-owned `AuditJournal` for tool-call/background-task rolling JSONL (`data/audit.jsonl` plus `.1`…`.9` by default), thread-safe, 10MB per file / 10 files by default, never raises, never blocks tool calls. Per-tool params/result trimming lives here (`_trim_params` / `_trim_result`). Events record source/request/session/client context, explicit `search_id`/`source_search_id`/`keyword_query_id` links, known memory namespaces, capped confirmation codes and response codes. IDs are caller-declared associations; validation belongs to [26]. Streamable HTTP tool calls additionally carry `transport=streamable_http` and a per-request `http_request_id`, set through the dedicated `http_transport_context` ContextVar and merged by `MCPApplication._audit_context()`; `request_audit_context` (admin/SSE admission) is unchanged. Offline CLIs use separate journals; legacy module functions remain for direct low-level callers.
- Every writable service or maintenance process obtains a lifetime OS `flock` on the canonical SQLite-primary sidecar `<db>.writer.lock`; a second writer for that primary fails immediately. Read-only diagnostics remain possible. In dual mode, the NDJSON replica additionally has its own one-writer lock per replica path. SQLite outbox rows carry an internal per-memory `version`: each batch is appended and `fsync`ed before only matching `(id, version)` rows are acknowledged, so a concurrent new write remains pending. Compact/Full Sync use durable atomic replacement. Startup first syncs pending outbox rows and then performs an exact SQLite/JSONL health check; scheduled checks run every 24 hours by default, with drift/error retried after one hour and recorded in stats/audit without automatic repair. A background compact check runs at most once per 24 hours, while live Stats always verifies on-disk IDs and corrupt lines instead of treating `pending=0` as sufficient health.
- **`app/service.py`** — Explicit `MemoryService(settings, storage, embedding_provider)` instance; no global storage/provider or import workers. Business layer: memory CRUD/search, storage bootstrap, JSONL exports, and one-at-a-time background JSONL imports. Runtime storage is strictly `sqlite` or `dual`; JSON array storage was removed, and NDJSON cannot run as a business backend. Import preserves/prefixes namespaces, regenerates memory and index-value embeddings with the target active profile, commits atomically, and preserves missing summaries as null. Namespace governance is centralized here (`known_namespaces()` / `check_namespace()`, shared by MCP and the admin API).
- **`app/lexical.py`** — Minimal shared query builder for the lexical (FTS5/BM25) retrieval primitive: the single `LEXICAL_INDEX_VERSION`, query limits (4096 chars / 256 tokens) and safe MATCH construction (every term quoted and OR-combined; zero-token input returns an empty result). Tokenization here only decides which substrings count as searchable tokens for validation/limits; SQLite `unicode61` performs the matching, so CamelCase and CJK are never hand-split.
- **`app/summary_policy.py`** — Model-free summary fallback. Caller summaries win; ordinary add/text-update writes derive a deterministic leading sentence/line when absent, while whitespace-equivalent or metadata-only updates preserve the old summary. Import/recovery retain null summaries, and LLM-facing search/list responses derive the same display fallback dynamically.
- **`app/anchors.py`** — Retrieval anchors (`metadata.anchors`, max 5, order preserved, case preserved). `normalize_anchors` is strict for caller input (NFKC, whitespace collapse, ASCII case-insensitive dedupe, exact non-ASCII dedupe, ≥128-char/empty/non-string/over-5 all rejected rather than dropped) and is called before embedding or storage work; `clean_anchors` is lenient for indexing/rebuild/import. Anchors are identity signals (names, classes, APIs, errors, config keys, versions), never generated by the server.
- **`app/categories.py`** — Category dimension (`metadata.categories`, max 3 per memory): 20-seed canonical vocabulary (`CATEGORY_VOCABULARY`), normalization/cap helpers, and `category_counts_with_seeds` (stats always lists the seed vocabulary, count 0 when unused). Categories are a separate dimension from keywords — keywords = content concepts, categories = memory nature/classification. SQLite stores both dimensions in generic `index_values(dimension, value)` / `memory_index_values(memory_id, index_value_id)` tables and manages them through shared sync/rebuild/count/filter paths; startup migrates and drops the legacy per-dimension tables by rebuilding from authoritative `memories.metadata`. A third `anchors` dimension reuses the same relation tables but is registered with `requires_embedding=False`: it is excluded (in both Python and SQL) from index-value embedding storage, completeness checks, missing/backfill, profile reindex and semantic resolution. NDJSON carries metadata for recovery.
- **`app/namespace_policy.py`** — Namespace governance. Format/type failures use `InvalidNamespace(ValueError)`; admin memory list/add/update/search distinguish these (`invalid_namespace`, HTTP 400) from request parsing errors (`invalid_request`, HTTP 400) and internal failures (HTTP 500). A namespace is just a record attribute; the known set is derived dynamically from live records (`SQLiteStorage.list_namespaces()`), plus `MEMORY_DEFAULT_NAMESPACE`, which is always valid so a fresh database can bootstrap. Names are trim-normalized, case-sensitive, Unicode-friendly, ≤64 chars and cannot be reserved words (`all`/`*`/`none`/`null`). Unknown namespaces raise `NamespaceNotFound` unless the caller passes `confirm_codes` containing the matching rule name (`namespace_not_found`); a code only bypasses its own rule and never format/length/reserved checks. No alias/registry/history-merge (tracked separately in the backlog).
- **`app/category_migrate_deepseek.py`** — Offline backfill CLI for `metadata.categories` on existing memories (deepseek-v4-flash via `opencode run`, strictly serial, per-record resilience, `--dry-run` supported). No local-Ollama/qwen path by design.
- **`app/keyword_migrate_deepseek.py`** — Explicit offline backfill CLI for `metadata.keywords` using deepseek-v4-flash via `opencode run`; it is not exposed through MCP or the admin UI. Normal writes rely on caller-provided `metadata.keywords`; omitted values remain absent.
- **`app/storage_sqlite.py`** — SQLite primary plus versioned embedding profiles. Factory-created writable stores hold the process-lifetime primary writer lock and release it on `stop`/`close`; direct low-level instances opt in explicitly for tests/internal work. Dense memory-text vectors live in `memory_embeddings(memory_id, profile_id, embedding)` and keyword/category vectors in `index_value_embeddings(index_value_id, profile_id, embedding)`; both share one active profile and codec lifecycle. The first startup migrates legacy inline pickle vectors into the active profile. New BLOBs use the portable `RMEV` codec and float16 by default. Reindex builds both vector sets in a shadow profile and switches it atomically; retained profiles support rollback only when both sets are complete. The derived FTS indexes `memory_content_fts` and `memory_anchor_fts` (fts5/unicode61) are kept in sync inside the same transaction as each authoritative write (`add`/`update`/`update_metadata`/`delete`/`clear`/`import_records`/`_add_with_id`); each table's pre-existing presence is checked **before** creation, so dropping a table cannot leave an empty index hidden behind a still-valid version marker. A missing table, a missing `storage_state.lexical_index_version` or a version mismatch triggers a full rebuild of both indexes before the service enters `running`; that rebuild also backfills the derived `anchors` relation dimension from metadata (so anchor search and `anchor_total`/`memories_with_anchors` cannot disagree after an upgrade), and a rebuild failure fails startup. `search_lexical` matches memory **content only** and `search_anchor` matches **anchors only**; both return `score = -raw_bm25` with a `match_info` block; read-only instances skip `_init_db` and never rebuild. The NDJSON disaster-recovery replica deliberately stores no embeddings.
- **`app/storage_ndjson.py`** — `NDJSONReplica` implements the narrow `ReplicaStorage` contract only: vector-free durable batch append/tombstone, fsynced atomic snapshot replacement, compact/stats, and recovery reads. Internal SQLite sync bookkeeping (`synced`, `version`) never enters the portable JSONL format. It does not implement business CRUD/search/import.
- **`app/index_value_resolver.py`** — Exact-first semantic resolver shared by keywords/categories. Unmatched values use query embeddings against same-dimension document embeddings, restricted to values used by live memories in the requested namespace. Adaptive Top-k applies an absolute score floor and maximum gap from the best match; resolved exact values constrain the existing SQLite candidate filter and never alter memory-text cosine scores.
- **`app/embedding_codec.py`** — Versioned float16/float32 dense-vector BLOB codec with read compatibility for trusted legacy pickle data.
- **`app/embed.py`** — Provider abstraction with distinct document/query paths. Ollama remains the default; FastEmbed 0.8 is optional and performs local ONNX inference, cache/offline validation, batching, normalization, dimension checks and input-length checks.
- **`app/embedding_cli.py`** — Offline embedding administration: `status`, `migrate-codec`, `download-model`, `reindex`, `reindex-index-values`, and `activate`. `status` is read-only; mutations take the shared SQLite-primary writer lock and therefore refuse to run while the service owns that database. `reindex-index-values` backfills keyword/category vectors for the active or a specified matching profile; full reindex builds both memory-text and index-value vectors. Reindex is intentionally not an MCP tool.
- Embeddings use **cosine similarity** (via numpy) for search scoring.
- **`app/jsonl_transfer.py`** — Strict shared JSONL reader/validator for Import and Recovery, clean snapshot writer, tolerant integrity inspection, hashing, safe filenames, and temporary-file cleanup. Historical append-only records/tombstones resolve by timestamp and file order; transfer JSONL never carries embeddings.
- **`app/admin.py`** — Web UI (admin console) routes under `/admin/console` and `/api/*` (incl. `/api/audit`). Ops actions require runtime maintenance mode and run one at a time; MCP/Web ordinary memory writes return `maintenance_mode` while reads continue. `POST /api/recover` accepts uploaded JSONL and rebuilds the current primary online; database switching briefly pauses reads, then dual mode full-syncs the replica. The standalone `app.recover_cli` still requires shutdown. Frontend is `web/index.html`; unknown namespaces on add/update/search/list use HTTP 400 with the same `response_code`/`response_meta` envelope as MCP. See `docs/project-management/plans/42-runtime-maintenance-mode.md`.

## Running & verification

```bash
source .venv/bin/activate
python -m app.server --help
python -m pytest -q
python tests/run_admin_browser.py
# Production service: bin/start.sh --port 1314
```

## Conventions

- Tests live in `tests/` and run with `python -m pytest`. Pytest overrides inherited storage/provider settings, uses an in-memory SQLite primary and temporary audit files, and disables embedding preload; tests needing other settings must override them explicitly. Browser regression: `.venv/bin/python tests/run_admin_browser.py` (requires google-chrome; isolated in-memory service and fake vectors). No linter/formatter/typecheck config. Python stdlib style.
- New SQLite embeddings use the versioned RMEV float16 codec. Legacy pickle BLOBs remain read-compatible and are migrated transactionally into the first active profile.
- Update merges metadata (shallow merge, new keys overwrite old).
- Namespace governance: a namespace must already exist as a live record before it can be written to or queried, except the configured `MEMORY_DEFAULT_NAMESPACE`, which is always known and permits the first write in a fresh database. Names are trim-normalized, case-sensitive, Unicode-friendly, ≤64 chars, and reserved words (`all`/`*`/`none`/`null`) are rejected. A confirmation code only bypasses its own existence rule — never format/length/reserved or embedding validation. An omitted namespace on `search` means all namespaces; an omitted namespace on `update` leaves the existing namespace unchanged.
- `clear` without namespace deletes everything. `clear` with namespace deletes only that namespace.
- The docker-compose.yml uses external images (`mem0/openmemory-mcp`, `mem0/openmemory-ui`) — unrelated to this local code.
- **Whenever adding or modifying an MCP tool, audit must be considered.** Checklist: ① register in `handle_list_tools`; ② add the dispatch branch in `handle_call_tool`; ③ add a `_TOOL_HINTS` entry; ④ wire audit through the runtime journal and update `app/audit.py` `_trim_params`/`_trim_result`; ⑤ keep tool description and hint consistent; ⑥ update the tool list in `app/read_me.md` so the `/read-me` guide stays accurate. Do not add a tool that is invisible to the audit journal.

## Memory usage protocol

Follow the global memory protocol rules in `~/.config/opencode/AGENTS.md`.

This project's namespace is `remember-me`. Set `MEMORY_DEFAULT_NAMESPACE=remember-me` when launching the MCP server so all agent-written memories default to this namespace.

## Project management documents

The authoritative project backlog is kept in the repository, not in a long memory record:

- `docs/project-management/backlog-todo.md` — open and ignored work.
- `docs/project-management/backlog-done.md` — completed work archive.
- `docs/project-management/plans/` — per-item implementation plans.

Memory records should contain only a short pointer to these files and a compact status, so updates do not exceed the embedding model context window.
