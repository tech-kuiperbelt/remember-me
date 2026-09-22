# [37] 第一阶段技术债修复

2026-09-22 实现并验证，随本次提交归档；未重启生产。范围仅限测试隔离、HTTP 错误分类、前端动态数据绑定；不改存储事务或部署方式。

## 测试隔离

pytest 配置阶段强制覆盖继承的 MEMORY_/EMBED_/OLLAMA_/FASTEMBED_/SENTENCE_TRANSFORMERS_ 设置。默认 SQLite :memory:、namespace=default、Ollama 配置、预热关闭，provider 地址指向不可用的测试端口；业务测试显式注入假向量，不加载宿主模型。审计写入本次测试独立临时目录，结束时关闭审计、清理目录并恢复原环境。需要测试其他配置的 fixture 仍可显式覆盖。

子进程回归故意继承指向“生产样本”的数据库、副本、审计及 Sentence Transformers 配置，验证样本不变、不生成 writer lock、不加载模型、临时审计目录被清理。

## HTTP 错误契约

- namespace 类型/格式校验抛出 InvalidNamespace（继承 ValueError）；HTTP 保留 invalid_namespace / 400。
- 记忆列表、添加、更新、搜索的 JSON/对象/必要字段/数字转换错误返回 invalid_request / 400，包含 detail 与 response_meta.reason。
- 搜索过滤词超过既有 20 项限制通过 InvalidSearchFilter 显式识别，返回 invalid_request / 400。
- 内部未知异常（包括内部 ValueError）仍返回 500，不包装成用户 namespace 错误。
- 保留 namespace_not_found 与确认流程、embedding_input_too_long / 413；正常请求响应、默认值和 summary 既有容错不变。
- MCP 工具名、schema、hint 不变；namespace 异常仍兼容 ValueError，错误审计会体现更具体的异常类型。

## 前端

namespace 页签、搜索下拉框、详情编辑输入及候选选择使用 DOM 属性/事件绑定，避免将数据拼入事件代码或 HTML 属性。记忆列表/搜索/详情按钮闭包传递原始 ID，URL 中编码 ID；动态 title 使用 DOM 属性。错误消息使用 textContent，保留已有布局和交互；静态模板与静态按钮不全面改写。

## 验证

- `.venv/bin/python -m pytest -q`：354 passed，1 条既有 Starlette TestClient/httpx 弃用提示。
- `node --check`：页面脚本和浏览器回归脚本语法通过。
- `.venv/bin/python tests/run_admin_browser.py`：通过。该可重复入口要求 PATH 上有 google-chrome，在随机回环端口启动内存库服务，使用假向量与临时审计/浏览器目录，不使用生产数据或模型。
- 浏览器覆盖单/双引号、反斜杠、中文、HTML 片段的 namespace；列表/搜索/详情操作正确传递特殊 ID，URL 编码，错误消息不产生 HTML 元素/脚本；保留关键词字面/语义、分页、精确跳转、选择保留及过期响应回归。
- `git diff --check` 通过。保留用户原有 [35] TODO 修改。

后续配置/runtime 生命周期重构另行计划，本阶段不实现依赖容器、并发执行器或数据库职责拆分。
