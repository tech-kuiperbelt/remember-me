# Codex — Implementation Plan (front end first)

## 红线
- 先盘点代码，再修改；只针对 Configuration / 共享 Shell 状态显示所需的前端范围。
- **不擅自修改后端 API、Schema、业务语义、安全门控、MCP 工具、离线管理命令**。Debug 配置已接入，复用既有读写契约。
- 不新增 gRPC 端口、存储路径、Logging 等效果图中误画的设置；不暴露 reindex/activate/embedding 重算。
- 对外文本、禁用原因与能力以真实后端为准；不要伪造进度、模型列表、结果数。

## Phase 0 — Read-only investigation
1. 检查 `page-ops` / `ops.js`、Shell 状态组件、样式 token、Light/Dark 机制和后端已有配置/维护 API。
2. 梳理原控件→API→状态守卫→反馈，逐项对照 `INTERACTION_AND_STATE.md`。
3. 核对 `debug` 的读写路径、默认值、后端更新白名单，以及“刷新页面生效”行为；按当前实现复用，不新增启动覆盖。
4. 汇报差异，尤其是模型重试、Restart/Shutdown 特殊允许状态、失败任务是否允许 Exit、代理隐藏凭据语义。

## Phase 1 — Information architecture & Shell
1. 在既有「配置」内落地 Settings / Maintenance 两 Tab（不引入新一级导航）。
2. Shell 右侧统一 5 态胶囊，支持跨页面一致状态与适度 hover 详情，错误时禁用写操作。
3. 切 Tab 不触发维护状态改变，复用后端全局状态。Light/Dark 复用既有 token。

## Phase 2 — Settings
1. Appearance：主题即时本地生效，与服务配置保存流程隔离。
2. Service Configuration：完整字段 `port/language/local_only/debug/auto_download_model/proxy`，正确空值保留代理凭据。
3. 脏态/校验、保存成功反馈、待重启提示、临时状态变化与模型未就绪自动重试反馈。
4. 开放局域网的实时红色安全警告和保存确认；不自动重启。

## Phase 3 — Maintenance control & Model
1. 顶部 Maintenance Mode 卡片，展示状态/当前动作/错误与 Enter/Exit。
2. Model Management 卡片自适应展开：已就绪简洁、准备中进度、失败可重试。
3. 所有按钮接入既有门控，不因卡片折叠绕过限制。

## Phase 4 — Data and Server tools
1. Data Transfer：Import/Export 折叠表单、进度与完成统计、下载头解析。
2. Data Management：Sync 直接执行；Migrate 表单、记录数与确认。
3. Server：Restart/Shutdown 确认与安全状态限制；Restart 有限重连并处理地址变化。

## Phase 5 — Danger Zone & regression
1. Recover/Clear 共用 8 位随机确认码交互，分开说明影响范围。
2. 验证数据修改完成后刷新 Memory/Stats、任务互斥、错误就地展示。
3. 检查两主题、窄屏、长文案、多语言混排、键盘操作/焦点和屏幕阅读器说明。

## 验收场景（至少覆盖）
- Settings/Maintenance 切换不改变维护状态；Shell 跨页面状态一致。
- 正常运行时所有维护操作可查看、执行按钮禁用且提示原因；可主动 Enter。
- 维护就绪后正确开放按钮，任务运行中互斥、Exit 禁用；结束后不自动 Exit。
- 模型未就绪自动维护、重试、失败、下载未知总量、取消中的禁用逻辑。
- 状态 API 不可用时 fail closed；维护异常保留后端允许的 Sync/Restart/Shutdown 修复入口。
- Debug 默认 false、保存后刷新生效、无 `--debug` 优先级显示。
- 代理保存值保留/开关反复切换/修改地址/脱敏 URL 拒绝。
- 本机→全网监听展示安全说明，保存需确认；需重启的修改不自动生效。
- Import 大小/记录数、进度统计；Full 与 Namespace 导出；Sync、迁移确认。
- Recover 与 Clear 都要求准确输入随机 8 位码；操作结束状态与刷新正确。
- Restart 重新连接成功/超时/端口变更；Shutdown 不伪装能从网页恢复。
- 最终 UI 仅有需求确认过的字段，不因图片新增虚构选项。
