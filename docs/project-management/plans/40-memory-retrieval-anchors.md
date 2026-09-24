# [40] Retrieval Extension Phase 2 — Manual Anchors + Anchor Search

2026-09-24 coding plan；**已实现（未提交）**，依赖 [39] 完成。设计依据：`docs/design/memory-retrieval-hld.md`、
`docs/design/memory-retrieval-detailed-design.md`、`docs/design/memory-retrieval-pre-coding-validation.md`。

## 目标与范围

在 [39] 的 Lexical 基础上，引入 **Anchor** 作为 identity signal（技术标识符、异常码、类/接口名、
版本号、命名概念等），由**调用方**提供，并实现独立的 Anchor Search primitive。

- Anchor 保存于 `metadata.anchors`；复用 `index_values` / `memory_index_values` 通用关系结构。
- **完全隔离**通用索引值的 embedding 生命周期与 semantic resolution。
- 服务端**不自动生成** Anchor（Phase 3 另行决定）。
- 不改 UI 搜索模式；Keywords 保持 Observational 定位，不重命名、不淘汰。

## 决策摘要（来自设计，本计划不再更改）

| 项 | 决定 |
|---|---|
| Anchor 来源 | 调用方（LLM 或人工）通过 `metadata.anchors` 提供，服务端严格校验 |
| 每记忆上限 | 5 |
| 规范化 | Anchor 专属规则，**不沿用** keywords/categories 的小写化 |
| 关系存储 | dim=`anchors` 写入 `index_values` / `memory_index_values` |
| 向量 | 不写 `index_value_embeddings`；不参与完整性检查/回填/reindex/activate |
| 解析 | `IndexValueResolver` 对 `anchors` 显式报错，不做 semantic resolution |
| FTS | 独立 `memory_anchor_fts`，与 content FTS 逻辑/物理分离，共享单一 `LEXICAL_INDEX_VERSION` |
| 检索参数 | 仅 `query` / `k`（默认 10，1–100）/ `namespace` |
| 公开 score | `score = -raw_bm25`；`match_info{type:"anchor", score_type:"fts5_bm25", raw_bm25}` |
| Admin | `POST /api/memories/search/anchor`；本轮共两个独立 Admin 搜索 API |
| 版本 | 不新增 `ANCHOR_INDEX_VERSION` 或状态机；沿用 `lexical_index_version` |

## 详细方案

### 1. Anchor 模型与校验（元数据）

- `metadata.anchors: list[str]`，最多 5 个；提交入口严格校验：类型、非空、长度上限、去重、
  数量上限；非法输入明确拒绝，不静默丢弃。
- 规范化使用 Anchor 专属规则（保留大小写/标识符形态的大部分信息，仅做必要的 trim 与去重），
  与 keywords/categories 的小写化规则**分开实现**。
- 调用方（LLM）在提交前自行整理候选，属调用方职责；服务端只做严格校验。
- 缺失 anchors 表示"未提供"，与"显式提交空数组"语义区分（见 §3）。

### 2. 关系索引与 embedding 生命周期隔离

维度规则注册表增加 `requires_embedding`：

```python
_INDEX_DIMENSIONS = {
    "keywords":  {"max_values": None,        "requires_embedding": True},
    "categories":{"max_values": MAX_CATEGORIES, "requires_embedding": True},
    "anchors":   {"max_values": 5,           "requires_embedding": False},
}
```

强制隔离点（**Python 与 SQL 两层同时过滤 `requires_embedding=True`**，不能只做一层）：

- 向量准备与写入（`_store_index_value_embeddings` 及调用方）；向量写入入口拒绝 `anchors`。
- 完整性统计与启动检查：`_queryable_index_values`、`index_embedding_completeness`。
- 回填与重建：`missing_index_value_embeddings`、`missing_queryable_index_value_embeddings`、
  `reindex_index_values`、完整 `reindex`、`activate`、codec migration。
- `IndexValueResolver`：显式请求对 `anchors` 做 semantic resolution 必须报错。
- embedding model/profile 变化、切换、回滚**不触发** Anchor 关系或 Anchor FTS 重建。

### 3. Anchor FTS 与写路径

```sql
CREATE VIRTUAL TABLE memory_anchor_fts
USING fts5(memory_id UNINDEXED, anchors, tokenize = 'unicode61');
```

- 每个有 anchors 的非 deleted memory 一行；无 anchors **不插行**（不存空串）。
- 与主记录同事务同步；Anchor FTS 失败回滚整个写入。
- 写/更新语义：
  - 正文**实质变化**且未提交 anchors → 清空既有 anchors 与 Anchor FTS 行；
  - 正文**空白等价** → 保留既有 anchors；
  - **显式提交** anchors（含空数组）优先于上述规则。

### 4. Storage / Service / MCP / Admin

- `SQLiteStorage.search_anchor(query, k=10, namespace=None)`；`MemoryStorage` 协议新增；
  `DualStorage` 委托 primary。
