"""Compatibility launcher for ``python -m app.recover_cli``."""
from app.cli.recover_cli import entrypoint


if __name__ == "__main__":
    entrypoint()
