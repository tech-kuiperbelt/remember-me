"""Entrypoint compatibility and resource ownership after moving app modules."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.storage.sqlite import SQLiteStorage


ROOT = Path(__file__).resolve().parents[1]
COMMANDS = (
    "manage_cli", "embedding_cli", "recover_cli", "keyword_migrate_deepseek",
    "category_migrate_deepseek", "anchor_migrate_deepseek",
)


def environment(tmp_path):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("MEMORY_", "EMBED_", "OLLAMA_", "FASTEMBED_",
                                  "SENTENCE_TRANSFORMERS_"))}
    env.update(PYTHONPATH=str(ROOT), MEMORY_DATA_DIR=str(tmp_path / "data"),
               MEMORY_STORAGE="dual", EMBED_PRELOAD="false",
               OLLAMA_BASE_URL="http://127.0.0.1:1")
    return env


def run(tmp_path, env, *args):
    return subprocess.run([sys.executable, "-B", *args], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=20)


@pytest.mark.parametrize("command", COMMANDS)
def test_old_and_new_command_help_match_without_initialization(tmp_path, command):
    env = environment(tmp_path)
    env.update(MEMORY_STORAGE="invalid", EMBED_DIM="invalid")
    old = run(tmp_path, env, "-m", f"app.{command}", "--help")
    new = run(tmp_path, env, "-m", f"app.cli.{command}", "--help")
    # Recovery historically prints usage and exits 1, even for --help.
    assert old.returncode == new.returncode == (1 if command == "recover_cli" else 0)
    assert (old.stdout, old.stderr) == (new.stdout, new.stderr)
    assert "usage" in (old.stdout + old.stderr).lower()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("command,args,exit_code", [
    ("manage_cli", ["unknown"], 2),
    ("embedding_cli", ["unknown"], 2),
    ("recover_cli", ["bad.txt", "target.db"], 2),
    ("keyword_migrate_deepseek", ["--unknown"], 2),
    ("category_migrate_deepseek", ["--unknown"], 2),
    ("anchor_migrate_deepseek", ["unknown"], 2),
])
def test_old_and_new_command_errors_match(tmp_path, command, args, exit_code):
    env = environment(tmp_path)
    old = run(tmp_path, env, "-m", f"app.{command}", *args)
    new = run(tmp_path, env, "-m", f"app.cli.{command}", *args)
    assert old.returncode == new.returncode == exit_code
    assert (old.stdout, old.stderr) == (new.stdout, new.stderr)
    assert not list(tmp_path.rglob("*.db"))
    assert not list(tmp_path.rglob("*.writer.lock"))
    assert not list(tmp_path.rglob("config.json*"))


@pytest.mark.parametrize("command,args", [
    ("manage_cli", ["status"]),
    ("embedding_cli", ["status", "memories.db"]),
    ("keyword_migrate_deepseek", ["--dry-run"]),
    ("category_migrate_deepseek", ["--dry-run"]),
    ("anchor_migrate_deepseek", ["draft", "--dry-run"]),
])
def test_read_only_commands_work_while_primary_writer_is_active(tmp_path, command, args):
    env = environment(tmp_path)
    data = Path(env["MEMORY_DATA_DIR"])
    data.mkdir()
    primary = SQLiteStorage(str(data / "memories.db"), writer_lock=True)
    try:
        before = primary.export_records()
        old = run(tmp_path, env, "-m", f"app.{command}", *args)
        new = run(tmp_path, env, "-m", f"app.cli.{command}", *args)
        assert old.returncode == new.returncode == 0, old.stderr + new.stderr
        assert old.stdout == new.stdout
        assert primary.export_records() == before
        assert not list(data.glob("config.json*"))
        assert not (data / "memories.jsonl").exists()
        assert not (data / "anchor-backfill").exists()
    finally:
        primary.close()


@pytest.mark.parametrize("reverse", [False, True])
def test_all_modules_import_without_starting_resources(tmp_path, reverse):
    env = environment(tmp_path)
    env.update(MEMORY_STORAGE="invalid", EMBED_DIM="invalid")
    script = '''
import importlib, logging, pathlib, sys, threading
before = tuple(logging.getLogger().handlers), tuple(threading.enumerate())
sys.argv = ['probe', '--invalid-argument']
import app.storage
assert not any(name in sys.modules for name in (
    'app.storage.sqlite', 'app.storage.dual', 'app.storage.ndjson'))
from app.storage.base import MemoryStorage
assert app.storage.MemoryStorage is MemoryStorage
root = pathlib.Path(sys.argv_root)
modules = sorted('.'.join(p.relative_to(root).with_suffix('').parts)
                 for p in (root / 'app').rglob('*.py') if p.name != '__init__.py')
for module in modules[::sys.import_direction]:
    importlib.import_module(module)
assert before == (tuple(logging.getLogger().handlers), tuple(threading.enumerate()))
assert not any(name in sys.modules for name in ('fastembed', 'sentence_transformers', 'torch'))
'''
    setup = f"import sys; sys.argv_root = {str(ROOT)!r}; sys.import_direction = {-1 if reverse else 1}\n"
    result = run(tmp_path, env, "-c", setup + script)
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_static_routes_and_relative_data_root_from_foreign_cwd(tmp_path):
    env = environment(tmp_path)
    env["MEMORY_DATA_DIR"] = os.path.relpath(tmp_path / "data", ROOT)
    env["MEMORY_STORAGE"] = "sqlite"
    env["MEMORY_STORAGE_PATH"] = ":memory:"
    script = '''
from pathlib import Path
from starlette.testclient import TestClient
from app.config.configuration import PROJECT_ROOT
from app.config.settings import Settings
from app.runtime import Runtime
from app.server import create_http_app
from tests.conftest import FakeProvider
from tests.conftest import mock_version_client
import app.operations.version_update as version_update
version_update.create_http_client = mock_version_client
settings = Settings.from_config()
assert Path(settings.DATA_DIR) == Path.cwd() / 'data'
runtime = Runtime(settings, provider_factory=lambda **kw: FakeProvider(), configure_logging=False)
with TestClient(create_http_app(runtime, port=2345), base_url='http://127.0.0.1:2345') as client:
    admin = client.get('/admin/console')
    assert admin.status_code == 200
    # The console serves the real template localized for the saved language.
    assert '<html lang="zh-CN"' in admin.text
    assert 'id="i18n-catalog"' in admin.text
    assert 'id="page-memories"' in admin.text
    guide = client.get('/read-me')
    assert guide.status_code == 200
    assert guide.text == (PROJECT_ROOT / 'app/read_me.md').read_text().replace('{base}', 'http://127.0.0.1:2345')
'''
    result = run(tmp_path, env, "-c", script)
    assert result.returncode == 0, result.stderr
