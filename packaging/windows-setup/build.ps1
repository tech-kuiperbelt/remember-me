param(
    [Parameter(Mandatory=$true)][string]$Bundle,
    [Parameter(Mandatory=$true)][string]$Output
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $IsWindows) { throw 'Setup builds require Windows x64 PowerShell 7' }
$bundlePath = (Resolve-Path -LiteralPath $Bundle).Path
$outputPath = [IO.Path]::GetFullPath($Output)
if (Test-Path -LiteralPath $outputPath) { throw 'Output must be a new directory' }
if ($outputPath.StartsWith($bundlePath.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Output must be outside Bundle' }
New-Item -ItemType Directory -Path $outputPath | Out-Null
$inputsDir = Join-Path $outputPath 'inputs'
New-Item -ItemType Directory -Path $inputsDir | Out-Null
$inputs = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'inputs.json') -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($name in @('build.ps1', 'install-vcredist.ps1', 'check-running.ps1')) {
    $tokens = $null
    $errors = $null
    $null = [Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot $name), [ref]$tokens, [ref]$errors)
    if ($errors.Count -gt 0) { throw ($errors | Out-String) }
}
$python = Join-Path $bundlePath 'runtime\python\python.exe'
$appVersion = & $python -I (Join-Path $PSScriptRoot 'validate_bundle.py') $bundlePath
if ($LASTEXITCODE -ne 0) { throw 'Setup bundle validation failed' }
$appVersion = ([string]$appVersion).Trim()
$evidence = [ordered]@{ app_version = $appVersion; inputs = @(); bundle = $bundlePath }
function Receive-VerifiedInput($entry, [string]$fileName, [switch]$CheckPeVersion) {
    $path = Join-Path $inputsDir $fileName
    Invoke-WebRequest -Uri $entry.url -OutFile $path
    $sha = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($sha -cne $entry.sha256) { throw "Pinned input hash mismatch: $fileName" }
    $signature = Get-AuthenticodeSignature -LiteralPath $path
    if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate -or
        $signature.SignerCertificate.GetNameInfo([Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false) -cne $entry.publisher) {
        throw "Invalid Authenticode identity: $fileName"
    }
    $fileInfo = [Diagnostics.FileVersionInfo]::GetVersionInfo($path)
    $version = '{0}.{1}.{2}.{3}' -f $fileInfo.FileMajorPart, $fileInfo.FileMinorPart, $fileInfo.FileBuildPart, $fileInfo.FilePrivatePart
    $required = [version]$entry.version
    $required = [version]::new($required.Major, $required.Minor, [Math]::Max(0, $required.Build), [Math]::Max(0, $required.Revision))
    # The Inno bootstrap installer has no useful numeric PE version. Its bytes
    # are pinned to the official release; remember-me.iss checks compiler Ver.
    $evidence.inputs += [ordered]@{ name = $fileName; url = $entry.url; sha256 = $sha; package_version = $entry.version; pe_version = $version; version_check = $(if ($CheckPeVersion) { 'numeric_pe' } else { 'pinned_release_and_compiler_guard' }); signature = [string]$signature.Status; signer = $signature.SignerCertificate.Subject }
    if ($CheckPeVersion -and [version]$version -ne $required) { throw "Input version mismatch: $fileName ($version)" }
    return $path
}
try {
    $inno = Receive-VerifiedInput $inputs.inno 'innosetup.exe'
    $vc = Receive-VerifiedInput $inputs.vcredist 'vc_redist.x64.exe' -CheckPeVersion
    $toolDir = Join-Path $outputPath 'inno'
    $install = Start-Process -FilePath $inno -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER', ('/DIR="' + $toolDir + '"')) -Wait -PassThru
    if ($install.ExitCode -ne 0) { throw "Inno compiler installation failed: $($install.ExitCode)" }
    $compiler = Join-Path $toolDir 'ISCC.exe'
    & $compiler ("/DBundleDir=$bundlePath") ("/DInputsDir=$inputsDir") ("/DAppVersion=$appVersion") ("/DVcVersion=$($inputs.vcredist.version)") ("/DVcSha256=$($inputs.vcredist.sha256)") ("/DDestination=$outputPath") (Join-Path $PSScriptRoot 'remember-me.iss')
    if ($LASTEXITCODE -ne 0) { throw 'Inno compilation failed' }
    $setup = Join-Path $outputPath 'remember-me-windows-x64-beta-setup.exe'
    $sha = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash.ToLowerInvariant()
    "$sha  $([IO.Path]::GetFileName($setup))" | Set-Content -LiteralPath "$setup.sha256" -Encoding UTF8
    $evidence.setup = [ordered]@{ name = [IO.Path]::GetFileName($setup); size = (Get-Item -LiteralPath $setup).Length; sha256 = $sha; status = 'Beta candidate; Windows 11 installation acceptance pending' }
} finally {
    $evidence | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $outputPath 'setup-build-evidence.json') -Encoding UTF8
}
