"""Compatibility launcher for ``python -m app.embedding_cli``."""
from app.cli.embedding_cli import entrypoint


if __name__ == "__main__":
    entrypoint()
