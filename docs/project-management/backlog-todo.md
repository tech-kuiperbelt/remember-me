# Backlog TODO

本文件是 remember-me 项目的开放待办清单。完成项移入 `backlog-done.md`。

## 当前待办

### [41] Historical Anchor Backfill

- 计划：详见 `docs/project-management/plans/41-historical-anchor-backfill.md`（含 Draft/Apply 冻结规格与 manifest v2 线格式）。
- 目标：通过离线 DeepSeek CLI 为历史记忆生成并自动写入 anchors；分批覆盖各 namespace。
- 约束：在线 add/update 不生成 anchors；默认只处理缺少 anchors 的记录；批量模式下一次 `opencode run` 处理 `--batch-size`（默认 10）条记忆并按 id 回填，结果追加到 manifest（一个 manifest = 一批 = 一个 SQLite 原子事务）；用 `--exclude-manifest` 以既有 manifest 作账本分批（下一批 = eligible − 已提取），因此不 apply 也能推进且各批不重叠；任一条陈旧/冲突或事务错误则整批不写；成功提交后由 dual outbox 同步 JSONL；候选可不逐字出现在正文；不设逐条人工审核。
- 状态：2026-09-29 已实现并在真实数据上执行完成（实现提交于 `af0cf58`；本次文档更新记录执行结果）。
  - 实现：`bin/manage migrate anchors draft/apply`、`SQLiteStorage.update_anchors_batch`（单事务整批）、批量抽取、`--exclude-manifest` 账本式分批；pytest 458 通过。
  - `remember-me`：试点 + 全量（115 条 → 114 generated / 1 skip）单事务 apply 完成；该 namespace live 145 中 141 有 anchors。
  - `atos-next-master`：941 条 draft（95 个 manifest，全 generated、0 skip/0 error），逐个 manifest 单事务 apply 完成（941 条、0 失败、`updated_at` 不变）；重启后 dual `in_sync=true`、`pending=0`、replica integrity 0；该 namespace live **956/956 全部有 anchors**。
  - 全局：`anchor_total=3166`、`memories_with_anchors=1097`；anchor 向量 0（隔离未破坏）。
  - 剩余（可选）：`remember-me` 期间新增的少量记录、`待报销` 2 条、以及后续新增记忆；沿用同一流程（draft → 停机 apply）。
  - 调用复现：服务端 Request ID = opencode assistant messageID（`msg_…`）；可用 opencode DB（session/message/part）还原该次发送的 prompt 与 response（本地辅助脚本，未入库）。

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
