"""Small OS adaptations for existing sidecar locks and durable file publication.

Windows Beta policy: retain file fsync and atomic replacement on local NTFS;
Windows has no portable directory fsync here. POSIX behavior stays unchanged.
"""
import errno
import os

WINDOWS = os.name == 'nt'


def acquire_file_lock(handle):
    if WINDOWS:
        import msvcrt
        handle.seek(0)
        try:
            # Locks byte zero even on an empty sidecar; no sentinel/data writes.
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise BlockingIOError(errno.EAGAIN, 'Sidecar already locked') from exc
            raise
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def release_file_lock(handle):
    if WINDOWS:
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def fsync_directory(directory):
    if WINDOWS:
        return  # Approved Beta limitation: no POSIX-equivalent power-loss claim.
    flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0)
    fd = os.open(directory, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
