# [30] JSONL Export / Import 跨环境迁移需求

状态：`implemented-and-production-smoke-verified`（2026-09-15，待 commit）  
关联任务：[22] storage 后端与角色收敛、[31] NDJSON 多进程一致性与真实健康状态

## 1. 背景

remember-me 当前以 SQLite 为唯一权威业务主库，并在 `dual` 模式下通过 outbox 异步维护一个不含 embedding 的 NDJSON/JSONL 灾备副本。系统已有：

- 管理端 SQLite→SQLite Import，要求来源与目标 active embedding profile 完全一致；
- SQLite→固定 JSONL 副本的 Full Sync；
- JSONL→SQLite 的离线灾难恢复 `recover_cli`；
- 没有正式、可下载的 Export。

SQLite 文件包含运行时 schema、embedding profile、派生向量和 WAL 一致性问题，不适合作为跨环境交换格式。项目决定统一使用现有 JSONL 记录格式作为可移植数据格式：Export 产出 JSONL，Import 只接受 JSONL，不再支持 SQLite 文件导入。

## 2. 目标

1. 提供全量 JSONL 下载：先从 SQLite 权威主库执行 Full Sync，校验主副本一致后下载现有 JSONL 灾备文件。
2. 提供单 namespace JSONL 下载：直接从 SQLite 的一致性快照生成独立临时 JSONL，不修改全量灾备副本。
3. 提供 JSONL Import：将上传文件合并到现有 SQLite，全部派生向量使用目标库 active profile 重新生成。
4. 保持 Import 与 Disaster Recovery 为两个语义、入口和安全边界不同的功能，但共享 JSONL 解析、规范化和向量生成基础能力。
5. 让导入过程具备整批原子性；embedding、校验或数据库写入失败时不得留下部分导入数据。
6. Export/Import 具备清晰的进度、结果、错误和 audit 记录。

## 3. 非目标

- 不导出或导入 SQLite 文件。
- 不在 JSONL 中保存正文 embedding 或 keyword/category embedding。
- Import 不覆盖或更新目标库已有记忆；第一版只新增、跳过或为冲突 ID 改名。
- Import 不替代完整灾难恢复，不原子替换生产数据库文件。
- Namespace Export 不可作为完整灾备快照。
- 第一版不提供任意 metadata 条件、时间范围或多个 namespace 的组合导出。
- 第一版不把 Export/Import 注册为 MCP 工具；入口属于管理 API/UI，避免 LLM 无意中下载或批量导入数据。

## 4. 术语和功能边界

### 4.1 Full Export

导出所有有效记忆。现有 `data/memories.jsonl`（或 `MEMORY_REPLICA_PATH` 指定文件）是唯一持久化的全量 JSONL 副本。Full Export 不长期维护第二份导出文件。

### 4.2 Namespace Export

只导出一个 namespace。输出是按请求生成的临时 JSONL 下载文件，不更新、不裁剪、不替换全量灾备副本。

### 4.3 Import

把 JSONL 中的有效记忆合并进现有 SQLite。目标库现有数据保留；重复正文跳过；ID 冲突时改名；目标 active profile 负责生成所有派生向量。

### 4.4 Disaster Recovery

服务停止后，从全量 JSONL 构建新的临时 SQLite，完成校验和向量生成后原子替换目标数据库。它不与目标库合并，也不执行面向现有记录的去重或 ID 冲突改名。

```text
同一 JSONL 记录格式
├── Import：合并到现有 SQLite
└── Recovery：离线重建并替换 SQLite
```

## 5. 已确认设计决策

- SQLite 是权威主库；JSONL 是迁移格式和灾备副本，不是业务查询后端。
- Full Export 必须先 Full Sync，再下载现有全量 JSONL。
- Namespace Export 独立生成，绝不修改全量 JSONL。
- Import 只接受 `.jsonl`/`.ndjson` 上传，不接受服务器本地路径和 SQLite 文件。
- JSONL 不携带可被信任或复用的 embedding；Import 和 Recovery 都使用目标环境当前 active profile 重新向量化。
- Import 默认保留来源 namespace，并保留可选的 `namespace_prefix`。
- Import 按 `namespace + text` 判重；重复记录跳过。
- 来源 ID 在目标中未占用时保留；已被其他记录占用时生成新 ID。
- 来源 `created_at`、`updated_at` 和已有 summary 保留。
- 缺少 summary 不阻断导入，原样保存为空且不调用 summary worker；全局 summary 生成流程的拆除属于独立任务 [32]，不纳入本任务。
- Import 与 Recovery 共享底层 JSONL reader/validator，但不得共享会模糊“合并”和“替换”语义的顶层入口。

## 6. JSONL 数据契约

### 6.1 记录格式

沿用现有灾备副本格式，每行一个 JSON object。有效记忆记录包含：

