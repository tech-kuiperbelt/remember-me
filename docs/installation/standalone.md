# Linux Standalone Runner

Use an existing Linux x86_64 Standalone directory. Keep the directory contents together and start the foreground service from it:

```bash
./start.sh
# Or choose a separate data directory and port:
./start.sh --data-dir /absolute/path/to/remember-me-data --port 1314
```

Open `http://127.0.0.1:1314/admin/console`, adjusting the port if specified. Stop the service with Ctrl+C. The package includes the runtime and model; it does not require system Python, pip or Docker to run.

## Data Location

By default, data is stored in `~/.remember-me/` on Linux. `--data-dir` overrides `MEMORY_DATA_DIR`, which overrides the default. For example, use `MEMORY_DATA_DIR=/absolute/path/to/data ./start.sh` or the `--data-dir` command above. Relative paths are resolved against the Standalone directory, not the shell's current directory. Inspect the startup arguments and `printf '%s\n' "${MEMORY_DATA_DIR:-$HOME/.remember-me}"` to determine the chosen location when no `--data-dir` was passed.

The bundled model is under the Standalone directory's `models/`; an explicit `--model-dir` selects a different model root.

Switching installation methods does not automatically migrate data. Replacing the program does not itself delete persistent data; keep your chosen data directory outside any program directory you plan to replace or delete.
