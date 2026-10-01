# Retrieval Extension — Coding Plan 前验证记录

验证日期：2026-09-24。验证使用当前 Python SQLite 的内存数据库、现有代码静态检查，以及生产数据的只读聚合统计。没有修改生产数据库，也没有实现检索功能。

## 1. FTS5 与分词

当前 Python SQLite 为 **3.45.1**，`ENABLE_FTS5=1`。内存 FTS5 表及 `fts5vocab` 可正常建立和查询。用默认 `unicode61` 对单条示例文本建索引后的实际 token 如下：

| 输入 | `unicode61` token |
|---|---|
| `PoolAcquirePendingLimitException` | `poolacquirependinglimitexception` |
| `subscriptionsPerConnection` | `subscriptionsperconnection` |
| `operation-overlord` | `operation`, `overlord` |
| `ORA-00060` | `ora`, `00060` |
| `atos-v11:mass:group` | `atos`, `v11`, `mass`, `group` |
| `snake_case` | `snake`, `case` |
| `Redis 主从复制延迟` | `redis`, `主从复制延迟` |
| `中文English混排` | `中文english混排` |

结论：CamelCase 不自动拆分；连字符、冒号和下划线会拆开标识符；连续中文常作为一个 token。`unicode61` 默认还会去除拉丁重音符号，如 `naïve` 索引为 `naive`。这些是当前环境的实测行为，不能据此声称中文召回率已达标。

用双引号包住完整的 `ORA-00060` 可以让 FTS5 按其两个连续 token 做 phrase 匹配；把 `ora` 和 `00060` 用 OR 连接，则其他 `ORA-*` 记录也可能被召回。在三条合成记录中，`"ora" OR "00060"` 召回目标记录及另一条 `ORA-00001`，目标 BM25 排名更高。故第一版 OR 查询提供广覆盖，但不保证整个标识符精确匹配。需要在真实查询评测中重点观察这类误召回。

## 2. QueryBuilder 与输入限制

直接将 `!!!`、`NEAR OR AND` 传给 FTS5 `MATCH` 会报语法错误；将无词输入处理成零 token 并直接返回空结果，能避免这一问题。每个可检索词必须被安全引用，用户输入不得直接成为 FTS5 表达式。

一个基于 Unicode 字母/数字、以标点和下划线分界的候选分词规则，在上述技术标识符和中英混合样例上与 `unicode61` 的 token 边界一致；在带重音拉丁词上表面 token 不同，而 FTS5 自身会做重音归一化。该候选规则还需在实施时用更完整样本检验，不能视作与 SQLite tokenizer 完全等价。不要手工对 CamelCase 或中文再切词，除非 benchmark 显示确有需要；否则查询和索引规则会分叉。

实验中 100、256、512、900、1100 个 OR 词的 FTS5 表达式均可执行。这只说明 SQLite 接受这些合成表达式，不说明其在生产负载下合适。建议第一版固定 **query 最多 4096 个 Unicode 字符、最多 256 个可检索 token**，超限明确拒绝，不截断；现有已关联正例查询最长 156 字符，按候选规则最多 19 个词，均在范围内。与已确定的契约一致：`k` 默认 10、范围 1–100；空白 query 拒绝；纯标点 query 返回空结果。上限是保守工程选择，不是 SQLite 的硬限制；coding plan 可直接采用并在 benchmark 后评估是否调整。

## 3. 真实评测数据及限制

只读聚合 `data/audit.jsonl` 与 `data/memories.db` 得到：当前有 **1053** 条存活 Memory、**4** 个存活 namespace；审计中有 **368** 次 Semantic Search、**327** 次 Report。按 `search_id` 关联并过滤已删除目标后，可得到 **279** 个有正向反馈的 Semantic 查询及 **912** 个存活正例关联。验证文档不复制查询正文或 Memory 内容。

这些正例来自先前已展示的搜索结果，有**展示选择偏差**：用户无法对未召回的记录评分。因此它们适合作为首批真实 query → positive memory 回归集，却不足以单独衡量新增检索对 Semantic 漏召回的补救效果。审计中按现有展示顺序计算出的 Semantic Hit@1=0.857、Hit@5=0.986、MRR=0.911，只描述这批被评过分的已展示结果，**不是无偏的 Semantic 质量基线**，也不能直接与未来在全库重新运行的 Lexical/Anchor 结果比较。

Coding plan 应安排：固定一份脱敏的 query → positive memory 评测清单，在同一数据库快照上重新运行 Semantic、Lexical、Anchor；按标识符、异常码、中文、混合语言等分组记录 Hit@1、Hit@5、MRR 和无结果率。为评估 Anchor 增量价值，还需补充 Semantic 未展示但经人工确认相关的正例；否则只能衡量已知正例重排，不能证明增量召回。Agent 工具选择质量需独立的选择记录/人工标注，现有审计不足以直接证明。

## 4. 实施入口核对

- `SQLiteStorage` 的 `add`、`update`、`update_metadata`、`delete`、`clear`、`import_records` 以及恢复使用的 `_add_with_id` 均可能改变 FTS 来源数据；对应事务必须同步 FTS。`update` 的空白等价判断只决定是否清空 Anchors，实际正文变化仍需更新正文 FTS。
- 导入由 `MemoryService._run_import` 准备向量后调用存储层 `import_records`；离线恢复在 `app/storage/recovery.py` 逐条调用 `_add_with_id`，随后重建 metadata 关系和索引。两条路径都要覆盖 Anchor 严格输入、FTS 重建及版本标记。
- 当前 `_queryable_index_values`、`index_embedding_completeness`、`reindex_index_values`、`missing_index_value_embeddings`、`missing_queryable_index_value_embeddings`、`_store_index_value_embeddings`、Runtime 启动完整性检查和 `IndexValueResolver` 会触及通用索引值。加入 anchors 后必须在 Python 与 SQL 两层过滤 `requires_embedding=False`，并拒绝 Anchor semantic resolution。
- `DualStorage` 的搜索方法需委托 SQLite primary。MCP 新工具须同步注册、分发、hint、审计裁剪和 `/read-me` 工具列表；Admin 新增两个独立搜索端点。本轮无 UI 搜索模式改动。

## 5. Coding plan 输入结论

环境满足 FTS5 基础要求，可以按 Phase 1（正文 Lexical）和 Phase 2（调用方 Anchors + Anchor Search）写 coding plan。第一版采用 `unicode61`、安全的 OR 查询、上述保守 query 限制和已确定的事务/版本重建规则。分词效果、Anchor 的增量价值和 Agent 选择效果仍须由实施后的真实数据评测确认，不作为设计阶段的功能保证。
