# Linux AppImage Beta

Remember Me 1.1.2 是面向 Linux x86_64 的公开预览版。以下下载链接将在 **v1.1.2 正式发布后**可用。单文件包含 Launcher、Python Runtime、CPU 依赖和完整 BGE-M3 模型；无需安装 Python、pip、Docker 或下载模型。

## 下载和启动

发布后，从 [v1.1.2 Release](https://github.com/tech-kuiperbelt/remember-me/releases/tag/v1.1.2) 下载 AppImage 与 SHA256 文件，并在相同目录校验：

```bash
curl -fLO https://github.com/tech-kuiperbelt/remember-me/releases/download/v1.1.2/remember-me-1.1.2-linux-x86_64.AppImage
curl -fLO https://github.com/tech-kuiperbelt/remember-me/releases/download/v1.1.2/remember-me-1.1.2-linux-x86_64.AppImage.sha256
sha256sum -c remember-me-1.1.2-linux-x86_64.AppImage.sha256
chmod +x remember-me-1.1.2-linux-x86_64.AppImage
./remember-me-1.1.2-linux-x86_64.AppImage
# 或指定独立的绝对数据目录和端口：
./remember-me-1.1.2-linux-x86_64.AppImage --data-dir /absolute/path/to/remember-me-data --port 1314
```

仅在校验结果为 `OK` 时继续。使用托盘菜单打开 Remember Me 或退出；关闭浏览器不会停止服务。可在菜单中选择开启或关闭登录自启动。自启动记录外部 AppImage 路径；将文件移动或替换到不同路径后，请从新位置启动并重新启用自启动。现有 Launcher 与 AppImage 共用自启动条目，修改前请先检查当前设置。

## 系统要求

正常运行需要宿主机提供可用的 FUSE 设备、权限及辅助程序。桌面还需 GTK3/GLib、GI typelibs、Cairo、Ayatana AppIndicator/dbusmenu 和会话 D-Bus，详见 [Launcher 指南](launcher.zh-CN.md)。托盘需要兼容的 host；GNOME 可能需要扩展。GTK 可用但没有 host 时会显示 Open/Quit 小窗口。三个目标发行版的真实 GNOME/Wayland 桌面尚未全部测试。extract-and-run 可能残留临时文件，本版本不将其作为受支持的免 FUSE 方案。

## 数据保存位置与升级

AppImage Launcher 默认使用 `$XDG_DATA_HOME/remember-me-launcher/`；若 `XDG_DATA_HOME` 未设置或为空，则使用 `~/.local/share/remember-me-launcher/`。使用 `--data-dir /absolute/path/to/data` 可指定其他位置；AppImage 会拒绝相对的 `--data-dir` 路径。`MEMORY_DATA_DIR` 不用于选择 Launcher 数据根目录。未指定 `--data-dir` 时，可在启动环境中使用 `printf '%s\n' "${XDG_DATA_HOME:-$HOME/.local/share}/remember-me-launcher"` 查看默认位置。桌面或登录会话的环境可能与终端不同。

升级前请退出 Launcher 并确认已停止，将**整个数据目录**备份到独立的安全位置。阅读目标版本的发布说明，下载并校验新文件，再使用原来选定的数据目录启动。保留旧 AppImage 和备份以便回滚；若旧版本无法使用更新后的数据，请恢复与其匹配的备份。移动或替换 AppImage 不会移动或删除外部数据，切换安装方式也不会自动迁移数据。
