"""Compatibility launcher for ``python -m app.anchor_migrate_deepseek``."""
from app.cli.anchor_migrate_deepseek import entrypoint


if __name__ == "__main__":
    entrypoint()
