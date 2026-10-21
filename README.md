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

Existing installations retain their active database embedding profile when no Provider is explicitly configured, including historical Ollama profiles. `bin/start.sh` loads a local `.env` if present. Changing Provider or model requires an explicit target and offline reindexing; see the [operations guide](docs/operations/runbook.md).

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

The Web UI proxy settings apply to model downloads. Installing Python dependencies with pip requires its own network or proxy configuration. Automatic model downloads are controlled by `auto_download_model`; when disabled, use the configuration page to request a retry explicitly.

## Docker (local AMD64 image)

Phase 2.2 provides `compose.yaml` for the locally built `remember-me:phase2.2-amd64` image. Run `docker compose up -d`, then open <http://127.0.0.1:1314>. Data and configuration persist in `./data`; the model is included in the image. A public registry release is pending. See the [deployment, permissions, backup and restore guide](docs/operations/docker.md).

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
- **[Operations guide](docs/operations/runbook.md):** backup, recovery, and maintenance procedures.

Remember Me is intended to run locally by default. **Do not expose memory or administrative endpoints directly to the public internet without authentication and appropriate access controls.** Namespaces organize memory; they are not an authentication or authorization boundary.

## About This Project

Remember Me is an independent personal open-source project developed with the assistance of AI coding agents.

## License

Remember Me's original project code is released under the [Apache License 2.0](LICENSE), subject to its terms, including preservation of applicable attribution and notices.

- [LICENSE](LICENSE) — project license.
- [NOTICE](NOTICE) — original project attribution.
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) — third-party code, models, and asset notices.

Model weights, tokenizers, and caches are downloaded separately and remain subject to their upstream terms; the project license does not relicense them.

Created by **shenyu1997** · Published via [tech-kuiperbelt](https://github.com/tech-kuiperbelt).
