# remember-me Retrieval Extension — High-Level Design

## 1. Purpose

本文描述 `remember-me` 在现有 Semantic Retrieval 基础上增加 Lexical Retrieval 与 Anchor Retrieval 的高层设计。

目标不是替换当前 `search_semantic`，而是形成三个职责明确、可以独立使用和评测的 Retrieval Capability：

```text
search_semantic
search_lexical
search_anchor
```

未来可以在三者之上增加：

```text
search
```

统一执行 Hybrid Retrieval / Fusion / Reranking。

本阶段不实现统一 Hybrid Search，也不提前确定不同 Retrieval Signal 的融合权重。

---

## 2. Existing System Context

当前系统核心调用路径：

```text
MCP / Admin API
       |
       v
 MemoryService
       |
       +------------------+
       |                  |
       v                  v
    Storage            Embedder
       |
       v
 SQLiteStorage
```

生产数据以 SQLite 为权威主库。

Dual Storage 模式下：

```text
              DualStorage
              /         \
             /           \
    SQLiteStorage     NDJSONReplica
       Primary          Disaster
       Store            Recovery
```

NDJSON Replica 不保存向量。

当前 Semantic Search：

```text
search_semantic
      |
      v
MemoryService.memory_search()
      |
      +--> resolve namespace
      |
      +--> resolve keywords/categories
      |        |
      |        v
      |   IndexValueResolver
      |
      +--> embed_query(query)
      |
      v
storage.search()
      |
      +--> metadata/index filters
      |
      +--> cosine similarity
      |
      v
    Top K
```

现有 `keywords` 和 `categories` 已通过：

```text
index_values
memory_index_values
index_value_embeddings
```

形成通用 Metadata Index Infrastructure。

该机制应尽量复用，而不是引入一套与现有模型重复的业务索引体系。

---

## 3. Problem

当前 Retrieval 的主要能力来自 Semantic Search。

Semantic Search 擅长：

- 不同措辞表达相同概念
- 自然语言模糊描述
- Query 与 Memory 原文没有直接词面重叠
- Concept / event / decision / problem retrieval

例如：

```text
Query:

"之前那个为了避免批量操作把下游压垮的方案"
```

即使 Memory 使用：

```text
Mass Action Throttle
```

Embedding 仍有机会召回。

但是 Semantic Retrieval 对以下信号并不天然稳定：

```text
PoolAcquirePendingLimitException
ORA-00060
RedisMassActionAdmissionStore
operation-overlord
subscriptionsPerConnection
atos-v11:mass:group
```

这些值的价值主要来自：

```text
exact identity
rarity
lexical distinctiveness
```

而不是 semantic similarity。

因此需要增加：

```text
Lexical Retrieval
Anchor Retrieval
```

---

## 4. Retrieval Model

目标 Retrieval Architecture：

```text
                        Query
                          |
          +---------------+---------------+
          |               |               |
          v               v               v
      Semantic         Lexical          Anchor
          |               |               |
     Embedding       Content Index     Anchor Index
          |               |               |
     Vector Score     BM25 Score      Anchor Score
          |               |               |
          v               v               v
 search_semantic   search_lexical   search_anchor
```

三个 Retrieval Capability 独立存在。

它们不是三个不同名字的同一种搜索，而是回答三个不同问题。

---

## 5. Semantic Retrieval

### 5.1 API

现有：

```text
search_semantic
```

继续保留。

不改变现有核心语义。

### 5.2 Responsibility

Semantic Retrieval 回答：

> Which memories have meaning similar to this query?

主要数据源：

```text
memory.text
        |
        v
memory_embeddings
```

Query：

```text
"上次那个 Netty 连接池满了的问题"
```

Memory：

```text
"PoolAcquirePendingLimitException occurred when pending acquire reached..."
```

即使 Query 不包含异常类名，也应该通过 semantic similarity 召回。

### 5.3 Existing Metadata Filtering

现有 `search_semantic` 支持：

```text
namespace
categories
keywords
```

并通过：

```text
IndexValueResolver
```

