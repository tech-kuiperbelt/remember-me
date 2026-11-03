import ast
import importlib.util
import json
from pathlib import Path
import re
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('beta_start',ROOT/'packaging/windows-beta/beta_start.py')
start = importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(start)


def test_beta_uses_exact_native_candidate_and_keeps_production_versions():
    report = json.loads((ROOT/'packaging/windows-beta/resolved-dependencies.json').read_text())
    assert report['_source_run']==41604531455 and len(report['install'])==72
    normalize=lambda x:re.sub(r'[-_.]+','-',x).lower()
    resolved={normalize(e['metadata']['name']):e['metadata']['version'] for e in report['install']}
    pins=dict(re.findall(r'^([\w-]+)==([^\s\\]+)',(ROOT/'requirements/docker-cpu-py312-linux-amd64.lock').read_text(),re.M))
    pins['torch']='2.14.1+cpu'
    assert all(resolved[normalize(k)]==v for k,v in pins.items())
    assert all(re.fullmatch('[a-f0-9]{64}',e['download_info']['archive_info']['hashes']['sha256']) for e in report['install'])


def test_beta_data_is_external_and_settings_are_local_offline(monkeypatch,tmp_path):
    root=tmp_path/'program';root.mkdir();(root/'beta-policy.json').write_text('{"storage":"dual"}')
    data=tmp_path/'data'
    # Environment mutations are confined to this test process and restored.
    with patch.dict(start.os.environ,{'REMEMBER_ME_BETA_DATA_DIR':str(data),'MEMORY_STORAGE_PATH':'outside.db'}):
        assert start.configure(root)==data
        assert start.os.environ['MEMORY_STORAGE']=='dual'
        assert 'MEMORY_STORAGE_PATH' not in start.os.environ
        config=json.loads((data/'config.jsonc').read_text())
        assert config['local_only'] is True and config['auto_download_model'] is False
        assert start.os.environ['MEMORY_MODEL_DIR']==str(root/'models')
    with patch.dict(start.os.environ,{'REMEMBER_ME_BETA_DATA_DIR':str(root/'data')}):
        with pytest.raises(RuntimeError,match='outside'):start.configure(root)


def test_beta_smoke_and_finalizer_compile():
    for p in (ROOT/'packaging/windows-beta').glob('*.py'):
        compile(ast.parse(p.read_text()),str(p),'exec')


def test_zip_is_gated_by_smoke_and_excludes_external_test_data(tmp_path):
    import subprocess
    import sys
    from zipfile import ZipFile
    import hashlib
    bundle=tmp_path/'bundle';(bundle/'build-evidence').mkdir(parents=True)
    (bundle/'start.cmd').write_text('fixture')
    evidence=tmp_path/'evidence';evidence.mkdir()
    (evidence/'business-smoke.json').write_text('{"status":"failed"}')
    (tmp_path/'smoke-data').mkdir();(tmp_path/'smoke-data/private.db').write_text('test data')
    args=[sys.executable,'-I',str(ROOT/'packaging/windows-beta/finalize.py'),'--work',str(tmp_path),'--storage','dual','--zip']
    assert subprocess.run(args,capture_output=True).returncode!=0
    (evidence/'business-smoke.json').write_text('{"status":"passed","fixture":true}')
    subprocess.run(args,check=True,capture_output=True)
    archive=tmp_path/'remember-me-windows-x64-beta-candidate.zip'
    with ZipFile(archive) as z:
        assert z.testzip() is None
        assert 'remember-me-windows-beta/start.cmd' in z.namelist()
        assert not any('private.db' in n or 'smoke-data' in n for n in z.namelist())
    assert archive.with_suffix('.zip.sha256').read_text().split()[0]==hashlib.sha256(archive.read_bytes()).hexdigest()
