# Linux Tray Launcher

Use an existing Linux x86_64 Launcher directory. Keep the directory contents together and start the tray application from it:

```bash
./launch.sh
# Or choose a separate data directory and port:
./launch.sh --data-dir /absolute/path/to/remember-me-data --port 1314
```

Use the tray menu to open Remember Me or quit. Closing the browser does not stop the service. The package retains `./start.sh` for an independent foreground Runner; see the [Standalone guide](standalone.md) for its different data default.

The desktop needs GTK3/GLib, GI typelibs, Cairo, Ayatana AppIndicator/dbusmenu and a session D-Bus. A compatible tray host is needed for the tray; if GTK is available without a host, the Launcher provides a small Open/Quit window.

## Data Location

The Launcher defaults to `$XDG_DATA_HOME/remember-me-launcher/`, or `~/.local/share/remember-me-launcher/` when `XDG_DATA_HOME` is unset or empty. `--data-dir` overrides that default; relative paths are resolved against the Launcher directory. The Launcher does not use `MEMORY_DATA_DIR` to choose its data root. If no `--data-dir` was passed, inspect the launching environment with `printf '%s\n' "${XDG_DATA_HOME:-$HOME/.local/share}/remember-me-launcher"`. A desktop or login session can have a different environment from your terminal.

Switching installation methods does not automatically migrate data. Replacing the program does not itself delete persistent data; keep your chosen data directory outside any program directory you plan to replace or delete.
