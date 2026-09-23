"""Smoke checks for the operator-facing wrapper."""

import os
import subprocess
from pathlib import Path


def test_manage_help_and_subcommand_help():
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "MEMORY_STORAGE_PATH": str(root / "data" / "test-manage.db")}
    for args in (
        ["--help"],
        ["embedding", "--help"],
        ["migrate", "keywords", "--help"],
        ["recover", "--help"],
    ):
        result = subprocess.run([str(root / "bin" / "manage"), *args], cwd=root,
                                env=env, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        assert "Usage" in result.stdout or "usage:" in result.stdout


def test_manage_rejects_unknown_command():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([str(root / "bin" / "manage"), "unknown"], cwd=root,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 2
    assert "Unknown command" in result.stderr
