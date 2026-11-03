# Windows S1-A-P 最小修正报告

日期：2026-11-03。状态：本地修正供审阅；S1-A 原生验证仍待执行。

## 修改范围

- `packaging/windows-poc/build.ps1`：保留 12 GiB 阈值；在检查前建立证据目录和 transcript，写出 `disk-preflight.json`，记录盘符、实际可用字节、要求字节和判定原因，并在抛错前输出日志。不清理 runner 软件。
- `packaging/windows-poc/verify.py`：推理完成且维度、归一化通过后，从已打包应用导入现有 `EmbeddingSpec` 元数据类型，检查逻辑模型 BAAI/bge-m3 的 profile ID 为 `ep_6134c0b15d2bb471`，在结果中输出 profile。无需启动第二个模型、服务或存储，不修改核心代码。物理 revision 和逻辑模型身份分别记录；此项不代表 Windows 业务 provider/存储集成通过，结果明确标记该项为 S1-B 待验收。
- `.github/workflows/windows-s1a-poc.yml`：在下载 PBS 前记录文件系统磁盘状态，使用 PowerShell 原生 Parser 检查 `build.ps1`；解析错误终止后续构建，独立预检目录随 always artifact 步骤上传。
- 本报告与 `packaging/windows-poc/README.md`：记录修正结果和边界。

## 实际本地检查

通过 Python 3 AST 解析两个 POC Python 脚本；从既有源码 AST 提取并执行未改动的 EmbeddingSpec，确认上述元数据生成预期 ID（不加载模型、不访问数据库）。通过 PyYAML 解析全部三个 workflow，并断言预检位于构建前、独立证据路径包含在上传范围中、只读权限和专用分支条件成立。静态核对磁盘记录先于空间不足抛错，且先于网络下载，阈值仍为 12 GiB。`git diff --check` 通过。

全部 workflow 触发核查：

| Workflow | 事件 | 专用分支 Push 的结果 |
|---|---|---|
| docker-amd64-ci.yml | 仅 workflow_dispatch | 不触发 |
| docker-release.yml | 仅 workflow_dispatch | 不触发 |
| windows-s1a-poc.yml | push，精确分支 poc/windows-s1a-native | 仅 Windows POC；另有仓库/ref job 条件 |

本地缺少 pwsh 和 actionlint，未执行 PowerShell 解析器或 Actions 专用 lint。上述顺序与路径检查是静态断言，不是原生执行结果。

## 未验证及剩余风险

Windows 上的磁盘失败路径、Parser 运行、PBS 搬移、依赖解析/安装、DLL、模型真实推理及 profile 输出均待执行。元数据 ID 相等不证明 provider 与数据库集成；该项留待 S1-B。宿主提供 DLL、原生库网络隔离和整个 job 超时时证据上传的既有局限仍存在，本次未扩展修改。GitHub 管理的 runner 镜像和实际可用磁盘/内存仍需观察。

未提交、未 Push、未运行 GitHub Actions、未 Release，未进入 S1-B。等待维护者审核及后续单独授权。此前 ~/tmp 的 ZIP 是修正前快照，本次未更新。
