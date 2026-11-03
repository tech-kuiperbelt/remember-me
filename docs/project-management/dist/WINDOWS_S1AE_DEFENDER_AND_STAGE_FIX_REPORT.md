# Windows S1-A-E 第三次失败最小修复报告

2026-11-03。仅本地准备与验证，未 Commit、Push、第四次 Actions 或进入 S1-B。

## 修改与实施成本

只改 verify.py，新增 defender_identity.ps1 和 tests/test_windows_poc_dll_audit.py。使用 Python 标准库及 Windows 自带 Windows PowerShell、注册表、Get-AuthenticodeSignature/Get-FileHash/FileVersionInfo；每个精确候选启动一次只读进程，超时 60 秒。不新增依赖或常驻组件，不关闭 Defender，不改变固定输入或业务代码。

## 精确 DLL 分类

保留私有 runtime 和 Windows 系统目录原有来源规则。外部 DLL 仅允许额外尝试以下候选：ProgramData/Microsoft/Windows Defender/Platform/<四段数字版本>-<数字>/MpOav.dll，不能含额外路径层级。

候选必须全部满足：

1. 采集路径与已经 resolve 的已加载路径相同。
2. 采集前后实际 SHA256 相同且格式有效；摘要仅作为实际证据，身份信任来自签名与注册核对，不把本地计算摘要当作独立可信证明。
3. Authenticode 状态为 Valid；签名者 SimpleName 是 Microsoft Windows、Microsoft Windows Publisher 或 Microsoft Corporation，Subject 的组织为 Microsoft Corporation，记录证书 thumbprint。
4. 原始文件名 MpOav.dll、公司 Microsoft Corporation、文件版本与目录中四段版本一致。
5. HKLM 64-bit SOFTWARE/Microsoft/AMSI/Providers 中存在有效 GUID，且 HKLM SOFTWARE/Classes/CLSID/<GUID>/InprocServer32 注册文件的展开绝对路径与实际加载文件完全一致。HKLM 登记只读，不改注册，不依赖 HKCU 覆盖。

通过才分类 verified_host_defender；未知外部项、签名无效、注册/身份不匹配、采集超时或失败均保持 unknown_external，最终审计失败。没有放行 ProgramData 或整个 Defender 目录。身份 JSON 保留原始注册项、展开路径、签名状态/消息、签名者、摘要和文件版本；采集失败留下错误记录；dll-audit.json 保存全部分类。

微软资料依据：[AMSI Provider 注册](https://learn.microsoft.com/en-us/windows/win32/amsi/dev-audience)、[Defender MpOav.dll 与注册核对示例](https://learn.microsoft.com/en-us/exchange/antispam-and-antimalware/amsi-integration-with-exchange)、[Authenticode 状态](https://learn.microsoft.com/powershell/module/microsoft.powershell.security/get-authenticodesignature)。这些说明机制，不能替代本机实际身份采集。

## 阶段结果顺序

模型数学和 profile 全部通过后，先采集实际 pooling、向量 shape/范数、原始 CLS 最大差、load/encode 时间、进程内存计数、bundle 字节及磁盘可用空间，写 model-results.json：status=partial、model_validation=passed、dll_audit=pending。

然后采集最终 DLL，执行身份和来源审计及剩余断言；全部完成才将 status/dll_audit 更新为 passed 并写 native-results.json。失败时阶段文件保留，不能将 partial 当作整个 POC 成功。没有修改原模型/CLS/Normalize/1024 维/profile 数学断言，AST 比较确认该代码块与 HEAD 一致。

## 第三次真实证据回归与测试

使用 Run 41594920241 的真实 loaded-modules.json：共 205 项，162 在私有 runtime，42 在 Windows，唯一外部是 C:/ProgramData/Microsoft/Windows Defender/Platform/4.18.26100.4-0/MpOav.dll。没有原生签名、摘要和注册数据，不能追认第三次 DLL 为已验证。

6 项本地测试通过：严格有效身份夹具；无效签名/签名者/摘要变化/版本/注册路径等拒绝；路径范围拒绝；未知外部模块拒绝并留证据；205 项真实清单搭配模拟身份分类回归；审计失败时 model-results 保留且无 native-results。所有签名与注册结果均为模拟，不是实际 Windows 验收。

原有 5 项 Pooling 测试在固定版本 Linux Standalone 通过；模型数学/profile 代码块 AST 不变；Python AST/compile 和 git diff --check 通过。

## 尚未验证与限制

本机没有 pwsh，未执行新 PowerShell 脚本的原生解析器、Authenticode、注册表采集或文件版本检查。后续 Windows 必须核对真实 signer/version/注册路径；严格目录版本/注册精确匹配可能因 Defender 更新或注册别名而安全失败，不能静默放宽。签名验证使用 Windows 自身信任策略，可能涉及系统证书/吊销查询，不是本次离线模型网络证明的一部分。

没有提供 PE 导入图、进程内文件映像 hash 或完整反恶意篡改证明；磁盘前后 hash 检查也不能消除所有竞态。现有 System32 VC++ 宿主依赖、导入/模型构造失败前证据缺失、整个 job 超时仍是既有限制。本轮只解决指定外部组件识别和模型通过后的结果保存，不扩展为通用 DLL 安全框架。

新脚本同 verify.py 位于仓库 POC 目录，原生执行从该目录读取；现有 build input glob 会记录它。新 JSON/错误文件均在现有 evidence artifact 范围中。停止等待审核。
