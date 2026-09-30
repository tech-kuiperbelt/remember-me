# [48] 后端重构 Coding Plan

日期：2026-09-30。状态：阶段 1–5 已实现。基线：`d16be35`。

## 实施进度

- 阶段 1（隔离阻塞业务，明确取消与停服）：已完成。新增 `app/calls.py`（Runtime 拥有的 `AsyncCallAdapter`：4 业务 worker + 1 控制 worker、延迟创建、worker 内准入、`asyncio.shield` 取消语义、关闭时取消排队并等待运行中任务）与 `app/provider_adapter.py`（`SerializedProvider` 单把可重入锁串行 query/document/batch/warmup/close）。MCP 工具/resource 与 Admin 阻塞业务全部经适配器执行，配置读写走控制池；Admin 变更类记忆路由把准入推迟到 worker，排队写入会在维护进入后被重新拒绝。`Runtime.close()` 先关闭适配器再释放存储/provider/audit。
- 阶段 2（收回任务与恢复协调职责）：已完成。新增 `app/import_coordinator.py`（每个导入一个完成 Event，最外层 finally 统一收尾，`wait_import` 返回终态且保证清理完成）；`Runtime.start_maintenance_import()` 拥有导入维护动作的启动与收尾（单个等待线程），`maintenance_import_status()` 供 Admin 查询；`Runtime.maintenance_sync()` 集中 force_sync、恢复标记核验/删除与 sticky error 清除；恢复 job 读写加锁并提供 `recover_status()` 快照；`Runtime._close_business()` 统一业务资源释放（任一步失败也继续释放并保留首个错误）。Admin 删除 100ms 轮询、额外维护等待线程及对 `service._workers` / `runtime._verify_recovered_replica()` 的访问。
- 阶段 3（补齐存储契约与 provider 边界）：已完成。`MemoryStorage` 补全 `stop` 与索引/关键词/分类业务接口，并把 `force_sync`/`export_full_snapshot` 变为默认抛 `StorageCapabilityError` 的具体能力方法；新增窄接口 `SQLitePrimaryStorage`（outbox、连接替换、embedding profile 维护，方法公开化）与 `ReplicaStorage` 的 `acquire/release_writer_lock`；Service 删除 `_primary_storage`/`hasattr`/`isinstance(DualStorage)` 能力探测，导入准备与导出直接走 `self.storage`，并用 `active_embedding_profile()` 避免 dual 全量 `get_stats` 扫描；provider 门面统一委托 `embed_document/embed_query/embed_documents`，删除 Ollama 名称分支，异常转换集中到 `SerializedProvider`。新增 `tests/test_storage_contract.py`（sqlite/dual 同契约、能力异常、不完整替身构造失败、provider 委托与异常）。
- 阶段 4（声明工具与路由策略，合并响应公共逻辑）：已完成。新增 `app/tool_definitions.py`（11 个工具 schema/描述逐字迁出）与 `app/tools.py`（`ToolSpec` 表关联 definition/handler/write/search/namespace_mode，审计策略登记在 `app/audit.py` 注册表，`list_tools` 与分发同源，共享 `execute_tool` 统一准入、计时、异常、检索回执与审计；参数适配仍是普通函数）；`server.py` 变薄只留传输生命周期。`audit.py` 裁剪改为显式 `AUDIT_EVENTS` + `PARAM_POLICIES`/`RESULT_POLICIES` 注册表，去掉隐式兜底。`admin.py` 用声明式 `ROUTE_TABLE`（static/read/write/action/control/exempt）替换 actions/exempt/deferred_writes 与 `next(iter(methods))`，共享 JSON/错误响应映射并用 `_maps_domain_errors` 统一标准 handler，import/export/recover/keywords/config 保留自有清理与审计，lexical/anchor 共用 `_terms_search`。新增 `tests/test_tool_specs.py`、`tests/test_audit_policies.py`、`tests/test_admin_routes.py` 与 golden fixtures。
- 阶段 5（离线 CLI 去重及 Stats 读取优化）：已完成。新增 `app/deepseek_cli.py`（共用存储装配、`opencode run` 调用、ANSI/fence/JSON 数组解析、候选块渲染、资源关闭），keyword/category/anchor 三个回填 CLI 保留原有函数门面并委托到它；离线路径解析统一到配置快照（删除三处 `"data/memories.db"` 与 anchor `"data/anchor-backfill"` 硬编码，dry-run 仍只读、无写锁、无副本线程）；`MemoryStorage` 新增 `live_memory_ids()`（`SELECT id ... WHERE deleted = 0`），`DualStorage` 健康检查改用它，不再导出全部正文/metadata。新增 `tests/test_deepseek_cli.py` 及健康检查 ID 轻读/零 pending 漂移回归。

