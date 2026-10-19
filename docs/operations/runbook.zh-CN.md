# Remember Me 运维手册

[English](runbook.md) · [简体中文](runbook.zh-CN.md)

本手册用于维护已安装的 Remember Me 服务。安装步骤见 [README](../../README.zh-CN.md)。命令使用 Bash 风格的 Shell，从仓库根目录执行；请将示例路径和 Profile ID 替换为实际值。

## 目录

1. [启动、接入与关闭](#启动接入与关闭)
2. [配置与数据目录](#配置与数据目录)
3. [模型准备与代理](#模型准备与代理)
4. [检查、备份与标准维护](#检查备份与标准维护)
5. [Embedding 维护](#embedding-维护)
6. [数据迁移与恢复](#数据迁移与恢复)
7. [离线元数据回填](#离线元数据回填)
8. [常见故障](#常见故障)
9. [附录：代码结构与兼容入口](#附录代码结构与兼容入口)

## 启动、接入与关闭

### 默认启动

```bash
bash bin/start.sh
```

启动脚本加载仓库中的本机 `.env`（如存在），激活 `.venv`，并将 `MEMORY_STORAGE` 默认设为 `dual`。HTTP 默认监听 `127.0.0.1`，使用已保存的端口，初始值为 `1314`。

| 用途 | 默认地址 |
|---|---|
| 配置、记忆、统计与帮助 | `http://127.0.0.1:1314/admin/console` |
| 面向 Agent 的接入说明 | `http://127.0.0.1:1314/read-me` |
| MCP Streamable HTTP | `http://127.0.0.1:1314/mcp` |
| 兼容 MCP SSE | `http://127.0.0.1:1314/sse` |

### 带参启动

```bash
bash bin/start.sh --port 8080
bash bin/start.sh --data-dir /path/to/remember-me-data
bash bin/start.sh --port 8080 --data-dir /path/to/remember-me-data
bash bin/start.sh --stdio
```

`--port` 仅覆盖本次启动的端口，不修改配置文件；浏览器和 MCP 地址也需使用对应端口。`--stdio` 显式选择子进程传输，不能与 `--port` 同时使用。选择其他数据目录不会自动搬迁已有数据。

### MCP 客户端

HTTP 的两种传输共用一个运行时。`/read-me` 是只读接入指南，不是 MCP 协议端点。Web UI 的**帮助**页面提供客户端配置说明和记忆提示词。

项目为 Codex 提供的 Streamable HTTP 配置示例：

```toml
[mcp_servers.memory-server]
url = "http://127.0.0.1:1314/mcp"
```

将其放入客户端配置，通常为 `~/.codex/config.toml`。若同一服务已有 `command`/`args` 桥接条目，应替换该条目，避免重复注册。OpenCode 可将以下示例合并到其配置中：

```json
{
  "mcp": {
    "memory-server": {
      "type": "remote",
      "url": "http://127.0.0.1:1314/sse",
      "enabled": true
    }
  }
}
```

若客户端版本的配置格式不同，以该版本的说明为准。远程客户端不能直接使用你电脑上的 loopback 地址。

### 重启与关闭

在**配置**页面先点击**进入维护**。普通维护暂停记忆写入，查询继续；模型准备和数据库切换有额外限制。没有维护动作正在执行时，可点击**重启**或**关闭**。关闭会等待运行中的受管任务及资源清理；执行离线写入前须等待进程退出。关闭后从终端启动，继续使用原数据目录及原有端口覆盖参数。

## 配置与数据目录

### 选择数据目录

| 操作系统 | 默认目录 |
|---|---|
| Linux | `~/.remember-me/` |
| Windows | `%LOCALAPPDATA%\remember-me\`；未设置时使用 `%USERPROFILE%\AppData\Local\remember-me` |
| macOS | `~/Library/Application Support/remember-me/` |

优先级为 `--data-dir` > `MEMORY_DATA_DIR` > 系统默认目录。相对数据目录以仓库根目录为基准。所选目录包含 `config.jsonc`、数据库、副本、审计文件、日志、临时文件、回填产物和模型缓存。

默认主库和副本分别为 `memories.db`、`memories.jsonl`。Sentence Transformers 默认缓存为 `models/sentence-transformers/`，历史 FastEmbed 缓存为 `models/fastembed/`。运行时路径覆盖项和 CLI 输出路径在数据目录内解析，越界路径（包括通过符号链接越界）会被拒绝。仓库资源、`.env`、外部只读导入源及独立 Ollama 服务的存储不受此规则约束。

### 配置字段

正常启动时，若配置不存在，将创建以下配置：

```jsonc
{
  "port": 1314,
  "language": "zh-CN",
  "auto_download_model": true,
  "local_only": true,
  "debug": false,
  "proxy": {"enabled": false, "url": ""}
}
```

| 字段 | 含义 | 在 UI 保存后的生效时间 |
|---|---|---|
| `port` | HTTP 端口，1–65535 | 重启；显式 `--port` 仍优先 |
| `language` | `zh-CN` 或 `en` 界面 | 刷新页面 |
| `auto_download_model` | 自动下载缺失的模型文件 | 下次模型准备或启动；显式重试可请求下载 |
| `local_only` | 监听 loopback 或全部网络接口 | 重启 |
| `debug` | Debug 按钮、Audit 导航和诊断入口 | 刷新页面 |
| `proxy` | 模型下载的 HTTP/HTTPS 代理 | 下次模型准备或重试 |

保存普通设置无需进入维护。直接编辑配置文件后，通过重启重新加载。Debug 不改变日志或 MCP 工具；元素检查还需点击 Debug 按钮。没有 `--debug` 启动参数，stdio 也没有 UI。

JSONC 支持单行／块注释和尾随逗号，UI 保存会保留注释。仅当 `config.jsonc` 不存在时，正常启动才采用旧 `config.json`，并保留旧文件。两者同时存在时以 JSONC 为准，JSONC 无效不会退回 JSON。帮助和只读配置检查不会迁移文件。CLI 的 `--port 0` 可申请随机可用端口，与配置文件的取值范围不同。

### 网络访问

`local_only: true` 监听 `127.0.0.1`。设为 `false` 后监听 `0.0.0.0`，所有 HTTP 端点（包括管理功能）都可被网络可达的客户端访问。服务没有身份认证，仅应在具有适当访问控制的可信网络中使用。Namespace 不是权限边界。

仅本机模式下，`/mcp` 限制 Host/Origin 为 loopback：非法 Host 返回 421，非法 Origin 返回 403。通过安全检查后，非 POST 方法返回 405 和 `Allow: POST`。局域网模式关闭此 Host/Origin 保护。即使在局域网模式下，`/read-me` 的示例仍使用 loopback 地址及实际端口。

## 模型准备与代理

### 默认模型与已有 Profile

新安装默认使用 **Sentence Transformers + BAAI/bge-m3 + CPU**，基础依赖已包含相应运行库。未设置 `EMBED_PROVIDER` 时，已有数据库沿用其活动 Embedding Profile。启动会拒绝冲突的模型身份；已有记忆却无法识别活动 Profile 时，需要显式指定原 Provider 和模型参数。

历史 Ollama Profile 继续兼容，但仍需独立运行的 Ollama 服务及匹配模型；Remember Me 不负责安装或启动它。必要时在该服务所在主机执行 `ollama pull MODEL`。FastEmbed 保留为可选历史后端，通过 `requirements-fastembed.txt` 安装，不作为新安装推荐路径。

### 首次下载与重试

本地 Provider 优先读取缓存。HTTP 模式遇到可识别的缓存缺失时，会开放配置 UI 并强制进入维护状态（`model_not_ready`）。允许自动下载时启动准备任务，否则可显式点击**重试**。准备期间，记忆读写、搜索和退出维护均被阻止。下载、加载、预热及数据库校验成功后，手动点击**退出维护**恢复写入。

后台准备失败会保留配置 UI。缺少依赖、Provider 配置无效、已有缓存加载失败或其他启动错误，可能导致 HTTP 服务完全无法启动。显式本地模型路径缺失时应恢复目录，不会将其当作可下载的仓库 ID。stdio 和需要模型的 CLI 命令同步准备模型，失败退出；只读状态、帮助、codec 转换和 Profile 激活命令不加载模型。

### 代理

在**配置**页面设置下载代理，保存后重试：

```json
{"proxy": {"enabled": true, "url": "http://127.0.0.1:7890"}}
```

仅支持 HTTP/HTTPS 代理。关闭代理意味着直连，并忽略继承的 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`（含小写变量）。loopback Ollama 请求绕过代理，独立 Ollama 服务的下载配置由它自己管理。代理 URL 可包含凭据，`config status` 会隐藏凭据。

重试使用已保存的配置，无需重启服务。准备过程中再次请求重试，会先取消旧任务，再启动新任务。进度展示依赖库报告的真实字节数，总量不可知时显示不确定状态。配置入口覆盖历史 Provider／Hugging Face offline 变量，采用缓存优先加载，并禁用独立 Xet 下载通道。

这些设置不用于 pip 安装联网。即使自动下载关闭，也可在按下文选定数据目录后，执行 `bin/manage --data-dir "$RM_DATA_DIR" embedding download-model`，显式下载缺失模型。

## 检查、备份与标准维护

### 始终使用同一数据目录

将以下 Shell 变量设为服务实际使用的目录，不要直接使用一个新的示例目录：

```bash
RM_DATA_DIR="/absolute/path/to/your/current/remember-me-data"
bin/manage --data-dir "$RM_DATA_DIR" config status
bin/manage --data-dir "$RM_DATA_DIR" status
bin/manage --data-dir "$RM_DATA_DIR" embedding status
```

`bin/manage` 会加载 `.env` 并激活 `.venv`。`.env` 中的赋值可能覆盖写在命令前的环境变量。执行模型变更前，应先统一其中的 Provider／模型设置；CLI 的 `--data-dir` 仍然优先。后续示例继续使用同一个 `RM_DATA_DIR`。帮助入口为 `bin/manage --help` 和 `bin/manage embedding --help`。

| 操作 | 服务状态／影响 |
|---|---|
| status、config status、help | 服务运行时可执行，不加载模型 |
| 模型下载 | 写入模型缓存，不修改记忆记录；避免与正在进行的准备任务冲突 |
| UI 导出、同步、导入、恢复、清空、Namespace 迁移 | 进入维护；其他前提满足后 UI 才启用操作 |
| 离线重建、codec 转换、激活、恢复、应用回填、数据目录迁移 | 停服并等待进程退出 |

离线写入进程会获取主库 writer lock，不会替你终止已有服务。不要删除 `.writer.lock` 绕过它：锁文件存在不代表进程当前持有操作系统锁。

### 备份与健康检查

dual 存储的**完整导出（Full Export）**会先同步并校验 JSONL 副本，再下载快照。维护前应将下载结果独立保存。**同步（Sync）**刷新的是当前副本，后续变化仍会同步到它，因此不等于历史备份。Namespace 导出只是部分快照，不是完整灾备。JSONL 包含记忆记录及元数据，不包含向量，恢复需要模型推理。

SQLite-only 存储不支持依赖副本的 Full Export／Sync。独立备份数据库前先正常关闭服务，并保留相关配置和 Provider／模型设置。备份包含私人记忆，应妥善保存。

dual 模式下检查统计中的诊断信息：`pending=0`、`in_sync=true`、`missing=0`、`extra=0`、`corrupt_lines=0`。仅待同步数量归零不能证明副本健康。Debug 会开放诊断入口。SQLite-only 没有需要核验的副本。

### 标准步骤

1. 确认服务的数据目录和启动覆盖参数，在配置页面进入维护。
2. dual 模式下载 Full Export，并独立保存为备份。
3. 点击关闭并等待进程退出。SQLite-only 此时先独立备份数据库，再执行任何离线写入。
4. 使用上面的状态命令核对目标目录，再执行所需操作。
5. 通过 `bash bin/start.sh --data-dir "$RM_DATA_DIR"` 启动，并保留原有 `--port` 覆盖参数。
6. 检查活动 Profile、记忆可用性及适用的 dual 副本健康状态。若启动进入模型准备维护状态，准备成功后手动退出维护。

## Embedding 维护

### 查看与准备

```bash
bin/manage --data-dir "$RM_DATA_DIR" embedding status
bin/manage --data-dir "$RM_DATA_DIR" embedding download-model
```

未显式指定 Provider 时，模型准备沿用已有活动 Profile。为 `download-model` 显式指定 Provider 时，则准备所指定的目标模型。选择迁移目标或回滚 Profile ID 前，先查看状态输出。

### 切换 Provider 或模型

先备份并停服。在仓库的私有 `.env` 中设置目标，更新已有赋值，避免追加相互冲突的内容。推荐目标示例：

```bash
EMBED_PROVIDER=sentence_transformers
SENTENCE_TRANSFORMERS_MODEL=BAAI/bge-m3
SENTENCE_TRANSFORMERS_DEVICE=cpu
```

同时检查已有 revision 和查询／文档前缀覆盖项。然后执行：

```bash
bin/manage --data-dir "$RM_DATA_DIR" embedding download-model
bin/manage --data-dir "$RM_DATA_DIR" embedding reindex
bin/manage --data-dir "$RM_DATA_DIR" embedding status
```

reindex 为目标重建记忆正文和关键词／分类向量，完成后原子激活 Profile。同一 Provider 内切换模型或 revision 也使用此流程。未显式指定目标 Provider 时沿用活动 Profile，通常返回 `already_active`。重启前确保启动配置与新 Profile 一致。

Profile 身份包括 Provider、模型（及编码在模型标识中的 revision）、维度、归一化方式和查询／文档前缀。设备和 codec dtype 不属于此身份。

### 修复索引向量与回滚

以下操作在服务关闭后执行：

```bash
bin/manage --data-dir "$RM_DATA_DIR" embedding reindex-index-values
bin/manage --data-dir "$RM_DATA_DIR" embedding reindex-index-values --profile-id PROFILE_ID
bin/manage --data-dir "$RM_DATA_DIR" embedding activate PROFILE_ID
```

第一条修复活动 Profile 的关键词／分类向量；`--profile-id` 则选择保留的历史 Profile。应移除或调整与目标冲突的显式 Provider／模型设置。激活要求记忆向量和索引向量均完整，不会通过推理补齐缺失项。回滚后启动前，需让 `.env` 匹配已激活模型，并确保对应模型或服务可用。后续记忆变更可能导致历史 Profile 不再完整。

### 修改向量编码

```bash
bin/manage --data-dir "$RM_DATA_DIR" embedding migrate-codec --dtype float32
```

先关闭服务。此操作改变向量编码，不切换 Embedding 模型。要在后续启动时保留 float32，还应在 `.env` 设置 `EMBED_DTYPE=float32`；切回时使用对应的 `float16` 配置。启动会按配置重新编码不匹配的向量，所以只运行一次转换命令而不调整启动配置，转换结果可能被改回。通过 embedding status 核对实际 codec／dtype。

## 数据迁移与恢复

### 迁移数据目录

仅选择 `--data-dir` 不会迁移文件。明确指定源目录和目标目录：

```bash
RM_NEW_DATA_DIR="/absolute/path/to/new/remember-me-data"
bin/manage --data-dir "$RM_NEW_DATA_DIR" config migrate --source-data-dir "$RM_DATA_DIR" --dry-run
# 关闭服务并等待进程退出，再执行下一条命令。
bin/manage --data-dir "$RM_NEW_DATA_DIR" config migrate --source-data-dir "$RM_DATA_DIR"
bash bin/start.sh --data-dir "$RM_NEW_DATA_DIR"
```

迁移仓库历史 `data/` 时使用 `--source-data-dir data`。迁移会复制并校验文件、保留来源，并更新配置和 `.env` 路径；拒绝冲突目标和使用中的数据库。执行前审核预览，在新目录确认模型加载和存储健康后，再处理旧文件。需要时保留原端口覆盖参数，后续维护统一使用新目录。

若迁移后希望采用系统默认目录，应从 `.env` 移除过时的 `MEMORY_DATA_DIR` 赋值；自定义目录则继续显式配置。崩溃遗留的迁移日志可能阻止启动，应重新运行相同迁移命令恢复，检查结果后再决定重试。不要手动删除迁移日志或共享缓存绕过恢复。

### 在线 JSONL 恢复

在配置页面进入维护，上传可信的完整 JSONL 备份并选择**恢复（Recover）**。重建期间查询继续，最终切换数据库时会短暂拒绝查询并提示重试。持续观察任务直到完成。dual 模式还会执行 Full Sync；失败时保持维护，重试 Sync 成功后再退出。导入通过导入流程添加记录，而恢复重建数据库，不能将恢复当作追加导入使用。

### 离线 JSONL 恢复

先停服，并独立保留源文件和已有目标的备份。恢复先构建临时数据库，成功后才替换目标：

```bash
# 从配置的 JSONL 副本重建配置的主库。
bin/manage --data-dir "$RM_DATA_DIR" recover

# 在选定数据目录内重建另一个目标文件。
bin/manage --data-dir "$RM_DATA_DIR" recover /path/to/backup/memories.jsonl recovered.db
```

显式指定 Provider 时，恢复采用该目标配置，不探测旧数据库。否则，可读目标沿用活动 Profile，不存在的目标采用产品默认值。目标损坏、不可读，或有数据却无法识别 Profile 时，要求显式指定 Provider／模型。独立的 `recovered.db` 不会自动成为后续服务使用的主库；需要将它投入使用时，应明确调整主库路径配置。

重启前查看恢复后的 Profile，并匹配启动设置。核对记忆记录及适用的 dual 副本健康状态；同步需要修复时保持维护。

## 离线元数据回填

关键词／分类回填使用外部 `opencode` CLI 和配置的 DeepSeek 模型。实际生成会将选中记忆内容发送至该外部流程，只应对允许这样处理的数据使用。dry-run 统计符合条件的记录，不生成更新：

```bash
bin/manage --data-dir "$RM_DATA_DIR" migrate keywords --dry-run --limit 20
bin/manage --data-dir "$RM_DATA_DIR" migrate categories --dry-run --limit 20
```

核对范围、备份并停服后，再去掉 `--dry-run`。可用 `--namespace`、`--memory-id`、`--limit` 或 `--db` 缩小范围。重试前查看逐条失败记录。

Anchor 回填分为模型生成草稿和应用已审核清单。由于命令具有项目专用的默认 Namespace，应显式指定目标 Namespace：

```bash
bin/manage --data-dir "$RM_DATA_DIR" migrate anchors draft --namespace PROJECT --limit 20 --dry-run
bin/manage --data-dir "$RM_DATA_DIR" migrate anchors draft --namespace PROJECT --limit 20 --manifest anchor-backfill/review.jsonl
# 审核生成的清单，应用前关闭服务。
bin/manage --data-dir "$RM_DATA_DIR" migrate anchors apply --manifest "$RM_DATA_DIR/anchor-backfill/review.jsonl" --dry-run
bin/manage --data-dir "$RM_DATA_DIR" migrate anchors apply --manifest "$RM_DATA_DIR/anchor-backfill/review.jsonl"
```

draft 不修改数据库，但未加 `--dry-run` 时会调用外部模型并写入清单。apply 使用已审核清单，以事务提交整批修改。具体参数见各命令的 `--help`；三种回填命令并不共享全部参数。

## 常见故障

| 现象 | 检查与处理 |
|---|---|
| Web UI 无法打开 | 检查进程、实际端口和数据目录，查看启动错误和日志；缺失依赖或缓存模型初始化失败可能阻止 HTTP 启动。 |
| 模型未就绪／后台下载失败 | 在配置页查看模型错误，保存正确代理后重试；准备成功后退出维护。 |
| Profile 不匹配 | 对照 embedding status 与 `.env`；恢复匹配配置，或停服后显式重建到目标模型。 |
| Ollama 连接／模型错误 | 启动独立安装的匹配 Ollama 服务并准备模型，或执行显式离线迁移。 |
| Writer lock 被占用 | 找到使用该主库／副本的进程并正常停止，不要删除锁文件。 |
| 副本不一致／恢复后同步失败 | 保留独立备份，检查诊断信息，在维护状态运行 Sync；退出前确认健康状态。 |
| 无法识别损坏的恢复目标 | 显式设置目标 Provider／模型，再从可信 JSONL 备份执行离线恢复。 |
| 遗留数据迁移日志 | 重跑相同迁移命令进行恢复，启动前检查最终路径。 |
| 重启后似乎丢失数据 | 创建新记忆或尝试恢复前，先核对 `--data-dir`、`MEMORY_DATA_DIR`、`.env` 和主库路径。 |

## 附录：代码结构与兼容入口

| 位置 | 职责 |
|---|---|
| `app/config/` | Settings、JSONC、数据根目录解析 |
| `app/domain/` | 纯校验和检索规则 |
| `app/storage/` | SQLite／dual 契约、副本、锁、JSONL 和恢复 |
| `app/embedding/` | Provider、Profile 解析、向量 codec 和索引值解析 |
| `app/transport/` | MCP 工具和 Admin HTTP |
| `app/operations/` | 线程池、维护、导入和模型准备 |
| `app/cli/` | 离线管理和回填 |
| `app/server.py`、`runtime.py`、`service.py` | 传输启动、资源生命周期和业务逻辑 |

`app/audit.py`、`app/log_config.py` 负责审计与日志。Domain 不依赖服务／传输层，在线业务不导入 CLI，包导入不获取运行时资源。

`bin/start.sh`、`bin/manage`、`bin/recover.sh` 和根目录 `python -m app.*` 命令入口继续兼容。CLI 实现位于 `app.cli`，旧内部导入路径不属于兼容 API。上述操作优先使用 `bin/manage`；直接调用模块时，参数顺序可能不同，也不会自动加载仓库 `.env`。
