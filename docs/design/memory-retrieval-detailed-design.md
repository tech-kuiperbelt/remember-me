# remember-me Retrieval Extension — Detailed Design

## 1. Purpose

本文基于《remember-me Retrieval Extension — High-Level Design》细化实现设计，目标是在现有 `remember-me` 架构中增加：

```text
search_lexical
search_anchor
```

并保持现有：

```text
search_semantic
```

不变。

本设计覆盖：

- 数据模型
- SQLite / FTS5 索引结构
- Tokenization
- Lexical Search
- Anchor Search
- Anchor Generation
- MemoryService / Storage 接口
- Add / Update / Delete / Clear
- Import / Recovery / Reindex
- DualStorage / NDJSON Replica
- MCP Tool Contract
- Admin API
- Audit / Feedback
- Benchmark
- Migration
- Failure Handling
- Testing

本设计不实现：

- Hybrid `search`
- RRF / weighted fusion
- reranker
- learned ranking
- Memory Relationship Graph
- Keyword feature removal

---

# 2. Existing Constraints

当前系统约束：

```text
- SQLite 为 Primary Store
- 单进程、单写者
- SQLite WAL
- MemoryService 为业务唯一入口
- SQLiteStorage 为主库实现
- DualStorage 在主库提交后通过 outbox 同步 NDJSONReplica
- NDJSONReplica 不保存 derived indexes
- Memory soft delete
- active embedding profile 独立管理
- metadata 通过 index_values / memory_index_values 建立通用索引
```

因此 Retrieval Extension 必须满足：

1. 不改变现有单写者模型；
2. 不绕过 `MemoryService`；
3. lexical / anchor index 必须与 SQLite transaction 保持一致；
4. FTS/lexical index 必须是 derived data；
5. NDJSON Replica 只保存 authoritative memory data；
6. embedding lifecycle 与 lexical lifecycle 独立；
7. `keywords` 保持 Observational Feature，不进入 deprecated 状态。

---

# 3. Terminology

## 3.1 Semantic Search

```text
search_semantic
```

基于：

```text
query embedding
vs
memory document embedding
```

得到 semantic similarity。

---

## 3.2 Lexical Search

```text
search_lexical
```

基于 Memory 原始正文：

```text
memories.text
```

执行 lexical ranking。

第一版使用：

```text
SQLite FTS5 + BM25
```

---

## 3.3 Anchor

Anchor 是：

> A distinctive term or short phrase that a future query may use to identify a specific memory.

例如：

```text
PoolAcquirePendingLimitException
ORA-00060
RedisMassActionAdmissionStore
operation-overlord
Mass Action Throttle
```

---

## 3.4 Anchor Search

```text
search_anchor
```

只搜索：

```text
metadata.anchors
```

不搜索正文。

第一版使用独立 FTS5 index，并使用 BM25 ranking。

---

## 3.5 Keyword

Keyword 表示：

```text
Memory Content Concept
```

例如：

```text
Redis
Hibernate
Caching
Transaction
```

Keyword 保留现有能力。

当前状态：

```text
Observational Feature
```

不是 Deprecated Feature。

未来可能参与：

```text
Memory Relationship
Concept Clustering
Related Memory Discovery
Memory / Concept Graph
```

---

# 4. High-Level Component Changes

沿用现有调用路径：
```text
MCP / Admin API
    ↓
MemoryService
    ↓
MemoryStorage protocol
    ↓
SQLiteStorage
    ├── memory_content_fts
    └── memory_anchor_fts
```
DualStorage 的搜索委托给 SQLite primary。直接增加 search_lexical / search_anchor，不新增 LexicalSearchRepository / AnchorSearchRepository。两个 FTS 表分别维护正文与 Anchor 的统计和 ranking 边界。共享一个最小 FTSQueryBuilder。

当前阶段由调用方提供 anchors，服务端只做严格校验、规范化和事务内索引同步。

---

# 5. Source of Truth

## 5.1 Authoritative Data

以下数据属于业务数据：

```text
memories.text
memories.summary
memories.metadata
memories.namespace
memories.created_at
memories.updated_at
```

Anchor 存于：

```text
memories.metadata.anchors
```

Keyword 存于：

```text
memories.metadata.keywords
```

Category 存于：

```text
memories.metadata.categories
```

---

## 5.2 Derived Data

以下数据必须可以重建：

```text
memory_embeddings
index_value_embeddings
content FTS index
anchor FTS index
```

因此：

```text
NDJSON Replica
```

不需要存：

```text
FTS internal index
BM25 statistics
lexical posting lists
```

---

# 6. Memory Metadata Model

Memory metadata 示例：

```json
{
  "keywords": [
    "Redis",
    "Throttle"
  ],
  "categories": [
    "technical"
  ],
  "anchors": [
    "Mass Action Throttle",
    "RedisMassActionAdmissionStore",
    "operation-overlord"
  ]
}
```

约束：

```text
keywords:
  existing behavior unchanged

categories:
  existing behavior unchanged

anchors:
  0..5
  unique after normalization
  ordered
```

Anchor 顺序保留调用方提交顺序，去重后保留首次出现项。

第一版不使用 anchor order 参与 ranking。

---

# 7. Anchor Normalization and Validation

新增 `normalize_anchors(values)`，用于所有显式提交的 `metadata.anchors`。调用方包括人工客户端、调用 MCP 的 LLM 和外部回填程序；服务端对它们采用相同的严格规则。

1. 字段缺失沿用 Add / Update 的缺省行为；显式值必须是字符串数组，`null`、非数组和非字符串元素均返回 validation error。
2. 原始数组超过 5 项返回 validation error；先检查提交数量，再去重，不通过去重绕过数量限制。
3. 每项先做 NFKC Unicode normalization、首尾空白清理及内部连续空白折叠。
4. 规范化后为空字符串或超过 128 个 Unicode 字符，返回 validation error；不截断字符串。
5. 保存规范化后的大小写形式；去重比较只对 ASCII 字母忽略大小写，并保留首次出现项及其顺序。
6. 显式 `[]` 合法；update 中表示清空已有 Anchors。
7. 任一项非法即拒绝整个写入，不静默丢弃非法项、不部分保存；校验应在 embedding 调用和数据库写入前完成。

