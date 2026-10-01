# remember-me 系统设计

2026-10-01 更新包结构说明。本文基于当前代码（`app/`）与 `AGENTS.md`，描述架构、主要类职责、MCP 工具读写流程与数据结构。

待实现的默认数据目录与现有服务迁移方案见 [按操作系统选择默认数据目录](platform-data-directory.md)。

## 1. 架构

### 1.1 部署形态

单进程、单写者。同一份 SQLite 主库在任一时刻只允许一个写入进程：可写进程在启动时对
`<db>.writer.lock` 取进程生命周期内的 `flock` 独占锁（`app/storage/database_lock.py`），第二个写者
立即失败。多客户端通过**同一个 HTTP 服务实例**共享访问；不要为每个客户端各起一个 stdio 进程。

```
                         ┌──────────────────────── 一个 Python 进程 ────────────────────────┐
 stdio client ── stdin/stdout ─┐                                                                  │
 opencode ── GET /sse + POST /messages/ ─┤   ┌──────────────┐                                  │
 Codex ────── POST /mcp (Streamable HTTP)─┼──►│ Starlette/   │                                  │
 运维/浏览器 ─ /admin/console, /api/* ────┤   │ Uvicorn app  │                                  │
 LLM agent ── GET /read-me ───────────────┘   └──────┬───────┘                                  │
                                                     │                                           │
                          AdmissionMiddleware(503 关闭中) + Host/Origin(仅 /mcp)                │
                                                     │                                           │
                          ┌──────────────────────────┴───────────────────────────┐               │
                          ▼                                                       ▼               │
                   MCPApplication                                          AdminAPI              │
                   (11 tools / 2 resources)                                (/api/*)              │
                          └──────────────────────────┬───────────────────────────┘               │
                                                     ▼                                           │
                                              MemoryService  (业务/命名空间/导入/导出)           │
                                       ┌─────────────┼──────────────┬─────────────┐              │
                                       ▼             ▼              ▼             ▼              │
                                 DualStorage    IndexValueResolver Embedder   AuditJournal        │
                                 ┌────┴─────┐    (关键词/分类语义)  (provider)  (JSONL 滚动)      │
                                 ▼          ▼                                                    │
                        SQLiteStorage   NDJSONReplica                                            │
                        (主库, 向量)    (灾备副本, 无向量)                                        │
                                       └── 后台: outbox 同步 / compact / 健康核验 ──┘            │
                                                                                                 │
 Runtime: 拥有上面全部资源的创建与释放；Settings 为不可变配置快照                                │
                                                                                                 └
```

### 1.2 分层职责

| 层 | 模块 | 职责 |
|---|---|---|
| 配置 | `config/configuration.py`, `config/settings.py`, `config/jsonc.py` | 配置解析、JSONC 保真读写及不可变 Settings 快照 |
| 领域规则 | `domain/` | anchors、categories、namespace、summary 和 lexical 查询规则 |
| 生命周期 | `runtime.py` | `Runtime.start()` 配置日志/审计、创建 provider 与 storage、校验 active profile/index 向量、预热、启动后台线程；`close()` 幂等释放 |
| 传输 | `server.py`, `transport/` | MCP 生命周期、工具定义与分发、HTTP 管理路由和检索反馈；不承载业务逻辑 |
| 业务 | `service.py` | 记忆 CRUD/检索、命名空间治理、摘要策略、JSONL 导入导出、关键词查询 |
| 存储 | `storage/` | 契约与延迟工厂、SQLite 主库、NDJSON 灾备副本、dual 协调、锁及 JSONL 恢复 |
| 向量 | `embedding/` | provider 抽象（document/query 双路径）、维度与长度校验、RMEV 编解码、索引值语义解析 |
| 审计 | `audit.py` | 滚动 JSONL 工具调用/后台任务日志，永不抛错、永不阻塞 |
| 运维协调 | `operations/` | 异步调用池、维护状态、导入协调和模型准备 |
| 迁移/运维 | `cli/`, `bin/manage` | 离线维护，写操作依赖主库 writer lock；根目录 CLI 模块保留兼容入口 |

现有 `python -m app.server` 与 `bin/` 命令保持不变。原有六个 Python CLI 命令仍由根目录
薄入口提供；实现位于 `app.cli`。内部模块统一从职责包导入；`app.storage` 导出公共契约和
工厂，工厂延迟加载具体后端。Domain 不依赖 Service、Runtime 或传输层，在线业务不依赖 CLI。

