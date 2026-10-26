# Docker 安装与升级（Linux AMD64）

官方镜像 `ghcr.io/tech-kuiperbelt/remember-me:latest` 已公开，无需登录 GHCR 即可拉取。
**镜像内置 BGE-M3，CPU 推理，无需安装 Python、下载模型或准备独立 Embedding 服务。**
若希望从源码安装，见[源码安装与升级](source.zh-CN.md)。

以下 Bash 命令面向本机 rootful Linux Docker Engine、AMD64；Compose 方式还需
Docker Compose 插件。Rootless、Docker Desktop、SELinux 和网络文件系统需独立验证权限与平台。
镜像解包后约 3.5 GB，请为拉取、升级、备份和模型运行预留空间与内存；最低内存要求尚未确定。

## 准备一个实例

**选择一种**安装方式，使用专用工作目录，不要让两个实例同时写入同一数据目录。
路径必须位于 Docker daemon 本机。两种方式默认使用 `latest`，也可按需指定完整版本或 digest。
仅拉取新镜像不会更新已创建的容器。

## 方式一：docker run

### 最简单启动

```bash
mkdir remember-me
cd remember-me
docker run --name remember-me -v "$PWD:/data" -p 127.0.0.1:1314:1314 ghcr.io/tech-kuiperbelt/remember-me:latest
```

应用在前台运行，终端直接显示日志。数据保存在**宿主机当前目录**，它被挂载到容器的 `/data`。
容器端口 `1314` 映射到宿主机本机端口 `1314`。浏览器访问
<http://127.0.0.1:1314/admin/console>，MCP 客户端连接
`http://127.0.0.1:1314/mcp`（传统 SSE 使用 `/sse`）。

镜像默认以容器内 root 运行，普通本地目录无需单独初始化或执行 `chown`。
已有文件和目录的属主、权限不会自动修改；新建文件通常归 root 所有。
只读挂载等文件系统限制仍然有效。

### 推荐启动

在准备好的数据目录中，**选择此命令或上面的最简命令之一**：

```bash
docker run -d --name remember-me \
  --restart unless-stopped --stop-timeout 120 \
  -v "$PWD:/data" \
  -v /etc/localtime:/etc/localtime:ro \
  -p 127.0.0.1:1314:1314 \
  ghcr.io/tech-kuiperbelt/remember-me:latest
```

| 参数 | 含义 |
| --- | --- |
| `-d` | 后台运行，启动后返回终端。 |
| `--name remember-me` | 指定容器名称，方便后续管理。 |
| `--restart unless-stopped` | 容器退出后自动重启，随 Docker 启动；手动停止后保持停止。宿主机重启后自动运行还要求 Docker 服务自启动。 |
| `--stop-timeout 120` | 停止时最多等待 120 秒，让应用完成清理，超时后强制终止。 |
| `-v "$PWD:/data"` | 配置、记忆、日志和缓存保存在宿主机当前目录。 |
| `-v /etc/localtime:/etc/localtime:ro` | 只读挂载宿主机时区文件，让应用日志使用宿主机时区。最简命令使用容器默认时区；存储记录中的 UTC 时间格式不变。 |
| `-p 127.0.0.1:1314:1314` | 顺序为 `宿主机地址:宿主机访问端口:容器端口`；访问 <http://127.0.0.1:1314/admin/console>。修改宿主机端口时，只改 IP 地址后的第一个端口号，保留最后的容器端口 `1314`。 |
| `ghcr.io/tech-kuiperbelt/remember-me:latest` | 使用最新发布的稳定镜像。需要固定部署时，将 `latest` 替换为已发布完整版本，或使用 `ghcr.io/tech-kuiperbelt/remember-me@sha256:...`。 |

如果宿主机 `1314` 已被占用，将 `-p 127.0.0.1:1314:1314` 改为
`-p 127.0.0.1:5263:1314`，然后访问 <http://127.0.0.1:5263/admin/console>，
MCP 地址也使用 `5263`。容器内部仍监听 `1314`。

