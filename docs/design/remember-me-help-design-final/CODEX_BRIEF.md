# Codex 落地说明（Help 页面）

本文件用于指导 Codex 在 Remember Me Web 管理界面中实现新的 Help 页面。

## 1. 先读这些文件

按以下顺序阅读：

1. `README.md`
2. `HELP_DESIGN.md`
3. `HELP_CONTENT.md`
4. `HELP_IMPLEMENTATION.md`
5. `mockups/*.png`
6. `backgrounds/*.png`

说明：
- Markdown 文档是**权威规范**。
- `mockups/*.png` 仅用于理解风格、布局、密度与 Light / Dark 视觉方向。
- **不要机械照搬 mockup 中不准确的细节**，例如：
  - 不要新增独立的文档侧边栏；继续使用 Remember Me 现有全局 Sidebar。
  - 不要新增与现有 Shell 不一致的顶部导航。
  - 不要在 Help 页面中实现真实的配置控件或危险操作按钮。

## 2. 新增图片文件与建议放置位置

请将以下两张背景图加入项目静态资源目录：

建议目录：`web/static/img/help/`

- `backgrounds/help-hero-bg-light-tech.png`
- `backgrounds/help-hero-bg-dark-tech.png`

建议复制到项目后的目标路径：

- `web/static/img/help/help-hero-bg-light-tech.png`
- `web/static/img/help/help-hero-bg-dark-tech.png`

## 3. 图片使用位置

### 首页 About Remember Me 区域

仅在 Help 首页顶部的 **About Remember Me** 品牌介绍区使用这两张背景图。

#### Light 主题
- 使用：`help-hero-bg-light-tech.png`
- 位置：About 卡片背景层
- 建议：
  - `background-position: right center;`
  - `background-size: cover;`
  - `background-repeat: no-repeat;`
  - 在文字区域上保持足够留白和对比度
  - 整体视觉应轻、淡、科技感，不要压过正文

#### Dark 主题
- 使用：`help-hero-bg-dark-tech.png`
- 位置：About 卡片背景层
- 建议：
  - `background-position: right center;`
  - `background-size: cover;`
  - `background-repeat: no-repeat;`
  - 保持深色卡片与发光蓝色波纹的对比，但不要让它变成“宣传页”

## 4. 实现要求（重要）

### 保持简洁
Help 是个人软件的使用指南，不是企业级文档站点：

- 优先解释必要概念
- 不追求面面俱到
- 不堆砌图标、卡片和大段技术说明
- 让用户感觉“容易理解、容易上手、不吓人”

### Agent-first
必须清楚表达：

- 记忆主要由 **LLM agent** 在工作过程中产生、检索和使用
- Web 界面主要用于：查看、管理、整理、修正和排查
- 不要让用户误以为这是一个需要手工录入为主的知识库

### About 区域背景图的边界
背景图只是气质增强，不是主体内容：

- 不要使用大面积高对比度炫技效果
- 不要让图片影响正文可读性
- 不要把整个 Help 页面都铺成海报风
- 仅限首页 About 区域使用

## 5. 实施建议

### Phase A
- 只读检查现有 Help 实现与设计包差异
- 输出实施计划
- 不修改代码

### Phase B
- 实现首页信息架构
- About 区域接入背景图
- 实现 Getting Started 三张入口卡片
- 保持 Shell 一致

### Phase C
- 实现 Connect AI / Memory / Configuration 等详情页
- 实现简化版 Reference 与 Troubleshooting
- 完成 Light / Dark 适配

### Phase D
- 收尾与验收
- 响应式检查
- 无障碍与键盘焦点检查
- 文案与链接复核

## 6. mockup 文件说明

- `mockups/help-home-light-dark-concept.png`
  - 首页视觉参考
- `mockups/connect-ai-light-dark-concept.png`
  - Connect AI 详情页视觉参考
- `mockups/memory-light-dark-concept.png`
  - Memory 详情页视觉参考
- `mockups/configuration-light-dark-concept.png`
  - Configuration 详情页视觉参考

再次强调：
- **文案、结构、功能边界以 Markdown 文档为准**。
- mockup 主要帮助理解视觉风格，不是像素级实现蓝图。
