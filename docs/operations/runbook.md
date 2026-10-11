# remember-me 运维手册

## 代码结构

`app/` 按职责组织；`server.py` 是传输启动入口，`runtime.py` 负责资源装配与生命周期，
`service.py` 是业务入口，`audit.py` 和 `log_config.py` 保留在根目录。

| 包 | 职责 |
|---|---|
| `config/` | 配置、Settings 快照、JSONC 与数据根目录解析 |
| `domain/` | anchors、categories、namespace、summary 与词法查询规则 |
| `storage/` | 存储契约、工厂、SQLite/dual/NDJSON、锁、JSONL 与恢复 |
| `embedding/` | provider、串行适配器、向量 codec 与索引值语义解析 |
| `transport/` | MCP 工具定义和分发、反馈回执、Admin HTTP 接口 |
| `operations/` | 调用线程池、维护状态、导入协调与模型准备 |
| `cli/` | 离线管理、embedding、恢复、配置迁移及 DeepSeek 回填 |

内部代码与测试直接导入新模块；Storage 对外通过 `app.storage` 导出契约和工厂，
工厂延迟加载具体后端。Domain 不依赖服务或传输层，在线业务不依赖 CLI。

现有 `bin/start.sh`、`bin/manage`、`bin/recover.sh` 及 `python -m app.server` 不变。
根目录保留 `manage_cli`、`embedding_cli`、`recover_cli` 和三种 `*_migrate_deepseek`
的薄入口，原来的 `python -m app.<命令>` 继续可用；实现位于 `app.cli.<命令>`。
其他旧的内部模块导入路径不保留兼容层。分包不改变数据格式或数据目录，无需迁移数据。

## 启动和关闭

Admin Debug 模式默认关闭。配置页勾选“启用 Debug 模式”并保存，刷新页面后显示
顶部及新增／明细抽屉的 Debug 按钮、Audit 导航和 Stats 诊断入口；点击 Debug
按钮才开启元素 ID 检查。配置持久保存为 `config.jsonc` 的 `debug` 布尔字段。
也可用 `bin/start.sh --debug`（或 `python -m app.server --debug`）在启动时启用；
之后允许配置页关闭。Restart Server 保留原参数，因此带 `--debug` 重启会再次启用。
该参数本身不写入配置，两种方式均不调整日志级别或 MCP，stdio 模式没有界面效果。

从仓库根目录运行 `bin/start.sh`。Ops 页面执行任何运维动作前，先点 **Enter
Maintenance**；此时普通记忆写入暂停，查询继续。动作完成后点 **Exit Maintenance**。
Restart Server 和 Shut Down Server 也要求先进入维护状态。需要执行独立 CLI 时，
在维护状态下点击 Shut Down Server，等待服务退出。

关闭后也可以从终端执行：

```bash
bin/start.sh
```

## MCP 客户端接入

默认 HTTP 模式同时提供旧版 HTTP+SSE 与新版 Streamable HTTP，两者共用同一实例。
默认仅监听 `127.0.0.1`（`local_only: true`），只有本机可访问。让 LLM agent 自行读取接入
说明：`GET http://127.0.0.1:<port>/read-me`（只读、无鉴权，端口跟随启动参数）。

推荐（多客户端并发共享、Codex 原生直连）——`~/.codex/config.toml`：

```toml
[mcp_servers.memory-server]
url = "http://127.0.0.1:1314/mcp"
```

若原条目使用 `command`/`args` 的 `mcp-remote` 桥接，请替换该条目，避免同一服务重复注册。
现有 opencode 的 `type: remote` + `/sse` 连接保持不变。默认仅接受 loopback
（`127.0.0.1`/`localhost`）Host/Origin：非法 Host 返回 421，非法 Origin 返回 403，
非 POST 方法返回 405（`Allow: POST`）。设置 `local_only: false` 后监听 `0.0.0.0`，局域网
可访问全部 HTTP 入口，且 `/mcp` 不再校验 Host/Origin；服务无鉴权，仅限可信网络。

## 维护入口

`bin/manage` 会定位仓库、加载 `.env`、激活 `.venv`，然后调用已有的维护 CLI。默认
主库是数据目录下的 `memories.db`，副本是同目录下的 `memories.jsonl`；路径可以在 `.env` 或
命令参数中覆盖，但输出必须位于数据目录内。

## 配置与数据目录

默认数据目录是当前用户的持久目录，与启动时的工作目录无关：