对未知 index value 做 semantic resolution。

本设计第一阶段不要求删除或修改这一行为。

Keyword 的未来定位独立于本 Retrieval Extension，不应因为引入 Anchor 而改变现有 Search Contract。

---

## 6. Lexical Retrieval

### 6.1 API

新增：

```text
search_lexical
```

### 6.2 Responsibility

Lexical Retrieval 回答：

> Which memories contain lexically relevant terms from this query?

主要搜索：

```text
memory.text
```

它关注：

```text
exact terms
rare terms
term frequency
document frequency
document length
```

第一版使用 SQLite FTS5 + BM25。

### 6.3 Example

Memory A：

```text
Reactor Netty reported PoolAcquirePendingLimitException
when pending acquire reached 200.
```

Memory B：

```text
Reactor Netty connection pool tuning notes.
```

Query：

```text
PoolAcquirePendingLimitException pending 200
```

该示例用于检验 Lexical Search 是否能使 Memory A 排在 Memory B 前；实际排名和召回改善必须由 benchmark 验证。

---

## 7. Retrieval Anchor

### 7.1 Definition

Anchor 是：

> A distinctive term or short phrase that a future query may use to identify a specific memory.

Anchor 的目的不是描述 Memory 的主题。

Anchor 的目的在于提供：

```text
identity evidence
```

### 7.2 Examples

Good Anchors：

```text
PoolAcquirePendingLimitException
ORA-00060
RedisMassActionAdmissionStore
MassActionThrottleService
operation-overlord
atos-v11:mass:group
Mass Action Throttle
```

通常较差的 Anchor：

```text
Java
Performance
Issue
Design
Database
Cache
```

这些词通常已经能够通过 semantic similarity 表达，而且辨识度较低。

---

## 8. Anchor vs Existing Keywords

当前系统：

```text
metadata.keywords
```

定义为：

> Memory content concepts.

Anchor 与现有 Keyword 的语义不同。

Keyword 回答：

```text
What concepts/topics does this memory contain?
```

Anchor 回答：

```text
What distinctive reference may allow a future query
to identify this memory?
```

例如：

```text
Memory:

Mass Action Throttle 使用 Redis ZSET，
实现位于 operation-overlord 中的
RedisMassActionAdmissionStore。
```

可能存在：

```text
keywords:

Redis
Throttle
Distributed System
```

而：

```text
anchors:

Mass Action Throttle
RedisMassActionAdmissionStore
operation-overlord
```

因此本设计不把 Anchor 定义为 Keyword 的简单重命名。

---

## 9. Keyword Strategy

当前系统保留：

```text
metadata.keywords
```

Keyword 与 Anchor 具有不同的长期语义：

```text
Keyword
    -> Content Concept

Anchor
    -> Retrieval Identity
```

### 9.1 Current Status

Keywords 定位为：

```text
Feature Status: Observational
```

即：

- 保留现有数据模型；
- 保留现有能力；
- 不列入 deprecated feature；
- 当前不继续扩大其在核心 Retrieval Pipeline 中的职责；
- 持续通过实际数据观察其价值；
- 不因为 Semantic / Lexical / Anchor Retrieval 的引入而删除。

当前已有 Keyword Capability 包括：

```text
metadata.keywords
index_values.dimension = keywords
IndexValueResolver
search_meta keyword filtering
memory_list_keywords
keyword statistics
keyword semantic resolution
```

这些能力继续存在。

### 9.2 Keyword Is Not Anchor

禁止因为引入 Anchor 而把：

```text
metadata.keywords
```

直接迁移或重命名为：

```text
metadata.anchors
```

二者表达不同的信息。

Keyword 表达：

> What concepts are contained in this memory?

Anchor 表达：

> What distinctive reference may identify this memory in a future query?

因此同一条 Memory 可以同时拥有：

```text
content
keywords
anchors
```

三者不存在替代关系。

### 9.3 Current Retrieval Role

当前 Retrieval Architecture 的主要 Search Signal 为：

```text
Semantic
Lexical
Anchor
```

即：

