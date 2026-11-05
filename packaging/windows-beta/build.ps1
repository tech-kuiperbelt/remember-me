param([Parameter(Mandatory=$true)][string]$Work, [Parameter(Mandatory=$true)][ValidateSet('sqlite','dual')][string]$Storage)
$ErrorActionPreference = 'Stop'
# Reuse fixed PBS, license, hash and dependency installation; preserve strict POC default.
& "$PSScriptRoot/../windows-poc/build.ps1" -Work $Work -PrepareOnly -ResolvedReport "$PSScriptRoot/resolved-dependencies.json"
$python = Join-Path $Work 'bundle/runtime/python/python.exe'
& $python -I "$PSScriptRoot/finalize.py" --work $Work --storage $Storage
if ($LASTEXITCODE -ne 0) { throw 'Beta layout preparation failed' }
& $python -I "$PSScriptRoot/smoke.py" --work $Work
if ($LASTEXITCODE -ne 0) { throw 'Beta business smoke failed; evidence retained, no Setup input sealed' }
& $python -I "$PSScriptRoot/diagnostics.py" --work $Work
if ($LASTEXITCODE -ne 0) { throw 'Native icon/controller/performance checks failed; evidence retained' }
& $python -I "$PSScriptRoot/finalize.py" --work $Work --storage $Storage --seal
if ($LASTEXITCODE -ne 0) { throw 'Beta Setup input sealing failed' }
