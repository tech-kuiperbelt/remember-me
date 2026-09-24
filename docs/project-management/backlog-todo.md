# Backlog TODO

本文件是 remember-me 项目的开放待办清单。完成项移入 `backlog-done.md`。

## 当前待办

### [34] 已完成，详见 `docs/project-management/plans/34-operations-runbook.md`

- 背景：embedding 维护、keyword/category 迁移和 JSONL recovery 目前分别暴露 Python module 命令；`bin/recover.sh` 只初始化 venv，没有像 `bin/start.sh` 一样加载 `.env`。停服务、选择正确 provider/path、备份、重启和健康检查仍依赖人工拼装命令。
- 目标：在 `bin/` 下提供统一且可发现的运维入口或职责清晰的脚本，复用同一套项目目录、`.venv`、`.env`、SQLite/JSONL 默认路径和错误处理；覆盖 embedding `status`/codec migration/reindex/index-value reindex/activate、keyword/category dry-run 与正式迁移、JSONL→SQLite recovery。
- 安全约束：明确区分在线只读与离线写操作；写操作必须依赖主库 writer lock fail-fast，不自动 kill 服务、不删除 lock file。恢复和高风险维护前提示或检查备份，失败不得覆盖原主库；脚本参数必须能覆盖默认数据库、replica、profile、namespace 和 batch size。
- Runbook：新增根目录 `README.md`，说明推荐启动方式、在线可执行操作、标准“Full Sync/Export → 正常停服 → 运维/迁移/恢复 → 重启 → Stats 验证”流程、embedding profile 配置一致性，以及 `.writer.lock` 文件存在不等于锁被占用。
- 验收：所有脚本支持 `--help`，从任意工作目录调用均可正确定位项目并加载环境；只读命令可在服务运行时成功，写命令在服务运行时明确失败，停服后成功；README 示例与实际 CLI 一致，并覆盖 `pending=0`、`in_sync=true`、missing/extra/corrupt_lines 为零的验收检查。

### [29] MCP Streamable HTTP（新 SSE）传输支持

- 计划：详见 `docs/project-management/plans/29-streamable-http-and-read-me.md`（2026-09-24 review 修订，未实现；明确 GET 405、Origin 校验、stateless 审计边界、SDK 下限和真实 Codex 无桥接直连验收；含 `/read-me` 配置发现页）。
- 背景：当前 `--port` 暴露的是 MCP 旧版 HTTP+SSE 传输（`mcp.server.sse.SseServerTransport`，`GET /sse` + `POST /messages/`）；新规范以 Streamable HTTP 取代（单端点、POST/GET、可选 SSE 流）。
- 目标：保留 stdio、旧 `/sse` 与 `/messages/`，新增 `/mcp`，让 Codex 原生 URL 直连同一实例，无需 `mcp-remote` 桥接；使用 SDK `StreamableHTTPSessionManager`，stateless + JSON，非 POST 方法校验后 405（`Allow: POST`），不提供独立 GET SSE 流。
- 关注点：复用 SDK Host/Origin 校验（本次 loopback Host 范围），验证 task group 的审计 contextvar 传播和每请求关联 ID，明确无跨请求 clientInfo 的限制；依赖统一为 `mcp>=1.28.0,<2`，覆盖关闭/重启排空。新增 `/read-me` 与 README/AGENTS.md 配置说明，不改用户持久 MCP 客户端配置。
- 验收：最低 SDK 版本、Python SDK 与双客户端调用、旧 `/sse`/stdio、审计及生命周期回归、全量 pytest 和浏览器回归。真实 Codex 直连由用户自行实测（不阻塞归档）；未通过不得宣称 Codex 原生直连目标完成。

### [27] LLM 结构化检索与分类写入引导

- 调整 MCP 工具描述与 hint：写入时要求/引导提供 1–3 个 categories，检索时说明 keywords/categories 精确优先、语义解析预过滤的适用场景。
- 基于 audit 复核采用率与检索效果，避免仅靠文案而未形成实际行为改变。

### [26] report 反馈可信度与来源标记

- 校验 `search_id` 存在，且每个 `m_id` 属于该次搜索结果；拒绝或明确记录无效反馈。
- 为反馈记录来源/用途（LLM、人工、测试等）和必要的关联上下文，区分可用于质量分析的真实标注与测试数据。

### [23] keywords 人工合并（后续）

- 优先讨论人工选择标准词、预览影响范围、合并历史关键词及别名处理。
- 黑名单暂缓，待确认低价值词持续写入的问题；暂不做独立准入词表、审批、停用等治理。
- UI/MCP 词表拆分和批量语义找词已单独实现为 [36]，不代表合并已完成。

### 其他开放项

- [19] memory priority/置顶字段。
- [15] 通过共享索引/实体建立记忆间关联网络。
- [16] 关键词收敛与低频词表裁剪的目标冲突。
- [9] keywords 浓度/词频排序及自动提取研究（暂不实现）。

## 已明确不做

- [35] namespace alias、改名与历史变体合并：2026-09-16 决定不做。[28] 只保留存在性治理（未知 namespace 拒绝 + 确认码），不引入 alias、改名或历史变体（如 `atos-next-master`/`atos-next`）合并。

## 维护约定

- 以本文件为项目管理 TODO 的权威来源，不把完整 backlog 正文写入记忆系统。
- 完成项移入 `backlog-done.md`，并在提交记录中注明 commit 和验证结果。
- MCP 工具变更必须同步考虑 audit。