### 1.3 传输端点

| 端点 | 传输 | 说明 |
|---|---|---|
| stdio | MCP stdio | 显式选择；单客户端；同一主库已有 HTTP 写者时不能再起 |
| `GET /sse` + `POST /messages/?session_id=…` | 旧版 HTTP+SSE | 保留兼容（opencode） |
| `POST /mcp` | MCP Streamable HTTP | `stateless=True` + `json_response=True`；无 `Mcp-Session-Id`；非 POST 经 loopback Host/Origin 校验后 405 `Allow: POST` |
| `GET /read-me` | 静态页 | LLM 配置说明；自引用 `http://127.0.0.1:<port>` |
| `/admin/console`, `/api/*` | 管理 HTTP | 管理台与运维 API |

三种 MCP 传输共用同一个 `mcp.server`、runtime、provider 与 storage。

## 2. 主要类与职责

### 2.1 生命周期与配置

- **`Settings`**（frozen dataclass）：一次运行的环境快照。字段见 §4.6。`from_env()` 校验
  `MEMORY_STORAGE`（仅 `sqlite`/`dual`）、`EMBED_DTYPE`、默认 namespace。
- **`Runtime`**：显式资源所有权。`start()` 依次配置日志→审计→provider→storage→profile 完整性
  校验→service→预热→（dual）启动后台；任一步失败即回滚已获资源并抛原始异常。`close()` 幂等，
  顺序：等待导入→等待同步→最后一次 outbox 同步→释放存储/写锁→provider→审计→日志 handler。
- **`DatabaseWriterLock`**（`storage/database_lock.py`）：`<db>.writer.lock` 上的非阻塞 `flock`；锁文件存在 ≠ 锁被占用。

### 2.2 传输层

- **`MCPApplication`**（`server.py`）：持有 `mcp.server.Server`，注册
  `list_tools` / `call_tool` / `list_resources` / `read_resource`。工具定义与分发由
  `transport.tools` 的 `ToolSpec` 表驱动，11 个工具调用均写入审计。`_audit_context()` 组装
  `source/request_id/session_id/client_name/client_version`，并在 Streamable HTTP 下合并
  `transport=streamable_http` 与每请求 `http_request_id`。
- **`AdminAPI`**（`transport/admin.py`）：管理台与 `/api/*` 路由；与 MCP 共用 `MemoryService`，
  未知命名空间映射为 HTTP 400 + 同一 `response_code` 信封。
- **`AdmissionMiddleware`**：`runtime.state != "running"` 时对所有 HTTP 返回 503；为每个 HTTP
  请求生成 `audit_request_id` 写入 `scope["state"]`。
- **ASGI 适配器**：`SSEEndpoint`（旧 SSE，CancelScope 可被 `close_connections()` 取消）、
  `StreamableHTTPEndpoint`（安全校验→方法分派→`session_manager.handle_request`）、
  `ReadMeEndpoint`（模板注入 `{base}`）。

### 2.3 业务层

- **`MemoryService(settings, storage, embedding_provider, audit_journal=None)`**：业务唯一入口。
  - 命名空间：`known_namespaces()` / `check_namespace()`（未知即拒，`confirm_codes` 可越过）。
  - 记忆：`memory_add/search/list/get/update/delete/clear/get_details`。
  - 关键词：`memory_list_keywords`（browse/literal/semantic）。
  - 统计与反馈：`memory_stats`、`memory_report`。
  - 运维：`memory_sync`、`memory_export_full/namespace`、`memory_import_start`（一次一个后台导入）、
    `memory_migrate_namespace`、`memory_storage_paths`。
  - 辅助：`embed/embed_query/embed_documents`、`warmup_embedding_provider`、`_index_resolver()`。

### 2.4 存储层

- **`MemoryStorage` / `ReplicaStorage`**（`storage/base.py`）：业务存储与副本的窄接口。
  `create_storage(backend, **kwargs)` 工厂；`sqlite` 或 `dual`。
- **`SQLiteStorage`**（`storage/sqlite.py`）：主库实现。WAL 模式；持有写锁；记忆 CRUD（软删除）、
  余弦检索、`index_values` 通用索引维度、版本化向量表、embedding profile 生命周期
  （`configure/reindex/activate/reencode`）、outbox（`synced`/`version`）、JSONL 导出/原子导入。
- **`NDJSONReplica`**（`storage/ndjson.py`）：灾备副本。只实现 `ReplicaStorage`：向量无关的
  durable 批追加、tombstone、原子快照替换、compact/stats、恢复读取。**不存向量**，也不存
  `synced`/`version`。
