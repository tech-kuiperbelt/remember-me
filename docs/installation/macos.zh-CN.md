# macOS Apple Silicon 离线 Beta

需要 Apple Silicon（ARM64）Mac 和 macOS 15+，不支持 Intel Mac。
安装包包含 Python、CPU 推理依赖、Web UI 和完整 BGE-M3，无需另外安装 Python
或下载模型。[English](macos.md)

## 下载状态与完整性校验

macOS 1.1.3 Beta 包目前是工程候选，**尚未获准公开 Beta 分发**。不要假定现有 Release
包含 macOS 附件。维护者明确批准发布后，从同一个
[v1.1.3 Beta 发布页](https://github.com/tech-kuiperbelt/remember-me/releases/tag/v1.1.3)（页面及下载仅在发布后可用）
下载 `remember-me-macos-arm64-beta.dmg` 及对应的 `.dmg.sha256`。
不要使用 Windows/Linux 文件代替。

在终端进入下载目录，执行：

```bash
shasum -a 256 -c remember-me-macos-arm64-beta.dmg.sha256
```

仅在显示 `OK` 后继续。哈希匹配只能证明文件字节一致，不能代替 Apple 开发者身份
认证或恶意软件检查。

## 安装与首次打开

1. 打开校验通过的 DMG，把 `Remember Me.app` 拖入 Applications。若系统
   Applications 需要管理员权限，可以改用自己的 `~/Applications`；安装不需要 `sudo`。
2. 弹出磁盘映像，从复制后的安装位置打开应用。
3. 免费 Beta 使用 ad-hoc 签名，**没有 Developer ID 签名和 Apple 公证**。
   包内所有嵌套 Mach-O 也已逐项 ad-hoc 签名，但这仍不提供 Apple 身份或公证。
   macOS 可能阻止打开，提示无法验证开发者或无法检查是否包含恶意软件。
   这类提示不代表应用已被确认安全。
4. 仅当确认来源可信且已校验下载时，按
   [Apple 官方的单个应用例外方法](https://support.apple.com/zh-cn/102445)：
   尝试打开后，进入“系统设置 → 隐私与安全性 → 安全性”，选择“仍要打开”，
   再确认“打开”；系统可能要求身份验证。受管理设备可能不提供此操作。
   当前候选尚未在真实用户下载场景完成这一步验证；若无法操作，请停止并反馈。

不需要全局关闭 Gatekeeper、不需要 sudo，也不要批量删除 quarantine 属性。
若提示应用已损坏、被修改或会损害电脑，请停止安装并联系维护者，不能把这些警告
都当作未公证提示而绕过。

## 使用、退出与更新

应用启动本机 HTTP/MCP 服务并打开浏览器；浏览器未打开时，可访问
`http://127.0.0.1:1314/admin/console`（修改过端口时用实际端口）。
选择“退出 Remember Me”或 Command-Q 结束后端；关闭浏览器不会停止服务。
配置页也提供维护模式下的关闭/重启操作。

用户数据位于 `~/Library/Application Support/remember-me/`，独立于应用目录。
更新前正常退出并备份整个数据目录，只替换 `.app`，保留用户数据与配置，不同时运行
竞争写入者。删除应用不会删除用户数据。包内资源损坏时替换应用，不向包内下载模型。

CI 已验证服务和原生启动器生命周期；Finder 拖拽、Dock 外观、真实浏览器下载的
安全提示仍需真机验证。CI 命令行下载不等同于浏览器 quarantine 场景。