| 操作系统 | 默认数据目录 |
|---|---|
| Linux | `~/.remember-me/` |
| Windows | `%LOCALAPPDATA%\remember-me\`（未设置时回退到 `%USERPROFILE%\AppData\Local\remember-me`） |
| macOS | `~/Library/Application Support/remember-me/` |

可通过 `--data-dir DIR` 或 `MEMORY_DATA_DIR` 覆盖；相对目录按仓库根目录解析，优先级
`--data-dir` > `MEMORY_DATA_DIR` > 系统默认。配置固定在选中目录的 `config.jsonc`，
首次正常启动会创建：

```jsonc
{
  // HTTP 端口
  "port": 1314,
  "language": "zh-CN",
  "auto_download_model": true,
  // true=仅监听 127.0.0.1；false=监听 0.0.0.0 并对局域网开放全部 HTTP 接口
  "local_only": true,
  // Debug 按钮、Audit 与诊断入口；配置页保存后刷新生效
  "debug": false,
  // 代理地址可以包含端口
  "proxy": {"enabled": false, "url": ""}
}
```

支持 `//` 单行注释、`/* … */` 块注释及尾随逗号；配置页面保存时保留已有注释。
仅存在旧 `config.json` 时，正常启动会沿用原值并生成 `config.jsonc`，旧文件保留作备份。
两者同时存在时只读取 `config.jsonc`；新文件出错不会退回旧文件。只读状态及帮助不迁移文件。

端口允许 1–65535，语言允许 `zh-CN` 和 `en`；本版只保存语言选择，尚未进行界面翻译。
修改配置后重启生效。`--port PORT` 临时覆盖端口（0 表示随机可用端口），`--stdio` 显式
启动标准输入输出模式，两者互斥。模块导入和 `--help` 不会创建配置或其他文件。

`local_only` 控制监听地址与访问范围：`true`（默认）仅监听 `127.0.0.1`；`false` 监听
`0.0.0.0`，局域网可访问全部 HTTP 入口（含管理页面与 `/mcp`），且 `/mcp` 不再校验
Host/Origin。服务没有任何账号或密码，`local_only: false` 等同于把读/写/删除记忆、导入导出
及重启/关机接口暴露给整个网络，请仅在完全可信的网络中启用。已有远程部署升级后默认回到
仅本机，需要远程访问时必须显式设置 `local_only: false`；修改访问范围需重启服务生效。

旧配置缺少新字段时自动使用默认值。Sentence Transformers / FastEmbed 优先仅从本地缓存
加载；缓存缺失时 HTTP 服务先开放配置页并强制进入维护状态，若 `auto_download_model=true`
则后台下载。完整缓存启动不联网。禁止自动下载时等待操作，下载失败时保留配置页并显示错误；不会自动更换模型
或数据库 embedding profile。显式本地模型路径缺失时请恢复该目录，服务不把路径当作远程模型名。

