# [36] 关键词查询从 Stats 拆分

2026-09-22 实现；用户重启后已验证真实 MCP，不执行历史词表清理。

## 接口

新增 MCP `list_keywords` 和 HTTP `POST /api/keywords/query`，共用业务层。

| 参数 | 约定 |
|---|---|
| namespace | 可选；省略为全库；只计存活记忆，未知 namespace 返回 namespace_not_found（HTTP 400） |
| mode | browse（默认）、literal、semantic |
| queries | browse 不传；literal 单个非空字符串；semantic 1–20 个概念 |
| limit | browse/literal 默认 50，上限 100；semantic 每组默认 5，上限 20 |
| offset | browse/literal 非负分页偏移；semantic 必须为 0 |
| sort_by | browse/literal 为 count（默认）或 keyword；semantic 仅 score |
| min_score | 仅 semantic，默认 0.65，范围 0–1 |

响应包含 namespace/mode/limit/offset/sort_by/results；semantic 回显 min_score。每组 results 包含 query、total、items，浏览 query=null。每项为 keyword/count，语义另含 score。total 是分页前、语义阈值后的匹配词数；次数是范围内去重后的存活记忆数。相同分数按次数降序、词名升序；相同次数按词名升序。

字面查询 trim/lower 后按包含匹配，%/_ 不作为通配符。语义查询复用 active profile 向量与 query 路径，不沿用记忆搜索的 exact-first/Top-3/max-gap 策略。精确命中仍可列出其他相近词。批量是接口批量，provider 按现有 query 方法执行；同批重复概念复用推理。

```json
{"namespace":"remember-me","mode":"semantic","queries":["关键词治理","向量模型"],"limit":5}
```

## UI、兼容与审计

新增 Keywords 页面，提供 namespace、字面/语义搜索、排序、分页、精确记忆筛选跳转。语义查询手动提交，展示相似度而非同义保证。现有记忆列表关键词选择改为按需加载、搜索、加载更多，保留已选项并忽略过期响应。

所有公开 Stats 删除 keyword_counts，改为 keyword_total；底层内部词频接口保留。删除旧 MCP 低频非中文裁剪逻辑；外部调用方必须改用 list_keywords。无数据迁移，无需重建向量。重启服务/重新连接 MCP 后获取新工具列表。

工具描述与 hint 引导设置 keywords 前在目标 namespace 批量语义找词；仅复用含义适合的候选，允许新词，不强制替换。审计记录查询与有界候选（最多 20 组，每组 5 项；查询文本最多 1000 字符），标记截断。不产生 search_id，不调用 report。

## 边界

本次不做合并、黑名单、自动替换和历史清理。[23] 人工合并另行讨论。近义词新增量是否下降需后续真实使用数据验证。

## 验证结果

- `.venv/bin/python -m pytest -q`：319 passed，1 条 Starlette TestClient/httpx 弃用提示。新增 36 项测试覆盖 SQLite/dual、查询校验、语义范围、MCP/HTTP/resource 和审计；移除旧裁剪逻辑的 3 项测试。HTTP 测试需可正常使用线程事件循环的环境，本次在沙箱外验证。
- `node --check`：页面 JavaScript 语法通过。
- 临时内存数据库 + 无头 Chrome：浏览、翻页、字面与语义搜索、精确记忆跳转、关键词加载更多/选择保留、低频词和过期响应处理通过；使用确定性假向量，不连接生产 embedding 服务。
- `git diff --check` 通过。
- 用户重启后真实 MCP 验证通过：description/hint 已更新；Stats、分页、字面、批量语义、namespace 隔离、错误响应和 audit 正常。真实 bge-m3 三概念查询服务端耗时约 627ms；相似候选仍由 LLM 判断，不自动合并。
