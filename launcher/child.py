"""Linux exec shim: notify the service if its owning Launcher disappears."""
import ctypes
import os
import signal
import sys


def main():
    if len(sys.argv) < 3:
        raise SystemExit('Expected Launcher PID and service command.')
    expected_parent = int(sys.argv[1])
    libc = ctypes.CDLL(None, use_errno=True)
    # PR_SET_PDEATHSIG; do this in the child interpreter, never preexec_fn.
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    # prctl does not retroactively notify if parent died before it was set.
    if os.getppid() != expected_parent:
        raise SystemExit('Owning Launcher exited before service startup.')
    os.execv(sys.argv[2], sys.argv[2:])


if __name__ == '__main__':
    main()
