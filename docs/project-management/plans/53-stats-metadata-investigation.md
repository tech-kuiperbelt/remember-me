# Stats 页面与统计 API 调查（复核版）

调查／复核日期：2026-10-10。范围：当前工作区代码阅读与分析；仅更新本文档，未实施或重构 Stats，未运行测试、查询生产数据库或执行查询计划。性能判断来自代码和 schema，不是实测结果。

## 1. 复核结论与上一版更正

实现完整目标所需的权威数据和关系索引已经存在，无需创建新的 Anchor 存储或生成 Anchor embedding。主要缺口是 Category／Keyword Coverage、三维度统一的 Namespace 范围聚合接口，以及 Stats 页展示。

上一版有以下遗漏或不够准确的表述，本版修正：

1. **Keyword Top N 已有公开 API**：`POST /api/keywords/query` 与 MCP `list_keywords` 的 browse 模式已支持 Namespace、按记忆数排名、limit 5／30／100，并返回范围内 Keyword 去重总数。
2. **Dual 的 Admin API 没有丢掉所有计数**：Service 只删除顶层 `keyword_counts`；`primary.keyword_counts` 仍保留。Anchor 的计数和 Coverage 数量也在 `primary` 内可取，只是没有统一顶层字段。
3. **Admin、MCP tool、MCP resource 的输出不同**：MCP `stats` 会再裁剪一次，完全不返回 Anchor 统计；`memories://stats` 返回完整 Service 结果。
4. **Anchor 的单条记忆内去重与跨记忆统计不同**：单条内 ASCII 不区分大小写去重，跨记忆关系索引按保留大小写的值区分；例如不同记忆分别使用 `MemoryService`、`memoryservice`，当前统计为两个不同值。
5. **Category 总数不能直接取 `category_counts` 的键数**：其中包含未使用的种子。当前 API 也没有显式 `category_total`。

## 2. 前后端代码路径

以下路径与行号对应本次复核时的代码；符号名称可用于后续定位。

| 位置 | 作用 |
|---|---|
| [web/index.html](../../../web/index.html)，约 136 行 | Stats 页面容器 |
| [web/static/js/stats.js](../../../web/static/js/stats.js)，`loadStats()`／`renderNamespaceCounts()` | GET Stats，展示总数、Namespace 徽标及 Keyword 总数；发布共享 Namespace、Category 和默认 Namespace 状态 |
| [web/static/js/main.js](../../../web/static/js/main.js) | 初始化、进入 Stats 和全局刷新时加载 Stats；其他页面也依赖这次数据加载 |
| [web/static/js/keywords.js](../../../web/static/js/keywords.js)，`loadKeywords()` | 独立 Keywords 页面调用 browse／literal／semantic 查询，支持 Namespace、Category 和分页 |
| [app/transport/admin.py](../../../app/transport/admin.py)，约 818／848 行 | `handle_keywords()` 与 `handle_stats()`；路由分别是 POST `/api/keywords/query`、GET `/api/stats` |
| [app/service.py](../../../app/service.py)，约 571／635 行 | `memory_list_keywords()` 参数校验与排名；`memory_stats()` 包装 Storage 结果 |
| [app/transport/tools.py](../../../app/transport/tools.py)，约 138／500 行 | MCP `list_keywords`、`stats` 适配，以及 `_wrap_stats()` 的字段裁剪 |
| [app/server.py](../../../app/server.py)，约 130 行 | `memories://stats` resource 直接返回 Service Stats |
| [app/storage/sqlite.py](../../../app/storage/sqlite.py)，约 1099／1215／1903 行 | `keyword_counts()`、`_metadata_index_counts()`、`get_stats()` 的 SQL 聚合 |
| [app/storage/dual.py](../../../app/storage/dual.py)，约 405／554 行 | Keyword 查询委托主库；Stats 合并主库统计和实时副本健康检查 |
| [app/storage/base.py](../../../app/storage/base.py)，约 185 行 | `get_stats()` 和 `keyword_counts()` 等 Storage 契约 |
| [app/domain/anchors.py](../../../app/domain/anchors.py)／[app/domain/categories.py](../../../app/domain/categories.py) | Anchor 规范化、去重、上限；Category 规范化及零计数种子合并 |

当前 Stats 页面只有总数、Namespace 名称及其记忆数、Keyword 去重总数属于本次目标。Category、Anchor 数据没有展示；不存在 Coverage、Top N 或 Namespace 统计筛选控件。Namespace 徽标没有比例和排名交互。

## 3. 六项目标的现有支持

