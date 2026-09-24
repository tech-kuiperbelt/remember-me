# [29] Streamable HTTP 传输与 /read-me 配置发现页

2026-09-23 计划；**未实现**。为 codex 提供符合新规范的 Streamable HTTP 传输，并新增面向 LLM 的静态配置发现页 `/read-me`。

## 背景与症状

- 当前 `python -m app.server --port 1314` 只暴露 MCP 旧版 HTTP+SSE：`GET /sse` + `POST /messages/?session_id=…`（`mcp.server.sse.SseServerTransport`）。`POST /mcp` 返回 404；`POST /sse` 会返回 `text/event-stream` 并挂起，无 Streamable HTTP 语义。
- 消费者：opencode 用 `{type: remote, url: .../sse}`；codex 用 `npx mcp-remote@0.4.1 .../sse --transport sse-only` 的 stdio 桥接，被迫降级到旧传输。
- 症状：codex 报告 memory MCP 的 SSE 传输“不够新”。codex-cli 0.156.1 基于 rmcp 3.2.0，`mcp add --url` 的官方语义即 “URL for a streamable HTTP MCP server”，SSE 被标记为 legacy。
- 服务端能力已具备：`mcp 1.28.0` 的 `LATEST_PROTOCOL_VERSION=2025-11-25`，`StreamableHTTPServerTransport` 与 `StreamableHTTPSessionManager` 均可导入，与 codex 支持的协议版本有交集。
- 单主库 writer lock 使“每客户端起一个 stdio 实例”不可行，多客户端必须共享同一个 HTTP 实例。

## 范围与不变量

- **只增不删（开闭原则）**：`stdio`、`/sse`、`/messages/`、现有 9 个工具与审计契约全部保留，不得删改。
- **不改动任何客户端配置**：不修改 codex 的 `~/.codex/config.toml`；codex 自行读取 `/read-me` 后完成配置。opencode 保持现状。
- 不引入鉴权。
- 不改业务逻辑与存储事务。

## 决策

| 项 | 决定 |
|---|---|
| Streamable HTTP 端点路径 | `/mcp` |
| 会话模型 | `stateless=True` + `json_response=True`（无 `Mcp-Session-Id`，每请求独立，无长连需 drain） |
| `/read-me` 端口来源 | `main()` 显式把 `args.port` 传入 `create_http_app` |
| `/read-me` 内容存放 | 独立模板文件 `app/read_me.md`（含 `{base}` 占位） |
| `/read-me` 主机 | 规范 `127.0.0.1`（`localhost` 等价，可旁注） |

## 交付物 1：`/mcp` Streamable HTTP

改动集中在 `app/server.py`：

- `create_http_app(runtime, *, port=None)` 内构造 `StreamableHTTPSessionManager(app=mcp.server, stateless=True, json_response=True)`，与现有 SSE 共用同一个 `mcp.server` 实例。
- lifespan 组合：`runtime.start()` → `async with manager.run():` → `yield` → 退出时 manager 清理 → `runtime.close()`。`StreamableHTTPSessionManager.run()` 是 asynccontextmanager 且每实例只能进入一次。
- 新增 `Route("/mcp", endpoint, methods=["GET", "POST", "DELETE"])`，endpoint 直接 `await manager.handle_request(scope, receive, send)`。
- `/sse`、`/messages/`、admin 路由原样保留；`AdmissionMiddleware` 自动覆盖 `/mcp`（服务非 running 时 503）。
- stateless 无长连，`[38]` 的关闭/重启 drain 语义天然成立；现有 `close_connections()` 仍只处理旧 SSE。

## 交付物 2：`/read-me`

- 新增模板 `app/read_me.md`，静态内容 + `{base}` 占位。
- 新增 `GET /read-me`：读模板，注入 `base = http://127.0.0.1:<port>`，返回 `text/markdown` 单页。
- 端口来自 `main()` 传入的 `args.port`，用户改 `--port` 时自动跟随，不硬编码。
- 只读、无鉴权；服务关闭中随全局中间件回 503，不做豁免。
- 定位：回答“怎么接”与“能干什么”。工具/资源的权威清单由 agent 连上后经 MCP `tools/list` 自取，本页只给概要。

### 内容结构（固定小节）

0. 服务地址（自动侦测端口，自引用）
1. 概述（这是什么、能干什么）
2. 三种接入方式（统一字段：名称 / 适用场景 / 传输 / 地址或命令 / 客户端配置要点 / 限制）
3. 推荐
4. 功能概览（9 工具 + 2 资源，概要级）
5. 快速验证
6. 注意事项

### 三种接入方式

| | stdio | 旧 HTTP+SSE | Streamable HTTP |
|---|---|---|---|
| 适用 | 单客户端、本地 | opencode 现状 | 多客户端并发共享 |
| 地址/命令 | 仓库 `bin/start.sh` | `http://127.0.0.1:<port>/sse` | `http://127.0.0.1:<port>/mcp` |
| 限制 | **无法多客户端并发共享** | codex 视为 legacy | — |

推荐：多客户端并发共享 → Streamable HTTP；单客户端本地 → stdio；旧 HTTP+SSE 仅保留兼容。

### 写作规则（“LLM 好理解、好配置”）

每种配置给可直接复制的最小块；显式标注推荐/不推荐；术语与字段名一致可预测；内容自包含。

## 测试

- 新增 `tests/test_streamable_http.py`：
  - 用 `mcp.client.streamable_http.streamablehttp_client` 连 `/mcp`，完成 `initialize / tools/list / tools/call(stats)`。
  - 两个客户端并发连接同一 `/mcp`。
  - `GET /read-me`：200、含 `127.0.0.1:<port>`、三种配置方式、推荐、stdio limitation、9 个工具名。
  - 服务关闭中 `/mcp` 与 `/read-me` 均返回 503。
- 回归：旧 `/sse`、`stdio`、工具/审计契约（现有测试）；全量 `pytest`；`tests/run_admin_browser.py`。

## 文档

- `AGENTS.md`：传输表新增 Streamable HTTP 行；新增 `/read-me` 事实；工具变更 checklist 增加“同步 `/read-me`”。
- `README.md`：`/read-me` 用法与 codex 原生 `url` 接入示例。
- `docs/project-management/backlog-todo.md`：[29] 完成后移入 `backlog-done.md`；顺带归档仍内联的 [34] 条目。

## 风险与回滚

- 风险：`/read-me` 为静态内容，工具增减/传输变更/端点路径变化会漂移，靠 checklist 与维护约定缓解；stateless 模型与个别客户端兼容性需实测。
- 回滚：两条新路由相互独立，删除路由即回滚，旧传输零影响。

## 实施顺序

1. 交付物 1（`/mcp`）+ 测试。
2. 交付物 2（`/read-me`）+ 测试。
3. 文档同步。
4. 全量验证。
5. 经用户许可后提交。
