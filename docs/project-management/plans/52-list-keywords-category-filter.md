# [52] list_keywords 按 categories 过滤

2026-10-01 实现并验证（隔离 pytest + 干净 HEAD 浏览器回归 + 重启后实况 smoke）。

## 背景

`list_keywords` / `POST /api/keywords/query` 原先只能按 `namespace` 过滤，无法回答
"某 namespace 下、属于某些 categories 的记忆，用了哪些 keywords"。索引层
（`index_values` / `memory_index_values`）本就支持该聚合，仅缺接口暴露。

设计与实现约束：keywords/categories 存储层无 namespace（`index_values` 唯一键为
`(dimension, value)`），namespace 仅在查询时通过 join `memories.namespace` 生效。

## 接口

`list_keywords` 与 `POST /api/keywords/query` 新增可选参数 `categories`；其余参数不变。

| 参数 | 约定 |
|---|---|
| categories | 可选字符串数组；最多 20 个（去重后计）；精确匹配（trim/lower/去重）；维度内 OR，与 namespace 用 AND；省略或归一化后为空等同不过滤 |

- 三个模式（browse/literal/semantic）均生效。
- 结果 envelope 在既有字段基础上新增 `categories`（回显归一化后的值或 null）。
- 向后兼容：仅新增可选参数与结果键；不传 `categories` 时 SQL 与行为与改动前完全一致。
- 校验：`categories` 非数组 → `ValueError`（HTTP 400）；去重后 > 20 → `ValueError`。

## 实现

- `app/storage/base.py` + `sqlite.py` + `dual.py`：`keyword_counts(namespace=None, categories=None)`。
  categories 非空时在 keywords 聚合上追加
  `AND m.id IN (SELECT l.memory_id FROM memory_index_values l JOIN index_values c ON c.id = l.index_value_id WHERE c.dimension='categories' AND c.value IN (...))`，
  经 `_normalize_index_filter` 归一化；`COUNT(DISTINCT m.id)` 保证一条多分类记忆只计一次。
- `app/service.py`：`memory_list_keywords(..., categories=None)` 归一化/校验后传给 storage，并在结果回显 `categories`。
- `app/transport/tool_definitions.py`：`list_keywords` schema 新增 `categories`（`maxItems=MAX_FILTER_VALUES`）与描述。
- `app/audit.py`：`_params_list_keywords` 白名单加入 `categories` 并截断（≤20 项、每项 ≤200 字符，标记 `categories_truncated`）。
- `app/transport/admin.py`：`handle_keywords` 允许集加入 `categories`。
- 前端：`store.js` 新增 categories 注册表；`stats.js` 用 `category_counts` 填充；`index.html` 新增 `kw-categories` 多选；`keywords.js` 读取选中项并随请求发送。
- 无 DB schema 变更，无数据迁移，无需 reindex。

## 边界

- 仅精确匹配，不做 categories 的语义解析。
- 多分类固定 OR（ANY），暂不提供"全部满足"（ALL）。
- namespace 与 categories 均可省略，四种组合见实现约束。

## 验证结果

- 全量 `python -m pytest -q`：716 passed。
- 浏览器回归：在干净 HEAD 上仅应用本次改动后 `tests/run_admin_browser.py` 全部场景通过（主工作区失败来自既有未提交的前端重构 WIP）。
- 重启后实况 smoke（`http://127.0.0.1:1314`，1155 条记忆）：
  - 无 ns/无 cat=2039；ns only=243；cat only(api)=322；ns+cat=80；多分类 OR(api,design)=152；空数组等同不过滤；`' API '` 正确归一化。
  - 负例：非数组 400、>20 个不同值 400、未知 namespace 400 `namespace_not_found`、未知参数 400。
  - 一致性交叉验证：`ns=remember-me` + `api` + keyword `mcp`，新聚合 count=13，独立 `/api/memories` 路径 =13，MATCH。
  - MCP `/mcp`：`tools/list` 含 `categories` schema，`tools/call` 带 categories 返回正确。
