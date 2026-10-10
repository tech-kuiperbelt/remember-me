# Remember Me — Memory Detail Drawer UI Design

状态：设计已确认，供 Codex 实施。范围仅为 Memory Detail Drawer（View / Edit / Create）。

## 0. 设计优先级
1. **已有产品行为与真实界面截图** 是功能和信息架构基准。
2. 本文与 IMPLEMENTATION.md 中明确的已确认决策优先于效果图。
3. 效果图只是风格、信息层次、交互状态的视觉参考；不要照搬示意图中的假数据、英文字段、导航、列表布局或删减按钮。
4. 延续现有 Light/Dark 主题、设计 token 和组件；不要引入新依赖或重做 Shell、Memory List、Search。

## 1. 三模式统一原则
- View、Edit、Create 共用 Drawer 的基础布局、Header、字段排版、标签语义、技术细节风格。
- View 以阅读为主，Edit/Create 以表单录入为主；保留现有字段顺序和具体功能。
- Drawer 当前固定宽度保持不变，不支持拖拽调宽；内容区独立垂直滚动，Header 固定顶部。
- 不添加额外分组卡片或 Basic information / Classification 标题；保留现有字段组名。

## 2. Header
- 标题：View/Edit 为当前 Summary，**单行截断**，正文 View 仍显示完整 Summary；Create 为 `New Memory`。
- View/Edit 标题下保留现有 Namespace 与时间；Create 不显示记录时间。
- Header 下方一条浅色分隔线，操作按钮靠右，整体紧凑；窄空间时优先保护按钮可用性与标题截断，不能遮盖按钮。
- **Header 固定顶部**，Drawer 内容独立滚动。
- **保持当前按钮及其顺序、含义，不移除功能**：View 为 Edit / More / Debug / Close；Edit 为 Cancel / Save changes / Copy draft / Debug / Close；Create 为 Cancel / Create memory / Copy draft / Debug / Close。
- 主操作按钮：View/Edit `Edit`、Edit `Save changes`、Create `Create memory` 用主题蓝色；Cancel/More/Copy draft/Debug 为次级样式；Close 为低视觉权重的 `×` 图标。注意 `Edit` 是 View 模式主操作，`Save changes` 是 Edit 模式主操作。

## 3. View 阅读区
- 按顺序显示 Summary → Content → Categories → Keywords → Anchors → Technical details。
- Summary 正文约 14px/500；Content 14px/400、行高 1.6；字段名约 12px 次级色；字段组间距 20–24px（在现有设计 token 内取近似值）。
- Content 使用普通 Drawer 背景，不添加独立灰色容器；完整保留原文段落、换行和 Markdown 渲染语义，**不得改写原文**。
- Metadata 与 List 同一语义配色：Categories 低饱和淡蓝底；Keywords 中性浅灰底；Anchors 透明底+浅灰细边框。标签不触发快捷筛选。
- Detail **显示完整 Metadata**，无 List 的两行省略 / `+N` 限制。
- 空字段：View 中显示 `—` 以利检查完整性；本规则仅作用于读取视图，不影响数据存储。

## 4. Edit / Create 表单
- 现有顺序不变：Namespace → Summary → Content → Categories & Keywords → Anchors；Edit 保留只读 Technical details。
- Summary：自动增高多行输入框，默认 2 行，最多 5 行，之后内部滚动；Create/Edit 共用相同样式；保留现有 Summary 校验规则。
- Content：保留原 Edit / Preview tabs；编辑容器默认约 240px，随内容增长最多 500px，超出内部滚动；两个 tab 遵循相同高度约束，尽量避免切换跳动。
- Preview 只读，实时展示当前**未保存**的内容；与 View 共用 Markdown 渲染规则。切换 tabs 不丢失输入，并尽量恢复编辑光标、选区和滚动位置；不增加所见即所得编辑。
- 分类/关键词编辑：保留既有标签、移除 `×` 和 Add 操作；Enter 或 Add 均可添加，Enter 不提交整个表单。添加时去除首尾空白、跳过重复值；若后端规则不允许前端这样规范化，先指出冲突，不要静默改写持久化语义。
- Anchors：Enter / Add 均可添加；**保留原始大小写及后端空白/重复判定规则**，不可自动大小写转换、分词或额外规范化；标签移除方式不变。
- Edit/Create 均不提供 Other metadata 原始 JSON 编辑入口；Create 只录入现有六个专用字段。已有其他 metadata 不随编辑丢失，在 Technical details 中只读展示；复制完整 Raw metadata 仍保留。
- Create 空状态：允许清晰、低视觉权重 placeholder；无输入时不显示预先触发的红色必填错误。

