# Linux AppImage prototype

The Linux x86_64 AppImage is currently a prototype, not a formal release. Use an existing candidate file, give it execute permission and start it on a compatible graphical desktop:

```bash
chmod +x ./remember-me-linux-x86_64-s2.AppImage
./remember-me-linux-x86_64-s2.AppImage
# Or choose a separate absolute data directory and port:
./remember-me-linux-x86_64-s2.AppImage --data-dir /absolute/path/to/remember-me-data --port 1314
```

Use the tray menu to open Remember Me or quit; closing the browser does not stop the service. Normal execution requires a usable FUSE environment and the system graphical components described in the [Launcher guide](launcher.md). A tray host is needed for the tray; when GTK works without one, a small management window is provided.

## Data Location

The AppImage Launcher defaults to `$XDG_DATA_HOME/remember-me-launcher/`, or `~/.local/share/remember-me-launcher/` when `XDG_DATA_HOME` is unset or empty. Use `--data-dir /absolute/path/to/data` to choose another location; AppImage rejects relative `--data-dir` paths. `MEMORY_DATA_DIR` does not select the Launcher data root. If no `--data-dir` was passed, inspect the launching environment with `printf '%s\n' "${XDG_DATA_HOME:-$HOME/.local/share}/remember-me-launcher"`. A desktop or login session can have a different environment from your terminal.

Moving or replacing the AppImage does not move or delete the external data directory. Switching installation methods does not automatically migrate data. If the AppImage moves, run it from the new location and re-enable login autostart to update the saved path.
