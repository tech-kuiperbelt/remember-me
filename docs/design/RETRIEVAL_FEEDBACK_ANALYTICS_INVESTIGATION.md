# Remember Me — 检索反馈分析可行性调查报告

日期：2026-10-10
状态：只读调查，未修改应用代码、Schema 或 Audit 文件，未实施任何功能。
范围：MCP Search → Report → Feedback 链路，以及本地 Audit JSONL 的可用性。

调查方法：

- 只读实现：`app/service.py`（`memory_report`、`_normalize_rating`）、
  `app/transport/tools.py`（`execute_tool`、`_handle_report`、`_wrap_search_*`）、
  `app/transport/report_feedback.py`、`app/audit.py`、`app/transport/tool_definitions.py`。
- 只读数据：主数据根 `~/.remember-me/audit.jsonl`（截至 2026-10-10 采集时 3272 事件，
  2026-08-18 → 2026-10-10，无轮转文件，3.0 MB < 10 MB 上限）。历史旧根
  `~/Desktop/data/audit.jsonl`（2026-08 前，未纳入主统计）仅作对照。
- 全程不改 audit；分析脚本在内存中读取。

结论速览：

- 新 Schema（2026-09-23 起）区间内，搜索与反馈的关联完整度是 **100%**（47/47 被报告
  `search_id` 均能匹配到搜索事件），反馈可定位具体 Memory 的比例 **99%**（102/103，
  1 条为已拒绝的非法探针）。
- 但**历史区间完全不可关联**：2026-09-23 之前 405 次 search、321 次 report 一律没有
  `search_id` 字段——这是 Audit Schema 不完整，不是关联失败。
- 从现有数据**可以**算出 Count / Non-empty Rate / Mode Distribution / Feedback Coverage /
  Positive Rate / Average / Distribution / Feedback-based Hit@K；但其中多项存在显著选择性偏差，
  **不能**等同于召回率或满意度。

---

## 1. Search → Report 数据关联模型

### 1.1 Report 的评价对象与评分语义

- **评价对象**：一次具体搜索调用返回结果集合中的**单个 Memory**。`report` 参数
  `search_id` 标识那一次搜索，`ratings=[{m_id, rating}]` 标识被评的 Memory。
  同一条 Memory 出现在多次搜索中时，评分按“搜索 × Memory”分别成立，不是对 Memory 的全局评分。
- **量表**：整数 `-10..10`。
  - 正数 = 有帮助，越大越有用；
  - 负数 = 误导 / 干扰，越负越强；
  - **`0` = 既无帮助也无害（中性）**，不是“未评价 / 缺失”。
  - `memory_report` 的宽松归一化（`_normalize_rating`：四舍五入 + 截断到 `-10..10`）只在
    直接调用 Service 时可达；MCP 边界由 `report_feedback.validate` 先做**严格校验**
    （非整数或越界直接拒绝），因此线上 Audit 中的成功评分恒为合法整数。
- **校验与记录分离**：校验只依赖**进程内搜索回执**（`SearchReceiptRegistry`），与 Audit 无关；
  Audit 只是把参数和结果落盘。回执 TTL 24h、上限 10000、进程重启即清空。

### 1.2 多次 Report 的处理方式

- **不合并、不去重、不覆盖**：同一 `search_id` 可被 `report` 多次，每次独立成为一条 Audit 事件，
  互不影响。`memory_report` 仅返回 `{recorded:true, count:N}`。
- 单次 `report` 内同一 `m_id` 重复 → `duplicate_memory_id` 拒绝；跨 `report` 重复允许。
- 回执只校验 `search_id` 存在、`m_id` 属于该次搜索返回集合、评分严格合法；不校验 `source_search_id`，
  也不与 Audit 交叉验证。

### 1.3 search_id / source_search_id / memory_refs 的关系

| 字段 | 产生方 | 语义 | 是否持久 | 是否校验 |
|---|---|---|---|---|
| `search_id` | `_wrap_search_*` 每次搜索现生成（`se_` + 16 字节 hex） | 关联该次搜索与后续 report/详情 | 仅响应 + Audit，不落库 | report 校验依赖它 |
| `source_search_id` | 调用方在 `get_details` / `update` 传入 | “本次读取/更新源自哪次搜索”的审计关联 | Audit-only | 不校验 |
| `memory_refs` | Tool 边界按事件产生 | 事件影响的 `{id, namespace}` 列表 | Audit-only | 不参与 report 校验 |