```json
{
  "id": "3c595d07ff011f63",
  "text": "memory body",
  "metadata": {
    "keywords": ["sqlite", "migration"],
    "categories": ["design"]
  },
  "namespace": "remember-me",
  "created_at": "2026-09-14T08:00:00+00:00",
  "updated_at": "2026-09-14T08:00:00+00:00",
  "summary": "short summary"
}
```

兼容的 tombstone 记录：

```json
{"id":"3c595d07ff011f63","deleted":true,"updated_at":"2026-09-14T09:00:00+00:00"}
```

### 6.2 格式规则

- 编码固定为 UTF-8；每条记录独占一行。
- `id`、`text`、`namespace`、`created_at`、`updated_at` 为有效记录必填字段。
- `metadata` 缺失或为 `null` 时按空 object 处理；存在时必须是 object。
- `summary` 可缺失或为 `null`。
- Import 必须忽略来源中的 `embedding`、`synced` 及其他内部派生字段。
- 对同一 ID 的多条记录，按文件顺序和 `updated_at` 解析出最终状态；最终 tombstone 的记录不导入。
- Full Sync 和两种 Export 应输出干净快照：每个有效 ID 仅一行，无 tombstone、无历史版本、无 embedding。
- Import 可兼容历史 append-only 灾备文件，而不仅限于干净 Export 文件。
- JSON 语法错误、缺少必填字段、字段类型错误、非法时间格式或同 ID 无法确定最终状态时，整批校验失败，不进入 embedding 或数据库提交阶段。

第一版保持现有无 manifest 的 JSONL 格式，避免灾备文件和迁移文件出现两套协议。格式版本化如有需要另立兼容设计，不在文件中插入当前 reader 无法识别的 header 行。

## 7. Full Export 需求

### 7.1 调用流程

1. 获取 Full Sync/compact/incremental sync 的互斥执行权。
2. 从 SQLite 获取一致的有效记忆快照。
3. 原子替换现有全量 JSONL，使其成为无历史版本的干净快照。
4. 精确校验 SQLite 与 JSONL 的有效 ID 集合；不能只比较 outbox `pending`。
5. 校验记录数、missing、extra 和损坏行均符合要求。
6. 从已校验 JSONL 取得不可变的下载内容，然后释放互斥锁并响应下载。

### 7.2 一致性要求

- Full Sync 失败或完整性校验失败时不得开始下载。
- 下载期间的新增写入不得造成半行、截断或文件内容变化。实现可以在锁内读取完整 bytes，或从现有 JSONL 创建仅用于本次响应的临时不可变副本；该临时文件不是第二个持久灾备源。
- 与 [31] 一致，多个服务进程共享同一 SQLite/JSONL 时不得使用过期的进程内 `_data` 发布 compact/full-sync 结果。
- 成功结果应包含记录数、文件字节数、SHA-256、生成时间和源 JSONL 路径；HTTP 响应提供下载文件名，例如 `remember-me-full-20260914T164921.jsonl`。

### 7.3 API/UI

- 管理 UI 提供独立的 `Full Export` 按钮。
- 使用会产生 Full Sync 副作用的 `POST` 下载接口，不使用语义上只读的 `GET` 触发同步。
- UI 在下载前提示该操作会先刷新全量灾备副本。

## 8. Namespace Export 需求

### 8.1 调用流程

1. 接收一个非空 namespace。
2. 从 SQLite 权威主库获取该 namespace 的一致性快照。
3. 写入临时 JSONL，校验行数、ID 唯一性和 JSON 可重新解析。
4. 返回不可变下载内容，响应完成后清理临时文件。

### 8.2 约束

- 不调用会替换全量副本的 Full Sync。
- 不读取并过滤现有 JSONL，因为 JSONL 可能尚有 outbox 延迟；SQLite 才是 namespace 导出的权威来源。
- namespace 不存在时返回明确的 404/空结果提示，不生成看似成功的空灾备文件。
- 文件名对 namespace 做安全化处理，例如 `remember-me-namespace-atos-next-master-20260914T164921.jsonl`。
- 第一版一次只导出一个 namespace。

### 8.3 API/UI

- 管理 UI 提供 namespace 下拉框和独立的 `Export Namespace` 按钮。
- Namespace Export 与 Full Export 分开展示，明确前者不能恢复完整数据库。

## 9. Import 需求

### 9.1 输入

- 管理端直接上传 `.jsonl`/`.ndjson` 文件，不再传入服务器文件路径。
- 可选 `namespace_prefix`；默认空字符串。
- 文件名、扩展名、MIME type 仅作为初筛，最终以内容校验为准。
- 上传内容落入受控临时目录，文件名不得参与服务器路径拼接，任务结束后清理。
- 配置最大文件字节数和最大有效记录数；超限在 embedding 前失败。默认 `MEMORY_IMPORT_MAX_BYTES=67108864`（64 MiB）、`MEMORY_IMPORT_MAX_RECORDS=10000`。