- **`DualStorage`**（`storage/dual.py`）：组合主库 + 副本。写只落主库；后台线程增量同步 outbox
  （先 `append_batch` 并 `fsync`，再按 `(id, version)` 条件确认），并负责 compact 与精确健康核验。
- **`embedding/embedding_codec.py`**：`RMEV` 版本化 dense-vector BLOB 编解码（默认 float16，兼容 float32
  与历史 pickle 只读）。

### 2.5 检索与向量

- **`Embedder`**（`embedding/embed.py` Protocol）+ `EmbeddingSpec`：`embed_document` / `embed_query` / `embed_documents`
  / `warmup`。实现：`OllamaEmbedder`（默认）、`FastEmbedEmbedder`、`SentenceTransformersEmbedder`。
  `create_embedder(provider, environment=...)` 工厂。document/query 走不同前缀路径。
- **`IndexValueResolver`**（`embedding/index_value_resolver.py`）：精确优先的索引值解析。未命中的值用 query 向量对同维度、同命名空间的
  文档向量打分，按绝对下限 `MIN_SCORE` 与最大间隔 `MAX_GAP` 自适应取 Top-K；解析结果只用于
  候选过滤，不改变记忆文本余弦分。
- **`domain/categories.py`**：20 个种子分类词表、归一化与上限（每记忆 ≤3）。
- **`domain/namespace_policy.py`**：命名空间格式/保留字/存在性规则与异常类型。
- **`domain/summary_policy.py`**：无模型摘要回退（调用方摘要优先；缺失时取首句/首行；空白等价更新保留旧摘要）。

### 2.6 审计与运维

- **`AuditJournal`**（`audit.py`）：线程安全滚动 JSONL（`data/audit.jsonl` + `.1…`），默认 10MB/10 文件；
  记录工具名、状态、耗时、namespace、`search_id`/`source_search_id`/`keyword_query_id`、
  `confirm_codes`、`response_code`、裁剪后的 params/result、`context`、`memory_refs`。
  两个 ContextVar：`request_audit_context`（admin/SSE admission）与
  `http_transport_context`（Streamable HTTP 每请求）。
- **`storage/jsonl_transfer.py` / `storage/recovery.py` / `cli/recover_cli.py`**：严格 JSONL 校验、干净快照写出、
  离线 JSONL→SQLite 重建（成功后原子替换目标库）。
- **`cli/embedding_cli.py`**：`status`（只读）/ `migrate-codec` / `download-model` / `reindex` /
  `reindex-index-values` / `activate`。

## 3. 主要 MCP 工具读写流程

### 3.1 写：`add`

```
handle_call_tool("add")
  → _audit_context()（含 transport/http_request_id）
  → MemoryService.memory_add(text, metadata, namespace, summary, confirm_codes)
      check_namespace()                    # 未知即拒，除非 confirm_codes
      _normalize_summary()                 # 调用方摘要优先
      embed(text)                          # document 向量
      _apply_keywords_to_metadata()        # 归一化 metadata.keywords
      _apply_categories_to_metadata()      # 归一化 + 上限 3
      IndexValueResolver.prepare_embeddings(metadata)  # 新索引值的文档向量
      storage.add(...)                     # DualStorage → SQLiteStorage.add
          dedup: (namespace, text) 已存在 → 返回既有 id, created=false
          INSERT memories(synced=0, version=0, summary=…)
          写 active profile 向量 memory_embeddings
          _sync_metadata_indexes()         # index_values + memory_index_values
          _store_index_value_embeddings()
          commit
  → _ok(data, hint) 附加 _TOOL_HINTS["add"]
  → audit.record("add", …)
后台：DualStorage 增量同步把该行（synced=0）追加到 NDJSON 副本
```

### 3.2 写：`update`

`memory_update` 语义：`text` 省略则保留原文与旧向量；`metadata` 浅合并（新键覆盖旧键）；
`namespace` 省略表示不改，显式值须已存在或确认。`summary` 经 `summary_for_update`
处理（文本未变/空白等价 → 保留旧摘要）。写入同样重建索引值与索引值向量。

### 3.3 写：`delete` / `clear`

软删除：`deleted=1, synced=0, version=version+1`，并移除 `memory_index_values`；副本通过
tombstone 事件删除对应行。`clear` 无 namespace 清全部，有则只清该命名空间。

### 3.4 读：`search_semantic`