例如 `PoolAcquirePendingLimitException` 不得改写为 `pool acquire pending limit exception`。

调用方输入与生成候选的边界：

```text
caller input is strict
generated enrichment is tolerant before submission
```

外部 LLM 生成流程可以在提交前过滤非法候选、去重并最多保留 5 项。但一旦通过 API 提交，即属于明确的调用方输入，必须通过上述严格校验。服务端不设置“来自 LLM 即宽松处理”的旁路，也不新增自动生成器。

---

# 8. Caller-Provided Anchors

## 8.1 Source and Interface

Anchors 由调用 `add` / `update` 的 LLM 根据 Memory 正文提取，并通过：

```text
metadata.anchors
```

提交给服务端。例如：

```json
{
  "metadata": {
    "anchors": [
      "RedisMassActionAdmissionStore",
      "operation-overlord"
    ]
  }
}
```

服务端不新增 `AnchorGenerator` 或 `LLMAnchorGenerator`，也不再次调用 LLM 生成或补全 Anchors。

---

## 8.2 Responsibility

调用方 LLM 负责：

- 根据正文提取 0–5 个有辨识度的 Anchor；
- 优先保留正文中的名称、标识符和独特短语；
- 不编造 Anchor；没有合适的值时提交 `[]`；
- 修改正文时同步检查并提交适合新正文的 Anchors。

服务端负责：

- 校验和规范化调用方提交的 `metadata.anchors`；
- 按明确的 metadata 更新规则保存 Anchors；
- 同步通用 metadata 关联索引和 Anchor FTS 索引；
- 提供 Anchor Search，并沿用现有写入、搜索审计机制。

服务端不负责 Anchor 提取，也不判断调用方是否应当选取某个具体 Anchor。

---

## 8.3 Caller Guidance

以下提取指导用于 MCP 工具描述或调用方指引，不作为服务端 LLM Prompt 执行：

```text
Extract 0-5 retrieval anchors from this memory and provide them
in metadata.anchors when calling add or updating the memory text.

Anchors are distinctive terms or short phrases that users may
mention in future queries to identify this memory.

Prefer names, projects, classes, APIs, errors, config keys,
versions, identifiers, technical terms, and distinctive phrases.

Avoid generic topics and vague terms.

Do not invent anchors.
Provide [] if none.
```

---

## 8.4 Missing and Empty Anchors

新增 Memory 时，未提供 `metadata.anchors` 表示没有提交 Anchors；服务端不自动生成。旧数据没有该字段仍然合法。

显式提交：

```json
{"anchors": []}
```

表示没有 Anchors；在 update 中表示清空已有 Anchors。

仅修改其他 metadata 且未提交 anchors 时，保留原 Anchors。正文实质变化且未提交 anchors 时清空旧值；正文未变化或仅空白等价变化时保留。显式提交 anchors 始终优先。实质变化采用现有 summary_policy.normalize_whitespace 比较规则（首尾空白清理、连续空白折叠）。

---

## 8.5 Failure Handling and Historical Data

服务端没有 Anchor 生成调用，因此不存在生成超时、生成 provider 不可用或 LLM 返回解析失败的处理流程，也不需要生成专用 provider 配置、超时配置或 audit event。

调用方提交不合法的 Anchors 时，按 Anchor 输入校验规则处理，不应把无效输入静默当作一次“生成失败”转换为 `[]`。

历史数据需要补充 Anchors 时，由外部 LLM 或离线调用方提取，再通过明确的更新流程写入。Import / Recovery 不隐式生成 Anchors。

本节规定当前 Phase 1/2 行为；服务端自动生成仅作为 Phase 3 的可选未来扩展，须在评测后另行设计。

---

# 9. Caller-Provided Anchor Handling

Add：显式 metadata.anchors 按第 7 节严格校验；缺失时不自动生成。

Update：显式 metadata.anchors 替换旧值，[] 清空；仅修改其他 metadata 且未提交 anchors 时保留。正文实质变化且未提交 anchors 时清空旧值；正文未变化或仅空白等价变化时保留。显式提交 anchors 始终优先。实质变化采用现有 summary_policy.normalize_whitespace 比较规则（首尾空白清理、连续空白折叠）。任何路径都不触发服务端自动生成。

不存在调用方值与服务端生成值之间的优先级竞争。

---

# 10. SQLite Schema Extension

## 10.1 Existing Tables

继续使用：

```text
memories
index_values
memory_index_values
embedding_profiles
memory_embeddings
index_value_embeddings
```

---

## 10.2 index_values Dimension

新增合法 dimension：

```text
anchors
```

形成：

```text
keywords
categories
anchors
```

---

## 10.3 Anchor / Embedding Lifecycle Isolation

Anchors 只复用 `index_values` / `memory_index_values` 的通用索引值关系结构，不复用通用索引值的 embedding 生命周期。以下约束为强制要求：

- 不写入 `index_value_embeddings`。
- 不参与 embedding 完整性检查，包括 expected / missing / stored 统计和 profile 激活检查。
- 不参与普通写入的 embedding 准备，也不参与启动时缺失 embedding 回填。
- 不参与完整 profile reindex 或 `reindex-index-values`。
- embedding model / profile 变化、切换和回滚不触发 Anchor 关系索引或 Anchor FTS 重建。
- `IndexValueResolver` 不对 anchors 做 semantic resolution；显式请求对 anchors 做该解析必须报错。

维度规则集中登记，并由 storage、embedding 准备及 resolver 共用：