```text
Query
  |
  +--> search_semantic
  |
  +--> search_lexical
  |
  +--> search_anchor
```

Keyword 当前主要继续承担已有的：

```text
filtering
browsing
concept lookup
metadata organization
```

而不是作为第四个与 Semantic / Lexical / Anchor 并列的主要 Retrieval Engine。

这只是当前职责划分，不限制未来用途。

### 9.4 Future Memory Relationship Usage

Keywords 未来可能成为 Memory Relationship 建模的重要输入。

例如：

```text
Memory A
keywords:
Redis
Redisson
Local Cache

Memory B
keywords:
Redis
Sentinel
Replication
```

它们可能因为共享：

```text
Redis
```

形成某种弱关系。

另一组：

```text
Memory C
keywords:
Hibernate
JPA
Optimistic Lock

Memory D
keywords:
Hibernate
JPA
Transaction
```

可能形成：

```text
shared concepts
related memories
topic clusters
```

因此 Keyword 的潜在长期价值不仅是 Search，还可能包括：

```text
Memory
  |
  v
Keywords / Concepts
  |
  +--> related memory discovery
  |
  +--> concept clustering
  |
  +--> memory relationship graph
  |
  +--> topic aggregation
  |
  +--> navigation / browsing
  |
  +--> higher-level memory organization
```

具体 Relationship Model 不属于本 HLD Scope。

本设计只要求：

> Retrieval Extension 不应破坏或移除未来利用 Keywords 构建 Memory Relationship 的可能性。

### 9.5 Relationship With Categories

当前语义：

```text
keywords
    -> memory content concepts

categories
    -> memory nature / classification

anchors
    -> retrieval identity
```

三者应保持不同语义：

```text
                   Memory Metadata
                         |
          +--------------+--------------+
          |              |              |
          v              v              v
      Keywords       Categories       Anchors
          |              |              |
       Concepts     Classification    Identity
          |              |              |
          v              v              v
    Relationships      Filter       Retrieval
     / Browsing
```

未来具体能力可能交叉，但数据语义不应因此混淆。

---

## 10. Anchor Storage

Anchor 属于 Memory 的业务数据。

推荐存储在：

```text
metadata.anchors
```

例如：

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

这样保持当前 Memory Business View：

```text
memories
    |
    +--> text
    +--> summary
    +--> metadata
    +--> namespace
```

不需要在 `memories` 主表增加新的业务列。

---

## 11. Reuse Existing Index Value Infrastructure

当前已有：

```text
index_values(
    id,
    dimension,
    value
)

memory_index_values(
    memory_id,
    index_value_id
)
```

当前 dimension：

```text
keywords
categories
```

Anchor 可以增加新的 dimension：

```text
anchors
```

形成：

```text
index_values

dimension       value
-------------------------------------------
keywords        Redis
categories      technical
anchors         operation-overlord
anchors         RedisMassActionAdmissionStore
anchors         Mass Action Throttle
```

关联关系继续使用：

```text
memory_index_values
```

因此 Anchor 不需要设计新的：

```text
memory_anchor
anchor
memory_anchor_mapping
```

业务表。

现有 generic index-value infrastructure 已经可以表达这种关系。

---

## 12. Anchor / Embedding Lifecycle Isolation

Anchors 仅复用 index_values / memory_index_values 的关系结构，完全隔离于 embedding 生命周期：
- 不进入 index_value_embeddings。
- 不参与向量准备、完整性统计、启动 embedding 回填、profile reindex 或 reindex-index-values。
- embedding model/profile 变化、激活或回滚不触发 Anchor 关系及 FTS 重建。
- IndexValueResolver 不对 anchors 做 semantic resolution。

维度注册表以 requires_embedding 区分：keywords/categories 为 true，anchors 为 false。Python 遍历、SQL 查询及向量写入入口统一执行该规则。Anchor Search 使用独立 FTS5 索引。

---

## 13. Caller-Provided Anchors

当前 Phase 1/2 由调用方 LLM 或人工客户端提取 Anchors，通过 add/update 的 metadata.anchors 提交。服务端负责严格校验、规范化、持久化及事务内索引同步。

