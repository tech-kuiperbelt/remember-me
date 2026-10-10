# Remember Me MCP — Stats Metadata API Design

**Status:** Proposed / 待评审（不授权编码）  
**Date:** 2026-10-10  
**Scope:** Web Stats 的后端只读统计支持；不包含前端视觉设计。

## 1. 背景与目标

现有 `GET /api/stats` 混合了业务统计、embedding 信息、SQLite/Dual 运维检查；在 Dual 模式中还涉及 NDJSON 副本完整性检查，不适合作为 Namespace 下拉切换时高频调用的统计接口。Stats UI 将定位为**记忆数据统计**，不展示运维健康信息。本设计基于 2026-10-10 Astra 代码复核；该复核未执行测试、真实查询计划或数据库压测。

目标：提供全局或指定 Namespace 的准确记忆总量、Namespace 分布，以及 Categories / Keywords / Anchors 的实际已用去重数、覆盖记忆数、覆盖率和 Top 100。无需修改 metadata schema、Anchor embedding 或搜索语义。

## 2. 非目标与兼容约束

- 不修改现有 `GET /api/stats`、`POST /api/keywords/query`、MCP `stats`、`list_keywords`、`memories://stats` 的既有契约。
- 不在本次设计中增加召回质量、历史趋势、时间序列或运维诊断统计。
- 不更改 Anchor 规范化、FTS、向量索引或存储写入流程。
- 不把筛选后的 Stats 数据写入目前由 `stats.js` 维护的全局 Namespace / Category 注册表；现有页面依赖要在前端实施阶段单独检查。
- 不预先决定新建数据库索引；先验证 SQL 查询计划和代表性规模性能。

## 3. HTTP 契约

### 3.1 Endpoint

`GET /api/stats/metadata`  
`GET /api/stats/metadata?namespace=<URL-encoded-name>`

- **省略** `namespace` 表示 All Namespaces。
- 明确提供 `namespace` 时必须验证为非空有效名称；**空字符串或仅空白不代表 All，也不默认为默认 Namespace**。校验、错误码和错误结构应尽可能沿用 Admin 层现有约定。
- 指定 Namespace 的校验复用服务已有规则：已存在的 Namespace 有效；配置的默认 Namespace 即使当前没有存活记忆也有效并返回空统计；未知 Namespace 返回已有约定的可识别参数错误，而非静默零结果。
- 不支持 Category 筛选、分页和用户指定任意 `limit`；每个维度至多返回 100 项，由前端执行 Top 5 / 30 / 100 切片。
- 采用现有 Admin API 的鉴权、安全与错误响应约定，不另行定义平行授权体系。

### 3.2 建议成功响应（字段名称在评审后冻结）

```json
{
  "scope": { "namespace": null },
  "total_memories": 1240,
  "namespace_options": ["atos-next-master", "remember-me", "待报销"],
  "default_namespace": "remember-me",
  "namespace_distribution": [
    { "name": "atos-next-master", "count": 969 },
    { "name": "remember-me", "count": 269 },
    { "name": "待报销", "count": 2 }
  ],
  "dimensions": {
    "categories": {
      "unique_count": 12,
      "covered_memories": 1100,
      "coverage_ratio": 0.8870967742,
      "top": [{ "value": "development", "count": 650 }]
    },
    "keywords": {
      "unique_count": 2045,
      "covered_memories": 1180,
      "coverage_ratio": 0.9516129032,
      "top": [{ "value": "memory", "count": 320 }]
    },
    "anchors": {
      "unique_count": 850,
      "covered_memories": 900,
      "coverage_ratio": 0.7258064516,
      "top": [{ "value": "MemoryService", "count": 130 }]
    }
  }
}
```

以上为**示例数字**，非真实数据库统计。`coverage_ratio` 是 [0, 1] 数值，UI 决定百分比格式和舍入；Top 每项百分比由 `count / total_memories` 计算（分母为零时为 0）。避免同时返回两个可能因舍入而不一致的比例字段。

### 3.3 响应语义

