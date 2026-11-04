param(
    [Parameter(Mandatory=$true)][string]$InstallerPath,
    [Parameter(Mandatory=$true)][string]$ExpectedSha256,
    [Parameter(Mandatory=$true)][string]$MinimumVersion,
    [Parameter(Mandatory=$true)][string]$EvidenceDirectory
)
# This helper stays unelevated; only Microsoft's verified executable requests UAC.
$ErrorActionPreference = 'Stop'
$code = 1
$result = [ordered]@{ status = 'failed'; started_utc = [DateTime]::UtcNow.ToString('o') }
New-Item -ItemType Directory -Force -Path $EvidenceDirectory | Out-Null
$evidence = Join-Path $EvidenceDirectory ('vcredist-' + [Guid]::NewGuid().ToString('N') + '.json')
function Get-RuntimeVersion {
    $baseKey = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, [Microsoft.Win32.RegistryView]::Registry64)
    $key = $baseKey.OpenSubKey('SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64')
    try {
        if ($null -eq $key -or $key.GetValue('Installed', 0) -ne 1) { return $null }
        return [version]([string]$key.GetValue('Version', '')).TrimStart('v')
    } finally {
        if ($null -ne $key) { $key.Dispose() }
        $baseKey.Dispose()
    }
}
try {
    $before = Get-RuntimeVersion
    $result.before = [string]$before
    if ($null -ne $before -and $before -ge [version]$MinimumVersion) {
        $result.status = 'already_satisfied'
        $code = 0
    } else {
        $hash = (Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $signature = Get-AuthenticodeSignature -LiteralPath $InstallerPath
        $result.sha256 = $hash
        $result.signature_status = [string]$signature.Status
        $result.signer = [string]$signature.SignerCertificate.Subject
        if ($hash -cne $ExpectedSha256.ToLowerInvariant()) { throw 'VC++ SHA256 mismatch' }
        if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate -or
            $signature.SignerCertificate.GetNameInfo([System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false) -cne 'Microsoft Corporation') {
            throw 'VC++ requires a valid Microsoft Corporation Authenticode signature'
        }
        $fileInfo = [Diagnostics.FileVersionInfo]::GetVersionInfo($InstallerPath)
        $fileVersion = '{0}.{1}.{2}.{3}' -f $fileInfo.FileMajorPart, $fileInfo.FileMinorPart, $fileInfo.FileBuildPart, $fileInfo.FilePrivatePart
        $result.file_version = $fileVersion
        if ([version]$fileVersion -ne [version]$MinimumVersion) { throw 'VC++ input file version mismatch' }
        # Microsoft's own installer logs go to the elevated account's TEMP.
        # This unelevated summary stays available to the installing user.
        $result.microsoft_log_location = 'Elevated administrator account TEMP (dd_vcredist*.log)'
        # The privileged installer never executes a script supplied by this helper.
        $result.status = 'installing'
        $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $evidence -Encoding UTF8
        $process = Start-Process -FilePath $InstallerPath -ArgumentList '/install /passive /norestart' -Verb RunAs -Wait -PassThru
        $process.Refresh()
        $result.installer_exit_code = $process.ExitCode
        if ($process.ExitCode -notin @(0, 3010, 1641)) {
            if ($process.ExitCode -eq 1602) { $code = 1602; throw 'VC++ installation cancelled' }
            throw ('VC++ installer failed with exit code ' + $process.ExitCode)
        }
        $after = Get-RuntimeVersion
        $result.after = [string]$after
        if ($null -eq $after -or $after -lt [version]$MinimumVersion) { throw 'VC++ post-install registry verification failed' }
        $code = $process.ExitCode
        $result.status = 'installed'
        $result.reboot_required = $code -in @(3010, 1641)
    }
} catch {
    $result.status = 'failed'
    if ($code -eq 1602) { $result.status = 'cancelled' }
    $result.error = $_.Exception.ToString()
    $exception = $_.Exception
    while ($null -ne $exception) {
        if ($exception -is [ComponentModel.Win32Exception] -and $exception.NativeErrorCode -eq 1223) {
            $code = 1602
            $result.status = 'cancelled'
        }
        $exception = $exception.InnerException
    }
} finally {
    $result.helper_exit_code = $code
    $result.completed_utc = [DateTime]::UtcNow.ToString('o')
    $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $evidence -Encoding UTF8
}
exit $code
