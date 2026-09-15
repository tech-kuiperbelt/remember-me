# [33] 移除本地 Ollama Keywords 生成功能

## 目标

服务端不再使用本地 qwen/Ollama 从正文生成或回填 `metadata.keywords`。关键词是检索约束；缺失关键词的成本低于错误抽取造成的错误过滤。正常写入只接受调用方直接提供的 keywords，缺失时保持为空。

## 保留边界

- 保留 add/update 的 `metadata.keywords`、归一化、SQLite index、index-value embeddings、语义 filter、JSONL Import/Recovery 和已有数据。
- 不迁移、不清空历史关键词，也不为缺失值使用规则 fallback。
- 保留 `python -m app.keyword_migrate_deepseek` 作为人工明确执行的离线 DeepSeek 回填 CLI；它不经 MCP 服务或管理台运行。

## 删除与验收

- 删除 `app.keyword_extract.py`、`memory_extract_keywords`、`POST /api/extract-keywords` 和管理台 Extract Keywords 卡片。
- 更新服务/架构文档，明确本地 Ollama 仅仍可作为可选 embedding provider，而非 keyword 生成器。
- 测试 caller-provided keywords 的索引/更新语义、DeepSeek CLI 的独立边界，以及本地模块/API/UI 的删除；完整测试套件通过。
