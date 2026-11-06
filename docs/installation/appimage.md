# Linux AppImage Beta

Remember Me 1.1.2 is a public preview for Linux x86_64. The download links below become available **after the v1.1.2 release is published**. The single file includes the Launcher, Python runtime, CPU dependencies and complete BGE-M3 model; Python, pip, Docker and a model download are not required.

## Download and start

After release, download the AppImage and its SHA256 file from the [v1.1.2 release](https://github.com/tech-kuiperbelt/remember-me/releases/tag/v1.1.2), then check the downloaded files in the same directory:

```bash
curl -fLO https://github.com/tech-kuiperbelt/remember-me/releases/download/v1.1.2/remember-me-1.1.2-linux-x86_64.AppImage
curl -fLO https://github.com/tech-kuiperbelt/remember-me/releases/download/v1.1.2/remember-me-1.1.2-linux-x86_64.AppImage.sha256
sha256sum -c remember-me-1.1.2-linux-x86_64.AppImage.sha256
chmod +x remember-me-1.1.2-linux-x86_64.AppImage
./remember-me-1.1.2-linux-x86_64.AppImage
# Or choose a separate absolute data directory and port:
./remember-me-1.1.2-linux-x86_64.AppImage --data-dir /absolute/path/to/remember-me-data --port 1314
```

Proceed only if the checksum reports `OK`. Use the tray menu to open Remember Me or quit; closing the browser does not stop the service. Login autostart is optional and can be enabled or disabled in that menu. It records the external AppImage path; after moving or replacing the file at a different path, start the new file and re-enable autostart. Existing Launcher and AppImage use the same autostart entry, so check the current setting before changing it.

## System requirements

Normal execution requires a usable FUSE device, permissions and helper on the host. The desktop also needs GTK3/GLib, GI typelibs, Cairo, Ayatana AppIndicator/dbusmenu and a session D-Bus, as described in the [Launcher guide](launcher.md). A compatible tray host is needed for the tray; GNOME may require an extension. If GTK works without a host, a small Open/Quit window is provided. The three target distributions have not all been tested on actual GNOME/Wayland desktops. Extract-and-run may leave temporary files and is not a supported FUSE-free alternative in this release.

## Data Location and upgrades

The AppImage Launcher defaults to `$XDG_DATA_HOME/remember-me-launcher/`, or `~/.local/share/remember-me-launcher/` when `XDG_DATA_HOME` is unset or empty. Use `--data-dir /absolute/path/to/data` to choose another location; AppImage rejects relative `--data-dir` paths. `MEMORY_DATA_DIR` does not select the Launcher data root. If no `--data-dir` was passed, inspect the launching environment with `printf '%s\n' "${XDG_DATA_HOME:-$HOME/.local/share}/remember-me-launcher"`. A desktop or login session can have a different environment from your terminal.

Before upgrading, quit the Launcher, confirm it has stopped, and back up the **entire data directory** in a separate safe location. Read the target release notes, download and verify the new file, then run it with the same intended data directory. Keep the old AppImage and backup for rollback; restore the matching backup if the old version cannot use the updated data. Moving or replacing the AppImage does not move or delete external data, and switching installation methods does not automatically migrate it.
