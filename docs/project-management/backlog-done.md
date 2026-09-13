# Backlog Done

本文件归档已完成的项目待办。详细实现记录保留在 git 提交和项目变更日志中。

- [25] embedding 存储优化与 Python 原生向量化（2026-09-11）：完成 RMEV float16 codec、旧 pickle 兼容迁移、provider/profile 抽象、可选 FastEmbed、本地缓存、影子 reindex/回滚、长文本保护和管理统计。生产副本向量存储减少约 77.7%，完整测试 223 项通过。Jina v2 zh 用 155 条 audit 实际标注查询 A/B 后 Recall@10 比 bge-m3 低 18.0pp，未切换生产默认。详见 [`plans/25-embedding-storage-and-provider.md`](plans/25-embedding-storage-and-provider.md)。
- [14] categories 维度：词表、索引、过滤、统计和迁移（commit `71005a0`）。
- [21] NDJSON 低频 compact job（commit `4246e44`）。
- [18] 无 embedding 的 JSONL/NDJSON 灾备副本与离线恢复。
- [20] audit 日志滚动保留（commit `a9ab663`）。
- [12] summary 输入与 worker fallback（commit `c5e521e`）。
- [13] search 结果 feedback 的 report 工具（commit `eae3c78`）。
- [11] MCP 调用 audit 日志（commit `fa02bc5`）。
- [10] 清理未注册的 MCP 分发分支（commit `07cd60b`）。
- [7] keywords、精确搜索和语义搜索工具改名（commit `1baae9e` 等）。
- JSONL 灾备化架构与管理 UI（commit `12904ac`）。
- summary 功能、字段精简、工具精简、UI 运维页和 ID 短格式。
- 重复判定规则（content + namespace，commit `32e4acd`/`de86cdd`）。

## 归档约定

新完成项从 `backlog-todo.md` 移入本文件，并保留日期、commit、测试和生产验证信息。
