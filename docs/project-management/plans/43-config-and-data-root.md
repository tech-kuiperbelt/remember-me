# [43] 配置文件与统一数据目录

## 已确认的规则（2026-09-30）

数据目录通过 `--data-dir`、`MEMORY_DATA_DIR` 或默认项目 `data` 选择，命令参数优先；相对目录按项目根目录解析。用户进一步明确不需要独立配置文件位置：配置固定在选中目录的 `config.json`，只包含 `port`（默认 1314）和 `language`（默认 `zh-CN`，也接受 `en`）。首次正常启动创建默认文件，导入模块和帮助不写文件。语言字段暂不驱动翻译。默认启动 HTTP，`--port` 临时覆盖端口，stdio 必须显式传 `--stdio`；修改配置后重启生效。

## 实现

- 服务、管理命令和直接离线 CLI 共用配置及路径解析。环境变量、CLI 输出以数据目录为根；数据库、副本、audit、轮转、锁和恢复侧文件、日志、上传/导出临时文件、回填产物及本地模型缓存都受根目录约束。越界路径和指向根外的符号链接拒绝使用。内部路径为绝对路径，Stats 和存储路径 API 展示相对路径。
- 默认模型目录为 `models/fastembed`、`models/sentence-transformers`；Hugging Face、Torch、通用依赖缓存也归数据目录。程序和安装资源、`.env`、只读外部输入及独立 Ollama 服务管理的模型文件为例外。
- `bin/manage config migrate --dry-run` 只读预览。正式迁移要求源库和目标库均无活动写者；不覆盖目标文件。复制后校验文件集合和 SHA-256，模型缓存的链接解引用为根内文件；成功才切换 `.env` 和配置，保留源文件用于回退。更换目录用 `bin/manage --data-dir NEW config migrate --source-data-dir OLD`。
- 迁移日志记录发布进度和旧配置。异常回退；进程崩溃后目标服务拒绝启动，再运行相同迁移命令恢复，然后重新预览/迁移。
- 菜单“运维”显示为“配置”；本次不新增网页编辑配置接口。

## 验证与部署

新增配置/迁移测试覆盖首次创建、非法配置、目录解析、路径越界、模型链接、写锁竞争、目标冲突、失败回退及真实子进程崩溃恢复。传输测试覆盖配置端口、`--data-dir`、`--port` 覆盖和显式 stdio。

全量 pytest 517 项通过，浏览器回归通过。部署前预览没有目标冲突：数据库与 audit 已在 `data`，需要迁入约 4.3 GB 的 `/var/tmp/rm-model-cache` 和约 6 MB 的项目 `logs`。

2026-09-30 已完成生产迁移：通过维护模式优雅停服，复制、校验模型缓存和日志并更新 `.env`；新服务以 `bin/start.sh` 无参数启动，读取 `data/config.json`，端口 1314、语言 zh-CN。Sentence Transformers 使用迁入的缓存离线加载；1106 条迁移前记忆及 active profile `ep_6134c0b15d2bb471` 均不变，`pending=0`、`in_sync=true`。在线管理页面显示“配置”，存储路径 API 返回 `memories.db` / `memories.jsonl`，实际 MCP 语义检索成功。旧日志和 `/var/tmp/rm-model-cache` 保留供回退，没有删除共享缓存。

## 后续目录清理（2026-09-30）

按用户要求删除 `data/anchor-backfill` 的 134 个历史回填产物，以及 `data/archive` 中旧 JSON 存储的 451 条历史记忆备份和校验清单。当前 SQLite 主库、NDJSON 副本和已应用的 anchors 不属于清理范围。`data/tmp` 保留作为运行期临时目录，目前包含模型依赖生成的 `torchinductor_yu` 缓存。