```python
_INDEX_DIMENSIONS = {
    "keywords": {"max_values": None, "requires_embedding": True},
    "categories": {"max_values": MAX_CATEGORIES, "requires_embedding": True},
    "anchors": {"max_values": 5, "requires_embedding": False},
}
```

这是代码中的维度规则注册表，不是新增数据库表。Anchor 的值规范化使用第 7 节规则，不能直接沿用 keywords/categories 的小写化规则。

通用关联的同步、删除、导入及恢复处理全部维度。向量准备、写入、缺失查询、完整性统计、启动回填、profile reindex 和激活检查只处理 `requires_embedding=True` 的维度。Python 遍历与 SQL 查询必须共同遵循这一集合，不能只过滤其中一层。向量写入入口拒绝 anchors，避免调用方绕过上层检查。

Anchor 关系及 FTS 的重建由 Anchor 业务数据变化、恢复或其自身索引版本变化触发，与 embedding profile 生命周期独立。

---

# 11. FTS5 Physical Design

推荐第一版使用两个逻辑独立 FTS table。

原因：

- Content 与 Anchor scoring semantics 不同；
- 调试更清晰；
- 可以独立 rebuild；
- 可以独立更换 tokenizer；
- 不需要 column weighting；
- 避免 Anchor 内容进入 Content BM25 document length。

推荐：

```sql
CREATE VIRTUAL TABLE memory_content_fts
USING fts5(
    memory_id UNINDEXED,
    text,
    tokenize = 'unicode61'
);
```

以及：

```sql
CREATE VIRTUAL TABLE memory_anchor_fts
USING fts5(
    memory_id UNINDEXED,
    anchors,
    tokenize = 'unicode61'
);
```

这里的 SQL 只是概念结构。

Tokenizer 在 implementation 前必须验证。

---

# 12. Why Separate FTS Tables

不推荐第一版：

```text
one FTS table:
(memory_id, content, anchors)
```

原因：

1. BM25 document length 会混合两种字段；
2. Anchor 是高密度 identity field；
3. Content 是自然语言正文；
4. 两者未来 scoring 很可能不同；
5. Separate table 更容易 benchmark；
6. `search_anchor` 可以独立演进为非 BM25 scorer。

因此：

```text
physical separation
```

与 HLD 的：

```text
logical separation
```

保持一致。

---

# 13. FTS Row Model

每个非 deleted Memory：

```text
memory_content_fts:
    one row per memory

memory_anchor_fts:
    zero or one row per memory
```

如果 Memory 没有 anchors：

推荐：

```text
do not insert anchor FTS row
```

而不是存空字符串。

---

# 14. FTS Synchronization Strategy

第一版推荐：

```text
Application-managed FTS synchronization
```

而不是 SQLite trigger。

理由：

- 当前已有复杂 service/storage transaction；
- metadata JSON 的 anchors 解析放在 Python 更容易；
- update semantics 已在业务层；
- recovery/reindex 需要显式控制；
- soft delete 不是 physical DELETE。

因此由 `SQLiteStorage` 在同一 transaction 中更新：

```text
memories
index_values
memory_index_values
memory_embeddings
memory_content_fts
memory_anchor_fts
```

---

# 15. Add Transaction

概念 SQL transaction：

```text
BEGIN

INSERT memories

INSERT/UPDATE memory_embeddings

SYNC index_values
SYNC memory_index_values
SYNC index_value_embeddings for keywords/categories only; never anchors

INSERT memory_content_fts(memory_id, text)

IF anchors not empty:
    INSERT memory_anchor_fts(memory_id, anchors_text)

COMMIT
```

任何一步失败：

```text
ROLLBACK
```

保证：

```text
authoritative memory
and
derived lexical indexes
```

不会部分提交。

---

# 16. Anchor FTS Serialization

Anchor FTS field 需要把多个 anchor 序列化成可检索文本。

例如：

```json
[
  "Mass Action Throttle",
  "RedisMassActionAdmissionStore",
  "operation-overlord"
]
```

FTS 文本：

```text
Mass Action Throttle
RedisMassActionAdmissionStore
operation-overlord
```

推荐 separator：

```text
newline
```

因为：

- 不改变 anchor token；
- 易调试；
- 不需要特殊 delimiter tokenizer 逻辑。

原始 authoritative anchors 仍来自 metadata。

FTS 中的 serialized text 不是业务数据。

---

# 17. Update Transaction

本节 Anchor 处理规则：正文实质变化且未提交 anchors 时清空旧值；正文未变化或仅空白等价变化时保留。显式提交 anchors 始终优先。实质变化采用现有 summary_policy.normalize_whitespace 比较规则（首尾空白清理、连续空白折叠）。 正文 FTS 仍须与实际保存的正文保持一致；空白等价仅用于判断是否清空 anchors，不允许跳过必要的正文索引同步。

## 17.1 Text Unchanged

如果：

```text
text omitted
```

则：

```text
do not update content FTS
do not regenerate embedding
```

如果 metadata 没显式包含 anchors：

```text
preserve anchor FTS
```

---

## 17.2 Text Changed

如果 text 变化：

```text
regenerate embedding
apply explicit caller anchors first
otherwise clear anchors only when normalized text changed
preserve anchors for whitespace-equivalent text changes
never generate anchors in server
replace content FTS row
replace anchor FTS row
```

---

## 17.3 Anchors Explicitly Changed

如果：

```text
metadata.anchors present
```

无论 text 是否改变：

```text
normalize anchors
sync index_values dimension=anchors
replace anchor FTS row
```

---

## 17.4 Keyword/Category Change

不影响：

```text
content FTS
anchor FTS
```

除非 anchors 同时变化。

---

# 18. FTS Replace Strategy

建议封装：

```python
_replace_content_fts(memory_id, text)
_replace_anchor_fts(memory_id, anchors)
```

