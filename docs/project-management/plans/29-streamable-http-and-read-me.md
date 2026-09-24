# [29] Streamable HTTP 传输与 /read-me 配置发现页

2026-09-23 初稿，2026-09-24 按 review 修订；**未实现，未完成 Codex 直连验收**。为 Codex 提供符合新规范的 Streamable HTTP 传输，并新增面向 LLM 的静态配置发现页 `/read-me`。

## 目标与关键点（实施前必读）

**核心目标：Codex 原生通过 `http://127.0.0.1:<port>/mcp` 连接同一个 memory-server，完成工具发现与调用，不再依赖 `mcp-remote` 或其他 stdio 桥接。** `/read-me` 是配置说明入口；真正提供协议能力的是 `/mcp`。

1. **协议名称是 Streamable HTTP**，不是给旧 `/sse` 换路径。旧 `/sse` 与 `/messages/` 保留，opencode 现有连接继续工作。
2. **stateless + JSON 不等于没有 GET 长连接。** 当前 SDK 仍会为 GET 开 SSE；新端点必须显式让 GET 返回 405，才能采用本计划的无独立 SSE 长连接方案。
3. **不引入鉴权，但必须校验新端点的 Origin。** SDK 的默认值不能替代显式配置；非法 Origin 返回 403。
4. **stateless 无跨请求客户端会话信息。** 工具审计仍完整记录，但新传输不能假装保留 initialize 的 clientInfo 或稳定 session_id；具体语义见下文。
5. **依赖版本和真实客户端都是交付条件。** 更新 SDK 最低依赖，Python SDK 测试通过后，还必须用真实 Codex 做无桥接直连验收。
6. **本次只修订计划。** 实施与验收时不改用户持久客户端配置，不以“配置已登记”或“HTTP 200”代替工具调用成功。

## 背景与症状

- 当前 `python -m app.server --port 1314` 只暴露 MCP 旧版 HTTP+SSE：`GET /sse` + `POST /messages/?session_id=…`（`mcp.server.sse.SseServerTransport`）。`POST /mcp` 返回 404；`POST /sse` 会返回 `text/event-stream` 并挂起，无 Streamable HTTP 语义。
- 消费者：opencode 用 `{type: remote, url: .../sse}`；codex 用 `npx mcp-remote@0.4.1 .../sse --transport sse-only` 的 stdio 桥接，被迫降级到旧传输。
- 症状：codex 报告 memory MCP 的 SSE 传输“不够新”。codex-cli 0.156.1 基于 rmcp 3.2.0，`mcp add --url` 的官方语义即 “URL for a streamable HTTP MCP server”，SSE 被标记为 legacy。
- 服务端能力已具备：`mcp 1.28.0` 的 `LATEST_PROTOCOL_VERSION=2025-11-25`，`StreamableHTTPServerTransport` 与 `StreamableHTTPSessionManager` 均可导入，与 codex 支持的协议版本有交集。
- 单主库 writer lock 使“每客户端起一个 stdio 实例”不可行，多客户端必须共享同一个 HTTP 实例。

## 范围与不变量

- **兼容现有入口**：`stdio`、`/sse`、`/messages/`、现有 9 个工具及其响应契约全部保留；旧传输审计语义不变。新传输的 stateless 审计边界必须显式记录和测试。
- **不改动用户持久客户端配置**：不修改 Codex 的 `~/.codex/config.toml`；opencode 保持现状。真实 Codex 验收由用户自行实测（见 Q5），后续用户可按 `/read-me` 自行切换。
- 不引入鉴权。
- 不改业务逻辑与存储事务。

## 决策

