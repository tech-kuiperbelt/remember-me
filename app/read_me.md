# remember-me MCP — 配置说明

本页由 remember-me 服务在 `GET /read-me` 提供，供 LLM agent 与运维人员读取。
读完即可自行配置 remember-me MCP，并了解它能做什么。

## Quick Start for Agents

首次接入先完成以下五步，工具使用细节可在连接成功后查阅。

1. **检查传输支持。** 按当前客户端支持的 MCP 传输选择第 2 节的接入方式；本页为 Codex 推荐 `{base}/mcp`，为 OpenCode 保留现有 `{base}/sse`。`/read-me` 是说明页，不是 MCP 端点。
2. **优先复用已有 HTTP 服务。** 同一数据库已有服务运行时，不要另启 stdio 写入实例。
3. **保留已有配置。** 先检查现有 MCP 注册，只新增或调整 Remember Me 对应条目，保留其他服务配置，避免重复注册。
4. **验证实际连接。** 配置生效并完成 MCP `initialize` 后，调用 `tools/list`，再调用 `stats` 读取实际记忆数量（0 也正常）；失败时说明原因，不要声称接入成功。
5. **无权限时交代操作。** 若无法修改配置，向用户提供需要编辑的配置文件位置、具体改动和生效步骤，并说明哪些验证尚未完成。

## 0. 服务地址

- 本服务地址（自引用，端口跟随服务启动参数）：`{base}`
- 默认仅本机：监听 `127.0.0.1`（`localhost` 与之等价）。配置 `local_only: false` 后监听 `0.0.0.0` 并允许局域网访问全部 HTTP 接口；本页自引用仍显示 `127.0.0.1`，局域网客户端请把主机替换为服务所在机器的局域网地址。
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
- 限制：默认仅本机 loopback 可直连（`local_only: true`，Host 白名单为 `127.0.0.1` / `localhost`）。设置 `local_only: false` 时监听 `0.0.0.0`，局域网可访问全部 HTTP 入口，且 `/mcp` 不再校验 Host/Origin；服务无鉴权，仅限可信网络。

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
  数据目录默认是当前用户目录（Linux `~/.remember-me/`，Windows `%LOCALAPPDATA%\remember-me\`，
  macOS `~/Library/Application Support/remember-me/`），可传 `--data-dir <DIR>` 覆盖；
  配置文件固定为该目录下的 `config.jsonc`。
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

`search_lexical` 与 `search_anchor` 均支持可选的公共筛选与分数门槛：`categories` / `keywords`（精确匹配，维度内 OR、跨维度 AND，不做语义解析）、`updated_after` / `updated_before`（与 `search_semantic` 相同的带时区 ISO 8601、微秒严格开区间）、以及 `min_score`（原始 BM25 最低分，接受有限非负数字、无统一 0–1 范围）。筛选与分数门槛都在 Top-K 截取前生效，同分按记忆 ID 升序稳定排序；返回原始 `-bm25` 分数（不再舍入到六位小数），与其它检索分数不可比较。

`search_anchor` 只匹配记忆声明过的 **anchors**（写入时通过 `metadata.anchors` 提供 0–5 个有辨识度的标识符：类名、API、异常码、配置键、版本号等），不搜索正文、keywords 或 categories。三种检索的原始分数互不可比；`search_anchor` 无结果时改用 `search_lexical` 或 `search_semantic`。写入引导：新增记忆或修改正文时，从内容中选择 0–5 个有辨识度的 anchors，保留原拼写，不要编造或直接复制泛化的 keywords，没有就传 `[]`。keywords 表示内容概念，categories 表示记忆性质，anchors 标识具体对象。显式 anchors 替换旧列表，`[]` 清空；正文实质变化且未重新提交 anchors 会清空旧值，仅改其他元数据或空白等价正文时省略 anchors 则保留原值。

新增记忆或修改正文时，为记忆选择 1–3 个相关的 `metadata.categories`；优先复用 `stats.category_counts` 中符合含义的分类。只修改其他元数据时可以保留原分类。已知目标关键词或分类时，`search_semantic` 可用 `keywords` / `categories` 先缩小候选集，再按正文余弦相似度排序：精确索引值优先，未命中的值会在同一维度与 namespace 内尝试语义解析，实际映射见 `filter_resolution`。探索性问题可省略过滤条件；`search_meta` 的同名过滤条件只做精确匹配。

`search_semantic` 也接受 `updated_after` / `updated_before`：带时区的 ISO 8601 时间（支持 Z），严格筛选更新时间大于／小于边界的候选，再计算相似度和 Top-K；同时传入时 after 必须早于 before。省略或空白表示无该边界，保留微秒精度。概念问题限定时间窗口用此检索；只想按更新时间查看最近记忆用 `search_meta`。相对窗口可参考 `stats.server_time`。`search_meta` 和旧 Admin 列表接口采用同样的时间校验及微秒边界规则；无时区、非法时间或 after >= before 均拒绝。Admin Memories 的日期条件同样同时适用于浏览与搜索。

`list_keywords` 提供 browse/literal/semantic 三种模式与可选 `namespace`，还可传 `categories`（精确匹配，多个为 OR，与 namespace 用 AND），用于查看某命名空间下、带这些分类的记忆实际使用的关键词词表；不传 `categories` 时行为与词表查询不变。

权威清单以客户端连上后的 MCP `tools/list` 为准；本页只给概要。

## 5. 快速验证

1. 客户端完成 MCP `initialize`。
2. 调用 `tools/list`，应返回上述 11 个工具。
3. 调用 `stats`，应返回记忆总数与命名空间统计。

## 6. 注意事项

- 本页只读；访问它不会修改任何客户端配置。
- 服务关闭中本页返回 503。
- 地址与端口以本页顶部为准，端口跟随服务启动参数。
- 远程 / 跨容器接入默认不支持；仅在 `local_only: false` 时同一局域网可达，且无任何鉴权，需自行确保网络可信。
