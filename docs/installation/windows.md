# Windows 11 x64 Beta installation, upgrade and uninstall

Windows is distributed only as a complete offline Setup, not a Portable ZIP. This guide targets the **1.1.1 Beta candidate**. Links become available only after the maintainer approves and publishes the Pre-release. Existing Linux/Docker releases are unchanged.

## Install and use

1. After publication, download `remember-me-windows-x64-beta-setup.exe` and its `.exe.sha256` from the [v1.1.1 Pre-release](https://github.com/tech-kuiperbelt/remember-me/releases/tag/v1.1.1). Compare the SHA256 before installation:

   ```powershell
   Get-FileHash .\remember-me-windows-x64-beta-setup.exe -Algorithm SHA256
   Get-Content .\remember-me-windows-x64-beta-setup.exe.sha256
   ```

2. Start Setup normally for a current-user installation. No system Python, Docker or separate model download is required. When a suitable x64 VC++ Runtime is missing, Setup uses the bundled official Microsoft package and requests UAC only for that child installer. A sufficient newer runtime is skipped. Collect logs on cancellation or failure; do not disable security protection.
3. Start Remember Me Beta from the Start menu. The service initializes in the background. The tray has only **Open Remember Me** and **Exit**; expand Windows' hidden notification icons if necessary. Wait for initialization instead of repeatedly launching it.
4. Open launches the Web UI, default `http://127.0.0.1:1314/admin/console`; MCP uses `http://127.0.0.1:1314/mcp`. A saved custom port applies. The tray binds the service to loopback. Login autostart and automatic updates are not implemented.
5. Choose Tray Exit and wait for it to disappear. Closing the browser does not stop the service. Web maintenance followed by Shut Down Server is another normal exit path.

## Data and upgrades

- Program default: `%LOCALAPPDATA%\Programs\Remember Me Beta`.
- Data default: `%LOCALAPPDATA%\remember-me-beta`. Existing `REMEMBER_ME_BETA_DATA_DIR` absolute user environment overrides remain supported; use the same visible environment when launching from the Start menu. Data must be outside the program directory on a local fixed NTFS volume.
- Before upgrading, exit normally and back up the **entire actual data directory** to a separate safe location. Run the new Setup as the same user over the existing installation. A running instance must prompt for normal exit instead of force-killing writes. Do not treat copying only an active memories.db as a complete backup.
- Verify old memories, new writes and searches after upgrading. Retain the old installer and complete backup. Arbitrary downgrade compatibility is not promised; restore a matching backup when needed. There is no automatic data-directory migration.

## Uninstall

Exit normally, then use Windows Installed Apps or the Start-menu uninstall entry. A running instance must be stopped normally first. Uninstall retains memories, configuration and logs, and does not remove shared VC++ Runtime. Reinstall with the same data directory to read retained memories. No delete-data shortcut is provided; do not remove lock files to bypass protection.

## Performance and logs

Logs live under the actual data directory's `logs\`: `launcher.log`, `launcher-service.log`, `memory-server.log`, and this candidate's `startup-timing.jsonl`/`performance-timing.jsonl`. Startup tracing covers launcher/import/model/service-ready stages. Add tracing covers embedding, metadata/index-vector preparation, database and total time, without recording memory text or vectors. Database timing measures the existing storage.add call; it does not force synchronization or include later background replica writes.

For slow startup or long additions, include Windows/VM CPU, RAM, disk configuration, character count, observed elapsed time and logs. Check other service logs for private information before sharing. VM effects are a hypothesis, not a confirmed cause. Setup logs are in the user's TEMP; prerequisite summaries use the default Beta data directory's `logs\setup`. Microsoft's detailed logs may reside in the elevated administrator's TEMP.

## Known limitations

This is a Beta; the final Setup is not code-signed and coverage does not include all Windows machines. Dual storage is limited to local fixed NTFS, with file fsync and atomic replacement but no POSIX-equivalent power-loss directory durability promise. Historical strict S1-A Defender identity auditing remains incomplete. No autostart, Windows Service or expanded tray menus; diagnostics do not imply comprehensive performance optimization.
