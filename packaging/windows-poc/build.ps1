param([Parameter(Mandatory=$true)][string]$Work, [switch]$PrepareOnly, [string]$ResolvedReport)
$ErrorActionPreference = 'Stop'
if (-not $IsWindows -or [Environment]::Is64BitProcess -ne $true) { throw 'Requires Windows x64 PowerShell 7' }
$Work = [IO.Path]::GetFullPath($Work)
if (Test-Path $Work) { throw 'Work directory must not exist' }
New-Item -ItemType Directory -Path "$Work/evidence" -Force | Out-Null
Start-Transcript -Path "$Work/evidence/build.log"
try {
    $drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($Work))
    $available = $drive.AvailableFreeSpace
    $required = 12GB
    $reason = if ($available -lt $required) { 'insufficient_disk_space' } else { 'sufficient_disk_space' }
    [ordered]@{ drive = $drive.Name; available_bytes = $available; required_bytes = $required; reason = $reason } |
        ConvertTo-Json | Set-Content -Encoding utf8 "$Work/evidence/disk-preflight.json"
    Write-Host "Disk preflight: available=$available bytes; required=$required bytes; reason=$reason"
    if ($available -lt $required) { throw "Insufficient disk space: available=$available bytes; required=$required bytes" }
    $archive = "$Work/python.tar.gz"
    $url = 'https://github.com/astral-sh/python-build-standalone/releases/download/20261020/cpython-3.12.15+20261020-x86_64-pc-windows-msvc-install_only.tar.gz'
    Invoke-WebRequest $url -OutFile $archive
    if ((Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne '5e7d27ae6181fcf1f992c5a221b8fa7a320e7fd576c476434886292ccf2e20fb') { throw 'PBS SHA256 mismatch' }
    New-Item -ItemType Directory "$Work/runtime" | Out-Null
    tar -xzf $archive -C "$Work/runtime"
    if ($LASTEXITCODE -ne 0) { throw 'Extraction failed' }
    Get-ChildItem Env: | Where-Object { $_.Name -match '^(PYTHON|PIP_|HF_|HUGGINGFACE_|TRANSFORMERS_|SENTENCE_TRANSFORMERS_|TORCH_|EMBED_|MEMORY_)' } | ForEach-Object { Remove-Item "Env:$($_.Name)" }
    $env:PIP_CONFIG_FILE = 'NUL'
    $env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
    $env:PYTHONNOUSERSITE = '1'
    $env:TEMP = "$Work/tmp"; $env:TMP = $env:TEMP
    New-Item -ItemType Directory $env:TEMP | Out-Null
    $prepareArgs = @('-I', "$PSScriptRoot/prepare.py", '--work', $Work)
    if ($ResolvedReport) { $prepareArgs += @('--resolved-report', $ResolvedReport) }
    & "$Work/runtime/python/python.exe" @prepareArgs
    if ($LASTEXITCODE -ne 0) { throw 'Windows POC failed; inspect retained evidence' }
    Move-Item "$Work/runtime" "$Work/bundle/runtime"
    if ($PrepareOnly) {
        Write-Host 'Prepared only: strict S1-A verification was not run or claimed passed.'
        return
    }
    & "$Work/bundle/runtime/python/python.exe" -I "$PSScriptRoot/verify.py" "$Work/bundle" "$Work/evidence"
    if ($LASTEXITCODE -ne 0) { throw 'Native verification failed' }
} finally { Stop-Transcript }
