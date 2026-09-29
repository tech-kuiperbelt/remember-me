# Backlog TODO

本文件是 remember-me 项目的开放待办清单。完成项移入 `backlog-done.md`。

## 当前待办

### [39] Retrieval Extension Phase 1 — Lexical Search

- 计划：详见 `docs/project-management/plans/39-memory-retrieval-lexical.md`；设计见 `docs/design/memory-retrieval-hld.md`、`memory-retrieval-detailed-design.md`、`memory-retrieval-pre-coding-validation.md`。
- 目标：为正文增加独立 Lexical Search primitive（SQLite FTS5 + BM25），与 Semantic 并列，不做 Hybrid/fusion。新增 `memory_content_fts`、单一 `lexical_index_version`、共用最小 QueryBuilder、`SQLiteStorage.search_lexical`、MCP `search_lexical` 与 `POST /api/memories/search/lexical`。
- 约束：仅 `query`/`k`（默认 10，范围 1–100）/`namespace`；query ≤4096 字符且 ≤256 token，超限拒绝不截断；纯标点空结果；`score=-raw_bm25`；复用现有 `search_id`/`report`；缺表/缺版本/版本不符时进入 running 前全量重建，失败即启动失败；写路径同事务同步 FTS。
- 验收：事务回滚、启动重建与失败路径、import/recovery、命名空间信封、query/k 边界、审计与 `/read-me` 工具列表同步、全量 pytest 与浏览器回归；Phase 1 tokenizer/BM25 benchmark（脱敏 query→positive、同一 DB 快照、分组 Hit@1/Hit@5/MRR/无结果率）。
- 状态：已实现并提交（`4b47dbe` + `dcc9dd1`），408 pytest + 浏览器回归通过，已部署并逐项验证；含 2 处 P2 review 修复（缺表恢复、lexical 未知 namespace 信封）。**Phase 1 benchmark 暂缓**（评测集已确认可用：284 query / 920 正例关联，165 含中文、273 含标识符；预估 1.5–2.5h，详见计划文档「评测集可行性与工作量」）。已知限制见计划文档「实现后观测与已知限制（2026-09-24 实测）」——中文不切字导致 `search_lexical` 对中文基本无效、短前缀（`ora`）会误召回、CamelCase 子词搜不到；工具描述与 `/read-me` 已补"中文请用 `search_semantic`"。

### [40] Retrieval Extension Phase 2 — Manual Anchors + Anchor Search

- 计划：详见 `docs/project-management/plans/40-memory-retrieval-anchors.md`；依赖 [39] 完成。
- 目标：调用方通过 `metadata.anchors` 提供 Anchor（≤5，严格校验），复用 `index_values`/`memory_index_values` 通用关系结构；新增独立 `memory_anchor_fts`、`search_anchor` 与 `POST /api/memories/search/anchor`；服务端不自动生成 Anchor。
- 隔离约束：`_INDEX_DIMENSIONS` 增加 `requires_embedding`；anchors 不写 `index_value_embeddings`，不参与完整性检查/回填/reindex/activate，`IndexValueResolver` 对其 semantic resolution 显式报错；Python 与 SQL 两层过滤；不新增 anchor 版本或状态机。
- 状态：主体已提交 `d41f149` 并在线生效；2026-09-28 完成写入引导收尾验收（引导改动未提交），433 pytest + 浏览器回归通过。`app/anchors.py`（严格 `normalize_anchors` / 宽松 `clean_anchors`）、`memory_anchor_fts`（`LEXICAL_INDEX_VERSION=3`，content+anchor 同步重建，并**同时回填 anchors 关系维度**）、写语义（显式锚点替换/`[]` 清空/正文实质变化清空/空白等价保留）、MCP `search_anchor`（工具 11 个）、`POST /api/memories/search/anchor`、`invalid_anchors` 结构化拒绝、stats 加 `anchor_total`/`memories_with_anchors`/`lexical_index_version`。含 2 处 P2 review 修复（升级回填 anchors 关系维度、`search_anchor` 纳入审计裁剪）。在线 tools/list 已确认 add/update anchors schema 与新版引导，/read-me 同步，v3 索引和 dual 健康检查通过，无需再次重启。功能收尾完成；新会话自然采用率及效果 benchmark 仍待验证，因此本项暂留开放清单。
- 验收：Anchor 校验与隔离（无向量）、写路径事务语义、导入恢复重建且不产生 anchor 向量、NDJSON 不含 FTS、回归；Phase 2 增量价值与 Agent 工具选择 benchmark，据此决定是否启动 Phase 3。