实测：阶段 1 完成时 610 pytest；阶段 2 完成后 630；阶段 3 完成后 643；阶段 4 完成后 658（review 修复后 659）；阶段 5 完成后 677（review 修复后 683）pytest 通过，浏览器回归均通过。无 schema/数据迁移，未改工具/schema/hint、HTTP 状态与字段、配置/目录、embedding profile 格式、SQLite/JSONL 格式。

生产冒烟（2026-09-30 21:03）：在加载未提交阶段 3 代码并重启的服务（dual 模式，端口 1314）上通过。`GET /read-me` 200、`POST /mcp` tools/list 正常、`GET /api/storage-paths` 返回 dual 的 primary/secondary 路径（验证新 `storage_paths()` 契约）、`GET /api/stats` total=1140 backend=dual in_sync=true pending=0 且副本 missing/extra/corrupt=0；MCP 走通 `add → get_details → search_lexical → search_anchor → update → delete` 闭环并清理，`list_keywords`（browse/semantic）、`search_semantic`/`search_meta`、`report` 均正常。

生产冒烟（2026-09-30 21:49）：重启（execv，PID 6059）加载未提交阶段 4 代码后通过。`GET /read-me` 200、`POST /mcp` tools/list 返回 11 个工具、`GET /api/storage-paths` 返回 dual 路径、`GET /api/stats` total=1141 in_sync=true pending=0；分别经 `/mcp` HTTP 端点与重连后的 MCP 客户端走通 `add → get_details → search_lexical → search_anchor → update → delete` 并清理，计数回到写入前（delta 0）。重启会令旧 MCP 客户端会话失效（-32602），客户端重连后恢复，服务端不受影响。

生产冒烟（2026-09-30 23:49）：用户重启服务与 opencode 后，加载未提交阶段 5 代码通过。`GET /read-me` 200、`POST /mcp` tools/list 返回 11 个工具、`GET /api/stats` total=1143 in_sync=true pending=0 且副本 missing/extra/corrupt=0；MCP 客户端走通 `add → get_details → search_lexical → search_anchor → update → delete` 并清理（计数回原值）。离线侧：`bin/manage status` 嵌入索引 complete 2056/2056，`bin/manage migrate keywords --dry-run` 只读运行（candidates=33/skipped=1110/total=1143，未取写锁），`MEMORY_DATA_DIR` 下 `default_primary_path()` 返回 `<root>/memories.db`（阶段 5 路径修复）。



## 目标与约束

消除同步业务对事件循环的阻塞，收回管理层泄漏的任务协调职责，明确存储与 provider 接口，减少扩展工具和检索时的重复修改。

保持现有 MCP 工具名称、schema、hint、搜索回执和审计关联，HTTP 状态码与响应字段、配置和目录规则、embedding profile、SQLite/JSONL 格式及事务边界。此次无需数据迁移。接口整理过程中允许增加内部类型和公开方法，不增加产品配置项或新的外部 API。

采用组合、小型声明表和明确接口。SQLite 仍是实际主库，dual 仍是副本协调层；保留已有 SQL 事务所有者。此次不按文件行数机械拆分 SQLiteStorage，不引入插件系统、通用工作流引擎或新第三方依赖。

## 编码顺序

### 阶段 1：隔离阻塞业务，明确取消与停服

