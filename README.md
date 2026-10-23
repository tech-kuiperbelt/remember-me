<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="web/static/img/brand/logo-dark-128.png">
    <img src="web/static/img/brand/logo-light-128.png" alt="Remember Me seahorse logo" width="96" height="96">
  </picture>
</p>

<h1 align="center">Remember Me</h1>

<p align="center">
  <strong>Agent-first memory. Human-visible by design.</strong>
</p>

<p align="center">
  <a href="README.md">English</a> · <a href="README.zh-CN.md">简体中文</a>
</p>

Remember Me is a **local-first, persistent memory service for AI agents**, built on the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/).

Agents are the primary creators and users of memory. They can preserve useful knowledge, recall earlier decisions, and update what they have learned across sessions. The Web UI gives people a way to see what their agents remember, how that information is organized, and how memory is retrieved and used.

**Memory infrastructure for agents — with the knowledge remaining under the user's control.**

## Why Remember Me?

AI coding agents can work through complex problems, but their experience is often fragmented across sessions. An agent may investigate an issue, compare approaches, reach a decision, and learn from mistakes — yet struggle to reuse that work the next time around.

Remember Me aims to make accumulated knowledge useful beyond a single conversation. Its design follows three principles:

**Agent-first.** Agents drive the memory workflow: finding relevant memories, deciding what is worth keeping, and revising outdated knowledge. People shouldn't have to maintain a note collection just to give an agent continuity. Remember Me exposes the tools; an agent's instructions and behavior determine how it uses them.

**Retrieval-oriented.** Memory is not a complete transcript archive. Its value lies in finding and reusing relevant experience when a new task calls for it. Different search methods help agents recall information through meaning, exact terms, or explicit anchors.

**Human-owned.** Knowledge accumulated through work with an agent should remain visible and under the user's control, not be locked inside a particular client or platform. Remember Me separates memory from the agent that uses it, with readable data and portability as design principles. Its Web UI enables people to inspect and manage that memory.

> Agents can change. Models can improve. Tools can evolve. The knowledge you've built should carry forward.

## Features

- **Agent-driven long-term memory.** MCP tools for creating, searching, updating, and deleting memories. Agents choose when to use them according to their tasks and instructions.
- **Three retrieval methods.** Semantic search for meaning, lexical search for terms, and anchor search for explicit clues or identifiers.
- **Structured organization.** Namespaces, categories, keywords, and anchors help organize knowledge across projects and contexts.
- **Observable Web UI.** Browse and inspect memories, search and filter them, edit when needed, and review memory and retrieval statistics.
- **Agent-independent access.** Compatible MCP clients can connect to the same standalone service and reuse memories in the appropriate namespace.
- **Local-first operation.** SQLite stores memory data. Sentence Transformers runs the **BAAI/bge-m3** embedding model inside the Python process, without a separate model service.

**Data portability:** User control, readable data, backup, and migration are important to the project. See the in-app Help and operational documentation for the operations and formats supported by your installed version; this README does not imply that every import/export workflow is available.

## See What Your Agents Remember

The Web UI exists primarily for **transparency and observability**, not to turn agent memory into another note-taking task.

### Memory Explorer

Browse and filter memories, switch among supported search methods, and inspect namespaces, categories, keywords, and anchors.

![Illustrative Remember Me Memory Explorer with fictional demo memories](assets/screenshots/memory-explorer.png)

### Memory Detail & Retrieval Statistics

Inspect the content of individual memories, make corrections when needed, and review retrieval activity and feedback.

<p align="center">
  <a href="assets/screenshots/memory-detail.png"><img src="assets/screenshots/memory-detail.png" alt="Illustrative Memory Detail with fictional demo data" width="49%"></a>
  <a href="assets/screenshots/retrieval-stats.png"><img src="assets/screenshots/retrieval-stats.png" alt="Illustrative AI retrieval statistics with fictional demo values" width="49%"></a>
</p>

*The images above are illustrative UI previews using fictional data, based on the project's interface design. They are not screenshots captured from a running instance.*

<a id="快速开始"></a>

## Quick Start

The recommended setup is **Python + Sentence Transformers + BAAI/bge-m3**. You do not need Ollama, Docker, or a separate embedding server.

### 1. Install

You will need Git, Python 3.10 or newer with `venv` support, and sufficient disk space and memory for BGE-M3. The commands below use a Linux/macOS-style shell.

