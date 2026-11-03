# Windows Beta Portable ZIP

目标是 Windows 11 虚拟机手工测试，不是正式发行。复用已原生验证的 PBS、72 项候选依赖和固定 BGE-M3；严格 S1-A Defender 审计保留在原 POC，不阻断 Beta ZIP。

维护者已批准的 Windows Beta 持久化策略：保留 dual，标准库 msvcrt 单字节非阻塞 lifetime sidecar 锁，文件 fsync + 同目录原子替换；跳过 Windows 不支持的目录 fsync，仅支持本地 NTFS，不承诺 POSIX 等价的突发断电目录持久性。SQLite 格式、outbox、JSONL 事件格式及正常关闭同步均不改变。Linux 继续 flock 和目录 fsync。

## Windows VM 本地构建

在 Windows x64 PowerShell 7 中，从完整源码目录运行：

```powershell
./packaging/windows-beta/build.ps1 -Work C:\rm-beta-build-001 -Storage dual
```

工作目录必须不存在，至少 12 GiB 可用。脚本复用 POC prepare-only 路径，使用本目录 resolved-dependencies.json（来自 Run 41604531455 的真实 72 项 URL/version/hash；仅 Beta candidate，不晋升生产锁），全部下载强制 hash、离线安装。模型直接移入最终 HF snapshot 布局，不重复复制权重，不需要外部 Python/模型。

随后用私有 Python 运行隔离 Web/MCP 添加/搜索、双 sidecar 排他、正常退出和重启持久化 smoke；只在通过后写 ZIP 和 SHA256。原始业务数据库不进入包，测试数据在工作目录 smoke-data，位于 bundle 外。

输出：`<Work>/remember-me-windows-x64-beta-candidate.zip` 和 `.zip.sha256`。如 hosted artifact 容量/上传受限，在 Windows VM 本地执行同一命令；不为平台限制改变程序设计。ZIP 为 Zip64，包含 runtime、app、web、完整模型、授权文本、start.cmd、说明和构建输入证据。初版入口没有托盘或 EXE。

## 已准备的远端路径（需单独授权）

workflow `.github/workflows/windows-beta-zip.yml` 只响应专用分支 `poc/windows-beta-zip` 的 push；使用标准 windows-2022、contents:read、无 Secrets/写权限。不会匹配原 POC 分支或两个手动 Docker workflow。Beta ZIP artifact 保留 7 天，证据 14 天；ZIP 使用上传 compression-level:0 避免再次压缩。没有 Tag/Release/正式渠道发布。

每次远端执行必须有对应授权。本地准备和旧 S1-A 的四次授权不等于允许新的运行或无限重试。

## 测试和限制

本地 Linux 的存储/配置/恢复及生命周期回归通过，另有实际私有运行时 Web/MCP CRUD/退出/重启 smoke；这些不是 Windows 结果。Windows 锁、首次配置写入、Web/MCP 启动与 ZIP 仍需原生构建验证，之后由维护者按随包 WINDOWS-TESTING.md 测试干净 Windows 11。

既有未通过审计的宿主 Defender/VC++ 运行库依赖仍列为已知限制。若干净 VM 启动报缺失 DLL，保留报错，不关闭 Defender，不从未知网站下载 DLL。Windows stdio 及所有高级运维路径未作为首版手工验收前置条件。