- `scope.namespace`：规范化后的当前范围；`null` 仅表示 All。
- `total_memories`：当前范围内 `deleted=0` 的 Memory 数。
- `namespace_options`：**全局**候选名称（已使用的 Namespace，另包含无存活记忆但合法的配置默认 Namespace），稳定排序；不因本次筛选而收缩。若实现中已有更权威的 Namespace 注册表，应以现有业务规则为准并写入测试。
- `default_namespace`：当前配置默认 Namespace，便于 UI 正确标识；并不意味着 All。
- `namespace_distribution`：**当前范围**的分布。All 返回全部有存活记忆的 Namespace；指定范围只返回该 Namespace 的正计数项；指定合法但空的默认 Namespace 返回 `[]`。UI 在单 Namespace 时可隐藏无意义的 100% 图。
- `dimensions.{categories,keywords,anchors}.unique_count`：当前范围内至少关联一条存活 Memory 的**不同已使用索引值**数量，不是词表历史记录数，不计未使用 Category 种子。
- `covered_memories`：当前范围内至少有一个**有效索引关联**的不同 Memory 数。
- `coverage_ratio = covered_memories / total_memories`；空范围为 `0`。
- `top`：每维度最多 100 个 `{value,count}`；`count` 是包含该值的不同 Memory 数，按 `count DESC, value ASC` 稳定排序；不足 100 项则全返回。Category 零计数种子不充榜。
- 每个维度单独的 Coverage 是记忆覆盖率，**不能**通过各值 `count` 求和，也**不能**仅使用 Top 100 推断。
- Category / Keyword 继续遵循现有派生索引的 trim + lowercase 语义。Anchor 继续沿用当前保留大小写的关系索引值：不同记忆分别使用 `MemoryService` 与 `memoryservice` 时统计为不同值；不在本项目中合并。
- Namespace 与 metadata 值的显示应保留用户内容，不引入额外大小写折叠。

## 4. 分层实现设计

### Admin Transport

注册新的只读 GET 路由，解析可选 `namespace`，使用现有 Admin 参数验证、授权及异常映射；不修改旧 Stats 路由。避免从客户端传入任意 SQL 片段或 dimension 标识。

### Service

新增例如 `memory_metadata_stats(namespace: Optional[str])` 的入口：解析 All / 合法默认 Namespace / 已使用 Namespace，委托 Storage 聚合并构造统一响应。全局 Namespace 候选包括合法但当前为零的默认 Namespace。**新 API 不调用 `memory_stats()` 或 `get_stats()`。**

### Storage 契约

在 `app/storage/base.py` 增加单一范围聚合接口，例如 `get_metadata_stats(namespace: Optional[str])`；返回约定一致的领域统计，避免把 SQLite / Dual 内部布局 (`primary`) 泄漏给前端。

- **SQLite:** 利用 `memories`、`index_values`、`memory_index_values` 查询，关联时约束 `m.deleted=0` 和可选 `m.namespace=?`。dimension 为服务端固定枚举（categories / keywords / anchors）。
- **Dual:** 直接委托 SQLite primary 的新方法，不调用 Dual `get_stats()`，不执行 NDJSON 完整性扫描，也不读取 embedding BLOB。
- **一致性:** `total_memories`、三维度 distinct / covered / top 和范围分布应来自单个一致的数据库读取快照；同时需要评估全局 namespace_options 与默认值的并发语义。不可仅凭 Python 锁声称已有 DB snapshot 保证，应验证 sqlite3 连接的事务 / snapshot 行为。避免长时间持有会阻塞写入的锁。

### SQL 聚合逻辑（概念性，不限定最终 SQL）

按范围定义活跃 Memory 集合 `M = {m | m.deleted = 0 AND (namespace 未指定 OR m.namespace = 指定值)}`。

对每个 dimension `d`：

1. 将 `M` 与 `memory_index_values`、`index_values` 连接，过滤 `iv.dimension=d`，形成关联 `(memory_id, value)`。
2. `GROUP BY value`，每组 `COUNT(DISTINCT memory_id)` 得到 Top 候选计数；`ORDER BY count DESC, value ASC LIMIT 100`。
3. `unique_count` 为**所有有正计数的 value** 数量，与 LIMIT 无关。
4. `covered_memories` 为整个维度关联的 `COUNT(DISTINCT memory_id)`，与 Top 截断无关。
5. 同一个请求内获取全部活跃记忆 `total_memories`，计算覆盖比例。

主键 `(memory_id,index_value_id)` 可避免同一记忆同一索引值重复关联，但 SQL 与测试需确保去重语义。`index_values` 中可能存在已无活跃关联的历史值，不能只对词表 `COUNT(*)`。不扫描 NDJSON，不依赖 FTS 分词，不生成 embedding。

## 5. 性能与资源约束