用 `docker logs -f remember-me` 查看后台日志。首次加载模型可能需要数分钟。
`latest` 不会自动替换已运行的容器，见[手动升级和回滚](#upgrade-and-rollback)。

### 其他可选启动参数

| 参数 | 用途 | 不填写时 |
| --- | --- | --- |
| `--platform linux/amd64` | 显式指定 Linux AMD64 平台。 | Docker 按默认平台选择镜像。 |
| `--user UID:GID` | 指定非 root 用户和组，要求该用户已能读写 `/data`。 | 使用镜像默认的 root 用户。 |
| `--read-only` | 容器根文件系统只读，挂载的 `/data` 仍可写。 | 容器自身文件系统可写。 |
| `--cap-drop ALL` | 移除额外 Linux capabilities 系统特权。 | 保留 Docker 默认的一组 capabilities。 |
| `--security-opt no-new-privileges:true` | 禁止进程通过执行程序获得额外权限。 | 不额外启用此限制。 |
| `--pull always` | 创建容器前检查并拉取指定镜像。 | `docker run` 在本地已有镜像时优先使用本地镜像。 |
| `--mount "type=bind,src=$PWD,dst=/data"` | 可替代 `-v "$PWD:/data"`，两种写法选一种。 | 示例已使用 `-v` 挂载数据。 |

对于**全新实例**，下面的可选组合使用宿主机当前用户的 UID/GID，并增加上表中的文件系统和
进程限制。它是另一种启动方式：

```bash
docker run -d --name remember-me --restart unless-stopped --stop-timeout 120 --user "$(id -u):$(id -g)" --read-only --cap-drop ALL --security-opt no-new-privileges:true -v "$PWD:/data" -v /etc/localtime:/etc/localtime:ro -p 127.0.0.1:1314:1314 ghcr.io/tech-kuiperbelt/remember-me:latest
```

请从非 root 宿主机账号运行。已有 root 创建的数据切换用户前，可能需要管理员处理权限；
镜像不会递归改属主。改权限前保留原数据。

## 方式二：Docker Compose

在新的工作目录下载[官方 compose.yaml](../../compose.yaml)。维护中的示例默认使用 `latest`，
无需克隆源码，也不需要每次发布都修改下面命令的版本号：

```bash
mkdir remember-me
cd remember-me
curl -fL https://raw.githubusercontent.com/tech-kuiperbelt/remember-me/master/compose.yaml -o compose.yaml
docker compose -f compose.yaml up -d --wait --wait-timeout 300
```

Compose 与推荐 Run 保持一致：默认 root、`latest`、宿主机时区、自动重启和 120 秒停止宽限。
数据保存在 **`compose.yaml` 所在目录**（按示例进入该目录执行时，就是当前目录），
发布**宿主机本机端口 `1314`**，无需独立数据初始化。健康后访问
<http://127.0.0.1:1314/admin/console>。

可在 `compose.yaml` 旁的本地 `.env` 中覆盖 `REMEMBER_ME_IMAGE`、`REMEMBER_ME_DATA_DIR`
（建议绝对路径）或 `REMEMBER_ME_PORT`，不写 `export`；已导出的 Shell 变量优先。
启动前运行 `docker compose -f compose.yaml config` 核对。固定版本部署也可从对应 Git Tag
下载 Compose，并指定完整镜像版本或 digest。切换方式前先停止并移除原容器；
要复用数据时，明确指定同一个数据目录。

Compose 也可显式配置服务的 `user`、`read_only`、`cap_drop` 和 `security_opt`，启用上文可选限制。
启用非 root 用户前先确认数据访问权限。历史 `docker-compose.yml` 与本部署无关，
请显式使用 `-f compose.yaml`。

## 连接、检查和停止

三种启动示例均默认访问 <http://127.0.0.1:1314/admin/console>。
Streamable HTTP 使用 `/mcp`，传统 SSE 使用 `/sse`。连接示例与各客户端配置方法由 Web Help
维护：使用实际宿主机映射端口打开 `/read-me`，如 <http://127.0.0.1:1314/read-me>。

| 操作 | docker run | Docker Compose |
| --- | --- | --- |
| 状态 | `docker ps -a --filter name=remember-me` | `docker compose -f compose.yaml ps -a` |
| 日志 | `docker logs --tail 100 -f remember-me` | `docker compose -f compose.yaml logs --tail 100 -f remember-me` |
| 健康 | `docker inspect --format '{{.State.Health.Status}}' remember-me` | `docker compose -f compose.yaml ps` |
| 停止 | `docker stop --time 120 remember-me` | `docker compose -f compose.yaml stop` |
| 再启动 | `docker start remember-me` | `docker compose -f compose.yaml up -d --wait --wait-timeout 300` |
| 移除已停止容器 | `docker rm remember-me` | `docker compose -f compose.yaml down` |

健康检查只观察模型就绪，不触发下载。启动失败请检查日志、空间和数据目录访问权限。
配置、SQLite 及其 sidecar、Dual JSONL、日志和缓存均保存在 `/data`。
删除容器不会删除挂载的宿主机目录；不要删除该目录或覆盖内置模型目录。
语言、Debug 与版本检查代理仍可在配置页修改；Docker 管理模型设置，Retry 只在本地重载内置模型。

端口仅发布到宿主机回环地址。同一 Docker 网络中的容器仍可直接访问容器端口；
Host/Origin 防护保留。API 和管理界面没有用户认证。停止前让导入等任务完成；
超过停止宽限期时 Docker 可能强杀进程。

## 权限、备份和恢复

普通启动无需准备权限。非 root 模式要求选定 UID/GID 能读写已有数据；
不要对共享目录递归改属主或使用 `chmod 777`。

按上表优雅停机，确认退出码为 **0**（Run 使用
`docker inspect --format '{{.State.ExitCode}}' remember-me`，Compose 使用
`docker compose -f compose.yaml ps -a`）。停止命令成功不等于未被强杀；若被强杀，
先正常启动再干净停止。备份**整个数据目录**，不能只保留 JSONL 导出或 SQLite 主文件。
同时记录实际运行版本/digest、数据路径和端口；仅记录 `latest` 不能确定回滚镜像。

先将下面的数据路径设置为实际值：在实例目录执行命令时，Run 和新 Compose 部署均为 `$PWD`；
已有或自定义部署使用其实际数据路径。归档必须放在数据目录**之外**；示例在同级备份目录生成带时间戳的文件：

```bash
export REMEMBER_ME_DATA_DIR="$PWD"
export REMEMBER_ME_BACKUP_DIR="$(dirname "$REMEMBER_ME_DATA_DIR")/remember-me-backups"
mkdir -p "$REMEMBER_ME_BACKUP_DIR"
export REMEMBER_ME_BACKUP="$REMEMBER_ME_BACKUP_DIR/remember-me-$(date -u +%Y%m%dT%H%M%SZ).tar"
sudo tar --numeric-owner -C "$REMEMBER_ME_DATA_DIR" -cpf "$REMEMBER_ME_BACKUP" .
```

归档完成后按上表重启。备份包含私有记忆，请妥善保管；换终端时重新设置这些变量为实际路径。
恢复时先停止实例，解压到**新的空同级目录**，保留数字属主和权限：

```bash
export REMEMBER_ME_RESTORE_DIR="$(dirname "$REMEMBER_ME_DATA_DIR")/remember-me-restored"
mkdir "$REMEMBER_ME_RESTORE_DIR"
sudo tar --numeric-owner --same-owner -C "$REMEMBER_ME_RESTORE_DIR" -xpf "$REMEMBER_ME_BACKUP"
```

Run 方式先移除已停止容器，把 `-v "$PWD:/data"` 替换为
`-v "$REMEMBER_ME_RESTORE_DIR:/data"` 后重建。Compose 方式将 `REMEMBER_ME_DATA_DIR`
设为恢复目录，同步 `.env`（如使用），然后运行
`docker compose -f compose.yaml up -d --wait --wait-timeout 300`。
检查健康、配置、记忆明细和语义检索，再决定是否停用原副本。

<a id="upgrade-and-rollback"></a>
## 手动升级和回滚

在原实例工作目录操作。拉取前记录实际运行版本和镜像 digest：用 `docker inspect`
查看运行容器的 `.Image`，再用 `docker image inspect` 查看该镜像的 `RepoDigests`。
Compose 的容器 ID 可通过 `docker compose -f compose.yaml ps -q remember-me` 获取。
保留原镜像和引用供回滚，先阅读所有相关版本的 Upgrade Notes / Breaking Changes。

选择 `latest` 或已发布的完整版本/digest；如使用 `.env`，同步修改其中保存的值，再拉取：

```bash
export REMEMBER_ME_IMAGE=ghcr.io/tech-kuiperbelt/remember-me:latest
docker pull --platform linux/amd64 "$REMEMBER_ME_IMAGE"
```

拉取后按上文优雅停机并做完整备份。Run 方式移除已停止容器，按原记录重建并替换为目标镜像，
保留数据挂载、端口、时区、用户和其他选项。Compose 方式保留原 `compose.yaml`、数据目录、
端口与配置。旧 Compose 默认使用 `./data` 和宿主机端口 `1314`，升级时保留这些值；
不要用新模板覆盖原文件而意外切换数据目录。导出选定的 `REMEMBER_ME_IMAGE` 后运行：

```bash
docker compose -f compose.yaml up -d --pull always --wait --wait-timeout 300
```

检查健康、Web UI 版本窗口的运行版本，并检索一条旧记忆。`docker restart` 和 `docker start`
仍使用原镜像，升级需要重建容器。版本提示不会自动更新、迁移，也不证明兼容性。

回滚使用记录的旧版本/digest，不能使用此时的 `latest`。只有数据库 Schema 和 Embedding Profile
兼容时才可复用当前数据；否则将对应完整备份恢复到新目录，并使用匹配镜像，备份后的变更不包含在内。
root 创建的数据交给旧版非 root 镜像前可能需要准备权限。回滚后再检查健康、版本和旧记忆检索。

## 下一步

在实际宿主机映射端口打开 Admin UI，进入 **Help → 开始使用**，完成连接 AI、为记忆分组、
设置记忆规则。把 `/read-me` 地址交给 Agent，默认是 <http://127.0.0.1:1314/read-me>。
容器内部始终监听 `1314`；使用其他映射时，只替换宿主机端口。MCP 客户端配置与记忆规则的详细说明只在 Web Help 维护。

- 如更希望使用 Python，见[源码安装与升级](source.zh-CN.md)。
- [版本编号规则](../../README.zh-CN.md#版本编号规则)与[版本检查与网络](../../README.zh-CN.md#版本检查与网络)。

备份与更新策略的背景说明见 Web UI 版本窗口及各目标版本的发布说明。
