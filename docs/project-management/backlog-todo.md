# Backlog TODO

本文件是 remember-me 项目的开放待办清单。完成项移入 `backlog-done.md`。

## 当前待办

### 前端工作

- [50] 前端重构与 UI 调整：第一阶段（保持行为的 HTML/CSS/JS 分离、公共能力提取、状态归属梳理、ES modules + 事件绑定）已实施并验证，见 [50.1 实施记录](plans/50.1-frontend-module-split.md)；第二阶段第一项 [50.2 管理台桌面布局](plans/50.2-admin-desktop-layout.md) 已实施并验证，其余布局/功能意见逐项明确；第三阶段布局稳定后复核组件化需求。详见 [路线图](plans/50-frontend-refactoring-and-ui-roadmap.md)。

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