实现语义：

```text
delete old FTS row
insert new FTS row
```

不要依赖 FTS rowid 与 `memories.rowid` 强绑定。

业务 identity 始终使用：

```text
memory_id
```

---

# 19. Soft Delete

当前 Memory 删除：

```text
deleted = 1
```

删除 transaction 中：

```text
UPDATE memories SET deleted=1, ...

DELETE content FTS row
DELETE anchor FTS row
DELETE memory_index_values
```

向量可以：

- 保留，但搜索必须通过 deleted filter 排除；
- 或删除对应 active profile vector。

保持现有语义即可。

Lexical/Anchor FTS 推荐直接删除，因为它们纯 derived。

---

# 20. Clear

`clear(namespace)`：

1. 找到 namespace 中 active memories；
2. soft delete；
3. 删除这些 memory 对应：
   - content FTS
   - anchor FTS
   - memory_index_values
4. 保持现有 outbox/version 语义。

必须在 bounded transaction strategy 下实现，避免一次大量 clear 导致事务过大。

具体 batching 延续现有 clear 设计。

---

# 21. search_lexical Service API

建议：

```python
def memory_search_lexical(
    self,
    query: str,
    k: int = 10,
    namespace: str | None = None,
) -> SearchResponse:
    ...
```

第一版不加入：

```text
threshold
keywords
categories
```

理由：

先建立最小、清晰的 lexical primitive。

未来可以再支持 metadata filters。

---

# 22. search_anchor Service API

建议：

```python
def memory_search_anchor(
    self,
    query: str,
    k: int = 10,
    namespace: str | None = None,
) -> SearchResponse:
    ...
```

第一版同样只支持：

```text
query
k
namespace
```

---

## 22.1 Shared Query Validation

query 必须是字符串；空字符串或纯空白拒绝，纯标点（无可检索 token）返回空结果。k 默认为 10，必须是 1–100 的整数，越界拒绝。查询长度上限与具体切词行为待技术验证确定。 MCP 和 Admin API 使用相同规则。

---

# 23. Namespace Handling

与 `search_semantic` 保持一致：

```text
unknown namespace
    ->
empty result envelope + namespace_not_found
```

不抛业务异常给 MCP 调用方。

---

# 24. Lexical Search SQL

概念 SQL：

```sql
SELECT
    m.id,
    bm25(memory_content_fts) AS score
FROM memory_content_fts
JOIN memories m
  ON m.id = memory_content_fts.memory_id
WHERE memory_content_fts MATCH ?
  AND m.deleted = 0
  AND (? IS NULL OR m.namespace = ?)
ORDER BY score
LIMIT ?;
```

注意 SQLite FTS5：

```text
bm25()
```

通常：

```text
smaller / more negative = better
```

因此 Public API 不应承诺：

```text
larger score means better
```

除非 service 做转换。

---

# 25. Public Score Contract

第一版固定：
```text
score = -raw_bm25
rank = 1-based result position
match_info.score_type = "fts5_bm25"
match_info.raw_bm25 = raw engine score
```
按 raw_bm25 升序排列，对外 score 越大越靠前。Semantic、Lexical、Anchor 的分数不能直接比较或相加。

---

# 26. Anchor Search SQL

与 content search 类似：

```sql
SELECT
    m.id,
    bm25(memory_anchor_fts) AS score
FROM memory_anchor_fts
JOIN memories m
  ON m.id = memory_anchor_fts.memory_id
WHERE memory_anchor_fts MATCH ?
  AND m.deleted = 0
  AND (? IS NULL OR m.namespace = ?)
ORDER BY score
LIMIT ?;
```

---

# 27. Query Escaping

FTS5 `MATCH` 有自己的 query syntax。

不能直接把用户字符串无处理传入。

需要新增：

```text
FTSQueryBuilder
```

职责：

```text
user query
    ->
safe lexical query
```

第一版默认行为建议：

```text
tokenize query
OR terms
```

而不是直接把 query 当作 FTS expression。

原因：

用户输入可能包含：

```text
"
:
-
(
)
*
NEAR
OR
AND
NOT
```

这些在 FTS5 中具有语法意义。

---

# 28. Query Semantics

第一版 search_lexical：

```text
user terms -> OR retrieval -> BM25 ranking
```

例如：

```text
PoolAcquirePendingLimitException pending 200
```

生成逻辑：

```text
"PoolAcquirePendingLimitException" OR pending OR 200
```

目的：

```text
high recall
```

BM25 负责排序。

未来可以增加：

```text
minimum_should_match
phrase boost
quoted phrase
```

但不在第一版。

---

# 29. Anchor Query Semantics

Anchor Search 更强调 identity。

第一版可以与 lexical 共用 QueryBuilder，但未来允许：

```text
phrase-aware
exact identifier boost
whole-anchor match
```

因此 API 层必须与 lexical 分离。

---

# 30. Tokenization Problem

这是实现前最重要的验证项之一。

系统数据包含：

```text
中文自然语言
English
CamelCase
snake_case
kebab-case
error codes
versions
configuration keys
URLs / paths
mixed text
```

例如：

```text
PoolAcquirePendingLimitException
operation-overlord
ORA-00060
subscriptionsPerConnection
atos-v11:mass:group
bge-m3
jina-embeddings-v2-base-zh
```

---

# 31. Tokenizer Design Goals

Tokenizer 必须尽量满足：

```text
1. 不丢失 identifier identity
2. 支持中英混合
3. 支持 exact technical token
4. 支持普通中文 lexical retrieval
5. Query 和 Document 使用一致规则
```

---

# 32. SQLite unicode61 Evaluation

SQLite FTS5 内置：

```text
unicode61
```

对英文和 Unicode token 有基础支持。

但它不是完整中文分词器。

因此 implementation 前必须 benchmark：

