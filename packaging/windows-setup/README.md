# Windows Beta Setup

Windows public delivery is Setup only. The prepared bundle remains an internal build input, not a second Portable ZIP product. Setup installs the existing private Python/App/Web/full BGE-M3 payload and starts `tray_launcher.py` through private `pythonw.exe`; no system Python or application freezing is used.

## Build inputs and commands (require separate remote authorization)

Use Windows x64 PowerShell 7 and the reviewed source commit. Build the existing full bundle with native business smoke, then compile Setup:

```powershell
./packaging/windows-beta/build.ps1 -Work C:\rm-beta-build-002 -Storage dual
./packaging/windows-setup/build.ps1 -Bundle C:\rm-beta-build-002\bundle -Output C:\rm-beta-setup-002
```

Both output directories must be new. The first command downloads the already-pinned runtime, wheels and model, runs the existing smoke and seals the bundle. The second accepts only a complete payload with passed `build-evidence/business-smoke.json`; reads application version from its `release.jsonc`; downloads and verifies the pinned compiler and VC++ input; emits `remember-me-windows-x64-beta-setup.exe`, `.exe.sha256` and `setup-build-evidence.json`. Build host needs network; end-user installation is fully offline. Do not place output inside the bundle. No Actions, Tag, Release or production publication is performed by these scripts.

`inputs.json` pins Inno Setup **6.7.3** and Microsoft x64 VC++ **14.51.36247.0**. Assets are downloaded to the build output, never committed. Inno's independent hash was read from its official immutable GitHub release asset digest. VC++ was downloaded through Microsoft's documented alias; its final Microsoft HTTPS URL includes the exact content hash. Hashes lock bytes but do not substitute for identity: Windows build requires valid Authenticode and exact publisher (`Pyrsys B.V.` / `Microsoft Corporation`) before running either input. VC++ additionally requires its exact numeric PE version. Inno's outer installer reports numeric PE version 0.0.0.0, so the signed, hash-pinned release identifies that package; the actual compiler must satisfy the exact `Ver` guard in `remember-me.iss`. The prerequisite is checked again immediately before elevation during installation. Linux download/hash or certificate inspection is not Windows Authenticode acceptance.

Source and license references:

- [Inno immutable release](https://github.com/jrsoftware/issrc/releases/tag/is-6_7_3), [verification](https://jrsoftware.org/isdl-verify.php), [license](https://github.com/jrsoftware/issrc/blob/is-6_7_3/license.txt). Preserve compiler/runtime copyright notices; confirm applicable commercial licensing before commercial distribution.
- [Microsoft official runtime source](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist/) and [deployment, redistributable license and registry guidance](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files). Confirm redistribution entitlement before external delivery.

## Installation and lifecycle

Default program: `%LOCALAPPDATA%\Programs\Remember Me Beta`. Data stays `%LOCALAPPDATA%\remember-me-beta`, or existing absolute `REMEMBER_ME_BETA_DATA_DIR`. Setup rejects selecting the default/current overridden data directory, a parent containing it, or a child inside it as the application directory. No migration or backup semantics are added: before an upgrade, exit normally and back up the complete actual data directory using the existing procedure.

The main installer stays unelevated (`PrivilegesRequired=lowest`). Only the hash- and Microsoft-signature-verified official runtime installer requests UAC. The helper waits for its actual exit code, distinguishes cancellation (including UAC 1223), failure and restart-required (3010/1641), and checks the installed x64 runtime registry again. A sufficient newer runtime is skipped, never downgraded. No restart is forced. A successful prerequisite may remain installed even if application installation later fails.

Prerequisite summary evidence stays under `%LOCALAPPDATA%\remember-me-beta\logs\setup`; the Microsoft installer additionally writes its own default logs under the elevated administrator account's TEMP. Setup's own log is enabled in the installing user's TEMP. Alternate administrator credentials may require that administrator to collect Microsoft's detailed logs. Diagnostic evidence is not a business data migration.

Start-menu entry launches the tray in the background. The tray includes status, Open/Memory/Stats/Settings/Help navigation, opt-in current-user login startup, and safe Exit, with Chinese/English labels. Use Tray Exit or Web maintenance → Shut Down Server before install/upgrade/uninstall. Installation and removal hold the same per-user SID installation guard checked by launcher/service startup. Both active launcher and active service guards block changes; a conservative same-user legacy process check also detects old unguarded ZIP services, including after Web restart. File detection failure blocks changes. No process is killed and no storage lock is deleted.

Stable Inno AppId supports in-place upgrades. CloseApplications/RestartApplications are disabled: Setup must not silently force-close the service. Uninstall removes installer-owned program files/shortcuts/registration only. It has no data-deletion option or recursive cleanup rule and never uninstalls shared VC++.

## Step 1 evidence and remaining native acceptance

Local targeted Python tests validate real JSONC version derivation, complete inputs, smoke gate, rejection of user state/symlinks and installation safety contracts. A tiny unrelated fixture can be compiled by pinned ISCC under an isolated Wine prefix to check actual Inno section/Pascal syntax; that is not a complete Windows Setup build or installation test.

Windows-only acceptance remains: real Microsoft/Pyrsys Authenticode chain validation; PowerShell/CIM execution; same-user cross-session guard behavior; UAC approve/cancel and alternate credentials; genuine runtime install/post-check/reboot branches; tray GUI; install/uninstall/upgrade on a clean Windows 11 VM; full-size single-file Setup and actual disk/temp space. Existing Linux tests and the tiny compiler fixture do not claim these have passed. Strict S1-A Defender identity remains unresolved and is not misrepresented as accepted.

## 1.1.1 release preparation

Maintainer approved 1.1.1 Beta; v1.1.0 already exists and must not be reused or moved. The current source includes the existing neutral brand ICO in Setup/Start-menu/tray, reviewed startup tracing, and opt-in add-phase tracing enabled by the Beta entrypoint. No model/dependency/storage changes.

The bundle build now also runs native `diagnostics.py`: actual LoadImage, headless Launcher Controller startup/stop, and short/2000-character real additions. Results and startup/performance traces remain in work/evidence, outside the installed data. This is Windows runner evidence, not VM visual or installer GUI acceptance. A fresh reviewed-source bundle is required; do not relabel the previously tested 1.1.0 installer as 1.1.1.

Prior Run 41959636700 and user VM testing established the baseline; new icon visual acceptance, old-to-new overwrite upgrade, and running install/uninstall refusal still require targeted VM confirmation. Windows timings for this new candidate require a separately approved Push/build. Publication remains manual and separately authorized.

## Full Windows menu candidate

Login startup is off by default. The standard-library helper uses HKCU Run and an exact ownership marker; no administrator rights or Windows Service. Its private Python command pins the selected data path. Uninstall removes the entry only when it matches the recorded owned command; unknown values and all memory data remain untouched. Existing entries are retained across upgrades at the same paths. Windows Startup Apps/policy may override execution; the application does not alter StartupApproved policy.

[Microsoft Run key documentation](https://learn.microsoft.com/en-us/windows/win32/setupapi/run-and-runonce-registry-keys) limits command lines to 260 characters. Longer commands are rejected before writes, keeping manual launch available. Native diagnostics only toggles a fresh temporary registry subtree, never the real Run key; VM login and uninstall acceptance is still required.
