# Source installation and upgrade

Install Remember Me from Git and run it with Python. For the official container,
see [Docker installation and upgrade](docker.md).

The recommended Python setup is **Sentence Transformers + BAAI/bge-m3 on CPU**.
You do not need Ollama, Docker, or a separate embedding server. Model weights are
**not** bundled in the GitHub repository; they are downloaded on first use.

## Requirements

- Git.
- Python 3.10 or newer with `venv` support.
- A Linux/macOS-style shell.
- Sufficient disk space and memory for BGE-M3 and its Python dependencies.

Validated on Linux x86_64 with Python 3.12. macOS still needs on-device
validation, and native Windows is not supported by this Bash installer.

## Install

```bash
git clone https://github.com/tech-kuiperbelt/remember-me.git
cd remember-me
bash bin/install.sh
```

The installer creates `.venv`, installs CPU-only PyTorch, then installs the
remaining dependencies while keeping that Torch version fixed. On Linux it uses
the official PyTorch CPU wheel index; macOS uses its non-CUDA distribution from
PyPI.

Use the installer for CPU setup: running `pip install -r requirements.txt` alone
does not select a CPU wheel. An existing GPU or broken environment causes the
installer to stop; preserve or rename it and rerun to create a clean `.venv`.
Compatible CPU environments can be reused.

## Start

**Default startup**

```bash
bash bin/start.sh
```

For a new installation, the default is **Sentence Transformers + BAAI/bge-m3 on
CPU**. The service uses the local model cache when available. When model files are
missing, the HTTP interface can start in a *model not ready* state while the model
is prepared. After preparation succeeds, manually exit maintenance mode in the Web
UI to enable memory writes.

Existing installations retain their active database embedding profile when no
Provider is explicitly configured, including historical Ollama profiles.
`bin/start.sh` loads a local `.env` if present. Changing Provider or model requires
an explicit target and offline reindexing.

With the default local address and port:

| Purpose | URL |
| --- | --- |
| Web UI | `http://127.0.0.1:1314/admin/console` |
| Instructions for agents | `http://127.0.0.1:1314/read-me` |
| MCP — Streamable HTTP | `http://127.0.0.1:1314/mcp` |
| MCP — SSE | `http://127.0.0.1:1314/sse` |

**Custom port and data directory**

You can pass startup options directly to `bin/start.sh`:

```bash
# Use a different HTTP port
bash bin/start.sh --port 8080

# Bind all interfaces for this launch (for example inside a Docker bridge
# container; MCP Host/Origin protection stays enabled)
bash bin/start.sh --host 0.0.0.0

# Choose where configuration, the database, and model caches are stored
bash bin/start.sh --data-dir /path/to/remember-me-data

# Combine both options
bash bin/start.sh --port 8080 --data-dir /path/to/remember-me-data
```

`--port` overrides the saved port for this launch without changing the
configuration file. With `--port 8080`, open
`http://127.0.0.1:8080/admin/console` and use port `8080` in your MCP client URL
as well.

`--host` overrides the bind address for this launch only. It accepts
`127.0.0.1` or `0.0.0.0`, is not written to the configuration file, and does not
change the MCP Host/Origin protection, which remains controlled by `local_only`.
A Docker container can therefore listen on `0.0.0.0` while the host publishes only
`127.0.0.1:1314:1314`; clients still use `http://127.0.0.1:1314/...`. The publish
mapping limits host-side exposure only: other containers on the same Docker network
can still reach the container port directly. This option cannot be combined with
`--stdio`.

`--data-dir` selects the data directory, including its `config.jsonc`, and takes
precedence over `MEMORY_DATA_DIR`. Relative paths are resolved from the project
root. Selecting a new directory starts with separate data; it does not move an
existing database or model cache.

`--model-dir` (or `MEMORY_MODEL_DIR`) selects the model root, defaulting to
`<data-dir>/models`. An explicit root may live outside the data directory, for
example a read-only model image. Provider caches and Hugging Face hub resources
then resolve inside that root, while writable metadata caches stay under
`<data-dir>/cache`.

### First model download and proxy setup

The first use may require downloading **BAAI/bge-m3** from its upstream source.
The model is cached locally; after it has been downloaded, embedding inference
runs on your machine without a continuously connected model service.

