# Linux AppImage 原型

Linux x86_64 AppImage 目前是原型，尚未正式发布。使用已有候选文件，在兼容的图形桌面中赋予执行权限并启动：

```bash
chmod +x ./remember-me-linux-x86_64-s2.AppImage
./remember-me-linux-x86_64-s2.AppImage
# 或指定独立的绝对数据目录和端口：
./remember-me-linux-x86_64-s2.AppImage --data-dir /absolute/path/to/remember-me-data --port 1314
```

使用托盘菜单打开 Remember Me 或退出；关闭浏览器不会停止服务。正常运行需要可用的 FUSE 环境，以及 [Launcher 指南](launcher.zh-CN.md)中说明的系统图形组件。显示托盘需要托盘 host；GTK 可用但没有 host 时会显示一个小型管理窗口。

## 数据保存位置

AppImage Launcher 默认使用 `$XDG_DATA_HOME/remember-me-launcher/`；若 `XDG_DATA_HOME` 未设置或为空，则使用 `~/.local/share/remember-me-launcher/`。使用 `--data-dir /absolute/path/to/data` 可指定其他位置；AppImage 会拒绝相对的 `--data-dir` 路径。`MEMORY_DATA_DIR` 不用于选择 Launcher 数据根目录。未指定 `--data-dir` 时，可在启动环境中使用 `printf '%s\n' "${XDG_DATA_HOME:-$HOME/.local/share}/remember-me-launcher"` 查看默认位置。桌面或登录会话的环境可能与终端不同。

移动或替换 AppImage 不会移动或删除外部数据目录。切换安装方式不会自动迁移数据。如果移动 AppImage，请从新位置启动并重新启用登录自启动，以更新保存的路径。
