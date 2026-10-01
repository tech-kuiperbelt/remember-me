"""Compatibility launcher for ``python -m app.manage_cli``."""
from app.cli.manage_cli import entrypoint


if __name__ == "__main__":
    entrypoint()
