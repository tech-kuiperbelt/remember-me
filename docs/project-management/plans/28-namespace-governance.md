# [28] Namespace 规范化与未知 namespace 治理

## Summary

固定 namespace 的业务行为，优先解决查询误用和写入误创建问题；暂不引入 alias、namespace 独立注册表或历史数据自动合并。

## 核心行为

- namespace 列表由 SQLite 中未删除记忆的 namespace 动态派生，不新增 namespace 表；配置的 `MEMORY_DEFAULT_NAMESPACE` 额外视为已知 namespace，以支持空库 bootstrap。
- 普通查询不提供 namespace 时，继续查询全部 namespace。
- 查询提供未知 namespace 时：
  - 不做模糊匹配或 alias 推断；
  - 返回空 `items`；
  - 返回 `response_code: "namespace_not_found"`；
  - 在 `response_meta` 中返回 `requested_namespace` 和当前可用 namespace 列表；
  - MCP 响应的 `hint` 说明可用 namespace 和重试方式。
- 已知 namespace 但没有命中记忆时，仍返回正常成功结果，不能与 namespace 不存在混淆。
- 成功响应保持现有结构，不强制增加 `"ok"`，避免无意义的 response 膨胀。

## 写入与确认

- `add` 和 `update` 增加可选请求字段：

```json
"confirm_codes": ["namespace_not_found"]
```

- 未知 namespace 默认拒绝写入并返回 `namespace_not_found`。
- 首次 `add` 省略 namespace 时解析为 `MEMORY_DEFAULT_NAMESPACE`；即使 live records 中还没有该名称，也允许直接创建第一条记忆，不需要 confirmation code。
- 当 `confirm_codes` 精确包含 `namespace_not_found` 时：
  - `add` 允许创建该 namespace 的第一条记忆；
  - `update` 允许将已有记忆迁移到该新 namespace。
- confirmation code 与触发它的 validation rule 使用同一个字符串，并通过服务端校验器映射管理。
- `confirm_codes` 只能放过对应的 namespace 存在性校验，不能绕过格式、长度、保留字、embedding 或其他校验。
- 没有对应校验的确认码不产生任何效果。
- namespace 名称先执行 trim；保持大小写敏感；允许 Unicode；增加长度和空值校验。
- 不自动迁移现有 `atos-next` 3 条记录，不引入 alias；后续若需要合并，单独设计迁移行为。

## 接口与实现

- 在 service 层集中实现 namespace 检查、可用列表生成和 confirmation-code 校验，MCP 与管理 API 共用。
- `search_semantic`、`search_meta` 使用统一的未知 namespace 响应信封。
- `add`、`update` 的 MCP schema、工具描述、hint、HTTP 管理 API 请求体同步增加 `confirm_codes`。
- 审计记录保留原始 namespace 和 confirmation codes；未知 namespace 事件记录 `response_code`。
- 更新 AGENTS、backlog 与测试用例（`tests/test_namespace_governance.py`），明确 namespace 动态来源与二次确认流程。

## 测试与验收

- 查询已知 namespace、未知 namespace、未提供 namespace 的行为分别验证。
- 未知 namespace 查询返回空结果、`namespace_not_found`、可用列表和可理解 hint。
- add/update 未确认时拒绝，带匹配确认码时成功，带错误或无关确认码时仍拒绝。
- 校验确认码不能绕过 namespace 名称格式和其他基础校验。
- 首次确认写入后 namespace 出现在可用列表；删除该 namespace 的最后一条 live 记忆后从列表消失。
- MCP 与 HTTP 两种入口行为一致，audit 内容正确。
- 完整 pytest 回归通过，现有 namespace、import/export、search filter 和 dual storage 行为不回归。

## Assumptions

- 当前 namespace 数量较少，`known_namespaces` 暂时返回全部 live namespace 加默认 namespace；若未来规模明显增长，再增加分页或上限。
- namespace 只是记忆记录的属性，不单独作为空对象管理。
- [28] 不处理 namespace alias、改名和历史数据合并；这些行为另立需求。
