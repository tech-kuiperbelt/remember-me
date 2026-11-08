# macOS S1 / Step 1 构建与验证报告

2026-11-08：**达到本阶段规定的自动化验收要求**。已交付包含
`Remember Me.app` 的完整离线 DMG；最终原生 CI 全部通过。下列 GUI 行为仍需
真机手工验证，不包含在通过结论内。

## 候选与下载

| 项目 | 结果 |
| --- | --- |
| 测试分支 | `poc/macos-s1-step1` |
| 已验证源码 | `b4bce53d64d644e906d0176aa5d4328beafe9e3b` |
| Workflow | [macos-beta.yml](../../.github/workflows/macos-beta.yml) |
| 成功运行 | [42457140969](https://github.com/tech-kuiperbelt/remember-me/actions/runs/42457140969) |
| DMG 与 SHA256 下载 | [macos-arm64-beta-42457140969](https://github.com/tech-kuiperbelt/remember-me/actions/runs/42457140969/artifacts/13607344736) |
| 构建与验收日志 | [macos-arm64-evidence-42457140969](https://github.com/tech-kuiperbelt/remember-me/actions/runs/42457140969/artifacts/13608366330) |
| DMG 文件名 | `remember-me-macos-arm64-beta.dmg` |
| DMG 大小 | **1,886,232,812 bytes**，约 1.89 GB / 1.76 GiB |
| 产物保留期限 | 2026-11-22；GitHub Actions 下载需登录 |

DMG 文件本身的 SHA256（不是 Actions ZIP 的 digest）：

```text
dced0eec97d12287f8767c37bdd757ae9d5ec1f91c83bef117f33c7d15ea222c
```

下载并解压 Actions 产物后，在 DMG 所在目录运行：

```bash
shasum -a 256 -c remember-me-macos-arm64-beta.dmg.sha256
```

将 DMG 内的 `Remember Me.app` 复制到本地目录，弹出磁盘后启动。
构建、启动及数据目录说明见 [README.md](README.md)。

## 实现

- 复用 PBS CPython **3.12.15 / 20261020** 和现有生产依赖版本；ARM64 原生
  PyPI wheel 经 SHA256 校验后从离线 wheelhouse 安装，`pip check` 通过。
  原生候选 lock、解析报告、wheel 哈希及 PBS 输入哈希已保存在构建日志产物。
- 复用 `docker/package_model.py`、模型 manifest 和许可证。BGE-M3 固定 revision
  `5617a9f61b028005a4858fdac845db406aefb181`，完整文件逐一校验。
- `.app/Contents/Resources/` 保持现有 app/web/models 布局，包含完整 Python
  运行环境。Swift AppKit 启动器管理现有 HTTP 服务，浏览器入口不引入新业务架构。
  终端验收使用同一原生可执行文件的 `--no-browser` 模式，以 exec 保持进程所有权。
- 原生启动器正常退出请求 SIGTERM，并等待服务关闭；服务另有启动器存活监测。
  Web Restart 复用现有 `exec_restart=False` 清理后重启循环。
- 数据保留在 `~/Library/Application Support/remember-me/`。继续使用 dual、
  原有配置和模型缓存机制、数据库格式及写锁；不迁移既有数据。
  服务实际 profile 为既有的 `ep_6134c0b15d2bb471`。
- `MEMORY_OFFLINE_BUNDLE=1` 仅由离线启动器声明：禁止自动下载及显式模型重试下载，
  保留用户已保存的配置，不改变 HTTP/MCP Host/Origin 或 LAN 策略。
- 审计 Mach-O ARM64 架构和加载依赖，排除系统库以外的绝对加载路径。
  区分 Universal2 输出标题、库自身 `LC_ID_DYLIB` 与真实加载依赖。
- 仅生成 ARM64 运行所需的本机 ad-hoc 代码身份；无 Developer ID、Apple 公证或
  正式分发身份。未合并 master、创建 Release/Tag 或修改 `release.jsonc`。
  包内显示沿用的 **1.1.2** 元数据，不代表新增正式 macOS Release。

## 实测结果

环境：GitHub Actions `macos-15`，实际 **macOS 15.8.2 ARM64**，7 GiB 内存，
Xcode 16.4。构建 4 分 06 秒，最终包验收 1 分 45 秒，完整 job 6 分 24 秒。
包内 PyTorch **2.14.1**、Sentence Transformers **5.7.0**。

| 自动化验收 | 最终结果 |
| --- | --- |
| 最终 DMG 验证、只读挂载、复制其中的 app | 通过 |
| 使用包内 Python，隔离宿主 Python / user site | 通过 |
| macOS 沙箱拒绝外网连接，外连探针返回 EPERM/EACCES | 通过 |
| BGE-M3 中英文 CPU 推理，形状 `(2, 1024)`，有限且归一化 | 通过 |
| CLS Pooling 和 Normalize，推理设备 CPU | 通过 |
| Web UI、CSS、JS、read-me 和 MCP HTTP initialize/tools | 通过 |
| MCP 记忆创建、更新、详情读取、中文语义检索、删除 | 通过 |
| 正常关闭后重新启动，更新内容与检索结果持久化 | 通过 |
| 重复启动拒绝同一数据目录 | 通过 |
| 独立进程争用 SQLite primary 和 JSONL replica 写锁 | 均被拒绝 |
| API 关闭及 SIGTERM 后服务完整清理 | 通过 |
| 退出后监听端口关闭、进程组清空、两把写锁重新获取 | 通过 |
| 退出后 SQLite integrity / foreign key 检查、JSONL 落盘 | 通过 |
| app 移到另一含空格路径，重新启动与检索 | 通过 |
| 推理、CRUD、关闭、搬移前后 app 内容/链接/权限哈希 | 完全一致 |

主进程退出时可短暂看到退出中的 Python 辅助进程；验收有界等待最多 10 秒，
两轮最终记录均为 `After drain: []`，并继续实测端口关闭和写锁释放。
`initial-processes.log` / `relocated-processes.log` 保存了原始身份记录。

Linux 本地另有 **150 项相关回归通过**，覆盖配置、embedding 工厂、离线下载策略、
平台文件、数据库锁和版本读取；这些结果仅作为代码回归补充。

最终失败项：**无**。此前运行分别修复 Universal2 标题误判、`LC_ID_DYLIB`
误判和退出检查缺少辅助进程收尾等待；记录保留在
[42456091672](https://github.com/tech-kuiperbelt/remember-me/actions/runs/42456091672)、
[42456315349](https://github.com/tech-kuiperbelt/remember-me/actions/runs/42456315349)、
[42456649374](https://github.com/tech-kuiperbelt/remember-me/actions/runs/42456649374)。

## 未验证与边界

- Finder 拖入 Applications、Dock/菜单外观、自动打开默认浏览器。
- AppKit 菜单 Cmd-Q，以及 GUI 启动器强退/崩溃后的存活监测行为；自动化验证的是
  同一包内入口的前台服务、API 关闭和 SIGTERM 清理。
- 独立真实 Mac 上的 Gatekeeper/quarantine 提示与首次打开流程。
- Web Restart API 未在本次脚本中单独触发；已验证完整退出后重新启动。
- 原生依赖 lock 仍标为工程候选，未提升为正式发行批准输入。

最低目标为 macOS 15+，本次实测系统为 15.8.2；未覆盖所有 15.x 小版本。
不包含 Intel Mac、Developer ID、Apple 公证或公开 Release；不提前进入 S2。
