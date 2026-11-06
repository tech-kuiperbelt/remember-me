# Windows 11 x64 Beta 安装、升级与卸载

Windows 对外仅提供完整离线 Setup，不提供 Portable ZIP。本页对应 **1.1.1 Beta 候选**；下载链接仅在维护者批准并公开 Pre-release 后可用。Linux/Docker 的现有发行不因此改变。

## 安装与使用

1. 发布后从 [v1.1.1 Pre-release](https://github.com/tech-kuiperbelt/remember-me/releases/tag/v1.1.1) 下载 `remember-me-windows-x64-beta-setup.exe` 和同名 `.exe.sha256`。安装前核对 SHA256：

   ```powershell
   Get-FileHash .\remember-me-windows-x64-beta-setup.exe -Algorithm SHA256
   Get-Content .\remember-me-windows-x64-beta-setup.exe.sha256
   ```

2. 正常双击 Setup，按当前用户安装。无需系统 Python、Docker或另行下载模型。缺少符合版本要求的 VC++ x64 Runtime 时，Setup 使用随包微软官方安装文件，并仅为该子安装程序请求 UAC；已有满足要求的新版本则跳过。取消或失败时按错误提示收集日志，不关闭安全防护。
3. 从开始菜单启动 Remember Me Beta。服务在后台初始化，托盘提供运行状态、打开主界面、记忆、统计、设置、帮助、登录时自动启动与安全退出；菜单跟随已保存的界面语言（中文/English）；图标可能位于 Windows 隐藏图标区域。首次加载较慢时等待就绪，不要反复启动。
4. Open 打开 Web。默认 `http://127.0.0.1:1314/admin/console`，MCP 为 `http://127.0.0.1:1314/mcp`，自定义端口以保存配置为准。托盘启动限制绑定本机；无自动更新。
5. 使用完毕选择 Tray Exit，等待图标退出。关闭浏览器不会关闭服务；也可在 Web 进入维护模式后关闭服务器。

## 登录时自动启动

默认关闭。在托盘勾选“登录时自动启动”后，仅为当前 Windows 用户注册；取消勾选只关闭以后的自动启动，不会立即退出正在运行的服务。禁用无需管理员权限。

启动入口记住启用时的程序路径和实际数据目录，不迁移或复制记忆。若更改程序或数据目录，请从新位置启动后重新勾选；手动入口的既有环境变量规则不变。自启动使用同一私有 Python、单实例和存储锁。

Windows 可能延迟登录启动，或通过启动应用设置/系统策略将其禁用；程序不会绕过系统策略。过长的启动命令会明确失败，不静默截断；此时仍可从开始菜单手动启动。

## 数据与升级

- 程序默认 `%LOCALAPPDATA%\Programs\Remember Me Beta`。
- 数据默认 `%LOCALAPPDATA%\remember-me-beta`，与程序分离。自定义目录沿用 `REMEMBER_ME_BETA_DATA_DIR` 用户环境变量的绝对路径规则，且必须位于程序目录外的本地固定 NTFS 磁盘。开始菜单启动须能读取同一变量。
- 更新前用 Tray Exit 正常退出，并备份**整个实际数据目录**到独立安全位置。运行新 Setup，使用同一用户及原安装路径覆盖升级；运行中应提示先退出，不强杀写入进程。备份不是只复制正在使用的 memories.db。
- 升级后检查旧记忆、新增与搜索。保留旧安装包及备份；不承诺任意降级兼容，必要时恢复与旧版匹配的完整备份。没有自动迁移数据目录。

## 卸载

先正常退出，再使用 Windows“已安装的应用”中的 Remember Me Beta 卸载入口，或开始菜单卸载入口。运行中应要求先退出。卸载仅清除能够确认由本应用创建的登录启动入口，默认保留记忆、配置和日志，也不卸载共享 VC++ Runtime；重装并使用同一数据目录可继续读取原数据。没有一键删除记忆选项，不删除锁文件来绕过运行保护。

## 性能与日志

日志在实际数据目录的 `logs\`：`launcher.log`、`launcher-service.log`、`memory-server.log`，以及本候选的 `startup-timing.jsonl`、`performance-timing.jsonl`。前者记录 Launcher/导入/模型加载/服务就绪阶段，后者记录新增记忆的 embedding、索引向量准备、数据库和总耗时；分段诊断不记录记忆正文或向量。数据库阶段计量现有 storage.add 调用返回，不强制同步；后台副本落盘不计入该阶段。

报告启动或长文本新增慢时，附 Windows/VM CPU、内存、磁盘配置、文本字符数、操作耗时和上述日志；分享前检查其他服务日志中的隐私。VM 配置可能影响性能，但不作为已确认根因。安装日志在当前用户 TEMP，前置安装摘要在默认 Beta 数据目录 `logs\setup`；微软详细日志可能在提权账号 TEMP。

## 已知限制

本版为 Beta，最终 Setup 未配置代码签名；不代表所有 Windows 机器均已覆盖。仅支持本地固定 NTFS 的 dual 存储，保留文件 fsync/原子替换，不承诺 POSIX 等价的突发断电目录持久性。严格 S1-A Defender 审计历史项仍未完成。无 Windows Service 或自动更新器；性能诊断用于定位，不承诺全面优化。
