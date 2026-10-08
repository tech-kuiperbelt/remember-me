# Backlog TODO

本文件是 remember-me 项目的开放待办清单。完成项移入 `backlog-done.md`。

## 当前待办

### 前端工作

- [50] 前端重构与 UI 调整：模块拆分、桌面布局、主题基础、Shell 视觉、Memory List 及 Memory Detail 已完成。详情的完整浏览器矩阵 112/112、隔离 Admin／SQLite 联调与真实服务只读复核已有通过记录，宽屏调整后三项定向回归通过。独立代码 review 未发现明确回归，reviewer 的浏览器复验受沙箱 socket 限制。用户已授权提交，完成记录见 [50.6.3](plans/50.6.3-memory-detail-coding-plan.md)。**当前待办：后续页面调整及布局稳定后的结构复核**，见[路线图](plans/50-frontend-refactoring-and-ui-roadmap.md)。

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
