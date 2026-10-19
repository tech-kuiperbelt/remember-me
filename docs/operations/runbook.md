# Remember Me Operations Guide

[English](runbook.md) · [简体中文](runbook.zh-CN.md)

This guide covers operating an installed Remember Me service. For installation, see the [README](../../README.md). Commands use a Bash-style shell and run from the repository root. Replace example paths and profile IDs with your actual values.

## Contents

1. [Start, connect, and stop](#start-connect-and-stop)
2. [Configuration and data directory](#configuration-and-data-directory)
3. [Model preparation and proxy](#model-preparation-and-proxy)
4. [Checks, backups, and standard maintenance](#checks-backups-and-standard-maintenance)
5. [Embedding maintenance](#embedding-maintenance)
6. [Data migration and recovery](#data-migration-and-recovery)
7. [Offline metadata backfills](#offline-metadata-backfills)
8. [Troubleshooting](#troubleshooting)
9. [Appendix: code structure and compatible entry points](#appendix-code-structure-and-compatible-entry-points)

Install dependencies with `bash bin/install.sh` before starting. The script selects CPU-only Torch and constrains its version while installing `requirements.txt`; installing that file alone does not guarantee CPU wheels. Python >=3.10 is required. Validated on Linux x86_64 with Python 3.12, using the official CPU index; macOS uses its non-CUDA PyPI build and still needs on-device validation. Native Windows is outside this Bash installer. Re-running reuses compatible CPU environments; GPU/NVIDIA packages or a broken `.venv` cause an error instead of automatic deletion. Preserve the old environment and create a clean one before retrying. Model files are prepared at service startup, separately from dependency installation.

## Start, connect, and stop

### Default startup

```bash
bash bin/start.sh
```

The launcher loads the repository's local `.env` when present, activates `.venv`, and defaults `MEMORY_STORAGE` to `dual`. HTTP binds to `127.0.0.1` by default and uses the saved port, initially `1314`.

| Purpose | Default URL |
|---|---|
| Configuration, memories, statistics, and Help | `http://127.0.0.1:1314/admin/console` |
| Connection instructions for agents | `http://127.0.0.1:1314/read-me` |
| MCP Streamable HTTP | `http://127.0.0.1:1314/mcp` |
| Legacy MCP SSE | `http://127.0.0.1:1314/sse` |

### Custom startup

```bash
bash bin/start.sh --port 8080
bash bin/start.sh --data-dir /path/to/remember-me-data
bash bin/start.sh --port 8080 --data-dir /path/to/remember-me-data
bash bin/start.sh --stdio
```

`--port` overrides the saved port for this launch; it does not change the configuration file. Update browser and MCP URLs accordingly. `--stdio` explicitly selects subprocess transport and cannot be combined with `--port`. Selecting a different data directory does not move existing data.

### MCP clients

HTTP serves both transports from one runtime. `/read-me` is a read-only connection guide, not an MCP protocol endpoint. Web UI **Help** contains client setup instructions and memory prompts.

For Codex, the project's Streamable HTTP configuration example is:

```toml
[mcp_servers.memory-server]
url = "http://127.0.0.1:1314/mcp"
```

Place it in the client's configuration (normally `~/.codex/config.toml`). Replace an existing `command`/`args` bridge entry for the same service instead of registering it twice. For OpenCode, merge this example into its configuration:

```json
{
  "mcp": {
    "memory-server": {
      "type": "remote",
      "url": "http://127.0.0.1:1314/sse",
      "enabled": true
    }
  }
}
```

Use the client version's setup guidance if its configuration format differs. A remote client cannot use your machine's loopback address directly.

### Restart and shutdown

In **Configuration**, select **Enter Maintenance**. Ordinary maintenance pauses memory writes while reads continue; model preparation and database switching have additional restrictions. When no maintenance action is running, use **Restart** or **Shut Down**. Shutdown waits for owned work and resource cleanup; wait for the process to exit before running an offline writer. After shutdown, start from the terminal with the same data directory and any original port override.

## Configuration and data directory

### Select the data directory

| OS | Default |
|---|---|
| Linux | `~/.remember-me/` |
| Windows | `%LOCALAPPDATA%\remember-me\`; otherwise `%USERPROFILE%\AppData\Local\remember-me` |
| macOS | `~/Library/Application Support/remember-me/` |

Precedence is `--data-dir` > `MEMORY_DATA_DIR` > OS default. Relative data-directory paths resolve against the repository root. The selected directory contains `config.jsonc`, the database, replica, audit files, logs, temporary files, backfill outputs, and model caches.

Default storage paths are `memories.db` and `memories.jsonl`. Default Sentence Transformers cache is `models/sentence-transformers/`; the historical FastEmbed cache is `models/fastembed/`. Runtime path overrides and CLI output paths resolve inside the data directory; paths escaping it, including through symlinks, are rejected. Repository assets, `.env`, external read-only import sources, and an independent Ollama service's storage are outside this rule.

### Configuration fields

Normal startup creates this configuration when absent:

```jsonc
{
  "port": 1314,
  "language": "zh-CN",
  "auto_download_model": true,
  "local_only": true,
  "debug": false,
  "proxy": {"enabled": false, "url": ""}
}
```

| Field | Meaning | When a UI-saved change applies |
|---|---|---|
| `port` | HTTP port, 1–65535 | Restart; explicit `--port` still wins |
| `language` | `zh-CN` or `en` UI | Refresh the page |
| `auto_download_model` | Download missing model files automatically | Next model preparation/startup; explicit Retry can request a download |
| `local_only` | Bind to loopback or all interfaces | Restart |
| `debug` | Debug buttons, Audit navigation, diagnostic entries | Refresh the page |
| `proxy` | Model download HTTP/HTTPS proxy | Next preparation/retry |

Saving ordinary settings does not require maintenance. For direct file edits, restart to load the changes. Debug does not change logging or MCP tools; element inspection additionally requires clicking a Debug button. There is no `--debug` startup option or stdio UI.

JSONC accepts line/block comments and trailing commas; UI saves preserve comments. Normal startup adopts legacy `config.json` only if `config.jsonc` is absent, retaining the old file. When both exist, JSONC is authoritative; invalid JSONC never falls back to JSON. Help and read-only configuration inspection do not migrate files. CLI `--port 0` requests an available ephemeral port, unlike the saved configuration field.

### Network access

`local_only: true` binds to `127.0.0.1`. Setting it to `false` binds to `0.0.0.0` and exposes all HTTP endpoints, including administration, to reachable network clients. The service has no authentication; use this only on a trusted network with appropriate access controls. Namespaces are not authorization boundaries.

In local-only mode, `/mcp` restricts Host/Origin to loopback: invalid Host returns 421 and invalid Origin returns 403. After security checks, methods other than POST return 405 with `Allow: POST`. LAN mode disables this Host/Origin protection. `/read-me` advertises a loopback URL with the effective port even in LAN mode.

## Model preparation and proxy

### Default and existing profiles

New installations use **Sentence Transformers + BAAI/bge-m3 + CPU**, included in the base dependencies. When `EMBED_PROVIDER` is unset, an existing database adopts its active embedding profile. Startup refuses conflicting model identities; a populated database without an identifiable active profile requires explicit original Provider/model settings.

Historical Ollama profiles remain supported, but require a separately running Ollama service with the matching model; Remember Me does not install or start it. Use `ollama pull MODEL` on that service's host if needed. FastEmbed remains an optional historical backend, installed with `requirements-fastembed.txt`; it is not the recommended new-install path.

### First download and retry

Local providers try cached files first. A recognized cache miss in HTTP mode opens the configuration UI in forced maintenance (`model_not_ready`). Automatic preparation starts when permitted. Otherwise, request **Retry** explicitly. While preparation is pending, memory reads, writes, searches, and exiting maintenance are blocked. After download, loading, warmup, and database validation succeed, manually select **Exit Maintenance** to resume writes.

A background preparation failure leaves the configuration UI available. Missing dependencies, invalid Provider settings, failures loading an existing cache, or other startup errors may prevent HTTP startup entirely. An explicit local model path that is missing must be restored; it is not treated as a downloadable repository ID. stdio and model-using CLI commands prepare synchronously and exit on failure; read-only status/help and codec/profile activation commands do not load a model.

### Proxy

Configure the download proxy in **Configuration**, save, then retry:

```json
{"proxy": {"enabled": true, "url": "http://127.0.0.1:7890"}}
```

Only HTTP/HTTPS proxies are supported. Disabled means direct access, ignoring inherited `HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY` variables, including lowercase variants. Loopback Ollama requests bypass proxies; an independent Ollama service has its own download configuration. Proxy URLs may contain credentials; `config status` redacts them.

Retry uses the saved configuration without restarting the server. A retry requested during preparation cancels the previous task before starting another. Progress reflects dependency-reported bytes, or an indeterminate state when totals are unavailable. Configuration-based startup overrides legacy provider/Hugging Face offline variables, uses cache-first loading, and disables the separate Xet download transport.

These settings do not configure pip installation networking. To explicitly download a missing model even when automatic downloads are disabled, use `bin/manage --data-dir "$RM_DATA_DIR" embedding download-model` after selecting the data directory as described below.

## Checks, backups, and standard maintenance

### Keep every command on the same data directory

Set this shell variable to the directory actually used by the service, not a new example directory:

```bash
RM_DATA_DIR="/absolute/path/to/your/current/remember-me-data"
bin/manage --data-dir "$RM_DATA_DIR" config status
bin/manage --data-dir "$RM_DATA_DIR" status
bin/manage --data-dir "$RM_DATA_DIR" embedding status
```

`bin/manage` loads `.env` and activates `.venv`. An assignment in `.env` can override a variable supplied before the command. Before model-changing commands, align Provider/model settings there; CLI `--data-dir` still wins. Keep this same `RM_DATA_DIR` for subsequent examples. Help is available through `bin/manage --help` and `bin/manage embedding --help`.

| Operation | Service state / effects |
|---|---|
| Status, config status, help | May run while the service is active; no model loading |
| Model download | Writes model cache, not memory records; coordinate with any active preparation |
| UI export, sync, import, recovery, clear, namespace migration | Enter maintenance; the UI enables actions when their prerequisites hold |
| Offline reindex, codec conversion, activation, recovery, applied backfills, data-directory migration | Stop the service and wait for exit |

Writable offline processes acquire the primary database writer lock; they do not stop an existing service for you. Do not delete `.writer.lock` to bypass it: the file's existence does not mean a process currently holds the OS lock.

### Backups and health

For dual storage, **Full Export** syncs and verifies the JSONL replica before downloading a snapshot. Store that download independently before maintenance. **Sync** refreshes the current replica; it is not a historical backup because it follows subsequent changes. Namespace Export is a partial snapshot, not a full disaster-recovery backup. JSONL contains records and metadata, not embeddings; restoring it needs model inference.

If using SQLite-only storage, replica-based Full Export/Sync are unavailable. Stop the service cleanly before making a separate database backup; preserve the relevant configuration and Provider/model settings as well. Protect backup files as private memory data.

In dual mode, inspect available Statistics diagnostics: `pending=0`, `in_sync=true`, `missing=0`, `extra=0`, and `corrupt_lines=0`. Pending writes reaching zero alone do not prove replica health. Debug exposes diagnostic entries. SQLite-only installations have no replica to validate.

### Standard sequence

1. Confirm the service's data directory and startup overrides. Enter maintenance in Configuration.
2. For dual mode, download Full Export and retain it as an independent backup.
3. Select Shut Down and wait for exit. For SQLite-only storage, now make an independent database backup before any offline write.
4. Check the chosen root with the status commands above, then perform the required operation.
5. Restart with `bash bin/start.sh --data-dir "$RM_DATA_DIR"`, preserving any prior `--port` override.
6. Check the active Profile, memory availability, and dual replica health where applicable. If startup entered model-preparation maintenance, exit it manually after preparation succeeds.

## Embedding maintenance

### Inspect and prepare

```bash
bin/manage --data-dir "$RM_DATA_DIR" embedding status
bin/manage --data-dir "$RM_DATA_DIR" embedding download-model
```

Without an explicit Provider, these model-preparation defaults follow the existing active profile. An explicit Provider on `download-model` selects the model to prepare. Read status output before choosing a target or rollback Profile ID.

### Switch Provider or model

Back up and stop the service first. Set the intended target in the repository's private `.env`, updating existing assignments rather than appending conflicting values. For the recommended target:

```bash
EMBED_PROVIDER=sentence_transformers
SENTENCE_TRANSFORMERS_MODEL=BAAI/bge-m3
SENTENCE_TRANSFORMERS_DEVICE=cpu
```

Also review any revision and query/document-prefix overrides. Then run:

```bash
bin/manage --data-dir "$RM_DATA_DIR" embedding download-model
bin/manage --data-dir "$RM_DATA_DIR" embedding reindex
bin/manage --data-dir "$RM_DATA_DIR" embedding status
```

Reindex builds memory-text and keyword/category vectors for the target, then activates the complete profile atomically. This also supports changing model or revision within the same Provider. Without an explicit target Provider, it adopts the active profile and normally returns `already_active`. Keep startup configuration aligned with the new profile before restarting.

Profile identity includes Provider, model (and encoded revision), dimension, normalization, and query/document prefixes. Device and codec dtype are not part of that identity.

### Repair index vectors and roll back

Run with the service stopped:

```bash
bin/manage --data-dir "$RM_DATA_DIR" embedding reindex-index-values
bin/manage --data-dir "$RM_DATA_DIR" embedding reindex-index-values --profile-id PROFILE_ID
bin/manage --data-dir "$RM_DATA_DIR" embedding activate PROFILE_ID
```

The first command repairs the active profile's keyword/category vectors; `--profile-id` selects a retained profile instead. Remove or align explicit Provider/model settings that conflict with that target. Activation requires complete memory and index vectors; it does not run inference to fill gaps. Before restarting after rollback, align `.env` with the activated model and ensure that model/service is available. Retained profiles are not guaranteed to remain complete after later memory changes.

### Change vector encoding

```bash
bin/manage --data-dir "$RM_DATA_DIR" embedding migrate-codec --dtype float32
```

Stop the service first. This changes vector encoding, not the embedding model. To retain float32 on later startup, also set `EMBED_DTYPE=float32` in `.env`; use matching `float16` settings to switch back. Startup can re-encode existing vectors to the configured dtype, so a one-time codec command without matching startup configuration may be undone. Verify the stored codec/dtype using embedding status.

## Data migration and recovery

### Move the data directory

Selecting `--data-dir` alone does not migrate files. Choose explicit source and destination directories:

```bash
RM_NEW_DATA_DIR="/absolute/path/to/new/remember-me-data"
bin/manage --data-dir "$RM_NEW_DATA_DIR" config migrate --source-data-dir "$RM_DATA_DIR" --dry-run
# Stop the service and wait for exit before the following command.
bin/manage --data-dir "$RM_NEW_DATA_DIR" config migrate --source-data-dir "$RM_DATA_DIR"
bash bin/start.sh --data-dir "$RM_NEW_DATA_DIR"
```

For a legacy repository `data/`, use `--source-data-dir data`. Migration copies and verifies files, retains sources, and updates configuration and `.env` paths. It refuses conflicting destinations and in-use databases. Review the preview before applying; confirm model loading and storage health in the new root before retiring old files. Preserve the original port override when needed and use the new root for subsequent maintenance.

To rely on the OS default afterward, remove a stale `MEMORY_DATA_DIR` assignment from `.env` after migration; otherwise keep the custom root explicit. If a crash leaves a migration journal that blocks startup, rerun the same migration command to recover, then inspect the result before retrying. Do not manually delete the journal or shared caches to bypass recovery.

### Online JSONL recovery

In Configuration, enter maintenance, upload a trusted complete JSONL backup, and choose **Recover**. Reads continue during rebuilding; the final database switch briefly rejects queries with a retry response. Watch progress through completion. In dual mode, recovery also performs Full Sync; if that fails, stay in maintenance and retry Sync before exiting. Import adds records through the import workflow; Recover rebuilds the database and must not be treated as an append operation.

### Offline JSONL recovery

Stop the service and retain an independent backup of the source and existing target. Recovery builds a temporary database and replaces the target only after success:

```bash
# Rebuild the configured primary from its configured JSONL replica.
bin/manage --data-dir "$RM_DATA_DIR" recover

# Rebuild a separate target inside the selected data directory.
bin/manage --data-dir "$RM_DATA_DIR" recover /path/to/backup/memories.jsonl recovered.db
```

With an explicit Provider, recovery uses that target without probing the old database. Otherwise, a readable target uses its active profile; a missing target uses the product default. An unreadable/corrupt target or populated target without an identifiable profile requires explicit Provider/model settings. A separate `recovered.db` is not selected automatically for later service startup; configure the primary path deliberately if it is to become the live database.

Inspect the resulting profile and match startup settings before restarting. Verify memory records and, in dual mode, replica health. Keep the service in maintenance if synchronization needs repair.

## Offline metadata backfills

Keyword/category backfills use an external `opencode` CLI and the configured DeepSeek model. Actual generation sends selected memory content to that external workflow; use it only for data permitted there. Dry-run counts the eligible records without generating updates:

```bash
bin/manage --data-dir "$RM_DATA_DIR" migrate keywords --dry-run --limit 20
bin/manage --data-dir "$RM_DATA_DIR" migrate categories --dry-run --limit 20
```

Inspect the scope, back up, and stop the service before removing `--dry-run`. Use `--namespace`, `--memory-id`, `--limit`, or `--db` to narrow scope. Review per-record failures before retrying.

Anchor backfill separates model-generated drafts from applying a reviewed manifest. Use an explicit namespace, since the command has a project-specific default:

```bash
bin/manage --data-dir "$RM_DATA_DIR" migrate anchors draft --namespace PROJECT --limit 20 --dry-run
bin/manage --data-dir "$RM_DATA_DIR" migrate anchors draft --namespace PROJECT --limit 20 --manifest anchor-backfill/review.jsonl
# Review the generated manifest; stop the service before applying it.
bin/manage --data-dir "$RM_DATA_DIR" migrate anchors apply --manifest "$RM_DATA_DIR/anchor-backfill/review.jsonl" --dry-run
bin/manage --data-dir "$RM_DATA_DIR" migrate anchors apply --manifest "$RM_DATA_DIR/anchor-backfill/review.jsonl"
```

Draft leaves the database unchanged but, without `--dry-run`, invokes the external model and writes a manifest. Apply uses the reviewed manifest and commits the batch transactionally. Use command-specific `--help` for supported options; the three backfills do not share every flag.

## Troubleshooting

| Symptom | Check and next action |
|---|---|
| No Web UI | Confirm process, effective port, and data directory; inspect startup errors and logs. Missing dependencies or cached-model initialization errors can stop HTTP startup. |
| Model not ready / background download failed | Open Configuration, inspect the model error, save the correct proxy, and retry. Exit maintenance after successful preparation. |
| Profile mismatch | Compare embedding status with `.env`. Restore matching settings or stop the service and explicitly reindex to the intended model. |
| Ollama connection/model error | Start the independently installed matching Ollama service, obtain its model, or perform an explicit offline migration. |
| Writer lock busy | Identify and gracefully stop the process using that primary/replica; do not delete lock files. |
| Replica drift or failed post-recovery sync | Retain an independent backup, inspect diagnostics, then run Sync in maintenance. Check health before exiting. |
| Corrupt recovery target cannot be identified | Supply the intended Provider/model explicitly, then rerun offline recovery from a trusted JSONL backup. |
| Pending data migration journal | Rerun the same migration command to recover; inspect the resulting paths before starting. |
| Data seems missing after restart | Verify `--data-dir`, `MEMORY_DATA_DIR`, `.env`, and the primary path before creating new records or attempting recovery. |

## Appendix: code structure and compatible entry points

| Location | Responsibility |
|---|---|
| `app/config/` | Settings, JSONC, data-root resolution |
| `app/domain/` | Pure validation and retrieval rules |
| `app/storage/` | SQLite/dual contracts, replica, locks, JSONL and recovery |
| `app/embedding/` | Providers, profile resolution, vector codec and index-value resolution |
| `app/transport/` | MCP tools and Admin HTTP |
| `app/operations/` | Worker pools, maintenance, imports and model preparation |
| `app/cli/` | Offline administration and backfills |
| `app/server.py`, `runtime.py`, `service.py` | Transport startup, resource lifecycle and business logic |

`app/audit.py` and `app/log_config.py` own audit/logging support. Domain does not depend on services/transports; online business does not import CLI. Package imports acquire no runtime resources.

`bin/start.sh`, `bin/manage`, `bin/recover.sh`, and the root `python -m app.*` command launchers remain available. CLI implementations live under `app.cli`; old internal import paths are not compatibility APIs. Prefer `bin/manage` for the examples above: direct module commands can use different argument ordering and do not source the repository `.env` automatically.