| 项 | 决定 |
|---|---|
| Streamable HTTP 端点路径 | `/mcp` |
| 会话模型 | `stateless=True` + `json_response=True`；无 `Mcp-Session-Id`，每请求独立 |
| HTTP 方法 | POST 交给 SDK；其余方法安全校验后 405，`Allow: POST`；HEAD 无响应体，不创建独立 SSE 流 |
| 新端点安全 | 复用 SDK `TransportSecurityMiddleware.validate_request`；显式配置 Host/Origin 白名单，缺省 Origin 允许，合法 Host 下非法 Origin 403 |
| SDK 依赖 | `requirements.txt` 调整为 `mcp>=1.28.0,<2`，并验证最低版本；不依赖本机偶然安装的新版本 |
| `/read-me` 端口来源 | `main()` 显式把 `args.port` 传入 `create_http_app` |
| `/read-me` 内容存放 | 独立模板文件 `app/read_me.md`（含 `{base}` 占位） |
| `/read-me` 主机 | 规范 `127.0.0.1`（`localhost` 等价，可旁注） |

## 交付物 1：`/mcp` Streamable HTTP

改动涉及 `app/server.py`、`app/audit.py`（按新增上下文需要）与 `requirements.txt`：

- `create_http_app(runtime, *, port=None)` 内构造 `StreamableHTTPSessionManager(app=mcp.server, stateless=True, json_response=True, security_settings=...)`，与现有 SSE 共用同一个 `mcp.server` 实例；共用 runtime/provider/storage，不另起 writer。
- lifespan 组合：`runtime.start()` → `async with manager.run():` → `yield` → 退出时 manager 清理 → `runtime.close()`。外层保留 `try/finally`，确保启动或 manager 异常仍释放资源。`run()` 每实例只能进入一次，不复用已经退出的 manager。
- 新增 `/mcp` 的 ASGI callable 实例（参照现有 `SSEEndpoint`，避免把三参数 ASGI 函数误当成 Starlette 的 `endpoint(request)`）。使用 `Route("/mcp", endpoint_instance, methods=None)`，让方法选择由 callable 统一执行，避免 Starlette 提前返回 405 绕过校验。安全检查后，POST 调用 `manager.handle_request(scope, receive, send)`；其余方法返回 405、`Allow: POST`，HEAD 无响应体，不进入 manager 的 GET 流程。
- `/sse`、`/messages/`、admin 路由原样保留；`AdmissionMiddleware` 自动覆盖 `/mcp`（服务非 running 时 503）。
- **关闭关键点**：GET 405 只消除了新端点的独立 SSE 流，正在执行的 POST 仍需 Uvicorn 排空。保留 `[38]` 的顺序：拒绝新工作 → 关闭旧 SSE → 等待已接收 HTTP 工作 → manager 清理 → runtime 释放资源 → restart exec。不得在请求完成前关闭 storage/provider。

### Origin/Host 校验边界

- 为新 `/mcp` 显式启用 `TransportSecuritySettings`；本次接入范围为本机，允许 Host `127.0.0.1:*`、`localhost:*`，Origin `http://127.0.0.1:*`、`http://localhost:*`。不新增通配任意主机/来源的配置。
- 从 `mcp.server.transport_security` 导入 `TransportSecurityMiddleware`，与 manager 共用一份显式 `TransportSecuritySettings`。在 callable 中先执行 `await security.validate_request(Request(scope, receive), is_post=False)`；有错误响应即返回，通过后再分派方法。这里先只校验 Host/Origin，不读 body；POST 的 Content-Type 与协议校验仍由 manager 内部 SDK 执行。POST 重复执行同一白名单检查可以接受，不访问 manager/transport 的私有字段以绕过它。
- 无 Origin 的正常 CLI 请求允许；合法 Host 下带非法 Origin 的请求返回 403，非法 Host 按 SDK 优先返回 421。**GET/HEAD/DELETE 等方法即使最终返回 405，也必须先做安全校验。** 服务关闭时仍由外层 admission 优先返回 503。
- 保留现有监听方式，不改变旧 SSE/admin 的访问策略；远程 IP/域名接入不属于本次验收范围，未来需显式扩展白名单。
- 依据：[MCP 2025-11-25 Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)。该规范允许不提供独立 SSE 流时 GET 返回 405；POST 返回 JSON 仍是 Streamable HTTP。

### 审计关键点：明确新传输的能力边界

