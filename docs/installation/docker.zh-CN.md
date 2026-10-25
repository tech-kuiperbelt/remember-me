# Docker 安装与升级（Linux AMD64）

安装并运行 Remember Me 官方容器。若希望用 Python 从源码安装，见[源码安装与升级](source.zh-CN.md)。

**发布前状态：官方 GHCR 镜像尚未公开。** 以下命令为正式发布准备；`1.0.0`
是示例版本，不代表已可下载。公开后请使用实际发布的完整版本。本地验收改用
`REMEMBER_ME_IMAGE=remember-me:phase3.2-ready-fixed`，不能据此认定匿名下载通过。
两种方式都无需安装 Python 或独立 Embedding 服务：**镜像内置 BGE-M3，CPU 推理，无需配置或下载模型。**

以下 Bash 命令面向本机 rootful Linux Docker Engine、AMD64；Compose 方式还需
Docker Compose 插件。Rootless、Docker Desktop、SELinux 和网络文件系统需独立验证权限与平台。
镜像解包后约 3.5 GB，请为拉取、升级、备份和模型运行预留空间与内存；最低内存要求尚未确定。

## 准备一个实例

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

## 方式一：docker run

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

## 方式二：Docker Compose

将**同一版本 Tag** 中的[官方 compose.yaml](../../compose.yaml) 下载到工作目录，无需克隆源码：

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

## 连接、检查和停止

健康后访问 <http://127.0.0.1:1314/admin/console>。首次加载可能需要数分钟；如修改宿主机端口，
同步修改浏览器与 MCP 地址中的 `1314`。Streamable HTTP 使用 `/mcp`，传统 SSE 使用 `/sse`；
见[客户端配置](../../README.zh-CN.md#连接并启用-agent-记忆)。

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

## 权限、备份和恢复

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

<a id="upgrade-and-rollback"></a>
## 手动升级和回滚

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

## 下一步

- 如更希望使用 Python，见[源码安装与升级](source.zh-CN.md)。
- [连接并启用 Agent 记忆](../../README.zh-CN.md#连接并启用-agent-记忆)，配置 MCP 客户端并验证连接。
- [版本编号规则](../../README.zh-CN.md#版本编号规则)与[版本检查与网络](../../README.zh-CN.md#版本检查与网络)。

备份与更新策略的背景说明见 Web UI 版本窗口及各目标版本的 Release Notes。
