# macOS S2 Step 3 — 嵌套签名修复与重建报告

2026-11-09。维护者批准的工程修复已完成：三个未签名嵌套 Mach-O 已按 ad-hoc
方式逐项签名，外层 App 重新封签，DMG 重新构建并完成针对性回归。**公开 Beta
仍未放行**，因为 Developer ID/公证不存在，且真实浏览器下载后的 Gatekeeper 例外
流程尚未在独立 Mac 验证。未创建 Tag/Release，未合并 master，未修改核心业务、
数据格式或持久化行为。

## 最终产物与 CI 证据

- 最终 CI：[Run 42573089657](https://github.com/tech-kuiperbelt/remember-me/actions/runs/42573089657)，
  源码 `0446af08be33bae22870e78bc75c3738dea12fc1`，macOS ARM64，10 个步骤全部成功。
- 证据附件：[macos-arm64-evidence-42573089657](https://github.com/tech-kuiperbelt/remember-me/actions/runs/42573089657/artifacts/13654890930)
  （2026-11-23 前有效）。
- DMG 附件：[macos-arm64-beta-42573089657](https://github.com/tech-kuiperbelt/remember-me/actions/runs/42573089657/artifacts/13654456970)。
- 候选锁来源（修复构建）：[Run 42571338413](https://github.com/tech-kuiperbelt/remember-me/actions/runs/42571338413)，
  源码 `cd7b5358ab20769b0daeec6a9e79dbdda08a3255`。

| 项目 | 值 |
| --- | --- |
| DMG 大小 | **1,881,929,980 bytes**（低于 GitHub 2 GiB 单附件限制） |
| DMG SHA256 | **`31b2bf5dc3a6ab4a4546ad1f438c76e1f564d3b7e0f52ca59047f5266f8e7fd4`** |
| 校验文件 | `remember-me-macos-arm64-beta.dmg.sha256`（与同名 DMG 同附件发布） |
| 外层签名 | `Signature=adhoc`，`TeamIdentifier=not set` |
| 嵌套 Mach-O | 235 个逐项检查，**0 个失败** |
| 签名对象总数 | 236（235 嵌套 + 外层 App），**0 个失败** |

## 签名修复

`build.py` 原先只执行 `codesign --force --deep --sign -`，实测仍遗留 3 个未签名
文件。修复后构建流程：

1. 枚举 App 内全部 Mach-O（含 `Contents/Resources/runtime/...` 下的 loose dylib）。
2. 按路径深度从深到浅逐项 `codesign --force --sign -`。
3. 最后对外层 `Remember Me.app` 执行 `codesign --force --sign -` 封签。
4. 执行 `codesign --verify --deep --strict` 并保留逐项签名证据。

修复后的逐项检查覆盖 `charset_normalizer` 两个 `.so` 与 `google._upb`
一个 `.so`，三者均通过。`spctl` 仍拒绝 app 与 DMG（`rejected; source=no usable
signature`），这与“无 Developer ID/未公证”预期一致，不是签名遗漏。

## 针对性回归

最终 DMG 在 `macos-15` ARM64 runner 上通过：

- **离线模型推理**：包内 Python/PyTorch/Sentence Transformers，OS sandbox 拒绝
  外网，CPU 中英双语向量 shape `(2,1024)`，norm 校验通过。
- **MCP 基本功能**：`initialize`、`tools/list`、`add`/`update`/`get_details`/
  `search_semantic`/`delete`；Web UI 与静态资源可访问。
- **原生启动/退出**：AppKit 原生 owner + 唯一受管 Python 后端；Web Restart、
  Cmd-Q、正常退出、owner SIGKILL 后自恢复；进程组、端口、SQLite/JSONL 双写锁
  均释放。
- **持久化**：重启后夹具仍可读；SQLite `integrity_check` 通过；JSONL replica
  存在；App 资源运行前后字节一致。
- **重复实例**：同一数据目录的第二次启动被拒绝。
- **分发检查**：DMG 容器、逐项嵌套签名、外层 deep verify、尺寸限制均通过。

未验证/不可靠项：

- 真实浏览器下载产生的 `com.apple.quarantine` 来源。
- Finder 拖拽安装、Dock/菜单视觉与交互。
- 真实首次打开的安全提示文字、“仍要打开”单应用例外与身份验证。
- 受管 Mac 是否允许该例外；其他 macOS 15.x 真机未逐一覆盖。
- LaunchServices 首次 `open` 的服务就绪时间不稳定：Run 42571338413 在 15 秒内
  观察到监听，Run 42573089657 在 15 秒内未就绪但确认原生 owner 与 Python 后端
  已启动。该路径不证明浏览器 quarantine 下的首次打开成功。

## 固定输入与供应链

已提交到仓库：

- `packaging/macos/macos-arm64.lock`：70 个 macOS ARM64/universal2/pure
  wheel 的 HTTPS URL + SHA256，固定锁 SHA256
  `d153b4434b35f4905f4d9c750482f31bf4b7508192f274802a78cb1852f27918`。
- `packaging/macos/macos-arm64-offline.lock`：同一组 wheel 的
  `name==version --hash` 离线安装视图。
- Python 归档 URL/SHA256 继续固定在 `build.py`；模型 revision、每文件大小/SHA
  固定在 `docker/bge-m3-manifest.json`。
- 构建证据（`build.json`、`macho-signatures.json`、`pip-check.txt`、
  `production-pins.txt`、逐项签名 JSON、回归日志）在 CI 证据附件中保留 14 天。

仍不承诺位级可复现：`macos-15` runner 镜像与 Xcode/Swift 工具链未固定到完整
身份，DMG 容器/签名元数据也未规定确定性。维护者公开前应保存候选锁、构建
manifest 与关键证据。

## 放行结论

- **S2 Step 3 工程修复：完成**。三个未签名嵌套 Mach-O 已修复，新 DMG 通过
  签名、离线和原生回归。
- **免费公开 Beta：仍为 NO-GO**。阻塞项只剩真实 Mac 的浏览器下载 → Finder
  安装 → Apple 单应用例外 → 启动验证，以及维护者对剩余风险与公开发布的明确
  决定。
- 可接受剩余风险：无 Developer ID/公证导致首次警告与单应用批准；受管 Mac 可能
  不允许例外；无位级可复现保证；CI 首次打开就绪时间不稳定。
