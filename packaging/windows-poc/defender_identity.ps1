param([Parameter(Mandatory=$true)][string]$Dll, [Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference = 'Stop'
$path = (Get-Item -LiteralPath $Dll).FullName
$before = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
$signature = Get-AuthenticodeSignature -LiteralPath $path
$info = [Diagnostics.FileVersionInfo]::GetVersionInfo($path)
$providers = @()
$base = 'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\AMSI\Providers'
foreach ($provider in Get-ChildItem -LiteralPath $base) {
    $key = "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Classes\CLSID\$($provider.PSChildName)\InprocServer32"
    if (Test-Path -LiteralPath $key) {
        $registered = (Get-Item -LiteralPath $key).GetValue('')
        if ($registered) {
            $expanded = [Environment]::ExpandEnvironmentVariables(([string]$registered).Trim('"'))
            $providers += @{ clsid = $provider.PSChildName; registry_path = $key; raw_path = $registered; path = [IO.Path]::GetFullPath($expanded) }
        }
    }
}
$signer = $signature.SignerCertificate
$result = [ordered]@{
    path = $path
    sha256 = $before
    sha256_after = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    signature_status = [string]$signature.Status
    signature_message = $signature.StatusMessage
    signer_name = $(if ($signer) { $signer.GetNameInfo([Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false) } else { '' })
    signer_subject = $(if ($signer) { $signer.Subject } else { '' })
    signer_thumbprint = $(if ($signer) { $signer.Thumbprint } else { '' })
    file_version = $info.FileVersion
    original_filename = $info.OriginalFilename
    company_name = $info.CompanyName
    amsi_providers = @($providers)
}
[IO.File]::WriteAllText($Output, ($result | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
