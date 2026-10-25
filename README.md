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

Remember Me runs locally. Choose one installation path; both keep configuration,
memories, logs and caches in a data directory you control.

- **SOURCE — Git + Python 3.10+:** [Source installation and upgrade](docs/installation/source.md).
  Uses Sentence Transformers with BAAI/bge-m3 on CPU; model weights are downloaded
  on first use.
- **Docker — Linux AMD64:** [Docker installation and upgrade](docs/installation/docker.md).
  The image includes BGE-M3 and runs it on CPU, so no Python or separate embedding
  service is required.

The guides cover install and start, stop, the data directory, backup and restore,
upgrade and rollback. Once the service is running, open
<http://127.0.0.1:1314/admin/console> (Web UI) or `/read-me` for agent setup, then
continue with [Connect & Enable Agent Memory](#connect--enable-agent-memory).

### Upgrades and rollback

<a id="source-updates"></a>
**SOURCE:** [installation, upgrade and rollback](docs/installation/source.md#upgrade-and-rollback)

<a id="docker-updates"></a>
**Docker:** [installation, upgrade, backup and rollback](docs/installation/docker.md#upgrade-and-rollback)

Read all relevant Upgrade Notes / Breaking Changes before updating. Keep the old
version and a stopped, full backup of the data directory.

## Version numbering

Remember Me uses `MAJOR.MINOR.PATCH` versions. The **minor** number identifies
the release series: even minor numbers (`1.2.x`, `1.4.x`) are stable release
series; odd minor numbers (`1.3.x`) are development/prerelease series. Patch
numbers have no parity restriction: `1.2.1` is a valid stable version.

This is Remember Me's project convention. The current release workflow only
publishes stable versions with an even minor number; publishing odd-minor series
or versions such as `1.3.0-rc.1` is not supported yet. Version checks use the latest
official stable GitHub Release. SOURCE checkouts may contain development code;
their displayed version still comes only from the shipped `release.jsonc`.
The numbers above are examples, not announcements of available releases.

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
