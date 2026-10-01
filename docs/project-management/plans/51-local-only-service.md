# [51] 仅本机服务配置与局域网访问

2026-10-01 实现并验证。默认仅监听 loopback；`local_only: false` 时监听 `0.0.0.0`，
局域网可访问全部 HTTP 入口，`/mcp` 关闭 Host/Origin 校验（无鉴权，仅限可信网络）。

## 配置与监听

- 新增布尔配置 `local_only`，默认 `true`；旧配置缺少此字段时也使用 `true`。非布尔值报配置错误。
- `true` 时 HTTP 监听 `127.0.0.1`；`false` 时监听 `0.0.0.0`，允许局域网访问所有 HTTP 入口，
  包括管理页面、SSE、`/mcp` 与全部 `/api`。
- 配置接入 `Configuration` 校验、JSONC 保存（保留注释）及不可变 `Settings` 快照
  （`MEMORY_LOCAL_ONLY`）。`GET/POST /api/config` 与兼容入口支持该字段。
- 启动时按快照选择监听地址和 `/mcp` 安全策略：本机模式保留 loopback Host/Origin 白名单；
  局域网模式显式关闭 SDK 的 DNS-rebinding 保护（`enable_dns_rebinding_protection=False`），
  端点预校验与会话管理器使用同一策略。
- 保存配置只写文件，当前进程的监听地址与传输策略保持启动时的值，重启后一起生效；
  stdio 不受影响；`--port` 仍优先。启动日志打印实际绑定地址与访问范围。

## 设置页面与提示

- 普通设置区域增加“仅本机服务（推荐）”复选框，与端口/语言一起加载、编辑和保存，不要求维护模式。
- 常驻说明：勾选仅监听 `127.0.0.1`；取消则监听 `0.0.0.0`，局域网可访问全部 HTTP 接口；
  修改后需重启，`--port` 仍优先。
- 取消勾选或加载到已保存的 `local_only: false` 时显示醒目的红色警告：会关闭 `/mcp` 的
  Host/Origin 防护并把全部接口暴露到局域网；服务无账号/密码，同网段任何设备可读取、写入、
  删除记忆、导入导出数据，甚至关闭或重启服务；仅限完全可信网络。
- 保存成功提示包含“访问范围重启后生效”。使用常驻文字提示，不增加额外确认弹窗。

## read-me 与文档

- `/read-me` 自引用仍为 `http://127.0.0.1:<port>`（即便在局域网模式），并注明局域网客户端需
  把主机替换为服务所在机器地址。`app/read_me.md` 更新 loopback 限制描述。
- `AGENTS.md` 的 HTTP 绑定、`/mcp` Host/Origin、`/read-me` 说明更新为按 `local_only` 区分。
- `docs/operations/runbook.md` 增加 `local_only` 字段、风险说明，以及“已有远程部署升级后需
  显式设置 `local_only: false` 才能继续远程访问”的迁移提示。

## 验证结果

- 全量 `python -m pytest -q`：720 passed。
- 浏览器回归 `tests/run_admin_browser.py` 全部场景通过；配置页断言覆盖复选框加载、取消时
  显示局域网警告、保存传递 `local_only`、以及“重启后生效”提示。
- `local_only` 默认/旧文件兼容/布尔校验/Settings 传播、`resolve_bind_host`、
  本机模式仍拒绝远程 Host（421）/Origin（403）、局域网模式不再拒绝且保留 POST-only（405）
  均有测试覆盖。
