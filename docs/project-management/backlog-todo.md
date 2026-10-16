# Backlog TODO

本文件是 remember-me 项目的开放待办清单。完成项移入 `backlog-done.md`。

## 当前待办

### 前端工作

- [50.13] Neutral 岩石灰主题已实施，待视觉验收：第三套浅色系蓝灰配色、即时切换与偏好保存，见[实施记录](plans/50.13-neutral-theme.md)。
- [50.12] Help 首页与八个主题详情页已实施，含品牌介绍、接入、参考与排错，待内容与视觉验收。见[实施记录](plans/50.12-help-page.md)。

- [50.11] 配置页 Settings / Maintenance 与响应式布局已实施，待用户视觉验收。见[实施计划](plans/50.11-configuration-ui-refinement.md)。

- [50] 前端重构与 UI 调整：模块拆分、桌面布局、主题基础、Shell 视觉、Memory List、Memory Detail 及 [50.7 新增抽屉](plans/50.7-memory-create-drawer.md) 已完成。2026-10-09 完成 Debug 入口和 Edit／Preview 标签页反馈调整，用户验收并授权提交；验证范围见各实施记录。**当前待办：后续页面调整及布局稳定后的结构复核**，见[路线图](plans/50-frontend-refactoring-and-ui-roadmap.md)。
- [50.15] 中英文界面国际化（W1–W6 已完成并提交，W7/W8 进行中）：`config.language`（zh-CN/en）驱动管理界面语言，保存后刷新生效、无需重启。已完成并提交：框架与首屏本地化、全局 Shell、记忆列表/检索、配置与维护、记忆抽屉与值选择器、统计（含 `Intl` 格式化）、Help 手册（`de09865`、`e287917`、`fc627c3`、`66f5557`）。词表 757 键（中英成对）。剩余：W7 错漏/术语复核（残留在 `app.css` 英文长版式）、W8 全矩阵 zh+en 验收（`NxM@scale` 视口用例在 HEAD 即失败，属既有限制）。详见[计划](plans/50.15-chinese-english-i18n.md)。

### 其他开放项

- [23] keywords 人工合并（后续）：讨论人工选择标准词、预览影响范围、合并历史关键词及别名处理。黑名单暂缓；暂不做独立准入词表、审批或停用等治理。[36] 已实现词表查询，不包含人工合并。
- [19] memory priority/置顶字段。
- [15] 通过共享索引/实体建立记忆间关联网络。
- [16] 关键词收敛与低频词表裁剪的目标冲突。
- [9] keywords 浓度/词频排序及自动提取研究（暂不实现）。

## 已明确不做

- [35] namespace alias、改名与历史变体合并：2026-09-16 决定不做。[28] 只保留存在性治理（未知 namespace 拒绝 + 确认码），不引入 alias、改名或历史变体（如 `atos-next-master`/`atos-next`）合并。

## 维护约定

- 以本文件为项目管理 TODO 的权威来源，不把完整 backlog 正文写入记忆系统。
- 完成项移入 `backlog-done.md`，并在提交记录中注明 commit 和验证结果。
- MCP 工具变更必须同步考虑 audit。