| 目标 | 已有数据／接口 | 缺少的能力 |
|---|---|---|
| Total Memories | `total` = `COUNT(*) FROM memories WHERE deleted=0`；Dual 使用 primary 的 total | Stats 页按 Namespace 切换；无需新增数据存储 |
| Namespace 分布和数量 | `namespace_counts` = 按 namespace 分组的存活记忆数；比例可由各 count / total 得到 | 页面比例展示（如需要）；新范围接口的分布口径 |
| Category 去重数量 | `category_counts` 的正计数键数量可导出实际已用分类数 | 无显式 total，无 Namespace 范围聚合 API；必须排除零计数种子 |
| Keyword 去重数量 | 顶层 `keyword_total` 为全局已用词数；Keyword browse 响应 `results[0].total` 为范围内去重数 | Stats 页没有使用范围查询 |
| Anchor 去重数量 | SQLite `anchor_total`；Dual `primary.anchor_total` | 顶层字段位置不统一；无 Namespace 范围 API |
| Category／Keyword Coverage | 关系索引已有全部关联 | 缺少“至少包含一项的不同记忆数”聚合及比例 |
| Anchor Coverage | SQLite `memories_with_anchors`；Dual `primary.memories_with_anchors`；比例可除以 total | 未显示；没有比例字段和 Namespace 范围查询 |
| Category Top 5／30／100 | 全局 `category_counts` 足以在客户端过滤 count>0 后排序截取 | 无范围 API；注意种子合并后的对象顺序不是总计数顺序 |
| Keyword Top 5／30／100 | POST `/api/keywords/query`／MCP `list_keywords` 已支持，包含 Namespace | Stats 页尚未接入；不能据此得到 Coverage |
| Anchor Top 5／30／100 | 全局 `anchor_counts` 或 Dual `primary.anchor_counts` 足以排序截取 | 无范围 API或专门 Anchor 词表查询；Stats 页未展示 |
| Namespace 筛选全部指标 | 总数可从全局 `namespace_counts[ns]` 取；Keyword 词表及排名已有范围查询 | Category／Anchor distinct、Top 和三维度 Coverage 缺少统一范围接口 |

Namespace 列表来自存活记忆：空 Namespace 不出现在 `namespace_counts` 中。配置的默认 Namespace 即使没有记忆，也被 Service 视为合法；Admin 通过独立的 `default_namespace` 字段暴露它。这与 Namespace 分布的键集合不同。

## 4. 已有 API 和可复用数据

### 4.1 GET `/api/stats`

`handle_stats()` 不读取 query 参数，因此传 `?namespace=...` 也不会过滤，仍返回全局统计。Service 包装后，Admin 额外加入 `default_namespace`。

| 字段 | SQLite 模式 | Dual 模式 |
|---|---|---|
| total／namespace_counts | 顶层 | 顶层（来自 primary） |
| keyword_total | 顶层 | 顶层 |
| keyword_counts | 顶层已被 Service 移除 | 顶层已移除，但 `primary.keyword_counts` 仍存在 |
| category_counts | 顶层，含 20 个种子及实际自定义分类 | 顶层同左；`primary.category_counts` 为未补种子的实际计数 |
| anchor_counts／anchor_total／memories_with_anchors | 顶层保留 | 在 `primary` 内保留，顶层没有 |

所以“API 没有返回逐值 Keyword 计数”只适用于顶层；不能据此断言 Dual 的完整 HTTP 响应不存在这份数据。嵌套 primary 是现有可读数据，但不适合作为新增统一统计接口的长期前端依赖。

### 4.2 POST `/api/keywords/query` 与 MCP `list_keywords`

现有接口已经可以查询 Keyword Top N，例如以下请求体（示例，不在本次执行）：

```json
{
  "mode": "browse",
  "namespace": "remember-me",
  "sort_by": "count",
  "limit": 30,
  "offset": 0
}
```

- `limit` 支持 1–100，所以 5／30／100 均可；browse 默认 50。
- `results[0].items` 为 `{keyword, count}` 列表，按 count 降序、keyword 升序排序。
- `results[0].total` 为范围内所有已使用 Keyword 的去重数量，不是记忆数，也不受本页 limit 限制。
- 全局查询应省略 Namespace；显式空白 Namespace 经此 Service 路径会解析为配置默认值，不能用它代表 All。
- 指定未知 Namespace 会返回 namespace 错误；配置默认 Namespace 即使为空仍合法。
- 可加精确 Category 筛选，但单纯查询 Namespace 统计应省略该字段。browse 不调用 embedding。
- 当前实现先 SQL 取出范围内全部 Keyword 计数，再在 Python 排序、切片；`limit=5` 并不意味着数据库只聚合 5 个词。