### [41] Historical Anchor Backfill

- 计划：详见 `docs/project-management/plans/41-historical-anchor-backfill.md`。
- 目标：通过离线 DeepSeek CLI 为历史记忆生成并自动写入 anchors；先在 `remember-me` 小批量试跑，检查整体效果后扩展到其余 `remember-me` 记录，再分批覆盖其他 namespace。
- 约束：在线 add/update 不生成 anchors；默认只处理缺少 anchors 的记录；每次给 LLM 一条记忆的小上下文并将结果追加到 ID 关联的临时 JSONL；整批生成完成后才写库，一个 manifest 对应一个 SQLite 原子事务；任一条陈旧/冲突或事务错误则整批不写；成功提交后由 dual outbox 同步 JSONL；候选可不逐字出现在正文，按记忆语义生成；不设逐条人工审核。
- 状态：离线 `bin/manage migrate anchors draft/apply` 已实现并通过自动化测试；尚未在真实数据上执行小批量试跑、对账和后续 namespace 回填。计划文档中的 rollout 仍待执行。

### [42] Runtime Maintenance Mode — 在线运维窗口

- 目标：服务进程保持运行时允许执行受控维护，减少为离线运维停服/重启的需要。
- 流程：Ops 页面显式进入维护状态；runtime 立即拒绝新的 MCP/Web 普通记忆写入，等待已开始的写入及 dual 后台任务排空；MCP/Web 查询继续。退出维护后恢复普通写入与后台任务。
- 并发约束：复用现有 SQLite/replica writer lock；维护期间普通记忆写入关闭。Ops 页面同一时刻只允许一个维护动作执行（含后台 import/recover）；runtime 负责排空、互斥和异常恢复；进程崩溃时 writer lock 随进程释放，普通维护状态不持久化。
- 运维边界：在线维护只支持由 Ops API/UI 在当前服务进程内执行；runtime 始终持有现有 writer lock，并串行协调维护操作。独立 CLI 不接入 maintenance mode，任何需要 CLI 的运维仍须先正常停服，再由 CLI 获取 writer lock。
- 接口/行为：`/api/maintenance` 状态与 enter/exit API；MCP/Web 普通写入口统一门禁，返回可识别的 `maintenance_mode` 和重试 hint；查询与 Ops 状态在线；所有 Ops 动作都要求维护状态并排他执行，动作中不能退出；关闭/重启也受此约束。
- Recover：Ops 上传完整 JSONL 备份；临时库重建期间查询继续，切换主库时短暂排空查询；持有 writer lock，通过 SQLite backup 原子替换主库，并将恢复后的全库精确同步至 JSONL。崩溃恢复标记保证启动时补齐副本；不启动独立 CLI。
- 验收：并发进入/退出、在途调用排空、maintenance hint、MCP/记忆写入拒绝、Ops 单动作互斥、recover 失败/成功后的恢复、dual sync 暂停与恢复、writer lock 竞争、进程中断恢复及浏览器回归。
- 状态：功能已实现，465 pytest 与浏览器回归通过；待服务部署及实际运行验证，尤其是上传备份后的 Recover/副本对账和进程中断恢复演练。详见 `docs/project-management/plans/42-runtime-maintenance-mode.md`。

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
