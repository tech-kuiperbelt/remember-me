# Backlog TODO

本文件是 remember-me 项目的开放待办清单。完成项移入 `backlog-done.md`。

## 当前待办

### [30] `memory_import v2`：SQLite 跨库事务合并

- 当前边界：[22] 已删除 JSON storage，并将现有 Import 收窄为来源/目标 active profile 完全一致且两类来源向量完整的 SQLite→SQLite 原子复制；NDJSON→SQLite 灾难恢复独立走 `recover_cli`，不经过 import。
- v2 目标：支持来源与目标 embedding profile 不同的 SQLite 跨库合并，不复制、不信任来源 embedding；统一使用目标数据库 active profile 重新生成正文向量。
- 数据可信边界：只采用来源记录的正文、metadata、summary、namespace 和时间字段；不信任也不复制来源 embedding。无论来源 profile 是否与目标同维，都统一使用目标数据库的 active embedding profile 重新生成正文向量。
- 原子流程：读取来源并完成去重、namespace prefix 和 ID 冲突规划；预先生成全部待导入正文向量以及缺失的 keyword/category index-value 向量；随后在单一事务中写入记忆、正文向量、索引关系和索引值向量。任一 embedding 或数据库操作失败，目标数据库保持不变。
- 后续处理：事务成功后再为缺少 summary 的记录排队；dual 模式由既有 outbox 将成功提交的数据同步到 NDJSON；Import 作为管理操作补充独立 audit 事件和明确错误信息。
- 约束：来源格式仅支持 SQLite；不得削弱现有同 profile 原子复制或 `recover_cli` 的临时库构建、重新向量化和原子替换保证。
- 验收：覆盖来源/目标 profile 相同与不同、同维不同模型、正文或 index-value embedding 失败、重复正文、ID 冲突、namespace prefix、summary 排队、dual outbox 和整批回滚测试。

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
