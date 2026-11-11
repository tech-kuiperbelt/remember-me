# macOS Beta: independent installation check

Use the final HTTPS DMG link and SHA256 supplied with the frozen release bundle.
The test download must have the same SHA256 as the archived Release candidate.
This is a test candidate; the official v1.1.3 Release remains unpublished.

1. On an Apple Silicon Mac running macOS 15+, download the DMG using Safari or
   Chrome. Record macOS version, browser version and download URL.
2. Run `shasum -a 256 remember-me-macos-arm64-beta.dmg` and compare the complete
   hash with the supplied SHA256. Check the browser-added quarantine using
   `xattr -p com.apple.quarantine remember-me-macos-arm64-beta.dmg`; record its
   presence and retain it. A missing attribute does not test the required flow.
3. Open the DMG in Finder, drag Remember Me.app into Applications, eject the DMG
   and launch the installed copy. Observe Finder/Dock icons.
4. The package is ad-hoc signed, without Developer ID or Apple notarization.
   Only after verifying and trusting the download, follow the
   [installation guide](macos.md) for the single-app Gatekeeper exception.
   Keep Gatekeeper and quarantine enabled. Stop on damaged/modified/harmful-app
   warnings; do not treat them as ordinary unsigned-app warnings.
5. Open the Web UI, confirm version 1.1.3, save a test memory, quit with Command-Q,
   reopen and verify that memory remains readable and searchable. Check the icon
   in Finder and the Dock. Record warnings and any failure.

Send the maintainer the completed record below and relevant screenshots. Do not
include private memories or credentials. The maintainer records it as the Draft
asset `mac-acceptance.json`; leave failed or untested checks false.

```json
{
  "source_commit": "FULL_FROZEN_SOURCE_SHA",
  "dmg_sha256": "FULL_ARCHIVED_DMG_SHA256",
  "tester": "TESTER",
  "accepted_at": "ISO8601_TIMESTAMP",
  "macos": "VERSION",
  "browser": "NAME_AND_VERSION",
  "download_url": "EXACT_HTTPS_URL",
  "notes": "Observed prompts and evidence references",
  "checks": {
    "browser_download": false,
    "sha256": false,
    "quarantine": false,
    "finder_install": false,
    "gatekeeper_per_app": false,
    "icon": false,
    "launch": false,
    "memory_write": false,
    "restart_persistence": false
  }
}
```

## 中文

使用最终冻结交付包提供的 HTTPS 链接，由 Safari/Chrome 下载 DMG；文件必须与
归档候选的完整 SHA256 一致。记录 macOS、浏览器版本和实际下载链接。
通过 `xattr -p com.apple.quarantine remember-me-macos-arm64-beta.dmg` 确认并保留
浏览器添加的 quarantine；缺失时不算完成本次验收。用 Finder 拖入 Applications、
弹出 DMG 后启动，检查 Finder/Dock 图标。按[中文指南](macos.zh-CN.md)操作
Gatekeeper 单应用例外，不关闭 Gatekeeper，不删除 quarantine；损坏或有害警告须停止。

在 Web UI 确认 1.1.3，写入测试记忆，Command-Q 退出后重开，确认仍能读取和检索。
将上表、实际提示和截图反馈给维护者；未测或失败项保持 false。CI 不替代这次独立真机验收。