```
handle_call_tool("search_semantic")
  → MemoryService.memory_search(query, k, threshold, namespace, categories, keywords, return_resolution=True)
      check_namespace()                    # 未知 → 空结果信封 namespace_not_found
      归一化 keywords/categories（各 ≤20）
      IndexValueResolver.resolve(filters, namespace)
          精确命中直接用；未命中 → 语义解析（同维度/同命名空间，Top-K + 下限 + 间隔）
      若某维度提供了值却完全无法解析 → 返回空结果
      embed_query(query)                   # query 向量
      storage.search(query_emb, k, threshold, namespace, resolved_filters)
          先按 namespace + 索引值维度过滤候选（维度内 OR，维度间 AND）
          再逐条 cosine 相似度打分，threshold 过滤，降序取 k
  → _wrap_search_semantic() 附加 search_id / note / total / items / filter_resolution
  → audit.record("search_semantic", …)   # 记录 search_id 供 report 关联
```

### 3.5 读：`search_meta`

精确过滤（不走语义解析）：`namespace` + `updated_after/before` + `keywords`(OR) +
`categories`(OR)，按 `created_at`/`updated_at` 降序分页。响应经 `_wrap_search_meta()`
附加 `search_id` 与 note。

### 3.6 读：`get_details` / `stats` / `list_keywords`

- `get_details`：按 id 列表取完整记录，部分成功（返回 `found` 与 `missing`）。
- `stats`：总记录数、`namespace_counts`、`keyword_total`、`category_counts`（含种子词表，未用计 0）、
  `embed_provider/model/dimension/profile/dtype`、`server_time`；dual 下附副本
  `pending/in_sync/missing/extra/corrupt_lines`。
- `list_keywords`：browse（分页按次数）/ literal（子串）/ semantic（复用关键词向量，1–20 概念，
  返回 score+count，不做精确优先抑制，不产生 search_id）。

### 3.7 写：`report`

`memory_report(search_id, ratings)`：纯记录，**不做任何存储写**；评分在 MCP 工具边界由
`handle_call_tool → audit.record` 落入审计，用于后续检索质量分析。`search_id` 必填非空，
评分宽松归一（越界钳制到 -10..10，非法项跳过），返回 `{recorded, count}`。来源/归属校验属 backlog [26]。

### 3.8 资源

- `memories://stats` → `memory_stats()`
- `memories://list` → `_prepare_for_llm(memory_list(limit=1000))`

### 3.9 管理 HTTP（与 MCP 共用业务层）

`GET/POST/DELETE /api/memories*`、`POST /api/memories/search`、`POST /api/keywords/query`、
`GET /api/stats`、`POST /api/sync`、`POST /api/export/{full,namespace}`、
`POST /api/import` + `GET /api/import/{id}`、`POST /api/namespaces/migrate`、
`POST /api/shutdown`、`POST /api/restart`、`GET /api/audit`。

## 4. 数据结构

### 4.1 SQLite 主库表

```
memories(
  id TEXT PK, text TEXT, embedding BLOB, metadata TEXT DEFAULT '{}',
  namespace TEXT, created_at TEXT, updated_at TEXT,
  deleted INTEGER DEFAULT 0,        -- 软删除
  synced INTEGER DEFAULT 0,         -- outbox 未同步标记
  version INTEGER DEFAULT 0,        -- 单调版本，仅用于条件确认 outbox
  summary TEXT
)
  idx: namespace, created_at, updated_at, (synced, updated_at)

storage_state(key TEXT PK, value TEXT)      -- 后台维护作业内部状态，不进副本

index_values(id INTEGER PK AUTOINCREMENT, dimension TEXT, value TEXT, UNIQUE(dimension,value))
memory_index_values(memory_id TEXT, index_value_id INTEGER, PK(memory_id,index_value_id))
  -- 维度: keywords / categories；权威值仍在 memories.metadata，可重建

embedding_profiles(
  profile_id TEXT PK, provider, model, dimension, normalized, query_prefix,
  document_prefix, codec, dtype, status, created_at, updated_at, error
)
  UNIQUE(status) WHERE status='active'       -- 同一时刻仅一个 active profile

memory_embeddings(memory_id, profile_id, embedding BLOB, PK(memory_id,profile_id))
index_value_embeddings(index_value_id, profile_id, embedding BLOB, PK(index_value_id,profile_id))
```

> `memories.embedding` 仅用于历史/无 profile 场景；有 active profile 时向量存
> `memory_embeddings`，检索 JOIN 该表。

