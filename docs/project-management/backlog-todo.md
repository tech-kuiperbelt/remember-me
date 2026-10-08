# Backlog TODO

本文件是 remember-me 项目的开放待办清单。完成项移入 `backlog-done.md`。

## 当前待办

### 前端工作

- [50] 前端重构与 UI 调整：模块拆分、桌面布局、主题基础、Shell 视觉及 Memory List 已完成。2026-10-08 用户确认列表阶段收尾，浏览／共用筛选／时间过滤／Semantic、Lexical、Anchor 搜索／列表视觉与 review 修复均已落地；最终浏览器矩阵 79/79、真实 Chrome 复核通过。列表归档见 [50.5 总体记录](plans/50.5-memories-workspace.md)及 [50.5.9 修复记录](plans/50.5.9-memories-search-modes.md)。**下一阶段：Memory Detail**，待明确详情展示与交互方案后实施。前端路线仍开放，布局稳定后的结构复核继续保留，详见[路线图](plans/50-frontend-refactoring-and-ui-roadmap.md)。

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
