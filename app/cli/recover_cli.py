"""CLI: Offline recovery of SQLite from a vector-free NDJSON backup.

Usage:
    python -m app.cli.recover_cli <source> <target>

Examples:
    # Stop the server first, then rebuild SQLite from its JSONL backup.
    python -m app.cli.recover_cli data/memories.jsonl data/memories.db
"""

import os
import sys

from app.embedding.embed import create_embedder
from app.embedding.provider_resolution import (
    EmbeddingResolutionError,
    resolve_embedding_environment,
)
from app.storage.recovery import recover_ndjson_to_sqlite


def main():
    if len(sys.argv) != 3:
        print("Usage: python -m app.recover_cli <source.jsonl> <target.db>", file=sys.stderr)
        print("", file=sys.stderr)
        print("Examples:", file=sys.stderr)
        print("  Stop the server first, then run:", file=sys.stderr)
        print("  python -m app.recover_cli data/memories.jsonl data/memories.db", file=sys.stderr)
        sys.exit(1)

    src_path = sys.argv[1]
    from app.config.configuration import output_path
    tgt_path = output_path(sys.argv[2])

    if not src_path.endswith((".jsonl", ".ndjson")) or not tgt_path.endswith(".db"):
        print("Recovery requires a .jsonl/.ndjson source and .db target.", file=sys.stderr)
        sys.exit(2)

    # An explicit provider drives the rebuild (and skips probing the target).
    # Otherwise an existing target's profile is inherited; a target that exists
    # but cannot be read is an error rather than a silent provider switch, so an
    # unidentifiable Ollama database is never rebuilt with the default model.
    try:
        environment = resolve_embedding_environment(
            os.environ, db_path=tgt_path, explicit_is_target=True)
    except EmbeddingResolutionError as exc:
        print(
            f"{exc}\nSet EMBED_PROVIDER (and its model parameters) to the original "
            "provider to rebuild this target, or remove the damaged file to use the "
            "product default.",
            file=sys.stderr,
        )
        sys.exit(2)
    provider = create_embedder(environment=environment)
    result = recover_ndjson_to_sqlite(
        src_path, tgt_path, provider.embed_document,
        embedding_spec=provider.spec.as_dict(),
        embedding_dtype=os.environ.get("EMBED_DTYPE", "float16"),
        embed_documents=provider.embed_documents,
    )

    print(f"Recovered: {result['recovered']}, Errors: {result['errors']}")
    print(f"  Source: {src_path}")
    print(f"  Target: {tgt_path}")


def entrypoint():
    """Run the command with its configuration and exit-status policy."""
    from app.config.configuration import cli_environment
    if not any(arg in ("--help", "-h") for arg in sys.argv[1:]):
        cli_environment()
    main()


if __name__ == "__main__":
    entrypoint()