### 9.2 解析与规划

1. 流式读取 JSONL，检查 UTF-8、JSON 和字段类型。
2. 解析 append-only 历史和 tombstone，形成最终有效来源集合。
3. 应用 `namespace_prefix`。
4. 对来源内部按最终 `namespace + text` 去重。
5. 读取目标库现有 `namespace + text` 与 ID，规划 skipped、imported 和 renamed。
6. 收集真正需要导入的正文，以及目标 active profile 下缺失的唯一 keyword/category index value。
7. 第一版直接执行 Import，不提供 dry-run；规划与实际提交使用同一套判定逻辑。

### 9.3 向量生成

- 使用目标环境 active embedding provider/profile。
- 正文使用 document embedding 路径。
- keyword/category 对唯一值批量生成，每个新值只生成一次。
- 已在目标 active profile 中完整存在的 index value 不重复推理。
- 检查返回数量、维度、空向量、非有限值和 codec 可编码性。
- 任一正文或 index-value embedding 失败，整批 Import 失败，目标数据库不变。
- 输入超长时返回具体记录 ID、字符/token 限制和可行动提示，但 audit 不记录正文。

### 9.4 原子提交

- embedding 全部完成后才开启目标 SQLite 写事务。
- 提交前重新校验并发期间新增的重复正文和 ID 冲突；重复改为 skipped，ID 冲突重新分配，不覆盖目标记录。
- 在一个事务中写入：
  - `memories`；
  - `memory_embeddings`；
  - `index_values`；
  - `memory_index_values`；
  - `index_value_embeddings`。
- 所有导入记录写为 `synced=0`，由既有 dual outbox 在提交后同步到全量 JSONL。
- 任意数据库错误整批回滚。
- 返回 `source_total`、`effective_total`、`imported`、`skipped`、`renamed`、`imported_ids` 和各阶段耗时。

### 9.5 提交后处理

- 已有 summary 随记录保存；缺少 summary 时保持为 `NULL`，不排队、不调用服务端摘要生成。
- 本任务只解除 Import 对 summary worker 的调用，不修改 add/update 的现有 worker 接入，不删除管理台手工 Summarize 或 summary 基础设施；完整拆除由 [32] 独立实施。
- outbox 同步失败不回滚 SQLite 提交，但必须在 Stats/audit 中暴露 pending 和错误。

### 9.6 执行模型

Import 的重新向量化可能持续数分钟，不得在 Starlette async handler 中同步阻塞服务。管理 API 应创建后台任务并立即返回任务 ID：

```text
POST /api/import              → 202 + import_id
GET  /api/import/{import_id}  → queued / validating / embedding / committing / completed / failed
```

- UI 轮询状态并显示阶段、进度和最终统计。
- 第一版不提供排队和取消；同一时刻只允许一个 Import，新请求返回 `409`。
- 服务进程退出后任务如何恢复可在第一版采用明确失败并允许重试；因为提交是原子的，重试不会看到半批数据。

第一版任务状态保存在进程内，保留当前及最近 20 个任务；进程重启后历史状态不恢复，操作者可安全重试。

## 10. Disaster Recovery 保持独立

`python -m app.recover_cli <source.jsonl> <target.db>` 继续作为停服、离线恢复入口：

- 只接受完整 JSONL；操作者负责确认不是 namespace 局部导出。
- 使用与 Import 相同的 JSONL reader/validator 和目标 embedding provider。
- 构建临时 SQLite，生成正文和全部 index-value embedding。
- 完整校验成功后原子替换目标 `.db`。
- 保留来源 ID 和 namespace，不执行针对旧目标库的 skip/rename。
- 恢复失败时保留原目标数据库。

可以复用解析、向量准备和 staging 组件，但 Import 与 Recovery 必须保留独立 service/CLI 函数、返回结果和文档。

## 11. Audit 与可观测性

### 11.1 Export audit

记录：

- export 类型：full/namespace；
- namespace（如适用）；
- 记录数、字节数、SHA-256；
- Full Sync 和完整性校验结果；
- duration、status 和错误阶段。

### 11.2 Import audit

记录：

- import ID、上传文件原始名称和 SHA-256；
- 文件字节数、来源有效记录数；
- namespace prefix；
- imported/skipped/renamed；
- 目标 embedding profile；
- validating/embedding/committing 各阶段耗时；
- 失败类型和记录 ID，不记录正文及完整 metadata。

Audit 写入失败不得破坏成功的 Export/Import，但应记录服务日志。

## 12. 安全要求

