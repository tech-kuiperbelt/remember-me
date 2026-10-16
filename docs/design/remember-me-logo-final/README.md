# Remember Me Logo Final Package

## 已落地方案（2026-10-16 用户确认）

- 页面左上角使用无圆框海马主标，保留 Remember Me · MCP Server 文字；48px 展示盒，使用 64/128px 资源。
- 浏览器 favicon 最终使用**无圆框海马**，随 Light / Neutral / Dark 同步切换。运行时 SVG 嵌入原始 128px 主标，使用 `viewBox="14 14 100 100"` 增大主体占比。
- 实际运行资源位于 [web/static/img/brand](../../../web/static/img/brand/)，实现提交为 `a0b6d6b`。
- 本目录保留完整原始设计交接素材。下方关于圆形徽章 favicon、Help、README、PWA 和 `public/brand` 的内容属于原始用途建议，实际接入范围为页头与浏览器图标。
- `preview.png` 是原交付包中的纯色空白预览；查看造型请打开各主题的 logo / badge 文件。

详细行为和验证记录见 [50.14 — 页头 Logo 与 favicon](../../project-management/plans/50.14-brand-logo.md)。

本设计包包含 Remember Me 的 **Light / Neutral / Dark** 三套 Logo 资源，分别提供：

- **logo**：无圆主标（用于 Web UI 左上角、Help、README、About）
- **badge**：圆形徽章版（用于 favicon、PWA、桌面快捷方式、头像、小尺寸图标）
- **favicon**：`.ico` 文件（从 badge 自动导出）

## 目录结构

```text
remember-me-logo-final/
├── assets/
│   ├── light/
│   │   ├── logo/
│   │   ├── badge/
│   │   └── favicon/
│   ├── neutral/
│   │   ├── logo/
│   │   ├── badge/
│   │   └── favicon/
│   └── dark/
│       ├── logo/
│       ├── badge/
│       └── favicon/
├── preview.png
└── README.md
```

## 尺寸说明

每套 logo / badge 都导出了以下尺寸：

- 16px
- 24px
- 32px
- 48px
- 64px
- 128px
- 192px
- 512px
- master（原始大图）

## 推荐用途

### 1. 无圆主标（logo）
用于：
- Web UI 左上角品牌位
- Help / About 页面
- README / 文档页头
- 品牌展示图

建议使用：
- 32px / 48px：页面左上角
- 64px / 128px：Help / About / 文档
- master：后续继续派生素材

### 2. 圆形徽章（badge）
用于：
- favicon
- 浏览器标签页图标
- PWA / 桌面快捷方式
- 小尺寸头像 / 徽记

建议使用：
- 16px / 24px / 32px：favicon、小尺寸 UI
- 48px / 64px：列表图标、系统快捷方式
- 192px / 512px：PWA / 桌面快捷方式

### 3. favicon
每个主题都附带一个 `.ico` 文件：
- `favicon-light.ico`
- `favicon-neutral.ico`
- `favicon-dark.ico`

一般推荐站点默认使用：
- `favicon-light.ico`（浅色站点）
- `favicon-neutral.ico`（若站点整体以 Neutral 为主）

## 建议放置位置（给 Codex / 前端）

推荐直接拷贝到项目：

```text
public/brand/remember-me/
├── light/
│   ├── logo/
│   ├── badge/
│   └── favicon/
├── neutral/
│   ├── logo/
│   ├── badge/
│   └── favicon/
└── dark/
    ├── logo/
    ├── badge/
    └── favicon/
```

如果前端希望更简洁，也可以只放这些常用文件：

```text
public/brand/
├── logo-light-64.png
├── logo-neutral-64.png
├── logo-dark-64.png
├── badge-light-32.png
├── badge-neutral-32.png
├── badge-dark-32.png
├── icon-neutral-192.png
├── icon-neutral-512.png
└── favicon.ico
```

其中建议：
- `favicon.ico`：优先使用 `assets/neutral/favicon/favicon-neutral.ico`
- `icon-neutral-192.png` / `icon-neutral-512.png`：可直接使用 `assets/neutral/badge/badge-neutral-192.png` 和 `badge-neutral-512.png`

## 当前结论

- **Light**：保留原主标和圆形徽章版
- **Dark**：保留深色主题适配版本
- **Neutral**：新增岩石灰主题对应 logo / badge / favicon 资源

## 说明

本包中的 Neutral 版本是在 **不改变海马 + M 造型** 的前提下，仅通过配色适配新主题。
