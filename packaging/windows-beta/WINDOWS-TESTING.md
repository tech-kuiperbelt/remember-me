# Remember Me Windows Beta — Setup 手工验收

完整离线 Beta 候选，需 Windows 11 x64、本地固定 NTFS；无需系统 Python。严格 S1-A Defender 身份审计历史限制保留。

1. 核对 Setup 的 SHA256，双击安装到默认当前用户目录。如缺 VC++，只批准微软官方运行库子安装程序的 UAC；取消/失败时保留提示和安装日志，不关闭安全防护。
2. 从开始菜单启动 Remember Me Beta，确认托盘出现图标，后台初始化。打开托盘 **Open Remember Me**，访问 Web；默认 `http://127.0.0.1:1314/admin/console`。自定义端口按配置。MCP 为同端口 `/mcp`，旧客户端可用 `/sse`。
3. 添加中英文记忆、搜索，连接 MCP；断网后重复核心操作，确认完整模型不需要下载。
4. 再次从开始菜单启动，确认没有第二个活动服务，也不影响当前实例。
5. 托盘 **Exit** 后等待退出，再从开始菜单启动，确认记忆与搜索正常。Web 维护后关闭也应移除托盘；Web 重启后托盘应继续跟踪服务。
6. 服务运行时尝试升级/卸载，必须提示先正常退出。退出后覆盖安装，旧数据仍可读。卸载后数据应保留，VC++ 不被卸载；重装后可读旧记忆。
7. 补测中文/空格安装路径、正常 Windows 重启，以及无 VC++/已有新版本/拒绝 UAC 三种前置条件路径。

程序默认 `%LOCALAPPDATA%\Programs\Remember Me Beta`，持久数据默认 `%LOCALAPPDATA%\remember-me-beta`。自定义数据沿用 `REMEMBER_ME_BETA_DATA_DIR` 绝对路径规则，不可放程序目录内；从开始菜单启动时需确保该变量对当前用户进程可见。升级前备份整个实际数据目录，不自动迁移。

日志在实际数据目录 `logs\`：服务日志、Launcher 日志、`startup-timing.jsonl`（启用时）、`startup-error.log`（发生时）。安装/VC++ 日志按安装提示定位。反馈附 Windows 版本、安装路径、实际数据路径、剩余磁盘、复现步骤、Setup SHA256 和相关日志；分享前检查记忆内容等隐私。

不要通过任务管理器强杀当作正常退出。若长时间无法退出，保留日志并反馈；不删除数据库或 sidecar 文件来绕过锁。Windows dual 仅本地 NTFS，保留文件 fsync/原子替换，但不承诺 POSIX 等价的断电目录持久性。
