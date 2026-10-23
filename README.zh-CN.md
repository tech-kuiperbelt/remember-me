<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="web/static/img/brand/logo-dark-128.png">
    <img src="web/static/img/brand/logo-light-128.png" alt="Remember Me 海马 Logo" width="96" height="96">
  </picture>
</p>

<h1 align="center">Remember Me</h1>

<p align="center">
  <strong>Agent-first memory. Human-visible by design.</strong>
  <br>
  <strong>以 Agent 为中心构建记忆，让人类始终看得见。</strong>
</p>

<p align="center">
  <a href="README.md">English</a> · <a href="README.zh-CN.md">简体中文</a>
</p>

Remember Me 是一个面向 AI Agent 的**本地优先持久化记忆服务**，通过 [Model Context Protocol（MCP）](https://modelcontextprotocol.io/) 为 Agent 提供跨会话的记忆能力。

Agent 是记忆的主要生产者和使用者，可以保存有长期价值的知识、回忆以往决策、更新过去的经验。Web UI 则让用户看见 Agent 记住了什么、这些记忆如何组织，以及记忆如何被检索和使用。

**这是为 Agent 提供的记忆基础设施，而积累的知识始终由用户掌控。**

## 为什么需要 Remember Me？

AI Coding Agent 已经能够完成复杂的工作，但其经验往往被一次次会话分割。一个 Agent 可能调查过问题、比较过方案、做过决策，也从错误中获得过经验；下一次会话开始时，却很难自然地继续利用这些积累。

Remember Me 希望让有价值的知识超越单次对话，成为未来工作可以复用的经验。它遵循三个原则：

**Agent-first：由 Agent 主导记忆。** 记忆的检索、创建和更新主要由 Agent 根据任务与指令完成，不要求用户像维护笔记一样持续手工整理。Remember Me 提供 MCP 工具，实际使用时机由 Agent 决定。

**Retrieval-oriented：为找回和复用而保存。** 记忆不是完整的聊天记录归档。它的价值在于未来需要时，能够根据语义、词语或明确的锚点找回已有知识、决策与经验。

**Human-owned：记忆由用户掌控。** 工作中积累的知识不应被锁定在单个 Agent 或平台中。记忆应当可见、可理解，用户可以检查和管理自己的记忆；开放、可读和可迁移是 Remember Me 的设计原则。Web UI 为此提供观察和管理窗口。

> Agent 可以更换，模型可以升级，工具可以演进，但用户积累的知识和经验应该能够延续。

## 核心功能

- **Agent 驱动的长期记忆**：通过 MCP 创建、检索、更新、删除记忆；由 Agent 根据任务与使用指令决定何时调用。
- **三种检索方式**：Semantic（语义）按含义检索、Lexical（全文）匹配词语、Anchor（锚点）使用明确线索或标识符检索。
- **结构化组织**：Namespace、Category、Keyword、Anchor 帮助跨项目、跨场景整理知识。
- **可观察的 Web UI**：浏览、检索、筛选、查看和编辑记忆，查看记忆分布与检索统计。
- **跨 Agent 访问**：不同兼容 MCP 的客户端可以连接同一个独立的记忆服务，并在适当的 Namespace 中复用积累的记忆。
- **本地优先运行**：SQLite 保存记忆数据，Sentence Transformers 在 Python 进程内运行 **BAAI/bge-m3**，无需独立的模型服务。

**关于数据可迁移性：** 用户控制、可读数据、备份和迁移是项目重视的原则。具体导入导出、备份和恢复能力及文件格式，请以所安装版本的 Help 与运维文档为准；这里不预先承诺所有导入导出流程均已实现。

## 看见 Agent 的记忆

Web UI 主要用于**透明性和可观察性**，而不是让用户每天手动管理笔记。

### Memory Explorer · 记忆浏览与检索

浏览、筛选记忆，选择检索方式，查看 Namespace、Category、Keyword 和 Anchor 等组织信息。

![使用虚构演示记忆制作的 Remember Me 记忆浏览界面示意图](assets/screenshots/memory-explorer.png)

### Memory Detail & Retrieval Statistics · 记忆详情与检索统计

查看单条记忆的内容与关联信息，必要时进行修正；同时观察记忆检索活动与反馈数据。

<p align="center">
  <a href="assets/screenshots/memory-detail.png"><img src="assets/screenshots/memory-detail.png" alt="使用虚构数据制作的记忆详情示意图" width="49%"></a>
  <a href="assets/screenshots/retrieval-stats.png"><img src="assets/screenshots/retrieval-stats.png" alt="使用虚构数据制作的 AI 检索统计示意图" width="49%"></a>
</p>

*以上为基于项目现有界面风格制作的演示预览，使用虚构数据，并非从实际运行服务中直接截取的原始截图。*

## 快速开始

推荐方案：**Python + Sentence Transformers + BAAI/bge-m3**。无需安装 Ollama、Docker 或另外启动 Embedding 服务。

### 1. 安装

需要 Git、支持 `venv` 的 Python 3.10 或更新版本，以及足够运行 BGE-M3 的磁盘空间和内存。以下命令适用于 Linux/macOS 风格的 Shell。

```bash
git clone https://github.com/tech-kuiperbelt/remember-me.git
cd remember-me
bash bin/install.sh
```

安装脚本创建 `.venv`，先安装 CPU-only PyTorch，再锁定该 Torch 版本安装其余依赖。Linux 使用 PyTorch 官方 CPU wheel 索引，macOS 使用 PyPI 上的无 CUDA 发行包。已在 Linux x86_64、Python 3.12 上验收；macOS 尚待实机验证，此 Bash 脚本暂不支持原生 Windows。

CPU 安装请使用脚本：仅执行 `pip install -r requirements.txt` 不能保证选择 CPU wheel。已有 GPU 或损坏环境会使脚本停止；请先保留或重命名原环境，再运行脚本创建干净的 `.venv`。兼容的 CPU 环境可复用。安装流程已包含 Sentence Transformers 依赖。**模型权重不包含在 GitHub 源码仓库中。**

### 2. 启动服务

**默认启动**

```bash
bash bin/start.sh
```

全新安装的默认配置为 **Sentence Transformers + BAAI/bge-m3 + CPU**。已有模型缓存会直接复用；如果缺少模型文件，在支持的“模型尚未就绪”状态下，HTTP/Web UI 仍可启动，模型准备成功后，还需在 Web UI 中手动退出维护模式，才能写入记忆。

已有安装在未显式配置 Provider 时沿用数据库的活动 Embedding Profile，包括历史 Ollama Profile。`bin/start.sh` 会加载本机 `.env`（如存在）。切换 Provider 或模型需要显式指定目标并离线重建索引。

使用默认本地地址和端口时：

| 用途 | 地址 |
| --- | --- |
| Web UI | `http://127.0.0.1:1314/admin/console` |
| 面向 Agent 的配置说明 | `http://127.0.0.1:1314/read-me` |
| MCP — Streamable HTTP | `http://127.0.0.1:1314/mcp` |
| MCP — SSE | `http://127.0.0.1:1314/sse` |

**自定义端口和数据目录**

可以直接向 `bin/start.sh` 传入启动参数：

```bash
# 指定 HTTP 端口
bash bin/start.sh --port 8080

# 本次启动绑定全部接口（例如 Docker bridge 容器内；MCP Host/Origin 防护仍生效）
bash bin/start.sh --host 0.0.0.0

# 指定配置、数据库和模型缓存所在的数据目录
bash bin/start.sh --data-dir /path/to/remember-me-data

# 同时指定端口和数据目录
bash bin/start.sh --port 8080 --data-dir /path/to/remember-me-data
```

`--port` 仅覆盖本次启动使用的端口，不修改配置文件中的端口。使用 `--port 8080` 时，Web UI 地址为 `http://127.0.0.1:8080/admin/console`，MCP 客户端连接地址中的端口也需改为 `8080`。

`--host` 仅覆盖本次启动的绑定地址，只接受 `127.0.0.1` 或 `0.0.0.0`，不会写入配置文件，也不改变由 `local_only` 决定的 MCP Host/Origin 防护。因此 Docker 容器可以监听 `0.0.0.0`，而宿主机只发布 `127.0.0.1:1314:1314`，客户端仍使用 `http://127.0.0.1:1314/...`。端口发布只限制宿主机侧暴露范围：同一 Docker 网络中的其他容器仍可直接访问容器端口。不能与 `--stdio` 同时使用。

`--data-dir` 选择数据目录及其中的 `config.jsonc`，优先级高于 `MEMORY_DATA_DIR` 环境变量。相对路径以项目根目录为基准。选择新目录会使用独立的数据，不会自动搬迁已有数据库或模型缓存。

### 3. 首次下载模型与代理配置

首次使用时可能需要从上游下载 **BAAI/bge-m3**。模型会缓存在本地，下载后可以在本机进行 Embedding 推理，无需持续连接外部模型服务。

如果所在网络需要代理才能访问模型下载源，请打开 Web UI 的**配置页面**，设置模型下载所使用的 HTTP/HTTPS 代理，保存后点击 **Retry（重试）**。后台模型下载失败后，通过代理配置重试不需要重启服务。

**注意：** 只有“本地缺少模型 / 后台下载失败”等可恢复情况，才能保证配置页面仍可用。缺少 Python 依赖、Provider 配置无效、已缓存模型加载严重失败等其他初始化错误，仍可能导致整个服务无法启动；此时应按错误信息及 Help 进行排查，不要将所有启动失败都归因于代理。

**资源提示：** 请为模型和 Python 依赖预留足够的磁盘空间与内存；最低硬件要求仍待验证。

Web UI 的代理设置供版本检查和 SOURCE 模型下载共用。通过 pip 安装 Python 依赖时，需要单独配置相应的网络或代理。自动下载模型由 `auto_download_model` 控制；关闭时可在配置页显式发起重试。

<a id="source-updates"></a>
## SOURCE 手动更新

先停止服务并确认进程退出，备份**完整数据目录**（包含配置、数据库及日志等），
保留旧版本和匹配备份；阅读所有相关版本的 Upgrade Notes / Breaking Changes。
在原源码目录中确认工作区修改已妥善处理、当前分支有上游且可快进后，由你手动执行：

```bash
git pull --ff-only
bash bin/install.sh
bash bin/start.sh
```

`git pull --ff-only` 跟随当前分支，可能取得尚未正式发布的代码；显示版本只来自
本地 `release.jsonc`。遇到 Dirty、Detached HEAD、分支分歧或无上游时，停止并人工处理，
不要强制覆盖本地修改。应用不探测 Git 状态，不执行这些命令。
重新启动时保留原数据目录、配置、环境与启动参数；验证健康和旧记忆检索。

## 版本检查与网络

HTTP/Web 启动后异步检查一次，之后默认每 24 小时检查；Sidebar 底部版本窗口可
手动刷新。stdio 和离线 CLI 不执行检查。`Unknown` 表示本地版本未知，不妨碍远程检查；
`No Release` 表示尚无正式发行，并非网络故障。应用只提示与复制，不会自动升级或备份。

`Unavailable` 时点击版本窗口的“配置代理”，进入现有 Settings；保存后返回窗口刷新，
下次检查即使用新配置，无需重启。后端访问 `api.github.com` 和 `raw.githubusercontent.com`，
关闭代理时直连；检查服务主机/容器的 DNS、连接、证书与代理可达性，限流时等待冷却，
不要关闭 TLS 校验。容器中的 `localhost` 指向容器自身；此代理不使 Docker 内置模型联网。
Docker Engine / Docker Desktop 拉取 GHCR 镜像的代理、Git 和 pip 的网络需分别配置，
应用 Settings 不会配置它们。完整操作见 Web UI 的 Help → 配置 → 更新与网络。

## Docker（Linux AMD64）

**发布前状态：官方 GHCR 镜像尚未公开。** 以下命令为正式发布准备；`1.0.0`
是示例版本，不代表已可下载。公开后请使用实际发布的完整版本。本地验收改用
`REMEMBER_ME_IMAGE=remember-me:phase3.2-ready-fixed`，不能据此认定匿名下载通过。
两种方式都无需安装 Python 或独立 Embedding 服务：**镜像内置 BGE-M3，CPU 推理，无需配置或下载模型。**

以下 Bash 命令面向本机 rootful Linux Docker Engine、AMD64；Compose 方式还需
Docker Compose 插件。Rootless、Docker Desktop、SELinux 和网络文件系统需独立验证权限与平台。
镜像解包后约 3.5 GB，请为拉取、升级、备份和模型运行预留空间与内存；最低内存要求尚未确定。

### 准备一个实例

**选择一种**安装方式。两种方式使用相同镜像和 `./data`，不要让两个实例同时写入同一目录。
在新的工作目录执行以下命令，后续继续使用该目录。路径必须位于 Docker daemon 本机。

```bash
mkdir -p remember-me
cd remember-me
export REMEMBER_ME_IMAGE=ghcr.io/tech-kuiperbelt/remember-me:1.0.0
export REMEMBER_ME_DATA_DIR="$PWD/data"
export REMEMBER_ME_PORT=1314
```

记录这些设置；新终端需要重新设置，尤其是自定义目录或端口。固定完整版本或发布说明中的
`ghcr.io/tech-kuiperbelt/remember-me@sha256:...`，避免隐式使用 `latest`。
Git Tag 为 `v1.0.0`，镜像 Tag 为 `1.0.0`。

### 方式一：docker run

创建专用目录，执行镜像已有的初始化器。只有这个短暂运行的辅助容器以 root 启动；
它将**空目录**准备为 UID/GID `10001:10001`，随后降权检查访问。
已有非空目录只检查权限，不递归修改属主或覆盖配置；初始化失败时不要继续启动。

```bash
mkdir -p "$REMEMBER_ME_DATA_DIR"
docker run --rm --platform linux/amd64 --network none --read-only \
  --user 0:0 --cap-drop ALL \
  --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
  --cap-add SETUID --cap-add SETGID --security-opt no-new-privileges:true \
  --mount "type=bind,src=$REMEMBER_ME_DATA_DIR,dst=/data" \
  --entrypoint python "$REMEMBER_ME_IMAGE" /opt/remember-me/docker/init_data.py
```

初始化成功后启动应用：

```bash
docker run -d --name remember-me --platform linux/amd64 \
  --restart unless-stopped --stop-timeout 120 \
  --user 10001:10001 --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --mount "type=bind,src=$REMEMBER_ME_DATA_DIR,dst=/data" \
  -p "127.0.0.1:${REMEMBER_ME_PORT}:1314" "$REMEMBER_ME_IMAGE"
```

### 方式二：Docker Compose

将**同一版本 Tag** 中的[官方 compose.yaml](compose.yaml) 下载到工作目录，无需克隆源码：

```bash
curl -fL https://raw.githubusercontent.com/tech-kuiperbelt/remember-me/v1.0.0/compose.yaml -o compose.yaml
docker compose -f compose.yaml up -d --wait --wait-timeout 300
```

发布文件和镜像下载都要求对应版本已存在。发布前本地验收使用当前源码中的 `compose.yaml`
副本，并按上文覆盖本地镜像。历史 `docker-compose.yml` 与本部署无关，请显式使用
`-f compose.yaml`。Compose 在应用前执行相同的权限初始化，默认值与准备步骤一致。
长期使用自定义配置时，可把三个 `REMEMBER_ME_*` 设置写进 `compose.yaml` 旁的本地 `.env`，
数据目录使用绝对路径、不写 `export`；已导出的 Shell 变量优先。启动前运行
`docker compose -f compose.yaml config` 核对。切换安装方式前先停止并移除原容器。

### 连接、检查和停止

健康后访问 <http://127.0.0.1:1314/admin/console>。首次加载可能需要数分钟；如修改宿主机端口，
同步修改浏览器与 MCP 地址中的 `1314`。Streamable HTTP 使用 `/mcp`，传统 SSE 使用 `/sse`；
见下方[客户端配置](#连接并启用-agent-记忆)。

| 操作 | docker run | Docker Compose |
| --- | --- | --- |
| 状态 | `docker ps --filter name=remember-me` | `docker compose -f compose.yaml ps -a` |
| 日志 | `docker logs --tail 100 -f remember-me` | `docker compose -f compose.yaml logs --tail 100 -f remember-me` |
| 健康 | `docker inspect --format '{{.State.Health.Status}}' remember-me` | `docker compose -f compose.yaml ps` |
| 停止 | `docker stop --time 120 remember-me` | `docker compose -f compose.yaml stop` |
| 再启动 | `docker start remember-me` | `docker compose -f compose.yaml up -d --wait --wait-timeout 300` |
| 移除已停止容器 | `docker rm remember-me` | `docker compose -f compose.yaml down` |

健康检查只观察模型就绪，不触发下载。启动失败请检查日志、空间和目录权限。
语言、Debug 与版本检查代理仍可在配置页修改；Docker 管理模型设置，Retry 只在本地重载内置模型。
根文件系统和模型只读；单个 `/data` Bind Mount 保留配置、SQLite 及其 sidecar、Dual JSONL、
日志和缓存。删除或重建容器不会删除宿主机目录；不要删除该目录或覆盖镜像模型路径。

端口仅发布到 `127.0.0.1`，未经独立访问控制设计不要改成公网监听。同一 Docker 网络中的
容器仍可直接访问容器端口；Host/Origin 防护保留。API 和管理界面没有用户认证。
`unless-stopped` 在异常退出后重启容器，请从终端停止。停止前让导入等任务完成；
超过 120 秒宽限期时 Docker 可能强杀进程。

### 权限、备份和恢复

全新空目录初始化为 `0750`。已有非空目录如属主不兼容、文件不可读写或包含符号链接，
初始化会停止。停服后由管理员仅为**此实例的专用目录**准备 UID/GID `10001:10001` 权限；
不要对共享目录递归改属主、使用 `chmod 777` 或让应用以 root 运行。改权限前保留原数据。

必须在**优雅停机后备份整个数据目录**，不能只保留 JSONL 导出或 SQLite 主文件。
先执行上表对应停止命令，确认退出码为 **0**（`docker run` 使用
`docker inspect --format '{{.State.ExitCode}}' remember-me`，Compose 使用
`docker compose -f compose.yaml ps -a`）。停止命令成功不等于未被强杀；若被强杀，
先正常启动再干净停止。备份时一并记录镜像版本/digest、数据路径和端口。

```bash
sudo tar --numeric-owner -C "$REMEMBER_ME_DATA_DIR" -cpf "$PWD/remember-me-backup.tar" .
```

随后按上表重启。备份包含私有记忆，请妥善保管。恢复时先停止实例，将数据解压到
**新的空目录**，保留数字属主和权限：

```bash
mkdir "$PWD/restored-data"
sudo tar --numeric-owner --same-owner -C "$PWD/restored-data" -xpf "$PWD/remember-me-backup.tar"
export REMEMBER_ME_DATA_DIR="$PWD/restored-data"
```

如使用 `.env`，同步更新其中的数据目录。使用恢复目录重建容器：`docker run` 方式先移除已停止容器，再执行初始化和启动命令；
Compose 方式运行 `docker compose -f compose.yaml up -d --wait --wait-timeout 300`。
检查健康、配置、记忆明细和语义检索，再决定是否停用原副本。不要同时运行两个实例写入同一目录。

<a id="docker-updates"></a>
### 手动升级和回滚

阅读目标版本兼容性说明，停机做完整备份，保留旧镜像/版本和备份。
把 `REMEMBER_ME_IMAGE` 改成选定的公开完整版本或 digest（如使用 `.env`，也同步修改），再拉取：

```bash
docker pull --platform linux/amd64 "$REMEMBER_ME_IMAGE"
```

`docker run`：拉取镜像本身不会升级运行中的容器。完成停服备份后移除旧容器，
按原部署记录重新执行上文初始化与启动步骤，仅更换目标镜像引用；保留实际使用的数据挂载、
端口、环境变量、UID/GID、只读文件系统和安全选项，不要照搬与现有实例不同的参数。

Compose：在原实例目录保留现有 `compose.yaml`、数据目录、端口与配置。将
`REMEMBER_ME_IMAGE` 设置并导出为已确认的目标完整版本，**同步 `.env` 中保存的值**；
当前 shell 导出值优先于 `.env`。停服备份后执行：

```bash
docker compose -f compose.yaml up -d --pull always --wait --wait-timeout 300
```

镜像设置变化会重建服务。检查健康并检索一条旧记忆。先阅读所有相关版本的
Upgrade Notes / Breaking Changes；版本提示不证明数据兼容，也不会自动更新或迁移。

回滚时按相同步骤选择旧版本/digest。只有数据库 Schema 和 Embedding Profile 兼容时
才可直接复用当前数据；否则把对应完整备份恢复到新目录，并使用与备份匹配的镜像，备份后的
变更不包含在内。本地重建测试不代表任意未来版本之间均可升级或降级。

## 连接并启用 Agent 记忆

连接 MCP 让 Agent **具备使用记忆的能力**；合适的记忆指令则帮助它知道**何时、如何**使用记忆。两个步骤都很重要。

### 推荐：让 Agent 自动配置 MCP

启动 Remember Me 后，可以向能够访问本地服务、并有权限管理自身配置的 Coding Agent 发送：

> 请阅读 `http://127.0.0.1:1314/read-me`，按照说明为你自己配置 Remember Me MCP，并验证连接。修改配置文件前先征求我的确认。

`/read-me` 是面向 Agent 的配置说明。如需手动操作或排查问题，Web UI 的 **Help** 页面提供详细指导。

### 手动配置示例

**Codex — Streamable HTTP**

```bash
codex mcp add remember-me --url http://127.0.0.1:1314/mcp
codex mcp list
```

**OpenCode — SSE**（将以下示例合并到 OpenCode 配置中，保留已有的 MCP 条目）：

```json
{
  "mcp": {
    "remember-me": {
      "type": "remote",
      "url": "http://127.0.0.1:1314/sse",
      "enabled": true
    }
  }
}
```

不同客户端版本的配置位置与细节请以 Web UI Help 为准。如果 Agent 运行在远程环境，不能在没有网络连接配置的情况下直接访问你电脑上的 `127.0.0.1`。

### 给 Agent 配置记忆使用策略

**MCP 连接成功，不等于 Agent 就会自动记住一切，或总能在正确时机检索到经验。** 长期记忆的效果很大程度上取决于 Agent 的使用策略。

Web UI 的 **Help** 页面准备了可直接复制的记忆提示词。将其放入 Agent 使用的指令文件，例如 `AGENTS.md`，再按项目需要调整。提示词覆盖主动回忆、保存值得复用的决策和经验、写入前去重、更新已有记忆，以及按 Namespace 隔离项目上下文。

### 验证跨会话记忆

1. 请 Agent 在指定项目 Namespace 中保存一条非敏感的测试记忆。
2. 开始一个新的 Agent 会话，让它从相同 Namespace 中检索刚才的内容。
3. 打开 Web UI，查看这条记忆和可用的检索活动。
4. 如有需要，验证后删除测试记忆。

## 文档与安全

- **Web UI → Help**：安装配置、Agent 接入、记忆提示词和故障排查。
- **`/read-me`**：服务运行后提供的面向 Agent 的配置说明。

Remember Me 默认以本地运行为目标。**没有身份认证及合适访问保护时，不要把记忆或管理接口直接暴露到公网。** Namespace 用于组织记忆，不等同于访问控制和身份认证。

## 关于这个项目

Remember Me 是一个个人独立开源项目，在多种 AI Coding Agent 的协助下开发。

## 开源许可

Remember Me 自有项目代码采用 [Apache License 2.0](LICENSE)，使用、修改和再分发需遵守许可证条款，并保留适用的版权与来源声明。

- [LICENSE](LICENSE)：项目许可证。
- [NOTICE](NOTICE)：原项目归属说明。
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)：第三方代码、模型及素材的来源和许可说明。

Python 安装单独下载模型文件；Docker 镜像内置 BGE-M3，并保留其 [MIT 许可证](docker/licenses/BGE-M3-MIT.txt)和[来源声明](docker/licenses/BGE-M3-NOTICE.txt)。模型权重、tokenizer 和缓存适用各自上游条款；项目许可证不对这些材料重新授权。

项目由 **shenyu1997** 开发，通过 [tech-kuiperbelt](https://github.com/tech-kuiperbelt) 发布。
