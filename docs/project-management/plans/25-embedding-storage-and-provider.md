# [25] Embedding 存储与 Provider 改造计划

状态：`implemented`（生产默认仍为 Ollama；切换 FastEmbed 前需完成业务查询集 A/B）  
原始设计记忆：`53c3797fa42e9cc3`  
关联任务：[24] keywords/categories 向量化与语义查询

## 目标

1. 用版本化二进制格式替代 pickle + Python float，默认使用 float16，在语义检索质量基本不变的前提下降低存储占用。
2. 抽象 embedding provider，保留 Ollama，并增加 Python 本地 ONNX/FastEmbed 实现。
3. 支持安全、可回滚的全量 reindex，禁止不同模型或不同向量空间静默混用。
4. 对超出模型上下文的文本给出明确、可行动的错误，引导调用方保存项目文档并在记忆中保留短引用。

## 当前基线

- 生产库：883 条有效 embedding，均为 1024 维。
- 当前编码：pickle 序列化的 `list[float]`，每条 9,234 B，合计约 7.78 MiB。
- float32 原始数据：每条 4,096 B，合计约 3.45 MiB。
- float16 原始数据：每条 2,048 B，合计约 1.72 MiB，较当前减少约 78%。
- float16 精度抽查：883 条向量自身余弦一致性最低约 0.99999997；100 条样本的 Top-10 集合全部一致，最大分数差约 6.2e-5。
- 上述结果只证明编码精度稳定，不替代真实查询集上的语义质量评估。

## 已确认决策

- 分离“向量生成”和“向量编码”两个概念，provider 不负责数据库序列化。
- 新写入向量默认使用 float16；读取期间兼容旧 pickle。
- 第一阶段不做 int8、PQ 或降维；这些方案只有在 float16 仍不能满足规模目标时再评估。
- FastEmbed 先作为可选 provider，不能未经基准验证直接替换生产默认 provider。
- reindex 先提供 CLI/管理入口，不注册为 MCP 工具。
- reindex 使用影子数据，全部成功并校验后才原子切换 active profile。
- 不静默截断超长记忆正文。

## 目标结构

### Provider 接口

provider 至少暴露：

- `embed_document(text)`：写入记忆时使用。
- `embed_query(text)`：查询时使用，允许 E5 等模型应用不同前缀。
- `embed_documents(texts)`：reindex 批处理。
- `spec`：返回 provider、model、dimension、normalize、max input 等信息。
- `warmup()`：服务启动时预加载；是否强制预加载由配置控制。

首批实现：

- `OllamaEmbedder`：保持现有 `bge-m3` 行为兼容。
- `FastEmbedEmbedder`：本地 ONNX 推理；模型必须来自运行时实际支持列表或显式配置的自定义模型。

### Embedding profile

新增 profile，用于标识一个可比较的向量空间：

```text
embedding_profiles
  id
  provider
  model
  dimension
  normalized
  codec
  dtype
  created_at
  status          # building / active / retired / failed
```

模型、维度或 query/document 处理规则不同，就必须使用不同 profile。仅 dtype/codec 改变但数值空间不变时，可以执行编码迁移而不重新推理。

### 向量表

第一阶段新增 `memory_embeddings(memory_id, profile_id, embedding)`，与 `memories` 主数据分离。后续 [24] 可增加 `index_value_embeddings(index_value_id, profile_id, embedding)`，共用 profile 和 codec。

### BLOB codec

新格式需要包含：

- magic 与格式版本；
- dtype；
- dimension；
- flags，例如 normalized；
- 连续 little-endian 数组数据。

解码顺序：识别新格式；不匹配时按 legacy pickle 读取。禁止对未知版本猜测解析。

## 实施阶段

### S0 — 基线与测试夹具

- 固化一组中英文真实查询、期望相关记忆及当前 Top-k/score。
- 记录当前延迟、模型加载时间、内存、数据库大小和 embedding 吞吐。
- 增加 codec、维度不匹配、profile 不匹配和长文本错误测试夹具。

完成标准：基线数据可重复生成，并能用于后续 provider A/B。

### S1 — 版本化 codec 与 float16

- 新增独立 codec 模块，支持 float16/float32 编解码及 legacy pickle 读取。
- SQLite 新写入使用 float16；查询统一解码为 numpy 数组参与 cosine 计算。
- 提供只改变编码、不重新调用模型的迁移命令。
- 迁移采用事务或临时表，失败时保留原 BLOB。
- stats 增加 embedding 数量、维度、codec/dtype 和估算存储占用。

完成标准：全量测试通过；真实数据迁移后 Top-k 与基线满足门槛；旧库可直接启动和读取。

### S2 — Provider 抽象