```text
Chinese natural language recall
technical identifier recall
mixed-language recall
```

---

# 33. Recommended Tokenizer Phase Strategy

## Phase A

先使用：

```text
unicode61
```

验证：

- technical identifiers
- English
- mixed identifiers
- basic Chinese behavior

因为当前 Memory Retrieval 已有 semantic search，Lexical Search 的第一价值主要是：

```text
exact / rare technical term
```

而不是替代 Semantic Search 做中文语义检索。

---

## Phase B

如果真实 benchmark 表明中文 lexical recall 不足，再考虑：

```text
custom tokenizer
pre-tokenization
jieba / pkuseg style segmentation
external lexical engine
```

不要第一版就引入复杂中文 analyzer。

---

# 34. Identifier Preservation

特别关注：

```text
operation-overlord
ORA-00060
atos-v11:mass:group
```

默认 tokenizer 可能拆成多个 token。

这种拆分并非一定错误。

例如：

```text
ORA-00060
```

拆成：

```text
ora
00060
```

仍可能有很强召回。

但必须验证：

```text
query "ORA-00060"
```

能否稳定排名到目标 Memory。

如果不能，再增加：

```text
identifier alias token
```

或 pre-tokenizer。

---

# 35. Match Info

SQLite FTS5 默认不会直接返回“哪些 normalized terms 命中”这一业务结构。

第一版推荐：

```text
match_info:
  type
  raw_bm25
```

而：

```text
matched_terms
matched_anchors
```

作为 optional enhancement。

不要为了 match-info 在第一版实现复杂 tokenizer duplication。

---

# 36. Search Result Contract

统一 envelope：

```json
{
  "search_id": "se_...",
  "total": 3,
  "items": [
    {
      "id": "...",
      "text": "...",
      "summary": "...",
      "metadata": {},
      "namespace": "remember-me",
      "score": 8.73,
      "rank": 1,
      "match_info": {
        "type": "lexical",
        "score_type": "fts5_bm25",
        "raw_bm25": -8.73
      }
    }
  ]
}
```

Anchor：

```json
{
  "match_info": {
    "type": "anchor",
    "score_type": "fts5_bm25",
    "raw_bm25": -10.11
  }
}
```

---

# 37. Search IDs

继续复用现有 search_id 生成机制和 se_ 前缀，不为 lexical / anchor 新建 ID 体系。检索策略由 audit 中的 tool 名及结果 match_info.type 区分。report 的输入结构不变。

---

# 38. MCP Tool Definitions

新增：

```text
search_lexical
search_anchor
```

---

## 38.1 search_lexical

Input：

```json
{
  "query": "string",
  "k": 10,
  "namespace": "optional"
}
```

限制：

```text
query: nonblank string; no searchable token -> empty results
k: integer, default 10, range 1..100; out of range -> validation error
```

Tool description：

```text
Search original memory content using lexical relevance.

Prefer when the query contains exact wording, identifiers,
error codes, configuration names, numbers, quoted text,
or terms likely to appear directly in the original memory.
```

---

## 38.2 search_anchor

Input：

```json
{
  "query": "string",
  "k": 10,
  "namespace": "optional"
}
```

Tool description：

```text
Search memories using distinctive retrieval anchors.

Prefer when the query contains a specific project, module,
class, API, error identifier, version, named concept,
or another distinctive reference that may identify a memory.
```

---

# 39. Agent Behavior

三个 primitive 分别暴露：

```text
search_semantic
search_lexical
search_anchor
```

允许 LLM：

```text
choose
retry
cross-check
```

不在 server 端强制：

```text
always call all three
```

未来 hybrid `search` 出现后，primitive 仍保留。

---

# 40. MemoryService Changes

沿用现有 MemoryService 依赖，不增加 anchor_generator 参数。新增 memory_search_lexical / memory_search_anchor，参数仅 query、k、namespace。服务层负责输入、namespace 和 anchors 校验，存储层负责事务与 FTS 查询。

---

# 41. Storage Protocol Changes

MemoryStorage 增加 search_lexical、search_anchor、rebuild_lexical_indexes。搜索参数仅 query、k、namespace；SQLiteStorage 直接实现，DualStorage 委托 primary，不另设 Repository 层。

---

# 42. SQLiteStorage Changes

新增主要职责：

```text
- FTS schema creation / migration
- content FTS synchronization
- anchor FTS synchronization
- lexical search
- anchor search
- FTS rebuild
- FTS table presence / single-version check and deterministic rebuild
```

---

# 43. DualStorage Changes

Read：

```text
search_lexical
search_anchor
```

直接 delegate：

```text
primary SQLiteStorage
```

Replica 不参与搜索。

Write：

继续：

```text
write primary
commit
background replica outbox sync
```

Anchor 因为在 metadata 中，会自然进入 NDJSON outbox payload。

---

# 44. NDJSONReplica Changes

不增加 lexical index。

只需要确保：

```text
metadata.anchors
```

完整保留。

旧记录没有 anchors：

```text
valid
```

解释为：

```text
[]
```

---

# 45. Import

Import 输入若包含：

```text
metadata.anchors
```

则：

```text
normalize
persist
index
```

如果旧备份不包含 anchors：

两种选择：

```text
A. import as []
B. regenerate anchors
```

推荐第一版：

```text
A. import as []
```

理由：

- Import 应尽量忠实恢复；
- 不应隐式调用 LLM 改写数据；
- Anchor 可以由外部调用方提取并通过明确更新流程后补。

---

# 46. Recovery

NDJSON -> SQLite Recovery：

1. 恢复 authoritative memory；
2. 恢复 metadata，包括 anchors；
3. 重建 metadata index_values；
4. 重建 active embedding profile（延续现有流程）；
5. 重建 content FTS；
6. 重建 anchor FTS。

顺序上：

```text
authoritative rows
    ->
metadata indexes
    ->
derived retrieval indexes
```