- Import API 不再接受任意服务器路径，消除通过管理接口读取本地 SQLite/文件的能力。
- 上传文件只写入受控临时目录，限制大小、记录数和单条正文长度。
- 下载文件名必须安全化，不能由 namespace 注入路径或响应头。
- 错误响应不泄露服务器绝对路径、SQLite SQL、正文或 embedding。
- Export 会暴露完整记忆正文，沿用管理端现有信任边界；若未来开放非本机访问，认证授权需作为独立前置任务。

## 13. 兼容与迁移

- 删除管理 UI 中的 SQLite path Import，改为 JSONL 文件上传。
- `/api/import` 从 JSON body `file_path` 改为 multipart 文件上传；这是管理 API 的有意不兼容变更。
- 删除或重做当前 `app.cli` 的 SQLite→SQLite import 行为，不保留隐藏兼容入口。
- 删除 `MemoryStorage.merge`/SQLite same-profile merge 中只为 SQLite Import 服务的代码和测试；共享的事务写入能力可重构后供 JSONL Import 使用。
- 保持当前 `data/memories.jsonl` 和历史 append-only/tombstone 文件可导入、可恢复。
- 保持 `recover_cli` 命令语义和原子替换保证。
- 更新 AGENTS.md、管理 UI 文案、架构类图和项目 backlog。

## 14. 与 [31] 的依赖

Full Export 依赖可信的 Full Sync。上线前必须同时满足 [31] 的以下最低条件：

- compact、incremental sync、Full Sync 和 Export 之间有正确的单写者/跨进程协调；
- Full Sync 从 SQLite 一致性快照构建，不从可能过期的 NDJSON 进程内 `_data` 构建；
- `in_sync` 或 Export 前置检查可以识别主副 ID 漂移；
- 完整性失败时禁止下载并提供 Full Sync/运维提示。

[31] 的长期定期深度核验可以后续完成，但不能在已知可能导出不完整文件的状态下发布 Full Export。

## 15. 验收标准

### 15.1 Full Export

- Full Sync 后下载文件与 SQLite 有效 ID 集合完全一致。
- 导出文件无 embedding、tombstone、重复 ID 和损坏行。
- Full Sync/校验失败不返回伪成功下载。
- 并发 add/update/delete 时导出仍对应一个一致快照。
- 下载期间发生新写入不会产生半行或变化中的响应内容。

### 15.2 Namespace Export

- 只包含指定 namespace，且与 SQLite 中该 namespace 的有效 ID 集合一致。
- 不改变全量 JSONL 的 bytes、mtime、记录数或 compact 状态。
- namespace 不存在、包含特殊字符或超长时行为明确且文件名安全。

### 15.3 Import

- 可导入 Full Export、Namespace Export 和历史 append-only 灾备 JSONL。
- SQLite 文件、非法 JSON、字段错误、超限文件和损坏 UTF-8 被拒绝。
- 正文与 keyword/category embedding 全部属于目标 active profile。
- embedding/profile/数据库任一失败时目标库无部分新增。
- 覆盖目标重复正文、来源内部重复、ID 冲突、namespace prefix 和并发冲突。
- 缺少 summary 的记录保持为空且不产生 summary 后台任务。
- dual 模式下成功记录进入 outbox，最终出现在全量 JSONL。
- 后台任务不会阻塞 MCP 搜索、写入或管理页面健康请求。

### 15.4 Recovery

- 继续满足临时库构建、重新向量化、完整校验和原子替换。
- Import 重构不得削弱原数据库在恢复失败时保持不变的保证。

### 15.5 回归

- 全量测试通过。
- 生产数据副本执行 Full Export 后，SQLite/JSONL 数量与 ID 集合一致、损坏行 0、outbox pending=0。
- 对生产导出的副本在临时数据库完成一次 Import 和一次 Recovery 演练，不修改生产主库。

## 16. 建议实施顺序

1. 提取共享 JSONL reader/validator，覆盖历史记录和 tombstone。
2. 完成 [31] 中 Full Sync/一致性检查的前置修复。
3. 实现 Full Export 和 Namespace Export API/UI。
4. 建立 Import staging、目标 profile 批量 embedding 和原子提交引擎。
5. 增加后台任务状态 API/UI。
6. 删除 SQLite Import 入口和专用 merge 代码。
7. 复用共享组件回归 `recover_cli`。
8. 补充 audit、文档、并发/故障测试和生产副本演练。

## 17. 交付边界

建议单独提交 [30]，提交内容包含：

- JSONL Export/Import 服务层与管理 API；
- Full/Namespace Export UI；
- Import 上传与后台进度 UI；
- JSONL 解析、staging、向量生成和原子提交实现；
- 删除 SQLite Import；
- Recovery 共享组件重构；
- audit、测试、AGENTS.md、架构图和 backlog 更新。

如果 [31] 的前置修复规模较大，应先独立提交 [31]，再提交 [30]，避免把灾备并发修复与迁移功能混成一个不可审查的提交。
