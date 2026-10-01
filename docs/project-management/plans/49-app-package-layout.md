# [49] app 按职责分包

基线：`ba28ed5`（[48] Phase 5 已完成，683 项 pytest 通过）。状态：已实现并验证，未部署。

## 目标与约束

按现有职责整理目录，降低代码定位和依赖判断成本。仅移动模块、调整引用和提取命令入口，
不切分 SQLiteStorage、MemoryService 或 AdminAPI，不改变资源所有权、事务边界、
MCP/HTTP 契约、日志名称、配置及 SQLite/JSONL/embedding 格式；无需数据迁移。

## 目录与依赖

根目录保留 server、runtime、service、audit、log_config 和 read_me.md。

| 包 | 模块 |
|---|---|
| config | configuration、settings、jsonc |
| domain | anchors、categories、namespace_policy、summary_policy、lexical |
| storage | base、factory、sqlite、dual、ndjson、database_lock、jsonl_transfer、recovery |
| embedding | embed、provider_adapter、embedding_codec、index_value_resolver |
| transport | admin、tools、tool_definitions、report_feedback |
| operations | calls、maintenance、import_coordinator、model_preparation |
| cli | manage_cli、embedding_cli、recover_cli、config_migrate、deepseek_cli、三种 *_migrate_deepseek |

原 storage.py 拆为 base（接口、异常、常量与辅助函数）和 factory（延迟加载的装配）。
storage/__init__.py 显式导出公共契约、异常、默认 namespace、路径解析与工厂，具体实现
直接导入 base。其余包的 __init__.py 仅说明职责，不集中加载模块。

Domain 保持纯规则；Storage/Embedding 不反向依赖 Service、Runtime、传输层；
Runtime 装配资源，Service 使用已有契约；在线业务不依赖 CLI。

## 兼容与实施

1. 确认 Phase 5 基线干净，移动配置/领域/存储/embedding 后验证相关行为。
2. 移动协调、传输、CLI，更新所有内部导入、子进程模块字符串与测试 monkeypatch。
3. 原有六个 CLI 模块保留薄入口，通过实现模块的 entrypoint 共享初始化与退出策略；
   server 启动/重启及 bin 命令不变。其他旧内部 import 路径不保留兼容层。
4. 修正 Configuration 的项目根目录深度；Admin 从 PROJECT_ROOT 定位页面；
   read_me.md 不移动，保留 server 的相邻定位。所有延迟导入、模型加载时机保持原状。
5. 更新 README、AGENTS 和当前项目管理记录；历史实施记录保留原始路径。

## 验收

- 旧/新六个 CLI 的帮助、错误参数、输出和退出码一致；帮助不初始化配置/数据库。
- 实际主库持有写锁时，旧/新 status 和回填 dry-run 均可运行，不创建配置或副本。
- 新包和兼容入口在独立子进程按正反顺序导入，无循环依赖、线程、根日志 handler、
  模型加载或数据文件创建；storage 包导入不加载具体实现。
- 从不同 cwd 验证相对数据根目录、Admin 页面和 /read-me。
- 全量 pytest、浏览器回归通过，检查三种传输、停服/重启、维护、审计、存储一致性。

本次不执行生产重启或部署。

## 实施结果

- 已完成上述七个包的迁移；保留六个旧 CLI 启动模块，共享实现模块的 entrypoint。
- 相关配置/存储/领域/embedding 测试 167 项通过，CLI/运行时/传输等测试 104 项通过。
- 新增 `tests/test_package_layout.py`：20 项兼容、只读锁、无副作用导入、资源定位回归通过。
- 全量 703 项 pytest 通过（7 条既有依赖弃用警告），隔离 Chrome 浏览器回归通过。
- `git diff --check` 通过。代码和测试中剩余旧模块字符串仅为兼容命令提示；
  对比移动前后 AST，类/函数体除导入和 CLI 模块派发路径外未变。
- 已更新 README、AGENTS、系统设计中的有效文件路径及项目归档；历史计划不重写。
