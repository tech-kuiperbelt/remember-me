# [44] 模型下载与代理配置

2026-09-30 实现；随本次提交归档、生产服务尚未重启。

后续启动行为由 [45](45-model-bootstrap-maintenance.md) 调整：HTTP 缺少模型时先开放
配置入口并强制维护，下载/验证失败不再关闭 Web 服务；stdio/CLI 保留同步行为。

## 配置与行为

`<data-dir>/config.json` 新增 `auto_download_model`（默认 true）及
`proxy: {"enabled": false, "url": ""}`。旧文件缺失字段使用默认值，首次创建写入全部字段。
代理只接受 HTTP/HTTPS 地址，允许 URL 凭据；配置状态隐藏凭据，数据迁移完整保留字段。
已为现有 `data/config.json` 补齐默认值。

Sentence Transformers / FastEmbed 先仅从本地缓存加载，无缓存且允许自动下载时再联网；
依赖缺失、模型不受支持、权限及其他加载错误不会按缓存缺失重试。显式本地模型路径须自行恢复。
缓存缺失但禁止下载、下载失败均阻止启动，并给出缓存位置和维护命令提示。
模型和 profile 身份保持原规则，下载成功后仍须通过启动校验与 warmup。

配置入口清除大小写系统代理变量；默认直连，启用时统一 HTTP/HTTPS 代理，保留本地直连。
修复 Ollama 地址解析及 ALL_PROXY 绕过；模型缺失提示 `ollama pull`，不自动拉取。
配置覆盖旧 provider/HF 离线变量，加载使用 `local_files_only`，禁用 Xet 独立传输。
低层直接调用仍兼容旧 offline 参数。手动 `bin/manage embedding download-model` 明确允许
缓存缺失时下载，即使自动下载关闭；缓存及代理仍共用配置。

## 验证

- 539 项 pytest 全量通过（7 条依赖弃用提示）；覆盖配置校验、旧配置兼容、宿主代理/离线变量覆盖、迁移保留、缓存优先、
  自动及手动下载、失败处理、Ollama 本地绕过和模型缺失提示。
- 子进程内用本地代理服务器验证 Hugging Face HTTPX 和 FastEmbed 使用的 requests 通道。
- 实际 BAAI/bge-m3 缓存独立加载并 warmup，通过 socket 连接拦截确认网络尝试 0；
  profile `ep_6134c0b15d2bb471`、维度 1024 不变。
- 真实 Sentence Transformers 空缓存且禁止自动下载：明确报错，网络尝试 0。
- 未重新下载生产模型，未修改记忆库或 profile；现有服务需重启使用新逻辑。
