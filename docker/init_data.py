"""Optionally prepare a bind mount for an explicit UID/GID 10001 deployment.

Never recursively change ownership or follow links outside the data directory.
Existing nonempty directories require operator-managed compatible permissions.
"""
import os
from pathlib import Path
import stat
import sys
import uuid

UID = GID = 10001


def check_directory(fd):
    """Check existing regular resources without modifying their contents."""
    for name in os.listdir(fd):
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            raise PermissionError(f"symbolic link in data directory: {name}")
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                if not os.access('.', os.R_OK | os.W_OK | os.X_OK, dir_fd=child):
                    raise PermissionError(f"directory is not writable: {name}")
                check_directory(child)
            finally:
                os.close(child)
        elif stat.S_ISREG(info.st_mode):
            if not os.access(name, os.R_OK | os.W_OK, dir_fd=fd, follow_symlinks=False):
                raise PermissionError(f"file is not readable/writable: {name}")
        else:
            raise PermissionError(f"unsupported data entry: {name}")


def initialize(path=Path('/data')):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if not os.listdir(fd):
            os.fchown(fd, UID, GID)
            os.fchmod(fd, 0o750)
        os.setgroups([])
        os.setgid(GID)
        os.setuid(UID)
        check_directory(fd)
        name = '.remember-me-write-check-' + uuid.uuid4().hex
        probe = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600, dir_fd=fd)
        try:
            os.write(probe, b'permission check\n')
            os.fsync(probe)
        finally:
            os.close(probe)
            os.unlink(name, dir_fd=fd)
        print('Data directory is ready for UID/GID 10001:10001.')
    finally:
        os.close(fd)


if __name__ == '__main__':
    try:
        initialize()
    except OSError as exc:
        print(f'Data directory initialization failed: {exc}. '
              'Existing data was not changed. Prepare the selected bind directory '
              'for UID/GID 10001:10001 (see Docker deployment instructions); '
              'then rerun this initializer before starting with --user 10001:10001.',
              file=sys.stderr)
        sys.exit(1)