### 4.3 MCP `stats` 与 `memories://stats`

MCP `stats` 不定义筛选参数，经过 `_wrap_stats()` 白名单，仅保留 total、namespace_counts、keyword_total、category_counts、server_time 及部分 embedding 描述字段。它不返回 Anchor 统计，也不返回 primary。

`memories://stats` resource 直接返回 `memory_stats()`，保留上表中的 Service 数据布局；它没有 Admin 额外添加的 `default_namespace`。二者不能视为完全相同的 Stats JSON。

### 4.4 其他现有能力

GET `/api/memories/page?namespace=...` 也能返回准确的范围内 `total`，但会同时查询并返回一页记忆，不宜为了统计反复调用。`MemoryService.memory_namespaces()` 会调用完整 `get_stats()`，目前也没有单独对应的只读 Namespace 路由，不能把它视为已有轻量 HTTP 统计接口。

## 5. Anchors 的权威存储、派生索引与去重口径

### 5.1 三层数据

1. 权威值：`memories.metadata` JSON 中的 `anchors` 数组；NDJSON 副本保存同一 metadata。
2. 计数关系：`index_values(dimension='anchors', value)` 和 `memory_index_values(memory_id, index_value_id)`。与 Category／Keyword 共用这两张表。
3. 搜索索引：`memory_anchor_fts` 保存每条记忆的 Anchor 文本，服务 FTS5／BM25 搜索；Stats 不从 FTS 分词结果计算词表或覆盖率。

Anchors 注册为 `requires_embedding=False`，不参与向量生成、完整性检查和 reindex。普通写入、metadata 更新、批量 Anchor 更新、删除和导入维护关系索引；Anchor 批量更新在同一事务里同步 metadata、关系和 FTS。FTS 重建还会重建 Anchor 关系维度，以覆盖升级前已存在的 metadata。

### 5.2 单条记忆与跨记忆的差别

`normalize_anchors()` 严格校验调用者输入：原始数组最多 5 项，NFKC、首尾去空白、内部连续空白合并；规范化后每项非空且最多 128 字符。保留大小写、顺序及首次出现的写法；ASCII 值在单条数组内按小写键去重，非 ASCII 按完整规范化字符串去重。`clean_anchors()` 用于索引／导入／历史重建，宽容丢弃非法值并截到最多 5 项。

但是 `index_values.value` 没有 NOCASE collation 或统一小写字段，唯一约束和 GROUP BY 使用保存的值。因此：

- 同一记忆的 `["MemoryService", "memoryservice"]` 规范化为一项。
- 两条记忆分别只写 `MemoryService` 和 `memoryservice` 时，形成两个值，当前 `anchor_total=2`，各 count=1。

这不是“跨库 ASCII 不区分大小写去重”。新增统计若沿用现有口径，应保留这一行为；若希望合并这些值，需要另行确认产品语义，不能作为 UI 统计顺手改动。

Category／Keyword 的派生值使用 trim + lowercase；Category 最多索引 3 项，Keyword 没有对应数量上限。三类统计都应基于有效派生关系，不能仅测试原始 JSON 数组长度。

## 6. 统计口径与缺少的聚合

所有统计应限定 `m.deleted=0`，并在选择 Namespace 时使用同一个 `m.namespace` 条件：

- distinct：至少关联一条范围内存活记忆的不同索引值数量。
- 每个值的 count：包含该值的不同 Memory ID 数量。
- Coverage 数量：该维度有至少一个有效关联的不同 Memory ID 数量。
- Coverage 比例：Coverage 数量 / 当前范围 total；建议 total=0 时显示 0%。

不能把各值的 count 相加当作 Coverage。例如一条记忆含 3 个 Keyword，各词 count 合计为 3，但覆盖记忆数是 1；Top 100 也不能代表完整词表或覆盖率。

`index_values` 会保留没有活跃关联的历史值，以保持稳定 ID／向量；直接 `COUNT(*) FROM index_values WHERE dimension=...` 会高估实际使用数量。必须连接范围内存活记忆。Category 的 20 个种子是 Service 后处理加入的候选，实际 distinct 应只数 count>0 的值，不能直接数 `category_counts` 的键。

## 7. 查询成本与索引影响

现有索引：

