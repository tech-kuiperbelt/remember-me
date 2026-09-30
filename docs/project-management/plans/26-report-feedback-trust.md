# [26] report 反馈可信度与来源标记

## 实现（2026-09-29）

- 四种 MCP 搜索产生非空结果时，服务进程保存 `search_id`、返回的记忆 ID 集合、搜索工具、namespace、请求 ID 与传输。回执最多保留 24 小时和 10,000 次搜索；重启、过期、淘汰后的 ID 不再接受反馈。新搜索 ID 使用 128 位随机值。
- MCP `report` 要求非空评分数组，每个 `m_id` 都属于对应搜索结果且在本次调用中不重复，`rating` 是 -10～10 的整数。整次反馈校验失败时不记录有效评分，返回 `response_code=invalid_report` 与具体原因；无效调用仍写入 audit，状态为 error。底层 `MemoryService.memory_report` 的宽松归一化保留给内部旧调用，MCP 边界执行严格规则。
- 新增可选 `feedback_source=llm|human|test` 和 `feedback_purpose=task|evaluation|test`。有效反馈的 `feedback_context` 记录声明值、`analysis_group`、来源搜索工具/namespace/请求 ID/传输及结果归属校验状态；audit 的独立 `context` 继续记录服务端观察到的调用来源。未声明值归入 `unclassified`，声明为 test 的归入 `declared_test`。所有来源与用途都只是调用方声明，不能由此证明“human”确为人工标注。
- Audit 对 `report` 参数有界裁剪，保留最多 1,000 个评分项，限制 ID 和异常值长度，避免错误输入放大日志。`/read-me` 与工具描述同步了新契约。

## 基线与分析边界

变更前当前 audit 文件中有 349 次 `report`：1 次找不到对应搜索，8 次虽能找到搜索但至少一个评分 ID 不在该次结果中。这些历史事件不追溯改写，也不能凭现有客户端字段区分真实使用和测试。质量分析应只将新版本中 `result_membership=validated` 的反馈作为候选，再按 `analysis_group` 区分声明的测试、LLM 任务反馈与人工评估；`declared_human_evaluation` 仍未经过身份核验。

## 验证与上线

针对性测试覆盖四种搜索的有效反馈、伪造/过期搜索 ID、跨结果 ID、重复与越界评分、空结果、来源分类、audit 有效与错误状态及裁剪；全量 pytest 491 项通过。2026-09-30 已在线验证重启后的服务：`tools/list` 暴露新字段，`search_meta` 返回结果后的跨结果 ID 反馈被拒绝，合法 `test/test` 反馈接受，audit 分别记录 error/ok、`streamable_http` 和 `declared_test`。旧服务进程的搜索 ID 不可在新进程中补报。
