# [39] Retrieval Extension Phase 1 — Lexical Search

2026-09-24 coding plan；**未实现**。设计依据：`docs/design/memory-retrieval-hld.md`、
`docs/design/memory-retrieval-detailed-design.md`、`docs/design/memory-retrieval-pre-coding-validation.md`。
本项是 [39]（Phase 1）与 [40]（Phase 2，Anchor）中的第一阶段；Phase 3 不纳入。

## 目标与范围

为 memory 正文增加一个**独立的 Lexical Search primitive**（SQLite FTS5 + BM25），与既有
Semantic Search 并列，不引入 Hybrid/fusion。

- 实现：正文 FTS 索引、共用最小 QueryBuilder、Storage/Service/MCP/Admin 检索入口、
  审计与既有 `search_id`/`report`、确定性启动重建、写路径事务一致性、import/recovery、自动化测试。
- 不改：UI 搜索模式、`search_semantic` 行为、keywords/categories 语义解析、JSONL 格式。
- 第一版**不支持** `threshold` / `keywords` / `categories` 过滤，也不新增 `list_anchors`。

## 决策摘要（来自设计，本计划不再更改）

| 项 | 决定 |
|---|---|
| FTS 表 | 正文单独一张 `memory_content_fts`；Anchor 表属 [40] |
| 同步方式 | 应用层在同一 SQLite 事务内同步，不用 trigger |
| 版本 | 单一 `LEXICAL_INDEX_VERSION`，存 `storage_state.lexical_index_version` |
| 启动 | 缺表/缺版本/版本不符 → 进入 running 前全量重建；失败即 startup fail |
| Tokenizer | `unicode61`（第一版），不做预分词 |
| Query 构造 | 安全引用每个词、OR 连接；纯标点（零 token）返回空结果 |
| Query 上限 | ≤4096 个 Unicode 字符且 ≤256 个可检索 token，超限**拒绝**不截断 |
| 检索参数 | 仅 `query` / `k` / `namespace`；`k` 默认 **10**、范围 1–100，越界拒绝 |
| 公开 score | `score = -raw_bm25`；`match_info{type:"lexical", score_type:"fts5_bm25", raw_bm25}` |
| search_id | 复用现有 `se_` 机制与 `report`，不新建 ID 体系 |
| Admin | `POST /api/memories/search/lexical`（两个独立 Admin API 之一，不改 UI） |

## 详细方案

### 1. Schema 与版本

```sql
CREATE VIRTUAL TABLE memory_content_fts
USING fts5(memory_id UNINDEXED, text, tokenize = 'unicode61');
```

- `memory_id UNINDEXED`，每个非 deleted memory 一行；no-content/external-content 取舍由实现决定，
  但必须保证 FTS 完全可从 `memories` 重建。
- `LEXICAL_INDEX_VERSION` 为模块常量，覆盖 FTS schema、tokenizer 与索引表示规则；规则变化即递增。
- 版本写入与两张 FTS 表（Phase 2 后）重建在**同一事务**提交。

### 2. 事务一致性（写路径）

以下路径必须与主记录**同事务**更新 `memory_content_fts`；任一步失败回滚整个 memory 写入：

- `SQLiteStorage.add`、`update`、`update_metadata`、`delete`（软删除：删除/清空 FTS 行）、
  `clear`（按范围）、`import_records`、恢复路径 `_add_with_id`。
- `update` 的正文变化判定：文本实质变化 → 更新 content FTS；空白等价 → 不触发 FTS 变化。

### 3. QueryBuilder（最小共用）

- 输入 query → 规范化 → 产出安全 FTS 表达式：每个可检索词**必须被引用**，用户输入不得直接成为
  FTS5 表达式（`!!!`、`NEAR OR AND` 等会报语法错误）。
- 零 token（空/纯标点）直接返回空结果，不调用 FTS。
- 超限（>4096 字符或 >256 token）显式拒绝。
- 候选分词规则：基于 Unicode 字母/数字、以标点和下划线分界；**不手工切 CamelCase 或中文**
  （避免查询与索引规则分叉）。该规则与 `unicode61` 边界一致性由 Phase 1 benchmark 复核。

