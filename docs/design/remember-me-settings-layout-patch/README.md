# Remember Me — Settings 布局增量修改包

日期：2026-10-12
目标：只调整 Configuration → 常规设置（Settings）Tab 的内部布局，消除 Service Configuration 左侧大面积留白。

## 交付内容
- `SETTINGS_LAYOUT_PATCH.md` — 目标布局与视觉约束
- `CODEX_IMPLEMENTATION.md` — Codex 工作范围、实施步骤及验收项

## 使用方式
将本目录交给 Codex，要求先阅读两份文档，再检查现有实现，按限定范围修改。本文档是现有 Configuration 设计包的增量补丁；既有功能和接口契约继续有效。

## 核心边界
不修改 Maintenance Tab、Shell、路由、后端 API、状态门控、配置语义、验证规则或保存行为。不得添加 Mockup 中不存在于现有功能的配置项。
