"""Real isolated file/lock regression; no user data or Windows removal executed."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('uninstall_data', ROOT/'packaging/windows-setup/uninstall-data.py')
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


@pytest.fixture
def data(tmp_path):
    root = tmp_path/'中文 data'
    root.mkdir()
    for name in ('config.jsonc', 'memories.db', 'memories.jsonl'):
        (root/name).write_text('fixture', encoding='utf-8')
    (root/'logs').mkdir()
    (root/'logs/server.log').write_text('log')
    return root


def test_validate_is_readonly_and_explicit_remove_cleans_only_selected_root(data):
    before = sorted(p.relative_to(data) for p in data.rglob('*'))
    assert cleanup.validate(data, ROOT) == data
    assert before == sorted(p.relative_to(data) for p in data.rglob('*'))
    cleanup.remove(data, ROOT)
    assert not data.exists()
    assert ROOT.exists()


def test_unknown_files_are_never_silently_deleted(data):
    (data/'personal.txt').write_text('unrelated')
    with pytest.raises(ValueError, match='Unrecognized'):
        cleanup.remove(data, ROOT)
    assert (data/'memories.db').exists()


def test_reject_links_and_preserve_target(data, tmp_path):
    outside = tmp_path/'outside'
    outside.mkdir()
    (outside/'keep').write_text('keep')
    (data/'logs/link').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='link'):
        cleanup.remove(data, ROOT)
    assert (outside/'keep').exists()


def test_reject_linked_ancestor(data, tmp_path):
    alias = tmp_path/'alias'
    alias.symlink_to(data.parent, target_is_directory=True)
    with pytest.raises(ValueError, match='links'):
        cleanup.validate(alias/data.name, ROOT)


@pytest.mark.parametrize('which', ['memories.db', 'memories.jsonl'])
def test_active_writer_prevents_any_deletion(data, which):
    from app.platform_files import acquire_file_lock, release_file_lock
    with (data/(which+'.writer.lock')).open('a+') as handle:
        acquire_file_lock(handle)
        try:
            with pytest.raises(BlockingIOError):
                cleanup.remove(data, ROOT)
            assert (data/'memories.db').exists()
            assert (data/'logs/server.log').exists()
        finally:
            release_file_lock(handle)


def test_reject_program_and_parent_roots(data):
    with pytest.raises(ValueError, match='separate'):
        cleanup.validate(data, data/'program')
    with pytest.raises(ValueError, match='absolute'):
        cleanup.validate('../data', ROOT)


def test_dialog_is_opt_in_and_silent_uninstall_retains_everything():
    source = (ROOT/'packaging/windows-setup/remember-me.iss').read_text()
    assert 'DataBox.Checked := False' in source
    assert 'VCBox.Checked := False' in source
    assert 'if UninstallSilent then begin Result := True; exit; end' in source
    assert 'MB_YESNO or MB_DEFBUTTON2' in source
    assert source.index('if RemoveData and not DataTask(True)') < source.index('if RemoveVC then begin')