新增时未提交 anchors 不触发自动生成；显式 [] 合法，在更新时表示清空。仅修改其他 metadata 且未提交 anchors 时保留原值。正文实质变化且未提交 anchors 时清空旧值；正文未变化或仅空白等价变化时保留。显式提交 anchors 始终优先。实质变化采用现有 summary_policy.normalize_whitespace 比较规则（首尾空白清理、连续空白折叠）。

---

## 14. Optional Future Anchor Generation

当前不引入服务端 AnchorGenerator、生成 provider、超时、失败状态或自动 backfill。

Phase 2 先评测人工或调用方提供的 Anchors 是否具有增量价值。只有评测支持进一步投入时，才考虑可选 Phase 3 的服务端自动生成、失败状态与回填，并单独确定其设计。若价值不足，可以暂缓。

外部生成流程可在提交前过滤候选并最多保留 5 项；提交 API 后一律按严格调用方输入处理，不设 LLM 宽松校验旁路。

---

## 15. Anchor Extraction and Input Rules

以下指导面向调用方 LLM，不是服务端执行的 Prompt：
```text
Extract 0-5 distinctive retrieval anchors from the memory.
Prefer names, projects, classes, APIs, errors, config keys,
versions, identifiers and distinctive phrases.
Do not invent anchors. Provide [] if none.
Submit them in metadata.anchors.
```

调用方输入必须是字符串数组，原始数组超过 5 项即拒绝。每项做 NFKC、首尾空白清理和内部空白折叠；规范化后为空或超过 128 个 Unicode 字符即拒绝，不静默截断或过滤非法项。null、非数组、非字符串项均拒绝。

保存大小写形式，按 NFKC 及 ASCII 忽略大小写安全去重，保留首次出现顺序。先检查原始数量，再去重。任一项非法则拒绝整个写入，校验在 embedding 调用与持久化之前完成。

---

## 16. Anchor Search

### 16.1 API

新增：

```text
search_anchor
```

### 16.2 Responsibility

Anchor Search 回答：

> Which memories are identified by distinctive references contained in this query?

Query：

```text
operation-overlord 里面那个 Mass Action Throttle
```

Anchor：

```text
operation-overlord
Mass Action Throttle
```

这是 Anchor Search 期望覆盖的 identity signal；实际排名及增量价值必须由 benchmark 验证。

---

## 17. Anchor Scoring

第一版 Anchor Search 使用独立 Anchor FTS 表的 FTS5 BM25。对外 score = -raw_bm25，保留 rank 和 match_info.raw_bm25。

未来是否采用 IDF 累加、whole-anchor boost 或其他评分方式，由真实 benchmark 决定，不属于当前实现。

---

## 18. Lexical Index

Lexical Search 需要针对：

```text
memory.text
```

建立全文 lexical index。

该索引属于：

```text
Derived Data
```

而不是 authoritative business data。

其内容可以从：

```text
memories.text
```

完全重建。

---

## 19. Anchor Index

Anchor Index 同样属于 Derived Data。

权威 Anchor 数据位于：

```text
memories.metadata.anchors
```

以及现有通用：

```text
index_values
memory_index_values
```

Anchor lexical index 可以从这些数据重新构建。

因此：

```text
Anchor Business Data
        |
        v
metadata.anchors
        |
        v
index_values / memory_index_values
        |
        v
Anchor Lexical Index
```

---

## 20. Lexical Infrastructure

沿用现有 MemoryService → MemoryStorage protocol → SQLiteStorage 路径，直接实现 search_lexical 和 search_anchor；DualStorage 将搜索委托给 SQLite primary。

两种搜索共享一个最小 FTSQueryBuilder：安全构造字面查询词的 OR 表达式，不将用户输入直接作为原始 FTS 语法执行。参数仅 query、k、namespace；默认 k=10，范围 1–100，越界拒绝。第一版不增加 threshold、keywords 或 categories。

