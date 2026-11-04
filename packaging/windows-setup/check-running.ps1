param([Parameter(Mandatory=$true)][string]$InstallDirectory)
# Fail closed on diagnostic failure. Never stop a process or touch a data/lock file.
$ErrorActionPreference = 'Stop'
try {
    $root = [IO.Path]::GetFullPath($InstallDirectory).TrimEnd('\') + '\'
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    foreach ($process in @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'")) {
        if ($process.ExecutablePath -and $process.ExecutablePath.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
            Write-Output ('Program runtime is in use: PID ' + $process.ProcessId)
            exit 10
        }
        # Older ZIP copies have no service mutex; do not upgrade alongside one
        # owned by this user, even if that copy lives in a different directory.
        $legacy = $process.CommandLine -match '(?i)(?:^|[\\/])beta_start\.py(?:["\s]|$)'
        if ($process.ExecutablePath -and $process.CommandLine -match '(?i)(?:^|\s)-m\s+app\.server(?:\s|$)') {
            $bundle = [IO.Path]::GetFullPath((Join-Path ([IO.Path]::GetDirectoryName($process.ExecutablePath)) '..\..'))
            $legacy = $legacy -or ((Test-Path -LiteralPath (Join-Path $bundle 'beta-policy.json')) -and
                (Test-Path -LiteralPath (Join-Path $bundle 'beta_start.py')))
        }
        if ($legacy) {
            $owner = Invoke-CimMethod -InputObject $process -MethodName GetOwnerSid
            if ($owner.ReturnValue -ne 0) { throw 'Cannot determine legacy Remember Me process owner' }
            if ($owner.Sid -eq $sid) {
                Write-Output ('Remember Me Beta is still running: PID ' + $process.ProcessId)
                exit 10
            }
        }
    }
    exit 0
} catch {
    Write-Error $_
    exit 11
}