- 将当前单函数 `embed()` 改为 provider factory 和生命周期对象。
- 新增配置：`EMBED_PROVIDER`、provider 对应模型和缓存目录、预加载开关。
- 服务启动校验配置模型的维度和 profile，不匹配时拒绝进入可写/可搜索状态。
- 将 add/update 与 search 分别切换到 document/query 接口。
- recovery 复用同一 provider factory。

完成标准：Ollama 模式行为和现有结果兼容；错误日志包含 provider/model/profile，而不记录正文。

### S3 — FastEmbed 可选实现

- 增加可选依赖与模型下载/缓存命令。
- 支持联网缓存、预下载和离线缓存目录。
- 启动时验证模型确实受当前 FastEmbed 版本支持。
- 选择至少一个中文/多语言候选模型，与 Ollama bge-m3 做质量和性能 A/B。
- 模型未缓存且处于离线模式时快速失败并给出操作提示。

完成标准：全新环境按文档可安装、下载、离线启动并生成稳定向量；尚未通过门槛前不修改默认 provider。

### S4 — 安全 reindex

- 新建 building profile 和影子向量，不覆盖 active profile。
- 分批生成并记录总数、成功数、失败数、耗时和当前位置。
- 校验全部有效记忆均有新向量、维度一致、无空向量，再用单事务切换 active profile。
- 保留旧 profile 供回滚；清理由独立显式命令执行。
- 第一版要求停服务运行 CLI，避免 reindex 期间新增/更新造成缺口；以后再评估在线双写。
- 管理入口只负责显示状态或触发后台任务，并写入 background-task audit。

完成标准：中途失败、进程退出或模型异常均不会影响旧 profile 查询；切换和回滚都有集成测试。

### S5 — 超长文本保护

- provider 将“输入超过上下文”转换为统一的 `EmbeddingInputTooLong`。
- add/update 返回明确提示：长文档应放在项目文档目录，记忆只保存摘要、路径和索引信息。
- 不静默截断；如未来需要 chunk embedding，另立任务设计聚合与检索语义。
- audit 只记录错误类型和长度，不记录正文。

完成标准：超长 add/update 不产生半写入；错误提示可直接指导调用方修正。

### S6 — 与 [24] 衔接

- keywords/categories 词表 embedding 复用 active profile 和 codec。
- profile 切换时，memory 与 index value 向量必须作为同一批迁移并共同激活。
- 明确词表语义召回、正文相似度和精确预过滤之间的组合策略后，再实施 [24]。

## 质量门槛

- float16：基准查询 Top-10 overlap 平均值不低于 0.99；关键查询的相关结果不得丢失。
- 新 provider/model：人工标注查询集 Recall@10 不明显低于当前 bge-m3；阈值需按新模型重新校准。
- 性能：记录冷启动、单条、批量、搜索延迟和峰值内存，不以单一吞吐数字决定切换。
- 可靠性：维度/profile 不匹配必须显式失败，不能返回空结果或跳过错误向量。

## 回滚策略

- codec 迁移前保留 legacy 读取能力；迁移失败不删除旧数据。
- provider reindex 不覆盖 active profile；切换只更新 active 标记。
- 新 profile 上线异常时原子切回旧 profile。
- 旧 profile 只有在稳定观察期后通过显式维护命令删除。
- NDJSON 继续不保存 derived embedding；离线恢复后按 active profile 重新生成。

## 配置草案

```text
EMBED_PROVIDER=ollama
EMBED_MODEL=bge-m3
EMBED_DTYPE=float16
EMBED_PRELOAD=true
FASTEMBED_CACHE_DIR=...
FASTEMBED_OFFLINE=false
```

配置最终命名在 S2 实施时统一，避免同时保留 `OLLAMA_MODEL`、`EMBED_MODEL` 等重复含义。

## 交付与提交边界

- Commit 1：测试基线、profile/codec、legacy 兼容。
- Commit 2：float16 编码迁移与 stats。
- Commit 3：provider 抽象，Ollama 兼容实现。
- Commit 4：FastEmbed、缓存/离线命令与文档。
- Commit 5：reindex、原子切换/回滚和管理状态。
- Commit 6：超长文本错误、audit 和最终生产验证。

每个 commit 独立可运行、可测试、可回滚；任何阶段不得依赖尚未切换完成的新 profile 才能启动服务。

## 实施结果（2026-09-11）

