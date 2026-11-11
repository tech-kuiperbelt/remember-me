# macOS Apple Silicon offline Beta

Requires macOS 15+ on Apple Silicon (ARM64). Intel Macs are not supported.
The package includes Python, CPU inference dependencies, Web UI and BGE-M3;
no host Python or separate model download is needed.

## Availability and verification

The macOS 1.1.3 Beta package is currently an engineering candidate, **not yet approved for
public Beta distribution**. Do not assume an existing release contains a macOS
asset. Once a maintainer explicitly approves publication, obtain both
`remember-me-macos-arm64-beta.dmg` and its `.dmg.sha256` file from the same
[v1.1.3 Beta release](https://github.com/tech-kuiperbelt/remember-me/releases/tag/v1.1.3)
(the page and downloads become available only after publication).
Do not substitute Windows or Linux downloads.

In Terminal, change to the download directory and verify:

```bash
shasum -a 256 -c remember-me-macos-arm64-beta.dmg.sha256
```

Proceed only if verification reports `OK`. A matching checksum detects changed
bytes; it is not an Apple developer identity or malware assessment.

## Install and open

1. Open the verified DMG. Drag `Remember Me.app` to Applications. If the system
   Applications folder requires administrator permission, use your own
   `~/Applications` folder instead; this installation does not require `sudo`.
2. Eject the disk image. Open the copied app from its installed location.
3. This free Beta has ad-hoc signatures, **no Developer ID signature and no Apple
   notarization**. All bundled nested Mach-O code is ad-hoc signed, but that still
   provides no Apple identity or notarization. macOS may block it or say Apple
   cannot verify the developer or check it for malicious software. This is not an
   assurance that it is safe.
4. Only if you trust the source and have verified the download, follow
   [Apple's official per-app exception procedure](https://support.apple.com/en-au/102445):
   after trying to open the app, go to System Settings → Privacy & Security →
   Security → Open Anyway, then confirm Open and authenticate if macOS requests it.
   Controls may vary or be unavailable on managed Macs. This specific candidate's
   real-user approval flow has not been verified; stop and report it if unavailable.

Do not globally disable Gatekeeper, run `sudo`, or remove quarantine attributes
in bulk. Do not bypass a warning that the app is damaged, modified or will harm
your computer; stop and obtain a verified replacement or contact the maintainer.

## Use, exit and update

The app starts a local HTTP/MCP server and opens the browser. If the browser does
not open, visit `http://127.0.0.1:1314/admin/console` (or your configured port).
Quit Remember Me / Command-Q shuts down the backend; closing the browser does not.
The Web configuration page also provides maintenance shutdown/restart actions.

User data is in `~/Library/Application Support/remember-me/`, outside the app.
Before replacing a Beta, quit it and back up that complete directory. Replace
only the app, retaining your data and configuration. Do not run competing writers.
Removing the app does not remove user data. If packaged resources are damaged,
replace the app; do not download models into its bundle.

CI has verified service and native launcher lifecycle. Finder dragging, Dock
appearance and real browser-download Gatekeeper dialogs still need real-Mac
validation; CI command-line downloads do not reproduce browser quarantine.