1. 增加 Runtime 拥有的异步调用适配器，统一承接 MCP、HTTP 和 MCP resource 的同步业务。业务线程池固定最多 4 个 worker；配置读写等控制操作使用独立的单 worker 执行器，避免推理占满业务池时无法配置。纯内存状态和静态路由直接响应。执行器延迟创建，仅启动后可提交，关闭时释放。
2. 网络请求和上传读取仍在异步层；embedding、SQLite、JSONL 扫描/写入、审计文件读取、配置文件读写等阻塞段进入执行器。先梳理所有入口，避免仅迁移搜索而遗漏 Stats、导出、Sync、resource 和配置保存。
3. 提交前捕获 request/session/transport 上下文，在线程内通过 `contextvars.copy_context()` 传播。SDK 的 request context 在异步入口读取后作为值传递，worker 不依赖 SDK 的异步上下文访问。
4. 准入保护与实际业务执行在同一 worker 生命周期内：执行前重新检查 closing/model/maintenance 状态，通过检查才执行；业务、结果登记及审计完成后释放保护。排队中的任务不能凭旧状态绕过新进入的维护模式。
5. 跟踪每个已提交任务。调用方取消时，尚未开始的任务可取消；已经执行的任务继续到完成，不强行终止线程，也不提前释放维护计数。完成路径负责审计、清理和异常消费，不依赖客户端仍然连接。异步等待通过 shield 保持底层任务所有权。
6. 停服顺序明确为：拒绝新提交，取消尚未开始的任务，异步等待运行中的调用结束，等待已有 import/recover/model 任务，关闭存储/provider/audit，最后关闭执行器。事件循环内不调用阻塞的 join/shutdown；重启仅在上述清理完成后 exec。
7. 增加共享 provider 同步适配器，使用同一把可重入锁串行执行 query/document/batch/warmup/close。在线请求、导入和恢复共用它；保留 provider 内部已有批处理。取得存储事务锁前完成推理，避免锁顺序反转。

验收：慢推理期间控制端点和事件循环可响应；并发数量有界；排队写入遇到维护或 closing 被拒绝；取消中的写入、审计和停服清理均不丢失。

### 阶段 2：收回任务和恢复协调职责

1. 将导入任务状态、worker、完成事件及文件清理封装为 service 内部的导入协调对象。MemoryService 继续提供现有 start/status 门面，新增公开 `wait_import(import_id)`，返回最终状态且保证清理结束。
2. 完成事件在 worker 的最外层 finally 中设置，覆盖成功、失败、文件清理和 active job 状态更新；启动线程失败也必须完成状态收尾。`close()` 等待全部 worker。
3. Runtime 提供维护导入入口与状态查询，拥有维护动作的预约和完成：后台任务结束后由 Runtime 收尾。Admin 删除 100ms 轮询、额外的维护等待线程及对 `service._workers` 的访问。
4. Runtime 提供公开的维护 Sync 操作，集中完成 force_sync、恢复标记核验/删除和 sticky error 清除。Admin 不再访问 `_verify_recovered_replica()` 或自行操作恢复标记。
5. 保留恢复的阶段、写锁与 switching 行为；恢复状态读写增加锁并返回快照，避免 worker 更新和 HTTP 查询共享可变字典。
6. 统一模型准备失败和正常退出的业务资源释放路径：即使某项 close 抛错，也继续释放后续资源，保留原始错误。模型准备仍独立拥有取消和重试流程。

验收：导入完成前维护动作始终占用；清理或启动失败不会永久卡住维护；恢复错误仅在验证成功后清除；Admin 不访问 Service/Runtime 私有成员。

### 阶段 3：补齐存储契约与 provider 边界

