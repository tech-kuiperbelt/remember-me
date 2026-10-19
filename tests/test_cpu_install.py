"""Exercise installer boundaries without downloading packages or GPU wheels."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import venv

import pytest


@pytest.fixture
def installation(tmp_path):
    (tmp_path / 'bin').mkdir()
    root = Path(__file__).resolve().parents[1]
    shutil.copy2(root / 'bin/install.sh', tmp_path / 'bin/install.sh')
    shutil.copy2(root / 'requirements.txt', tmp_path / 'requirements.txt')
    venv.EnvBuilder(with_pip=False).create(tmp_path / '.venv')
    python = tmp_path / '.venv/bin/python'
    site = Path(subprocess.check_output(
        [str(python), '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip())
    (site / 'torch-2.6.0+cpu.dist-info').mkdir()
    (site / 'torch-2.6.0+cpu.dist-info/METADATA').write_text('Name: torch\nVersion: 2.6.0+cpu\n')
    (site / 'torch.py').write_text(
        'from types import SimpleNamespace\nversion = SimpleNamespace(cuda=None, hip=None)\n')
    (site / 'sentence_transformers.py').write_text('__version__ = "5.0.0"\n')
    (site / 'pip').mkdir()
    (site / 'pip/__init__.py').write_text('')
    (site / 'pip/__main__.py').write_text('''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['RM_INSTALL_TEST_LOG'], 'a') as f:
    f.write(json.dumps({'args': args, 'extra_index': os.getenv('PIP_EXTRA_INDEX_URL')}) + '\\n')
if '--constraint' in args:
    constraint = Path(args[args.index('--constraint') + 1])
    assert constraint.read_text().strip() == 'torch==2.6.0+cpu'
    Path(os.environ['RM_INSTALL_CONSTRAINT_PATH']).write_text(str(constraint))
    if os.getenv('RM_INSTALL_FAIL') == 'requirements':
        sys.exit(7)
if '--index-url' in args and os.getenv('RM_INSTALL_FAIL') == 'index':
    sys.exit(8)
''')
    environment = dict(os.environ, RM_INSTALL_TEST_LOG=str(tmp_path / 'calls.jsonl'),
                       RM_INSTALL_CONSTRAINT_PATH=str(tmp_path / 'constraint-path'),
                       PIP_EXTRA_INDEX_URL='https://example.invalid/cuda', TMPDIR=str(tmp_path))
    for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'):
        environment.pop(key, None)
    return tmp_path, site, environment


def run_install(installation):
    root, _, environment = installation
    result = subprocess.run(['bash', str(root / 'bin/install.sh')], env=environment,
                            capture_output=True, text=True)
    calls = [json.loads(line) for line in (root / 'calls.jsonl').read_text().splitlines()]
    return result, calls


def test_cpu_version_is_constrained_and_temporary_file_removed(installation):
    result, calls = run_install(installation)
    assert result.returncode == 0, result.stderr
    torch = next(call for call in calls if '--index-url' in call['args'])
    assert torch['extra_index'] is None
    assert '--only-binary=:all:' in torch['args']
    constraints = installation[0] / 'constraint-path'
    assert constraints.exists()
    assert not Path(constraints.read_text()).exists()


@pytest.mark.parametrize('gpu', ['nvidia', 'cuda', 'rocm'])
def test_existing_gpu_environment_stops_before_installing(installation, gpu):
    _, site, _ = installation
    if gpu == 'nvidia':
        (site / 'nvidia_cudnn_cu13-1.0.dist-info').mkdir()
        (site / 'nvidia_cudnn_cu13-1.0.dist-info/METADATA').write_text(
            'Name: nvidia-cudnn-cu13\nVersion: 1.0\n')
    else:
        cuda = '13.0' if gpu == 'cuda' else None
        hip = '6.0' if gpu == 'rocm' else None
        (site / 'torch.py').write_text(
            'from types import SimpleNamespace\nversion = SimpleNamespace('
            f'cuda={cuda!r}, hip={hip!r})\n')
    result, calls = run_install(installation)
    assert result.returncode != 0
    assert all('install' not in call['args'] for call in calls)
    assert (installation[0] / '.venv').exists()


def test_failed_dependency_resolution_cleans_constraint(installation):
    installation[2]['RM_INSTALL_FAIL'] = 'requirements'
    result, _ = run_install(installation)
    assert result.returncode == 7
    assert not Path((installation[0] / 'constraint-path').read_text()).exists()


def test_failed_cpu_index_never_falls_back(installation):
    installation[2]['RM_INSTALL_FAIL'] = 'index'
    result, calls = run_install(installation)
    assert result.returncode == 8
    assert sum('install' in call['args'] for call in calls) == 1


def test_broken_environment_is_preserved(tmp_path):
    (tmp_path / 'bin').mkdir()
    root = Path(__file__).resolve().parents[1]
    shutil.copy2(root / 'bin/install.sh', tmp_path / 'bin/install.sh')
    (tmp_path / '.venv').mkdir()
    marker = tmp_path / '.venv/keep-me'
    marker.write_text('keep')
    result = subprocess.run(['bash', str(tmp_path / 'bin/install.sh')], capture_output=True, text=True)
    assert result.returncode != 0
    assert marker.read_text() == 'keep'
