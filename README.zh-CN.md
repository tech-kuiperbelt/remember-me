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

Remember Me 在本机运行。请选择一种安装方式；两种方式都把配置、记忆、日志和缓存
保存在你控制的数据目录中。

- **SOURCE — Git + Python 3.10+：**[源码安装与升级](docs/installation/source.zh-CN.md)。
  使用 Sentence Transformers + BAAI/bge-m3，CPU 推理；模型权重在首次使用时下载。
- **Docker — Linux AMD64：**[Docker 安装与升级](docs/installation/docker.zh-CN.md)。
  镜像内置 BGE-M3 并在 CPU 上运行，无需 Python 或独立 Embedding 服务。

安装指南涵盖安装与启动、停止、数据目录、备份与恢复、升级与回滚。服务启动后，打开
<http://127.0.0.1:1314/admin/console>（Web UI），Agent 接入使用 `/read-me`，然后继续阅读
[连接并启用 Agent 记忆](#连接并启用-agent-记忆)。

### 升级与回滚

<a id="source-updates"></a>
**SOURCE：**[安装、升级与回滚](docs/installation/source.zh-CN.md#upgrade-and-rollback)

<a id="docker-updates"></a>
**Docker：**[安装、升级、备份与回滚](docs/installation/docker.zh-CN.md#upgrade-and-rollback)

更新前请阅读所有相关版本的 Upgrade Notes / Breaking Changes，并保留旧版本和停机后的
完整数据目录备份。

## 版本编号规则

Remember Me 使用 `主版本号.次版本号.补丁号`。按**次版本号**区分发布系列：
偶数次版本号（如 `1.2.x`、`1.4.x`）是正式发布系列；奇数次版本号（如 `1.3.x`）
是开发／预发布系列。补丁号不受奇偶限制，例如 `1.2.1` 可以是正式版本。

这是 Remember Me 的项目约定。当前发布 Workflow 只允许偶数次版本号的正式版本；
奇数系列及 `1.3.0-rc.1` 这类预发布版本暂不支持发布。版本检查以 GitHub 最新正式
Release 为目标。SOURCE 检出可能包含开发代码，显示版本仍只读取随包的
`release.jsonc`。上述版本号均为规则示例，不表示已有对应版本可下载。

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