```bash
git clone https://github.com/tech-kuiperbelt/remember-me.git
cd remember-me
bash bin/install.sh
```

The installer creates `.venv`, installs CPU-only PyTorch, then installs the remaining dependencies while keeping that Torch version fixed. On Linux it uses the official PyTorch CPU wheel index; macOS uses its non-CUDA distribution from PyPI. Validated on Linux x86_64 with Python 3.12; macOS still needs on-device validation, and native Windows is not supported by this Bash installer.

Use the installer for CPU setup: running `pip install -r requirements.txt` alone does not select a CPU wheel. An existing GPU or broken environment causes the installer to stop; preserve or rename it and rerun to create a clean `.venv`. Compatible CPU environments can be reused. The installation includes the Sentence Transformers runtime. Model weights are **not** bundled in the GitHub repository.

### 2. Start the service

**Default startup**

```bash
bash bin/start.sh
```

For a new installation, the default is **Sentence Transformers + BAAI/bge-m3 on CPU**. The service uses the local model cache when available. When model files are missing, the HTTP interface can start in a *model not ready* state while the model is prepared. After preparation succeeds, manually exit maintenance mode in the Web UI to enable memory writes.

Existing installations retain their active database embedding profile when no Provider is explicitly configured, including historical Ollama profiles. `bin/start.sh` loads a local `.env` if present. Changing Provider or model requires an explicit target and offline reindexing.

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

`--port` overrides the saved port for this launch without changing the configuration file. With `--port 8080`, open `http://127.0.0.1:8080/admin/console` and use port `8080` in your MCP client URL as well.

`--host` overrides the bind address for this launch only. It accepts `127.0.0.1` or `0.0.0.0`, is not written to the configuration file, and does not change the MCP Host/Origin protection, which remains controlled by `local_only`. A Docker container can therefore listen on `0.0.0.0` while the host publishes only `127.0.0.1:1314:1314`; clients still use `http://127.0.0.1:1314/...`. The publish mapping limits host-side exposure only: other containers on the same Docker network can still reach the container port directly. This option cannot be combined with `--stdio`.

`--data-dir` selects the data directory, including its `config.jsonc`, and takes precedence over `MEMORY_DATA_DIR`. Relative paths are resolved from the project root. Selecting a new directory starts with separate data; it does not move an existing database or model cache.

`--model-dir` (or `MEMORY_MODEL_DIR`) selects the model root, defaulting to `<data-dir>/models`. An explicit root may live outside the data directory, for example a read-only model image. Provider caches and Hugging Face hub resources then resolve inside that root, while writable metadata caches stay under `<data-dir>/cache`.

### 3. First model download and proxy setup

The first use may require downloading **BAAI/bge-m3** from its upstream source. The model is cached locally; after it has been downloaded, embedding inference runs on your machine without a continuously connected model service.

If the download source is unreachable from your network, open the Web UI's **configuration page**, set an HTTP/HTTPS proxy for model downloads, save the settings, and choose **Retry**. A failed *background model download* does not require restarting the service just to retry after changing the proxy.

**Important:** The Web UI remains available in maintenance mode for model-preparation problems: a missing model, a failed background download, and also an incomplete/corrupt cache or a load/warmup failure — these no longer stop the HTTP server. Retry re-loads the model and re-downloads an incomplete cache. Missing Python dependencies report a reinstall message; invalid provider settings and other configuration errors can still prevent the server from starting. stdio mode and model-using CLI commands fail fast instead of opening a UI.

**Resource note:** Allow sufficient disk space and memory for the model and its Python dependencies. Minimum hardware requirements remain to be validated.

The Web UI proxy settings are shared by version checks and SOURCE model downloads. Installing Python dependencies with pip requires its own network or proxy configuration. Automatic model downloads are controlled by `auto_download_model`; when disabled, use the configuration page to request a retry explicitly.

<a id="source-updates"></a>
## SOURCE updates

Stop the service and confirm it has exited. Back up the **entire data directory**,
including configuration, database and journals. Keep the old version and matching
backup, and read all relevant Upgrade Notes / Breaking Changes. In the original
checkout, resolve local changes and confirm the current branch has an upstream
and can fast-forward before manually running:

```bash
git pull --ff-only
bash bin/install.sh
bash bin/start.sh
```

