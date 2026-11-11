# macOS S2 Step 3 — 免费公开 Beta 分发评估

> 2026-11-09 后续：维护者批准 ad-hoc 嵌套签名修复，重建结果见
> [MACOS_S2_STEP3_FIX_REPORT.md](MACOS_S2_STEP3_FIX_REPORT.md)。本文件保留原始
> NO-GO 评估，其中 3 个未签名嵌套 Mach-O 已在修复构建中解决。

2026-11-08。**评估与文档任务完成；当前 DMG 不满足公开 Beta 放行标准（NO-GO）。**
未更改已验收包、核心功能、数据格式或系统安全策略。没有执行 Developer ID、公证、
Tag/Release 或合并 master。

## 原始证据

- 本步骤 [CI 42471192285](https://github.com/tech-kuiperbelt/remember-me/actions/runs/42471192285)，
  测试源码 `da79af3`，macOS ARM64；workflow success 只表示观察任务执行完，**不表示检查全通过**。
- [完整观察结果及日志](https://github.com/tech-kuiperbelt/remember-me/actions/runs/42471192285/artifacts/13613980236)。
- 原样复用 Step 1 Run `42457140969` 的 DMG，基线 `b4bce53d64d644e906d0176aa5d4328beafe9e3b`。
  字节数 **1,886,232,812**；SHA256：
  `dced0eec97d12287f8767c37bdd757ae9d5ec1f91c83bef117f33c7d15ea222c`。

| 实测项目 | 结果 |
| --- | --- |
| DMG SHA256、hdiutil verify | 通过 |
| 复制 app 到用户可写 Applications 目录 | 通过；ditto，不冒充 Finder 拖拽 |
| Bundle `codesign --verify --deep --strict` | 通过 |
| 外层身份 | `Signature=adhoc`，`TeamIdentifier=not set` |
| 逐个嵌套 Mach-O | **235 个中 232 个验证成功，3 个未签名，见下** |
| spctl app execute assessment | `rejected`，exit 3 |
| spctl DMG open assessment | `rejected; source=no usable signature`，exit 3 |
| Gatekeeper 系统状态前后 | 均 `assessments enabled` |
| 原始 DMG 和复制 app quarantine | 都没有 `com.apple.quarantine`，没有人为添加/删除 |
| LaunchServices 首次 open 请求 | 命令返回 0，但 15 秒后未发现测试端口监听；**不能认定成功启动** |

逐个检查失败的嵌套可执行文件均报告 `code object is not signed at all`：

- `Contents/Resources/runtime/python/lib/python3.12/site-packages/charset_normalizer/cd.cpython-312-darwin.so`
- `Contents/Resources/runtime/python/lib/python3.12/site-packages/charset_normalizer/md.cpython-312-darwin.so`
- `Contents/Resources/runtime/python/lib/python3.12/site-packages/google/_upb/_message.abi3.so`

这说明外层 deep verify 不能代替非标准资源目录内嵌套代码的逐项检查。不推断以上
未签名项必然是本次首次打开失败原因，也不将其泛化为已确认恶意或损坏。

## Gatekeeper 能确认与不能确认的部分

已确认当前系统的命令行策略评估拒绝 app 和 DMG；这不是付费身份或公证已经具备的包。
CI 的 `gh` 下载没有浏览器 quarantine，不能替代 Safari/Chrome 下载后的首次打开。
没有伪造 quarantine、调整 TCC、全局关闭 Gatekeeper、调用 sudo 或删除属性。
未自动点击安全例外，也不把 `open` 返回 0 当作真实用户成功打开。

真实 Mac 下载、Finder 安装、首次警告的准确文字、系统设置“仍要打开”与之后的嵌套
Python/动态库行为仍未验证。Step 2 已证明未隔离原包的 AppKit/Cmd-Q/owner异常退出/
Web Restart 行为，不证明真实浏览器下载后的安全例外可完成。到此停止CI绕行尝试。

用户安装说明已写在：
[中文](../../installation/macos.zh-CN.md)、
[English](../../installation/macos.md)。说明明确当前只是未放行工程候选，
未来发布后才从官方 Release 下载 DMG 及同名 `.sha256`；不声称已有 macOS Release。

Apple 官方说明支持：可信来源应用被阻止后，在“系统设置 → 隐私与安全性”针对单个
应用选择“仍要打开”，可能需身份验证；受管理设备可能受限。没有要求全局关闭安全策略。
[Apple 安全打开应用](https://support.apple.com/en-au/102445)、
[Apple 中文说明](https://support.apple.com/zh-cn/102445)。

## Release 尺寸和校验文件

GitHub 官方规定单个附件必须小于 **2 GiB = 2,147,483,648 bytes**。
本 DMG 1,886,232,812 bytes，低于限制 261,250,836 bytes，可作为单附件，毋须分卷。
正式发布应同时附原始 DMG 和 `remember-me-macos-arm64-beta.dmg.sha256`，并在 Release
文字列出同一 SHA256；Actions ZIP 的 digest 不是 DMG 的哈希。
[GitHub 官方限制](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)。

## 构建输入与供应链复查

已固定：PBS CPython 3.12.15/20261020 URL及SHA256，pip bootstrap hash-lock，
生产依赖版本，BGE-M3 revision和每文件SHA/Git/LFS身份；workflow actions使用commit SHA。
已验收构建的原生wheel候选锁、解析报告、wheel哈希和pip check记录在Step1 evidence；
安装实际使用hash校验且离线wheelhouse。没有修改模型/profile/持久化。

尚不足以宣称可复现与供应链放行：

- `build.py` 每次原生解析并生成 `macos-arm64-candidate.lock`，自身仍标为未批准候选；
  原生wheel完整URL/hash输入尚未晋升为仓库中经过维护者审核的正式固定锁。
- `macos-15` runner、Xcode/Swift工具链未固定到完整镜像身份；DMG容器/签名元数据也未
  规定确定性构建，因此**没有位级可复现保证**。
- 当前SHA可核对原包字节，但ad-hoc不证明发布者Developer ID，不能取代审查或恶意软件检测。
- Actions产物/完整候选锁仅保留14天，公开交付前需维护者保存锁、构建manifest及关键证据。

不要求为Beta建立复杂可复现基础设施；最低充分措施是固定并审核原生完整hash-lock、
保存输入清单及证据，并明确不承诺bit-for-bit DMG。现阶段不能把候选锁标签当作批准。

## 必须解决与维护者决策

公开前必须：

1. 解决3个未签名嵌套Mach-O并重新验证整个包。**推荐最低充分方案**：在构建时逐项
   ad-hoc签嵌套代码，再封签外层app，重新生成DMG和SHA，针对签名/真实下载首次打开补验。
   不需要Developer ID或公证，但会改变已验收DMG身份；作为重要安全问题提交维护者决定，
   本步骤不擅自修改签名或重新发布产物。
2. 审核并固定原生wheel hash-lock及必要输入，保存长期证据；明确工程候选的供应链放行责任。
3. 在独立真实Mac完成一次浏览器下载 → Finder安装 → Apple官方单应用例外 → 启动检查。
   如果该流程不能成功，不可用全局关闭Gatekeeper/删quarantine替代。

可接受剩余风险（需明确告知）：未公证/无Developer ID带来首次警告及单应用批准步骤、
受管理Mac可能不允许例外、所有15.x未逐一覆盖、无位级DMG可复现保证。免费分发路线本身
不要求申请Developer ID，但当前包仍有上述放行阻塞；不增加阶段，不提前执行S2签名公证。
