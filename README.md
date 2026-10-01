# remember-me

本项目提供可通过 MCP 使用的本地记忆服务，支持 HTTP（Streamable HTTP 与兼容 SSE）和 stdio。

从仓库根目录运行 `bin/start.sh` 启动 HTTP 服务。服务默认监听 1314 端口；配置、数据库和
模型缓存位置及 MCP 客户端接入方式见[运维手册](docs/operations/runbook.md)。

开发计划与系统设计文档位于 [`docs/`](docs/)。全仓库开发约定见 [`AGENTS.md`](AGENTS.md)。
