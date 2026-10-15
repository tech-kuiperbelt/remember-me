# Remember Me · Help 分阶段实施与验收

> 状态：供 Codex 执行的实施约束。**本设计包不代表已经修改应用代码**。

## 0. Codex 工作规则

1. 先阅读 `HELP_DESIGN.md`、`HELP_CONTENT.md`、当前 `help-page.md`、现有 HTML/CSS/JS 和真实页面；对照真实 UI 后列出实施差异。
2. **未经用户明确要求，不修改代码**。如果用户已明确要求实施，遵守下面的阶段限制；不要自行重构 Help 以外的模块。
3. 遇到设计文案与当前实现冲突，先说明差异，保持实际行为为准；尤其不要新增不存在的指标或功能。
4. 保留 Light/Dark 主题、现有 Shell 与 Sidebar、离开编辑保护、可访问性、复制反馈和响应式能力。
5. 每阶段修改前说明将触及的文件，完成后给出验证证据；不要将视觉图中的虚构控件当成需求。

## 1. 文件与作用域

现有 Help 相关位置（以当前仓库为准）：

- `web/index.html`：Help 结构、介绍文案、复制文本与内容；
- `web/static/app.css`：Help 页面样式与响应式；
- `web/static/js/help.js`：页面初始化、路由或 Tab、URL 生成、复制与反馈；
- `web/static/js/main.js`：Help 页面注册及入口。

**严格范围**：Help 展示层及必要导航。不要改变 MCP `/read-me` 接口、MCP 工具、后台存储、模型下载行为、统计 API、Configuration 操作语义。若实施必须碰后端，暂停并先给出契约变更说明等待确认。

## 2. 分阶段计划

### 阶段 A：梳理现状与内容映射（只读）

- 核实当前 Help DOM、CSS 约定、JS 初始化与路由方式、现有 `/read-me` URL 生成机制、复制反馈机制。
- 核实各页面实际可见字段、Stats Memory / LLM Retrieval 指标、Configuration Settings / Maintenance 控件。
- 输出旧 Help 内容 → 新导航目标的逐项映射；确保现有复制行为、警示内容、手动工具说明没有悄然丢失。
- 明确不能保证的点（例如运行时可访问 URL 是否一定适用于 Agent）；对用户只给准确提示。

**验收**：映射完整；没有提出不存在的功能；全部差异被记录。

### 阶段 B：首页与内部导航

- 将原来两个 Tab 的长文档入口调整为首页 + 详情页。
- 首页四区域：About（完整文案/引文）、Getting Started（三卡片）、Reference（四个轻量入口）、Troubleshooting（轻量入口）。
- 详情页顶部支持返回 Help 的面包屑；保留现有 Sidebar，不出现第二侧边栏。
- 处理 Back/Forward、刷新、从其它应用模块切换至 Help 的状态；沿用编辑离开保护。

**验收**：点击、返回、刷新和窄屏导航有效；四区域层级清楚，首页不过长、不过密。

### 阶段 C：Getting Started

- Connect AI：以运行时 `/read-me` URL + **复制地址**为主；显示三步流程。现有完整接入请求作为次级操作；地址必须由运行时生成，不写死示例。
- Namespace：用简单项目例子解释「同组跨 Agent」，强调「不是权限隔离」。
- Memory Rules：组合复制保存请求与八条规则，按需展开手动设置和单独复制规则。
- 对已有 Agent 接入说明的外部链接及可复制内容做回归测试。

**验收**：复制内容正确且不丢规则；Agent 访问边界说明准确；用户无需先学习 SSE/stdio 才能开始。

### 阶段 D：Reference 四页

- Memory：最先说明 Agent 是主要记忆生产者，Web 是管理窗口；示例字段、CRUD 和筛选说明；手动新建不喧宾夺主。
- Retrieval：三检索方式对照、搜索/筛选/无结果处理、分数不可比。
- Stats：仅使用真实指标；Memory 与 LLM Retrieval 对应现有页面，不出现 Hit@1、Hit@5。
- Configuration：Settings / Maintenance + 简洁 Danger Zone 提醒；Help 中不得有可执行配置更改或危险操作按钮。

**验收**：易懂、无技术堆砌，所有术语有可找到的解释；不重复渲染一套完整业务页面。

### 阶段 E：Troubleshooting、精简和视觉收尾

- FAQ 按 Connection & Setup / Memory & Search / Service & Configuration 分组。
- 新增「搜索无结果」「模型未就绪」「模型下载失败」，不要混为一谈。
- 依据已有组件进行 Light/Dark 和响应式视觉精简：少卡片、少图标、正文舒适，警示色克制。
- 真实截图人工检查，删掉多余说明和重复入口。

**验收**：FAQ 对真实常见问题有用；手机端无横向溢出；Light/Dark 结构一致；操作入口不误导。

## 3. 统一验收清单

### 产品与内容
- [ ] 用户打开 Help 后知道 Remember Me 为 **LLM Agent** 保存和检索长期记忆。
- [ ] 用户知道 Web UI 主要负责查看、管理和修正，不必主要靠手动录入。
- [ ] 保留完整品牌理念及《西部世界》引文与来源。
- [ ] Connect AI 推荐路径是 **复制 `/read-me` → 告诉 Agent → 实际验证**。
- [ ] `/read-me` 明确不是 MCP 端点；跨机器的 `localhost` 风险说明准确。
- [ ] Namespace 和 Semantic / Lexical / Anchor 有通俗示例。
- [ ] Memory Rules 的八条规则与保存请求完整，组合复制结果正确。
- [ ] Stats 指标与实际 UI 完全一致，不出现不存在的 Hit@1/Hit@5。
- [ ] Configuration 只读解释，Danger Zone 不提供执行按钮。
- [ ] FAQ 包含模型下载失败（含代理）与模型未就绪这两个不同问题。

### 布局与交互
- [ ] 首页 About 双栏（窄屏单栏），三张 Getting Started 卡片，Reference 轻量入口，Troubleshooting 轻量入口。
- [ ] 详情页仅用现有 Sidebar + 面包屑，无新固定文档侧边栏。
- [ ] 长段正文不随宽屏无限拉宽；操作模块可以局部双栏。
- [ ] Light/Dark 文案、布局和交互一致。
- [ ] URL、保存请求、规则复制有可见成功与失败反馈。
- [ ] Tab/折叠/链接可用键盘操作并保留焦点样式。
- [ ] 返回、刷新、编辑离开保护按现有应用规范工作。
- [ ] 页面没有额外的企业式搜索框、复杂层级导航或过密小卡片。

### 回归与安全
- [ ] 现有 Memories/Stats/Configuration 页面行为没有被改变。
- [ ] MCP 接口、配置、模型管理、维护模式没有发生后端行为变更。
- [ ] 未引入无鉴权局域网访问的错误安全承诺。
- [ ] `npm`/前端测试、静态检查及实际浏览器验收遵循仓库可用脚本，不虚构验证结果。

## 4. 交付要求

Codex 每阶段回报：修改文件、主要改动、测试或人工检查结果、未解决事项、下一阶段建议。最后提供 Light/Dark 实际页面截图（首页、Connect AI、Memory、Configuration；如有布局问题再补其他详情），与设计意图逐项对比。

**参考图优先级低于规范和真实 UI**：生成的 mockup 可能包含独立导航、装饰控件、与实际功能不符的细节，应主动忽略。