不新增 LexicalSearchRepository / AnchorSearchRepository。两个物理 FTS 表承担字段和 ranking 统计隔离。

---

## 21. SQLite Integration

第一版确定使用 SQLite FTS5 + BM25，分别建立：
- memory_content_fts：每个 live memory 一行，索引正文。
- memory_anchor_fts：每个有非空 anchors 的 live memory 一行，索引 anchors。

FTS 由 SQLiteStorage 应用代码维护，与权威记录和关系索引在同一事务中提交；不使用触发器。不将业务 identity 与 SQLite rowid 强绑定，以 memory_id 关联。

---

### Query Input and Admin API

query 必须是字符串；空字符串或纯空白拒绝，纯标点（无可检索 token）返回空结果。k 默认为 10，必须是 1–100 的整数，越界拒绝。查询长度上限与具体切词行为待技术验证确定。

本轮提供 POST /api/memories/search/lexical 和 POST /api/memories/search/anchor，不改变既有端点，也不增加 UI 搜索模式。

---

## 22. Tokenization

系统包含大量中英文混合技术 Memory，例如：

```text
Redis 主从复制延迟
PoolAcquirePendingLimitException
operation-overlord
MassActionThrottleService
ORA-00060
```

因此 lexical retrieval 的 Tokenization 是关键设计点。

Detailed Design 必须验证：

- Chinese tokenization
- English words
- CamelCase identifiers
- snake_case identifiers
- kebab-case identifiers
- error codes
- version numbers
- configuration keys
- mixed Chinese/English text

例如必须考虑：

```text
PoolAcquirePendingLimitException
operation-overlord
ORA-00060
subscriptionsPerConnection
atos-v11:mass:group
```

不能因为 tokenizer 拆分策略导致 distinctive identifier 丢失。

第一版先验证 FTS5 unicode61；若真实 benchmark 显示不足，再决定是否引入预分词或其他 analyzer，不预先承诺中文及标识符召回效果。

---

## 23. Search Result Contract

新增搜索复用 search_id / total / items 外层结构。结果包含 score、从 1 开始的 rank，以及 match_info.type、score_type 和 raw_bm25。

固定 score = -raw_bm25，越大越靠前；match_info.score_type = "fts5_bm25"。三种检索方式的分数不可直接比较或相加。matched_terms / matched_anchors 属于可选增强，第一版不要求提供。既有 Semantic Search 契约保持兼容。

search_id 沿用现有生成机制及 se_ 前缀，不新增 lexical/anchor ID 体系；检索类型通过 tool 和 match_info.type 识别。

---

## 24. Semantic Result

例如：

```json
{
  "id": "...",
  "score": 0.82,
  "rank": 1,
  "match_info": {
    "type": "semantic"
  }
}
```

---

## 25. Lexical Result

```json
{
  "id": "...",
  "score": 7.31,
  "rank": 1,
  "match_info": {
    "type": "lexical",
    "score_type": "fts5_bm25",
    "raw_bm25": -7.31
  }
}
```
分数仅为结构示例，不表示质量承诺。

---

## 26. Anchor Result

```json
{
  "id": "...",
  "score": 9.87,
  "rank": 1,
  "match_info": {
    "type": "anchor",
    "score_type": "fts5_bm25",
    "raw_bm25": -9.87
  }
}
```
matched_anchors 是未来可选增强。

---

## 27. MCP Tools

现有 MCPApplication 已注册多个工具，并通过统一：

```text
handle_call_tool
```

分派。

新增：

```text
search_lexical
search_anchor
```

后形成：

```text
add
search_semantic
search_lexical
search_anchor
search_meta
...
```

三种 Search 应分别产生：

```text
search_id
```

并进入现有 AuditJournal。

---

## 28. Tool Descriptions

Tool Description 对 LLM Tool Selection 非常重要。

### search_semantic

```text
Search memories by meaning.

Use when the query describes a concept, event, problem,
decision, or idea without relying on exact wording.
```

### search_lexical

```text
Search original memory content using lexical relevance.

Prefer when the query contains exact wording, identifiers,
error codes, configuration names, numbers, quoted text,
or terms likely to appear directly in the original memory.
```

