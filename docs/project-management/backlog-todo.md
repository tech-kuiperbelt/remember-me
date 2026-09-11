# Backlog TODO

本文件是 remember-me 项目的开放待办清单。完成项移入 `backlog-done.md`。

## 当前待办

### [25] embedding 存储优化与 Python 原生向量化

- 目标：在语义质量基本不下降的前提下减少 embedding 存储空间；评估 float16、int8/标量量化、乘积量化和降维。
- 目标：探索 FastEmbed/本地 ONNX 等 Python 原生 provider，降低对 Ollama HTTP 服务的依赖。
- 原始设计记忆：`53c3797fa42e9cc3`（FastEmbed 本地推理 + reindex）。
- 待验证：压缩格式、反量化、旧向量兼容、模型/维度版本、全量 reindex、质量与性能 A/B 基线。

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
- [17] MCP 调用来源（session/client）标识。
- [15] 通过共享索引/实体建立记忆间关联网络。
- [16] 关键词收敛与低频词表裁剪的目标冲突。
- [9] keywords 浓度/词频排序及自动提取研究（暂不实现）。

## 维护约定

- 以本文件为项目管理 TODO 的权威来源，不把完整 backlog 正文写入记忆系统。
- 完成项移入 `backlog-done.md`，并在提交记录中注明 commit 和验证结果。
- MCP 工具变更必须同步考虑 audit。
