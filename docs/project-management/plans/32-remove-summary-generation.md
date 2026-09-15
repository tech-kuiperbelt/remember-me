# [32] 移除服务端 Summary 生成流程

## 决策与目标

Summary 生成不再依赖 Ollama 或其他生成模型。原因是生成式摘要增加常驻资源、模型下载和部署配置，而 MCP 调用方本身通常已是 LLM，能够直接提供质量更高的摘要。`summary` 字段和 add/update 输入保持兼容，但服务端只提供确定性的无模型 fallback。

不使用正文 embedding 的余弦相似度判断摘要是否仍然有效：高相似度不能可靠识别数字、状态和结论变化。

## 写入规则

- 有效 caller summary 去除首尾空白后直接保存，不重写调用方内部格式。
- add 缺少 summary 时，抽取正文第一个非空有效行；去除 Markdown 标题/列表前缀，短标题与下一行拼接，80 字内优先取完整首句，超长截断并追加省略号。
- update 提供 summary 时保存新值；不更新正文时保留旧摘要。
- update 正文经空白标准化后与旧正文相同，保留旧摘要；否则使用新正文抽取结果。
- 上述 update 判断在 SQLite 写锁和事务范围内完成；duplicate add 不修改已有记录。

## 历史数据与迁移

- 不进行数据库迁移或历史 summary 回填。
- JSONL Import 和离线 Recovery 忠实保留来源 summary，包括 null。
- MCP search/list 及管理列表对 null summary 动态展示相同的确定性抽取；详情仍保留真实字段状态和完整正文。

## 删除内容与验收

- 删除摘要模型调用、worker 队列、启动扫描、关闭钩子和 worker 专用存储接口。
- 删除管理台手工 Summarize API/UI，移除 `SUMMARIZE_MODEL` 和统计字段；`OLLAMA_BASE_URL` 继续供可选 Ollama embedding provider/keyword 工具使用。
- MCP tool description/hint 与 audit `summary_mode` 更新为确定性行为。
- 单元测试覆盖抽取规则、add/update/duplicate、历史 null 展示、Import/Recovery 边界及 API/UI 删除；全量测试必须通过。
