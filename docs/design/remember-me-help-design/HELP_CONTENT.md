# Remember Me · Help 页面文案与内容清单

> 版本：2026-10-14。面向用户的中文短文案草案，优先自然易懂。凡涉及精确运行时参数、枚举和错误状态，实施时核对当前代码及真实页面。

## 首页

### 标题

**Help · 使用帮助**

副标题：**让 AI 记住你的项目和工作，下次交流不用从头解释。**

### About Remember Me

**记忆，是你最重要的财产。**

记忆是智力的基石。我们凭借记忆理解来路、形成判断，让经历沉淀为智慧。与 AI 一同走过的思考、选择和发现，也值得被认真留存，成为下一次出发的起点。

Remember Me 为你保存这份积累。每一条记忆都清晰可见，可以查看、修改，也可以导出、带走。你决定留下什么，也决定交给谁使用。

工具会更迭，你的积累应当始终属于你。换一个 AI 助手，仍能接续已有的记忆，让长久的理解随你同行，让选择的自由始终握在自己手中。

> “你的记忆，编织成了你的自我。”  
> “Your memories are woven into your identity.”  
> — Robert Ford，HBO《西部世界》第一季第 9 集 *The Well-Tempered Clavier*（中文意译） · [台词出处](https://www.springfieldspringfield.co.uk/view_episode_scripts.php?episode=s01e09&tv-show=westworld-2016)

此处品牌文案沿用现有 Help 的已确认版本，原则上不为节省卡片空间而任意删除思想递进。引文出处可以按现有实现链接，发布前最好核对台词文本与归属。

### Getting Started

- **连接 AI**：把接入说明地址告诉 Agent，让它帮你完成连接。
- **给记忆分组**：为项目取一个 Namespace，让不同 AI 使用同一份项目记忆。
- **设置记忆规则**：告诉 Agent 什么时候检索、保存和更新记忆。

### Reference

**想了解某个功能？** Memory · Retrieval · Stats · Configuration

### Troubleshooting

**遇到问题？** 查看连接、检索、服务配置和模型下载的常见问题。

---

## Getting Started / Connect AI

**连接你的 AI**

把下面的接入说明地址发送给支持 MCP 的 AI Agent，让它根据说明协助你完成配置。

- **主区域**：`<运行时获取的完整 /read-me URL>` + **复制地址**。
- **第一步**：把这个地址发送给 Codex、Claude Code、OpenCode 等具备相应能力的 Agent，请它阅读说明并连接 Remember Me。
- **第二步**：按 Agent 提示确认必要操作；如果配置需要重新加载，请重新打开会话。
- **第三步**：请 Agent 读取实际记忆数量，确认 MCP 可用。数量为 0 也可能是正常的初始状态。

简短提示：`/read-me` 是配置说明，**不是 MCP 连接端点**。Agent 需要能够访问这个地址。另一台电脑、容器或云端 Agent 未必可以使用当前的 `localhost` 地址。

**次级操作：复制完整接入请求**（保留现有行为，运行时插入真实 URL）：

```text
请读取 <当前服务的完整 /read-me 地址>，帮我连接已独立运行的 Remember Me，保留已有配置。需要我操作时，逐步告诉我怎么做；完成后读取记忆数量验证。若不支持或连接失败，请说明原因。
```

**按需展开**：连接不上怎么办？／需要自己启动服务？／查看详细接入说明。不要在主流程铺开 Streamable HTTP、SSE、stdio 的实现细节；详细内容在 `/read-me`。

## Getting Started / Namespace

**给记忆起个分组名**

Namespace 就是记忆的**分组名**。给当前项目取一个容易辨认的名字，例如「网站改版」。同一个项目即使换用不同 Agent，也可以沿用同一个 Namespace；不同项目可以使用不同名字。

举例：Codex 和 OpenCode 都使用「网站改版」，就可以围绕这个分组检索和保存项目记忆。

**注意**：Namespace 用于组织记忆，不是权限隔离；首次使用新名字时按服务或 Agent 的提示确认。

## Getting Started / Memory Rules

**让 AI 记住使用规则**

连接成功只说明 Agent 能访问记忆服务。要让它在日常工作中主动、合理地使用记忆，还需要把规则写进该项目会持续加载的长期指令中，而不是只在当前聊天里说一次。

1. 在 Agent 中打开你的工作项目。
2. 复制「保存请求（含记忆规则）」并发送给 Agent；把 `<项目 Namespace>` 换成自己的分组名。
3. 按 Agent 提示完成保存或重载，然后询问当前使用的 Namespace，并试着检索项目记忆。

**主按钮**：复制保存请求（含记忆规则）。

**保存请求**（与既有文案保持一致）：

```text
请把下面的记忆规则保存为当前工作项目的长期指令。找到当前 AI 工具会加载的指令文件，没有就创建；保留原文，合并重复规则。分组名未填写时先问我，不要设为所有项目的默认值。完成后告诉我文件完整路径和生效步骤；无法保存时，说明原因并给出手动操作方法。
```

**完整记忆规则**（与当前 Help 文案保持一致）：

```text
使用 Remember Me 管理项目长期记忆。Namespace：<项目 Namespace>。未指定 Namespace 时先询问，不擅自设置跨项目默认值。

1. 主动回忆：在理解问题、分析方案和执行任务时，自然联想相关记忆；信息不足时，尝试不同线索继续检索，必要时读取全文。找到足够依据或继续检索已无明显价值时停止，不编造历史信息。
2. 复用经验：需要探索环境、寻找操作方法或反复尝试前，先回忆是否存在已有做法，核实后优先复用。下一步方法明确可靠时无需机械检索。
3. 决策与行动：重要决策前回忆历史经验、既有约束和失败教训；执行提交、部署、删除等关键操作前，检查相关的长期行为规则（如 action-rule），即使操作方法已经明确。
4. 主动积累：用户明确要求记住或长期沿用的信息应及时保存。工作中发现重要决策、经验、解决方案或可复用知识时主动保存；阶段结束或上下文压缩前检查是否遗漏。单纯要求总结不等于要求持久保存。
5. 提炼知识：以独立知识点组织记忆，保存有价值的事件、知识和抽象原则，必要时引用相关记忆 ID。区分事实、推测和待验证结论，注明依据与适用范围，避免过度泛化。自行归纳的长期行为规则须经用户确认后保存。
6. 去重与演进：写入前检索已有记忆，判断新增、更新或跳过。保留有独立价值的事件，合并重复知识，持续完善已有原则；修正过时或错误结论时保留必要的原因与证据。
7. 组织与反馈：保存、检索时使用本项目 Namespace，不擅自跨项目混写。合理填写 Categories、Keywords 和 Anchors，优先复用合适的现有分类。实际使用或评估检索结果后，通过 report 如实评价有用程度；无法判断时不强求评分。
8. 事实与安全：以当前事实和用户明确指令为准，核实记忆的适用性，遇到重要冲突时说明。不保存密码、密钥或无关隐私；删除、清空或覆盖记忆前取得明确授权。工具失败时如实说明，不声称未完成的操作已经成功。
```

组合复制内容为「保存请求 + 完整八条规则」；同时保留查看与单独复制规则的次级操作。

**按需展开**：手动设置工具说明（Claude Code / Codex / OpenCode / DeepSeek Harness / 豆包）。工具列表与具体支持情况应依据当前产品和现有文案，不对不支持 MCP 的产品做虚假承诺。

---

## Reference / Memory

**让 AI 积累记忆，让你掌握记忆。**

Agent 可以在日常工作中通过 MCP 保存重要决定、经验、发现和问题解决方案，并在以后检索使用。实际是否主动记录取决于 Agent 的指令和行为。Web 界面主要让你**查看、搜索、修正和整理**这些积累；手动新建记忆是补充能力，不是主要工作方式。

**理解一条记忆**：

| 字段 | 用户易懂的解释 |
|---|---|
| Summary | 这条记忆讲什么，方便快速浏览 |
| Content | 具体内容和必要背景 |
| Namespace | 属于哪个项目或分组 |
| Categories | 这条记忆是什么性质，最多 3 项 |
| Keywords | 涉及哪些主题或概念 |
| Anchors | 可准确定位的标识，如类名、接口、异常码，最多 5 项 |

**你可以做什么**：浏览、筛选、查看详情、编辑修正、删除；必要时手动添加一条记忆。摘要留空时可由系统根据正文生成（遵从实际表单状态）。

**筛选怎么组合**：同一维度多个值采用 OR，不同维度采用 AND；日期按更新时间筛选，也适用于浏览与搜索。避免将分组误解为安全隔离。

## Reference / Retrieval

**找回你想要的记忆**

| 方式 | 怎么理解 | 典型使用 |
|---|---|---|
| Semantic | 按意思找 | 问「之前怎样处理数据库死锁？」 |
| Lexical | 按正文词语找 | 搜 `ORA-00060`、英文标识符、版本号 |
| Anchor | 按已保存的标识找 | 搜明确写入 Anchors 的类名或接口名 |

Lexical 对连续中文匹配能力有限；中文和概念检索优先尝试 Semantic。Anchor 只匹配明确保存的 Anchors，不等于全文搜索。

简要解释：Search/Enter 提交检索，Clear 清除检索并回到浏览；Advanced 设置结果数量和分数门槛；日期、Namespace 和其它过滤条件可能让结果减少。无结果时先查看筛选范围，再考虑放宽条件或切换模式。不同搜索方式的原始分数**不能直接比较**。

Web 检索供用户手动查找；Agent 的检索由它实际调用的 MCP 工具与规则决定。

## Reference / Stats

**Stats 用于观察记忆积累与检索活动，不是给用户布置的分析任务。**

**Memory**：概览中的 Memories / Namespaces / Categories / Keywords / Anchors；Namespace Distribution；Categories、Keywords、Anchors 的 Top N、Unique Count、Coverage，以及点击项目查看对应记忆。

- Unique Count：有多少种不同的值，不是有多少条记忆。
- Coverage：有多少比例的记忆至少填写了这一类字段。

**LLM Retrieval**：Searches、Rated Searches、Feedback Coverage、Search Activity、Search Modes（Semantic / Anchor / Lexical / Meta）、Average Rating（-10 到 +10）、Positive / Neutral / Negative、Namespace 和 7 / 30 / 90 Days 过滤。

- Feedback Coverage：得到评分的检索所占比例，**不是检索准确率**。
- Average Rating 只基于有评分的反馈；评分区间的精确分类门槛应以实现代码为准，不要推断。
- 当天时间窗口可能是不完整的一天；Memory 和 LLM Retrieval 是不同统计视图。

**不要加入**目前产品没有的 Hit@1、Hit@5 等指标。

## Reference / Configuration

**Settings：日常设置**。解释 Appearance、HTTP Port（重启生效且启动参数 `--port` 优先）、Language（当前只是保存偏好，非完整翻译）、Localhost Only、Debug Mode、Auto-download、HTTP/HTTPS Proxy；明确各项的即时、刷新或重启生效差异。

**Maintenance：维护工具**。解释 Maintenance Mode 的手动进入与退出，Model Management、Data Transfer（JSONL 导入/导出）、Data Management（Sync Backup、Migrate Namespace）、Server（Restart / Shut Down）。浏览维护页面**不等于**进入维护模式。

**Danger Zone**：JSONL Recover 可能覆盖当前数据；Clear Memories 会清除数据。仅说明用途、影响、备份和确认要求；Help 中不放真正执行按钮。

**区别**：Import 用于导入记录；Recover 用于恢复备份，可能替换/覆盖现有数据。具体数据行为与可撤销性以当前实现为准。

---

## Troubleshooting / FAQ

采用三个小分组的折叠问答；回答以 2–5 句为目标，必要时链接对应详情页。

### Connection & Setup

**Agent 无法打开 `/read-me`？** 确认服务正在运行，且 Agent 能访问这个地址。本机 `127.0.0.1` 不适用于另一台机器或远程云端环境；如 Agent 不能读取 URL，可手动将说明内容转给它。

**配置好了但仍然无法调用 MCP？** 请 Agent 检查传输方式与连接配置是否生效，必要时重开会话，并实际验证 `tools/list` 和 `stats`。只修改配置文件不等于连接成功。

**服务怎么启动？** 在程序目录使用已有 `bin/start.sh` 的 HTTP 启动方式；具体启动方式以现有安装说明为准。已有 HTTP 服务使用同一数据库时，不要再启动 stdio 写入实例。

### Memory & Search

**为什么保存按钮不可用？** 检查是否做了实际修改、必填项/格式是否合法，以及服务当前是否允许写入。

**为什么搜索不到记忆？** 先检查 Namespace、日期和其它筛选条件，再放宽条件或改用其它检索模式；Anchor 只查已有 Anchors。

**模型未就绪怎么办？** 配置页仍可能可用，但涉及模型的记忆工具可能返回 `model_not_ready`；先到 Configuration → Maintenance 查看模型状态与准备操作。

### Service & Configuration

**模型下载失败怎么办？** 先检查下载网络、代理设置、自动下载选项和模型状态；在 Configuration → Maintenance → Model Management 查看进度并重试。若仍失败，根据显示的错误信息排查网络、磁盘或模型初始化问题。关闭自动下载不等于可以不准备模型。

**为什么看不到 Debug / Audit？** 检查 Debug Mode 设置并按提示保存刷新；正常使用通常不需要开启。

**为什么代理地址不完整？** 界面会保护已保存的代理凭据；不要把显示为空误以为没有保存。具体编辑/保留规则按当前 Settings 页面说明。

**为什么另一台设备打不开 Web？** 检查 Localhost Only、服务实际监听地址、网络连通性；对局域网开放服务时要注意当前服务没有鉴权，仅适用于可信网络。

**顶部显示状态不可用？** 先检查服务进程、访问地址和端口；刚重启时允许等待服务恢复，再根据实际错误进一步排查。

---

## 术语索引（不单设庞大术语百科）

Namespace → Getting Started / Namespace；Summary / Content / Categories / Keywords / Anchors → Reference / Memory；Semantic / Lexical / Anchor / Score → Reference / Retrieval；Unique Count / Coverage / Rated Searches / Feedback Coverage → Reference / Stats；Maintenance Mode / Import / Recover → Reference / Configuration。
