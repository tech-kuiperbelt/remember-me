# Remember Me — Configuration UI Design Package

状态：前端已实施，待视觉验收（2026-10-11）。见[实施计划与验证记录](../../project-management/plans/50.11-configuration-ui-refinement.md)。

## 文件
- `UI_DESIGN.md`：信息架构、最终功能清单与视觉交互要求。
- `INTERACTION_AND_STATE.md`：状态门控、各工具操作、校验及安全要求。
- `IMPLEMENTATION_PLAN.md`：按阶段实施和验收要求，强调先确认 API 契约。
- `assets/layout-reference-light-dark.png`：最终选定的**视觉风格与密度参考**，不是字段功能真值。
- `assets/settings-early-reference.png`、`assets/maintenance-early-reference.png`：早期视觉探索，仅供对比。

## 对 Codex 的重要说明
**文档高于图片。** 最终参考图错误地出现了 gRPC Port、Data Storage、Logging 等未批准功能，也遗漏了部分真实字段。不得照图添加功能。图内维护状态及某些按钮可用态也不准确，必须按本文档真实状态门控实现。

既有入口为左侧「配置」（`page-ops` / `ops.js`）。保留主 Shell，不新增一级导航。现有业务逻辑与后端 API 不得擅改；发现前后端契约缺口，先报告并确认。