### search_anchor

```text
Search memories using distinctive retrieval anchors.

Prefer when the query contains a specific project, module,
class, API, error identifier, version, named concept,
or another distinctive reference that may identify a memory.
```

---

## 29. Agentic Retrieval

独立暴露三种 Search Capability 的一个重要目的，是允许 Agent 自己选择 Retrieval Strategy。

例如：

```text
Query:

"之前那个防止批量操作压垮下游的设计"
```

Agent 可以：

```text
search_semantic
```

而：

```text
Query:

"RedisMassActionAdmissionStore"
```

Agent 可以：

```text
search_anchor
```

而：

```text
Query:

"包含 PoolAcquirePendingLimitException 和 pending 200 的记录"
```

Agent 可以：

```text
search_lexical
```

Agent 也可以在第一次 Retrieval 结果不足时尝试另一种 Search。

---

## 30. Audit Integration

新增搜索沿用现有 AuditJournal，记录 tool、search_id、query、k、namespace、rank、score、score_type、结果 memory refs 和耗时。不得记录完整 Memory 正文或向量。matched_terms / matched_anchors 仅在实现了可选增强时记录。

每次搜索独立生成 ID，但复用现有 se_ 机制；report 无需改变输入结构。

---

## 31. Report / Feedback Integration

现有：

```text
memory_report(search_id, ratings)
```

可以直接用于三个 Search 的 Retrieval Quality Evaluation。

因此：

```text
search_semantic
search_lexical
search_anchor
```

都必须生成独立：

```text
search_id
```

使正向/负向评分能够关联到具体 Retrieval Strategy。

这对后续 Hybrid Search 设计非常重要。

---

## 32. Correctness and Retrieval Evaluation

Correctness guarantees：
- transaction atomicity
- soft-deleted memory invisible
- namespace isolation
- update consistency
- import/recovery consistency
- backward compatibility

上述契约必须通过自动化测试验证，包括故障回滚与启动重建；保证范围是测试覆盖的行为，不是任意环境下的绝对证明。

Retrieval quality：
- Chinese lexical recall
- error-code recall
- identifier recall
- Anchor incremental value
- Agent tool-selection quality

这些效果只能通过真实 query benchmark 评估。Lexical / Anchor designed to improve / intended to cover exact lexical and identity signals，不预先声称提高召回率。

技术验证须确认 QueryBuilder 与 unicode61 切词一致性，并确认真实 query、目标 memory 和数据来源；在同一数据集上先记录现有 Semantic 基线，再比较新能力。

使用真实 query → positive memory 样本，独立评测 Semantic / Lexical / Anchor 和 Agent-selected Retrieval；至少记录 Hit@1、Hit@5、MRR，可附 Recall@10、延迟及无结果率。按标识符、异常码、类/API、技术短语、中文、混合语言和数字/版本分组。

比较候选结果集合的覆盖率以衡量 Anchor 增量价值，不因此引入线上 Hybrid/Fusion。未来融合策略另行评测。Keywords 的长期组织价值不由检索 benchmark 单独决定。

---

## 33. Write Path

服务端校验 namespace 和调用方 metadata.anchors，规范化 summary、keywords/categories，并准备 memory embedding 与 keywords/categories 向量。Anchors 不生成向量，也不调用服务端 LLM。

SQLiteStorage 在一个事务内写入 memory、embedding、通用关系及相关 FTS。任何索引更新失败均回滚整个写入。重复记录沿用现有去重语义。

---

## 34. Update Path

正文未变化时不无故更新正文 FTS 或生成 Anchors。仅更新其他 metadata 且未提交 anchors，保留旧 Anchors。

显式 metadata.anchors 严格校验后替换关系和 Anchor FTS；[] 清空。正文变化更新正文 embedding 和正文 FTS，调用方应同步提供适合新正文的 anchors。正文实质变化且未提交 anchors 时清空旧值；正文未变化或仅空白等价变化时保留。显式提交 anchors 始终优先。实质变化采用现有 summary_policy.normalize_whitespace 比较规则（首尾空白清理、连续空白折叠）。

