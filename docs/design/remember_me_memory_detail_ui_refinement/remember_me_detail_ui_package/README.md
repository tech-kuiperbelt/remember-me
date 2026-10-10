# Remember Me — Memory Detail UI refinement package

Design package for existing **View / Edit / Create** Drawer. The user reviewed and approved the direction. This is a targeted polish of current UI, **not a redesign**.

## Contents
- `UI_DESIGN.md` — agreed UX, visual semantics, interaction decisions.
- `IMPLEMENTATION.md` — phase-by-phase Codex coding plan and acceptance tests.
- `reference/current_view.png` — existing View.
- `reference/current_edit.png` — existing Edit.
- `reference/current_create.png` — existing Create.
- `reference/current_technical_details.png` — existing Technical details.
- `reference/approved_mockup.png` — approved conceptual overview of View/Edit/Create and states.

## Important caution
The conceptual mockup changes some example data/navigation and omits existing controls. **DO NOT blindly recreate mockup pixels or copy its field set.** Existing screenshot + written decisions govern the work. Preserve Debug, More, Copy draft, technical data, namespace logic, current Drawer width, and original app layout.

## Paste into Codex
请阅读本 ZIP 的 `README.md`、`UI_DESIGN.md` 和 `IMPLEMENTATION.md`，先分析现有 Memory Detail View/Edit/Create 的实现、识别已满足项与缺失项，再分阶段修改并验证。只修改 Detail Drawer 范围，不动 Memory List、Search、Shell、后端接口和数据模型。效果图仅为视觉参考，书面规格优先。请不要擅自提交代码或重启生产服务。完成后列出修改文件、验证结果和任何偏差。

## 用户最终边界（2026-10-09）

Edit／Create 均不提供原始 metadata JSON 编辑；Create 仅六个专用字段，已有其他 metadata 保留并只读展示。Summary 仍可留空。效果图里的 Other metadata 输入和必填星号不采用。

2026-10-10 补充确认：Create／Edit 的 Categories、Keywords、Anchors 拆成三个平级独立字段，始终纵向排列，统一标题、对齐和 24px 间距，保留现有选择及添加方式。
