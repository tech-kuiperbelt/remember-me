# [34] 统一离线运维入口与 Runbook

2026-09-23 实现。新增 `bin/manage`，统一项目路径、`.env`、`.venv` 和默认数据库/副本路径；新增 Runbook 记录启动、关闭、Full Sync、离线维护、恢复和 Stats 验收流程。Runbook 现位于 `docs/operations/runbook.md`，根目录 `README.md` 保留项目入口与文档链接。

入口复用已有 Python CLI，不复制维护逻辑。embedding status 作为只读状态检查；embedding reindex/codec/profile、keyword/category backfill 和 JSONL recovery 继续使用原有 writer lock、provider/profile 校验和原子替换保护。关键词和分类回填支持 `--dry-run`。

验证：所有帮助入口和未知命令处理通过；完整 pytest 回归保留协议、存储和 UI 覆盖。写操作要求先关闭服务，维护后检查 `pending=0`、`in_sync=true`、`missing=0`、`extra=0`、`corrupt_lines=0`。