1. 盘点 MemoryService、IndexValueResolver、Runtime 实际调用，补齐 MemoryStorage 的业务查询/索引向量接口及 stop 生命周期方法。SQLiteStorage、DualStorage 与测试替身显式实现，删除必需能力缺失时静默跳过的分支。
2. outbox、连接替换、embedding profile 维护属于 SQLite 主库能力，明确由 SQLite 类型或专门的窄接口承载；不把这些方法塞进 JSONL ReplicaStorage。
3. 补齐 ReplicaStorage 被协调层实际依赖的锁生命周期方法。允许 Runtime 在装配位置识别 SQLite/dual，但 Service 的普通检索、导入准备不拆开 dual.primary。Full Export 与 Sync 的特殊能力通过明确接口/适配器表达，不依赖任意 `hasattr`。
4. Service 统一调用 provider 的 embed_query/embed_document/embed_documents，删除 Ollama 名称分支；异常转换放在共享 provider 适配器。测试改为注入假 provider，保留现有公开方法门面。
5. 保持 query/document 前缀、批处理、维度校验、token 上限与 profile ID 不变。

验收：SQLite/dual 运行相同的业务契约测试；不完整测试替身在构造或契约测试时失败；各 provider 的 query/document/batch 路径和错误保持一致。

### 阶段 4：声明工具与路由策略，合并响应公共逻辑

1. MCP 使用静态 ToolSpec 表关联工具定义、handler、读写属性和 hint 生成器。list_tools 与 dispatch 从同一表生成；公共外层统一准入、计时、异常处理、搜索回执和审计。各工具参数适配仍是普通函数。
2. 工具 schema/描述与传输生命周期代码分离。动态 hint 保持原有语义；audit 参数裁剪继续由 audit 模块承担，并增加工具集合与裁剪策略完整性测试；每个工具必须显式声明专用裁剪或允许的默认策略。
3. Admin 路由声明直接携带 read/write/maintenance-action/control 策略。删除分离的 actions/exempt 集合和路径布尔推断；GET/HEAD、多方法配置路由的策略必须明确，不能依赖 set 的迭代顺序。
4. 提取 HTTP JSON 响应与领域异常映射，保留各端点既有状态码和 `code`/`response_code` 字段差异，避免借重构改变客户端契约。意外异常只在边界记录一次 traceback。
5. lexical/anchor 共用请求解析、namespace 校验与结果 envelope 的机械流程，传入明确的业务函数；内容检索与 anchors 检索的评分含义、hint、审计名称和查询实现保持独立。

验收：11 个工具全部具备 handler/hint/audit 策略；HTTP 每个路由策略有覆盖；原有搜索回执、反馈校验、namespace 错误、维护拒绝和传输上下文测试通过。

### 阶段 5：离线 CLI 去重及 Stats 读取优化

1. 从 keyword/category 回填提取共用存储装配、opencode 子进程执行、ANSI/JSON 输出处理和资源关闭辅助函数。保留现有函数门面、不同提示词、候选集合更新、数量限制与逐条容错行为；用原有输出样本验证提取结果。
2. 删除重复的硬编码默认数据目录，离线入口使用统一配置快照。只读与 dry-run 不取得写锁、不创建配置、不启动副本线程；写入仍受现有主库锁保护。
3. SQLite 新增只读取 live memory ID 的接口供完整健康检查使用，替换为比较 ID 而导出完整文本/metadata 的逻辑。副本读取仍执行当前的损坏行与 ID 检查。
4. Stats 保持每次实时完整核验，保留 `in_sync`、pending、缺失/多余/损坏行及 checked_at 语义；本次不引入缓存或新增快速统计 API。

验收：完整健康检查能发现 pending=0 时的副本漂移和损坏；ID 查询不加载正文或向量；迁移 CLI 读写锁和 dry-run 行为保持一致。

## 测试与合入门槛

- 并发测试用 Event/Barrier 控制慢 provider，不依赖短 sleep 和严格耗时阈值：推理阻塞时另一个控制请求必须在释放推理之前完成；同时验证 provider 最大并发为 1。
- 覆盖取消发生在排队、执行中、写入完成与返回之间；维护进入、恢复 switching、关闭与这些时刻交错。验证一次写入至多提交一次、一次调用只产生一个完成审计，资源关闭前无活跃写者。
- 保留并运行真实 stdio/SSE/Streamable HTTP 子进程退出与重启测试、维护/恢复故障注入、SQLite outbox 与 JSONL 一致性测试。
- 每阶段先运行受影响测试；全部阶段完成后跑全量 pytest 和浏览器回归。禁止只凭新接口的 mock 测试替代原有行为验收。
- 审查依赖方向：Admin 只依赖公开操作，Service 不识别具体 embedding provider，必需存储能力有声明，索引/主记录/outbox 仍在原子事务中更新。
- 阶段依序实施，每阶段形成可独立 review 的代码差异；提交由用户授权。更新设计文档与 AGENTS，记录实测测试数量和差异。