- Top 100 是响应数量上限，**不是**最多扫描 100 项；精确 distinct、coverage、排名仍需完整聚合当前范围。
- 复用已有 `(dimension,value)` 唯一约束、`memory_index_values(memory_id,index_value_id)` 主键、`index_value_id` 索引及 `memories.namespace` 索引。
- 开始实施前或同一实施任务中，使用代表性数据对全局、小 Namespace、大 Namespace 执行 `EXPLAIN QUERY PLAN` 和基准计时，记录结果。
- 记录 SQL 耗时、接口整体耗时、数据规模、索引命中与临时排序情况；关注锁竞争以及数据库写入期间的延迟。
- 仅在实测与查询计划显示必要时提出额外索引（例如与活跃记录范围有关的候选索引），并评估写入 / 导入维护成本；不要先行添加索引。
- 前端快速切换 Namespace 时须使用请求代际标识或取消旧请求，防止慢响应覆盖新选择；这是前端实施验收点，不要求服务端保存会话状态。
- 该 API 不宜自动按短周期轮询；用户主动进入 Stats 或更换筛选时请求即可，是否手动刷新由 UI 阶段确定。

## 6. 测试和验收

### 函数 / 存储测试

- 全局 / 指定 Namespace：总数、逐值 count、真实 distinct、coverage、ratio 正确。
- 合法但为空的配置默认 Namespace 返回 total=0、各 unique/covered=0、ratio=0、top=[]、distribution=[]；未知 Namespace 返回规范参数错误。
- 所有值为空的 Memory 不计 Coverage；一条 Memory 有三个 Keyword 时 Coverage 仅计 1。
- `deleted=1` 不计入任何指标；清空 metadata、更新/删除后派生关联准确。
- `index_values` 中保留的无活跃关联历史值不计 distinct / Top。
- 未使用的 Category 种子既不计 distinct，也不进入 Top。
- Anchor 单条规范化后去重；跨记忆大小写差异按当前 index value 精确区分。
- Top 排名按 count 降序、value 升序；数量为 0 / 少于 5 / 5–29 / 30–99 / 超过 100 的行为；Top 100 截断不影响 distinct 和 Coverage。
- 全局 Namespace 候选不会被局部范围污染，始终包含合法的空默认 Namespace。

### 集成与兼容测试

- SQLite / Dual 新 API 的业务响应一致，Dual 不触发 NDJSON 副本核验或 embedding BLOB 全量统计。
- 确认旧 `/api/stats`、Keyword browse、MCP `stats`、`list_keywords`、`memories://stats` 返回契约不变。
- 现有全局注册表初始化及 Memories / Create/Edit / Keywords 现有行为不回归。
- 并发写入下，各统计字段保持同一数据库读快照的内部一致性；具体方案由实施中验证。
- URL encoding、特殊字符、空白 Namespace 参数及错误结构测试。

### 性能验收

本设计不虚构延迟 SLA。代码评审需附查询计划、代表性数据下的实测延迟及锁等待观察，确定能否满足交互体验；如有性能问题，应先改查询策略再评估索引。最终指标需根据数据规模与部署环境确定。

## 7. 推荐实施顺序（仅计划）

1. 先明确现有 Namespace 校验与全局注册表契约，冻结新 API 响应字段。
2. 增加 Storage 范围聚合与 SQLite / Dual 测试，验证一致快照和性能。
3. 增加 Service、Admin GET 路由及 API 集成测试。
4. 单独进入 Stats UI 设计/实施：筛选器、指标、Namespace 分布、三维 Coverage 与各自 Top 5 / 30 / 100；不得改动已验收的 Memory List / Search / Detail 视觉设计。

## 8. 需 Codex/Astra 在编码前评审确认

1. 当前 Admin 对 Namespace 名称的实际校验及错误状态码，空白 query 参数是否能明确与省略区分。
2. 当前全局 Namespace / Category 注册表的调用依赖：新 API 不得覆盖，旧初始化何时触发以及后续是否需要解耦（本期不强制）。
3. SQLite 连接与锁的真实实现：怎样在不扩大写锁竞争的前提下完成同一读快照。
4. Anchor 既有存储/统计区分大小写行为的测试断言是否与现有代码一致。
5. Query plan / 代表性性能结果是否支持无需新增索引。

**Review gate:** 此文档先用于设计评审。评审结论与用户确认之前，不修改生产代码或迁移 schema。