---

# 47. Rebuild Lexical Index

新增 storage maintenance：

```text
rebuild_lexical_indexes()
```

建议行为：

```text
BEGIN IMMEDIATE

clear/recreate content FTS
clear/recreate anchor FTS

for each non-deleted memory:
    index text
    index anchors

COMMIT
```

在同一事务中写入当前 lexical_index_version，只有两张 FTS 表均重建成功才提交。失败回滚；启动期间失败阻止进入 running。第一版采用完整事务，不按库大小改变恢复规则。

---

# 48. Anchor Backfill

当前阶段不实现服务端生成式回填。外部 LLM 或离线调用方可以提取历史记录的 anchors，再通过现有写入流程提交；提交数据执行第 7 节严格校验。

自动生成、失败状态、重试和专用 backfill 属于可选 Phase 3，必须在 Phase 2 评测后另行设计，不能因省略 anchors 隐式启动。

---

# 49. Configuration

第一版尽可能少配置。Anchor 数量上限 5 和单项长度上限 128 为明确输入契约，BM25 使用 FTS5 默认参数。沿用现有 search_id 机制；不新增生成 provider、生成超时或生成开关配置。

搜索参数仅 query、k、namespace。k 默认 10，固定范围 1–100，越界拒绝；不为不同检索方式建立重复配置。

---

# 50. Schema Migration

FTS 是 derived data。启动时先检查当前版本所需 FTS 表是否存在，并读取 storage_state.lexical_index_version。任一表不存在、版本记录缺失或版本不匹配，均在进入 running 前完整重建该版本的 FTS 索引。

迁移不调用 LLM，不通过 row count 判断索引正确，也不因 embedding profile 变化重建 FTS。

---

# 51. Lexical Index State

只持久化一个 storage_state.lexical_index_version。第一版不增加 ready/stale/rebuilding/error 状态机。服务是否可用由既有 Runtime 生命周期决定。索引版本只在完整重建成功的事务中写入。

---

# 52. Index Versioning

使用一个 LEXICAL_INDEX_VERSION，覆盖 content / anchor FTS schema、tokenizer 及索引表示规则。相关规则变化时递增版本，不另设 ANCHOR_INDEX_VERSION。

首次建库、缺表或版本不匹配均重建。版本匹配且所需表存在时复用现有索引；这不是对任意物理损坏的正确性证明。

---

# 53. Startup

在现有主库 writer lock 和 Runtime.start 生命周期内完成：
1. 检查所需 FTS 表及 lexical_index_version。
2. 缺表、缺版本或版本不匹配时，从 live memories 全量重建。
3. 两个索引及版本标记在同一事务中提交；全部启动检查成功后才进入 running。
4. 重建失败则回滚，startup fail，按现有启动失败流程清理资源；整个服务不进入 running，不提供 lexical/anchor search。

规则不依赖数据量或预计耗时，不设置“快速自动重建、慢则另行决定”的分支。

---

# 54. Audit

`search_lexical` / `search_anchor` 记录：

```text
tool
status
duration_ms
namespace
search_id
params
result
memory_refs
context
```

不记录完整 Memory text。

建议 result audit 保留：

```text
ids
scores
ranks
score_type
```

---

# 55. Anchor Write Audit

当前 Anchor 输入沿用 add/update/import 的写入审计。不增加 anchor_generation 事件、生成耗时或生成失败事件，不记录完整 Memory 正文。未来 Phase 3 若获准引入服务端生成，再单独定义相应审计。

---

# 56. Feedback

现有：

```text
memory_report(search_id, ratings)
```

无需改变结构。

关键是确保：

```text
semantic
lexical
anchor
```

都生成 search_id。

后续 benchmark 可以从 audit 恢复：

```text
query
retrieval type
ranked result
user positive rating
```

---

# 57. Benchmark Dataset

技术验证须确认 QueryBuilder 与 unicode61 切词一致性，并确认真实 query、目标 memory 和数据来源；在同一数据集上建立现有 Semantic 基线，再比较 Lexical 与 Anchor。

继续使用真实：

```text
query -> positively rated memory
```

样本。

新增评测：

```text
Semantic Only
Lexical Only
Anchor Only
```

然后：

```text
Agent-selected Retrieval
```

未来再评测 fusion。

---

# 58. Metrics

至少：

```text
Hit@1
Hit@5
MRR
```

建议新增：

```text
Recall@10
Latency p50/p95
No-result rate
```

---

# 59. Lexical Benchmark Categories

建议把 query 分组：

```text
A. Exact identifier
B. Error code
C. Class/API
D. Technical phrase
E. Chinese natural language
F. Mixed Chinese/English
G. Number/config/version
```

这样可以判断：

```text
BM25 到底改善了哪类 query
```

而不是只看 aggregate score。

---

# 60. Anchor Benchmark

重点观察：

```text
Anchor Only Hit@K
Semantic misses rescued by Anchor
Lexical misses rescued by Anchor
```

最关键指标：

```text
incremental recall over Semantic + Lexical
```

但 Anchor 是否长期保留不能只看一版数据，需要结合 agent behavior。

---

# 61. Keyword Observation

Keywords 不进入删除评估。

需要分别观察：

```text
Retrieval Value
Organization Value
Relationship Value
```

未来 Relationship HLD 可以使用：

```text
shared keywords
keyword rarity
keyword co-occurrence
semantic similarity
temporal relation
```

构建 Memory Relationship。

---

# 62. Failure Modes

## 62.1 FTS Search Failure

如果 SQLite FTS query 执行失败：

```text
return tool error
audit
```

不要自动 fallback semantic。

因为：

```text
primitive API 应保持行为可观察
```

---

## 62.2 Invalid Anchor Input

当前服务端不生成 Anchors。调用方输入非法时拒绝整个写入；不静默转换为 []。

---

