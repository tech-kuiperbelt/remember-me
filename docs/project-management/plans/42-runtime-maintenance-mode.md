# [42] Runtime Maintenance Mode and Online Recover

## Behavior

Ops has explicit Enter/Exit Maintenance controls. Enter first blocks new MCP and Web memory writes, waits for accepted writes, then drains and pauses scheduled dual sync/health/compact work. Searches, details, listings, stats, resources, report and audit remain available. All Ops actions require maintenance and share one server-side execution slot; an active import holds that slot until its background worker exits. Exit requires no active action or unresolved recovery error and resumes background work. Runtime maintenance state is process-local and resets on ordinary restart.

MCP writes return a `maintenance_mode` result with retry guidance. Web writes return HTTP 503 and the same response code. Ops actions without maintenance or while another action runs return HTTP 409 (`maintenance_required` / `maintenance_busy`). During the short primary switch, data reads return `maintenance_switching`; Ops status and progress remain available. These rules are checked by the backend, not just by disabled UI controls.

The first online Recover accepts an uploaded `.jsonl`/`.ndjson` file, subject to the existing import size and live-record limits. It always replaces the complete current database. The offline `bin/manage recover` remains separate; reindex/profile/model maintenance and LLM backfills stay offline CLIs.

## Recovery flow

1. Reserve the Ops action slot before receiving the upload, then store the bounded upload in a temporary file. Parse and validate all JSONL events.
2. Build a complete temporary SQLite primary beside the target DB with the active provider/profile: preserve IDs, content, metadata, namespaces, timestamps and summaries; regenerate memory and keyword/category embeddings plus derived relations and both FTS indexes. Verify SQLite integrity and embedding completeness.
3. Enter the brief switching state and wait for in-flight data reads to finish. Write and fsync a recovery marker while holding the existing lifetime writer lock. Close only the old SQLite connection, use SQLite's backup API to replace the target in one committed backup, and reopen the primary connection. Queries then use the recovered DB.
4. In dual mode, perform a full SQLite → JSONL snapshot and exact health check; remove the recovery marker only after success. Exit maintenance remains disabled if replica repair fails; Ops Sync can retry it. Startup detects a surviving marker and performs the full sync before regular background work.
5. Upload/temp files are removed, the task result is audited, and the Ops slot is released. Disconnecting a browser does not cancel the task. Shutdown waits for it.

SQLite's backup transaction makes an interrupted target update recover as either the old or new complete DB. The marker distinguishes a primary whose replica still needs a full rebuild, including after process interruption. The lifetime primary and replica writer locks remain held through online recovery.

## API and verification

- `GET /api/maintenance`, `POST /api/maintenance/enter`, `POST /api/maintenance/exit` expose runtime state.
- `POST /api/recover` returns a task ID; `GET /api/recover/{recover_id}` exposes phase, progress and outcome.
- Existing Ops paths are unchanged but require maintenance mode. Status and audit paths remain usable.
- Tests cover entry/exit, read and write admission, Ops gating, full primary/replica restore, transport shutdown, and existing regression suites. Browser regression checks controls and status display.
