# Linux Tray Launcher

使用已有的 Linux x86_64 Launcher 目录。保持目录内容完整，在其中启动托盘程序：

```bash
./launch.sh
# 或指定独立数据目录和端口：
./launch.sh --data-dir /absolute/path/to/remember-me-data --port 1314
```

使用托盘菜单打开 Remember Me 或退出。关闭浏览器不会停止服务。程序包保留独立前台 Runner 入口 `./start.sh`；其默认数据位置不同，见 [Standalone 指南](standalone.zh-CN.md)。

桌面需要 GTK3/GLib、GI typelibs、Cairo、Ayatana AppIndicator/dbusmenu 和会话 D-Bus。显示托盘需要兼容的托盘 host；GTK 可用但没有 host 时，Launcher 会提供小型 Open/Quit 窗口。

## 数据保存位置

Launcher 默认使用 `$XDG_DATA_HOME/remember-me-launcher/`；若 `XDG_DATA_HOME` 未设置或为空，则使用 `~/.local/share/remember-me-launcher/`。`--data-dir` 可覆盖默认值，相对路径以 Launcher 目录为基准。Launcher 不使用 `MEMORY_DATA_DIR` 选择数据根目录。未指定 `--data-dir` 时，可在启动环境中使用 `printf '%s\n' "${XDG_DATA_HOME:-$HOME/.local/share}/remember-me-launcher"` 查看默认位置。桌面或登录会话的环境可能与终端不同。

切换安装方式不会自动迁移数据。替换程序本身不会删除持久数据；请将选用的数据目录放在计划替换或删除的程序目录之外。