### 4. Storage / Service / MCP / Admin

- `SQLiteStorage.search_lexical(query, k=10, namespace=None)`：
  先按 `deleted=0` + namespace 过滤，再 `MATCH` + `ORDER BY bm25(...)`，返回
  `score = -raw_bm25` 与 `match_info`。
- `MemoryStorage`（协议）新增 `search_lexical`；`DualStorage` 委托 SQLite primary。
- `MemoryService.memory_search_lexical(query, k=10, namespace=None)`：
  `check_namespace()` 用既有规则；未知 namespace → 空结果信封 `namespace_not_found`（不抛业务异常）。
- MCP 新增 `search_lexical`：`handle_list_tools` 注册、`handle_call_tool` 分派、`_TOOL_HINTS` 条目、
  审计裁剪、**`app/read_me.md` 工具列表同步**。
- Admin 新增 `POST /api/memories/search/lexical`，与 MCP 共用业务层；未知 namespace → HTTP 400 同信封。

### 5. 启动重建

在既有主库 writer lock 与 `Runtime.start` 生命周期内：

1. 检查所需 FTS 表是否存在、读取 `storage_state.lexical_index_version`。
2. 缺表、缺版本或版本不匹配 → 从 live memories 全量重建。
3. 表与版本标记同事务提交；全部启动检查成功后才进入 running。
4. 重建失败 → 回滚 + startup fail（沿用现有启动失败清理），服务不提供 lexical search。
5. 不按数据量/耗时分支，不调用 LLM，不因 embedding profile 变化重建 FTS。

### 6. 结果契约

```
score = -raw_bm25
match_info = {"type": "lexical", "score_type": "fts5_bm25", "raw_bm25": <float>}
```

三种 search 的 raw score **不直接跨类型比较**；响应沿用既有 search 结果信封与 `search_id`。

## 测试

- 事务：正文写入/更新/软删除/clear 与 FTS 同步；FTS 更新失败导致整体回滚。
- 启动：缺表、版本记录缺失、版本不匹配 → 重建；重建成功才 running；重建失败 → startup fail。
- import 与 recovery 后 lexical search 正常。
- unknown namespace → 空结果信封；已知 namespace 无命中 → 正常空结果。
- query：空/纯空白拒绝；纯标点空结果；超 4096 字符或 256 token 拒绝；`k` 越界拒绝。
- 回归：`search_semantic`、`report`、审计契约、`/read-me` 工具列表、DualStorage 委托、全量 pytest、
  浏览器回归。

## 评测（属本项，Phase 1 benchmark）

- 固定一份**脱敏**的 `query → positive memory` 清单；在**同一 DB 快照**上运行 Semantic 与 Lexical。
- 按分组记录 Hit@1、Hit@5、MRR、无结果率：技术标识符、异常码、中文、中英混排、自然语言。
- 记录 `unicode61` 的中文/标识符召回实况，判断是否需要预分词（Phase A/B）。
- 说明限制：现有审计正例来自已展示结果，有展示选择偏差，不是无偏基线；指标仅描述所评样本。

## 不在范围

- Anchor（[40]）、Phase 3 自动生成、Hybrid/fusion、metadata filters、`list_anchors`、UI 搜索模式。

## 风险

- `unicode61` 对中文与技术标识符的召回不确定 → 由 benchmark 决定后续分词策略。
- Lexical 与 Semantic 的 raw score 不可直接比较，需避免在文档/UI 中暗示可比较。
- 启动全量重建在大库上的耗时是接受的代价（设计明确不设"快速自动重建"分支）。
- 新增 MCP 工具必须同步 audit 裁剪与 `app/read_me.md`，否则违反 AGENTS.md 维护约定。

## 实施顺序

1. 正文 FTS schema、单一版本、原子重建与启动失败处理。
2. 共用 QueryBuilder、`SQLiteStorage.search_lexical`、`MemoryStorage` 协议、`MemoryService` 接口。
3. MCP 工具、`search_id`/`report`/audit 集成及正确性测试。
4. Phase 1 tokenizer/BM25 benchmark，输出结论后再进入 [40]。
