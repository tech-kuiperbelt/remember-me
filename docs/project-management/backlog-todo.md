# Backlog TODO

本文件是 remember-me 项目的开放待办清单。完成项移入 `backlog-done.md`。

## 当前待办

### [28] namespace 规范化与别名治理

- 定义受控 namespace 标识及显示名，统一 `atos-next-master`、`atos-next`、`ATOS Next` 等历史变体。
- 提供迁移/别名策略，并在 MCP 写入与查询时拒绝或明确提示非规范 namespace，避免静默空检索和数据孤岛。

### [27] LLM 结构化检索与分类写入引导

- 调整 MCP 工具描述与 hint：写入时要求/引导提供 1–3 个 categories，检索时说明 keywords/categories 精确预过滤的适用场景。
- 基于 audit 复核采用率与检索效果，避免仅靠文案而未形成实际行为改变。

### [26] report 反馈可信度与来源标记

- 校验 `search_id` 存在，且每个 `m_id` 属于该次搜索结果；拒绝或明确记录无效反馈。
- 为反馈记录来源/用途（LLM、人工、测试等）和必要的关联上下文，区分可用于质量分析的真实标注与测试数据。

### [25] audit 调用关联与可用性状态

- 在 audit 中记录 caller/client、session/request ID，并让 `get_details`、`update`、`report` 等无 namespace 参数的事件可关联至原始 namespace/搜索链路。
- 对 embedding、迁移、锁库等不可用状态提供可被 LLM 明确识别的状态与恢复提示，避免失败后盲目重试。

### [24] keywords/categories 向量化与语义查询

- 为词表项生成 embedding，支持 keyword/category 语义匹配。
- 明确词表命中反查记忆与正文向量融合方案，以及 OR、阈值、Top-k、namespace 和 blacklist 规则。
- 覆盖同义词、多维组合、空结果和与精确查询的召回质量对比。

### [23] keywords 词表治理与黑名单

- 建立独立的 canonical keyword vocabulary，支持展示、查询、新增、改名/合并、停用/删除。
- 增加 blacklist，明确新记忆、迁移和历史数据命中黑名单时的处理。
- 设计 alias/merge、归一化、管理 API/UI、权限审计及现有关键词迁移策略。

### [22] 收敛 storage 后端支持

- 梳理 `MemoryStorage`、factory、配置、恢复、测试和文档，移除不必要的 JSON 后端支持。
- 保留 SQLite 业务主库和无 embedding 的 NDJSON 灾备/离线恢复链路。
- 完成后验证 CRUD、语义搜索、索引过滤、同步、compact、Full Sync 和恢复流程。

### 其他开放项

- [19] memory priority/置顶字段。
- [15] 通过共享索引/实体建立记忆间关联网络。
- [16] 关键词收敛与低频词表裁剪的目标冲突。
- [9] keywords 浓度/词频排序及自动提取研究（暂不实现）。

## 维护约定

- 以本文件为项目管理 TODO 的权威来源，不把完整 backlog 正文写入记忆系统。
- 完成项移入 `backlog-done.md`，并在提交记录中注明 commit 和验证结果。
- MCP 工具变更必须同步考虑 audit。