所有主数据与相关索引变化保持同事务。

---

## 35. Delete / Clear

当前 Memory 使用 Soft Delete：

```text
deleted = 1
```

删除时必须保证该 Memory 不再出现在：

```text
search_semantic
search_lexical
search_anchor
```

因此删除流程同时清理或屏蔽：

```text
vector retrieval
content lexical index
anchor lexical index
memory_index_values
```

---

## 36. NDJSON Replica

NDJSON Replica 当前保存：

```text
id
text
metadata
namespace
created_at
updated_at
summary
```

不保存向量。

由于 Anchor 位于：

```text
metadata.anchors
```

因此 Anchor 会自然进入 NDJSON Replica。

Lexical Index 不进入 NDJSON Replica。

原因：

```text
Lexical Index = Derived Data
```

恢复 SQLite 后应从：

```text
text
metadata.anchors
```

重建：

```text
Content Lexical Index
Anchor Lexical Index
```

这与现有“副本不保存向量”的原则一致。

---

## 37. Import / Recovery

Import / Recovery 保留权威正文和 metadata.anchors，并遵循严格 Anchor 校验规则。旧记录没有 anchors 合法，不隐式调用 LLM 补齐。

恢复 memory-text 和 keywords/categories embedding，重建 metadata 关系、正文 FTS 和 Anchor FTS；不生成 Anchor 向量。Recovery 在重建成功后发布恢复结果。

---

## 38. Index Rebuildability and Startup

FTS 是 derived data，可从 live memories.text 和 metadata.anchors 完整重建。

使用一个 LEXICAL_INDEX_VERSION，覆盖两个 FTS 表的 schema、tokenizer 和索引表示；在 storage_state 中只保存 lexical_index_version，不另设 Anchor 版本或 ready/stale/rebuilding/error 状态机。

确定性规则：
1. 所需 FTS 表缺失、版本记录缺失或版本不匹配 → 完整重建。
2. FTS 重建与版本标记在同一事务中提交。
3. 重建及其余启动检查成功 → Runtime 进入 running。
4. 重建失败 → 回滚并 startup fail，整个服务不进入 running。

不按库大小或耗时改变启动规则，不依赖 row count 或复杂局部健康检查证明索引正确。正常运行通过事务原子性保持一致，schema/version/recovery 场景通过重建处理。版本匹配不代表已证明任意物理损坏不存在。

---

## 39. Embedding Profile Independence

Lexical Index 与 Anchor Lexical Index 不属于：

```text
EmbeddingProfile
```

因此 embedding model migration：

```text
bge-m3
   ->
another model
```

不应要求重建 lexical index，除非 Memory Source Data 同时变化。

同样：

```text
BM25 index rebuild
```

不应改变 active embedding profile。

两套 index lifecycle 应保持独立。

---

## 40. Future Hybrid Search

未来可以增加：

```text
search
```

结构：

```text
                     Query
                       |
          +------------+------------+
          |            |            |
          v            v            v
      Semantic      Lexical       Anchor
          |            |            |
        Top N         Top N        Top N
          \            |            /
           \           |           /
            +----------+----------+
                       |
                       v
                     Fusion
                       |
                       v
                    Reranking
                       |
                       v
                     Top K
```

第一候选 Fusion Algorithm：

```text
Reciprocal Rank Fusion (RRF)
```

原因：

- Semantic cosine score 与 BM25 score 不同尺度；
- Anchor score 也可能采用不同尺度；
- RRF 可以直接融合排名；
- 初期不需要训练权重。

但是：

**本阶段不实现或固定 RRF。**

是否采用 RRF 应由 Benchmark 决定。

---

## 41. Future Work — Memory Relationships

Memory Relationship 属于独立于本 Retrieval Extension 的未来设计方向。

候选输入信号可能包括：

```text
keywords
semantic similarity
shared anchors
categories
namespace
temporal proximity
explicit user references
co-occurrence
```

其中 Keywords 具有特殊价值，因为它表达的是：

