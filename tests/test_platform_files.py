"""Windows adaptation unit checks; native exclusion is also tested on the runner."""
import errno
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from app import platform_files


def test_windows_locks_exact_byte_and_reports_contention(monkeypatch, tmp_path):
    calls = []
    api = SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=lambda *args: calls.append(args))
    monkeypatch.setitem(sys.modules, 'msvcrt', api)
    monkeypatch.setattr(platform_files, 'WINDOWS', True)
    with (tmp_path / 'sidecar').open('a+') as handle:
        platform_files.acquire_file_lock(handle)
        platform_files.release_file_lock(handle)
        assert calls == [(handle.fileno(),2,1),(handle.fileno(),0,1)]
        assert handle.tell() == 0
        api.locking = Mock(side_effect=OSError(errno.EACCES,'locked'))
        with pytest.raises(BlockingIOError):platform_files.acquire_file_lock(handle)
        api.locking = Mock(side_effect=OSError(errno.EBADF,'bad descriptor'))
        with pytest.raises(OSError) as error:platform_files.acquire_file_lock(handle)
        assert not isinstance(error.value,BlockingIOError)
    assert (tmp_path/'sidecar').read_bytes()==b''


def test_windows_skips_directory_fsync_only(monkeypatch):
    monkeypatch.setattr(platform_files, 'WINDOWS', True)
    opened = Mock(side_effect=AssertionError('Windows must not open a directory for fsync'))
    monkeypatch.setattr(platform_files.os,'open',opened)
    platform_files.fsync_directory('unused')
    opened.assert_not_called()