## 62.3 FTS Index Update Failure

因为 FTS 与 Memory 同一 SQLite transaction：

```text
rollback entire write
```

不能提交 Memory 但索引缺失。

---

## 62.4 Rebuild Failure

重建事务失败则回滚；启动期间必须 startup fail，不进入 running。版本标记与 FTS 重建同事务提交，不持久化复杂错误状态机。显式维护重建失败明确报错，保留事务回滚后的数据。

---

# 63. Consistency Rules

必须满足：

```text
Committed active Memory
    =>
content FTS row exists

Committed Memory with non-empty anchors
    =>
anchor FTS row exists

Soft-deleted Memory
    =>
no searchable FTS row

metadata.anchors
    =>
index_values anchors consistent
    =>
anchor FTS consistent
```

---

# 64. Integrity and Recovery Policy

第一版依赖版本、可重建索引与日常事务原子性，不实现基于 row count 的自动健康判定，也不引入复杂局部一致性检查或自动局部修复。

日常 add/update/delete/clear/import 将主记录、关联和 FTS 放在同一事务中。Recovery 从权威数据完整重建 FTS，成功后再发布恢复结果。缺表或版本变化遵循第 50–53 节启动重建规则。

显式维护重建可以从权威数据重新建立索引。运行期查询异常明确报错并审计，不降级成空结果或 Semantic Search。

---

# 65. Performance Expectations

当前 Memory 数量较小，FTS5 查询应为毫秒级。

目标：

```text
search_lexical p95 < 20ms
search_anchor  p95 < 20ms
```

此处不含：

```text
network
MCP serialization
LLM
```

这只是初始工程目标，不是硬 SLA。

---

# 66. Write Overhead

当前新增写入成本为 Anchor 校验、关系更新及 FTS 行替换，没有服务端 LLM 生成调用。具体写入与查询开销需要实测，不以设计推断替代测量。可选 Phase 3 的生成延迟和调用成本须单独评估。

---

# 67. Security / Input Safety

FTS query 必须经过：

```text
FTSQueryBuilder
```

不能允许用户控制原始 FTS5 syntax。

防止：

```text
malformed query
unexpected operator semantics
very expensive pathological expression
```

---

# 68. Backward Compatibility

旧 Memory：

```text
no metadata.anchors
```

完全合法。

旧 NDJSON：

```text
no metadata.anchors
```

完全合法。

旧客户端：

```text
不调用 search_lexical/search_anchor
```

无影响。

现有：

```text
search_semantic
search_meta
list_keywords
stats
```

保持兼容。

---

# 69. Admin API

本轮交付两个独立端点：

```text
POST /api/memories/search/lexical
POST /api/memories/search/anchor
```

参数仅 query、k、namespace，复用服务层校验及既有 namespace 错误契约。Phase 1 交付 lexical，Phase 2 交付 anchor；不改变既有搜索端点，本轮不增加 UI 搜索模式。

---

# 70. Stats

可提供 anchor_total、memories_with_anchors 和 lexical_index_version；不新增 anchor_index_version 或 lexical_index_status 状态机。Keyword stats 保持现有契约。

---

# 71. list_keywords

保持不变。

不改名。

不迁移为 anchor list。

未来如果需要 Anchor 浏览，可以独立增加：

```text
list_anchors
```

但不属于第一阶段必要能力。

---

# 72. Testing Strategy

## 72.1 Unit Tests

Anchor normalization：

```text
empty
duplicates
case duplicate
unicode normalization
over max count
over max length
explicit []
```

Anchor caller input：

严格输入校验，包括超限拒绝、空白拒绝、安全去重；外部 LLM 提交也适用相同规则。

FTSQueryBuilder：

```text
quotes
hyphen
colon
parentheses
reserved words
Chinese
mixed identifiers
```

---

## 72.2 SQLiteStorage Tests

Add：

```text
memory row
content FTS
anchor FTS
index_values anchors
```

Update：

```text
text unchanged
text changed
anchors explicit
anchors cleared
```

Delete：

```text
soft delete
FTS removed
```

Clear：

```text
namespace isolation
```

---

## 72.3 Search Tests

Lexical：

```text
exact error code
class name
rare term
common term
namespace
deleted exclusion
ranking
```

Anchor：

```text
exact anchor
partial token
multiple anchors
namespace
no anchors
deleted exclusion
```

---

## 72.4 Recovery Tests

```text
export
delete DB
recover from NDJSON
rebuild FTS
search same anchors
search same content
```

---

## 72.5 DualStorage Tests

确保：

```text
anchor metadata replicated
FTS not replicated
primary search works
recovery rebuild works
```

---

## 72.6 Anchor Isolation and Strict Input Acceptance

- 超过 5 项、规范化后超过 128 字符、空字符串、纯空白、非字符串项、非数组及显式 null 均拒绝整个写入；数据库无部分变更。
- 验证 NFKC、ASCII 大小写去重、顺序保留、恰好 5 项、恰好 128 字符及显式 []。
- 添加和更新 anchors 后，关系表及 Anchor FTS 正确，`index_value_embeddings` 无 anchor 向量。
- 启动 embedding 回填、profile 完整性统计、完整 reindex、index-value reindex 及 profile 激活均不要求或生成 anchor 向量。
- embedding model/profile 切换不改变 Anchor 关系和 Anchor FTS，不触发其重建。
- `IndexValueResolver` 拒绝 anchors semantic resolution。
- 导入恢复后 Anchor 关系和 FTS 可重建，Anchor Search 正常，且不产生 anchor 向量。
- 外部 LLM 提交同样执行严格输入规则，没有宽松 API 旁路。

---

## 72.7 Correctness Guarantees vs Retrieval Quality

补充验收：正文实质变化且遗漏 anchors 清空关系及 Anchor FTS；空白等价时保留；显式 anchors 优先。两种 MCP 搜索及 Admin API 验证 k 默认值、1/100 边界、越界拒绝、空白查询拒绝及纯标点空结果。