```text
explicit extracted concepts
```

而不是单纯：

```text
vector similarity
```

未来可以研究：

```text
Memory A
    |
    +-- keyword --> Redis
    |
    +-- keyword --> Redisson


Memory B
    |
    +-- keyword --> Redis
    |
    +-- keyword --> Sentinel


              Redis
             /     \
            /       \
       Memory A   Memory B
```

这可以进一步演化为：

```text
Memory
  ↕
Concept
  ↕
Memory
```

甚至：

```text
Memory / Concept Graph
```

但上述方向仅作为未来扩展点记录，不属于当前 Lexical / Anchor Retrieval 的实施范围。

---

## 42. Target Architecture

```text
MCP / Admin / Agent
        |
   MemoryService
        |
 MemoryStorage protocol
        |
 SQLiteStorage (DualStorage delegates to primary)
        +-- memory_embeddings          Semantic
        +-- memory_content_fts         Lexical
        +-- memory_anchor_fts          Anchor
        +-- index_values / memory_index_values
        |      keywords / categories / anchors
        +-- index_value_embeddings
        |      keywords / categories only
        +-- memories (authoritative)
                    |
                dual outbox
                    |
               NDJSONReplica
```
当前写路径由调用方提供 Anchors；无服务端生成器。

---

## 43. Key Design Decisions

1. 保留 Semantic Search，新增独立 Lexical / Anchor primitive，当前不做 Hybrid Search 或固定 fusion。
2. 正文和 Anchors 分别使用独立 FTS5 表、BM25 统计及排名，共享最小查询构造器。
3. 直接沿用 MemoryService / Storage / SQLiteStorage，不增加独立 Repository 层。
4. 对外 score=-raw_bm25，沿用现有 search_id；输入仅 query/k/namespace。
5. Anchors 保存于 metadata，复用通用关系表，完全隔离 embedding 生命周期及 semantic resolution。
6. 调用方 Anchor 输入严格校验；服务端不自动生成。可选服务端生成必须经 Phase 2 评测后另行决定。
7. FTS 与主记录原子提交，使用单一版本及完整重建；重建失败阻止启动。
8. NDJSON 保存 Anchor 业务数据，不保存 FTS；恢复重建索引。
9. Keywords 保留 Observational 定位及既有能力；不重命名为 Anchors，不预先淘汰。
10. 正确性通过自动化测试验证；检索效果由真实 query benchmark 评估，不承诺必然提高召回。
11. 配置保持最少，不提前暴露 BM25 参数或添加生成器配置。
12. 本轮 coding plan 覆盖 Phase 1、2，分阶段交付验收；提供两个独立 Admin 搜索 API，不改 UI 搜索模式。正文更新和查询输入遵循已确定规则。

---

## 44. Recommended Implementation Phases

本轮 coding plan 覆盖 Phase 1、2，分别验收；Phase 3 不纳入。Manual Anchors 包括调用方 LLM 提取并提交，不要求人工填写。

### Phase 1 — Lexical Search
实现正文 FTS、最小 QueryBuilder、SQLiteStorage/service/MCP 搜索、审计与既有 search_id/report、确定性迁移重建和事务一致性。交付包含对应 import/recovery 及自动化测试。先评测 FTS5 / unicode61 / BM25 的实际表现。

### Phase 2 — Manual Anchors + Anchor Search
支持人工或调用方 LLM 提供 metadata.anchors，严格校验、复用关系表并隔离 embedding；实现 Anchor FTS、搜索、审计及导入恢复。评测 Anchor 相对 Semantic + Lexical 的增量价值及 Agent 工具选择质量。

### Phase 3 — Optional Automatic Anchor Generation
仅在 Phase 2 评测支持时，另行设计服务端自动生成、failure state 和 backfill。不是当前承诺的实现范围；价值不足可暂缓，不提前引入生成 provider、延迟和调用成本。

Keywords 在各阶段持续保留和观察。Hybrid Search、融合和 Memory Relationships 作为后续独立工作，不属于上述当前阶段。

---