| 索引／约束 | 可复用能力 |
|---|---|
| `memories` 的 id 主键 | 由关联记录查记忆 |
| `idx_memories_namespace(namespace)` | 缩小 Namespace 内的记忆候选 |
| `index_values UNIQUE(dimension, value)` | 维度筛选、值唯一性和查找 |
| `memory_index_values PRIMARY KEY(memory_id, index_value_id)` | 从记忆查关联；同一记忆与同一值不会重复关联 |
| `idx_memory_index_values_value(index_value_id)` | 从索引值查关联记忆 |

现有 schema 没有专用于 `deleted=0` 的活跃 Namespace 覆盖索引，也没有直接按“记忆频率”维护的排名索引。关系表已支持两个连接方向，新增统计首先应复用它们。GROUP BY、COUNT(DISTINCT) 和聚合后的排序可能使用临时结构；加 LIMIT 100 会限制结果传输，但不会消除精确统计所需的关联扫描。

性能风险还包括：

- SQLite `get_stats()` 在存储 RLock 内读取全部活跃 memory embedding BLOB，检查格式、字节数并计算 profile 统计，不能当作纯计数查询。
- Dual `get_stats()` 同时持有存储锁和同步互斥锁，读取 primary 统计、全部活跃 ID，并通过 `NDJSONReplica.get_stats()`／`inspect_jsonl()` 重新检查磁盘副本；频繁刷新会与写入或同步竞争锁。
- Admin 将工作交给后台调用池，能避免阻塞事件循环，但不会消除数据库／文件扫描或存储锁等待。
- 返回完整 Keyword／Anchor 映射可能随词表增长扩大 payload；新增排名接口宜限制返回条目，但 distinct 和 Coverage 必须仍针对全部范围计算。

本次没有执行 EXPLAIN 或性能测试，不能据此声称现有索引已足够或必须新增某个索引。实现前可在代表性规模上检查全局／小 Namespace／大 Namespace 查询计划；仅当证据显示必要时，再评估活跃 Namespace 的部分覆盖索引等候选，同时考虑维护和写入成本。无需为本目标新增 FTS 或 embedding 索引。

## 8. 推荐的最小改动方向（尚未实施）

建议新增一个轻量 Admin 聚合接口，例如 GET `/api/stats/metadata?namespace=...`，将三维度统一范围统计补齐。保留现有 `/api/stats` 的全局健康核验和候选注册表用途；新接口不是现有能力，具体名称仍是建议。

1. 增加一个 Storage 公共聚合方法及 Service 入口。SQLite 从 `memories` 和现有关系表读取；Dual 直接委托 primary。单次调用中的 total、distinct、Coverage 和排名应在同一锁／读快照下生成，避免并发写入导致分母和分子来自不同状态。
2. 统一返回当前范围 total、Namespace 分布，以及三维度各自的 distinct、covered memories、coverage ratio 和 Top 100 `{value, count}`。前端切换 Top 5／30／100 只需截取，不必重复查询；实际条目不足时显示全部，Category 不补零计数种子充榜。
3. Top 按 count 降序、value 升序；遵守当前 Anchor 保留大小写的跨记忆统计口径。Category／Keyword Coverage 增加 COUNT(DISTINCT memory_id) 或等效 EXISTS 聚合，复用已有 Anchor 逻辑。
4. All 请求省略 Namespace；建议具体 Namespace 沿用既有校验与未知 Namespace 错误，允许合法但无记忆的默认 Namespace 返回零。不要把未知名称和真正的空范围静默混淆。
5. 明确 Namespace 分布也受范围限制：All 返回完整分布，选定某 Namespace 后只展示该范围及数量。选择器的完整候选继续来自全局注册表；若要保留全局分布作为对照，应明确标注它的全局范围。
6. Stats 的 scoped 结果只用于当前统计展示，不调用全局 `setNamespaces()`／`setCategories()` 覆盖注册表，避免影响 Memories、Create／Edit 和 Keywords 页的候选。沿用现有请求版本控制，防止快速切换后旧响应覆盖新范围。

现有 Keyword browse API 可立即复用来单独展示 Keyword 排名；但完整目标仍需新增 Category／Anchor 范围统计和 Coverage。统一轻量接口可避免一次刷新混用不同时间的分母、逐词计数和三个独立响应，也无需修改 MCP `stats`／`list_keywords` 的既有契约。

后续实施时建议验收：SQLite／Dual 返回一致；全局／单 Namespace／空默认 Namespace；删除与字段清空；未关联的历史值；零计数种子；一条记忆多值不重复计 Coverage；Anchor 大小写示例；Top 少于／超过 100、并列稳定顺序；范围切换不污染全局候选。性能验证与索引决定应单独记录实测证据。

本次仅更新调查文档，以上 API、查询和 UI 均未开始实施。