- 继续通过现有 `handle_call_tool` 与 runtime journal 记录每次工具调用；`search_id`、`source_search_id`、`keyword_query_id` 等业务关联字段不变。
- 新传输显式标识 `transport=streamable_http`，保持 `source=mcp`。为每个 HTTP 请求保留服务端生成的唯一关联 ID（建议上下文字段 `http_request_id`），现有 `request_id` 继续保留 JSON-RPC ID；不能只靠不同客户端可能重复的 JSON-RPC ID 关联。
- 使用专供新传输的 `ContextVar`（默认 `None`）：callable 从 `scope["state"]["audit_request_id"]` 取 ID，在调用 manager 前 `set` 每请求新建的上下文字典，并在 `finally` 中 `reset(token)`；`_audit_context()` 显式读取并合并白名单字段 `transport/http_request_id`，仍自行构造 `source=mcp` 与 JSON-RPC `request_id`。不能仅 set 变量却指望 journal 自动合并显式 context。详细传播依据与实施第 0 步见 Q4。
- stateless 后续工具请求没有初始化时的 `clientInfo`：`client_name/client_version` 如实为空；`session_id` 如保留，只代表本次 SDK 临时 session，不能用于跨请求客户端归属。不从 IP、User-Agent 或旧请求缓存猜测客户端身份。
- 旧 stdio/SSE 的稳定 session 与 clientInfo 记录保持不变。新传输与旧传输分别断言，不能用旧回归测试代替新语义验证。
- **取舍边界**：本计划接受新传输不提供跨请求客户端身份关联。如果后续要求 Codex/opencode 按会话归属追踪，必须重新选择 stateful 模型并补生命周期设计，不能宣称当前 stateless 已支持。

## 交付物 2：`/read-me`

- 新增模板 `app/read_me.md`，静态内容 + `{base}` 占位。
- 新增 `GET /read-me`：读模板，注入 `base = http://127.0.0.1:<port>`，返回 `text/markdown` 单页。
- 端口来自 `main()` 传入的 `args.port`，用户改 `--port` 时自动跟随，不硬编码。
- 明确 `port=None` 或 `--port 0` 的边界：使用 ASGI `scope["server"]` 中的实际监听端口作为回退；无法获得有效端口时显式报错，不能生成 `:None` 或 `:0` 的可复制配置。主机始终固定为 `127.0.0.1`，不从请求 Host 拼接。
- 只读、无鉴权；服务关闭中随全局中间件回 503，不做豁免。
- 定位：回答“怎么接”与“能干什么”。工具/资源的权威清单由 agent 连上后经 MCP `tools/list` 自取，本页只给概要。

### 内容结构（固定小节）

0. 服务地址（跟随启动端口，自引用）
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

- Codex 原生示例使用 `[mcp_servers.memory-server]` 与 `url = "{base}/mcp"`，不包含桥接 command/args；注明替换旧桥接条目，避免同一服务重复注册。
- stdio 使用可执行的仓库绝对路径，并注明：同一主库已有 HTTP 服务运行时不能再启动 stdio writer；不仅仅是“单客户端限制”。
- 本机地址要求客户端与服务处于可访问同一 loopback 的运行环境；`/read-me` 被访问不会自动修改客户端配置。
- 模板只替换 `{base}`，避免 JSON 配置代码块的大括号被通用 `str.format` 误解析。

## 测试

- 新增 `tests/test_streamable_http.py`：
  - 用 `mcp.client.streamable_http.streamablehttp_client` 连 `/mcp`，完成 `initialize / tools/list / tools/call(stats)`。
  - 两个客户端并发连接同一 `/mcp`。
  - POST 返回 JSON，无 `Mcp-Session-Id`；初始化通知接受并返回 202；合法 Host/Origin 的 GET（含 `Accept: text/event-stream`）/HEAD/DELETE/PUT/OPTIONS 立即 405、`Allow: POST`，HEAD 无响应体，不出现 SSE 挂起。
  - 无 Origin、合法 Origin 可以调用；合法 Host 下非法 Origin 的 POST/GET/HEAD/DELETE 均 403，非法 Host（包括 LAN IP）421。验证安全检查优先于方法检查；不新增 CORS/OPTIONS 成功响应。
  - 连续两次工具调用、两个客户端使用相同 JSON-RPC ID 时，审计 HTTP 关联 ID 仍不同且不串线；断言 stateless clientInfo 为空、业务反馈关联仍有效、旧传输审计不变。
  - `GET /read-me`：200、含 `127.0.0.1:<port>`、三种配置方式、推荐、stdio limitation、9 个工具名。
  - 不同启动端口及 `None`/`0` 回退行为；配置块大括号完整、没有未替换的 `{base}`。
  - 服务关闭中 `/mcp` 与 `/read-me` 均返回 503。
