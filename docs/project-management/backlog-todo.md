# Backlog TODO

本文件是 remember-me 项目的开放待办清单。完成项移入 `backlog-done.md`。

## 当前待办

### [41] Historical Anchor Backfill

- 计划：详见 `docs/project-management/plans/41-historical-anchor-backfill.md`。
- 目标：通过离线 DeepSeek CLI 为历史记忆生成并自动写入 anchors；先在 `remember-me` 小批量试跑，检查整体效果后扩展到其余 `remember-me` 记录，再分批覆盖其他 namespace。
- 约束：在线 add/update 不生成 anchors；默认只处理缺少 anchors 的记录；每次给 LLM 一条记忆的小上下文并将结果追加到 ID 关联的临时 JSONL；整批生成完成后才写库，一个 manifest 对应一个 SQLite 原子事务；任一条陈旧/冲突或事务错误则整批不写；成功提交后由 dual outbox 同步 JSONL；候选可不逐字出现在正文，按记忆语义生成；不设逐条人工审核。
- 状态：离线 `bin/manage migrate anchors draft/apply` 已实现并通过自动化测试；尚未在真实数据上执行小批量试跑、对账和后续 namespace 回填。计划文档中的 rollout 仍待执行。

### [27] LLM 结构化检索与分类写入引导

- 调整 MCP 工具描述与 hint：写入时要求/引导提供 1–3 个 categories，检索时说明 keywords/categories 精确优先、语义解析预过滤的适用场景。
- 基于 audit 复核采用率与检索效果，避免仅靠文案而未形成实际行为改变。
- 状态：2026-09-29 已更新 11 个 MCP 工具的描述与按结果生成的 hint，并同步 `/read-me`；新增对空结果、重复新增、目标缺失、找词模式及反馈记录数的引导检查，全量 475 项 pytest 通过。当前 audit 基线显示近期新建记忆分类采用率高，但语义搜索没有使用过滤条件，暂无法评估检索效果。待部署并观察真实调用后复核采用率和效果；详见 [`plans/27-structured-retrieval-guidance.md`](plans/27-structured-retrieval-guidance.md)。

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