`git pull --ff-only` follows the current branch and may include unreleased code.
The displayed version comes only from the local `release.jsonc`. If the checkout
is dirty, detached, diverged or has no upstream, stop and resolve it manually;
do not force an overwrite. The application neither inspects Git nor runs these
commands. Preserve the original data path, configuration, environment and startup
arguments when restarting. Verify health and retrieve an existing memory.

## Version checks and networking

HTTP/Web checks asynchronously after startup and every 24 hours by default.
Refresh manually in the sidebar's version dialog. stdio and offline CLI do not
check. `Unknown` means the local version is unknown and does not prevent checks;
`No Release` means no official release exists, rather than a network failure.
The application only offers guidance and copying; it does not upgrade or back up.

For `Unavailable`, choose **Configure proxy** in the version dialog to reach the
existing Settings. Save, return and refresh; the next check uses the new settings
without restarting. The backend contacts `api.github.com` and
`raw.githubusercontent.com`; disabling the proxy connects directly. Check DNS,
connectivity, certificates and proxy reachability from the service host/container,
and wait out rate-limit cooldowns. Keep TLS verification enabled. In a container,
`localhost` refers to that container; this proxy does not enable networking for
Docker's built-in model. Docker Engine / Docker Desktop needs its own proxy for
GHCR pulls; Git and pip also have separate networking. Application Settings do
not configure them. See **Help → Configuration → Updates and networking**.

## Docker (Linux AMD64)

**Pre-release: the official GHCR image is not public yet.** The commands below
are prepared for publication; `1.0.0` is the example version, not an announcement
that it is available. Use the actual published full version when available.
Local acceptance uses `REMEMBER_ME_IMAGE=remember-me:phase3.2-ready-fixed` instead;
it does not prove public downloads. Neither path requires Python or a separate
Embedding service: **BGE-M3 is included and runs on CPU without model downloads.**

These Bash commands target a local, rootful Linux Docker Engine on AMD64. Compose
also requires the Docker Compose plugin. Rootless Docker, Docker Desktop, SELinux
hosts and network filesystems need separate permission/platform validation. The
image is about 3.5 GB unpacked; allow extra disk space for pulls, updates, backups
and model working memory. A minimum RAM requirement has not been established.

### Prepare one instance

Choose **one** installation method. Both use the same image and `./data` directory;
never start two writers against it. Run the following in a new working directory
and keep using that directory. Paths must be on the Docker daemon's local host.

```bash
mkdir -p remember-me
cd remember-me
export REMEMBER_ME_IMAGE=ghcr.io/tech-kuiperbelt/remember-me:1.0.0
export REMEMBER_ME_DATA_DIR="$PWD/data"
export REMEMBER_ME_PORT=1314
```

Keep a record of these settings. In a new terminal, set them again, especially if
you selected another directory or port. Pin a full version or the published
`ghcr.io/tech-kuiperbelt/remember-me@sha256:...` digest; avoid implicit `latest`.
The release's Git tag is `v1.0.0`, while its image tag is `1.0.0`.

### Option 1: docker run

Create a dedicated directory and run the image's existing initializer. Only this
short-lived helper starts as root; it prepares an **empty** directory for
UID/GID `10001:10001`, then drops privileges and checks access. Reusing an existing
directory checks permissions without recursively changing ownership or overwriting
configuration. Do not continue if initialization fails.

```bash
mkdir -p "$REMEMBER_ME_DATA_DIR"
docker run --rm --platform linux/amd64 --network none --read-only \
  --user 0:0 --cap-drop ALL \
  --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
  --cap-add SETUID --cap-add SETGID --security-opt no-new-privileges:true \
  --mount "type=bind,src=$REMEMBER_ME_DATA_DIR,dst=/data" \
  --entrypoint python "$REMEMBER_ME_IMAGE" /opt/remember-me/docker/init_data.py
```

After the initializer succeeds, start the application:

```bash
docker run -d --name remember-me --platform linux/amd64 \
  --restart unless-stopped --stop-timeout 120 \
  --user 10001:10001 --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --mount "type=bind,src=$REMEMBER_ME_DATA_DIR,dst=/data" \
  -p "127.0.0.1:${REMEMBER_ME_PORT}:1314" "$REMEMBER_ME_IMAGE"
```

### Option 2: Docker Compose

Download the [official compose.yaml](compose.yaml) from the **same release tag**
into the working directory (Git source checkout is unnecessary):

```bash
curl -fL https://raw.githubusercontent.com/tech-kuiperbelt/remember-me/v1.0.0/compose.yaml -o compose.yaml
docker compose -f compose.yaml up -d --wait --wait-timeout 300
```

