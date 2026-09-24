# Backlog TODO

本文件是 remember-me 项目的开放待办清单。完成项移入 `backlog-done.md`。

## 当前待办

### [39] Retrieval Extension Phase 1 — Lexical Search

- 计划：详见 `docs/project-management/plans/39-memory-retrieval-lexical.md`；设计见 `docs/design/memory-retrieval-hld.md`、`memory-retrieval-detailed-design.md`、`memory-retrieval-pre-coding-validation.md`。
- 目标：为正文增加独立 Lexical Search primitive（SQLite FTS5 + BM25），与 Semantic 并列，不做 Hybrid/fusion。新增 `memory_content_fts`、单一 `lexical_index_version`、共用最小 QueryBuilder、`SQLiteStorage.search_lexical`、MCP `search_lexical` 与 `POST /api/memories/search/lexical`。
- 约束：仅 `query`/`k`（默认 10，范围 1–100）/`namespace`；query ≤4096 字符且 ≤256 token，超限拒绝不截断；纯标点空结果；`score=-raw_bm25`；复用现有 `search_id`/`report`；缺表/缺版本/版本不符时进入 running 前全量重建，失败即启动失败；写路径同事务同步 FTS。
- 验收：事务回滚、启动重建与失败路径、import/recovery、命名空间信封、query/k 边界、审计与 `/read-me` 工具列表同步、全量 pytest 与浏览器回归；Phase 1 tokenizer/BM25 benchmark（脱敏 query→positive、同一 DB 快照、分组 Hit@1/Hit@5/MRR/无结果率）。

### [40] Retrieval Extension Phase 2 — Manual Anchors + Anchor Search

- 计划：详见 `docs/project-management/plans/40-memory-retrieval-anchors.md`；依赖 [39] 完成。
- 目标：调用方通过 `metadata.anchors` 提供 Anchor（≤5，严格校验），复用 `index_values`/`memory_index_values` 通用关系结构；新增独立 `memory_anchor_fts`、`search_anchor` 与 `POST /api/memories/search/anchor`；服务端不自动生成 Anchor。
- 隔离约束：`_INDEX_DIMENSIONS` 增加 `requires_embedding`；anchors 不写 `index_value_embeddings`，不参与完整性检查/回填/reindex/activate，`IndexValueResolver` 对其 semantic resolution 显式报错；Python 与 SQL 两层过滤；不新增 anchor 版本或状态机。
- 验收：Anchor 校验与隔离（无向量）、写路径事务语义（正文实质变化清空/空白等价保留/显式提交优先）、导入恢复重建且不产生 anchor 向量、NDJSON 不含 FTS、回归；Phase 2 增量价值与 Agent 工具选择 benchmark，据此决定是否启动 Phase 3。

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
