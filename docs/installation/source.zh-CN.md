# 源码安装与升级

从 Git 获取 Remember Me，并用 Python 在本机运行。若使用官方容器，见 [Docker 安装与升级](docker.zh-CN.md)。

推荐的 Python 方式是 **Sentence Transformers + BAAI/bge-m3，CPU 推理**。
不需要 Ollama、Docker 或独立 Embedding 服务。模型权重**不包含**在 GitHub 仓库中，首次使用时下载。

## 环境要求

- Git。
- Python 3.10 或更高版本，并支持 `venv`。
- Linux/macOS 风格的 Shell。
- 为 BGE-M3 及其 Python 依赖预留足够的磁盘空间和内存。

已在 Linux x86_64 + Python 3.12 验证。macOS 仍需在真实设备上验证；该 Bash 安装脚本不支持原生 Windows。

## 安装

```bash
git clone https://github.com/tech-kuiperbelt/remember-me.git
cd remember-me
bash bin/install.sh
```

安装器会创建 `.venv`，安装 CPU 版 PyTorch，再在固定该 Torch 版本的前提下安装其余依赖。
Linux 使用 PyTorch 官方 CPU wheel 索引；macOS 使用 PyPI 上的非 CUDA 发行版。

CPU 环境请使用安装器：单独执行 `pip install -r requirements.txt` 不会选择 CPU wheel。
已有 GPU 环境或损坏的环境会让安装器停止；请保留或重命名后重新运行，以创建干净的 `.venv`。
兼容的 CPU 环境可以复用。

## 启动

**默认启动**

```bash
bash bin/start.sh
```

新安装默认使用 **Sentence Transformers + BAAI/bge-m3，CPU**。服务优先使用本地模型缓存。
模型文件缺失时，HTTP 服务可先进入 *model not ready* 维护状态并准备模型；
准备成功后，需在 Web UI 手动退出维护模式，才能写入记忆。

未显式配置 Provider 时，已有安装继续使用数据库中的活动 Embedding Profile，包括历史 Ollama Profile。
`bin/start.sh` 会加载本地 `.env`（如存在）。切换 Provider 或模型需要显式目标并离线 reindex。

默认本地地址与端口：

| 用途 | URL |
| --- | --- |
| Web UI | `http://127.0.0.1:1314/admin/console` |
| 面向 Agent 的说明 | `http://127.0.0.1:1314/read-me` |
| MCP — Streamable HTTP | `http://127.0.0.1:1314/mcp` |
| MCP — SSE | `http://127.0.0.1:1314/sse` |

**自定义端口与数据目录**

可以直接向 `bin/start.sh` 传入启动参数：

```bash
# 使用其他 HTTP 端口
bash bin/start.sh --port 8080

# 本次启动绑定全部接口（例如 Docker bridge 容器内；MCP Host/Origin 防护仍生效）
bash bin/start.sh --host 0.0.0.0

# 指定配置、数据库和模型缓存所在的数据目录
bash bin/start.sh --data-dir /path/to/remember-me-data

# 同时指定端口和数据目录
bash bin/start.sh --port 8080 --data-dir /path/to/remember-me-data
```

`--port` 只覆盖本次启动的端口，不修改配置文件。使用 `--port 8080` 后，浏览器打开
`http://127.0.0.1:8080/admin/console`，MCP 客户端 URL 也要使用端口 `8080`。

`--host` 只覆盖本次启动的绑定地址，接受 `127.0.0.1` 或 `0.0.0.0`，不写入配置文件，
也不改变由 `local_only` 控制的 MCP Host/Origin 防护。Docker 容器因此可以监听 `0.0.0.0`，
而宿主机只发布 `127.0.0.1:1314:1314`；客户端仍使用 `http://127.0.0.1:1314/...`。
发布映射只限制宿主机侧暴露：同一 Docker 网络中的其他容器仍可直接访问容器端口。
该选项不能与 `--stdio` 同时使用。

`--data-dir` 选择数据目录（包括其中的 `config.jsonc`），优先于 `MEMORY_DATA_DIR`。
相对路径以项目根目录为基准。选择新目录会使用独立数据，不会移动已有数据库或模型缓存。

`--model-dir`（或 `MEMORY_MODEL_DIR`）选择模型根目录，默认 `<data-dir>/models`。
显式根目录可以位于数据目录之外，例如只读模型镜像。Provider 缓存与 Hugging Face Hub 资源
随后解析到该根目录内，可写元数据缓存仍位于 `<data-dir>/cache`。

### 首次下载模型与代理配置

首次使用可能需要从上游下载 **BAAI/bge-m3**。模型会缓存在本地；下载完成后，
Embedding 推理在本机运行，不依赖持续在线的模型服务。

