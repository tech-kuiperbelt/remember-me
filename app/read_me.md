# remember-me MCP — 配置说明

本页由 remember-me 服务在 `GET /read-me` 提供，供 LLM agent 与运维人员读取。
读完即可自行配置 remember-me MCP，并了解它能做什么。

## 0. 服务地址

- 本服务地址（自引用，端口跟随服务启动参数）：`{base}`
- 主机固定为 `127.0.0.1`；`localhost` 与之等价。
- 模型缓存缺失时，配置页面 `{base}/admin/console` 仍可访问，可设置下载代理、查看进度并重试。
  此时记忆工具返回 `model_not_ready`，请先完成模型准备；模型就绪后由操作者退出维护再写入。

## 1. 概述

remember-me 是一个基于 MCP 协议的记忆服务器，提供记忆写入、语义检索、元数据检索、
更新/删除、统计与关键词发现等能力。三种传输共享同一个服务实例与同一份存储。

## 2. 接入方式

三种方式并存，按场景选择。

### 2.1 Streamable HTTP（推荐：多客户端并发共享）

- 适用场景：多个客户端（例如 opencode + Codex）同时连接同一服务。
- 传输：MCP Streamable HTTP（单端点；POST 返回 JSON；本服务不提供独立 GET SSE 流）。
- 地址：`{base}/mcp`
- Codex 配置要点（`~/.codex/config.toml`）：

```toml
[mcp_servers.memory-server]
url = "{base}/mcp"
```

  若原条目使用 `command`/`args` 桥接（如 `mcp-remote`），请替换该条目，避免同一服务重复注册。
- opencode 配置要点（`opencode.jsonc`）：保持现有 `/sse` 连接不变。

```jsonc
"memory-server": { "type": "remote", "url": "{base}/sse", "enabled": true }
```

  只有确认支持 Streamable HTTP 的客户端（例如 Codex）才指向 `{base}/mcp`；不支持时继续用 `{base}/sse`。
- 限制：仅本机 loopback 可直连（Host 白名单为 `127.0.0.1` / `localhost`）。

### 2.2 旧版 HTTP+SSE（保留兼容）

- 适用场景：现有使用 `type: remote` + `/sse` 的客户端（例如当前 opencode）。
- 地址：`{base}/sse`（消息回传端点为 `/messages/`）。
- 限制：被 Codex 视为 legacy 传输，新接入不推荐。

### 2.3 stdio（单客户端本地）

- 适用场景：只使用一个客户端，且与仓库同机。
- 命令：

```toml
[mcp_servers.memory-server]
command = "<REPO>/bin/start.sh"
args = ["--stdio"]
```

  将 `<REPO>` 替换为本仓库的绝对路径；`bin/start.sh` 会加载 `.env` 与 `.venv`。
  无参数启动默认 HTTP，端口取自数据目录中的 `config.jsonc`（默认 1314）；stdio 必须显式传 `--stdio`。
  数据目录默认 `<REPO>/data`，可传 `--data-dir <DIR>`；配置文件固定为该目录下的 `config.jsonc`。
- 限制：**无法支持多个客户端同时连接**；且当同一主库已有 HTTP 服务在运行时，
  不能再启动 stdio 写入进程（单主库写锁）。

## 3. 推荐

- 多客户端并发共享 → **Streamable HTTP**（`{base}/mcp`）
- 单客户端本地 → stdio
- 旧版 HTTP+SSE → 仅保留兼容

## 4. 功能概览

- 工具（11 个）：`add`、`search_semantic`、`search_lexical`、`search_anchor`、`search_meta`、`get_details`、`update`、`delete`、`stats`、`report`、`list_keywords`
- 资源（2 个）：`memories://stats`、`memories://list`

四种搜索有结果时，可用 `get_details(memory_ids=[...], source_search_id=<search_id>)` 读取全文；使用结果后，再用 `report` 和同一个 `search_id` 反馈有用程度。空结果按返回 hint 放宽条件或更换搜索方式，无需对空结果调用 `report`。`get_details` 的 `memory_ids` 始终是数组，单条记忆也传一个元素的数组。

`report` 只接受当前服务进程中最近 24 小时内、仍在保留范围内的非空搜索结果；每个评分 ID 必须属于该次结果，评分须为 -10～10 的整数，同一 ID 只能评一次。无效反馈返回 `response_code=invalid_report` 且不计为有效评分。可选的 `feedback_source`（`llm` / `human` / `test`）和 `feedback_purpose`（`task` / `evaluation` / `test`）是调用方声明；audit 另存服务端观察到的传输与客户端上下文，不能仅凭声明认定为已核实的人工标注。未声明的旧客户端反馈归入 `unclassified`。

Ops 进入运维状态时，`add`、`update`、`delete` 暂停并返回 `response_code=maintenance_mode`；请稍后重试。检索、详情、统计和资源读取继续服务；Recover 切换数据库的短暂窗口可能返回 `maintenance_switching`，同样稍后重试。

`search_lexical` 面向英文/代码类文本（类名、异常码、符号、版本号），且 BM25 分数与 `search_semantic` 的余弦分不可比较；连续中文在 FTS 中不切分，中文与概念检索请用 `search_semantic`。

`search_anchor` 只匹配记忆声明过的 **anchors**（写入时通过 `metadata.anchors` 提供 0–5 个有辨识度的标识符：类名、API、异常码、配置键、版本号等），不搜索正文、keywords 或 categories。三种检索的原始分数互不可比；`search_anchor` 无结果时改用 `search_lexical` 或 `search_semantic`。写入引导：新增记忆或修改正文时，从内容中选择 0–5 个有辨识度的 anchors，保留原拼写，不要编造或直接复制泛化的 keywords，没有就传 `[]`。keywords 表示内容概念，categories 表示记忆性质，anchors 标识具体对象。显式 anchors 替换旧列表，`[]` 清空；正文实质变化且未重新提交 anchors 会清空旧值，仅改其他元数据或空白等价正文时省略 anchors 则保留原值。

新增记忆或修改正文时，为记忆选择 1–3 个相关的 `metadata.categories`；优先复用 `stats.category_counts` 中符合含义的分类。只修改其他元数据时可以保留原分类。已知目标关键词或分类时，`search_semantic` 可用 `keywords` / `categories` 先缩小候选集，再按正文余弦相似度排序：精确索引值优先，未命中的值会在同一维度与 namespace 内尝试语义解析，实际映射见 `filter_resolution`。探索性问题可省略过滤条件；`search_meta` 的同名过滤条件只做精确匹配。

权威清单以客户端连上后的 MCP `tools/list` 为准；本页只给概要。

## 5. 快速验证

1. 客户端完成 MCP `initialize`。
2. 调用 `tools/list`，应返回上述 11 个工具。
3. 调用 `stats`，应返回记忆总数与命名空间统计。

## 6. 注意事项

- 本页只读；访问它不会修改任何客户端配置。
- 服务关闭中本页返回 503。
- 地址与端口以本页顶部为准，端口跟随服务启动参数。
- 远程 / 跨容器接入不在当前支持范围（Host 白名单仅 loopback）。