- 扩展 `tests/test_runtime_transports.py`：真实 Uvicorn 子进程覆盖 shutdown/restart/SIGTERM；新 `/mcp` 与旧 SSE 同时存在时进程能退出，已接收工具请求在资源关闭前完成，writer lock 已释放，restart 只在清理后执行。测试使用临时数据库与 fake provider。
- 在隔离依赖环境验证 `mcp==1.28.0` 的导入、`--help`、stdio 与新传输 smoke，避免只在开发机当前依赖下通过。
- 回归：旧 `/sse`、`stdio`、工具/审计契约（现有测试）；全量 `pytest`；`tests/run_admin_browser.py`。

### 最终验收：真实 Codex 无桥接直连（由用户自行实测，不阻塞我方交付）

1. 启动隔离测试服务，使用临时数据库与独立端口；记录 Codex 和服务端 SDK 版本。
2. 按 Q5 使用 `-c` 进程级覆盖，新命名 `mem29test`（若已存在则换唯一名字），注册原生 `/mcp` URL；同时临时禁用继承的其他 MCP 入口及插件提供的桥接入口。沿用原登录环境，不重设 `CODEX_HOME`，不改用户持久配置、不启动 `mcp-remote`。真实模型调用须在额度授权明确后执行。
3. 用真实 Codex 成功完成初始化、发现全部 9 个工具并实际调用 `stats`；通过测试服务响应与审计记录确认调用到的是新端点，而非已有连接。
4. 验证旧 `/sse` 仍可调用且与新端点共享同一实例，并记录退出/重启结果。
5. **该真实 Codex 验收由用户自行在本机实测（2026-09-24 决定）**，我方不实现自动化隔离/验收脚本；若实施困难可直接跳过，不作为交付物 1/2 的完成条件。`codex mcp list`、URL 可访问、Python SDK 测试通过仍不能单独证明 Codex 原生直连。