### 4.2 记忆记录（业务视图）

```json
{
  "id": "…", "text": "…", "summary": "…",
  "metadata": {"keywords": ["…"], "categories": ["…"]},
  "namespace": "remember-me",
  "created_at": "ISO8601", "updated_at": "ISO8601",
  "score": 0.87              // 仅检索结果
}
```
`metadata.keywords` 为内容概念；`metadata.categories` 为记忆性质/分类（≤3）。

### 4.3 NDJSON 灾备副本记录

每行一个 JSON 对象；**不含向量**，也不含 `synced`/`version`：

```json
{"id":"…","text":"…","metadata":{…},"namespace":"…",
 "created_at":"…","updated_at":"…","summary":null}
```
删除为 tombstone：`{"id":"…","deleted":true,"updated_at":"…"}`。
历史为 append-only（可含重复行与 tombstone），`compact()` 原子重写为每 id 一行。

### 4.4 Embedding profile 与向量编码

- profile 身份由 provider/model/dimension/前缀等派生（`EmbeddingSpec.profile_id`）。
- 新 BLOB 用 `RMEV` 版本化编码（默认 float16）；历史 pickle 只读兼容，首次启动迁移进 active profile。
- 维度不匹配在写入/检索时显式报错，不静默截断。

### 4.5 审计事件

```json
{"ts":"…","tool":"search_semantic","status":"ok","duration_ms":1.2,
 "namespace":"…","search_id":"se_…","source_search_id":null,"keyword_query_id":null,
 "confirm_codes":null,"response_code":null,"error":null,
 "params":{…},"result":{…},
 "context":{"source":"mcp","request_id":1,"session_id":"ss_…",
            "client_name":"…","client_version":"…",
            "transport":"streamable_http","http_request_id":"rq_…"},
 "memory_refs":[…]}
```
用户内容（`text`/`summary`）与向量不进审计。

### 4.6 配置（`Settings` / 环境变量）

| 变量 | 默认 | 含义 |
|---|---|---|
| `MEMORY_STORAGE` | `sqlite` | `sqlite` 或 `dual` |
| `MEMORY_STORAGE_PATH` | `data/memories.db` | 主库路径 |
| `MEMORY_REPLICA_PATH` | 派生 | 副本路径（dual） |
| `EMBED_PROVIDER` / `EMBED_MODEL` / `EMBED_DIM` / `EMBED_DTYPE` | `ollama`/`bge-m3`/`1024`/`float16` | 向量 provider 与编码 |
| `EMBED_PRELOAD` | `true` | 启动预热 |
| `MEMORY_DEFAULT_NAMESPACE` | `default` | 默认命名空间（始终视为已知） |
| `MEMORY_INDEX_SEMANTIC_{TOP_K,MIN_SCORE,MAX_GAP}` | `3/0.65/0.05` | 索引值语义解析参数 |
| `MEMORY_SYNC_INTERVAL` / `MEMORY_COMPACT_*` / `MEMORY_HEALTH_CHECK_INTERVAL` | `300` / `86400,1MiB,0.25` / `86400` | dual 后台周期 |
| `MEMORY_IMPORT_MAX_{BYTES,RECORDS}` | `64MiB/10000` | 导入上限 |
| `MEMORY_AUDIT_{PATH,MAX_BYTES,MAX_FILES,MAX_EVENTS}` | `data/audit.jsonl,10MiB,10,0` | 审计滚动 |

## 5. 一致性与并发

- **单写者**：主库 `flock` 保证任一时刻一个写进程；只读诊断不受限。
- **outbox**：写只改主库并置 `synced=0`、`version+1`；后台按 `(id, version)` 条件确认，
  并发新写不会被误确认。
- **副本**：`append_batch` 先 `fsync` 再确认；快照替换与 compact 均为原子替换并 `fsync` 目录。
- **健康核验**：启动先同步 outbox 再做精确 SQLite/JSONL 比对；周期检查发现漂移/错误只记录
  stats/audit，不自动修复（需人工 Full Sync）。
- **导入**：一次一个后台作业；`BEGIN IMMEDIATE` 内校验 active profile 未变，冲突 id 重命名、
  `(namespace,text)` 重复跳过，成功后原子可见。
- **关闭/重启**：拒绝新工作 → 结束旧 SSE 会话 → Uvicorn 排空已接收 HTTP → manager 清理 →
  释放存储/provider/锁 → （重启）`execv`。
