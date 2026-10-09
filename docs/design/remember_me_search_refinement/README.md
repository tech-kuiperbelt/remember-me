# Remember Me MCP — Search UI Refinement

本包仅用于 **Memories 页 Search / Filters / Advanced / Results toolbar** 的视觉与响应式布局精修，直接交给 Codex 实施。

## 文件
- `IMPLEMENTATION.md`：范围、设计决策、组件与布局、边界情况、验收清单、执行限制。
- `reference/current-search-expanded.png`：实施前真实界面（Advanced 展开）。
- `reference/approved-visual-direction-light-dark.png`：已确认的视觉方向（Light/Dark、Advanced 折叠/展开）。效果图仅用于视觉参考，并非精确 DOM / 文案 / 控件行为规范。

## Codex 提示词（可直接复制）

请先阅读本包 `IMPLEMENTATION.md` 以及 reference 两张截图。只实施 Memories 页面搜索区域及结果工具栏的视觉与响应式布局精修。以现有源代码与真实截图为功能真相，视觉效果图仅作风格参考。务必保留 Semantic/Lexical/Anchor、Namespace、Categories、Keywords、Search、Clear search、Advanced 参数及 Apply、记录数量、Sort、New Memory 的现有行为和可访问性。不修改接口/参数含义/筛选算法/状态持久化/业务逻辑，不改 Memory Card、Shell、Detail Drawer。先检查组件和相关测试，再做最小范围改动；完成后运行现有相关测试、lint/build（如项目支持），给出改动文件列表、验证结果及未解决问题。
