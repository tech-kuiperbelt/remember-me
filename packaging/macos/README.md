# macOS ARM64 offline Beta — S1 Step 1

Build on macOS 15+ Apple Silicon (Xcode command line tools and a host Python 3
are needed only for building):

```bash
python3 packaging/macos/build.py --work /tmp/remember-me-macos-build
python3 packaging/macos/verify.py \
  --dmg /tmp/remember-me-macos-build/output/remember-me-macos-arm64-beta.dmg \
  --work /tmp/remember-me-macos-verify
```

Both work directories must be new. The workflow `macos-beta.yml` runs these
commands on `macos-15`, asserts ARM64, and uploads a validated DMG plus SHA256
and build/acceptance evidence. It runs only on `poc/macos-s1-step1`; it cannot
create a Release/Tag, merge master, or publish a production artifact. Artifacts
expire after 14 days. A failed validation uploads evidence without promoting
the DMG to the validated artifact.

Copy `Remember Me.app` from the DMG to Applications (or another local directory),
eject the DMG, then open the app. The native launcher opens the browser when HTTP
becomes available. Quit Remember Me / Cmd-Q requests graceful SIGTERM shutdown
and waits for Runtime cleanup. Configuration / Maintenance supports the existing
Web Restart and Shut Down Server operations. A second instance for the same data
directory fails explicitly. Startup errors are recorded in the data directory's
`logs/macos-launcher.log`. No login startup or tray is included in this step.

The app includes relocatable PBS CPython 3.12.15 (20261020), production dependency
versions resolved to macOS ARM64 wheels, Web UI, and the existing SHA256-verified
BGE-M3 snapshot. The build records the exact candidate wheel lock and installs
it from a hash-checked offline wheelhouse. The torch wheel has macOS MPS support,
but the launcher explicitly selects CPU. The existing embedding identity and
database formats are retained. Version comes only from the copied release.jsonc;
this engineering candidate does not establish a new release version.

Resources live in `Contents/Resources/` with the existing app/web/model layout.
Mach-O dependencies must use system library paths or relative install names;
the build rejects runner/Homebrew dependencies. ARM64 ad-hoc code signatures are
generated locally for executable integrity. There is no Developer ID identity,
Apple notarization, distribution entitlement or public release in this phase.

User data remains at `~/Library/Application Support/remember-me/`, independently
of the app path. Temporary/cache/log files are rooted there. New configurations
disable automatic model downloads; existing configurations are retained. The
offline bundle enforces cache-only loading even when an existing configuration
enables downloads or the UI requests a retry. This is a launcher-only runtime
policy and does not rewrite the saved configuration. The
bundled model is read locally, with optional safetensors conversion disabled.
If packaged model files are damaged, replace the app. Do not repair or download
models into its resource directory. Existing databases must already match the
active BGE-M3 profile; no automatic data migration is performed. Back up the
complete data directory before trying a Beta against existing data.

For terminal operation and automation, use the same bundled entry point:

```bash
"/Applications/Remember Me.app/Contents/MacOS/remember-me" --no-browser
```

`--data-dir /absolute/path` and `--port PORT` allow isolated acceptance data.
The data directory cannot be inside the app. Headless mode replaces the launcher
with its bundled Python process, retaining signal/exit ownership.

Acceptance uses the final mounted DMG, copies and relocates its app, denies
external networking with a macOS sandbox while allowing loopback, and exercises
the shipped interpreter, CPU bilingual embeddings, Web UI, MCP, CRUD, persistent
restart, writer exclusion and lock release. The evidence explicitly separates
automated checks from unverified Finder/Gatekeeper prompts, browser presentation,
Dock/menu interaction and real-machine GUI behavior.
