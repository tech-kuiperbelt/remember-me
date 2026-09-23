# [25] Audit 调用关联与运行期错误状态

2026-09-23 实现。运行中的服务由 `Runtime` 持有独立 `AuditJournal`；MCP、admin、导入及 NDJSON 后台任务共用它，离线 embedding CLI 自建 journal。滚动 JSONL、旧字段、裁剪和读取兼容历史记录；旧的模块级接口保留给低层工具和既有测试，服务运行链路不再依赖其进程级状态。

MCP 审计 `context` 记录来源、SDK 请求 ID、服务生成的会话 ID、客户端自报的名称和版本。admin HTTP 请求使用独立请求 ID，后台任务标为 `background`。客户端名称仅用于分析，不能视为认证身份。

`list_keywords` 的 MCP/admin 响应新增 `keyword_query_id`。MCP `add`、`update`、`search_semantic`、`search_meta` 可选传此 ID；`get_details`、`update` 可选传 `source_search_id`。这些字段只进入 audit，不改变写入、过滤、排序或现有 `search_id`/`report` 契约。无 ID 时不猜测关联；传入 ID 是调用方声明，不验证 ID 是否对应有效上游调用，严格反馈校验留给 [26]。详情审计带已找到记忆的 ID/namespace，更新带操作前后 namespace，限制至前 100 项；audit 不复制正文或摘要。

运行中的 embedding 失败返回 `response_code=embedding_unavailable`，超长输入返回 `embedding_input_too_long`，正在关闭返回 `service_closing`；响应提供简短恢复提示，同一代码记入 audit。启动前的 writer lock/profile 故障仍通过终端和运维命令诊断，因为 MCP 尚未运行。

验收覆盖实例隔离、轮转和裁剪、显式找词/搜索/详情/更新/report 链路、admin 关键词查询、并行 SSE 会话 ID 区分、stdio/SSE 实际 MCP 请求以及完整 pytest 回归。部署需重启服务，旧 MCP 会话需重新初始化。
