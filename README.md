# remember-me

本项目提供可通过 MCP 使用的本地记忆服务，支持 HTTP（Streamable HTTP 与兼容 SSE）和 stdio。

Remember Me 自有源码采用 [Apache-2.0](LICENSE)，版权署名为 shenyu1997。
再分发时请遵守许可证及 [NOTICE](NOTICE) 中适用的归属保留要求；第三方组件与素材另见
[第三方声明](THIRD_PARTY_NOTICES.md)。不要求派生产品在 UI 中突出署名，也不要求派生产品整体开源。

## 快速开始

从仓库根目录创建环境并安装基础依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

默认使用本机 Ollama 的 bge-m3。先安装并启动 Ollama，再执行
`ollama pull bge-m3`，最后运行 `bin/start.sh`。如已有数据，提供方和模型必须匹配
数据库的活动 embedding profile；切换提供方需按运维手册离线 reindex/activate。

默认管理界面为 http://127.0.0.1:1314/admin/console，接入说明为
http://127.0.0.1:1314/read-me。Codex 使用 Streamable HTTP 地址
http://127.0.0.1:1314/mcp，OpenCode 可使用兼容 SSE 地址
http://127.0.0.1:1314/sse。具体客户端配置见[运维手册](docs/operations/runbook.md)。

## 模型与下载

模型权重、tokenizer 和模型缓存**不随源码仓库分发**，也不由项目的 Apache-2.0
重新授权；模型与推理依赖适用各自上游条款。

| 提供方 | 默认模型／来源 | 获取方式 |
|---|---|---|
| ollama | Ollama bge-m3 | 见上方快速开始 |
| sentence_transformers（可选） | Hugging Face BAAI/bge-m3 | 安装 requirements-sentence-transformers.txt 并配置提供方；按缓存及下载设置加载 |
| fastembed（可选） | intfloat/multilingual-e5-large；FastEmbed 0.8.0 映射到 Qdrant ONNX 制品 | 安装 requirements-fastembed.txt 并配置提供方；来源随模型及 FastEmbed 版本变化 |

本地提供方优先读取缓存。缓存缺失且 `auto_download_model` 允许时才下载；
HTTP 模式会进入模型准备维护状态，成功后需手动退出维护。
本地提供方可通过 `bin/manage embedding download-model` 显式允许下载缺失模型。
模型来源及转换制品的许可边界见[第三方声明](THIRD_PARTY_NOTICES.md#independently-downloaded-models)。

根目录 `docker-compose.yml` 是旧 OpenMemory 调研配置，**不是 Remember Me 部署方式**；
本仓库没有提供 Remember Me Docker 镜像发布流程。

## 配置与开发

`bin/start.sh` 会加载 gitignored 的本机 `.env`（如存在），默认采用 dual 存储。
服务默认监听 1314 端口；配置、数据库及模型缓存位置见[运维手册](docs/operations/runbook.md)。

需要界面元素 ID 调试时，在配置页启用 Debug，保存后刷新生效，默认关闭；
不再支持 `--debug` 启动参数。

开发计划与系统设计文档位于 [docs/](docs/)。全仓库开发约定见 [AGENTS.md](AGENTS.md)。
