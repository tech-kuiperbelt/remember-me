# S2-A GUI technology probe

This is a bounded tray menu experiment, not the Remember Me Launcher. It never
starts the service, opens a database/browser, registers login autostart, or changes
desktop extensions. Existing Standalone outputs and production locks are separate.

Build from the source root using the isolated build task:

```bash
bash packaging/tray-prototype/build.sh --output "$PWD/dist/remember-me-tray-s2a-clean"
```

See the build task and its generated dependency/build evidence for exact inputs
and build-host requirements. The output uses the existing Portable CPython 3.12
version plus private `vendor/` packages; no system Python or developer venv is
used at runtime. GTK3/GLib, GI runtime/typelibs, Ayatana AppIndicator and a working
graphical session D-Bus/tray host remain explicit system requirements. Customer
runtime does not compile dependencies. A successful build is not desktop proof.

The verified build host is **Ubuntu 24.04 x86_64**, with existing GCC, pkgconf,
GLib/GObject/GIO, Cairo and libffi development headers, plus curl, tar, dpkg-deb
and coreutils. The task extracts one hash-checked Ubuntu 1.80.1 GI development
header package into its cache, creates explicit build-only pkg-config metadata,
and installs pinned Python build tools there. It does not install host packages.
This build recipe is currently specific to that host baseline; it is not a
universal build task for all Linux distributions. Customers running the generated
directory need the documented GUI **runtime** libraries/typelibs and desktop host,
not GCC, development headers, Meson, pip or system Python. The evidence records
the actual system package versions; other desktop baselines remain unverified.

Run the generated directory from a terminal in the desktop session:

```bash
./dist/remember-me-tray-s2a-clean/run.sh
```

The foreground probe prints a newly created temporary evidence directory and
structured JSON events. To use a selected isolated location, pass
`--evidence-dir /absolute/path/to/empty-test-directory`; `events.jsonl` must not
already exist, and evidence must be outside the prototype directory. For bounded
verification, add `--duration-seconds 120` (1–3600; default 0 waits for Exit).

Look for the green **S2-A** tray icon titled **Remember Me S2-A GUI probe**. Open
its menu and verify:

1. Click **Increment click counter** twice; the disabled status becomes
   **Clicks: 2**, and the journal contains two `menu_click` events.
2. Toggle **Test checkbox** on and off. Its displayed tick must follow; the
   journal records `checkbox_toggled` with matching `checked` values.
3. Click **Update label (revision 0)**; reopen the menu and confirm revision 1.
   The journal records `menu_updated`.
4. Select **Exit prototype**; the icon disappears and the process exits.
   The journal records `stop_requested` and `closed`.

An event named `ready` proves only client initialization, not an actually visible
icon. D-Bus/programmatic event activation alone also does not prove a physical
menu click or rendering; record those separately from desktop observation.
No host or no graphical session produces an error and exits; there is no silent
Xorg fallback or hidden background service. Ctrl+C/SIGTERM is handled after GTK
initialization; abnormal SIGKILL cannot write a clean exit event.

After exiting, copy the complete output, preserving symlinks, and repeat from a
path with spaces and a fresh evidence directory:

```bash
probe_test_root=$(mktemp -d /tmp/remember-me-tray-move-XXXXXX)
cp -a dist/remember-me-tray-s2a-clean "$probe_test_root/probe moved"
"$probe_test_root/probe moved/run.sh" --duration-seconds 120
```

Do not copy only Python packages or only the script. No build/source directory
is needed by the copied runtime. Current Ubuntu GNOME evidence does not establish
Ubuntu22.04, Debian13, Fedora44 or Wayland compatibility. S2-A finishes with a
separate prototype verification report and maintainer review; it does not
authorize S2-B, full Launcher implementation, Push, Release or AppImage work.