- `MemoryService.memory_search_anchor(...)`：命名空间规则同 [39]；未知 namespace → 空结果信封。
- MCP 新增 `search_anchor`：注册、分派、`_TOOL_HINTS`、审计裁剪、**`app/read_me.md` 工具列表同步**。
- Admin 新增 `POST /api/memories/search/anchor`。
- `add` / `update` 接受并校验 `metadata.anchors`；`get_details` 返回 anchors；写路径同步关系表。

### 5. 导入、恢复与副本

- `import_records` 与恢复路径（`app/recovery.py` → `_add_with_id`）重建 Anchor 关系与 Anchor FTS，
  **不产生 anchor 向量**；导入的向量准备跳过 `requires_embedding=False`。
- NDJSON 副本保存 Anchor 业务数据（在 `metadata` 内），**不保存 FTS**；恢复后由权威数据重建。
- `LEXICAL_INDEX_VERSION` 覆盖 content 与 anchor 两套 FTS 的 schema/tokenizer/表示规则；版本递增即
  触发两者重建。重建同时也**回填 anchors 关系维度**（见下「实现后 review 修复」）。

### 6. Stats

- 可新增 `anchor_total`、`memories_with_anchors`，并暴露 `lexical_index_version`。
- 不新增 anchor 专属版本或 `ready/stale/rebuilding/error` 状态机。

### 实现后 review 修复（2026-09-24）

1. **升级未回填 anchors 关系维度**（`storage_sqlite.py`）：`_rebuild_lexical_index()` 原先只重建
   `memory_content_fts` / `memory_anchor_fts` 与版本戳，不触碰通用关系索引。对**已含
   `metadata.anchors` 的历史库**，升级后 anchor 搜索/FTS 有数据，而 `anchor_total` /
   `anchor_counts` / `memories_with_anchors` 永远为空。现于重建事务内追加
   `_rebuild_metadata_index("anchors")`，与版本戳同事务提交；并把 `LEXICAL_INDEX_VERSION` 升到
   **3**，使已被旧版 v2 迁移过的库在下一次启动时也重建并回填。回归：
   `test_upgrade_backfills_anchor_relations`。
2. **`search_anchor` 未走审计裁剪**（`audit.py`）：`_trim_result` 的 search 分支与保留分数分支
   未包含 `search_anchor`，导致每次成功调用都按 `dict(result)` 记录完整结果与引导文案，放大滚动
   日志。现将其纳入两处分支（与 semantic/lexical 一致：只留 `{id, summary, score}`）。回归：
   `test_audit_trims_search_anchor_results`。

## 测试

- Anchor 校验：类型/空值/超长/重复/超过 5 个 → 拒绝；合法值正确入库。
- **隔离**：写入 anchors 后 `index_value_embeddings` 无新增行；完整性检查、回填、`reindex-index-values`、
  `activate`、codec migration 均忽略 anchors；`IndexValueResolver` 对 anchors 报错。
- 写路径：正文实质变化清空 anchors、空白等价保留、显式提交优先；Anchor FTS 与主记录同事务、
  失败回滚。
- 检索：`search_anchor` 命中 anchors；namespace 信封；`k`/query 边界；score 契约。
- 导入/恢复：Anchor 关系与 FTS 重建正常，且不产生 anchor 向量；NDJSON 无 FTS 字段。
- 回归：Semantic/Lexical、审计契约、`/read-me`、DualStorage 委托、全量 pytest、浏览器回归。

## 评测（属本项）

- 在**同一 DB 快照**上对比 Semantic、Lexical、Anchor 三者的分组指标（Hit@1/Hit@5/MRR/无结果率）。
- 评估 Anchor 相对 Semantic+Lexical 的**增量召回**：需补充 Semantic 未展示但经人工确认相关的正例，
  否则只能衡量已知正例的重排。
- 评估 Agent 的**工具选择质量**，需独立的选择记录或人工标注（现有审计不足以直接证明）。
- 结论用于决定是否启动 Phase 3（服务端自动生成）；价值不足则暂缓。

## 不在范围

- Phase 3 服务端自动 Anchor 生成、failure state、backfill。
- Hybrid/fusion、whole-anchor boost 及其它评分方式、metadata filters、`list_anchors`、UI 改动。

## 风险

- `requires_embedding` 的两层过滤漏一层 → Anchor 被误纳入向量回填或完整性检查（高影响，必须有专门测试）。
- Anchor 规范化若与 keywords 规则混淆，会在更新/导入时产生不一致。
- `unicode61` 对标识符与中文的 token 化影响 Anchor 命中，需由 benchmark 复核。
- 评测正例的展示选择偏差会高估已知召回、低估增量价值。

## 实施顺序

1. 严格 Anchor policy、`requires_embedding` 注册表与关系索引、embedding 生命周期隔离。
2. Anchor FTS、写路径事务语义与 `search_anchor`。
3. MCP/Admin/审计集成、导入恢复与副本处理。
4. Phase 2 增量价值与工具选择 benchmark，据结论决定 Phase 3。