The release file and image download require the release to exist. For local
pre-release acceptance, copy `compose.yaml` from this checkout and use the local
image override above. The legacy `docker-compose.yml` is unrelated; explicitly
use `-f compose.yaml`. Compose runs the same permission initializer before the
application. Its defaults match the setup above. For custom settings across
terminal sessions, put the three `REMEMBER_ME_*` settings in a local `.env` file
beside `compose.yaml`, with an absolute data path and without the `export` prefix;
exported shell values take precedence. Check `docker compose -f compose.yaml config`
before starting. Stop/remove the previous method's container before switching.

### Connect, inspect and stop

Open <http://127.0.0.1:1314/admin/console> when healthy. First startup may take
several minutes. If using another host port, replace `1314` in browser and MCP
URLs. Use `/mcp` for Streamable HTTP or `/sse` for legacy SSE; see
[client configuration below](#connect--enable-agent-memory).

| Operation | docker run | Docker Compose |
| --- | --- | --- |
| Status | `docker ps --filter name=remember-me` | `docker compose -f compose.yaml ps -a` |
| Logs | `docker logs --tail 100 -f remember-me` | `docker compose -f compose.yaml logs --tail 100 -f remember-me` |
| Health | `docker inspect --format '{{.State.Health.Status}}' remember-me` | `docker compose -f compose.yaml ps` |
| Stop | `docker stop --time 120 remember-me` | `docker compose -f compose.yaml stop` |
| Start again | `docker start remember-me` | `docker compose -f compose.yaml up -d --wait --wait-timeout 300` |
| Remove stopped container | `docker rm remember-me` | `docker compose -f compose.yaml down` |

Health observes model readiness and does not initiate a download. If startup
fails, check the logs, disk space and data permissions. Language/Debug and the version-check proxy remain
editable in the configuration page; Docker manages model settings. Retry reloads
the included model locally. The root filesystem and model stay read-only; the
single `/data` bind mount retains configuration, SQLite and its sidecars, Dual
JSONL, logs and caches. Deleting/recreating a container does not delete this host
directory. Do not delete it or mount over the image's model directory.

The service publishes only `127.0.0.1`; do not change it to a public bind without
a separate access-control design. Containers on the same Docker network can
still access the container directly. Host/Origin protection remains enabled.
Neither API nor Admin UI provides user authentication. `unless-stopped` enables
restart after unexpected exits; stop from the terminal. Allow active imports to
finish before stopping; if 120 seconds expires, Docker can force-kill the process.

### Permissions, backups and restore

A new empty directory is initialized with mode `0750`. For existing nonempty
data, initialization stops on incompatible ownership, unreadable files or
symlinks. After stopping the service, an administrator must prepare **only that
dedicated instance directory** for numeric UID/GID `10001:10001`; do not apply
recursive ownership changes to shared directories, use `chmod 777`, or run the
application as root. Preserve existing data before changing permissions.

Back up the **entire data directory after graceful shutdown**, not just the JSONL
export or SQLite main file. Use your method's Stop command above, then verify
exit code **0** (`docker inspect --format '{{.State.ExitCode}}' remember-me`, or
`docker compose -f compose.yaml ps -a` for Compose). A successful stop command
alone does not prove a clean exit. If force-killed, start and stop cleanly before
backing up. Record the image version/digest, data path and port with the backup.

```bash
sudo tar --numeric-owner -C "$REMEMBER_ME_DATA_DIR" -cpf "$PWD/remember-me-backup.tar" .
```

Restart using the table above. Protect the archive: it contains private memories.
To restore, stop the instance, create a **new empty** directory and preserve
numeric ownership/permissions when extracting:

```bash
mkdir "$PWD/restored-data"
sudo tar --numeric-owner --same-owner -C "$PWD/restored-data" -xpf "$PWD/remember-me-backup.tar"
export REMEMBER_ME_DATA_DIR="$PWD/restored-data"
```

Update any saved `.env` data path too. Recreate the container with the restored mount: for `docker run`, remove the
stopped container, rerun the initializer and start commands; for Compose, run
`docker compose -f compose.yaml up -d --wait --wait-timeout 300`. Check health,
configuration, memory details and semantic search before retiring the original
copy. Never run both copies as writers to the same directory.

<a id="docker-updates"></a>
### Manual upgrades and rollback

Read the target release's compatibility notes and take a stopped full backup.
Keep the old image/version and backup. Set `REMEMBER_ME_IMAGE` to the chosen
published full version or digest (also update `.env` if used), then pull it:

```bash
docker pull --platform linux/amd64 "$REMEMBER_ME_IMAGE"
```

For `docker run`, pulling alone does not upgrade a running container. After
stopping and backing up, remove the old container, then repeat the initialization
and start steps above from your deployment records, changing only the image
reference. Preserve the actual mounts, ports, environment, UID/GID, read-only
filesystem and security options; do not replace custom settings with examples.

For Compose, keep the existing `compose.yaml`, data path, ports and settings in
the original instance directory. Set and export `REMEMBER_ME_IMAGE` to the verified
target full version and **update its saved value in `.env` too**. Exported shell
values override `.env`. After stopping and backing up, run:

```bash
docker compose -f compose.yaml up -d --pull always --wait --wait-timeout 300
```

The changed image setting recreates the service. Check health and retrieve an
existing memory. Read all relevant Upgrade Notes / Breaking Changes first;
version notifications do not prove data compatibility or perform updates or migrations.

Rollback follows the same steps with the previous version/digest. Reuse current
data only when that release's database schema and Embedding Profile are compatible.
Otherwise restore the matching full backup into a new directory and run its
matching image; post-backup changes are not included. Local recreation tests do
not establish compatibility between arbitrary future versions.

## Connect & Enable Agent Memory

Connecting MCP gives an agent the **ability** to use memory. Giving it good memory instructions helps it decide **when and how** to do so. Both steps matter.

### Recommended: let your coding agent set up MCP

After starting Remember Me, ask a coding agent that can access the local service and edit its own configuration:

> Read `http://127.0.0.1:1314/read-me`. Follow its instructions to configure Remember Me MCP for yourself and verify the connection. Ask me before modifying configuration files.

The `/read-me` endpoint is written for agents. For manual setup and troubleshooting, the Web UI **Help** page provides detailed guidance.

### Manual examples

**Codex — Streamable HTTP**

```bash
codex mcp add remember-me --url http://127.0.0.1:1314/mcp
codex mcp list
```

**OpenCode — SSE** (merge this example into your OpenCode configuration, preserving any existing MCP entries):

```json
{
  "mcp": {
    "remember-me": {
      "type": "remote",
      "url": "http://127.0.0.1:1314/sse",
      "enabled": true
    }
  }
}
```

For configuration file locations and client-version-specific details, use the Web UI Help. A remote agent cannot reach `127.0.0.1` on your computer unless an appropriate network path has been configured.

### Give the agent a memory strategy

**A successful MCP connection does not make an agent automatically remember everything or consistently recall the right experience.** Its memory strategy matters.

The **Help** page includes a ready-to-copy memory instruction prompt. Place it in your agent's instruction file (for example, `AGENTS.md`) and adjust it for the project. The guidance covers proactive recall, saving useful decisions and lessons, avoiding duplicate memories, updating existing information, and selecting the right namespace.

### Verify persistence across sessions

1. Ask your agent to save a harmless test memory in a chosen project namespace.
2. Start a new agent session and ask it to recall that memory from the same namespace.
3. Open the Web UI to see the memory and any available retrieval activity.
4. Remove the test memory when finished, if desired.

## Documentation & Security

- **Web UI → Help:** setup, agent configuration, memory prompts, and troubleshooting.
- **`/read-me`:** machine-readable, agent-oriented connection guidance from the running service.

Remember Me is intended to run locally by default. **Do not expose memory or administrative endpoints directly to the public internet without authentication and appropriate access controls.** Namespaces organize memory; they are not an authentication or authorization boundary.

## About This Project

Remember Me is an independent personal open-source project developed with the assistance of AI coding agents.

## License

Remember Me's original project code is released under the [Apache License 2.0](LICENSE), subject to its terms, including preservation of applicable attribution and notices.

- [LICENSE](LICENSE) — project license.
- [NOTICE](NOTICE) — original project attribution.
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) — third-party code, models, and asset notices.

Python installations download model files separately; the Docker image includes BGE-M3 with its [MIT license](docker/licenses/BGE-M3-MIT.txt) and [attribution](docker/licenses/BGE-M3-NOTICE.txt). Model weights, tokenizers, and caches remain subject to their upstream terms; the project license does not relicense them.

Created by **shenyu1997** · Published via [tech-kuiperbelt](https://github.com/tech-kuiperbelt).