如果当前网络无法访问下载源，打开 Web UI 的**配置页**，为模型下载设置 HTTP/HTTPS 代理，
保存后选择 **Retry**。后台模型下载失败后，修改代理不需要仅为重试而重启服务。

**重要：**模型准备问题仍可使用 Web UI 维护模式：模型缺失、后台下载失败，以及缓存不完整/损坏
或加载、warmup 失败，都不再使 HTTP 服务退出。Retry 会重新加载模型，并重新下载不完整缓存。
缺少 Python 依赖会提示重新安装；无效 Provider 设置和其他配置错误仍可能阻止服务启动。
stdio 模式和使用模型的 CLI 命令会快速失败，而不是打开 UI。

**资源提示：**请为模型及其 Python 依赖预留足够的磁盘空间和内存。最低硬件要求尚待验证。

Web UI 的代理设置同时用于版本检查和 SOURCE 模型下载。用 pip 安装 Python 依赖需要单独配置网络或代理。
自动模型下载由 `auto_download_model` 控制；关闭后，可在配置页显式请求 Retry。

## 停止

在运行终端按 `Ctrl+C`（或发送 `SIGTERM`），等待进程退出。HTTP 服务会关闭活动 MCP/SSE
会话并处理已接受的工作；stdio 模式下关闭 stdin 或发送 `SIGTERM` 会触发同样的清理。
不要对同一数据目录启动第二个写入进程，也不要在服务运行时复制或升级数据。

## 数据目录

默认使用操作系统按用户划分的数据目录（例如 Linux 的 `~/.remember-me/`，
macOS 的 `~/Library/Application Support/remember-me/`）。可用 `--data-dir` 或
`MEMORY_DATA_DIR` 指定显式根目录；相对路径以项目根目录为基准，`--data-dir` 优先。

数据目录包含 `config.jsonc`、SQLite 数据库及其 writer lock/outbox 状态、审计日志、缓存，
以及模型根目录（默认 `models/`）。使用 `bin/start.sh` 启动时默认启用 Dual 存储，
同时保留 NDJSON 副本。选择其他目录不会移动已有数据；新目录从空数据开始。

## 备份与恢复

先停止服务。备份**整个数据目录**，不要只保留 SQLite 主文件或 JSONL 导出。

```bash
export DATA_DIR=/path/to/remember-me-data
tar -C "$DATA_DIR" -cpf "$PWD/remember-me-source-backup.tar" .
```

备份时一并记录 `release.jsonc` 中的代码版本、数据路径和启动参数。归档包含私有记忆，请妥善保护。

恢复时先停止服务，创建**新的空目录**，在保留属主和权限的前提下解压，再以该目录启动：

```bash
mkdir "$PWD/restored-data"
tar -C "$PWD/restored-data" -xpf "$PWD/remember-me-source-backup.tar"
bash bin/start.sh --data-dir "$PWD/restored-data"
```

检查健康、配置、记忆明细和语义检索，再决定是否停用原副本。
不要同时运行两个服务写入同一目录。

<a id="upgrade-and-rollback"></a>
## 升级与回滚

阅读所有相关版本的 Upgrade Notes / Breaking Changes。停止并确认服务已退出，备份**整个数据目录**
（包括配置、数据库和日志文件），并保留旧版本与匹配备份。

在原源码目录解决本地修改，确认当前分支有上游且可 fast-forward，然后手动执行：

```bash
git pull --ff-only
bash bin/install.sh
bash bin/start.sh
```

`git pull --ff-only` 跟随当前分支，可能取得尚未正式发布的代码。显示版本只来自本地 `release.jsonc`。
如果检出目录有未提交修改、处于 Detached HEAD、分支分歧或没有上游，请先停止并人工处理；
不要强制覆盖。应用本身不检查 Git 状态，也不执行这些命令。重启时保留原数据路径、配置、
环境变量和启动参数；验证健康并检索一条已有记忆。

### 回滚

使用旧版本代码及其匹配的完整备份。只有该版本的数据库 Schema 和 Embedding Profile 兼容时，
才可直接复用当前数据；否则把对应完整备份恢复到新目录，并运行与备份匹配的代码，
备份后的变更不包含在内。版本通知和本地重建测试不代表任意未来版本之间均可兼容。

## 下一步

打开 <http://127.0.0.1:1314/admin/console>，进入 **Help → 开始使用**，依次完成
连接 AI、为记忆分组、设置记忆规则三步。将 <http://127.0.0.1:1314/read-me>
交给 Agent，作为面向 Agent 的接入说明。如使用其他端口，请把两个地址中的端口一并替换。

MCP 客户端配置与记忆规则的详细说明只在 Web Help 维护；本指南不重复这些内容。

- 如更希望使用容器，见 [Docker 安装与升级](docker.zh-CN.md)。
- [版本编号规则](../../README.zh-CN.md#版本编号规则)与[版本检查与网络](../../README.zh-CN.md#版本检查与网络)。