Codex URL 配置依据：[OpenAI Docs MCP](https://developers.openai.com/learn/docs-mcp)；本机 `codex mcp add --help` 已确认 `--url` 用于 Streamable HTTP。

## 文档

- `AGENTS.md`：传输表新增 Streamable HTTP 行；新增 `/read-me` 事实；工具变更 checklist 增加“同步 `/read-me`”。
- `README.md`：`/read-me` 用法与 codex 原生 `url` 接入示例。
- 同步刷新 `backlog-todo.md` 的 [29] 正文：删除“同步更新 MCP 客户端配置”及已经放弃的会话/GET 订阅评估描述，统一为本计划已选方案；不能仅更新计划链接。
- `docs/project-management/backlog-todo.md`：[29] 全部验收完成后移入 `backlog-done.md`；[34] 的归档独立处理，不混入本次范围。

## 风险与回滚

- 风险：`/read-me` 静态概要可能漂移，靠 checklist 与内容测试缓解；stateless 的客户端兼容性、GET 405 行为须通过真实 Codex 验收；跨请求 clientInfo 缺失是明确限制。
- 回滚：撤回新路由、manager lifespan 接入及新传输专属审计/安全逻辑，保留既有 runtime 清理路径；恢复相应依赖与文档，并复跑 stdio/SSE 生命周期回归。不能仅删路由就假设旧传输零影响。

## 实施顺序

0. **先验证审计 contextvar 的真实传播路径**：跨 manager 的 task group 到工具 handler，再到显式审计 context；并发、异常、取消后无串线。2026-09-24 已完成 SDK 隔离探针，尚需项目级正式回归，详见 Q4。
1. SDK 依赖下限与交付物 1（`/mcp`）：方法限制、安全校验、审计语义、生命周期 + 测试。
2. 交付物 2（`/read-me`）+ 测试。
3. 文档同步。
4. 最低依赖版本与全量回归，记录证据。
5. 真实 Codex 直连由用户自行实测（Q5），不阻塞归档；经用户许可后提交。

## 实施问答与明确决策（2026-09-24）

### Q1. 安全校验怎么复用？

**选择 (a)：自建 SDK `TransportSecurityMiddleware`，调用 `validate_request`。** 它是可导入、无下划线的方法，但不视为长期稳定的顶层 API；把依赖限制在新 endpoint 的小适配层，并用最低版本与升级回归覆盖。自己复制白名单逻辑容易在端口匹配、缺省 Origin、错误码与校验顺序上漂移，本次不选 (b)。

同一份 settings 传给外层校验实例和 manager。外层所有方法先以 `is_post=False` 校验 Host/Origin；POST 的 Content-Type 校验由 SDK 内层完成。不要访问 SDK 的 `_security` 等私有实例属性。路由必须让非 POST 方法也进入 callable，不能设置 `methods=["POST"]` 后把安全检查留在 callable 内。调用顺序和状态码以正文为准。

### Q2. 405 的 Allow 头写什么？

**统一 `Allow: POST`。** Allow 描述本服务实际支持的方法，不照搬 SDK 的 `GET, POST, DELETE`。合法安全头的 GET/HEAD/DELETE/PUT/OPTIONS 都由适配层返回同一 405；不能让其中部分方法落入 Starlette 或 SDK 的兜底，产生另一套 Allow。安全失败的 403/421 与关闭中的 503 不伪装成 405。

### Q3. HEAD 也返回 405 吗？

**确认，`HEAD /mcp` 返回 405，且不发送响应体。** `/mcp` 是协议端点，不承诺 HEAD 探活；改为 200 会让其与“只支持 POST”的契约不一致。现有环境若用 HEAD 探测 `/mcp`，应调整为 `GET /read-me` 的可用性检查；要检查 MCP 能力则执行 initialize + stats。`/read-me` 可访问只证明 HTTP 页面可用，不能替代 MCP/存储健康验收。本次不新增健康检查 API。

### Q4. http_request_id 如何传递，是否先验证 contextvar？旧 source 标注是否修正？

**确认列为实施第 0 步；采用独立 ContextVar + `_audit_context()` 显式合并。** 现有 `AuditJournal.record` 对显式 context 优先，不会把 `request_audit_context` 自动合并进去，所以仅修改 middleware 或仅 set 变量都不够。

传播链固定为：Admission 生成 ID → 写入 ASGI scope state → `/mcp` callable 取出 ID、set 专用变量 → manager 在请求任务中调用 `task_group.start` → SDK 工具 handler → `_audit_context()` 合并 `transport/http_request_id` → journal。每请求新建值，不原地修改共享字典；callable 的 `finally` 必须 reset，包括异常、取消路径。显式 context 中保留 `source=mcp`，不把外层 `source=admin_http` 或 HTTP ID 覆盖到 JSON-RPC ID 上。

AnyIO 拷贝的是调用 `start/start_soon` 的任务上下文，不是创建 task group 的 lifespan 任务上下文；SDK 1.28.0 的 stateless 分支在当前请求任务中执行 `await self._task_group.start(...)`，因此这条路径具备传播条件。依据：[AnyIO Context propagation](https://anyio.readthedocs.io/en/stable/tasks.html#context-propagation)。

**本次已做隔离探针，结果通过**：MCP 1.28.0 / AnyIO 4.14.0，真实 manager + ASGI 请求 + SDK 工具 handler；两个并发请求共用 JSON-RPC ID `1`，分别携带 `rq_A/rq_B`，handler 在 await 前后均读到正确 ID；随后 `rq_C` 也正确，父上下文恢复为 `None`。这只验证 SDK 传播，不代表项目审计已实现；实施时仍须把实际 `handle_call_tool → AuditJournal`、异常/取消 reset、并发 ID 隔离固化成回归测试。若失败，不得降级为无 HTTP 关联 ID 而宣称完成。

**本次不顺手修改旧 `/sse` 的 middleware source 标注。** 它最终被 MCP 显式 context 正确覆盖，调整其底层缺省来源涉及旧路径其他日志语义，另列清理事项即可。新传输通过专用变量和显式合并解决，不扩大本次改动范围。

### Q5. Codex 验收如何隔离？是否已授权消耗额度？

**技术方案确认：使用 `-c` 进程级覆盖 + 新 server 名，沿用现有登录环境。** 示例形态如下（实施时替换 PORT，按实际继承条目补齐禁用项）：

```bash
codex -c 'mcp_servers.mem29test={url="http://127.0.0.1:PORT/mcp"}' \
      -c 'mcp_servers.memory-server.enabled=false' \
      exec '仅调用 mem29test 的 stats，报告返回结果，不修改文件。'
```

**新名字只避免同名字段合并，不会关闭其他 server。** 必须检查有效配置，并用进程级 `enabled=false` 覆盖禁用其他继承的 MCP server；若插件也提供 MCP/桥接入口，同样按当前 CLI 支持的配置禁用。不能把上面的单个禁用项视为对所有环境的完整隔离。验收前确认 `mem29test` 没有 command/args，已有桥接不会启动；结束后不需要改回持久配置。`-c` 用法依据：[Codex Advanced Configuration](https://learn.chatgpt.com/docs/config-file/config-advanced)及本机 `codex --help`。

不以临时 `CODEX_HOME` 作为首选，避免丢失当前认证/配置环境；也不为测试复制或输出认证文件。只读 `--help`/配置检查不调用模型。本次问答未启动 Codex 模型会话。

**Q5 改由用户自行用 Codex 实测（2026-09-24 决定）。** 我方不实现自动化隔离/验收脚本，也不消耗模型额度；若实施困难可直接跳过。真实 Codex 直连由用户在其本机自行验证，因此**不阻塞**交付物 1/2 的实现、测试与归档。上面的 `-c` 隔离方法仅作为用户实测时的参考，不再是我方交付条件。

### Q6. 是否接受整个项目的 mcp 下限抬到 1.28.0？

**接受，作为本计划的统一运行依赖决策。** `requirements.txt` 使用 `mcp>=1.28.0,<2`，不为离线维护另保留一套旧 MCP 兼容分支。使用该 requirements 安装/更新的维护环境也受此下限约束；只修改声明不会自动升级已经存在的虚拟环境。

实施时说明需要更新环境，并在最低版本下验证 server 导入、`--help`、stdio/HTTP smoke 和维护 CLI 的只读 `--help`/status 路径。抬高 SDK 下限本身不要求重建数据库或 reindex；本次也不借机执行生产维护或升级生产环境。兼容问题须解决后再交付，不能只依据开发环境已装 1.28.0 就跳过验证。

### Q7. 只允许 loopback Host，LAN IP 返回 421 是否预期？

**确认，这是本次原生直连接入范围的明确限制。** 标准客户端访问 `http://<LAN-IP>:<port>/mcp` 时 Host 不在白名单，返回 421；IPv6 loopback `[::1]` 也不在本次已列白名单中。Codex 与服务需能访问同一 `127.0.0.1`，不同容器/远程机器不能直接套用示例。

**Host 白名单不等于网络层只允许本机访问。** 服务仍绑定 `0.0.0.0`，远程请求者可以自行声明允许的 Host；这不是鉴权或来源 IP 限制，不能在文档中声称 `/mcp` 已经仅本机可达。保持旧监听方式是兼容性取舍，未来真正限制网络可达性需单独处理监听地址/网络访问控制；本次不改变旧 SSE/admin 的部署方式。

### 文档一致性：backlog [29] 正文是否一起刷新？

**确认，随本轮文档修订一起更新，而非留到实现后。** 删除“同步更新 MCP 客户端配置”，改为新增配置说明、仅临时进程覆盖验收；同步明确 `/mcp`、stateless + JSON、GET 405、安全和审计边界、真实 Codex 验收。保持 [29] 未实现状态，不归档为完成。
