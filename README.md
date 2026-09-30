# remember-me 运维手册

## 启动和关闭

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
让 LLM agent 自行读取接入说明：`GET http://127.0.0.1:<port>/read-me`（只读、无鉴权，
端口跟随启动参数）。

推荐（多客户端并发共享、Codex 原生直连）——`~/.codex/config.toml`：

```toml
[mcp_servers.memory-server]
url = "http://127.0.0.1:1314/mcp"
```

若原条目使用 `command`/`args` 的 `mcp-remote` 桥接，请替换该条目，避免同一服务重复注册。
现有 opencode 的 `type: remote` + `/sse` 连接保持不变。`/mcp` 仅接受 loopback
（`127.0.0.1`/`localhost`）Host/Origin：非法 Host 返回 421，非法 Origin 返回 403，
非 POST 方法返回 405（`Allow: POST`）。

## 维护入口

`bin/manage` 会定位仓库、加载 `.env`、激活 `.venv`，然后调用已有的维护 CLI。默认
主库是数据目录下的 `memories.db`，副本是同目录下的 `memories.jsonl`；路径可以在 `.env` 或
命令参数中覆盖，但输出必须位于数据目录内。

## 配置与数据目录

默认数据目录是仓库的 `data`，与启动时的工作目录无关。可通过 `--data-dir DIR` 或
`MEMORY_DATA_DIR` 指定；相对目录按仓库根目录解析，命令参数优先。配置固定在选中目录的
`config.json`，首次正常启动会创建：

```json
{"port": 1314, "language": "zh-CN"}
```

端口允许 1–65535，语言允许 `zh-CN` 和 `en`；本版只保存语言选择，尚未进行界面翻译。
修改配置后重启生效。`--port PORT` 临时覆盖端口（0 表示随机可用端口），`--stdio` 显式
启动标准输入输出模式，两者互斥。模块导入和 `--help` 不会创建配置或其他文件。

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

旧配置先预览，再停服迁移。迁移保留源文件，复制校验成功后更新 `.env` 路径和配置：

```bash
bin/manage config migrate --dry-run
# 在配置页面进入 Maintenance 并 Shut Down Server，等待退出
bin/manage config migrate
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
