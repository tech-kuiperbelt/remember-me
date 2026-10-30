# Linux Standalone Runner

使用已有的 Linux x86_64 Standalone 目录。保持目录内容完整，在其中启动前台服务：

```bash
./start.sh
# 或指定独立数据目录和端口：
./start.sh --data-dir /absolute/path/to/remember-me-data --port 1314
```

打开 `http://127.0.0.1:1314/admin/console`；如指定了其他端口，请相应修改地址。按 Ctrl+C 停止服务。程序包包含运行时和模型，运行时无需系统 Python、pip 或 Docker。

## 数据保存位置

Linux 默认将数据保存在 `~/.remember-me/`。`--data-dir` 优先于 `MEMORY_DATA_DIR`，后者优先于默认目录。例如使用 `MEMORY_DATA_DIR=/absolute/path/to/data ./start.sh`，或使用上面的 `--data-dir` 命令。相对路径以 Standalone 目录为基准，而不是终端当前目录。未指定 `--data-dir` 时，可查看启动参数，并用 `printf '%s\n' "${MEMORY_DATA_DIR:-$HOME/.remember-me}"` 查看所选位置。

内置模型位于 Standalone 目录的 `models/`；显式指定 `--model-dir` 可选择其他模型根目录。

切换安装方式不会自动迁移数据。替换程序本身不会删除持久数据；请将选用的数据目录放在计划替换或删除的程序目录之外。