## 上线与完成标准

完成编码、测试与 review 后安排维护窗口，保存只读统计和 active profile 基线，再优雅重启加载新代码；验证配置页、三类传输、检索、维护进入退出及 SQLite/JSONL 健康。生产不做破坏性恢复演练。

本次重构无 schema 或数据迁移；失败时停止新进程并恢复基线代码启动，避免并行写者。计划完成需代码及验收全部通过，部署状态单独记录；本次编写计划不执行部署。

## 阶段 1 Review 修复记录（2026-09-30）

- HTTP 取消时取消尚未开始的调用；已经开始的 worker 必须完成后才传播取消，包括重复取消，确保外层 reader/action 保护不会提前释放。MCP 保持 worker 内部准入与完成审计。
- 导出完成审计由 worker 执行；HTTP 取消等待其收尾后清理临时文件，覆盖成功、失败和排队取消。导入启动期间取消时，已启动的后台任务接管维护 action 与上传文件。
- `stop_accepting()` 与等待关闭分离；Runtime 进入 closing 时立即停止提交并取消业务/控制两条队列，worker 开始执行时再次检查状态。
- 新增真实 Admin handler 的取消回归；修正原排队取消测试先释放线程再等待取消的竞争。全量 620 项 pytest 与浏览器回归通过。
- 本次修复未提交、未重启生产服务；阶段 2–5 仍待实施。

## 阶段 2 Review 修复记录（2026-09-30）

- 维护等待线程启动失败时，由当前调用 worker 等待已启动导入的完成事件；清理完成后才释放 action 并返回任务 ID，避免 HTTP 错误路径提前删除输入文件。请求取消仍等待该收尾路径结束。
- `wait_import(timeout)` / `ImportCoordinator.wait()` 在完成事件未触发且超时后抛出 `TimeoutError`；未知任务仍返回 None，只有完成事件触发后才返回已清理的任务状态。
- 新增超时、等待线程启动失败、失败收尾期间 HTTP 取消的三项回归；相关导入、取消、维护和 Runtime 共 46 项测试通过。本轮未重跑全量与浏览器，未提交、未重启。

## 阶段 3 Review 修复记录（2026-09-30）

- `resolver_for_configured_profile` 先读取 active profile，缺失时直接返回 None，再创建 embedder；避免对无 profile 的存储在实例化 provider 之后才早退。
- `MemoryStorage.storage_paths` docstring 与实际返回对齐：始终返回 `backend`，仅 replica 后端附带 primary/secondary 路径。
- 全量 643 项 pytest 通过；未提交、未重启。

## 阶段 4 实现记录（2026-09-30）

- 新增 `app/tool_definitions.py`（11 个工具 schema/描述由 `server.py` 逐字迁出）与 `app/tools.py`（`ToolSpec` 表：definition/handler/write/search/namespace_mode；审计策略登记在 `app/audit.py` 注册表；`execute_tool` 统一准入、计时、异常映射、检索回执与审计；参数适配保持普通函数）。`server.py` 仅保留传输生命周期与薄适配。
- `app/audit.py` 裁剪改为显式 `AUDIT_EVENTS` + `PARAM_POLICIES`/`RESULT_POLICIES` 注册表，移除隐式兜底，新增工具/事件与裁剪策略完整性测试。
- `app/admin.py` 用声明式 `ROUTE_TABLE`（static/read/write/action/control/exempt）替换 `actions`/`exempt`/`deferred_writes` 与 `next(iter(methods))`；提取 `json_response`/`error_response`/`domain_error_response`/`internal_error_response`，标准 handler 经 `_maps_domain_errors` 统一映射，import/export/recover/keywords/config 保留自有清理与审计；lexical/anchor 共用 `_terms_search`。
- 新增 golden fixtures（`tests/fixtures/`）与 `tests/test_tool_specs.py`、`tests/test_audit_policies.py`、`tests/test_admin_routes.py`，保证工具 schema/hint/note 与错误码逐字不变。
- 全量 658 pytest + 浏览器回归通过；未改工具名称/schema/hint/审计关联与 HTTP 状态码/字段；无 schema/数据迁移。尚未提交。

