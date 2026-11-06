"""Execute helper branches in PowerShell with explicit OS/identity mocks.

Set REMEMBER_ME_TEST_PWSH to a temporary official Linux pwsh binary if needed.
These tests never install VC++, elevate, or validate a Windows signature.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
PWSH = os.environ.get('REMEMBER_ME_TEST_PWSH') or shutil.which('pwsh')
pytestmark = pytest.mark.skipif(not PWSH, reason='PowerShell required for mocked prerequisite branch regression')


@pytest.mark.parametrize('case,code,status', [
    ('uninstall_success', 0, 'uninstalled'),
    ('uninstall_absent', 0, 'already_absent'),
    ('uninstall_newer', 1, 'failed'),
    ('uninstall_cancelled', 1602, 'cancelled'),
    ('uninstall_remaining', 1, 'failed'),
    ('uninstall_reboot', 3010, 'uninstalled'),
    ('newer', 0, 'already_satisfied'),
    ('success', 0, 'installed'),
    ('reboot', 3010, 'installed'),
    ('failed', 1, 'failed'),
    ('cancelled', 1602, 'cancelled'),
    ('bad_hash', 1, 'failed'),
    ('bad_signature', 1, 'failed'),
    ('postcheck_failed', 1, 'failed'),
])
def test_real_helper_control_flow_with_mocked_windows_identity(tmp_path, case, code, status):
    source = (ROOT/'packaging/windows-setup/install-vcredist.ps1').read_text(encoding='utf-8')
    assert source.count('function Get-RuntimeVersion {') == 1
    source = source.replace('function Get-RuntimeVersion {','function Get-UnusedRealRuntimeVersion {')
    old = '$fileInfo = [Diagnostics.FileVersionInfo]::GetVersionInfo($InstallerPath)'
    assert source.count(old) == 1
    source = source.replace(old, "$fileInfo = [pscustomobject]@{FileMajorPart=14;FileMinorPart=51;FileBuildPart=36247;FilePrivatePart=0}")
    payload = tmp_path/'fixture.exe'
    payload.write_bytes(b'explicitly mocked vendor payload, never executed')
    sha = hashlib.sha256(payload.read_bytes()).hexdigest()
    if case == 'bad_hash':
        sha = '0'*64
    prelude = r'''
$script:runtimeReads = 0
function Get-RuntimeVersion {
    $script:runtimeReads++
    if ($env:RM_TEST_CASE -like 'uninstall_*') {
        if ($env:RM_TEST_CASE -eq 'uninstall_absent') { return $null }
        if ($env:RM_TEST_CASE -eq 'uninstall_newer') { return [version]'99.0.0.0' }
        if ($script:runtimeReads -eq 1 -or $env:RM_TEST_CASE -eq 'uninstall_remaining') { return [version]'14.51.36247.0' }
        return $null
    }
    if ($env:RM_TEST_CASE -eq 'newer') { return [version]'99.0.0.0' }
    if ($script:runtimeReads -ge 2 -and $env:RM_TEST_CASE -ne 'postcheck_failed') {
        return [version]'14.51.36247.0'
    }
    return $null
}
function Get-AuthenticodeSignature {
    param($LiteralPath)
    $cert = [pscustomobject]@{ Subject = 'MOCK Microsoft Corporation' }
    $cert | Add-Member -MemberType ScriptMethod -Name GetNameInfo -Value { return 'Microsoft Corporation' }
    $status = if ($env:RM_TEST_CASE -eq 'bad_signature') { 'HashMismatch' } else { 'Valid' }
    return [pscustomobject]@{ Status = $status; SignerCertificate = $cert }
}
function Start-Process {
    param($FilePath,$ArgumentList,$Verb,[switch]$Wait,[switch]$PassThru)
    if ($env:RM_TEST_CASE -like 'uninstall_*' -and $ArgumentList -ne '/uninstall /passive /norestart') { throw 'Wrong removal arguments' }
    if ($env:RM_TEST_CASE -in @('cancelled','uninstall_cancelled')) { throw [ComponentModel.Win32Exception]::new(1223) }
    $code = switch ($env:RM_TEST_CASE) { 'uninstall_reboot' {3010}; 'reboot' {3010}; 'failed' {1603}; default {0} }
    $process = [pscustomobject]@{ ExitCode = $code }
    $process | Add-Member -MemberType ScriptMethod -Name Refresh -Value {}
    return $process
}
& {
'''
    script = tmp_path/'flow.ps1'
    script.write_text(prelude + source + '\n} @args\n', encoding='utf-8')
    evidence = tmp_path/'evidence'
    env = dict(os.environ, RM_TEST_CASE=case, XDG_CACHE_HOME=str(tmp_path/'cache'),
               XDG_CONFIG_HOME=str(tmp_path/'config'), XDG_DATA_HOME=str(tmp_path/'data'))
    proc = subprocess.run([PWSH,'-NoProfile','-NonInteractive','-File',str(script),
                           '-InstallerPath',str(payload),'-ExpectedSha256',sha,
                           '-MinimumVersion','14.51.36247.0','-EvidenceDirectory',str(evidence)] + (['-Uninstall'] if case.startswith('uninstall_') else []),
                          env=env,capture_output=True,text=True,timeout=30)
    records = list(evidence.glob('vcredist-*.json'))
    assert len(records) == 1, (proc.stdout,proc.stderr)
    result = json.loads(records[0].read_text(encoding='utf-8-sig'))
    assert result['helper_exit_code'] == code, result
    assert result['status'] == status, result
    if case == 'reboot':
        assert result['reboot_required'] is True
    if case in ('bad_hash','bad_signature','newer'):
        assert 'installer_exit_code' not in result
