# 按操作系统选择默认数据目录

日期：2026-09-30。状态：已实现并部署（代码、583 项 pytest、浏览器回归及真实迁移均完成）。

## 1. 目标与默认规则

将默认数据目录从项目内的 `data/` 改为当前用户的持久化目录。配置文件固定为所选数据目录下的 `config.jsonc`，不增加独立的配置文件位置参数。

| 操作系统 | 默认数据目录 |
|---|---|
| Linux | `~/.remember-me/`（用户指定） |
| Windows | `%LOCALAPPDATA%\remember-me\` |
| macOS | `~/Library/Application Support/remember-me/` |

Windows 未设置 `LOCALAPPDATA` 时，使用当前用户主目录下的 `AppData/Local/remember-me`。主目录通过操作系统提供的当前用户主目录解析，不依赖启动时的工作目录。

参考：[Windows LocalAppData 已知文件夹](https://learn.microsoft.com/windows/win32/shell/knownfolderid)、[macOS Application Support 目录](https://developer.apple.com/documentation/foundation/url/applicationsupportdirectory)。

目录选择优先级：`--data-dir` > `MEMORY_DATA_DIR` > 操作系统默认目录。显式指定的相对目录仍按项目根目录解析，保持现有调用兼容性。

## 2. 数据与路径约束

- `config.jsonc`、SQLite 主库、JSONL 副本、audit 及轮转文件、锁和恢复侧文件、`logs/`、`tmp/`、本地模型缓存均以所选数据目录为根。
- 运行期内部使用规范化的绝对路径；存储路径 API 等展示保持相对于数据目录的路径。
- 保留现有路径越界和符号链接越界检查。
- 配置字段、JSONC 注释保留、旧 `config.json` 兼容规则不变；数据目录不写入配置文件，避免读取配置前需要配置自身的问题。
- 程序资源、安装文件、主机 `.env`、只读外部输入及独立 Ollama 管理的模型仍遵循现有例外。

## 3. 实现方案

### 3.1 统一默认目录解析

在配置层提供无副作用的默认目录解析函数，服务入口、`Settings`、管理 CLI 与迁移命令共用。导入模块、帮助和只读状态查询不创建目录或配置文件。

正常启动时按最终选定目录读取或创建 `config.jsonc`。同步调整默认数据库、审计和缓存路径，避免低层默认值把服务数据写回项目 `data/`。

本次定义并测试三种系统的路径规则。现有 `fcntl` 写锁和 Bash 启动脚本的 Windows 原生适配另行处理；路径规则通过测试不代表整个服务已支持 Windows 原生运行。

### 3.2 迁移命令与环境配置

- 迁移源、目标的默认目录选择复用同一规则；迁移旧项目目录时显式传 `--source-data-dir data`。
- 目标为当前系统默认目录时，成功迁移后从项目 `.env` 删除 `MEMORY_DATA_DIR` 覆盖项，让后续启动采用默认规则。
- 目标为自定义目录时保留显式覆盖项；数据库、副本、审计和缓存路径继续以目标数据目录为根。
- 沿用现有复制、文件集合及 SHA-256 校验、原子发布、迁移日志、目标冲突拒绝和崩溃恢复机制。
- 更新启动帮助、配置状态说明和运维文档。

## 4. 当前 Linux 服务迁移

用户已确认本次实现时一并将当前项目 `data/` 迁移到 `~/.remember-me/`。当前 `.env` 的 `MEMORY_DATA_DIR=data` 会覆盖新默认值，必须在迁移成功后移除。

1. 完成代码与测试，使用迁移命令只读预览，确认目标不存在冲突、可用空间足够。
2. 记录迁移前记忆数量、active embedding profile、配置和双存储健康状态。
3. 进入维护模式并优雅停服，等待运行期资源和写锁释放。
4. 显式指定源、目标执行迁移，复制并校验数据库、副本、日志、audit 和模型缓存；保留配置值与注释以及原有 profile。
5. 成功后移除旧目录覆盖，确认启动进程没有继承旧 `MEMORY_DATA_DIR`，以 `bin/start.sh` 无参数启动。
6. 核验端口、配置页面、实际文件位置、记忆数量和 profile、SQLite/JSONL 一致性及真实 MCP 检索。

保留旧项目 `data/` 作为回退副本，不自动删除。失败时使用迁移日志恢复配置与环境覆盖，明确选择原目录启动；恢复前确认目标实例已退出，避免两个目录同时对外提供写服务。

## 5. 验收

- 模拟 Linux、Windows、macOS 默认规则，覆盖 Windows 环境变量缺失的回退。
- 覆盖命令行与环境变量优先级、显式相对路径以及帮助/只读操作无文件副作用。
- 覆盖新默认目录中的配置读写、运行期路径归根和越界拒绝。
- 覆盖迁移到默认目录时删除覆盖项、自定义目录时保留覆盖项、目标冲突和失败恢复。
- 全量 pytest 与浏览器回归通过；实际迁移后完成在线验收。

## 6. 实施记录

- 配置层新增无副作用的 `default_data_dir()`，`Configuration.load`、`Settings.from_env`、管理 CLI 与迁移命令共用；低层默认数据库/审计/副本路径改为以数据目录为根，不再回落到项目 `data/`。
- 迁移到系统默认目录时成功后删除 `.env` 的 `MEMORY_DATA_DIR`，自定义目录保留覆盖项。
- 583 项 pytest 与浏览器回归通过。
- 生产已从仓库 `data/` 迁移到 `~/.remember-me/`：优雅停服后复制并校验 SQLite、NDJSON、audit、日志与模型缓存；旧 `data/` 保留回退。重启后 `bin/start.sh` 无参数启动，端口 1314、语言 zh-CN，active profile `ep_6134c0b15d2bb471`、1125 条记忆、`pending=0`、`in_sync=true` 均与迁移前一致；在线 MCP 语义检索正常。未提交 Git。