- 三者是**平行**的审计关联，不是一条链：`report` 只用 `search_id` 对回执；`source_search_id`
  目前仅 21 条（`get_details` 20、`update` 1）；`search` / `report` 事件的 `memory_refs` 恒为空。
- 因此“搜索 → 详情 → 更新”的归因是可能的但样本极少；“搜索 → 反馈”的归因靠 `search_id` 且只在新区间有效。

### 1.4 Search 结果在 Audit 中保留的内容与顺序

`_result_search` 保留：

```json
{"search_id": "...", "total": N,
 "filter_resolution": {...仅 search_semantic...},
 "items": [{"id": "...", "summary": "...", "score": 0.60}]}
```

- **顺序 = 返回排名（rank）**，按 items 数组顺序原样保留，且不截断（保留原始 k 条）。
- `score` 仅 `search_semantic` / `search_lexical` / `search_anchor` 保留；**`search_meta` 去掉 score**（仍有 rank）。
- `params` 保留 `query` / `k` / `namespace` / 过滤参数（被剥离的只有 `text` / `summary`，`query` 不受影响）。
- 结论：**rank 与 id 永远可还原；绝对分数只有三种模式有**。`lexical`/`anchor` 的 BM25 分与
  `semantic` 余弦分**不可比**，但评分量表是同一套。

---

## 2. 基于真实 Audit 的统计结果

数据：`~/.remember-me/audit.jsonl`。采集时 3272 事件，`corrupt = 0`，范围
`2026-08-18T08:30:25Z → 2026-10-10T07:05:53Z`。

### 2.1 Schema 分界（关键前提）

审计字段 `search_id` / `context` / `source_search_id` / `keyword_query_id` / `memory_refs`
在 **2026-09-23T09:09:54Z** 才随“Audit 调用关联”进入（对应 backlog [25]）。据此分：

| 区间 | 事件数 | search 事件 | report 事件 | 带 `search_id` |
|---|---|---|---|---|
| 旧（< 09-23） | 2079 | 405 | 321 | **0** |
| 新（≥ 09-23） | 1192 | 158 | 48 | search 155 / report 48 |

旧区间的缺失是**历史 Schema 不完整**，不是关联失败：当时 report 调用本身带 `search_id`，
只是 Audit 没记录该字段，事后无法补。

### 2.2 新区间关联完整性

- 搜索事件（有 `search_id`）155 次，其中非空 139 次。
- 搜索模式分布（非空）：`search_semantic` 60、`search_lexical` 44、`search_meta` 25、`search_anchor` 10。
- report 事件 48 条，覆盖 **47 个不同 `search_id`**；**47/47 全部匹配到搜索事件，且都匹配到非空搜索**（100%）。
- 同一次搜索结果内无重复 id。
- 有反馈转换率（新区间）：`47 / 139 ≈ 33.8%`。

### 2.3 反馈可定位性

- 评分条目 103 条：**全部为整数、全部落在 `-10..10`、0 条越界、0 条非整数**。
- 可定位（`m_id` 出现在对应搜索的 `result.items` 中）：**102 / 103 = 99.0%**。
- 剩余 1 条不可定位来自一条**已被拒绝的非法测试探针**
  （`m_id="not-from-this-search"`，`feedback_source=purpose=test`，事件 `status=error`、
  `response_code=invalid_report`、`reason=result_id_mismatch`）。它不计入有效反馈。

### 2.4 排名还原

- 可还原 `(rank, rating)` 对：**102**；其中正分 92。
- 正分样本 Hit@1 = `31/92 = 33.7%`；Hit@5 = `78/92 = 84.8%`。
- 说明：这是**有反馈子集上的排序质量**，**不是全量召回率**；正分本身与 rank 相关，存在循环偏置（见 §3/§5）。

### 2.5 评分分布

- 直方图：`{0:11, 1:3, 2:6, 3:9, 4:6, 5:7, 6:8, 7:6, 8:29, 9:7, 10:11}`。
- 正 92 / 零 11 / **负 0**；均值 5.79，中位数 7。
- 声明分组（`feedback_context.analysis_group`，2026-09-30 才进入 result）：仅 19 条 report 有该字段——
  `declared_llm_task` 11、`declared_test` 7、`unclassified` 1，其余 29 条无该字段。

### 2.6 异常与缺失归类