- 新增 `app/embedding_codec.py`：`RMEV` v1 自描述 BLOB，支持 float16/float32，兼容读取可信旧库中的 pickle。
- SQLite 向量已从主记录分离至 `memory_embeddings`，并用 `embedding_profiles` 管理 building/active/retired/failed 状态。
- 旧 inline embedding 首次启动时在单事务内迁移；模型/profile 不一致时拒绝启动并要求离线 reindex。
- reindex 分批写入影子 profile，完整性和维度校验通过后原子激活；失败保留旧 active profile，retired profile 可原子回滚。
- provider 已抽象为 document/query/batch/warmup 接口；Ollama 保持默认，FastEmbed 作为可选本地 ONNX provider。
- add/update/search 对超长输入给出统一错误；HTTP 返回 413，MCP audit 不保存正文。
- stats 和管理页显示 provider、模型、维度、active/configured profile、codec/dtype、向量数量及字节数。
- 离线 CLI 提供 `status`、`migrate-codec`、`download-model`、`reindex`、`activate`。

### 安装与离线操作

以下变更数据库的命令都要求先停止 remember-me 服务：

```bash
source .venv/bin/activate
pip install -r requirements-fastembed.txt

# CPU-only 主机先安装不含 CUDA 运行时的 PyTorch，再安装 Python BGE-M3 provider
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-sentence-transformers.txt

# 可选：通过本机 HTTP 代理预下载模型
HTTP_PROXY=http://localhost:7890 \
HTTPS_PROXY=http://localhost:7890 \
EMBED_PROVIDER=fastembed \
FASTEMBED_MODEL=intfloat/multilingual-e5-large \
python -m app.embedding_cli download-model

# Python 进程内运行 BAAI/bge-m3（模型缓存可放到空间更充足的磁盘）
HTTP_PROXY=http://localhost:7890 \
HTTPS_PROXY=http://localhost:7890 \
ALL_PROXY=http://localhost:7890 \
EMBED_PROVIDER=sentence_transformers \
SENTENCE_TRANSFORMERS_MODEL=BAAI/bge-m3 \
SENTENCE_TRANSFORMERS_CACHE_DIR=/path/to/model-cache \
SENTENCE_TRANSFORMERS_DEVICE=cpu \
python -m app.embedding_cli download-model

# 查看现有 profile/codec
python -m app.embedding_cli status data/memories.db

# 仅迁移编码，不重新推理
EMBED_DTYPE=float16 \
python -m app.embedding_cli migrate-codec data/memories.db --dtype float16

# 使用已经缓存的 FastEmbed 模型建立影子向量并原子切换
EMBED_PROVIDER=fastembed \
FASTEMBED_MODEL=intfloat/multilingual-e5-large \
FASTEMBED_OFFLINE=true \
python -m app.embedding_cli reindex data/memories.db --batch-size 50

# 回滚；重启服务前还要把 provider/model 环境变量改回该 profile 的配置
python -m app.embedding_cli activate data/memories.db ep_xxxxxxxxxxxxxxxx
```

所有修改数据库的 embedding CLI 命令使用数据库级非阻塞维护锁；同一数据库已有 reindex、codec 迁移或 profile 切换时，第二个命令会立即失败。reindex 每批向 stderr 输出已完成数量、总数和耗时。

### 验证记录

- 自动测试：`225 passed`；`compileall`、管理页 JavaScript 语法检查和 `git diff --check` 通过。
- 生产库副本：884 条 1024 维旧向量全部迁移成功；inline embedding 清零；活跃向量从 8,162,856 B 降至 1,821,040 B，减少约 77.7%。
- float16：既有 883 条向量抽查中，自身余弦一致性最低约 0.99999997；100 条样本 Top-10 集合全部一致，最大 score 差约 6.2e-5。
- 安全切换：生产库副本完成 884 条影子 reindex，并成功切回旧 profile；模拟中途失败时旧 profile 保持 active。
- FastEmbed 真实冒烟：经 `http://localhost:7890` 下载 `BAAI/bge-small-zh-v1.5`，随后从离线缓存加载；真实中文 query/document 均产生 512 维单位向量，相关文本得分高于无关文本，并完成小库 reindex、查询和回滚。
- 新增可选 `sentence_transformers` provider，用于在 remember-me Python 进程内直接运行 `BAAI/bge-m3`；它与 FastEmbed 分开安装，支持离线缓存、设备选择、批量推理和 8192-token 显式保护。生产切换前仍需在数据库副本完成全量 reindex 和 audit A/B。
- 混合状态恢复：发现旧进程可能在首次 profile 迁移后继续写 inline embedding；新版本启动时会将这类向量补入 active profile 并清空 legacy 列。生产副本验证从 887/890 自动恢复到 890/890。

### Jina v2 中文模型 A/B（2026-09-11）

