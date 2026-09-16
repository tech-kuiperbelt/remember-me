# Backlog Done

本文件归档已完成的项目待办。详细实现记录保留在 git 提交和项目变更日志中。

- [28] namespace 规范化与未知 namespace 治理（2026-09-16 实现，commit `9543acb`）：已知 namespace 集合由 live 记忆动态派生（不新增 namespace 表），并始终纳入配置的 `MEMORY_DEFAULT_NAMESPACE` 以支持空库 bootstrap；名称 trim、大小写敏感、Unicode 友好、≤64 字符、保留字 `all`/`*`/`none`/`null`，隐式默认路径同样执行格式校验。查询未知 namespace 返回空 `items` + `response_code=namespace_not_found` + `response_meta`（`requested_namespace`、`known_namespaces`）与可理解 hint；已知 namespace 无命中仍为正常空结果，成功响应结构不变。`add`/`update` 未知 namespace 默认拒写，调用方传 `confirm_codes=["namespace_not_found"]` 才可创建/迁入；确认码只放行对应的存在性规则，不能绕过格式/长度/保留字校验。MCP 返回结构化结果（非协议 error），admin 返回 HTTP 400 同一信封，UI 二次确认后重试；audit 记录原始 namespace 与 `confirm_codes`/`response_code`。JSONL import 的 `namespace_prefix` 与批量 migrate 目标按显式导入/迁移内部豁免存在性校验。286 项测试通过。alias/改名/历史变体合并另立 [35]。详见 [`plans/28-namespace-governance.md`](plans/28-namespace-governance.md)。
- [31] NDJSON 灾备副本的多进程一致性与真实健康状态（2026-09-16 实现并完成生产验证，待 commit）：SQLite 主库与 NDJSON replica 分别使用生命周期 OS writer lock，禁止第二写实例及在线写维护并保留只读诊断；outbox 使用逐记录递增 `version`，JSONL batch 完成 `fsync` 后才按 `(id, version)` 条件确认，compact/Full Sync 通过持久化临时文件和目录后原子发布。真实子进程崩溃测试覆盖 durable append 与 SQLite ack 边界，锁竞争矩阵覆盖服务、embedding、迁移和 recovery；服务启动先同步 pending 并精确核验主副 ID，之后默认每日检查、异常一小时重试，状态持久化并写 audit，Web Stats 报告 pending/missing/extra/corrupt lines 与检查时间。漂移不会自动修复，页面明确提示通过 Full Export 执行经过校验的 Full Sync。生产重启后验证 `pending=0`、`in_sync=true`、missing/extra/corrupt_lines 均为 0；249 项测试通过。运维和部署 Runbook 后续归入 [34]。
- [33] 移除本地 Ollama Keywords 生成流程（2026-09-15 实现，待 commit）：删除本地 qwen/Ollama extractor、服务层回填入口和管理台 Extract Keywords API/UI；正常 add/update 仅接受调用方的 `metadata.keywords`，缺失值保持为空，不做自动或规则型生成。保留既有 keyword 索引、语义 filter、向量、JSONL 迁移语义及独立的 `keyword_migrate_deepseek.py` 人工离线 CLI；后者不暴露 MCP/HTTP 入口。236 项测试通过。详见 [`plans/33-remove-local-keyword-generation.md`](plans/33-remove-local-keyword-generation.md)。
- [32] 移除服务端 Summary 自动与手工生成流程（2026-09-15 实现，待 commit）：删除 Ollama 摘要调用、后台 worker/启动补偿、专用存储接口和管理台 Summarize API/UI；保留 caller `summary` 输入。普通 add 或正文变化且未提供摘要时，SQLite 在原子写入中使用无模型的首句/首行抽取（80 字上限）；仅元数据及空白等价正文更新保留旧摘要。Import/Recovery 继续原样保留 null，搜索/list/UI 对历史空摘要动态使用相同抽取规则。audit 改记 `text_extract`，无需数据迁移。240 项测试通过。详见 [`plans/32-remove-summary-generation.md`](plans/32-remove-summary-generation.md)。
- [30] JSONL Export / Import 跨环境迁移（2026-09-15 实现并完成生产 smoke，待 commit）：统一使用无 embedding JSONL 作为迁移格式；新增 Full Export（Full Sync、内容与 ID 完整校验、不可变下载副本）、Namespace Export 和单任务后台 Import（64 MiB、10,000 live records、namespace prefix、正文与 keyword/category 按目标 active profile 重新向量化、单事务提交）。Import/Recovery 共享严格历史 JSONL parser，但保持合并与离线替换的独立语义；删除 SQLite path Import、`MemoryStorage.merge`、`app.cli` 和 `bin/import.sh`；缺失 summary 保持为空且不触发 worker。[31] 前置加入每 replica path 单 writer OS lock，以及 missing/extra/corrupt_lines 的真实健康检查。238 项测试通过。生产 Full Export 为 918 行/918 唯一 ID、SHA-256 `98941d4fea7f445f972ddce251fa882e4b8c972502dce02631fef40d6c6c7cb5`、无 embedding；Unicode namespace Export 为 2/2；导出后生产 `pending=0`、`in_sync=true`。全量文件在 `/tmp` 的 Import/Recovery 演练均恢复 918 条且 index-value embeddings 完整。详见 [`plans/30-jsonl-export-import.md`](plans/30-jsonl-export-import.md)。
- [22] storage 后端与角色收敛（2026-09-14 实现并部署，commit `dc8068f`）：运行时仅保留 SQLite 和 SQLite+NDJSON dual；生产启动脚本默认 `dual`，当前 `.env` 未覆盖该设置，因此 SQLite 是唯一权威业务主库，写入提交后经 outbox 异步复制到无 embedding 的 `data/memories.jsonl` 灾备副本，而非同步双写。删除 JSONStorage，将 NDJSON 收窄为不具备业务 CRUD/search/import 的 `ReplicaStorage`。新增 `MEMORY_REPLICA_PATH` 并兼容旧路径变量；同 profile SQLite Import 增加只读来源、完整性校验和记忆/正文向量/index-value 向量单事务复制。历史 `data/memories.json` 已带 SHA-256 manifest 归档；227 项测试通过。生产服务已重启，Full Sync 后 SQLite/JSONL 均为 918 条，ID 集合完全一致、无损坏 JSONL 行、outbox pending=0；后续多进程一致性治理见 [31]。
- [24] keywords/categories 索引值语义解析（commit `98b24b1`）：新增按 embedding profile 版本化的 `index_value_embeddings`，keyword/category 共用 exact-first、namespace-aware 的自适应 Top-3 解析（最低分 0.65、最大分差 0.05）；解析值约束候选，正文 cosine 负责最终排序。`filter_resolution` 同时返回并写入 audit；add/update、metadata 迁移、完整 reindex、activate、codec migration 和 NDJSON 恢复均覆盖索引值向量。生产 active profile `ep_6134c0b15d2bb471` 已离线补全并验证。
- [25] embedding 存储优化与 Python 原生向量化（2026-09-11 实现，2026-09-13 提交并切换生产，commit `539163d`）：完成 RMEV float16 codec、旧 pickle 兼容迁移、provider/profile 抽象、可选 FastEmbed 与 sentence-transformers、本地模型缓存、影子 reindex/原子切换与回滚、超长文本保护、管理统计和离线 `embedding_cli`。生产副本向量存储减少约 77.7%，完整测试 225 项通过。Jina v2 zh 用 155 条 audit 实际标注查询 A/B 后 Recall@10 比 bge-m3 低 18.0pp，未采用。2026-09-13 生产 embedding provider 由 `ollama/bge-m3` 切换为 `sentence_transformers/BAAI/bge-m3`（active `ep_6134c0b15d2bb471`，旧 ollama profile 保留 retired 可回滚），并用 gitignored `.env` + `bin/start.sh` 持久化 provider 配置。详见 [`plans/25-embedding-storage-and-provider.md`](plans/25-embedding-storage-and-provider.md)。
- [14] categories 维度：词表、索引、过滤、统计和迁移（commit `71005a0`）。
- [14] 后续：通用 metadata 索引（commit `f209ad3`）、`search_semantic` categories 过滤（`f66f946`）、迁移 CLI `--db` 修正（`935920f`）、deepseek 全量分类回填 857 条 0 失败（`1735e78`，2026-09-10）。
- [21] NDJSON 低频 compact job（commit `4246e44`）。
- [18] 无 embedding 的 JSONL/NDJSON 灾备副本、离线恢复与管理 UI（commit `12904ac`）。
- [20] audit 日志滚动保留（commit `a9ab663`）。
- [12] summary 输入与 worker fallback（commit `c5e521e`）。
- [13] search 结果 feedback 的 report 工具（commit `eae3c78`；`1fa9d8e` 引导 LLM 在搜索后调用 report）。
- [11] MCP 调用 audit 日志（commit `fa02bc5`）。
- [10] 清理未注册的 MCP 分发分支（commit `07cd60b`）。
- [7] keywords、精确搜索和语义搜索工具改名（commit `1baae9e` 等）。
- summary 功能、字段精简、工具精简、UI 运维页和 ID 短格式。
- 重复判定规则（content + namespace，commit `32e4acd`/`de86cdd`）。

## 归档约定

新完成项从 `backlog-todo.md` 移入本文件，并保留日期、commit、测试和生产验证信息。
