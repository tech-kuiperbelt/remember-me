"""Installer input and safety contracts; these are not Windows GUI acceptance."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / 'packaging/windows-setup'
spec = importlib.util.spec_from_file_location('windows_setup_inputs', SETUP / 'validate_bundle.py')
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / '中文 program with spaces'
    for name in ('runtime/python/python.exe', 'runtime/python/pythonw.exe', 'beta_start.py',
                 'remember-me.ico', 'tray_launcher.py', 'windows_tray.py', 'windows_autostart.py', 'app/startup_timing.py', 'app/performance_timing.py', 'beta-policy.json', 'models/manifest.json',
                 'web/index.html', 'web/static/img/brand/favicon-neutral.ico'):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture', encoding='utf-8')
    (root / 'build-evidence').mkdir()
    (root / 'build-evidence/business-smoke.json').write_text('{"status":"passed"}', encoding='utf-8')
    (root / 'release.jsonc').write_text('// 版本\n{"version":"1.1.0",}', encoding='utf-8')
    return root


def test_version_comes_from_shipped_jsonc(bundle):
    assert validator.validate(bundle) == '1.1.0'
    (bundle / 'release.jsonc').write_text('{"version":"1.3.12"}', encoding='utf-8')
    assert validator.validate(bundle) == '1.3.12'


@pytest.mark.parametrize('version', [None, '1.1', '1.1.0-beta', 110])
def test_invalid_release_identity_rejected(bundle, version):
    (bundle / 'release.jsonc').write_text(json.dumps({'version': version}), encoding='utf-8')
    with pytest.raises(ValueError, match='numeric version'):
        validator.validate(bundle)


def test_failed_native_smoke_cannot_be_packaged(bundle):
    (bundle / 'build-evidence/business-smoke.json').write_text('{"status":"failed"}', encoding='utf-8')
    with pytest.raises(ValueError, match='smoke must pass'):
        validator.validate(bundle)


def test_incomplete_tray_payload_rejected(bundle):
    (bundle / 'windows_tray.py').unlink()
    with pytest.raises(ValueError, match='Missing Setup input'):
        validator.validate(bundle)


@pytest.mark.parametrize('name', ['memories.db', 'memories.db-wal', 'config.jsonc', 'config.json', 'memories.db.writer.lock'])
def test_user_state_cannot_enter_setup(bundle, name):
    (bundle / name).write_text('private user state', encoding='utf-8')
    with pytest.raises(ValueError, match='Unexpected user/runtime state'):
        validator.validate(bundle)


def test_symlink_payload_rejected(bundle):
    (bundle / 'linked-model').symlink_to(bundle / 'models/manifest.json')
    with pytest.raises(ValueError, match='real files'):
        validator.validate(bundle)


def test_installer_preserves_data_by_default_and_never_kills_processes():
    source = (SETUP / 'remember-me.iss').read_text(encoding='utf-8')
    assert '[UninstallDelete]' not in source
    assert '[InstallDelete]' not in source
    assert 'PrivilegesRequired=lowest' in source
    assert 'CloseApplications=no' in source
    assert 'RestartApplications=no' in source
    assert 'pythonw.exe' in source
    assert 'tray_launcher.py' in source
    for name in ('remember-me.iss', 'check-running.ps1', 'install-vcredist.ps1'):
        text = (SETUP / name).read_text(encoding='utf-8').lower()
        for dangerous in ('stop-process', 'taskkill', 'remove-item'):
            assert dangerous not in text


def test_pinned_inputs_have_independent_identity_requirements():
    inputs = json.loads((SETUP / 'inputs.json').read_text(encoding='utf-8'))
    for entry in inputs.values():
        assert len(bytes.fromhex(entry['sha256'])) == hashlib.sha256().digest_size
        assert entry['url'].startswith('https://')
        assert entry['publisher']
    vc = inputs['vcredist']
    assert 'download.visualstudio.microsoft.com/' in vc['url']
    assert vc['sha256'].upper() in vc['url']
    helper = (SETUP / 'install-vcredist.ps1').read_text(encoding='utf-8')
    assert 'Get-AuthenticodeSignature' in helper
    assert "-Verb RunAs -Wait -PassThru" in helper
    assert "@(0, 3010, 1641)" in helper
    assert '1602' in helper and '1223' in helper
    assert helper.count('Get-RuntimeVersion') >= 3


def test_pinned_model_configs_are_assets_not_user_configuration(bundle):
    manifest = json.loads((ROOT / 'docker/bge-m3-manifest.json').read_text(encoding='utf-8'))
    snapshot = bundle / 'models/sentence-transformers/models--BAAI--bge-m3/snapshots' / manifest['revision']
    for relative in ('config.json', '1_Pooling/config.json'):
        source = ROOT / 'tests/fixtures/windows-bge-m3-configs' / relative
        record = next(item for item in manifest['files'] if item['path'] == relative)
        assert hashlib.sha256(source.read_bytes()).hexdigest() == record['sha256']
        destination = snapshot / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    assert validator.validate(bundle) == '1.1.0'
    (snapshot / 'config.json').write_text('{"user":"state"}', encoding='utf-8')
    with pytest.raises(ValueError, match='Pinned model configuration mismatch'):
        validator.validate(bundle)


def test_model_config_name_or_contents_do_not_allow_unknown_paths(bundle):
    unknown = bundle / 'models/unknown/config.json'
    unknown.parent.mkdir(parents=True)
    unknown.write_bytes((ROOT / 'tests/fixtures/windows-bge-m3-configs/config.json').read_bytes())
    with pytest.raises(ValueError, match='Unexpected user/runtime state'):
        validator.validate(bundle)


def test_inno_version_checks_actual_compiler_not_bootstrap_pe_version():
    inputs = json.loads((SETUP / 'inputs.json').read_text(encoding='utf-8'))
    script = (SETUP / 'remember-me.iss').read_text(encoding='utf-8')
    compiler = ', '.join(inputs['inno']['version'].split('.'))
    assert f'#if Ver != EncodeVer({compiler})' in script
    build = (SETUP / 'build.ps1').read_text(encoding='utf-8')
    assert "Receive-VerifiedInput $inputs.inno 'innosetup.exe'" in build
    assert "Receive-VerifiedInput $inputs.vcredist 'vc_redist.x64.exe' -CheckPeVersion" in build
    assert 'if ($CheckPeVersion -and [version]$version -ne $required)' in build
    assert 'pinned_release_and_compiler_guard' in build
