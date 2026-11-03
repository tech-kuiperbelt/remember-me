# Remember Me Windows Beta — Windows 11 手工测试

这是待手工验收的 Beta 候选，不是正式 Windows 发行。无需系统 Python。
建议先在 Windows 11 x64 虚拟机、本地固定 NTFS 磁盘测试，至少留出 8 GiB 可用空间。

1. 将整个 ZIP 解压到短路径，例如 `C:\RememberMeBeta`。不要在 ZIP 内直接运行，也不要只复制 start.cmd。
2. 双击 start.cmd，保持控制台打开，等待服务就绪。默认仅监听本机。
3. 打开 http://127.0.0.1:1314/admin/console 。MCP 客户端使用 Streamable HTTP：
   `http://127.0.0.1:1314/mcp`；旧客户端可用 `/sse`。本版优先测试 HTTP，不要求 stdio。
4. 添加一条中文和一条英文记忆，记录 ID，进行语义搜索；连接 MCP 重复添加和搜索。
5. 断网后重复添加/搜索，确认没有模型下载提示。完整模型已包含在 ZIP 中。
6. 按 Ctrl+C 一次并等待进程退出，或在 Web Ops 进入维护模式后点击 Shut Down Server；不要把直接关闭窗口当作正常退出。
7. 重启，检查旧记忆仍能搜索。将下一版解压到另一个目录，确认旧数据仍存在。

持久化数据默认在 `%LOCALAPPDATA%\remember-me-beta`，与程序目录分离；更新时只替换程序，不删除数据。
如需不同数据目录，在启动前设置 `REMEMBER_ME_BETA_DATA_DIR` 为绝对路径；不可放在程序目录中。
不要把虚拟机测试数据指向已有真实数据库。需要清空测试环境时先退出，再由维护者自行备份/处理数据目录。

日志：数据目录 `logs\memory-server.log`、`logs\startup-error.log`（若存在），另附控制台报错截图和 ZIP SHA256。
提交反馈时说明系统版本、解压路径、剩余磁盘、复现步骤和相关记忆 ID；日志/导出可能包含记忆内容，分享前检查隐私。

已知限制：严格 S1-A Defender 身份审计未通过，不作为本 Beta 构建门槛，历史证据保留。
本版保留 dual（SQLite 主库和 NDJSON 副本）。Windows 保留文件 fsync 和同目录原子替换，跳过不支持的目录 fsync；仅支持本地 NTFS，不承诺突发断电时与 POSIX 完全相同的目录持久性。不要在网络盘使用。
宿主 VC++ 运行库依赖仍需干净 Windows 11 验证；若提示 DLL 缺失，保留原始报错，不下载来历不明 DLL。
无托盘、自启动、正式 EXE；端口冲突会在控制台/日志中显示，不自动改端口或开放局域网。
