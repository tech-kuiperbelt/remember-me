# Windows S1-A-E 第二次失败最小修复报告

2026-11-03。仅本地修复验证，未 Commit、Push、执行 Actions 或进入 S1-B。

## 最小代码 diff

`packaging/windows-poc/verify.py`：

```python
poolings = [m for m in model if isinstance(m, Pooling)]
assert len(poolings) == 1 and poolings[0].get_config_dict()["pooling_mode"] == "cls", 'Expected pure CLS pooling'
```

替代旧 boolean 实例属性访问；要求恰好一个纯 CLS Pooling，拒绝 Mean、混合及多个 Pooling，不降低标准。保留 Normalize、中英文 1024 维、单位范数、归一化原始 Transformer CLS 数值比较、既有 profile 身份和最终 DLL 来源检查。

仅增加一次模型加载后的早期 modules() 快照，写入 loaded-modules-before-model-checks.json，在所有模型断言前保存。后续任一模型断言失败仍保留该文件；最终 loaded-modules.json 继续在推理后采集并审计，不用早期快照替代最终结果。此处前移适用于模型构造成功后的断言失败，不解决导入/模型构造自身失败或硬退出的证据保留。

新增 `tests/test_windows_poc_pooling.py`，执行实际脚本中的早期快照和模型检查语句，使用真实 5.7.0 Pooling/Normalize 对象。测试中的 DLL 路径为明确标记的 mock，不是 Windows 枚举结果。

## 固定 API 检查

核对本地 Sentence Transformers 5.7.0 源码：Pooling.get_config_dict() 包含 pooling_mode，旧 boolean 配置在加载时转换；Normalize、模型索引/迭代、local_files_only、encode(convert_to_tensor=True, normalize_embeddings=False)、model.tokenize 及 Transformer forward 可用于当前固定版本。真实回归覆盖了上述模型路径与原始 CLS 比对，未发现另一个确定性兼容错误。

models 导入路径和 tokenize 有弃用警告，当前固定版仍可工作，本次不作无关迁移。

## 实际本地验证

1. 5 项针对性测试通过：纯 CLS 接受；Mean、混合、多/缺失 Pooling、缺失 Normalize 拒绝；失败前早期快照已落盘。先在开发 venv 执行，再在版本完全匹配的 Linux Standalone 执行，均通过。
2. 开发 venv Torch 2.14.0+cpu / Transformers 5.17.0 不匹配，未使用它的真实推理结果作为固定依赖回归结论。改用已有 Standalone：PBS Python、Torch 2.14.1+cpu、Sentence Transformers 5.7.0、Transformers 5.20.2。
3. 重用已有模型快照，先按固定 manifest 校验全部 11 文件（size、SHA256、Git/LFS 身份）；revision 保持 5617a9f61b028005a4858fdac845db406aefb181。缓存/数据/输出均指向新建 /tmp 隔离目录，禁止 Python socket 网络，模型只读使用且不重复复制；未访问业务数据库。
4. 执行当前 verify.py 中真实模型/API/断言片段，仅省略 Windows DLL 枚举与其他 Windows 运行时入口：模块 Transformer/Pooling/Normalize；pooling_mode="cls"；中英文输出 (2,1024)，范数 [1.0,1.0]；与归一化原始 CLS 最大差 0.0；profile 为 ep_6134c0b15d2bb471。
5. Linux 本地加载约 5.002 秒，两文本 encode 约 0.235 秒；这些不是 Windows 数据。证据位于本报告相邻 evidence/windows-s1ae-pooling-fix/local-results.json，包含实际结果；回归脚本同时留存。
6. 两个变更 Python 文件 AST/compile 检查及 git diff --check 通过。

## 剩余风险和待验收

Windows 原生 DLL 枚举及快照时序/权限、DLL 来源、Windows 模型推理/数值/profile、峰值内存和性能仍需第三次获授权执行。本次 Linux 回归不能替代 Windows 验收。早期 DLL 清单可减少模型断言失败时证据丢失，但不消除宿主 System32 隐藏运行库、job 超时或硬退出等既有限制。

固定 PBS、生产依赖、模型 revision、业务、存储、Launcher、Linux/Docker 流程均未改变。代码变更仅 verify.py 与针对性测试。停止等待维护者审核第三次执行。
