# Windows Beta — Setup + 最小 Tray Launcher

Windows 对外只交付完整离线 Setup，不再发布 Portable ZIP。现有私有 PBS、72 项固定候选依赖及 BGE-M3 目录继续复用；严格 S1-A Defender 审计的历史结果保留，不作为 Beta 构建门槛。

## 本地构建输入

Windows x64、PowerShell 7，从完整源码运行：

```powershell
# 生成隔离 bundle、执行现有 Web/MCP/存储排他/退出恢复 smoke，封存输入。
./packaging/windows-beta/build.ps1 -Work C:\rm-beta-build-001 -Storage dual
# 使用上述 bundle 生成安装器；第 2 步获准后执行。
./packaging/windows-setup/build.ps1 -Bundle C:\rm-beta-build-001\bundle -Output C:\rm-beta-setup-001
```

工作目录必须不存在，运行目录准备沿用至少 12 GiB 可用空间预检。安装器另需展开、压缩和输出空间，具体峰值由第 2 步实测。首次准备构建输入需要联网取得受校验的资产；生成的 Setup 面向用户完整离线安装。

`resolved-dependencies.json` 是 Run 41604531455 的真实 72 项 URL/version/hash，仅 Beta candidate，不晋升生产锁。全部 wheel 下载强制哈希校验、离线安装。模型直接移入最终 HF snapshot 布局，不重复复制权重。`finalize.py --seal` 要求 business smoke 成功，不再输出 ZIP。

安装器工具与 VC++ 固定输入、SHA256 和签名要求见 `../windows-setup/`。版本来自 Bundle 的 `release.jsonc`；不独立输入产品版本。新远端构建需单独授权，旧 ZIP workflow 已停用；本步骤不增加或运行 Setup Actions。

## 启动与生命周期

开始菜单入口使用私有 `pythonw.exe -I tray_launcher.py`。仅提供 **Open Remember Me**、**Exit**；后台服务默认仅本机访问，不需要系统 Python。Launcher 单实例与服务启动保护独立于 SQLite/NDJSON 既有 sidecar 单写者锁。

Exit 通过所属服务的维护/正常关闭流程退出，等待进程释放存储；不杀其他 Python。Web 主动关闭后托盘随之退出，异常退出显示错误并保存日志。Web 重启仍由 Launcher 跟踪。程序尚未就绪时 Open 不宣称可用；慢模型初始化期间不要反复启动。

`start.cmd` 保留为诊断入口，不作为开始菜单或对外 ZIP 入口。Ctrl+C 的旧限制未被宣称修复，使用托盘 Exit 或 Web 维护后关闭。

## 数据与限制

默认数据 `%LOCALAPPDATA%\remember-me-beta`；`REMEMBER_ME_BETA_DATA_DIR` 可设为程序目录外的绝对路径。Setup 不迁移数据、不创建第二份数据库；卸载保留数据库、NDJSON、配置和日志，不卸载共享 VC++。升级前正常退出，并备份完整实际数据目录；沿用现有人工备份方式。

Windows Beta 保留 dual：标准库 msvcrt 单字节非阻塞 lifetime sidecar 锁、文件 fsync、同目录原子替换；跳过不支持的目录 fsync，仅本地固定 NTFS，不承诺 POSIX 等价的突发断电持久性。Linux 行为保持不变。

用户机已有 Web、断网、路径、重复启动和重启恢复证据；新托盘 Win32、安装、UAC、升级和卸载需要第 2/3 步 Windows 验证。Linux mock 测试不等同于 Windows 原生成功。无自启动、服务、自动更新或应用 EXE 冻结。

原生待观察的边界：Windows Web Restart 的底层 exec 是否产生新控制台；极短的新进程若在两次轮询之间启动并失败，最终退出归类可能不完整。已观察到的新 PID 会记录真实退出码；这两个边界不由 Linux mock 证明。Launcher 意外退出不会通过 Job close 强杀正在写入的业务子进程，必要时使用 Web 正常关闭。