## 阶段 4 Review 修复记录（2026-09-30）

- 移除无效的 `ToolSpec.audit_policy` 字段（裁剪按工具名/事件名键控）；`test_tool_specs` 改为校验 `spec.name` 同时登记在 `AUDIT_EVENTS` 与裁剪策略注册表，避免"声明了却不生效"。
- `_maps_domain_errors` 增加 `domain=False`；`/api/namespaces/migrate` 保持原有 `{"detail": ...}` 错误形状，不套用统一领域 envelope，避免客户端契约漂移（新增回归 `test_migrate_namespace_keeps_plain_error_envelope`）。
- 全量 659 pytest + 浏览器回归通过；未提交。

## 阶段 5 实现记录（2026-09-30）

- 新增 `app/deepseek_cli.py`：共用存储装配（`build_cli_storage`）、`opencode run` 调用（`run_opencode`）、ANSI/fence/JSON 数组解析（`extract_json_array`）、候选块渲染（`format_candidates_block`）与资源关闭（`stop_storage`）。keyword/category/anchor 三个 CLI 保留 `build_storage`/`format_candidates_block`/`extract_*`/`_extract_json_array` 门面并委托到它；提示词、候选集合、限量与逐条容错不变。
- 离线路径解析统一到配置快照：删除三处 `"data/memories.db"` 与 anchor `"data/anchor-backfill"` 硬编码，兜底走 `default_data_dir()`（纯计算，不创建目录或配置）；dry-run 与只读仍用 `sqlite(read_only=True)`，不取写锁、不启动副本线程，写入仍由主库 flock 保护。
- `MemoryStorage` 新增 `live_memory_ids()`（`SELECT id FROM memories WHERE deleted = 0`），`SQLiteStorage` 实现、`DualStorage` 委托；`_inspect_replica_locked()` 改用它，实时 Stats/健康检查不再导出全部正文与 metadata。副本侧 `inspect_jsonl` 的损坏行与 ID 校验保持不变。
- 新增 `tests/test_deepseek_cli.py`（解析样本、opencode 调用、路径装配）与 `tests/test_ndjson_replica.py` 的健康检查 ID 轻读、零 pending 漂移回归；`test_storage_contract.py` 增加 `live_memory_ids` 契约断言。
- 全量 677 pytest + 浏览器回归通过；无 schema/数据迁移，未改 MCP/HTTP/格式。尚未提交。

## 阶段 5 Review 修复记录（2026-09-30）

- `deepseek_cli.extract_json_array` 恢复按模式选择正则：`strings_only`（keyword/category）用非贪婪 `\[.*?\]`，raw（anchor）用贪婪 `\[.*\]`；此前统一按非贪婪优先会导致多对象数组被 prose 包裹时只解析出内层数组，anchor 批次被误记为 `error`。新增多对象 + prose 回归（`test_deepseek_cli.py`、`test_anchor_migrate.py`）。
- anchor `_write_outcome` 恢复 `MEMORY_DATA_DIR` 下的 `rooted_path` 包含（相对/空目录），修正 `--manifest` 默认帮助文案。
- `deepseek_cli.default_primary_path()` 复用新增的无副作用 `configuration.resolve_data_dir()`：未设 `MEMORY_STORAGE_PATH` 时尊重 `MEMORY_DATA_DIR`（相对路径相对仓库根）并 root 默认主库，纯计算不创建目录或配置；新增自定义/相对数据根与"不创建文件"回归。
- 全量 683 pytest + 浏览器回归通过；未提交。
