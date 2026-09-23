# [38] 配置与运行生命周期重构

2026-09-23 实现。将启动、资源所有权和退出顺序显式化，并在 Ops 的 Restart Server 旁增加 Shut Down Server。

## 结构

- `Settings.from_env()` 生成不可变环境快照，保留原变量、默认值及 replica 兼容别名。服务默认主库路径为 `data/memories.db`；`.env` 仍由 `bin/start.sh` 加载。
- `MemoryService(settings, storage, embedding_provider)` 持有业务依赖、导入状态和 worker；导入模块不打开数据库、不加载模型、不启动线程。
- `create_runtime(settings)` 仅构造对象。`start()` 配置日志/审计、创建 provider 和 storage、检查 active profile/index vectors、按配置预热，然后执行 dual 启动同步与健康检查，最后启动周期线程。
- `close()` 幂等。等待导入结束，再等待周期同步线程，尝试最后一次 outbox 同步，随后关闭存储/释放 writer locks、provider、审计及本 runtime 创建的日志 handler。最后同步失败保留 durable outbox；初始化中途失败也释放已取得的资源，并保留原始启动异常。
- MCP 与 admin 通过工厂绑定同一个 service/runtime。参数解析仅在 `main()` 内执行；导入模块和 `--help` 不读取运行配置或获取业务资源。审计仍为每进程单实例，由 runtime 或维护 CLI 显式配置/关闭。

## 停服与重启

- `POST /api/shutdown` 和 `POST /api/restart` 先发送完整响应，再停止接收新请求并通知 Uvicorn 正常退出。
- SSE 会话主动结束，避免长连接阻止退出；已进入的同步业务调用完成后才会执行关闭回调。HTTP 请求由 Uvicorn 排空，后台导入按用户决定一直等待完成，不用超时中断。
- UI 请求确认后禁用两个按钮；关闭提示需要从终端重新启动。请求失败时恢复按钮，并明确未确认成功；不把网络错误当成关闭成功。
- 重启在所有清理完成后使用原 Python、module 参数及继承环境执行 `execv`；关闭只退出。
- stdio EOF、SIGTERM 和事件循环取消均进入同一清理流程。管道使用可取消的异步读取，避免 SDK 默认线程 readline 在客户端仍持有 stdin 时阻塞退出；普通文件输入保留 SDK 读取方式。

## 验证

- `.venv/bin/python -m pytest -q --tb=short`：374 passed，1 条既有 Starlette TestClient/httpx 弃用提示。
- `.venv/bin/python tests/run_admin_browser.py`：通过。`git diff --check` 通过。

- 生命周期回归覆盖配置快照、模块导入/`--help` 无资源副作用、启动故障注入与锁释放、重复 start/close、导入和同步等待、最终 outbox 同步失败保留 pending、先响应后关闭、新请求拒绝。
- 隔离子进程运行真实 MCP initialize/tools/list/tools/call：stdio EOF/SIGTERM；SSE 保持连接时关闭、重启和 SIGTERM。重启测试在 exec 边界验证锁已释放及 argv，替身不加载宿主模型。
- 回归包含非 Ollama provider 的查询/批量路径、HTTP 导出响应及已有工具/审计契约。
- Chrome 回归覆盖关闭确认取消、网络失败、成功后的按钮状态和终端启动提示，并保留关键词及特殊字符用例。
- 未提交。用户在 11:48 通过 UI 重启过运行中的服务；本次最终收尾后未再次部署。数据库职责拆分、并发执行器和 Streamable HTTP 不在本阶段范围内。
- 重启后的真实连接检查：旧 agent 会话收到 `-32602`，服务日志为 `Received request before initialization was complete`；新会话完成 initialize 后，stats/get_details/list_keywords 全部成功。客户端重连必须重新执行 MCP 初始化握手，仅恢复 SSE 连接并直接重试工具不足以恢复会话。