代理默认关闭，直连且忽略系统的 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`（含小写）。
启用时设置 `"proxy": {"enabled": true, "url": "http://127.0.0.1:7890"}`；只支持 HTTP/HTTPS
代理，允许 URL 中的用户名/密码，`config status` 隐藏凭据。本地 Ollama 请求始终直连。
该策略用于 Memory 进程及其模型下载，不修改独立 Ollama 服务的代理设置。
配置入口以 `auto_download_model` 为准，覆盖旧 `.env` 的 `FASTEMBED_OFFLINE`、
`SENTENCE_TRANSFORMERS_OFFLINE`、`HF_HUB_OFFLINE`、`TRANSFORMERS_OFFLINE`；
模型库在加载时通过 `local_files_only` 控制联网。禁用独立 Xet 下载通道，让模型下载统一遵循 HTTP 代理配置。

首次模型缺失时，浏览器打开 `/admin/console` 自动进入配置页，可保存代理并重试，不需要
为代理变更重启。页面每两秒更新真实字节进度（当前文件或下载批次）；总量未知、等待网络
或验证期间显示不确定进度。保存后重试会先结束旧任务，再使用新配置准备，保持一个任务运行。
模型下载、加载及数据库校验完成前，记忆读写/搜索不可用，后端拒绝退出维护；完成后仍保持维护，
由操作者手动退出。重启、关机始终可用，并等待模型任务取消和资源清理。
配置页面上方可修改端口、语言、访问范围（“仅本机服务”）、自动下载和代理，保存无需进入维护，
也不暂停当前记忆服务。取消“仅本机服务”会显示局域网暴露警告。
端口、语言与访问范围重启后生效（显式 `--port` 仍优先），下载策略供下次模型准备/启动使用。
页面下方集中提供需要维护模式的操作，包括模型重试、重启/关机、导入/恢复、导出、Sync、清空及 namespace 迁移。
stdio 和维护 CLI 仍同步准备模型，失败退出；没有 Web 配置页。也可预先执行
`bin/manage embedding download-model`：该命令明确允许下载，即使自动下载关闭，也使用同一代理
和 data 下的缓存。Ollama 模型仍由其服务管理，缺失时执行 `ollama pull MODEL`。

```bash
bin/start.sh --data-dir /path/to/runtime-data
bin/start.sh --stdio
bin/manage --data-dir /path/to/runtime-data config status
```

数据库、副本、audit 及轮转文件、`logs/`、`tmp/`、`anchor-backfill/`、模型缓存都归数据目录。
本地模型缓存默认在 `models/fastembed/` 和 `models/sentence-transformers/`；其他依赖缓存也
归该目录。路径环境变量和维护命令的输出参数以数据目录为根解析，越界路径（包括符号链接）
被拒绝；安装资源、`.env`、外部只读导入源和独立 Ollama 服务的模型目录不受此规则约束。
管理界面显示数据目录内的相对路径，内部执行使用绝对路径。

旧配置先预览，再停服迁移。迁移保留源文件，复制校验成功后更新 `.env` 路径和配置。
迁移源、目标的缺省值都按上面的优先级选择；迁移仓库旧 `data/` 时显式传源目录，
`bin/manage` 会加载 `.env`，因此还需显式指定目标（或先移除 `MEMORY_DATA_DIR`）。
迁移到系统默认目录时，成功后从 `.env` 删除 `MEMORY_DATA_DIR`，让后续启动采用默认规则；
迁移到自定义目录时保留显式覆盖项：

```bash
# 迁移仓库 data/ 到当前系统默认目录
bin/manage --data-dir ~/.remember-me config migrate --source-data-dir data --dry-run
# 在配置页面进入 Maintenance 并 Shut Down Server，等待退出
bin/manage --data-dir ~/.remember-me config migrate --source-data-dir data
bin/start.sh
```

更换数据目录：`bin/manage --data-dir NEW config migrate --source-data-dir OLD --dry-run`，
确认后去掉 `--dry-run`。迁移会拒绝覆盖冲突文件和正在使用的数据库。异常时回退；进程
崩溃留下迁移日志时，服务拒绝启动，重新运行相同迁移命令先恢复，再预览和执行迁移。
验收模型加载及主副本健康后，可自行清理保留的旧日志和缓存；不要删除仍在使用的共享缓存。

先看帮助：

```bash
bin/manage --help
bin/manage embedding --help
```

只读状态检查可以在服务运行时执行。它会显示当前配置的路径和 embedding profile；副本
是否完全同步以管理台 Stats 的 `pending`、`in_sync`、`missing`、`extra` 和
`corrupt_lines` 为准：

```bash
bin/manage status
bin/manage embedding status
```

需要写 SQLite 或副本的命令必须在服务关闭后执行。它们会获取现有的 primary writer
lock；服务仍运行时会快速失败，不会自动终止服务，也不要手工删除 `.writer.lock`：锁
文件存在不等于锁当前被占用。

## 标准维护流程

1. 在管理台执行 Full Export 或确认最近一次 Full Sync 成功。
2. 执行 Shut Down Server，等待服务退出。
3. 运行 `bin/manage status`，确认当前数据库和副本路径正确。
4. 执行所需的离线命令。
5. 重新运行 `bin/start.sh`。
6. 查看管理台 Stats，确认 `pending=0`、`in_sync=true`、`missing=0`、`extra=0`、
   `corrupt_lines=0`。

## Embedding 维护

```bash
bin/manage embedding status
bin/manage embedding download-model
bin/manage embedding reindex
bin/manage embedding reindex-index-values
bin/manage embedding migrate-codec --dtype float16
bin/manage embedding activate PROFILE_ID
```

切换 provider 或 model 前，先准备对应的本地模型和 `.env`，再执行完整 `reindex`。
active profile 必须与服务启动时的 provider、model、dimension 和 dtype 一致。

## Keyword/category 离线回填

这两项会调用外部 `opencode` CLI，并逐条更新数据库。先使用 dry-run 检查范围：

```bash
bin/manage migrate keywords --dry-run --limit 20
bin/manage migrate categories --dry-run --limit 20
```

确认后去掉 `--dry-run`。可以用 `--namespace`、`--memory-id`、`--limit` 和 `--db` 缩小
范围；失败记录会在命令输出中列出，之后可以再次运行。

## JSONL 恢复

服务运行时，可在 Ops 进入维护状态，上传完整 JSONL 备份并点击 Recover。重建期间查询
继续；主库切换时查询会短暂收到重试提示。页面显示任务进度，成功后会对 dual 副本执行
Full Sync。若副本同步失败，保持维护状态，使用 Ops 的 Sync 重试后再退出。

离线恢复仍可用；它从不含 embedding 的 JSONL 重建 SQLite，并在成功前不替换目标数据库：

```bash
bin/manage recover
bin/manage recover /path/to/backup/memories.jsonl recovered.db
```

恢复前确认服务已关闭，并确认 source 是可信的 JSONL。恢复后启动服务，检查 active
embedding profile 和 Stats；如果 provider/profile 不匹配，先按 Embedding 维护章节重新
索引。

所有命令也可以直接使用对应的 `python -m app.*` 形式；`bin/manage` 只是统一了路径、
环境和操作顺序。