If the download source is unreachable from your network, open the Web UI's
**configuration page**, set an HTTP/HTTPS proxy for model downloads, save the
settings, and choose **Retry**. A failed *background model download* does not
require restarting the service just to retry after changing the proxy.

**Important:** The Web UI remains available in maintenance mode for
model-preparation problems: a missing model, a failed background download, and
also an incomplete/corrupt cache or a load/warmup failure — these no longer stop
the HTTP server. Retry re-loads the model and re-downloads an incomplete cache.
Missing Python dependencies report a reinstall message; invalid provider settings
and other configuration errors can still prevent the server from starting. stdio
mode and model-using CLI commands fail fast instead of opening a UI.

**Resource note:** Allow sufficient disk space and memory for the model and its
Python dependencies. Minimum hardware requirements remain to be validated.

The Web UI proxy settings are shared by version checks and SOURCE model downloads.
Installing Python dependencies with pip requires its own network or proxy
configuration. Automatic model downloads are controlled by `auto_download_model`;
when disabled, use the configuration page to request a retry explicitly.

## Stop

Stop the process from its terminal with `Ctrl+C` (or send `SIGTERM`) and wait until
it exits. The HTTP server closes active MCP/SSE sessions and drains accepted work;
in stdio mode, closing stdin or `SIGTERM` triggers the same cleanup. Do not start a
second writer against the same data directory, and do not copy or upgrade data
while the service is still running.

## Data directory

By default the service uses the OS per-user data directory (for example
`~/.remember-me/` on Linux and `~/Library/Application Support/remember-me/` on
macOS). Use `--data-dir` or `MEMORY_DATA_DIR` to choose an explicit root; relative
paths are resolved from the project root, and `--data-dir` takes precedence.

The data directory contains `config.jsonc`, the SQLite database and its writer
lock/outbox state, the audit journal, caches, and the model root (`models/` by
default). Dual-storage installations (the default when launched with
`bin/start.sh`) also keep the NDJSON replica. Selecting a different directory
does not move existing data, and a new directory starts empty.

## Backup and restore

Stop the service first. Back up the **entire data directory**, not only the SQLite
main file or a JSONL export.

```bash
export DATA_DIR=/path/to/remember-me-data
tar -C "$DATA_DIR" -cpf "$PWD/remember-me-source-backup.tar" .
```

Record the code version from `release.jsonc`, the data path and the startup
arguments with the backup. The archive contains private memories; protect it
accordingly.

To restore, stop the service, create a **new empty** directory, extract while
preserving ownership and permissions, and start with that directory:

```bash
mkdir "$PWD/restored-data"
tar -C "$PWD/restored-data" -xpf "$PWD/remember-me-source-backup.tar"
bash bin/start.sh --data-dir "$PWD/restored-data"
```

Check health, configuration, memory details and semantic search before retiring
the original copy. Never run two services as writers against the same directory.

<a id="upgrade-and-rollback"></a>
## Upgrade and rollback

Read all relevant Upgrade Notes / Breaking Changes. Stop the service and confirm
it has exited. Back up the **entire data directory**, including configuration,
database and journals. Keep the old version and matching backup.

In the original checkout, resolve local changes and confirm the current branch has
an upstream and can fast-forward before manually running:

```bash
git pull --ff-only
bash bin/install.sh
bash bin/start.sh
```

`git pull --ff-only` follows the current branch and may include unreleased code.
The displayed version comes only from the local `release.jsonc`. If the checkout
is dirty, detached, diverged or has no upstream, stop and resolve it manually; do
not force an overwrite. The application neither inspects Git nor runs these
commands. Preserve the original data path, configuration, environment and startup
arguments when restarting. Verify health and retrieve an existing memory.

### Rollback

Use the previous checkout/version and its matching full backup. Reuse current data
only when that release's database schema and Embedding Profile are compatible.
Otherwise restore the matching full backup into a new directory and run the code
that matches it; changes made after the backup are not included. Version
notifications and local recreation tests do not prove compatibility between
arbitrary future versions.

## Next steps

- [Connect & Enable Agent Memory](../../README.md#connect--enable-agent-memory)
  for MCP client configuration and verification.
- [Docker installation and upgrade](docker.md) if you prefer the container.
- [Version numbering](../../README.md#version-numbering) and
  [version checks and networking](../../README.md#version-checks-and-networking).
