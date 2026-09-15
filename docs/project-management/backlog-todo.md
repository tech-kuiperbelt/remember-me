# Backlog TODO

本文件是 remember-me 项目的开放待办清单。完成项移入 `backlog-done.md`。

## 当前待办

### [31] NDJSON 灾备副本的多进程一致性与真实健康状态

- 已确认的问题：2026-09-11 多个 `dual` 服务实例共享同一 SQLite/JSONL 时，一个实例用自身过期的 NDJSON 内存快照执行 compact，覆盖了其他实例已经追加的记录；SQLite 中这些记录已经被标记 `synced=1`，因此 outbox 不会重试。最终主库 918 条、副本 912 条，而 Stats 仍错误显示 `in_sync=true`。2026-09-14 已通过 Full Sync 恢复为 918/918。
- 当前缓解：Codex/OpenCode 客户端统一连接单个 `127.0.0.1:1314` 服务，不再为每个会话直接启动 `bin/start.sh`；但底层实现仍没有跨进程安全保证。
- 目标：明确并实现单写者约束或可靠的跨进程协调；append、incremental sync、compact 和 Full Sync 不得相互覆盖，compact 必须以 SQLite 权威快照或加锁后重新读取的完整副本为输入，不能直接发布可能过期的进程内 `_data`。
- 健康检查：`in_sync` 不能只依据 `pending == 0`；至少校验主副有效记录数，管理操作应支持精确 ID 差集/完整性校验，并明确报告 missing、extra、损坏行和最后一次完整校验时间。
- 恢复策略：发现漂移时不得继续报告健康；提供安全的 Full Sync 修复提示，并评估启动时低成本自动核验或定期深度核验。
- 验收：增加多进程/强制交错测试，覆盖一个实例追加、另一个实例 compact，两个实例竞争 outbox、compact 与 Full Sync 并发、进程在 append/mark-synced 边界退出，以及 Stats 对副本漂移的识别；单实例现有行为与离线恢复不回归。
- 2026-09-14 前置修复已随 [30] 实现：每个 replica path 使用生命周期 OS writer lock，第二个 dual 进程启动即失败；Full Sync 从 SQLite 快照构建；Stats 精确扫描磁盘 JSONL 并报告 missing/extra/corrupt_lines/checked_at，任一异常时 `in_sync=false`。剩余项是更广泛的强制交错、崩溃边界和定期深度核验测试，不阻断单写者部署下的 [30]。

### [29] MCP Streamable HTTP（新 SSE）传输支持

- 背景：当前 `--port` 暴露的是 MCP 旧版 HTTP+SSE 传输（`mcp.server.sse.SseServerTransport`，`GET /sse` + `POST /messages/`）；新规范以 Streamable HTTP 取代（单端点、POST/GET、可选 SSE 流）。
- 目标：保留旧 `/sse` 兼容的同时新增 Streamable HTTP 端点，供新客户端接入；mcp 1.28.0 已内置 `mcp.server.streamable_http` / `streamable_http_manager`，可评估直接使用或改用 FastMCP。
- 关注点：会话管理（Mcp-Session-Id）、GET 流式订阅与断线重连、与 stdio 双模式共存、调用来源关联（复用 [25]）；同步更新 `.env`/`@remember-me`/MCP 客户端配置与 AGENTS.md。
- 验收：支持 Streamable HTTP 的客户端可完成 initialize/tools/list/tools/call；旧 `/sse` 客户端无回归；`pytest` 通过。

### [28] namespace 规范化与别名治理

- 定义受控 namespace 标识及显示名，统一 `atos-next-master`、`atos-next`、`ATOS Next` 等历史变体。
- 提供迁移/别名策略，并在 MCP 写入与查询时拒绝或明确提示非规范 namespace，避免静默空检索和数据孤岛。

### [27] LLM 结构化检索与分类写入引导

- 调整 MCP 工具描述与 hint：写入时要求/引导提供 1–3 个 categories，检索时说明 keywords/categories 精确优先、语义解析预过滤的适用场景。
- 基于 audit 复核采用率与检索效果，避免仅靠文案而未形成实际行为改变。

### [26] report 反馈可信度与来源标记

- 校验 `search_id` 存在，且每个 `m_id` 属于该次搜索结果；拒绝或明确记录无效反馈。
- 为反馈记录来源/用途（LLM、人工、测试等）和必要的关联上下文，区分可用于质量分析的真实标注与测试数据。

### [25] audit 调用关联与可用性状态

- 在 audit 中记录 caller/client、session/request ID，并让 `get_details`、`update`、`report` 等无 namespace 参数的事件可关联至原始 namespace/搜索链路。
- 对 embedding、迁移、锁库等不可用状态提供可被 LLM 明确识别的状态与恢复提示，避免失败后盲目重试。

### [23] keywords 词表治理与黑名单

- 建立独立的 canonical keyword vocabulary，支持展示、查询、新增、改名/合并、停用/删除。
- 增加 blacklist，明确新记忆、迁移和历史数据命中黑名单时的处理。
- 设计 alias/merge、归一化、管理 API/UI、权限审计及现有关键词迁移策略。

### 其他开放项

- [19] memory priority/置顶字段。
- [15] 通过共享索引/实体建立记忆间关联网络。
- [16] 关键词收敛与低频词表裁剪的目标冲突。
- [9] keywords 浓度/词频排序及自动提取研究（暂不实现）。

## 维护约定

- 以本文件为项目管理 TODO 的权威来源，不把完整 backlog 正文写入记忆系统。
- 完成项移入 `backlog-done.md`，并在提交记录中注明 commit 和验证结果。
- MCP 工具变更必须同步考虑 audit。
