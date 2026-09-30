# Backlog TODO

本文件是 remember-me 项目的开放待办清单。完成项移入 `backlog-done.md`。

## 当前待办

- [48] 后端重构：阶段 1–4 已完成（隔离阻塞业务与取消/停服；收回导入/恢复协调职责；补齐存储/provider 契约；声明工具与路由策略并合并响应公共逻辑），659 pytest + 浏览器回归通过；阶段 5 待实现（合并 CLI 重复代码并优化完整健康核验读取）。详见 [`plans/48-backend-refactoring.md`](plans/48-backend-refactoring.md)。

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