| 现象 | 数量 | 归类 |
|---|---|---|
| 旧区间 report 无 `search_id` | 321 | 历史 Schema 不完整（非失败） |
| 旧区间 search 无 `search_id` | 405 | 历史 Schema 不完整（非失败） |
| 有效 report 找不到对应 search | 0（新区间） | — |
| 重复 `(search_id, m_id)` | 0 | — |
| 越界 / 非整数评分 | 0 | — |
| 同一 `search_id` 多次 report | 1（测试，1 合法 + 1 拒绝） | 预期行为 |
| report 缺 `feedback_context` | 29（新区间早期） | 子 Schema 晚到 |
| `report.namespace` 恒 null | 全部 369 | 设计：report 参数不含 namespace |
| `report.memory_refs` 恒空 | 全部 | 设计 |

---

## 3. Audit 保留机制对统计的影响

- **容量**：`audit.jsonl` 按 10 MB/文件轮转，最多 10 个文件（含当前），即总量约 100 MB；
  平均 ~945 B/事件 → 约 **11 万事件**后开始淘汰最旧文件。当前 3.0 MB，尚未轮转。
  事件数上限 `MEMORY_AUDIT_MAX_EVENTS=0`（关闭）。
- **查询 API**：`GET /api/audit` 依次读 `[audit.jsonl, .1 … .9]`，**文件内倒序**，凑满 `limit` 即返回
  （默认 500，最大 5000）。含义：
  - 默认只覆盖最近约 500 条事件，**长期趋势会被截断**；
  - 无 `total` / 无分页信封，跨文件不保证严格全局时间序（截断点处）；
  - `tool` / `namespace` 为**精确等值**过滤。
- **namespace 过滤对 report 永久失效**：report 事件 `namespace` 恒为 null，`?namespace=X` 查不到 report。
- **轮转即数据丢失**：超过 10 个文件后最旧历史被删除，且**新旧 Schema 混杂**，跨期聚合必须先按分界切分。
- **source_search_id 链路**：可用于“搜索 → 详情/更新”的归因，但目前仅 21 条样本，统计意义有限。

因此，长期/窗口化统计**不能**依赖默认 `/api/audit` 结果；需全量读取文件，或新增聚合端点。

---

## 4. 指标可行性逐项评估

符号：`S` = search 事件集合，`R` = report 事件集合（仅新 Schema 区间内带 `search_id` 者）。

| 指标 | 计算公式 | 数据来源 | 可准确计算？ | 主要偏差 / 限制 |
|---|---|---|---|---|
| **Search Count** | `count(S)` 或 `count(distinct search_id)` | `tool ∈ search_*` | 是（全量） | 跨 Schema 只有 09-23 后有 id；轮转后截断 |
| **Non-empty Rate** | `|{s: s.result.items 非空}| / |S|` | `result.items` | 是（全量 531/563 ≈ 94.3%） | **≠ 召回率**。语义检索默认无阈值（threshold=0），近乎恒非空，区分度极低；受 namespace/过滤错误影响 |
| **Search Mode Distribution** | 按 `tool` 分组占比 | `tool` | 是（全量） | 反映 LLM 使用习惯，非检索质量；anchor/lexical 极少 |
| **Feedback Coverage** | `|distinct report.search_id| / |distinct 非空 search_id|`（同区间） | `report.search_id` 联合 search | 仅新区间：`47/139 ≈ 33.8%` | 受回执 TTL/重启影响（失效即被拒）；report 非强制；旧区间不可算；**≠ 满意度** |
| **Positive Feedback Rate** | `|rating>0| / |rating|` | `report.params.ratings` | 是：`92/103 ≈ 89.3%` | 自选样本（LLM 只评用到的），**无负样本 → 强正偏**；0 是中性 |
| **Average Feedback Score** | `mean(rating)` | 同上 | 是：5.79（median 7） | 同上正偏；三种模式分不可比却共用同量表；需先剔除声明 test |
| **Feedback Distribution** | 直方图 | 同上 | 是 | 样本小（103）；`feedback_source/purpose` 为**自声明**不可核 |
| **Feedback-based Hit@1 / Hit@5** | 对每个 (search, rated m) 求 `rank = items.index(m_id)`；`Hit@K = |{rank<K 且 rating>0}| / |{可定位且 rating>0}|` | `report.params.ratings` + `search.result.items` 顺序；按 `search_id` 关联 | 是：102 对，正分 `Hit@1 33.7%` / `Hit@5 84.8%` | **仅“有反馈子集”的排序质量，≠ 全量召回率**；正分与 rank 正相关 → 循环偏置会高估；`search_meta` 无 score 但有 rank；需按 `analysis_group` 剔除 test |

