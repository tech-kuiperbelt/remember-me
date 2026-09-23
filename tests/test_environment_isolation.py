"""Exercise pytest bootstrap with hostile inherited settings in a fresh process."""
import os
from pathlib import Path
import subprocess
import sys


def test_inherited_production_configuration_is_overridden(tmp_path):
    root = Path(__file__).resolve().parents[1]
    suite = tmp_path / 'suite'
    suite.mkdir()
    (suite / 'conftest.py').write_text((root / 'tests/conftest.py').read_text())
    (suite / 'test_probe.py').write_text('''
import os
from pathlib import Path

def test_probe():
    from app import audit
    from app.settings import Settings
    from app.embed import create_embedder
    settings = Settings.from_env()
    provider = create_embedder(environment=settings.environment)
    assert settings.STORAGE_BACKEND == 'sqlite'
    assert settings.STORAGE_PATH == ':memory:'
    assert settings.DEFAULT_NAMESPACE == 'default'
    assert settings.environment['EMBED_PROVIDER'] == 'ollama'
    assert not settings.EMBED_PRELOAD
    assert provider.spec.dimension == 1024
    assert 'MEMORY_REPLICA_PATH' not in os.environ
    assert 'MEMORY_STORAGE_PATH_DUAL' not in os.environ
    assert 'SENTENCE_TRANSFORMERS_MODEL' not in os.environ
    audit.record('probe', {}, {}, 0)
    assert Path(audit.AUDIT_PATH).is_file()
    Path(os.environ['PROBE_PATH']).write_text(audit.AUDIT_PATH)
''')
    paths = [tmp_path / name for name in ('production.db','production.jsonl','production-audit.jsonl')]
    for path in paths:
        path.write_bytes(b'unchanged production sentinel')
    probe = tmp_path / 'probe.txt'
    env = {**os.environ, 'PYTHONPATH':str(root), 'PROBE_PATH':str(probe),
           'MEMORY_STORAGE':'dual', 'MEMORY_STORAGE_PATH':str(paths[0]),
           'MEMORY_REPLICA_PATH':str(paths[1]), 'MEMORY_STORAGE_PATH_DUAL':str(paths[1]),
           'MEMORY_AUDIT_PATH':str(paths[2]), 'MEMORY_DEFAULT_NAMESPACE':'production',
           'EMBED_PROVIDER':'sentence_transformers', 'EMBED_PRELOAD':'true',
           'EMBED_DIM':'invalid', 'SENTENCE_TRANSFORMERS_MODEL':'must-not-load'}
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', str(suite)],
                            cwd=root, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert all(p.read_bytes() == b'unchanged production sentinel' for p in paths)
    assert not list(tmp_path.glob('*.writer.lock'))
    assert not Path(probe.read_text()).parent.exists(), 'temporary audit directory not cleaned'
