# About Remember Me — 背景图最终调整规范

## 目标

当前 About 区域的左侧正文密集、右侧引文稀疏。背景装饰应当 **右强左弱**，把视觉重心移向右侧，补足右侧留白，但不能降低引文可读性。

这是一项**局部视觉调整**：保留当前 About 卡片尺寸、双栏布局、品牌文案、引文、边框与下方 Help 内容，不重做页面，也不要增加额外卡片或功能。

## 资源与目标路径

把设计包里的：

- `backgrounds/help-hero-bg-light-tech.png`
- `backgrounds/help-hero-bg-dark-tech.png`

复制进项目（如文件已存在，可直接复用）：

- `web/static/img/help/help-hero-bg-light-tech.png`
- `web/static/img/help/help-hero-bg-dark-tech.png`

只用于 Help **首页**的 About Remember Me 背景，不用于其它 Help 页面。

## 布局要求

1. 把图片设为整个 About 卡片的**背景层**，而不是右侧引文容器独立的一张图；图片不会改变内容布局。
2. 图片靠 `right bottom` 或 `right center` 定位；右中、右下保留抽象网格与波纹，左侧正文背景尽量接近原纯色。
3. 使用由左向右逐渐变透明的主题背景色渐变，让装饰从中部向右侧自然出现，不要出现硬切线。
4. 引文文字背后保持低对比、低纹理，最亮光点和复杂波纹尽量处于右边缘、右下角；必要时另加轻柔局部蒙层。
5. Light/Dark 保持相同几何布局，仅换图片与适宜的遮罩强度。窄屏进一步减弱装饰或隐藏，以阅读为先。
6. 不把 Help 变为大面积炫光宣传页：科技感要轻、安静、精致。

## CSS 示意（按现有样式与主题系统适配）

```css
.help-about {
  position: relative;
  isolation: isolate;
  overflow: hidden;
  background: var(--help-about-surface);
}
.help-about::before {
  content: "";
  position: absolute;
  inset: 0;
  z-index: -1;
  pointer-events: none;
  background-image:
    linear-gradient(90deg,
      var(--help-about-surface) 0%,
      var(--help-about-surface) 42%,
      transparent 100%),
    url("../img/help/help-hero-bg-light-tech.png");
  background-position: center, right bottom;
  background-size: cover, cover;
  background-repeat: no-repeat;
}
[data-theme="dark"] .help-about::before {
  background-image:
    linear-gradient(90deg,
      var(--help-about-surface) 0%,
      var(--help-about-surface) 42%,
      transparent 100%),
    url("../img/help/help-hero-bg-dark-tech.png");
}
```

上述 CSS **仅为实现意图示例**，不应直接照抄：真实 DOM、CSS 变量、相对 URL 及主题选择器请以项目代码为准。若负 `z-index` 导致背景被遮挡，可用 `z-index: 0` 的伪元素配合内容 `position: relative; z-index: 1`。

## 验收

- 左侧正文比右侧背景更安静，所有文字清晰。
- 右侧适当增加质感，但引文不受干扰。
- 保持原应用 Shell、About 内容、卡片尺寸和其它 Help 模块不变。
- 分别检查 Light、Dark 和窄屏；提交两种主题的截图。
