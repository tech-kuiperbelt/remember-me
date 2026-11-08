# macOS S1 Step 2 — 原生 GUI 生命周期验证

2026-11-08：验证完成。最终 [CI 42463307999](https://github.com/tech-kuiperbelt/remember-me/actions/runs/42463307999)
成功，测试源码 `4405a57`。完整结果及原始日志见
[macos-gui-step2-42463307999](https://github.com/tech-kuiperbelt/remember-me/actions/runs/42463307999/artifacts/13610741298)
（保留至 2026-11-22）。

直接复用 Step 1 已验收的原始 DMG（基线
`b4bce53d64d644e906d0176aa5d4328beafe9e3b`，Run `42457140969`），下载后验证固定
SHA256 `dced0eec97d12287f8767c37bdd757ae9d5ec1f91c83bef117f33c7d15ea222c`。
没有重新构建或修改产品，没有重跑完整离线推理/CRUD，仅创建一条 Web Restart 持久化夹具。

| 项目 | 结论与证据 |
| --- | --- |
| 原生 AppKit 启动 | 通过；运行最终 `.app` 的原生可执行文件，不传 `--no-browser`；记录原生 PID、直接拥有的 Python 后端 PID，服务 ready |
| 默认浏览器行为 | 系统默认 handler 为 Safari，观察到 Safari 进程及浏览器对 `/admin/console`、JS/CSS 的真实请求；验证脚本自身不访问该页面 |
| 精确浏览器标签 URL / 视觉呈现 | 无法验证；Safari AppleScript 读取标签 URL 在 15 秒内超时，未绕过权限，也不将 HTTP 请求当作视觉验收 |
| Cmd-Q | 通过；System Events 将真实应用置前并发送 Command-Q，没有使用 AppleEvent Quit 替代；原生应用退出 |
| 正常退出清理 | 通过；后端 PID 消失、端口关闭、SQLite primary 与 JSONL replica 两把写锁可由独立进程重新获取 |
| GUI owner 异常退出 | 通过；向真实原生 owner 发送 SIGKILL，后端按现有父进程监测设计自行完成 graceful shutdown，进程、端口及双写锁释放 |
| Web Restart | 通过；日志确认旧 Runtime shutdown 与新 startup，原生 owner 和受管 Python PID 保持，唯一夹具仍可读取 |
| Finder / Dock | 未验证拖拽安装和 Dock 外观/交互；本次从只读 DMG 用 ditto 复制后原生启动，不将复制称作 Finder 安装验收 |

最终产品失败项：无。首次运行
[42462943042](https://github.com/tech-kuiperbelt/remember-me/actions/runs/42462943042)
在 SIGKILL 后立即检查端口而失败；修正了测试：Darwin 的受管 Python 有独立进程组，
必须记录真实后端 PID，并为现有 0.5 秒父进程监测及关闭提供最多 15 秒的有界观察。
未修改产品。最终 owner-kill 后进程记录为空，服务日志有完整 shutdown 标记。

剩余风险：真实 Mac 上 Safari 首次打开/标签与视觉呈现、Finder 拖入 Applications、
Dock 交互、Gatekeeper/quarantine 仍需手工确认。CI 的可观察结论不覆盖这些项目。
没有引入 GUI 自动化框架、修改数据格式或核心架构，没有调整 TCC/Accessibility 权限，
没有执行 S2 签名/公证、合并 master 或发布 Release。

新增脚本 `gui_verify.py` 和工作流 `.github/workflows/macos-gui.yml` 仅承担本步骤验证。
