#!/usr/bin/env python3
"""Check a private CPython prefix before installing application dependencies.

Only uses the standard library. Temporary lock files are disposable; no service,
business database, model cache or network connection is opened.
"""
from __future__ import annotations

import argparse
import ctypes
import fcntl
import json
import multiprocessing
from pathlib import Path
import platform
import site
import sqlite3
import ssl
import subprocess
import sys
import sysconfig
import tempfile


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def check_prefix(runtime):
    runtime = runtime.resolve()
    require(Path(sys.prefix).resolve() == runtime and sys.prefix == sys.base_prefix,
            "Run this check with the private CPython prefix, not a venv/system Python")
    require(Path(sys.executable).resolve().is_relative_to(runtime),
            "Python executable is outside the selected runtime")
    require(site.ENABLE_USER_SITE is False, "Disable user site-packages (-s or PYTHONNOUSERSITE=1)")
    require(Path(sysconfig.get_path("purelib")).resolve().is_relative_to(runtime),
            "site-packages is outside the selected runtime")
    return runtime


def spawn_check(runtime):
    # The spawn child must find the same complete standard library and executable.
    check_prefix(Path(runtime))
    require(sqlite3.connect(":memory:").execute("select 1").fetchone() == (1,),
            "Spawn child cannot use SQLite")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, type=Path)
    args = parser.parse_args()
    runtime = check_prefix(args.runtime)
    require(platform.python_implementation() == "CPython" and sys.version_info[:2] == (3, 12),
            "This prototype requires CPython 3.12")
    require(sys.platform == "linux" and platform.machine() in ("x86_64", "AMD64"),
            "This prototype requires Linux x86_64")
    ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctypes.CDLL(None)
    with sqlite3.connect(":memory:") as db:
        db.execute("create virtual table checks using fts5(text)")
        db.execute("insert into checks values ('standalone portability')")
        require(db.execute("select text from checks where checks match 'portability'").fetchone()
                == ("standalone portability",), "SQLite FTS5 search failed")
    with tempfile.TemporaryDirectory(prefix="remember-me-runtime-check-") as directory:
        path = Path(directory) / "writer.lock"
        with path.open("w") as first, path.open("w") as second:
            fcntl.flock(first, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                raise SystemExit("OS file lock did not exclude a second writer")
    child = subprocess.run([sys.executable, "-s", "-c",
                            "import json,sys; print(json.dumps([sys.executable, sys.prefix]))"],
                           check=True, capture_output=True, text=True, timeout=30)
    executable, prefix = json.loads(child.stdout)
    require(Path(executable).resolve().is_relative_to(runtime) and Path(prefix).resolve() == runtime,
            "Subprocess used an external Python")
    process = multiprocessing.get_context("spawn").Process(target=spawn_check, args=(str(runtime),))
    try:
        process.start()
        process.join(30)
        require(process.exitcode == 0, f"Spawn child failed or timed out: {process.exitcode}")
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        process.close()
    print(json.dumps({"result": "passed", "python": platform.python_version(),
                      "executable": sys.executable, "prefix": sys.prefix,
                      "ssl": ssl.OPENSSL_VERSION, "sqlite": sqlite3.sqlite_version,
                      "checks": ["private_prefix", "ssl", "ctypes", "sqlite_fts5", "flock",
                                 "subprocess", "multiprocessing_spawn"]}, indent=2))


if __name__ == "__main__":
    main()