候选：`jinaai/jina-embeddings-v2-base-zh`，FastEmbed/ONNX、768 维、float16。通过代理下载后，对生产库一致性副本的 890 条有效记忆完成全量 reindex，旧 bge-m3 profile 保持完整且可回滚。

评估集来自 audit 中真实 `search_semantic` + `report`：156 个带正向评分的事件中，155 个仍有有效标注记忆。所有查询在同一份当前语料上分别用 bge-m3 和 Jina 重新执行，并保留原 namespace/keywords/categories 条件。标签来自既有 bge-m3 候选，因此可用于衡量“已知有用结果保留率”，但不能衡量 Jina 新召回而未被人工标注的结果。

| 指标（rating >= 5） | bge-m3 | Jina v2 zh | 差值 |
|---|---:|---:|---:|
| Hit@1 | 42.6% | 32.3% | -10.3pp |
| Hit@5 | 87.7% | 81.3% | -6.4pp |
| Hit@10 | 96.1% | 89.0% | -7.1pp |
| 已知相关项 Recall@10 | 90.2% | 72.2% | -18.0pp |
| MRR@10 | 0.616 | 0.516 | -0.100 |

- `atos-next-master` 100 条：Hit@10 97.0% → 89.0%，Recall@10 90.6% → 73.6%。
- 含中文的 76 条：Hit@10 97.4% → 90.8%，Recall@10 94.1% → 73.8%。
- `remember-me` 只有 10 条：Jina Hit@10 为 100%，但 Recall@10 82.9%，bge-m3 分别为 90%/85%；样本过少，不作模型优劣结论。
- 155 条 query embedding + 当前副本检索耗时：Jina 13.57s，bge-m3 32.31s；Jina 约快 2.4 倍。Jina 全量 890 条 document reindex 约 4 分钟。

结论：Jina 的速度和资源收益成立，但真实历史查询的已知相关项召回下降明显，未通过“质量基本不下降”的生产切换门槛。保留 FastEmbed/Jina 支持与 profile，不激活到生产默认。

### Python 原生 BGE-M3 A/B（2026-09-11）

候选：`sentence_transformers` provider + `BAAI/bge-m3`，1024 维、float16，在 remember-me Python 进程内用 CPU/PyTorch 推理。模型离线缓存位于 `/tmp`，评估只修改生产 SQLite 的一致性副本，未修改线上数据库或默认 provider。

同口径使用 Jina A/B 时的 155 条 audit 正向标注查询（截止 `2026-09-11T09:02:00Z`），保留原 namespace/keywords/categories 预过滤：

| 指标（rating >= 5） | Ollama bge-m3 基线 | Python BAAI/bge-m3 | 差值 |
|---|---:|---:|---:|
| Hit@1 | 42.6% | 43.2% | +0.6pp |
| Hit@5 | 87.7% | 88.4% | +0.7pp |
| Hit@10 | 96.1% | 96.1% | 0.0pp |
| 已知相关项 Recall@10 | 90.2% | 90.0% | -0.2pp |
| MRR@10 | 0.616 | 0.622 | +0.006 |

- 真实离线冒烟：模型加载约 5.9s；3 条中文短文本批量约 0.25s；峰值 RSS 约 1.9GiB。
- 155 条 query embedding 约 6.8s，SQLite 侧 155 次过滤与余弦检索约 11.0s。
- 887 条新旧共有正文向量直接对比：同一文本向量余弦平均 0.99989、最低 0.99475；抽样 100 条的 Top-10 邻域集合平均重合率 0.99、最低 0.90，说明两个运行时生成的是高度一致但并非逐位相同的向量空间。
- 890 条文档在 CPU 上完成 reindex 约 27 分钟。中途人为并行试跑暴露唯一键冲突；补充数据库级维护锁后，同一数据库的第二个变更命令会立即失败。
- 本次环境实际模型缓存约 4.3GiB（Hub/权重文件布局可能包含重复物理文件），CPU-only Python 依赖使 `.venv` 增至约 1.6GiB。

结论：Python 原生 BGE-M3 通过当前语义质量门槛，可作为去除 Ollama embedding 依赖的候选；代价是明显高于 FastEmbed 小模型的磁盘、内存和 CPU reindex 时间。生产仍不自动切换，需显式 reindex 后再调整 provider 配置。

### 保留边界

- 不把冒烟模型直接设为生产默认。若要切换至 `intfloat/multilingual-e5-large` 或其他 FastEmbed 模型，仍需用业务标注查询集完成 Recall@10、阈值、延迟与内存 A/B。
- int8/PQ/降维未实施；float16 已达到约 78% 的存储缩减，只有规模继续增长且 float16 不足时再单独评估。
- keywords/categories 的向量化仍由 [24] 实施，但会复用本次落地的 profile 和 codec。
