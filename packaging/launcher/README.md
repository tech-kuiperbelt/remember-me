# Linux Tray Launcher

Build the existing offline Runner and the validated pystray/AppIndicator inputs
into one relocatable directory, with one Portable CPython interpreter:

```bash
bash packaging/launcher/build.sh
```

The default output is `dist/remember-me-launcher-linux-x86_64/`. Existing output
is refused. `--output DIR` and `--cache-dir DIR` select separate locations.
`--model-source SNAPSHOT` is forwarded to the existing Runner build task.
Production dependency and BGE-M3 locks are unchanged.

To reuse already-built, locally trusted inputs without downloading/recompiling:

```bash
bash packaging/launcher/build.sh \
  --runner-source "$PWD/dist/remember-me-standalone-linux-x86_64-manual" \
  --gui-source "$PWD/dist/remember-me-gui-ubuntu2204-r1"
```

The task validates source manifests, controlled input/resource hashes, installed
package RECORDs, pinned model assets and GUI versions. It copies only declared
runtime/model/license resources, then ships the current app/Web/Launcher source.
It does not copy source-root runtime data or a host `.env`. The original source
manifests are retained as historical provenance; the new manifest describes the
actual current resources and Launcher inputs. No source output is modified.
The GUI source must include its hash-locked PyGObject sdist under `sources/`;
only its verified COPYING and a short GUI license/source index are shipped in
`licenses/launcher/`, not the source archive. The index also points to the other
five GUI packages' retained wheel license materials.

Copy the complete generated directory elsewhere, preserving symlinks, and run:

```bash
./launch.sh
# Or explicitly select isolated data and an unused port:
./launch.sh --data-dir /absolute/path/to/separate-data --port 1315
```

It starts only its own service, displays status and opens existing Web pages on
menu selection. Browser closure does not stop the service. Quit waits for the
owned service's graceful shutdown; it never takes over Source/Docker instances.
The original `./start.sh` remains available as an independent foreground Runner.

## Data Location

`launch.sh` uses `$XDG_DATA_HOME/remember-me-launcher`, or
`~/.local/share/remember-me-launcher` when `XDG_DATA_HOME` is unset or empty.
Use `--data-dir` to select another directory; relative paths resolve against the
Launcher directory. `MEMORY_DATA_DIR` does not override the Launcher choice.
The independent `start.sh` defaults to `~/.remember-me/` on Linux;
`--data-dir` takes precedence over `MEMORY_DATA_DIR` for that entry point.
Switching installation methods does not migrate data; replacing the program does
not delete persistent data. See the project's [English README](https://github.com/tech-kuiperbelt/remember-me/blob/master/README.md)
or [中文 README](https://github.com/tech-kuiperbelt/remember-me/blob/master/README.zh-CN.md).

Run automatically at login is off by default. The checkbox manages one owned
user XDG Autostart entry. After moving the directory, run it at its new location
and switch autostart off/on to update that external pointer. It does not track
arbitrary moves or install a system service.

Runtime requires a graphical desktop, GTK3/GLib, GI1 runtime and relevant
typelibs, Cairo/libffi, Ayatana AppIndicator/dbusmenu, and a session D-Bus.
GNOME tray visibility requires an enabled compatible AppIndicator host. If GTK
works but no tray host exists, the Launcher provides a small Open/Quit management
window. Missing core GUI libraries cause a clear startup error before a service
is left running. Customers need no system Python, pip, compiler or development
headers.

The default GUI task (`packaging/launcher/build_gui.sh`) builds the fixed
PyGObject 3.48.2 and pycairo 1.26.1 wheels in an isolated Ubuntu 22.04 x86_64
environment using Portable CPython 3.12.15. Build tools remain in that build
environment; they are not customer prerequisites. GUI manifests retain the
baseline, input hashes and resulting wheel hashes. Docker is required only on
the GUI build machine; running the final copied directory does not use Docker.
The earlier S2-A Ubuntu 24.04 wheels reference newer GLib/Cairo APIs and are
rejected by assembly.
An older build baseline alone does not establish real GNOME/Wayland compatibility;
complete desktop compatibility acceptance belongs to S3.
AppImage, Windows/macOS and release publication are outside this build task.