Correctness guarantees：
- transaction atomicity
- soft-deleted memory invisible
- namespace isolation
- update consistency
- import/recovery consistency
- backward compatibility

上述行为作为自动化测试必须验证的正确性契约。覆盖写入中途故障回滚、FTS 缺表/版本缺失/版本不匹配重建、重建失败导致启动失败、成功后才进入 running、恢复发布前完成重建。测试提供所覆盖场景的保证，不声称证明任意故障下绝对正确。

Retrieval quality：
- Chinese lexical recall
- error-code recall
- identifier recall
- Anchor incremental value
- Agent tool-selection quality

这些效果只能由真实 query 数据 benchmark 评估。BM25 与 Anchor designed to improve / intended to cover exact lexical and identity signals；不预先承诺提高召回率，也不以正确性测试通过作为检索效果提升的证据。

---

# 73. Implementation Phases

本轮 coding plan 覆盖 Phase 1、2，分别交付验收；Phase 3 不纳入。Manual Anchors 包括调用方 LLM 提取并提交。

### Phase 1 — Lexical Search
实现正文 FTS、共享最小 FTSQueryBuilder、search_lexical、audit/search_id、事务同步及迁移恢复。先验证 FTS5 / unicode61 / BM25，跑正确性测试和真实查询 benchmark。

### Phase 2 — Manual Anchors and Anchor Search
支持调用方提供 metadata.anchors（调用方可以是 LLM），复用 index_values 关系但隔离 embedding 生命周期，实现 Anchor FTS 与 search_anchor。验证严格输入、导入恢复和 Anchor 相对 Semantic + Lexical 的增量价值。

### Phase 3 — Optional Automatic Anchor Generation
仅在 Phase 2 的真实数据支持进一步投入时考虑服务端自动生成、failure state 和 backfill。该阶段不是当前实现承诺；provider、失败重试及成本策略须另行设计。若 Anchor 增量价值不足，可暂缓。

---

# 74. Recommended Coding Order

1. 正文 FTS schema、单一版本、原子重建和启动失败处理。
2. 共用 FTSQueryBuilder、SQLiteStorage.search_lexical、Storage protocol 和 MemoryService 接口。
3. MCP 工具、现有 search_id/report/audit 集成及正确性测试。
4. Phase 1 tokenizer/BM25 benchmark。
5. 严格 Anchor policy、关系索引和 embedding 生命周期隔离。
6. Anchor FTS、search_anchor 及 MCP/audit 集成。
7. 各阶段交付前完成对应 add/update/delete/clear/import/recovery 事务路径与兼容性测试。
8. Phase 2 增量价值及工具选择 benchmark，再决定是否启动 Phase 3。

---

# 75. Remaining Evaluation Questions

已固定：两个 FTS 表、直接 Storage 实现、共用最小 QueryBuilder、score=-raw_bm25、现有 search_id、query/k/namespace、单一版本与确定性启动重建。

仍需数据回答：unicode61 的中文及技术标识符召回、是否需要预分词、Anchor 增量价值、未来是否需要 whole-anchor boost 或其他评分方式。第一版不支持 metadata filters，不增加 list_anchors。

已确定本轮交付两个 Admin API（不改 UI）；正文实质变化且遗漏 anchors 时清空，空白等价时保留，显式提交优先；k 默认 10、范围 1–100。仅查询长度上限、具体切词行为及评测数据来源待技术检查收敛。可选 Phase 3 的 provider 和失败状态另行设计。

---

# 76. Final Component Diagram

```text
MCP / Admin / Agent
        |
   MemoryService
        |
 MemoryStorage protocol
        |
 SQLiteStorage (DualStorage delegates to primary)
        |
        +-- memory_embeddings           Semantic
        +-- memory_content_fts          Lexical
        +-- memory_anchor_fts           Anchor
        +-- index_values / memory_index_values
        |      keywords / categories / anchors
        +-- index_value_embeddings
        |      keywords / categories only
        +-- memories (authoritative data)
                    |
               dual outbox
                    |
               NDJSONReplica
```

---

# 77. Final Design Principles

1. Semantic、Lexical、Anchor 是三个独立 Retrieval Primitive。

2. Public API 描述 capability，不绑定 BM25/FTS5/bge-m3 等具体技术。

3. Lexical Search 只搜索 Memory Content。

4. Anchor Search 只搜索 Memory Anchors。

5. Anchor 是 identity signal，不是 topic keyword。

6. Keyword 是 concept signal，状态为 Observational Feature，不 deprecated。

7. Keyword 的未来价值可能在 Memory Relationship，而不只在 retrieval。

8. Anchor 使用现有 `index_values` generic infrastructure，不创建重复业务表。

9. Content/Anchor FTS 都是 Derived Data，必须可重建。

10. NDJSON Replica 保存 Anchor 本身，但不保存 FTS index。

11. Lexical index lifecycle 与 embedding profile lifecycle 独立。

12. FTS index 与 authoritative Memory update 必须处于同一 SQLite transaction。

13. 当前由调用方提供 Anchors，服务端严格校验；自动生成只属于评测后另行决定的 Phase 3。

14. FTS index update failure 必须 rollback 当前 Memory write。

15. 三种 search raw score 不直接比较。

16. `search_id + memory_report + audit` 继续作为 Retrieval Quality Feedback 基础。

17. 第一版优先简单、可观察、可 benchmark，不提前实现 hybrid fusion。

18. 所有后续 ranking/fusion 决策必须由真实 query 数据驱动。

补充强制原则：anchors 仅复用通用关系索引，完全排除在 embedding 生命周期之外（第 10.3 节）；所有 API Anchor 输入严格校验（第 7 节），外部生成候选的宽松整理只发生在提交前。