## 5. 校验 / 保存 / 退出
- Create 初次打开时不显示校验错误；用户修改并失焦、或尝试提交后，针对对应字段就地显示错误。提交失败时聚焦第一个非法字段；修正后及时移除提示。沿用已有必填与后端规则。
- 保存中：主按钮 Loading 且防重复提交；保存成功转到对应 Memory 的 View；失败不丢失草稿、不关闭 Drawer，提示可理解的错误。
- 有真实未保存更改时，离开 Edit/Create 需要确认 `Continue editing` / `Discard changes`。没有修改直接退出。不要把 Markdown tab 切换、打开/折叠面板等纯 UI 状态当作草稿变更。
- 三种关闭方式：右上角 `×`、Esc、**点击 Drawer 外的遮罩/区域**。View 直接关闭；Edit/Create 有未保存修改时确认；保存中禁用这些关闭途径。
- 外侧点击只执行关闭流程，不顺带触发被点击的另一张 Memory Card。关闭后 List 的搜索、筛选、滚动位置、Last Viewed 状态原样保留。

## 6. Technical details（View / 已有详情）
- 默认折叠，展开后分为 **基本技术信息** 与 **Raw metadata**；保留现有字段、ID、日期及数据。
- 基本技术信息：两列键值对，字段名统一宽度，包含 ID / Created / Updated；ID 右侧放通用 Copy 图标，Hover 显示 `Copy ID`。
- 时间界面显示本地 `YYYY-MM-DD HH:mm:ss`，Hover/title 提供完整原始 ISO 时间（含精度和时区），不改变任何底层时间值。
- Raw metadata：只读 JSON 代码区域，等宽字体、保留缩进、低饱和语法高亮；浅色/深色主题一致；自适应高度，**最大 240px**，过长内部滚动，长行可横向滚动；不引入 Monaco/CodeMirror。
- JSON 右上角 Copy 按钮/图标，复制完整原始 Metadata，与可视滚动区域无关；Copy ID / JSON 成功反馈为短暂对勾，不弹无谓 Toast；失败遵循现有系统反馈。
- 注意：Metadata 原始 JSON 是否含 categories/keywords/anchors，以现有模型和数据为准，禁止为了视觉展示调整存储结构。

## 7. 外观与可访问性
- 延续已采用的淡蓝/蓝灰色系和 Light/Dark 主题语义 token，保持充足文字对比度。
- 图标仅用于零/低学习成本含义：关闭、复制、展开收起等；别给 Categories / Keywords / Anchors 添加新的概念图标。
- 所有交互按钮具备 Hover/Focus、键盘操作与可访问名称；Copy icon 必须有 aria-label/title。校验提示应与对应字段关联。
- 不改 Drawer 宽度、不额外引入常态投影、不重做卡片或搜索栏。

## 2026-10-09 用户最终澄清

Edit 和 Create 均不直接编辑 metadata JSON。Create 移除原有 Other metadata 输入，Edit 继续只读展示完整 Raw metadata；已有任意其他 metadata 通过现有更新合并契约保留。Summary 允许留空，效果图中的必填星号不采用。分类／关键词保留现有下拉候选和新增操作，只补 Enter 支持，不改成示意图中的独立录入框。