补充口径提醒：

- 若一次搜索被多次 report，Hit@K 应按“最后一条有效 report”或合并去重后再算，避免重复计权。
- `analysis_group` 只覆盖部分事件；`declared_test` 样本（7 条）应从面向产品的指标中剔除。

---

## 5. 可直接实现的指标

以下指标可在**纯只读脚本**中，由现 Audit 文件直接算出，无需改动任何服务端代码：

1. **Search Count**（全量 + 按模式）。
2. **Non-empty Rate**（明确标注为“返回非空比例”，不得命名或解读为召回率）。
3. **Search Mode Distribution**。
4. **Feedback Coverage**（限 09-23 后，并在报告中显式声明区间）。
5. **Positive Feedback Rate / Average / Distribution**（先剔除 `analysis_group=declared_test`）。
6. **Feedback-based Hit@K**（明确标注“有反馈子集上的排序质量”）。

**前置要求**（脚本必须处理）：按 Schema 分界切分、跨全部轮转文件全量读取并按 `ts` 排序、
按 `search_id` 关联 search↔report、按 `m_id` 还原 rank、按 `analysis_group` 分组。

---

## 6. 需要补充数据的指标

| 目标指标 | 缺失的数据 | 可行补充方式（供评审） |
|---|---|---|
| 真实 Recall / Precision@K | 每个查询的“正确答案集合”（ground truth） | 离线人工标注（沿用 backlog 记录中 “155 条 audit 查询人工标注做 Recall@10”的既有路径） |
| 隐式相关 / 未评分负例 | 未评分结果的语义标注 | 不可靠：未评分 ≠ 不相关（LLM 可能只评了“用到的”） |
| 按 namespace / 模式细分的反馈质量 | report 无 namespace；需从 search 事件 join | 新区间可 join 取 `search_namespace`；旧区间不可 |
| 端到端任务成功率 | 业务结果信号（后续动作是否解决问题） | `source_search_id` 归因链，但当前仅 21 条，样本不足 |
| 长期 / 时间窗口趋势 | 不受默认 limit、轮转、Schema 混杂影响的完整历史 | 服务端只读聚合端点，或离线全量扫描归档 |

---

## 7. 不建议实现的指标及原因

1. **把 Non-empty Rate 当作真实召回率**：语义检索阈值默认为 0，几乎恒非空，该数值无区分度，
   会把“检索没报错”误读成“检索有效”。
2. **把 Feedback-based Hit@K 当作全量召回率**：只在“有反馈的子集”上成立，且正分本身就偏向靠前结果，
   存在循环偏置，系统性地高估质量。
3. **用反馈自动训练 / 自动调参**：样本量小（103）、强正偏、无负例、来源为调用方自声明、
  回执链路受 TTL/重启断开，不具备训练级数据质量。
4. **仅凭 `report.namespace` 做命名空间维度统计**：该字段恒为 null。
5. **把原始 Audit 当审计级长期账本做统计**：超轮转上限即丢最旧数据，且新旧 Schema 混杂，
   必须切分处理，否则会把“历史无 id”误判为“关联失败率 100%”。

---

## 8. 最小改动建议（仅建议，不实施）

按风险从低到高：

1. **分析侧（零服务端改动，推荐先行）**：新增一个只读离线脚本，遍历 Audit 全部轮转文件，
   按 Schema 分界分流、按 `analysis_group` 分组，输出 §5 的可算指标。不触碰服务、Schema、Audit。
2. **产品化只读端点（需评审）**：若要把指标做成常驻能力，新增 `GET /api/audit/retrieval-stats`
   之类的**只读聚合**端点，在服务端全量扫描 + 聚合，避免客户端拉取全量 JSONL。属功能变更，单独评审。
3. **让 report 可按 namespace 过滤（需评审，行为/Schema 变更）**：report 成功时 `result` 已带
   `search_namespace`，可考虑把搜索回执的 namespace 复制到事件顶层 `namespace`（或独立字段），
   使 `?namespace=` 过滤对 report 生效，并稳定 `feedback_context` 字段。单列评审。
4. **真实召回评估**：继续沿用离线人工标注路径，而不是从 report 推断召回。

以上均**不在本阶段实施**；本阶段不设计 UI，不编写 Coding Plan，不改代码。
